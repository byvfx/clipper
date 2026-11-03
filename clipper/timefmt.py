"""Times as people read and type them. Positions in a stream are cut to the whole second
(a chapter at 59.9 s is 0:00:59, never a second after the moment); lengths are rounded."""
from __future__ import annotations

import re

from .errors import ClipperError


def hms(t: float) -> str:
    """A position as H:MM:SS (YouTube chapters, logs, hand-pick listings)."""
    t = max(0, int(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def clock(t: float) -> str:
    """A position as M:SS under an hour, else H:MM:SS (receipts chapters and lists)."""
    t = max(0, int(t))
    return hms(t) if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def length(t: float | None) -> str:
    """A duration to the nearest second, M:SS under an hour; "?" when unknown."""
    return "?" if t is None else clock(round(t))


def parse_time(s: str) -> float:
    """H:MM:SS, H:MM (hours, minutes), 4h13m / 4h13m20s / 253m, or plain seconds."""
    s = s.strip().lower()
    if ":" in s:
        parts = [float(x) for x in s.split(":")]
        if len(parts) == 2:
            parts.append(0.0)
        if len(parts) != 3:
            raise ClipperError(f"not a time: {s!r} (use seconds, M:SS or H:MM:SS)")
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s?)?", s)
    if not m or not any(m.groups()):
        raise ClipperError(f"not a time: {s!r} (use seconds, M:SS or H:MM:SS)")
    h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + sec
