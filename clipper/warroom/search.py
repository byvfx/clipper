"""Search the streams' transcripts (#24), from their SRT sidecars."""
from __future__ import annotations

from pathlib import Path

from .. import actions, jsonstate

SRT_CACHE: dict[str, tuple[float, str]] = {}

def srt_text(path: Path) -> str:
    """An item's caption text on one line, cached by mtime."""
    import re

    try:
        mt = path.stat().st_mtime
    except OSError:
        return ""
    hit = SRT_CACHE.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = " ".join(ln.strip() for ln in raw.splitlines()
                    if ln.strip() and not ln.strip().isdigit() and "-->" not in ln)
    text = re.sub(r"<[^>]+>", "", text)
    SRT_CACHE[str(path)] = (mt, text)
    return text

def search_transcripts(cfg: dict, q: str, limit: int = 300) -> dict:
    """Items whose captions say every word of q (case-insensitive), with a snippet each."""
    words = q.lower().split()
    if not words or len(q.strip()) < 3:
        return {"hits": {}}
    hits = {}
    for sdir in actions.review_dirs(cfg):
        state = jsonstate.read(sdir / "review_state.json", {}) or {}
        for key, st in state.items():
            text = srt_text((sdir / st["file"].replace("\\", "/")).with_suffix(".srt"))
            low = text.lower()
            if text and all(w in low for w in words):
                i = low.find(" ".join(words))  # the phrase where it's said, else the first word
                i = i if i >= 0 else low.find(words[0])
                hits[f"{sdir.name}/{key}"] = ("…" if i > 60 else "") + text[max(0, i - 60):i + 100] + "…"
                if len(hits) >= limit:
                    return {"hits": hits, "more": True}
    return {"hits": hits}
