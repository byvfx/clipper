"""The war room: a local web page with everything at once.

Usage:
    clipper warroom [--port 8765] [--open]

Serves http://127.0.0.1:<port>/ ([warroom] host/port): which shows are live on Kick, each
stream's pipeline progress, the review queue with previews and thumbnails (approve, ask for
changes, reject, hold, pin, edit title / description / hashtags / thumbnail words), the
release schedule, retakes, the bot's health and who is who on screen (#54: a stream's face
groups to name, and a box to draw round a face on a paused video). Decisions go through clipper/actions.py,
so they land in Kitsu exactly like the CLI's and the Discord bot's (Kitsu stays the
record); a "changes" decision starts `clipper retake` in the background, like the bot.

It reads the files the pipeline writes (review.json is as fresh as the last sync: the bot
syncs every few minutes, and the page's Sync button syncs now). Only listens on localhost
unless [warroom] host is changed, which then needs [warroom] token.
"""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, quote, unquote, urlparse
from zoneinfo import ZoneInfo

from . import actions, jsonstate, review, schedule, worker
from .config import _root, for_stream
from .errors import ClipperError

WEB = Path(__file__).with_name("web")
PAGE = WEB / "warroom.html"   # markup only: its style is warroom.css, its code web/js/*.js
STATIC = {"/manifest.webmanifest": "application/manifest+json", "/icon.svg": "image/svg+xml",
          "/icon-192.png": "image/png", "/icon-512.png": "image/png"}  # no secrets: no key needed
CODE = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
LOCK = threading.Lock()  # one Kitsu write / replan at a time
SYNC = {"running": False, "last": None, "error": ""}
LIVE_CACHE: dict = {"at": 0.0, "data": []}
MAX_POST = 1 << 20  # bytes; decisions, bulk edits, covers and layouts are far smaller


def static_file(path: str) -> tuple[Path, str, str] | None:
    """The page's own files, with their type and caching: no secrets, so no key (the phone's
    home-screen install fetches the icons without the cookie). Its stylesheet and scripts
    are fetched fresh each load, so a restarted bot's page never runs the old code."""
    if path in STATIC:
        return WEB / path.lstrip("/"), STATIC[path], "max-age=86400"
    f = (WEB / path.lstrip("/")).resolve()
    if f.suffix in CODE and WEB.resolve() in f.parents and f.is_file():
        return f, CODE[f.suffix], "no-cache"
    return None


def load(path: Path, default):
    try:
        return jsonstate.read(path, default)
    except (OSError, ValueError):
        return default


def age_s(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return 1e9


# ---------- data ----------

def media_url(sdir: Path, p: Path | str | None) -> str:
    if not p:
        return ""
    p = Path(p)
    try:   # no resolve(): it opens the file, a round trip each on the NAS share
        rel = p.relative_to(sdir)
    except ValueError:
        return ""
    if ".." in rel.parts:
        return ""
    return f"/media/{quote(sdir.name)}/{quote(rel.as_posix())}"


def row_json(sdir: Path, r: dict, ls: review.Listing | None = None) -> dict:
    ls = ls or review.Listing()
    out = {k: v for k, v in r.items() if not isinstance(v, Path)}
    out["thumbnail_url"] = media_url(sdir, r.get("thumbnail")) + (
        f"?v={int(r['thumb_mtime'])}" if r.get("thumb_mtime") else "")
    prev = r.get("preview")
    src = prev if prev and ls.exists(prev) else r.get("file")
    out["video_url"] = media_url(sdir, src) if src and ls.exists(src) else ""
    return out


# Per-stream results, rebuilt when the stream folder's top-level entries change (any file
# written there, or a file added to a subfolder, changes a name, mtime or size), or after
# CACHE_MAX_AGE_S. Reading every item's files over the NAS share takes seconds per stream.
CACHE: dict[tuple[str, str], tuple[tuple, float, object]] = {}
CACHE_LOCK = threading.Lock()
CACHE_MAX_AGE_S = 300


def _scan(sdir: Path) -> tuple[set[str], tuple]:
    try:
        with os.scandir(sdir) as it:
            ents = sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in it)
    except OSError:
        return set(), ()
    return {e[0] for e in ents}, tuple(ents)


def _streams(cfg: dict) -> list[tuple[Path, set[str], tuple]]:
    """(stream dir, its file names, a change signature) for each stream, one scan each."""
    with os.scandir(cfg["paths"]["streams"]) as it:
        dirs = sorted(Path(e.path) for e in it if e.is_dir() and not e.name.startswith("."))
    return [(d, *_scan(d)) for d in dirs]


def _cached(sdir: Path, what: str, sig: tuple, build):
    with CACHE_LOCK:
        hit = CACHE.get((sdir.name, what))
    if hit and hit[0] == sig and time.time() - hit[1] < CACHE_MAX_AGE_S:
        return hit[2]
    val = build()
    with CACHE_LOCK:
        CACHE[(sdir.name, what)] = (sig, time.time(), val)
    return val


def _mtime(p: Path) -> int:
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return 0


def stream_info(cfg: dict, sdir: Path) -> dict:
    sc = for_stream(cfg, sdir)
    meta = load(sdir / "meta.json", {})
    run = load(sdir / "run.json", {})
    stages = {}
    for st in worker.STAGES:
        info = (run.get("stages") or {}).get(st, {})
        status = info.get("status")
        if not status:
            status = "done" if worker.output_found(sdir, st) else ""
        stages[st] = status
    live = load(sdir / "live" / "state.json", {})
    running = run.get("status") == "running" and age_s(run.get("heartbeat")) < 15 * 60
    return {"id": sdir.name, "show": sc["show"]["key"], "show_name": sc["meta"]["show_name"] or "",
            "channel": meta.get("channel") or live.get("channel", ""),
            "title": meta.get("title") or live.get("title", ""),
            "started_at": meta.get("started_at") or live.get("stream_started_at") or "",
            "duration_s": meta.get("duration_s"),
            "stages": stages, "run_status": "running" if running else run.get("status", ""),
            "run_error": run.get("error", ""), "recording": live.get("status", ""),
            "published": (sdir / "review_state.json").exists(), "faces": (sdir / "faces.json").exists()}


def kick_live(cfg: dict) -> list[dict]:
    """Each show's channels' live status from Kick, cached for a minute."""
    import requests

    if time.time() - LIVE_CACHE["at"] < 60:
        return LIVE_CACHE["data"]
    root, out = _root(cfg), []
    for key, show in root["shows"].items():
        name = for_stream_show_name(cfg, key)
        for ch in show["channels"]:
            item = {"show": key, "show_name": name, "channel": ch, "live": False}
            try:
                r = requests.get(f"https://kick.com/api/v2/channels/{ch}", timeout=8,
                                 headers={"User-Agent": cfg["kick"]["user_agent"]})
                ls = (r.json() or {}).get("livestream") if r.ok else None
                if ls and ls.get("is_live"):
                    item.update(live=True, viewers=ls.get("viewer_count"),
                                title=ls.get("session_title", ""), since=ls.get("start_time", ""))
            except Exception as e:  # noqa: BLE001
                item["error"] = type(e).__name__
            out.append(item)
    LIVE_CACHE.update(at=time.time(), data=out)
    return out


