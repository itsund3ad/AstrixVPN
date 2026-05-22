# Astrix — by UNDEAD (https://github.com/itsund3ad)
# PyInstaller build script for astrix-server
#
# Usage: python build_exe.py
# Output: dist/astrix-server.exe (Windows) or dist/astrix-server (Linux/macOS)

import PyInstaller.__main__
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

PyInstaller.__main__.run([
    'astrix_server/__main__.py',
    '--name=astrix-server',
    '--onefile',
    '--console',
    '--clean',
    '--add-data=server_config.example.json:.',
    f'--icon={"astrix.ico" if os.path.exists("astrix.ico") else ""}',
    '--strip',
    '--exclude-module=tkinter',
    '--exclude-module=unittest',
    '--exclude-module=pytest',
    '--collect-all=astrix_server',
])
