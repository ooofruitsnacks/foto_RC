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
        # -y preserves symbolic links inside the application bundle.
        archive = output / f"{base}.zip"
        run(
            "zip",
            "-qry",
            archive,
            bundle.name,
            cwd=dist,
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
