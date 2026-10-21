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
    return [{**row_json(sdir, r, ls), "show": sc["show"]["key"],
             "show_name": sc["meta"]["show_name"] or ""}
            for r in actions.describe(cfg, sdir).values()]


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
    entries = []
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
                        "thumbnail_url": r.get("thumbnail_url", "")})
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
            "retakes": retakes(cfg), "health": health(cfg), "stages": worker.STAGES}


# ---------- actions ----------

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
            if not self.authorized(qs):
                return self.send_json({"error": "add ?key=<[warroom] token>"}, 401)
            try:
                if u.path in ("/", "/index.html"):
                    body = PAGE.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    if wc["token"] and qs.get("key"):
                        self.send_header("Set-Cookie", f"wr={wc['token']}; Path=/; SameSite=Strict")
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/overview":
                    self.send_json(overview(cfg))
                elif u.path == "/api/items":
                    self.send_json(all_rows(cfg))
                elif u.path == "/api/live":
                    self.send_json(kick_live(cfg))
                elif u.path == "/api/retakes":
                    self.send_json(retakes(cfg, 50))
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
                if u.path == "/api/action":
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


def server(cfg: dict, port: int | None = None) -> ThreadingHTTPServer:
    cfg = _root(cfg)
    wc = cfg["warroom"]
    if wc["host"] not in ("127.0.0.1", "localhost", "::1") and not wc["token"]:
        raise SystemExit("[warroom] host is reachable from other machines: set [warroom] token too")

    def warm():  # fill the per-stream cache so the first page load isn't the slow one
        try:
            overview(cfg)
        except Exception as e:  # noqa: BLE001
            print(f"warroom: warm-up: {type(e).__name__}: {e}", flush=True)
    threading.Thread(target=warm, name="warroom-warm", daemon=True).start()
    return ThreadingHTTPServer((wc["host"], port or wc["port"]), make_handler(cfg))


def url(cfg: dict, port: int | None = None) -> str:
    wc = _root(cfg)["warroom"]
    host = "127.0.0.1" if wc["host"] in ("0.0.0.0", "::") else wc["host"]
    return f"http://{host}:{port or wc['port']}/" + (f"?key={wc['token']}" if wc["token"] else "")


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
