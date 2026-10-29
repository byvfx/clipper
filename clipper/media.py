"""Facts about media files, from ffprobe, and a stream's source video."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import ClipperError


def _probe(path: Path, entries: str, stream: str | None = None) -> str:
    args = ["ffprobe", "-v", "error"] + (["-select_streams", stream] if stream else [])
    out = subprocess.run(args + ["-show_entries", entries, "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() if out.returncode == 0 else ""


def try_duration(path: Path, video: bool = False) -> float | None:
    """Length in seconds, or None if ffprobe can't tell. video: the first video stream's
    length rather than the container's (a download with a longer audio track)."""
    out = _probe(path, "stream=duration" if video else "format=duration", "v:0" if video else None)
    try:
        return float(out.splitlines()[0])
    except (IndexError, ValueError):
        return None


def duration(path: Path) -> float:
    """Length in seconds; ClipperError if ffprobe can't tell."""
    d = try_duration(path)
    if d is None:
        raise ClipperError(f"ffprobe found no duration in {path}")
    return d


def size(path: Path) -> tuple[int, int]:
    """Width and height of the first video stream."""
    out = _probe(path, "stream=width,height", "v:0")
    try:
        w, h = out.split(",")[:2]
        return int(w), int(h)
    except ValueError:
        raise ClipperError(f"ffprobe found no video size in {path}") from None


def source_file(stream_dir: Path) -> Path:
    """The stream's source.* video, skipping downloads still in progress."""
    for p in sorted(stream_dir.glob("source.*")):
        if p.suffix not in (".part", ".ytdl") and "Frag" not in p.name:
            return p
    raise ClipperError(f"no source video in {stream_dir}")
