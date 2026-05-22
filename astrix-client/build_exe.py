#!/usr/bin/env python3
# Astrix — by UNDEAD (https://github.com/itsund3ad)
# PyInstaller build script for astrix-client.
#
# Usage:
#   python build_exe.py                    # build with default name
#   python build_exe.py --version          # print version
#   python build_exe.py --name=my-client   # custom name
#
# Output: dist/astrix-client-vX.Y.Z (Linux) or .exe (Windows)

import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent

version = "0.0.0"
version_file = ROOT.parent / "VERSION"
if version_file.exists():
    version = version_file.read_text().strip()

if "--version" in sys.argv:
    print(f"astrix-client v{version}")
    sys.exit(0)

NAME = f"astrix-client-v{version}"
for arg in sys.argv[1:]:
    if arg.startswith("--name="):
        NAME = arg.split("=", 1)[1]

try:
    import PyInstaller  # noqa
except ImportError:
    print("Installing PyInstaller...")
    os.system(f"{sys.executable} -m pip install pyinstaller --quiet")

config_path = ROOT.parent / "client_config.example.json"
excludes = [
    "--exclude-module=tkinter", "--exclude-module=unittest",
    "--exclude-module=pytest", "--exclude-module=test",
    "--exclude-module=distutils", "--exclude-module=setuptools",
    "--exclude-module=pip", "--exclude-module=wheel",
    "--exclude-module=numpy", "--exclude-module=matplotlib",
    "--exclude-module=PIL", "--exclude-module=scipy",
    "--exclude-module=pandas", "--exclude-module=cv2",
    "--exclude-module=tensorflow", "--exclude-module=torch",
    "--exclude-module=IPython", "--exclude-module=jupyter",
    "--exclude-module=notebook", "--exclude-module=sphinx",
    "--exclude-module=requests", "--exclude-module=urllib3",
    "--exclude-module=certifi",
]
upx_flag = ["--upx-dir=/usr/bin"] if os.path.exists("/usr/bin/upx") else ["--noupx"]

spec_path = ROOT / f"{NAME}.spec"
if not spec_path.exists():
    print(f"Generating spec: {spec_path}")
    import PyInstaller.__main__

    args = [
        str(ROOT / "astrix_client" / "__main__.py"),
        f"--name={NAME}",
        "--onefile",
        "--console",
        "--clean",
        f"--add-data={config_path}{os.pathsep}.",
        "--strip",
    ] + upx_flag + excludes + [
        "--collect-all=astrix_client",
    ]
    PyInstaller.__main__.run(args)

print(f"Building {NAME}...")
import PyInstaller.__main__

PyInstaller.__main__.run([
    str(spec_path),
    "--clean",
])

print(f"\nDone: dist/{NAME}{'.exe' if sys.platform == 'win32' else ''}")
