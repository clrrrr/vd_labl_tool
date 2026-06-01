"""Read and write the video file's ``comment`` metadata as a JSON annotation.

The annotation JSON shape (matches the example given by the user):

    {
      "Process Name": "Lens Reflector Bowl Installation",
      "Parts Involved": ["Reflector", "Glass lens", ...]
    }

This module uses bundled ffmpeg/ffprobe binaries. The write path uses an
``ffmetadata`` file (rather than ``-metadata comment=...`` on the command
line) to avoid Windows argv-quoting issues with quotes, CJK, and other
JSON characters.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import ffbin


@dataclass(frozen=True)
class Annotation:
    """User-facing annotation written to the video's comment metadata."""

    process_name: str
    parts_involved: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to the exact JSON shape expected in the comment field."""
        return json.dumps(
            {
                "Process Name": self.process_name,
                "Parts Involved": list(self.parts_involved),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def from_comment(comment: str) -> "Annotation | None":
        """Parse a comment string into an Annotation, or None if invalid."""
        try:
            data = json.loads(comment)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        process_name = data.get("Process Name")
        parts = data.get("Parts Involved")
        if not isinstance(process_name, str) or not process_name.strip():
            return None
        if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
            return None
        return Annotation(process_name=process_name, parts_involved=list(parts))


class MetadataError(RuntimeError):
    """Raised when ffmpeg/ffprobe operations fail."""


def _creationflags() -> int:
    # Suppress the console window on Windows when running as a packaged GUI app.
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


def _run(cmd: list[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creationflags(),
        timeout=timeout,
    )


def _escape_ffmeta(value: str) -> str:
    """Escape a value for ffmpeg's ffmetadata format.

    ffmetadata reserves ``\\``, ``=``, ``;``, ``#``; we also collapse newlines
    so each key=value pair stays on a single line.
    """
    # Backslash first so we don't double-escape later substitutions.
    value = value.replace("\\", "\\\\")
    for ch in ("=", ";", "#"):
        value = value.replace(ch, "\\" + ch)
    value = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return value


def read_annotation(video_path: Path) -> Annotation | None:
    """Read the comment tag from a video file and parse it as an Annotation.

    Returns None if the file has no comment, the comment isn't valid JSON,
    or the JSON doesn't match the expected schema. Returns None on ffprobe
    failures too — caller should treat that as "unannotated".
    """
    info = read_video_info(video_path)
    return info[0]


def read_video_info(video_path: Path) -> tuple[Annotation | None, float | None]:
    """Read both the annotation and the duration (seconds) in one ffprobe call.

    Returns ``(annotation_or_None, duration_or_None)``. Either field is None
    on missing/invalid/probe-failure — callers should fall back gracefully.
    Using a single ffprobe invocation per file matters for large folders.
    """
    ffprobe = ffbin.ffprobe_path()
    if not ffprobe:
        raise MetadataError("ffprobe binary not found")

    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration:format_tags=comment",
        "-of", "json",
        str(video_path),
    ]
    try:
        result = _run(cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return None, None

    if result.returncode != 0:
        return None, None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, None

    fmt = data.get("format", {})
    tags = fmt.get("tags", {}) if isinstance(fmt, dict) else {}

    annotation: Annotation | None = None
    comment = tags.get("comment") if isinstance(tags, dict) else None
    if isinstance(comment, str):
        annotation = Annotation.from_comment(comment)

    duration: float | None = None
    dur_raw = fmt.get("duration") if isinstance(fmt, dict) else None
    if isinstance(dur_raw, (int, float)):
        duration = float(dur_raw)
    elif isinstance(dur_raw, str):
        try:
            duration = float(dur_raw)
        except ValueError:
            duration = None

    return annotation, duration


def write_annotation(video_path: Path, annotation: Annotation) -> None:
    """Write the annotation JSON into the video file's comment metadata.

    Uses ``ffmpeg -c copy`` so no re-encoding happens — operation is I/O bound,
    not CPU bound, and preserves all streams losslessly. The original file is
    replaced atomically via ``os.replace``; on any failure the original is left
    untouched and the temp output is cleaned up.

    The caller MUST release any open handle to ``video_path`` (e.g. close the
    QMediaPlayer source) before invoking this — Windows holds an exclusive
    rename lock otherwise.
    """
    ffmpeg = ffbin.ffmpeg_path()
    if not ffmpeg:
        raise MetadataError("ffmpeg binary not found")
    if not video_path.exists():
        raise MetadataError(f"video file not found: {video_path}")

    comment_value = annotation.to_json()

    # Use a sibling temp file (same directory) so os.replace is atomic.
    # Keep the same suffix so ffmpeg infers the correct muxer.
    out_path = video_path.with_name(
        f".{video_path.stem}.writing.{uuid.uuid4().hex[:8]}{video_path.suffix}"
    )

    # Write the ffmetadata file in the OS temp dir.
    meta_fd, meta_name = tempfile.mkstemp(suffix=".ffmeta", prefix="vlt_")
    meta_path = Path(meta_name)
    try:
        with os.fdopen(meta_fd, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            f.write(f"comment={_escape_ffmeta(comment_value)}\n")

        # Keep video / audio / subtitle / attachment streams from the input;
        # explicitly drop data streams (GoPro GPMF, iPhone "mebx" gyro tracks,
        # etc.) since ffmpeg can't always mux those back into MP4 with -c copy
        # and fails with "could not find tag for codec none". Doing this with
        # explicit -map 0:v? / 0:a? etc. (the "?" suffix means "optional")
        # is more robust than -map 0 + -ignore_unknown.
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(video_path),
            "-i", str(meta_path),
            "-map", "0:v?",
            "-map", "0:a?",
            "-map", "0:s?",
            "-map", "0:t?",
            "-map_metadata", "1",
            "-c", "copy",
            str(out_path),
        ]
        try:
            result = _run(cmd, timeout=1800)  # 30 min — stream copy of multi-GB files
        except subprocess.TimeoutExpired as e:
            raise MetadataError("ffmpeg timed out (>30 min)") from e

        if result.returncode != 0:
            raise MetadataError(
                f"ffmpeg failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        if not out_path.exists():
            raise MetadataError("ffmpeg reported success but no output file was produced")

        os.replace(out_path, video_path)
    finally:
        try:
            meta_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if out_path.exists():
                out_path.unlink()
        except OSError:
            pass
