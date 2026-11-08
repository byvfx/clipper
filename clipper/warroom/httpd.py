"""The HTTP server: static files, the GET and POST route tables, the token and host checks,
range requests for video, and `clipper warroom` itself (`run`)."""
from __future__ import annotations

import hmac
import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urlparse

from ..config import _root
from ..errors import ClipperError
from .cast import cast_get, cast_label, cast_mark, cast_run, cast_set
from .covers import cover_get, cover_post
from .data import (
    all_rows,
    analytics_status,
    costs_status,
    health,
    kick_live,
    modal_status,
    overview,
    retakes,
    usage_status,
)
from .faces import faces_enroll, faces_forget, faces_get, faces_name, faces_pick
from .investigations import investigations_get
from .lowres import _lowres_prewarm, _lowres_worker, lowres_status
from .news import news_context, news_dismiss, news_get, news_search
from .review import (
    _check,
    _replan,
    _sync,
    do_action,
    do_bulk,
    do_replace,
    do_slot,
    do_undo,
    recent_actions,
    start_retake,
)
from .saved_layouts import layouts, save_layout
from .search import search_transcripts
from .state import TAILNET, token
from .tailscale import tailnet_status

WEB = Path(__file__).resolve().parents[1] / "web"

PAGE = WEB / "warroom.html"   # markup only: its style is warroom.css, its code web/js/*.js

STATIC = {"/manifest.webmanifest": "application/manifest+json", "/icon.svg": "image/svg+xml",
          "/icon-192.png": "image/png", "/icon-512.png": "image/png"}  # no secrets: no key needed

CODE = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}

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

# The API: path -> fn(cfg, query) for GET, fn(cfg, JSON body) for POST; each returns JSON.
GET_API = {
    "/api/overview": lambda cfg, qs: overview(cfg),
    "/api/health": lambda cfg, qs: health(cfg),
    "/api/items": lambda cfg, qs: all_rows(cfg),
    "/api/live": lambda cfg, qs: kick_live(cfg),
    "/api/lowres": lambda cfg, qs: lowres_status(cfg, _q(qs, "src")),
    "/api/retakes": lambda cfg, qs: retakes(cfg, 50),
    "/api/modal": lambda cfg, qs: modal_status(cfg),
    "/api/costs": lambda cfg, qs: costs_status(cfg),
    "/api/usage": lambda cfg, qs: usage_status(cfg),
    "/api/analytics": lambda cfg, qs: analytics_status(cfg, _q(qs, "month")),
    "/api/undo": lambda cfg, qs: {"actions": recent_actions(cfg)},
    "/api/cover": lambda cfg, qs: cover_get(cfg, _q(qs, "ref")),
    "/api/search": lambda cfg, qs: search_transcripts(cfg, _q(qs, "q")),
    "/api/layouts": lambda cfg, qs: layouts(cfg),
    "/api/faces": lambda cfg, qs: faces_get(cfg, _q(qs, "stream")),
    "/api/cast": lambda cfg, qs: cast_get(cfg, _q(qs, "person")),
    "/api/news": lambda cfg, qs: news_get(cfg, _q(qs, "days")),
    "/api/news/search": lambda cfg, qs: news_search(cfg, _q(qs, "q")),
    "/api/investigations": lambda cfg, qs: investigations_get(cfg, _q(qs, "subject"), _q(qs, "run")),
}

POST_API = {
    "/api/check": _check,
    "/api/action": do_action,
    "/api/undo": lambda cfg, body: do_undo(cfg, body.get("id", "")),
    "/api/cover": cover_post,
    "/api/bulk": do_bulk,
    "/api/slot": do_slot,
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
        self.send_json({"error": f"{type(e).__name__} (details in the serve log)"}, status)

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
        if wc["tailnet"]:  # the phone's URL, for Health and the DMs (tailscale status takes a moment)
            try:
                TAILNET["served"] = tailnet_status(cfg)["served"]
            except Exception as e:  # noqa: BLE001
                print(f"warroom: tailnet status: {type(e).__name__}: {e}", flush=True)
    threading.Thread(target=warm, name="warroom-warm", daemon=True).start()
    threading.Thread(target=_lowres_worker, args=(cfg,), name="warroom-lowres", daemon=True).start()
    if wc["lowres_prewarm"]:
        threading.Thread(target=_lowres_prewarm, args=(cfg,), name="warroom-prewarm", daemon=True).start()
    return ThreadingHTTPServer((wc["host"], port or wc["port"]), make_handler(cfg))

def url(cfg: dict, port: int | None = None, with_key: bool = True) -> str:
    wc, key = _root(cfg)["warroom"], token(cfg)
    host = "127.0.0.1" if wc["host"] in ("0.0.0.0", "::") else wc["host"]
    return f"http://{host}:{port or wc['port']}/" + (f"?key={key}" if key and with_key else "")

def run(cfg: dict, port: int | None = None, open_browser: bool = False) -> None:
    try:
        srv = server(cfg, port)
    except OSError as e:
        raise ClipperError(f"war room: port {port or _root(cfg)['warroom']['port']} is busy ({e}); "
                         "clipper serve may already be serving it") from None
    url_ = url(cfg, port)
    print(f"war room on {url_}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url_)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