def for_stream_show_name(cfg: dict, key: str) -> str:
    from .config import for_show
    return for_show(cfg, key)["meta"]["show_name"] or key


def stream_rows(cfg: dict, sdir: Path) -> list[dict]:
    sc = for_stream(cfg, sdir)
    ls = review.Listing()
    rows = [{**row_json(sdir, r, ls), "show": sc["show"]["key"],
             "show_name": sc["meta"]["show_name"] or ""}
            for r in actions.describe(cfg, sdir).values()]
    for r in rows:  # #19: the card's warning badge
        r["issues"] = actions.style_issues(sc, {f: r.get(f) or "" for f in actions.STYLE_FIELDS})
    return rows


def all_rows(cfg: dict, streams: list | None = None) -> list[dict]:
    """Every published stream's rows, oldest stream first."""
    sched = _mtime(cfg["paths"]["schedule"])   # rows show scheduled/uploaded
    rows = []
    for sdir, names, sig in streams if streams is not None else _streams(cfg):
        if "review_state.json" not in names:
            continue
        try:
            rows += _cached(sdir, "rows", sig + (sched,), lambda d=sdir: stream_rows(cfg, d))
        # ClipperError too: for_stream raises it on a bad streams/<id>/config.toml
        except Exception as e:  # noqa: BLE001 - one broken stream shouldn't blank the page
            print(f"warroom: {sdir.name}: {type(e).__name__}: {e}", flush=True)
    return rows


def stream_infos(cfg: dict, streams: list) -> list[dict]:
    """stream_info for every recorded or recording stream, newest first, skipping any that
    can't be read. A stream the NAS recorder is on has only live/ until it finishes."""
    infos = []
    for d, names, sig in streams:
        if "meta.json" not in names and "live" not in names:
            continue
        try:
            infos.append(_cached(d, "info", sig, lambda d=d: stream_info(cfg, d)))
        except Exception as e:  # noqa: BLE001 - as in all_rows
            print(f"warroom: {d.name}: {type(e).__name__}: {e}", flush=True)
    return sorted(infos, key=lambda i: i["started_at"], reverse=True)


def _show_name(cfg: dict, sdir: Path) -> str:
    try:
        return for_stream(cfg, sdir)["meta"]["show_name"]
    except Exception:  # noqa: BLE001 - a bad config.toml: fall back to the base show
        return cfg["meta"]["show_name"]


def retakes(cfg: dict, limit: int = 12) -> list[dict]:
    out = []
    for p in cfg["paths"]["streams"].glob("*/retakes.json"):
        for key, hs in load(p, {}).items():
            for h in hs:
                out.append({"stream": p.parent.name, "key": key, "comment": h.get("comment", ""),
                            "result": h.get("result", ""), "applied": h.get("applied", []),
                            "summary": (h.get("plan") or {}).get("summary", ""),
                            "unsupported": (h.get("plan") or {}).get("unsupported") or "",
                            "error": h.get("error", ""), "at": h.get("at", "")})
    return sorted(out, key=lambda h: h["at"], reverse=True)[:limit]


