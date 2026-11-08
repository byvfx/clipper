"""The cover editor (#18): read and change a cover's frame, face box, words and photos."""
from __future__ import annotations

import time

from .. import actions
from ..errors import ClipperError
from .data import media_url, row_json
from .review import checked_ref
from .state import LOCK


def cover_key(item: actions.Item) -> str:
    from .. import thumbs

    return thumbs.video_key(item.stream_dir / item.state["file"].replace("\\", "/"))

def cover_get(cfg: dict, ref: str) -> dict:
    from .. import thumbs

    item = actions.resolve(cfg, checked_ref(ref))
    sdir = item.stream_dir
    info = thumbs.cover_info(sdir, cfg, cover_key(item))
    for f in info["frames"]:
        f["url"] = media_url(sdir, sdir / f.pop("path"))
    jpg = (sdir / item.state["file"].replace("\\", "/")).with_suffix(".jpg")
    info["cover_url"] = media_url(sdir, jpg) + f"?v={int(jpg.stat().st_mtime)}" if jpg.exists() else ""
    return info

def cover_post(cfg: dict, body: dict) -> dict:
    from .. import thumbs

    item = actions.resolve(cfg, checked_ref(body.get("ref", "")))
    sdir, preview = item.stream_dir, bool(body.get("preview"))
    changes = body.get("changes") or {}
    if not isinstance(changes, dict) or not changes:
        raise ClipperError("nothing changed")
    if preview:
        jpg = thumbs.edit_cover(sdir, cfg, cover_key(item), changes, preview=True)
        return {"url": media_url(sdir, jpg) + f"?v={int(time.time() * 1000)}"}
    with LOCK:
        jpg = thumbs.edit_cover(sdir, cfg, cover_key(item), changes)
        (jpg.with_name("_preview.jpg")).unlink(missing_ok=True)
        row = actions.describe(cfg, sdir)[item.key]
    return {"row": row_json(sdir, row), "url": media_url(sdir, jpg) + f"?v={int(time.time() * 1000)}"}
