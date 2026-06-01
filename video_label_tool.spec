# PyInstaller spec for video_label_tool (Windows one-folder build).
# Build with: pyinstaller video_label_tool.spec --clean --noconfirm
#
# Notes:
# - One-folder mode (no --onefile) avoids per-launch extraction to a temp dir,
#   which on Windows tends to trip AV scanners and adds 1-2s startup latency.
# - vendor/ffmpeg.exe and vendor/ffprobe.exe must be present before building.
# - PySide6 multimedia plugins are pulled in via collect_all so QMediaPlayer
#   has the WindowsMediaFoundation backend at runtime.

# ruff: noqa
from PyInstaller.utils.hooks import collect_all
from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve()

pyside_datas, pyside_binaries, pyside_hidden = collect_all('PySide6')
# openpyxl has some lazy-imported submodules (notably openpyxl.cell._writer)
# that PyInstaller's static analysis misses; collect_all sweeps them in.
openpyxl_datas, openpyxl_binaries, openpyxl_hidden = collect_all('openpyxl')

added_datas = list(pyside_datas) + list(openpyxl_datas) + [
    (str(PROJECT_ROOT / 'vendor' / 'ffmpeg.exe'), 'vendor'),
    (str(PROJECT_ROOT / 'vendor' / 'ffprobe.exe'), 'vendor'),
]

a = Analysis(
    ['main.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=pyside_binaries + openpyxl_binaries,
    datas=added_datas,
    hiddenimports=pyside_hidden + openpyxl_hidden + [
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='video_label_tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='video_label_tool',
)