def health(cfg: dict) -> dict:
    root = cfg["paths"]["streams"]
    bot_log = cfg["paths"]["bot_log"]
    last = ""
    if bot_log and Path(bot_log).exists():
        with open(bot_log, "rb") as f:
            f.seek(max(0, Path(bot_log).stat().st_size - 4000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        last = lines[-1] if lines else ""
    return {"bot_lock": (root / ".bot.lock").exists(),
            "bot_log_age_s": time.time() - Path(bot_log).stat().st_mtime if bot_log and Path(bot_log).exists() else None,
            "bot_last": last[:200], "retake_running": (root / ".retake.lock").exists(),
            "auto_upload_minutes": cfg["youtube"]["auto_upload_minutes"],
            "sync": SYNC}


def upload_status(cfg: dict, sched: dict) -> dict:
    """#21: the 24 h cap like `clipper upload` counts it, the last automatic run from the
    bot log, and what's due next."""
    yc, now = cfg["youtube"], datetime.now(UTC)
    recent = sorted(datetime.fromisoformat(e["uploaded_at"]) for e in sched.get("entries", [])
                    if e.get("uploaded_at")
                    and now - datetime.fromisoformat(e["uploaded_at"]) < timedelta(hours=24))
    cap = yc["max_per_24h"]
    frees = recent[len(recent) - cap] + timedelta(hours=24) if cap and len(recent) >= cap else None
    nxt = sorted((e for e in sched.get("entries", []) if e.get("status") == "scheduled"),
                 key=lambda e: e["publish_at"])[:6]
    horizon = now + timedelta(hours=yc["upload_ahead_hours"])
    return {"last_24h": len(recent), "max_per_24h": cap, "room": max(0, cap - len(recent)),
            "frees_at": frees.isoformat() if frees else "",
            "auto_minutes": yc["auto_upload_minutes"], "last_run": last_upload_run(cfg),
            "next": [{"key": e["key"], "title": e["title"], "kind": e["kind"],
                      "publish_at": e["publish_at"],
                      "due": datetime.fromisoformat(e["publish_at"]) <= horizon} for e in nxt]}


def last_upload_run(cfg: dict) -> dict:
    """The bot's last finished `clipper upload` (not the replan before it): when, exit code,
    what went up and any warnings or errors, from the tail of the bot log (and the rotated
    one before it). The bot logs a job's output when it ends, so one in progress only shows
    as running."""
    bot_log = cfg["paths"]["bot_log"]
    if not bot_log or not Path(bot_log).exists():
        return {}
    lines: list[str] = []
    for p in (Path(f"{bot_log}.1"), Path(bot_log)):  # RotatingFileHandler's previous file first
        if p.exists():
            with open(p, "rb") as f:
                f.seek(max(0, p.stat().st_size - 600_000))
                lines += f.read().decode("utf-8", "replace").splitlines()
    runs, cur, prev_done = [], None, ""
    for line in lines:
        if " clipper.bot: upload: " not in line:
            continue
        at, msg = line[:19], line.split(" clipper.bot: upload: ", 1)[1]
        if msg == "starting":
            # The bot replans, then uploads straight after: a start in the same minute as
            # the replan's end is the upload, even before its output is in the log.
            cur = {"at": at, "lines": [], "after_replan": prev_done[:16] == at[:16]}
        elif cur is not None and msg.startswith("done (exit"):
            prev_done = at
            cur["exit"] = msg[len("done (exit "):].rstrip(")")
            runs.append(cur)
            cur = None
        elif cur is not None:
            cur["lines"].append(msg)
    done = [r for r in runs if any(" due within " in x for x in r["lines"])]  # not the replans
    out: dict = {}
    if done:
        r = done[-1]
        keep = ("uploading ", "https://youtu.be/", "warning", "error", "Error", "failed",
                "missed slot", "uploaded in the last", " due within ", "+ ")
        out = {"at": r["at"], "exit": r["exit"],
               "lines": [x[:300] for x in r["lines"] if any(k in x for k in keep)][-14:]}
    if cur and cur["after_replan"]:
        out["running_since"] = cur["at"]
    return out


def views_by_video(cfg: dict) -> dict[str, int]:
    a = load(cfg["paths"]["schedule"].with_name("analytics.json"), {}) or {}
    return {vid: v.get("views") for vid, v in a.items() if isinstance(v, dict)}


def overview(cfg: dict) -> dict:
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = datetime.now(tz)
    streams_dir: Path = cfg["paths"]["streams"]
    streams = _streams(cfg)
    infos = stream_infos(cfg, streams)
    rows = all_rows(cfg, streams)
    counts = {"pending": 0, "changes": 0, "approved": 0, "approved_unscheduled": 0,
              "rejected": 0, "held": 0}
    name = {"changes_requested": "changes"}
    for r in rows:
        d = name.get(r["decision"], r["decision"])
        counts[d] = counts.get(d, 0) + 1
        if d == "approved" and not r["scheduled"] and not r["uploaded"] and not r.get("dropped"):
            counts["approved_unscheduled"] += 1
        counts["held"] += bool(r["hold"])
    sched = load(cfg["paths"]["schedule"], {}) or {}
    by_ref = {r["ref"]: r for r in rows}
    entries, views = [], views_by_video(cfg)
    for e in sched.get("entries", []):
        when = datetime.fromisoformat(e["publish_at"]).astimezone(tz)
        if when < now - timedelta(days=2) or when > now + timedelta(days=21):
            continue
        r = by_ref.get(e["key"], {})
        sd = streams_dir / e["stream"]
        entries.append({**{k: e.get(k) for k in ("key", "stream", "kind", "id", "title", "status",
                                                    "source", "url", "video_id")},
                        "publish_at": when.isoformat(), "show_name": r.get("show_name") or
                        _show_name(cfg, sd),
                        "thumbnail_url": r.get("thumbnail_url", ""),
                        "views": views.get(e.get("video_id") or "")})
    entries.sort(key=lambda e: e["publish_at"])
    sc = cfg["schedule"]
    # #20: the next 14 days' open slots (drop targets) and the spacing rules the page checks.
    taken = {e["publish_at"][:16] for e in entries}
    free = [{"at": when.isoformat(), "kind": kind}
            for when, kind in schedule.slots({**sc, "horizon_days": 14}, tz, now.date())
            if when > now + timedelta(minutes=30) and when.isoformat()[:16] not in taken]
    return {"generated_at": now.isoformat(), "timezone": sc["timezone"],
            "free_slots": free, "rules": {"max_per_day": sc["max_per_day"], "min_gap_minutes": sc["min_gap_minutes"]},
            "shows": [{"key": k, "name": for_stream_show_name(cfg, k), "channels": s["channels"]}
                      for k, s in _root(cfg)["shows"].items()],
            "counts": {**counts, "scheduled_7d": sum(
                1 for e in entries if now <= datetime.fromisoformat(e["publish_at"]) <= now + timedelta(days=7)),
                "uploaded": sum(e.get("status") == "uploaded" for e in sched.get("entries", []))},
            "streams": infos[:20],
            "items": rows, "schedule": entries, "schedule_generated_at": sched.get("generated_at"),
            "retakes": retakes(cfg), "health": health(cfg), "stages": worker.STAGES,
            "uploads": upload_status(cfg, sched)}


# ---------- actions ----------

def analytics_status(cfg: dict, month: str = "") -> dict:
    """#42: the Analytics panel, the same numbers as `clipper analytics --report`."""
    try:
        from . import analytics

        return analytics.report(cfg, month)
    except Exception as e:  # noqa: BLE001  # a panel, not a reason to fail the page
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def costs_status(cfg: dict) -> dict:
    """#50: the Costs panel, month to date (clipper/costs.py)."""
    try:
        from . import costs

        return costs.report(cfg)
    except Exception as e:  # noqa: BLE001  # a panel, not a reason to fail the page
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def modal_status(cfg: dict) -> dict:
    """#49: the Modal panel (clipper/modalmon.py). Modal's token stays on this machine."""
    try:
        from . import modalmon

        return modalmon.status(cfg)
    except Exception as e:  # noqa: BLE001  # no modal extra installed, or signed out
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def start_retake(cfg: dict) -> bool:
    if (cfg["paths"]["streams"] / ".retake.lock").exists():
        return False
    cmd = actions.clipper_cmd(cfg)
    log = Path(cfg["paths"]["bot_log"]).with_name("retake.log") if cfg["paths"]["bot_log"] else Path(os.devnull)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} war room\n")
        subprocess.Popen([*cmd, "retake"], stdout=out, stderr=subprocess.STDOUT,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True


WHO = "war room"
REPLANS = actions.REPLANS
COLUMN_OF = {"title": "YouTube title", "description": "YouTube description", "hashtags": "Hashtags",
             "tags": "Tags", "playlist": "Playlist"}  # row field -> Kitsu column
ROW_OF = {c: f for f, c in COLUMN_OF.items()}
UNDO_MAX = 20


def _apply(cfg: dict, item: actions.Item, action: str, body: dict, who: str) -> dict:
    return actions.decide(cfg, item, action, who, text=body.get("text", ""),
                          on=bool(body.get("on", True)), values=body.get("values"))


def _before(cfg: dict, item: actions.Item) -> dict:
    """What an undo puts back: the item's status, hold, pin and texts as the page shows them."""
    r = actions.describe(cfg, item.stream_dir)[item.key]
    return {k: r[k] for k in ("status", "decision", "hold", "publish_at", "thumb_text", "uploaded",
                              *COLUMN_OF)}


# ---------- #23 undo ----------
# The last UNDO_MAX war room actions (single or bulk), newest first, in [paths] warroom_undo.
# Undo goes through actions.py like everything else, attributed "war room (undo)".

def undo_path(cfg: dict) -> Path:
    return Path(cfg["paths"]["warroom_undo"])


def remember(cfg: dict, action: str, label: str, done: list[dict]) -> str:
    """done: [{"ref", "before", "values"?}]. Returns the undo id."""
    uid = f"{int(time.time() * 1000):x}"
    entry = {"id": uid, "at": datetime.now(UTC).isoformat(timespec="seconds"), "action": action,
             "label": label, "items": done, "undone": False}
    undo_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    with jsonstate.update(undo_path(cfg), {"actions": []}) as d:
        d["actions"] = [entry, *d["actions"]][:UNDO_MAX]
    return uid


def recent_actions(cfg: dict) -> list[dict]:
    d = jsonstate.read(undo_path(cfg), {"actions": []})
    return [{k: a[k] for k in ("id", "at", "action", "label", "undone")}
            | {"n": len(a["items"]), "uploaded": sum(1 for x in a["items"] if x["before"].get("uploaded"))}
            for a in d["actions"]]


# ---------- saved panel layouts ----------
# The page arranges its panels in zones; a layout is {"zones": {zone: [panel, ...]},
# "widths": {"left": px, "right": px}}. Named layouts are kept here so every machine and
# browser sees them; the one in use is remembered by each browser.

LAYOUT_ZONES = ("top", "left", "middle", "right", "bottom", "hidden")


def layouts(cfg: dict) -> dict:
    return jsonstate.read(Path(cfg["paths"]["warroom_layouts"]), {"layouts": {}})


def save_layout(cfg: dict, body: dict) -> dict:
    """{name, layout} saves (or replaces) a named layout; {name, delete: true} removes it."""
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 40:
        raise ClipperError("a layout needs a name of 1-40 characters")
    lay = body.get("layout") or {}
    if not body.get("delete"):
        zones, widths = lay.get("zones"), lay.get("widths") or {}
        if not isinstance(zones, dict) or set(zones) - set(LAYOUT_ZONES) or not all(
                isinstance(v, list) and all(isinstance(x, str) and re.fullmatch(r"[a-z_]{1,30}", x) for x in v)
                for v in zones.values()):
            raise ClipperError("not a layout: zones must be lists of panel names")
        if set(widths) - {"left", "right"} or not all(isinstance(w, int) and 200 <= w <= 900 for w in widths.values()):
            raise ClipperError("not a layout: widths are left/right in px, 200-900")
        lay = {"zones": zones, "widths": widths}
    path = Path(cfg["paths"]["warroom_layouts"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with jsonstate.update(path, {"layouts": {}}) as d:
        if body.get("delete"):
            d["layouts"].pop(name, None)
        else:
            d["layouts"][name] = lay
        out = {"layouts": dict(d["layouts"])}
    return out


def _inverse(cfg: dict, item: actions.Item, action: str, before: dict, values: dict, who: str) -> dict:
    if action in ("approve", "reject", "changes", "reopen"):
        return actions.restore(cfg, item, before["status"], who)
    if action == "hold":
        return actions.hold(cfg, item, before["hold"], who)
    if action == "pin":
        return actions.pin(cfg, item, before["publish_at"], who)
    if action in ("edit", "replace"):
        return actions.edit_meta(cfg, item, {c: before[ROW_OF[c]] for c in values}, who)
    if action == "thumbtext":
        return actions.thumbtext(cfg, item, before["thumb_text"], who)
    raise ClipperError(f"{action} can't be undone")


def do_undo(cfg: dict, uid: str) -> dict:
    d = jsonstate.read(undo_path(cfg), {"actions": []})
    entry = next((a for a in d["actions"] if a["id"] == uid), None)
    if not entry:
        raise ClipperError(f"that action is too old to undo here (only the last {UNDO_MAX} are kept)")
    if entry["undone"]:
        raise ClipperError("already undone")
    who = f"{WHO} (undo)"
    ok, failed, rows = 0, [], []
    with LOCK:
        for x in entry["items"]:
            try:
                item = actions.resolve(cfg, x["ref"])
                rows.append(row_json(item.stream_dir, _inverse(cfg, item, entry["action"], x["before"],
                                                               x.get("values") or {}, who)))
                ok += 1
            except Exception as e:  # noqa: BLE001  # report each, undo the rest
                failed.append({"ref": x["ref"], "error": str(e)[:200]})
        if ok and cfg["discord"]["replan"] and entry["action"] in REPLANS:
            schedule.run(cfg, sync_first=False)
    with jsonstate.update(undo_path(cfg), {"actions": []}) as d:
        for a in d["actions"]:
            if a["id"] == uid:
                a["undone"] = not failed
    up = sum(1 for x in entry["items"] if x["before"].get("uploaded"))
    note = f"{up} already on YouTube: change {'it' if up == 1 else 'those'} in Studio too" if up else ""
    return {"undone": ok, "failed": failed, "rows": rows, "note": note}


REF = re.compile(r"(?:[\w.-]+/)?[\w.-]+")  # <stream>/<key> or a key: names, never paths (#76)


def checked_ref(ref) -> str:
    if not isinstance(ref, str) or not REF.fullmatch(ref) or ".." in ref:
        raise ClipperError(f"not an item name: {str(ref)[:60]!r}")
    return ref


def do_action(cfg: dict, body: dict) -> dict:
    action, ref = body.get("action", ""), checked_ref(body.get("ref", ""))
    with LOCK:
        item = actions.resolve(cfg, ref)
        before = _before(cfg, item)
        row, note = actions.replan(cfg, item, action, _apply(cfg, item, action, body, WHO))
    uid = remember(cfg, action, f"{action} {item.short}",
                   [{"ref": item.ref, "before": before, "values": body.get("values") or {}}])
    if action == "changes" and cfg["retake"]["auto"]:
        note = "clipper is working on the retake" if start_retake(cfg) else \
            "a retake run is going; this one is next"
    return {"row": row_json(item.stream_dir, row), "note": note, "undo": uid}


# ---------- #18 cover editor ----------

def cover_key(item: actions.Item) -> str:
    from . import thumbs

    return thumbs.video_key(item.stream_dir / item.state["file"].replace("\\", "/"))


def cover_get(cfg: dict, ref: str) -> dict:
    from . import thumbs

    item = actions.resolve(cfg, checked_ref(ref))
    sdir = item.stream_dir
    info = thumbs.cover_info(sdir, cfg, cover_key(item))
    for f in info["frames"]:
        f["url"] = media_url(sdir, sdir / f.pop("path"))
    jpg = (sdir / item.state["file"].replace("\\", "/")).with_suffix(".jpg")
    info["cover_url"] = media_url(sdir, jpg) + f"?v={int(jpg.stat().st_mtime)}" if jpg.exists() else ""
    return info


def cover_post(cfg: dict, body: dict) -> dict:
    from . import thumbs

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


# ---------- #54 faces: name a stream's groups, pick a face by hand ----------

GROUP_KEY = re.compile(r"F\d{2,4}")


def stream_path(cfg: dict, stream) -> Path:
    stream = checked_ref(stream)
    sdir = cfg["paths"]["streams"] / stream
    if "/" in stream or not (sdir / "meta.json").exists():
        raise ClipperError(f"no stream {stream!r}")
    return sdir


def faces_get(cfg: dict, stream: str) -> dict:
    """The known faces, and the stream's groups with a sheet or a name, biggest first."""
    from . import faces

    sdir = stream_path(cfg, stream)
    known = faces.load_known(for_stream(cfg, sdir))
    out = {"stream": sdir.name, "known": {n: len(r) for n, r in sorted(known["people"].items())},
           "scanned": False, "groups": [], "more": 0}
    data = load(sdir / "faces.json", None)
    if not data:
        return out
    try:  # one listing for every sheet's mtime, not a stat each over the share
        with os.scandir(sdir / "faces") as it:
            sheets = {e.name: int(e.stat().st_mtime) for e in it}
    except OSError:
        sheets = {}
    for key, g in data["groups"].items():
        sheet = sheets.get(f"{key}.jpg")
        if sheet or g.get("name"):
            out["groups"].append({"key": key, **{k: g.get(k) for k in (
                "n", "first_s", "last_s", "median_px", "cohesion", "name", "by", "closest", "match")},
                "sheet_url": media_url(sdir, sdir / "faces" / f"{key}.jpg") + f"?v={sheet}" if sheet else ""})
    out.update(scanned=True, more=len(data["groups"]) - len(out["groups"]), frames=data.get("frames"),
               faces=len(data["faces"]))
    return out


def faces_name(cfg: dict, body: dict) -> dict:
    """{"stream", "names": {"F03": "Jer", "F07": ""}, "enroll"}: hand names ("" clears one),
    then the groups named again; enroll keeps the named groups' faces for every show."""
    from . import faces

    sdir = stream_path(cfg, body.get("stream"))
    scfg = for_stream(cfg, sdir)
    names = body.get("names")
    if not isinstance(names, dict) or not names:
        raise ClipperError("no names")
    clean = {}
    for key, name in names.items():
        if not isinstance(key, str) or not GROUP_KEY.fullmatch(key):
            raise ClipperError(f"not a face group: {str(key)[:20]!r}")
        clean[key] = faces.checked_name(name) if name else ""
    faces.set_names(sdir, clean)
    faces.apply(sdir, scfg)
    if body.get("enroll") and (keys := [k for k, v in clean.items() if v]):
        faces.enroll(sdir, scfg, keys)
        faces.apply(sdir, scfg)  # the new samples may name other groups
    return faces_get(cfg, sdir.name)


def checked_box(b) -> list[float]:
    try:
        box = [float(b[k]) for k in ("x", "y", "width", "height")]
    except (KeyError, TypeError, ValueError):
        raise ClipperError("no box") from None
    if not (0 <= box[0] < 1 and 0 <= box[1] < 1 and 0.01 <= box[2] <= 1 and 0.01 <= box[3] <= 1):
        raise ClipperError("the box is outside the frame")
    return box


def faces_pick(cfg: dict, body: dict) -> dict:
    """{"ref", "t", "box"}: the face in that box of the item's video at t seconds (box 0-1 of
    the frame) and who it looks like. Its embedding comes back for faces_enroll."""
    from . import faces

    item = actions.resolve(cfg, checked_ref(body.get("ref", "")))
    sdir = item.stream_dir
    try:
        t = float(body.get("t"))
    except (TypeError, ValueError):
        t = -1.0
    if not 0 <= t < 86400:
        raise ClipperError("no time in the video")
    box = checked_box(body.get("box"))
    # the cut, full size, on the preview's timeline; but a compilation's preview joins its
    # segments' previews whole while the cut trims their overlaps: the preview watched, then
    video = sdir / item.state["file"].replace("\\", "/")
    preview = sdir / "review" / f"{item.key}.mp4"
    if item.state.get("kind") == "compilation" and preview.exists():
        video = preview
    with tempfile.TemporaryDirectory(prefix="clipper-face-") as tmp:
        found = faces.pick(faces.grab(video, t, Path(tmp) / "frame.jpg"), box, for_stream(cfg, sdir))
    found["tile"] = "data:image/jpeg;base64," + base64.b64encode(found["tile"]).decode()
    return {**found, "stream": sdir.name}


def faces_enroll(cfg: dict, body: dict) -> dict:
    """{"stream", "name", "embedding"}: keep a picked face as name's, then name the stream's groups again."""
    from . import faces

    sdir = stream_path(cfg, body.get("stream"))
    n = faces.enroll_pick(sdir, for_stream(cfg, sdir), body.get("name"), body.get("embedding"))
    return {**faces_get(cfg, sdir.name), "samples": n}


def faces_forget(cfg: dict, body: dict) -> dict:
    """{"stream", "name"}: drop name from the known faces, then name the stream's groups again."""
    from . import faces

    sdir = stream_path(cfg, body.get("stream"))
    scfg = for_stream(cfg, sdir)
    faces.forget(scfg, faces.checked_name(body.get("name")))
    if (sdir / "faces.json").exists():
        faces.apply(sdir, scfg)
    return faces_get(cfg, sdir.name)


# ---------- #41 the cast library: keep or drop each person's photos, find more ----------

CAST_JOB: dict = {"running": False, "person": "", "what": "", "log": [], "error": "", "added": 0}
CAST_LOCK = threading.Lock()


def cast_get(cfg: dict, person: str) -> dict:
    """Everyone with counts, one person's photos (served under /cast/), and the running job."""
    from . import cast

    out = cast.overview(cfg, person)
    if "person" in out:
        p = out["person"]
        for ph in p["photos"]:
            ph["url"] = f"/cast/{quote(p['name'])}/{quote(ph['file'])}"
        p["single_url"] = f"/cast/{quote(p['single'])}" if p["single"] else ""
    out["job"] = {**{k: CAST_JOB[k] for k in ("running", "person", "what", "error", "added")},
                  "log": CAST_JOB["log"][-40:]}
    return out


def cast_mark(cfg: dict, body: dict) -> dict:
    """{"person", "ids", "status": "keep" | "drop"}."""
    from . import cast

    ids = body.get("ids")
    if body.get("status") not in ("keep", "drop") or not isinstance(ids, list) \
            or not all(isinstance(i, str) and re.fullmatch(r"[0-9a-f]{10}", i) for i in ids):
        raise ClipperError("cast: status keep or drop, and photo ids")
    cast.set_status(cfg, body.get("person", ""), ids, body["status"])
    return cast_get(cfg, body.get("person", ""))


def cast_label(cfg: dict, body: dict) -> dict:
    from . import cast

    cast.set_label(cfg, body.get("person", ""), str(body.get("id", "")), str(body.get("label", "")))
    return cast_get(cfg, body.get("person", ""))


def cast_set(cfg: dict, body: dict) -> dict:
    """{"person", "aliases": [...], "channels": [...]}: a new person too."""
    from . import cast

    lists = [body.get(k) for k in ("aliases", "channels")]
    if not all(v is None or (isinstance(v, list) and all(isinstance(s, str) for s in v)) for v in lists):
        raise ClipperError("cast: aliases and channels are lists of text")
    cast.set_info(cfg, body.get("person", ""), *lists)
    return cast_get(cfg, body.get("person", ""))


def cast_run(cfg: dict, body: dict) -> dict:
    """{"person", "what": "find" (+ "sources") | "add" (+ "url") | "grab" (+ "stream")}, in the
    background: downloads, face checks and labels take a while. One at a time."""
    from . import cast

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
                from .llm import Claude

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


# ---------- #17 bulk actions, find and replace ----------

BULK = ("approve", "reopen", "hold", "pin", "changes", "reject")


def do_bulk(cfg: dict, body: dict) -> dict:
    action, refs = body.get("action", ""), [checked_ref(r) for r in body.get("refs") or []]
    if action not in BULK:
        raise ClipperError(f"{action!r} isn't a bulk action")
    if not refs:
        raise ClipperError("nothing selected")
    done, failed, rows = [], [], []
    with LOCK:
        for ref in refs:
            try:
                item = actions.resolve(cfg, ref)
                before = _before(cfg, item)
                rows.append(row_json(item.stream_dir, _apply(cfg, item, action, body, WHO)))
                done.append({"ref": item.ref, "before": before})
            except Exception as e:  # noqa: BLE001  # report each, do the rest
                failed.append({"ref": ref, "error": str(e)[:200]})
        if done and cfg["discord"]["replan"] and action in REPLANS:
            schedule.run(cfg, sync_first=False)
    uid = remember(cfg, action, f"{action} {len(done)} items", done) if done else ""
    if done and action == "changes" and cfg["retake"]["auto"]:
        start_retake(cfg)
    return {"done": len(done), "failed": failed, "rows": rows, "undo": uid}


def replace_plan(rows: list[dict], fields: list[str], find: str, repl: str, regex: bool,
                 case: bool, word: bool = False) -> list[dict]:
    """[{ref, short, values: {column: new}, lines: [{field, before, after}]}] for the rows
    whose fields change."""
    import re

    if not find:
        raise ClipperError("find what?")
    if not fields or any(f not in COLUMN_OF for f in fields):
        raise ClipperError(f"fields are {', '.join(COLUMN_OF)}")
    try:
        pat = find if regex else re.escape(find)
        pat = re.compile(rf"\b(?:{pat})\b" if word else pat, 0 if case else re.IGNORECASE)
    except re.error as e:
        raise ClipperError(f"bad pattern: {e}") from None
    out = []
    for r in rows:
        values, lines = {}, []
        for f in fields:
            old = r.get(f) or ""
            try:
                new = pat.sub(repl if regex else lambda _m: repl, old)
            except (re.error, IndexError) as e:
                raise ClipperError(f"bad replacement: {e}") from None
            if new == old:
                continue
            values[COLUMN_OF[f]] = new
            olds, news = old.splitlines() or [""], new.splitlines() or [""]
            lines += [{"field": f, "before": a, "after": b}
                      for a, b in zip(olds, news, strict=False) if a != b]
            if len(olds) != len(news):
                lines.append({"field": f, "before": f"({len(olds)} lines)", "after": f"({len(news)} lines)"})
        if values:
            out.append({"ref": r["ref"], "short": r["short"], "values": values, "lines": lines,
                        "uploaded": r.get("uploaded", False)})
    return out


def do_replace(cfg: dict, body: dict) -> dict:
    refs = {checked_ref(r) for r in body.get("refs") or []}
    if not refs:
        raise ClipperError("nothing selected")
    rows = [r for r in all_rows(cfg) if r["ref"] in refs]
    plan = replace_plan(rows, body.get("fields") or [], body.get("find", ""), body.get("replace", ""),
                        bool(body.get("regex")), bool(body.get("case")), bool(body.get("word")))
    if body.get("dry_run", True):
        return {"dry_run": True, "items": plan, "checked": len(rows)}
    done, failed, out_rows = [], [], []
    with LOCK:
        for p in plan:
            try:
                item = actions.resolve(cfg, p["ref"])
                before = _before(cfg, item)
                out_rows.append(row_json(item.stream_dir, actions.edit_meta(cfg, item, p["values"], WHO)))
                done.append({"ref": item.ref, "before": before, "values": p["values"]})
            except Exception as e:  # noqa: BLE001
                failed.append({"ref": p["ref"], "error": str(e)[:200]})
    uid = remember(cfg, "replace", f"replace {body.get('find', '')!r} in {len(done)} items", done) if done else ""
    return {"dry_run": False, "done": len(done), "failed": failed, "rows": out_rows, "undo": uid}


# ---------- #24 search the transcripts ----------

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
        state = actions.load_json(sdir / "review_state.json", {}) or {}
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


def sync_all(cfg: dict, stream: str | None = None) -> None:
    if SYNC["running"]:
        return
    SYNC.update(running=True, error="")

    def run():
        try:
            dirs = ([cfg["paths"]["streams"] / stream] if stream else actions.review_dirs(cfg))
            for d in dirs:
                review.sync(d, cfg, quiet=True)
        except Exception as e:  # noqa: BLE001
            SYNC["error"] = str(e)[:200]
        SYNC.update(running=False, last=datetime.now(UTC).isoformat(timespec="seconds"))

    threading.Thread(target=run, daemon=True).start()


# ---------- HTTP ----------

def watch_page(qs: dict) -> bytes:
    """#26: a bare player for one item's video, to pop out next to the war room."""
    import html

    src = (qs.get("src") or [""])[0]
    if not src.startswith("/media/"):
        raise ClipperError("no such video")
    title = html.escape((qs.get("title") or ["clipper"])[0])
    t = float((qs.get("t") or ["0"])[0] or 0)
    return (f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>html,body{{margin:0;height:100%;background:#000}}video{{display:block;width:100%;height:100%;object-fit:contain}}</style>
</head><body><video src="{html.escape(src)}#t={t:.1f}" controls autoplay></video></body></html>""").encode()


def _q(qs: dict, key: str) -> str:
    return (qs.get(key) or [""])[0]


def _sync(cfg: dict, body: dict) -> dict:
    stream = body.get("stream")
    sync_all(cfg, checked_ref(stream) if stream else None)
    return {"started": True}


def _replan(cfg: dict, body: dict) -> dict:
    with LOCK:
        schedule.run(cfg, sync_first=False)
    return {"ok": True}


def _check(cfg: dict, body: dict) -> dict:  # #19: style warnings while typing
    item = actions.resolve(cfg, checked_ref(body.get("ref", "")))
    return {"issues": actions.style_issues(for_stream(cfg, item.stream_dir), body.get("values") or {})}


# ---------- #110 news: stories about the shows' people ----------

def news_get(cfg: dict, days: str | float = "") -> dict:
    """The last few days' stories (dismissed ones too, marked), the shows, each source's state."""
    from . import news

    try:
        d = min(max(float(days or 3), 0.5), 30)
    except ValueError:
        d = 3
    return {"stories": news.stories(cfg, d, everything=True), "shows": news.show_names(cfg),
            "feeds": news.feeds(cfg), "check_minutes": cfg["news"]["check_minutes"],
            "watching": sorted(w.name for w in news.watchlist(cfg).values())}


def news_search(cfg: dict, q: str) -> dict:
    from . import news

    if not q.strip():
        return {"hits": []}
    return {"hits": news.search(cfg, q.strip()[:200])[:60]}


def _news_ids(body: dict) -> list[int]:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise ClipperError("news: ids is a list of story numbers")
    return ids


def news_dismiss(cfg: dict, body: dict) -> dict:
    from . import news

    news.dismiss(cfg, _news_ids(body))
    return news_get(cfg, body.get("days") or "")


def news_context(cfg: dict, body: dict) -> dict:
    """{ids, show?, preview?, days?}: preview returns the text, the stories it covers and the show
    without queueing it."""
    from . import news

    ids = _news_ids(body)
    if body.get("preview"):
        found = news.stories(cfg, ids=ids)
        text, used = news.context_block(found)
        return {"show": str(body.get("show") or (news.story_show(cfg, found[0]) if found else "")),
                "text": text, "ids": used}
    e = news.set_context(cfg, ids, str(body.get("show") or ""), who="war room")
    return {**news_get(cfg, body.get("days") or ""), "added": e}


# The API: path -> fn(cfg, query) for GET, fn(cfg, JSON body) for POST; each returns JSON.
GET_API = {
    "/api/overview": lambda cfg, qs: overview(cfg),
    "/api/items": lambda cfg, qs: all_rows(cfg),
    "/api/live": lambda cfg, qs: kick_live(cfg),
    "/api/lowres": lambda cfg, qs: lowres_status(cfg, _q(qs, "src")),
    "/api/retakes": lambda cfg, qs: retakes(cfg, 50),
    "/api/modal": lambda cfg, qs: modal_status(cfg),
    "/api/costs": lambda cfg, qs: costs_status(cfg),
    "/api/analytics": lambda cfg, qs: analytics_status(cfg, _q(qs, "month")),
    "/api/undo": lambda cfg, qs: {"actions": recent_actions(cfg)},
    "/api/cover": lambda cfg, qs: cover_get(cfg, _q(qs, "ref")),
    "/api/search": lambda cfg, qs: search_transcripts(cfg, _q(qs, "q")),
    "/api/layouts": lambda cfg, qs: layouts(cfg),
    "/api/faces": lambda cfg, qs: faces_get(cfg, _q(qs, "stream")),
    "/api/cast": lambda cfg, qs: cast_get(cfg, _q(qs, "person")),
    "/api/news": lambda cfg, qs: news_get(cfg, _q(qs, "days")),
    "/api/news/search": lambda cfg, qs: news_search(cfg, _q(qs, "q")),
}
POST_API = {
    "/api/check": _check,
    "/api/action": do_action,
    "/api/undo": lambda cfg, body: do_undo(cfg, body.get("id", "")),
    "/api/cover": cover_post,
    "/api/bulk": do_bulk,
    "/api/replace": do_replace,
    "/api/sync": _sync,
    "/api/replan": _replan,
    "/api/retake": lambda cfg, body: {"started": start_retake(cfg)},
    "/api/layouts": save_layout,
    "/api/faces/name": faces_name,
    "/api/faces/pick": faces_pick,
    "/api/faces/enroll": faces_enroll,
    "/api/faces/forget": faces_forget,
    "/api/cast/mark": cast_mark,
    "/api/cast/label": cast_label,
    "/api/cast/set": cast_set,
    "/api/cast/run": cast_run,
    "/api/news/dismiss": news_dismiss,
    "/api/news/context": news_context,
}
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class Handler(BaseHTTPRequestHandler):
    """One request. make_handler gives each server a subclass with its config."""
    server_version = "clipper-warroom"
    cfg: ClassVar[dict] = {}
    wc: ClassVar[dict] = {}
    streams_root: ClassVar[Path] = Path()

    def log_message(self, fmt, *args):  # quiet: only errors
        pass

    def authorized(self, qs: dict) -> bool:
        if not self.wc["token"]:
            return True
        got = (qs.get("key") or [""])[0] or self.headers.get("X-Key", "")
        if not got:
            cookie = self.headers.get("Cookie", "")
            got = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("wr=")), "")
        return hmac.compare_digest(got.encode(), self.wc["token"].encode())

    def foreign_host(self) -> bool:
        """B13: with no token, only requests addressed to this machine by a local name. A
        DNS-rebinding page (evil.example resolving to 127.0.0.1) counts as same-origin, so
        the Sec-Fetch check alone lets it in; its Host header still says evil.example."""
        if self.wc["token"]:
            return False
        host = self.headers.get("Host", "")
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return host not in LOCAL_HOSTS

    def cross_site(self) -> bool:
        """#76: a request another site's page made (a form, an <img>, a fetch). The war room
        trusts localhost, so those never reach the API or the media; links into the page
        itself (from Discord, a DM) still work."""
        site = self.headers.get("Sec-Fetch-Site", "")
        if site:  # every current browser sends it
            return site not in ("same-origin", "none")
        origin = self.headers.get("Origin", "")  # older ones; behind tailscale serve, the forwarded host
        hosts = {self.headers.get("Host", ""), self.headers.get("X-Forwarded-Host", "")}
        return bool(origin) and urlparse(origin).netloc not in hosts

    def fail(self, e: BaseException, status: int = 500) -> None:
        """Errors go to the log in full; the page gets the type only (no NAS paths or
        Kitsu replies over the network)."""
        print(f"warroom: {self.command} {urlparse(self.path).path}: {type(e).__name__}: {e}", flush=True)
        self.send_json({"error": f"{type(e).__name__} (details in the bot log)"}, status)

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if found := static_file(u.path):
            path, ctype, cache = found
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)
            return
        if self.foreign_host():
            return self.send_json({"error": "open the war room as localhost"}, 403)
        if not self.authorized(qs):
            return self.send_json({"error": "add ?key=<[warroom] token>"}, 401)
        if (u.path.startswith(("/api/", "/media/", "/cast/"))) and self.cross_site():
            return self.send_json({"error": "cross-site request refused"}, 403)
        try:
            if u.path in ("/", "/index.html"):
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if self.wc["token"] and qs.get("key"):
                    # A year, so the home-screen app (#25) stays signed in after it closes.
                    https = self.headers.get("X-Forwarded-Proto") == "https"  # tailscale serve
                    self.send_header("Set-Cookie", f"wr={self.wc['token']}; Path=/; Max-Age=31536000; "
                                                   "HttpOnly; SameSite=Lax"  # Lax: links from Discord etc. keep it
                                     + ("; Secure" if https else ""))
                self.end_headers()
                self.wfile.write(body)
            elif u.path in GET_API:
                self.send_json(GET_API[u.path](self.cfg, qs))
            elif u.path == "/watch":
                body = watch_page(qs)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path.startswith("/media/"):
                self.send_media(unquote(u.path[len("/media/"):]))
            elif u.path.startswith("/cast/"):
                self.send_cast(unquote(u.path[len("/cast/"):]))
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except ClipperError as e:  # clipper's own messages (a bad ref): safe to show
            self.send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self.fail(e)

    def do_POST(self):
        u = urlparse(self.path)
        # Read the body before any refusal: closing with it unread resets the connection
        # (Windows), and the client sees an abort instead of the answer. The page's own
        # posts are a few KB; a bigger body is read in chunks and thrown away.
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_POST:
            while n > 0 and (chunk := self.rfile.read(min(n, 1 << 16))):
                n -= len(chunk)
            return self.send_json({"error": "request too large"}, 413)
        raw = self.rfile.read(n)
        if self.foreign_host():
            return self.send_json({"error": "open the war room as localhost"}, 403)
        if not self.authorized(parse_qs(u.query)):
            return self.send_json({"error": "unauthorized"}, 401)
        # #76: only the page's own fetch() calls: JSON (a cross-site form can't send it
        # without a CORS preflight, which this server never answers) from this origin.
        if self.cross_site() or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send_json({"error": "cross-site or non-JSON request refused"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self.send_json({"error": "bad JSON"}, 400)
        if not isinstance(body, dict):
            return self.send_json({"error": "bad JSON"}, 400)
        try:
            if u.path in POST_API:
                self.send_json(POST_API[u.path](self.cfg, body))
            else:
                self.send_json({"error": "not found"}, 404)
        except ClipperError as e:  # clipper's own messages: safe to show
            self.send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self.fail(e)

    def send_cast(self, rel: str):
        """#41: a cast photo from [thumbnail] photos (outside the streams folder)."""
        d = self.cfg["thumbnail"]["photos"]
        root = Path(d).resolve() if d else None
        path = (root / rel).resolve() if root else None
        if not path or root not in path.parents or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp")                 or not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")  # a photo's file never changes
        self.end_headers()
        self.wfile.write(body)

    def send_media(self, rel: str):
        path = (self.streams_root / rel).resolve()
        if self.streams_root not in path.parents or not path.is_file() \
                or path.suffix.lower() not in (".mp4", ".jpg", ".png", ".srt"):
            return self.send_json({"error": "not found"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = min(int(b), size - 1) if a and b else end
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "max-age=60")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def make_handler(cfg: dict) -> type[Handler]:
    return type("Handler", (Handler,), {"cfg": cfg, "wc": cfg["warroom"],
                                        "streams_root": cfg["paths"]["streams"].resolve()})


# ---------- low-res copies for phones ----------

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
            print(f"warroom: low-res {LOWRES['error']}", flush=True)
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
            print(f"warroom: low-res prewarm: {type(e).__name__}: {e}", flush=True)
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


def token(cfg: dict) -> str:
    """[warroom] token, else the one `clipper warroom --tailnet on` keeps in [paths] warroom_token."""
    wc, f = _root(cfg)["warroom"], Path(cfg["paths"]["warroom_token"])
    return wc["token"] or (f.read_text(encoding="utf-8").strip() if f.is_file() else "")


def server(cfg: dict, port: int | None = None) -> ThreadingHTTPServer:
    cfg = _root(cfg)
    cfg = {**cfg, "warroom": {**cfg["warroom"], "token": token(cfg)}}
    wc = cfg["warroom"]
    if wc["host"] not in ("127.0.0.1", "localhost", "::1") and not wc["token"]:
        raise ClipperError("[warroom] host is reachable from other machines: set [warroom] token too")

    def warm():  # fill the per-stream cache so the first page load isn't the slow one
        try:
            overview(cfg)
        except Exception as e:  # noqa: BLE001
            print(f"warroom: warm-up: {type(e).__name__}: {e}", flush=True)
        modal_status(cfg)  # billing and the archive folders take seconds the first time
    threading.Thread(target=warm, name="warroom-warm", daemon=True).start()
    threading.Thread(target=_lowres_worker, args=(cfg,), name="warroom-lowres", daemon=True).start()
    if wc["lowres_prewarm"]:
        threading.Thread(target=_lowres_prewarm, args=(cfg,), name="warroom-prewarm", daemon=True).start()
    return ThreadingHTTPServer((wc["host"], port or wc["port"]), make_handler(cfg))


def url(cfg: dict, port: int | None = None, with_key: bool = True) -> str:
    wc, key = _root(cfg)["warroom"], token(cfg)
    host = "127.0.0.1" if wc["host"] in ("0.0.0.0", "::") else wc["host"]
    return f"http://{host}:{port or wc['port']}/" + (f"?key={key}" if key and with_key else "")


# ---------- tailnet: the phone, through `tailscale serve` (#10) ----------

def tailscale_exe() -> str | None:
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        p = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale" / "tailscale.exe"
        exe = str(p) if p.is_file() else None
    return exe


def _ts(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run([tailscale_exe() or "tailscale", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)


def tailnet_status(cfg: dict) -> dict:
    """Is Tailscale installed and up here, this machine's tailnet name, and the https URL
    `tailscale serve` proxies to the war room (if it does)."""
    if not tailscale_exe():
        return {"installed": False, "state": "", "name": "", "served": ""}
    st = json.loads(_ts("status", "--json").stdout or "{}")
    out = {"installed": True, "state": st.get("BackendState", ""),
           "name": ((st.get("Self") or {}).get("DNSName") or "").rstrip("."), "served": ""}
    port = _root(cfg)["warroom"]["port"]
    sv = json.loads(_ts("serve", "status", "--json").stdout or "{}")
    for hostport, web in (sv.get("Web") or {}).items():  # {"name:443": {"Handlers": {"/": {"Proxy": ...}}}}
        for path, h in (web.get("Handlers") or {}).items():
            if (h.get("Proxy") or "").rstrip("/") in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                out["served"] = f"https://{hostport.removesuffix(':443')}{path}"
    return out


def tailnet(cfg: dict, action: str) -> None:
    """`clipper warroom --tailnet on|off|status`: put the war room on this machine's tailnet
    name over HTTPS (tailnet devices only; nothing on the LAN or the internet)."""
    cfg = _root(cfg)
    wc, tok_file = cfg["warroom"], Path(cfg["paths"]["warroom_token"])
    st = tailnet_status(cfg)
    if not st["installed"]:
        raise ClipperError("Tailscale isn't installed here: install it from https://tailscale.com/download, "
                         "sign in, and put the phone on the same tailnet (SETUP.md, Phone access)")
    if st["state"] != "Running":
        raise ClipperError(f"Tailscale is {st['state'] or 'not running'}: sign in from its tray icon "
                         "(or `tailscale up`), then run this again")
    if action == "off":
        r = _ts("serve", "--https=443", "off")
        print((r.stdout + r.stderr).strip() or "the war room is off the tailnet")
        return
    if action == "on" and not st["served"]:
        if wc["host"] not in ("127.0.0.1", "localhost", "0.0.0.0", "::"):
            raise ClipperError(f"[warroom] host is {wc['host']}; tailscale serve proxies to 127.0.0.1")
        if not token(cfg):  # the tailnet is the boundary; the token is the second check
            import secrets
            tok_file.parent.mkdir(parents=True, exist_ok=True)
            tok_file.write_text(secrets.token_urlsafe(9), encoding="utf-8")
            print(f"made a war room token in {tok_file} (restart the bot so it asks for it)")
        # Not captured, no timeout: the first time it prints a link to enable Serve / HTTPS for
        # the tailnet and waits until that's done in the browser.
        print("starting tailscale serve (if it shows a link, open it and allow; this waits)", flush=True)
        r = subprocess.run([tailscale_exe(), "serve", "--bg", "--https=443",
                            f"http://127.0.0.1:{wc['port']}"], check=False)
        if r.returncode:
            raise ClipperError("tailscale serve failed (above). If it asks to enable HTTPS, do that in "
                             "the Tailscale admin console (DNS: MagicDNS and HTTPS certificates), then rerun")
        st = tailnet_status(cfg)
    if not st["served"]:
        print(f"Tailscale is up as {st['name']}; the war room isn't on the tailnet "
              "(clipper warroom --tailnet on)")
        return
    key = token(cfg)
    print(f"war room on the tailnet: {st['served']}")
    if key:  # printed here only, never logged: open it once on the phone, the cookie keeps it
        print(f"open once on the phone (signs it in for a year): {st['served'].rstrip('/')}/?key={key}")
    else:
        print("no war room token: any device on the tailnet can open it")
    if not wc["tailnet"]:
        print("set [warroom] tailnet = true in clipper.toml so `clipper doctor` checks it")


def run(cfg: dict, port: int | None = None, open_browser: bool = False) -> None:
    try:
        srv = server(cfg, port)
    except OSError as e:
        raise ClipperError(f"war room: port {port or _root(cfg)['warroom']['port']} is busy ({e}); "
                         "the Discord bot may already be serving it ([warroom] with_bot)") from None
    url_ = url(cfg, port)
    print(f"war room on {url_}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url_)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
