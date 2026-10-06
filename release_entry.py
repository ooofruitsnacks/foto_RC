"""Packaging entry point for foto RC."""

import json
import sys
import traceback
from pathlib import Path


def self_test():
    import tempfile

    import numpy
    import rawpy
    import pillow_heif
    from PIL import Image
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import foto_rc

    app = QApplication([])
    app.setApplicationName("foto RC")

    # Verify that the actual application window can be constructed.
    window = foto_rc.FotoRC()
    window.setWindowOpacity(1.0)
    window.show()

    formats = {
        spec["fmt"]: spec
        for spec in foto_rc.available_formats()
    }

    required = ("JPEG", "PNG", "WEBP", "AVIF", "HEIF", "TIFF")
    missing = set(required) - set(formats)

    if missing:
        raise RuntimeError(
            f"Missing bundled output formats: {sorted(missing)}"
        )

    # Exercise the application's real conversion function.
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "input.png"
        Image.new("RGB", (128, 96), (100, 160, 70)).save(source)

        for fmt in required:
            destination = foto_rc.convert_one(
                str(source),
                formats[fmt],
                75,
                85,
                str(root / "output"),
            )

            with Image.open(destination) as result:
                result.load()
                if result.size != (96, 72):
                    raise RuntimeError(
                        f"{fmt}: unexpected size {result.size}"
                    )

    # Allow a short GUI event-loop pass, then exit automatically.
    QTimer.singleShot(300, app.quit)
    result = app.exec()

    if result != 0:
        raise RuntimeError(f"Qt exited with code {result}")

    return {
        "ok": True,
        "tested_formats": list(required),
        "rawpy": rawpy.__version__,
        "numpy": numpy.__version__,
    }


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        report_path = Path(sys.argv[2])

        try:
            report = self_test()
            exit_code = 0
        except Exception:
            report = {
                "ok": False,
                "traceback": traceback.format_exc(),
            }
            exit_code = 1

        report_path.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        sys.exit(exit_code)

    from launcher import main

    main()
