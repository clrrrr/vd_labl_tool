"""Locate bundled ffmpeg / ffprobe executables.

Resolution order:
1. PyInstaller runtime: ``sys._MEIPASS / "vendor"``
2. Development: project root ``vendor/`` (next to ``main.py``)
3. None — caller should surface a user-facing error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bin_name(stem: str) -> str:
    return f"{stem}.exe" if sys.platform == "win32" else stem


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    # PyInstaller one-folder bundle
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "vendor")
    # Development: project root sits two levels up from this file
    # (.../video_label_tool/video_label_tool/ffbin.py)
    dirs.append(Path(__file__).resolve().parent.parent / "vendor")
    # Last resort: cwd/vendor
    dirs.append(Path.cwd() / "vendor")
    return dirs


def _find(stem: str) -> str | None:
    name = _bin_name(stem)
    for d in _candidate_dirs():
        p = d / name
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return None


def ffmpeg_path() -> str | None:
    """Return absolute path to ffmpeg binary, or None if not found."""
    return _find("ffmpeg")


def ffprobe_path() -> str | None:
    """Return absolute path to ffprobe binary, or None if not found."""
    return _find("ffprobe")


def ensure_available() -> tuple[str, str] | None:
    """Return (ffmpeg, ffprobe) paths or None if either is missing."""
    ff = ffmpeg_path()
    fp = ffprobe_path()
    if ff and fp:
        return ff, fp
    return None
