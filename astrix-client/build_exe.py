# Astrix — by UNDEAD (https://github.com/itsund3ad)
# PyInstaller build script for astrix-client
#
# Usage: python build_exe.py
# Output: dist/astrix-client.exe (Windows) or dist/astrix-client (Linux/macOS)

import PyInstaller.__main__
import os
import sys

# Ensure we can import the package
sys.path.insert(0, os.path.dirname(__file__))

PyInstaller.__main__.run([
    'astrix_client/__main__.py',
    '--name=astrix-client',
    '--onefile',
    '--console',
    '--clean',
    '--add-data=client_config.example.json:.',
    f'--icon={"astrix.ico" if os.path.exists("astrix.ico") else ""}',
    '--strip',
    '--exclude-module=tkinter',
    '--exclude-module=unittest',
    '--exclude-module=pytest',
    '--collect-all=astrix_client',
])
