"""Low-res copies of previews for phones: made on demand, pre-warmed for what is in review."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from ..errors import ClipperError
from .data import all_rows

log = logging.getLogger("clipper.warroom")

LOWRES: dict = {"queue": [], "busy": "", "error": ""}

LOWRES_COND = threading.Condition()

def lowres_path(src: Path) -> Path:
    return src.parent / "lowres" / f"{src.stem}.mp4"

def lowres_ready(src: Path) -> bool:
    try:
        return lowres_path(src).stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False

def lowres_want(src: Path, urgent: bool = False) -> int:
    """Queue a low-res copy of src; returns how many are ahead of it. Someone waiting on the
    page (urgent) goes before the prewarm backlog."""
    key = str(src)
    with LOWRES_COND:
        if LOWRES["busy"] == key:
            return 0
        if key in LOWRES["queue"] and urgent:
            LOWRES["queue"].remove(key)
        if key not in LOWRES["queue"]:
            LOWRES["queue"].insert(0, key) if urgent else LOWRES["queue"].append(key)
            LOWRES_COND.notify()
        return LOWRES["queue"].index(key) + bool(LOWRES["busy"])

def render_lowres(src: Path, height: int) -> Path:
    dst = lowres_path(src)
    dst.parent.mkdir(exist_ok=True)
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    short_side = f"scale='if(gt(iw,ih),-2,{height})':'if(gt(iw,ih),{height},-2)'"  # Shorts too
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vf", short_side,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-maxrate", "700k",
           "-bufsize", "1400k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "2",
           "-movflags", "+faststart", str(tmp)]
    if os.name == "nt":  # stay out of the way of the pipeline's own encodes
        subprocess.run(cmd, check=True, creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        subprocess.run(["nice", "-n", "10", *cmd], check=True)
    tmp.replace(dst)
    return dst

def _lowres_worker(cfg: dict) -> None:
    while True:
        with LOWRES_COND:
            while not LOWRES["queue"]:
                LOWRES_COND.wait()
            key = LOWRES["queue"].pop(0)
            LOWRES["busy"] = key
        src = Path(key)
        try:
            if src.is_file() and not lowres_ready(src):
                render_lowres(src, cfg["warroom"]["lowres_height"])
        except Exception as e:  # noqa: BLE001
            LOWRES["error"] = f"{src.name}: {type(e).__name__}: {e}"
            log.warning(f"low-res {LOWRES['error']}")
        finally:
            with LOWRES_COND:
                LOWRES["busy"] = ""

def _lowres_prewarm(cfg: dict) -> None:
    """Every few minutes, queue copies for items waiting for review, newest stream first."""
    root = cfg["paths"]["streams"]
    while True:
        try:
            for r in reversed(all_rows(cfg)):
                if r["decision"] in ("pending", "changes_requested") and r["video_url"]:
                    src = root / unquote(urlparse(r["video_url"]).path[len("/media/"):])
                    if not lowres_ready(src):
                        lowres_want(src)
        except Exception as e:  # noqa: BLE001
            log.warning(f"low-res prewarm: {type(e).__name__}: {e}")
        time.sleep(300)

def lowres_status(cfg: dict, url: str) -> dict:
    """Where the low-res copy of a /media/ video is, or how soon it will be."""
    root = cfg["paths"]["streams"].resolve()
    src = (root / unquote(urlparse(url).path.removeprefix("/media/"))).resolve()
    if root not in src.parents or src.suffix.lower() != ".mp4" or not src.is_file():
        raise ClipperError("no such video")
    if lowres_ready(src):
        lo = lowres_path(src)
        return {"ready": True, "url": "/media/" + quote(lo.relative_to(root).as_posix()),
                "mb": round(lo.stat().st_size / 1e6, 1)}
    return {"ready": False, "ahead": lowres_want(src, urgent=True),
            "full_mb": round(src.stat().st_size / 1e6), "error": LOWRES["error"]}
