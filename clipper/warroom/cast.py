"""The cast library window (#41): keep or drop each person's photos, label them, find more
(a background grab, one at a time)."""
from __future__ import annotations

import re
import threading
from urllib.parse import quote

from ..errors import ClipperError
from .faces import stream_path

CAST_JOB: dict = {"running": False, "person": "", "what": "", "log": [], "error": "", "added": 0}

CAST_LOCK = threading.Lock()

def cast_get(cfg: dict, person: str) -> dict:
    """Everyone with counts, one person's photos (served under /cast/), and the running job."""
    from .. import cast

    out = cast.overview(cfg, person)
    if "person" in out:
        p = out["person"]
        for ph in p["photos"]:
            ph["url"] = f"/cast/{quote(p['name'])}/{quote(ph['file'])}"
        p["single_url"] = f"/cast/{quote(p['single'])}" if p["single"] else ""
    out["job"] = {**{k: CAST_JOB[k] for k in ("running", "person", "what", "error", "added")},
                  "log": CAST_JOB["log"][-40:]}
    out["estimate"] = find_estimate(cfg)
    return out

def find_estimate(cfg: dict) -> dict:
    """#203: what one Find can cost before it runs: a label call for each new photo, at most
    [cast] per_source of them, at the mean of past cast label calls (None before any)."""
    from .. import costs

    cc = cfg["cast"]
    per = costs.avg_call_usd(cfg["paths"].get("costs"), "cast") if cc["label_model"] else 0.0
    return {"per_source": cc["per_source"], "label_usd": per}

def cast_mark(cfg: dict, body: dict) -> dict:
    """{"person", "ids", "status": "keep" | "drop"}."""
    from .. import cast

    ids = body.get("ids")
    if body.get("status") not in ("keep", "drop") or not isinstance(ids, list) \
            or not all(isinstance(i, str) and re.fullmatch(r"[0-9a-f]{10}", i) for i in ids):
        raise ClipperError("cast: status keep or drop, and photo ids")
    cast.set_status(cfg, body.get("person", ""), ids, body["status"])
    return cast_get(cfg, body.get("person", ""))

def cast_label(cfg: dict, body: dict) -> dict:
    from .. import cast

    cast.set_label(cfg, body.get("person", ""), str(body.get("id", "")), str(body.get("label", "")))
    return cast_get(cfg, body.get("person", ""))

def cast_set(cfg: dict, body: dict) -> dict:
    """{"person", "aliases": [...], "channels": [...]}: a new person too."""
    from .. import cast

    lists = [body.get(k) for k in ("aliases", "channels")]
    if not all(v is None or (isinstance(v, list) and all(isinstance(s, str) for s in v)) for v in lists):
        raise ClipperError("cast: aliases and channels are lists of text")
    cast.set_info(cfg, body.get("person", ""), *lists)
    return cast_get(cfg, body.get("person", ""))

def cast_run(cfg: dict, body: dict) -> dict:
    """{"person", "what": "find" (+ "sources") | "add" (+ "url") | "grab" (+ "stream")}, in the
    background: downloads, face checks and labels take a while. One at a time."""
    from .. import cast

    person, what = cast.person_dir(cfg, body.get("person", "")).name, body.get("what")
    sources = body.get("sources") or list(cast.SOURCES)
    url = str(body.get("url") or "")
    if what not in ("find", "add", "grab") or not isinstance(sources, list) or not set(sources) <= set(cast.SOURCES):
        raise ClipperError("cast: what is find, add or grab")
    if what == "add" and not re.match(r"https?://", url):
        raise ClipperError("cast: a web address (http:// or https://)")
    sdir = stream_path(cfg, body.get("stream")) if what == "grab" else None
    with CAST_LOCK:
        if CAST_JOB["running"]:
            raise ClipperError(f"cast: still on {CAST_JOB['person']} ({CAST_JOB['what']})")
        CAST_JOB.update(running=True, person=person, what=what, log=[], error="", added=0)

    def say(msg: str) -> None:
        CAST_JOB["log"] = [*CAST_JOB["log"][-199:], msg]

    def run() -> None:
        try:
            claude = None
            if cfg["cast"]["label_model"]:
                from ..llm import Claude

                claude = Claude(cfg)
            if what == "find":
                n = cast.find(cfg, person, sources, claude, say)
            elif what == "add":
                n = int(bool(cast.add_file(cfg, person, url, claude, say)))
            else:
                n = cast.grab(cfg, sdir, person, 4, claude, say)
            CAST_JOB["added"] = n
            say(f"{n} new photo(s)" + (f", labels ${claude.cost:.3f}" if claude and claude.cost else ""))
        except ClipperError as e:
            CAST_JOB["error"] = str(e)
        except Exception as e:  # noqa: BLE001  # shown in the window
            CAST_JOB["error"] = f"{type(e).__name__}: {e}"[:300]
        CAST_JOB["running"] = False

    threading.Thread(target=run, name="warroom-cast", daemon=True).start()
    return cast_get(cfg, person)
