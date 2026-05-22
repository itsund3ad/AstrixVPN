#!/usr/bin/env python3
# Astrix — by UNDEAD (https://github.com/itsund3ad)
# PyInstaller build script for astrix-server.
#
# Usage:
#   python build_exe.py                    # build with default name
#   python build_exe.py --version          # print version
#
# Output: dist/astrix-server-vX.Y.Z (Linux) or .exe (Windows)

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

version = "0.0.0"
version_file = ROOT.parent / "VERSION"
if version_file.exists():
    version = version_file.read_text().strip()

if "--version" in sys.argv:
    print(f"astrix-server v{version}")
    sys.exit(0)

try:
    import PyInstaller  # noqa
except ImportError:
    os.system(f"{sys.executable} -m pip install pyinstaller --quiet")

spec_path = ROOT / "astrix-server.spec"
if spec_path.exists():
    print(f"Using existing spec: {spec_path}")
else:
    spec_path = ROOT / f"astrix-server-v{version}.spec"
    if not spec_path.exists():
        print(f"Generating spec: {spec_path}")
        config_path = ROOT.parent / "server_config.example.json"
        excludes = [
            "--exclude-module=tkinter", "--exclude-module=unittest",
            "--exclude-module=pytest", "--exclude-module=test",
            "--exclude-module=distutils", "--exclude-module=setuptools",
            "--exclude-module=pip", "--exclude-module=wheel",
            "--exclude-module=numpy", "--exclude-module=matplotlib",
            "--exclude-module=PIL",
        ]
        upx = ["--upx-dir=/usr/bin"] if os.path.exists("/usr/bin/upx") else ["--noupx"]

        import PyInstaller.__main__
        PyInstaller.__main__.run([
            str(ROOT / "astrix_server" / "__main__.py"),
            f"--name=astrix-server-v{version}",
            "--onefile", "--console", "--clean",
            f"--specpath={ROOT}",
            f"--distpath={ROOT / 'dist'}",
            f"--workpath={ROOT / 'build'}",
            f"--add-data={config_path}{os.pathsep}.",
            "--strip",
        ] + upx + excludes + [
            "--collect-all=astrix_server",
        ])

import PyInstaller.__main__
PyInstaller.__main__.run([
    str(spec_path.resolve()),
    "--clean",
    f"--distpath={ROOT / 'dist'}",
    f"--workpath={ROOT / 'build'}",
])

out = f"dist/astrix-server-v{version}{'.exe' if sys.platform == 'win32' else ''}"
print(f"\nDone: {out}")
