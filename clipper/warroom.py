"""The war room: a local web page with everything at once.

Usage:
    clipper warroom [--port 8765] [--open]

Serves http://127.0.0.1:<port>/ ([warroom] host/port): which shows are live on Kick, each
stream's pipeline progress, the review queue with previews and thumbnails (approve, ask for
changes, reject, hold, pin, edit title / description / hashtags / thumbnail words), the
release schedule, retakes and the bot's health. Decisions go through clipper/actions.py,
so they land in Kitsu exactly like the CLI's and the Discord bot's (Kitsu stays the
record); a "changes" decision starts `clipper retake` in the background, like the bot.

It reads the files the pipeline writes (review.json is as fresh as the last sync: the bot
syncs every few minutes, and the page's Sync button syncs now). Only listens on localhost
unless [warroom] host is changed, which then needs [warroom] token.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from zoneinfo import ZoneInfo

from . import actions, review, schedule, worker
from .config import _root, for_stream

PAGE = Path(__file__).with_name("web") / "warroom.html"
STATIC = {"/manifest.webmanifest": "application/manifest+json", "/icon.svg": "image/svg+xml",
          "/icon-192.png": "image/png", "/icon-512.png": "image/png"}  # no secrets: no key needed
LOCK = threading.Lock()  # one Kitsu write / replan at a time
SYNC = {"running": False, "last": None, "error": ""}
LIVE_CACHE: dict = {"at": 0.0, "data": []}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
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
            "channel": meta.get("channel", ""), "title": meta.get("title", ""),
            "started_at": meta.get("started_at", ""), "duration_s": meta.get("duration_s"),
            "stages": stages, "run_status": "running" if running else run.get("status", ""),
            "run_error": run.get("error", ""), "recording": live.get("status", ""),
            "published": (sdir / "review_state.json").exists()}


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
        except Exception as e:  # noqa: BLE001 - one broken stream shouldn't blank the page
            print(f"warroom: {sdir.name}: {type(e).__name__}: {e}", flush=True)
    return rows


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
    infos = sorted((_cached(d, "info", sig, lambda d=d: stream_info(cfg, d))
                    for d, names, sig in streams if "meta.json" in names),
                   key=lambda i: i["started_at"], reverse=True)
    rows = all_rows(cfg, streams)
    counts = {"pending": 0, "changes": 0, "approved": 0, "approved_unscheduled": 0,
              "rejected": 0, "held": 0}
    name = {"changes_requested": "changes"}
    for r in rows:
        d = name.get(r["decision"], r["decision"])
        counts[d] = counts.get(d, 0) + 1
        if d == "approved" and not r["scheduled"] and not r["uploaded"]:
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
                        for_stream(cfg, sd)["meta"]["show_name"],
                        "thumbnail_url": r.get("thumbnail_url", ""),
                        "views": views.get(e.get("video_id") or "")})
    entries.sort(key=lambda e: e["publish_at"])
    sc = cfg["schedule"]
    return {"generated_at": now.isoformat(), "timezone": sc["timezone"],
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
    cmd = [sys.executable, "-m", "clipper.cli"]
    if _root(cfg).get("_file"):
        cmd += ["--config", str(_root(cfg)["_file"])]
    log = Path(cfg["paths"]["bot_log"]).with_name("retake.log") if cfg["paths"]["bot_log"] else Path(os.devnull)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} war room\n")
        subprocess.Popen([*cmd, "retake"], stdout=out, stderr=subprocess.STDOUT,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True


def do_action(cfg: dict, body: dict) -> dict:
    action, ref = body.get("action", ""), body.get("ref", "")
    text = body.get("text", "")
    who = "war room"
    with LOCK:
        item = actions.resolve(cfg, ref)
        fn = {"approve": lambda: actions.approve(cfg, item, text, who),
              "reject": lambda: actions.reject(cfg, item, text, who),
              "changes": lambda: actions.changes(cfg, item, text, who),
              "reopen": lambda: actions.reopen(cfg, item, who),
              "hold": lambda: actions.hold(cfg, item, bool(body.get("on", True)), who),
              "pin": lambda: actions.pin(cfg, item, text, who),
              "edit": lambda: actions.edit_meta(cfg, item, body.get("values") or {}, who),
              "thumbtext": lambda: actions.thumbtext(cfg, item, text, who)}.get(action)
        if not fn:
            raise SystemExit(f"unknown action {action!r}")
        row = fn()
        note = ""
        if cfg["discord"]["replan"] and action in ("approve", "reject", "changes", "reopen", "hold", "pin"):
            schedule.run(cfg, sync_first=False)
            row = actions.describe(cfg, item.stream_dir)[item.key]
            if row["scheduled"]:
                note = f"scheduled {row['scheduled'][:16].replace('T', ' ')}"
    if action == "changes" and cfg["retake"]["auto"]:
        note = "clipper is working on the retake" if start_retake(cfg) else \
            "a retake run is going; this one is next"
    return {"row": row_json(item.stream_dir, row), "note": note}


def sync_all(cfg: dict, stream: str | None = None) -> None:
    if SYNC["running"]:
        return
    SYNC.update(running=True, error="")

    def run():
        try:
            dirs = ([cfg["paths"]["streams"] / stream] if stream else actions.review_dirs(cfg))
            for d in dirs:
                review.sync(d, cfg, quiet=True)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            SYNC["error"] = str(e)[:200]
        SYNC.update(running=False, last=datetime.now(UTC).isoformat(timespec="seconds"))

    threading.Thread(target=run, daemon=True).start()


# ---------- HTTP ----------

def watch_page(qs: dict) -> bytes:
    """#26: a bare player for one item's video, to pop out next to the war room."""
    import html

    src = (qs.get("src") or [""])[0]
    if not src.startswith("/media/"):
        raise SystemExit("no such video")
    title = html.escape((qs.get("title") or ["clipper"])[0])
    t = float((qs.get("t") or ["0"])[0] or 0)
    return (f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>html,body{{margin:0;height:100%;background:#000}}video{{display:block;width:100%;height:100%;object-fit:contain}}</style>
</head><body><video src="{html.escape(src)}#t={t:.1f}" controls autoplay></video></body></html>""").encode()


def make_handler(cfg: dict):
    wc = cfg["warroom"]
    streams_root = cfg["paths"]["streams"].resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "clipper-warroom"

        def log_message(self, fmt, *args):  # quiet: only errors
            pass

        def authorized(self, qs: dict) -> bool:
            if not wc["token"]:
                return True
            got = (qs.get("key") or [""])[0] or self.headers.get("X-Key", "")
            if not got:
                cookie = self.headers.get("Cookie", "")
                got = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("wr=")), "")
            return got == wc["token"]

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
            if u.path in STATIC:  # the phone's home-screen install fetches these without the cookie
                body = (PAGE.parent / u.path.lstrip("/")).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", STATIC[u.path])
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(body)
                return
            if not self.authorized(qs):
                return self.send_json({"error": "add ?key=<[warroom] token>"}, 401)
            try:
                if u.path in ("/", "/index.html"):
                    body = PAGE.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    if wc["token"] and qs.get("key"):
                        # A year, so the home-screen app (#25) stays signed in after it closes.
                        self.send_header("Set-Cookie", f"wr={wc['token']}; Path=/; Max-Age=31536000; "
                                                       "HttpOnly; SameSite=Lax")  # Lax: links from Discord etc. keep it
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/overview":
                    self.send_json(overview(cfg))
                elif u.path == "/api/items":
                    self.send_json(all_rows(cfg))
                elif u.path == "/api/live":
                    self.send_json(kick_live(cfg))
                elif u.path == "/watch":
                    body = watch_page(qs)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/lowres":
                    self.send_json(lowres_status(cfg, (qs.get("src") or [""])[0]))
                elif u.path == "/api/retakes":
                    self.send_json(retakes(cfg, 50))
                elif u.path == "/api/modal":
                    self.send_json(modal_status(cfg))
                elif u.path.startswith("/media/"):
                    self.send_media(unquote(u.path[len("/media/"):]))
                else:
                    self.send_json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (Exception, SystemExit) as e:  # noqa: BLE001
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            if not self.authorized(parse_qs(u.query)):
                return self.send_json({"error": "unauthorized"}, 401)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                if u.path == "/api/check":  # #19: style warnings while typing
                    item = actions.resolve(cfg, body.get("ref", ""))
                    sc = for_stream(cfg, item.stream_dir)
                    self.send_json({"issues": actions.style_issues(sc, body.get("values") or {})})
                elif u.path == "/api/action":
                    self.send_json(do_action(cfg, body))
                elif u.path == "/api/sync":
                    sync_all(cfg, body.get("stream"))
                    self.send_json({"started": True})
                elif u.path == "/api/replan":
                    with LOCK:
                        schedule.run(cfg, sync_first=False)
                    self.send_json({"ok": True})
                elif u.path == "/api/retake":
                    self.send_json({"started": start_retake(cfg)})
                else:
                    self.send_json({"error": "not found"}, 404)
            except SystemExit as e:
                self.send_json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

        def send_media(self, rel: str):
            path = (streams_root / rel).resolve()
            if streams_root not in path.parents or not path.is_file() \
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

    return Handler


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
        raise SystemExit("no such video")
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
        raise SystemExit("[warroom] host is reachable from other machines: set [warroom] token too")

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
        raise SystemExit("Tailscale isn't installed here: install it from https://tailscale.com/download, "
                         "sign in, and put the phone on the same tailnet (SETUP.md, Phone access)")
    if st["state"] != "Running":
        raise SystemExit(f"Tailscale is {st['state'] or 'not running'}: sign in from its tray icon "
                         "(or `tailscale up`), then run this again")
    if action == "off":
        r = _ts("serve", "--https=443", "off")
        print((r.stdout + r.stderr).strip() or "the war room is off the tailnet")
        return
    if action == "on" and not st["served"]:
        if wc["host"] not in ("127.0.0.1", "localhost", "0.0.0.0", "::"):
            raise SystemExit(f"[warroom] host is {wc['host']}; tailscale serve proxies to 127.0.0.1")
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
            raise SystemExit("tailscale serve failed (above). If it asks to enable HTTPS, do that in "
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
        raise SystemExit(f"war room: port {port or _root(cfg)['warroom']['port']} is busy ({e}); "
                         "the Discord bot may already be serving it ([warroom] with_bot)") from None
    url_ = url(cfg, port)
    print(f"war room on {url_}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url_)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
