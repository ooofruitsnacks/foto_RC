"""Build, smoke-test, and archive a native foto RC application."""

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "foto-RC"


def run(*args, **kwargs):
    subprocess.run(
        [str(arg) for arg in args],
        check=True,
        **kwargs,
    )


def main():
    os.chdir(ROOT)

    target = os.environ["BUILD_TARGET"]
    version = os.environ.get("RELEASE_VERSION", "test")

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError(f"Invalid release version: {version!r}")

    packaging = ROOT / ".packaging"
    output = ROOT / "release"

    shutil.rmtree(packaging, ignore_errors=True)
    shutil.rmtree(output, ignore_errors=True)

    packaging.mkdir()
    output.mkdir()

    dist = packaging / "dist"
    work = packaging / "work"
    spec = packaging / "spec"
    spec.mkdir()
    macos_options = []

    if sys.platform == "darwin":
        required = (
            "MACOS_SIGNING_IDENTITY",
            "APPLE_ID",
            "APPLE_TEAM_ID",
            "APPLE_APP_SPECIFIC_PASSWORD",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                "Missing macOS release settings: " + ", ".join(missing)
            )

        macos_options = [
            "--codesign-identity",
            os.environ["MACOS_SIGNING_IDENTITY"],
            "--osx-bundle-identifier",
            "com.ooofruitsnacks.foto-rc",
        ]

    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        APP_NAME,
        "--distpath",
        dist,
        "--workpath",
        work,
        "--specpath",
        spec,
        "--collect-all",
        "rawpy",
        "--collect-all",
        "pillow_heif",
        "--collect-submodules",
        "PIL",
        *macos_options,
        ROOT / "release_entry.py",
    )

    if sys.platform == "darwin":
        bundle = dist / f"{APP_NAME}.app"
        executable = bundle / "Contents" / "MacOS" / APP_NAME
    elif sys.platform == "win32":
        bundle = dist / APP_NAME
        executable = bundle / f"{APP_NAME}.exe"
    else:
        bundle = dist / APP_NAME
        executable = bundle / APP_NAME

    # Test the frozen executable, not just the Python source.
    report_path = packaging / "smoke-test.json"
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"

    completed = subprocess.run(
        [str(executable), "--self-test", str(report_path)],
        env=env,
        timeout=180,
        check=False,
    )

    if report_path.exists():
        report = json.loads(
            report_path.read_text(encoding="utf-8")
        )
        print(json.dumps(report, indent=2))
    else:
        report = {"ok": False, "error": "No smoke-test report"}

    if completed.returncode != 0 or not report.get("ok"):
        raise RuntimeError(
            f"Frozen application failed smoke test: {report}"
        )

    base = f"foto-RC-py-{version}-{target}"

    if sys.platform == "darwin":
        # Verify the signed app before sending it to Apple.
        run(
            "codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            bundle,
        )

        # Keep the submission ZIP outside release/ so it is not published.
        submission_zip = packaging / f"{base}-notarization.zip"
        run(
            "ditto",
            "-c",
            "-k",
            "--keepParent",
            bundle,
            submission_zip,
        )

        # Read credentials from Actions secrets, not from committed files.
        notary_auth = [
            "--apple-id",
            os.environ["APPLE_ID"],
            "--team-id",
            os.environ["APPLE_TEAM_ID"],
            "--password",
            os.environ["APPLE_APP_SPECIFIC_PASSWORD"],
        ]

        print("Submitting the signed app to Apple for notarization...", flush=True)

        result = subprocess.run(
            [
                "xcrun",
                "notarytool",
                "submit",
                str(submission_zip),
                *notary_auth,
                "--wait",
                "--timeout",
                "60m",
                "--output-format",
                "json",
            ],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )

        # Apple returns the submission ID and status, not the credentials.
        print(result.stdout, flush=True)

        try:
            submission = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                "Apple did not return a notarization JSON result. "
                "Check the workflow output above."
            ) from None

        submission_id = submission.get("id")
        status = submission.get("status")

        # Download Apple's diagnostic log for completed submissions.
        if submission_id and status in {"Accepted", "Invalid", "Rejected"}:
            log_path = packaging / "notarization-log.json"
            log_result = subprocess.run(
                [
                    "xcrun",
                    "notarytool",
                    "log",
                    submission_id,
                    *notary_auth,
                    str(log_path),
                ],
                check=False,
            )
            if log_result.returncode == 0 and log_path.exists():
                print(log_path.read_text(encoding="utf-8"), flush=True)

        if result.returncode != 0 or status != "Accepted":
            raise RuntimeError(
                f"Notarization did not succeed: status={status!r}, "
                f"submission_id={submission_id!r}. "
                "Do not publish this build."
            )

        # Attach Apple's ticket to the app, not to the ZIP.
        run("xcrun", "stapler", "staple", bundle)
        run("xcrun", "stapler", "validate", bundle)

        # Check the final app's signature and Gatekeeper assessment.
        run("codesign", "--verify", "--deep", "--strict", bundle)
        run(
            "spctl",
            "--assess",
            "--type",
            "execute",
            "--verbose=2",
            bundle,
        )

        # Create the release ZIP AFTER stapling.
        archive = output / f"{base}.zip"
        run(
            "ditto",
            "-c",
            "-k",
            "--keepParent",
            bundle,
            archive,
        )

    elif sys.platform == "win32":
        shutil.make_archive(
            str(output / base),
            "zip",
            root_dir=dist,
            base_dir=bundle.name,
        )

    else:
        # Preserve executable permissions and symbolic links.
        with tarfile.open(
            output / f"{base}.tar.gz",
            "w:gz",
            dereference=False,
        ) as archive:
            archive.add(bundle, arcname=bundle.name)

    dependencies = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze"],
        text=True,
    )

    (output / f"{base}-dependencies.txt").write_text(
        dependencies,
        encoding="utf-8",
    )

    print(f"Release files created in {output}")


if __name__ == "__main__":
    main()
