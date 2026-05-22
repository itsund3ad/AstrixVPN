# -*- mode: python ; coding: utf-8 -*-
#
# Astrix — by UNDEAD (https://github.com/itsund3ad)
# PyInstaller spec for astrix-server.
# Build:  pyinstaller astrix-server.spec
#

import os
import sys
from pathlib import Path

ROOT = Path(os.path.abspath(SPECPATH))

version = "0.0.0"
try:
    with open(ROOT.parent / "VERSION") as f:
        version = f.read().strip()
except Exception:
    pass

block_cipher = None

a = Analysis(
    [str(ROOT / "astrix_server" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT.parent / "server_config.example.json"), "."),
    ],
    hiddenimports=[
        "cryptography.hazmat.primitives.ciphers.aead",
        "zstandard",
        "aiohttp",
        "rich",
        "prompt_toolkit",
        "click",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pytest", "test",
        "distutils", "setuptools", "pip", "wheel",
        "numpy", "matplotlib", "scipy", "pandas",
        "PIL", "cv2", "tensorflow", "torch",
        "IPython", "jupyter", "notebook",
        "sphinx", "docutils", "nose", "coverage",
        "requests", "urllib3",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f"astrix-server-v{version}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version_file=str(ROOT.parent / "scripts" / "versioninfo.txt") if sys.platform == "win32" else None,
)

if sys.platform == "darwin":
    COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=True,
        upx=True,
        upx_exclude=[],
        name=f"astrix-server-v{version}",
    )
