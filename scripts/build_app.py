#!/usr/bin/env python3
"""Build a native one-folder desktop application on the current platform."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = PROJECT_ROOT / "packaging" / "AbletonInspector.spec"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-macos-icon",
        action="store_true",
        help="Regenerate the committed .icns file with macOS system tools.",
    )
    args = parser.parse_args()

    if sys.version_info < (3, 11):
        print("Collector Inspector requires Python 3.11 or newer.", file=sys.stderr)
        return 1
    if shutil.which("python") is None and not Path(sys.executable).exists():
        print("A working Python installation is required.", file=sys.stderr)
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is not installed. Install the development dependencies first.",
            file=sys.stderr,
        )
        return 1

    build_environment = os.environ.copy()
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(PROJECT_ROOT / "build" / "pyinstaller-config")
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "build_icons.py")],
        check=True,
        cwd=PROJECT_ROOT,
    )
    macos_icon = PROJECT_ROOT / "src" / "ableton_audio_collector" / "assets" / "app_icon.icns"
    if sys.platform == "darwin" and (args.refresh_macos_icon or not macos_icon.exists()):
        subprocess.run(
            [str(PROJECT_ROOT / "scripts" / "build_icon.sh")],
            check=True,
            cwd=PROJECT_ROOT,
        )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(PROJECT_ROOT / "dist"),
            "--workpath",
            str(PROJECT_ROOT / "build" / "pyinstaller"),
            str(SPEC_PATH),
        ],
        check=True,
        cwd=PROJECT_ROOT,
        env=build_environment,
    )

    output = (
        PROJECT_ROOT / "dist" / "Collector Inspector.app"
        if sys.platform == "darwin"
        else PROJECT_ROOT / "dist" / "Collector Inspector"
    )
    print(f"Built {platform.system()} application: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
