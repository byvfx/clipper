"""TikTok drafts: scheduled Shorts -> the TikTok app's inbox, posted by hand from the phone.

  clipper tiktok --sign-in            one-time sign-in in your browser (TikTok Login Kit,
                                      desktop PKCE); token saved in paths.tiktok_token and
                                      refreshed automatically (access 24 h, refresh 365 days)
  clipper tiktok --items s02,t20      send these as drafts; each caption goes to Discord
  clipper tiktok --status [stream]    sent / posted / failed per Short; asks TikTok about
                                      drafts still waiting, so posted ones are marked posted

Uses the Content Posting API's Upload route (scope video.upload): the video lands in the
app's inbox and you open the notification, add a sound if you like, paste the caption and
post. The API takes no caption for drafts, so clipper posts it to Discord ([tiktok]
caption_channel_id, else the [record] alert webhook/channel) and prints it. Direct posting
(video.publish) needs TikTok's app audit and is private only until then (issue #29).

TikTok allows at most 5 pending drafts in any 24 hours; [tiktok] max_per_24h (3) stays under
that and paces a new account. Which Shorts go is picked by hand with --items until the
content rules (issue #27) are settled. Each send is recorded in schedule.json under
"platforms": {"tiktok": {...}} (as `clipper rumble` does), so nothing goes twice.

Client key and secret: paths.tiktok_client, JSON {"client_key": ..., "client_secret": ...}
from the app at developers.tiktok.com (Login Kit desktop redirect URI
http://localhost:<redirect_port>/callback/, products Login Kit + Content Posting API).
"""

from __future__ import annotations

import hashlib
import http.server
import json
import re
import secrets as pysecrets
import time
import urllib.parse
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from . import rumble, youtube
from .config import for_stream
from .review import draft_fields, hashtag_line

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.upload"
MB = 1024 * 1024
CHUNK = 32 * MB      # TikTok: chunks 5-64 MB, the last one up to 128 MB
SINGLE_MAX = 64 * MB


# ---------- auth ----------

def client(paths: dict) -> dict:
    p: Path = paths["tiktok_client"]
    if not p.exists():
        raise SystemExit(f"no TikTok client key; put {{\"client_key\": ..., \"client_secret\": ...}} in {p}")
    c = json.loads(p.read_text(encoding="utf-8-sig"))
    if not c.get("client_key") or not c.get("client_secret"):
        raise SystemExit(f"{p} needs client_key and client_secret")
    return c


def save_token(paths: dict, tok: dict) -> dict:
    now = time.time()
    tok = {**tok, "expires_at": now + int(tok["expires_in"]) - 300,
           "refresh_expires_at": now + int(tok.get("refresh_expires_in") or 0)}
    paths["tiktok_token"].write_text(json.dumps(tok, indent=2), encoding="utf-8")
    return tok


def token_request(c: dict, **form) -> dict:
    r = requests.post(f"{API}/oauth/token/", data={"client_key": c["client_key"],
                                                    "client_secret": c["client_secret"], **form},
                      timeout=30)
    body = r.json() if r.content else {}
    if r.status_code >= 400 or "access_token" not in body:
        raise SystemExit(f"TikTok token request failed: {body.get('error')} "
                         f"{body.get('error_description', r.text[:200])}")
    return body


def sign_in(cfg: dict) -> dict:
    """Browser sign-in with PKCE; TikTok wants the challenge as the hex SHA-256 digest."""
    paths, port = cfg["paths"], cfg["tiktok"]["redirect_port"]
    c = client(paths)
    redirect = f"http://localhost:{port}/callback/"
    verifier = pysecrets.token_urlsafe(64)[:96]
    state = pysecrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": c["client_key"], "scope": SCOPES, "redirect_uri": redirect,
        "state": state, "response_type": "code", "code_challenge_method": "S256",
        "code_challenge": hashlib.sha256(verifier.encode()).hexdigest()})
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if "code" in q or "error" in q:
                got.update(q)
            ok = q.get("state") == state and "code" in q
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("Signed in to TikTok; you can close this tab." if ok else
                              f"TikTok sign-in failed: {q.get('error_description') or q.get('error') or 'no code'}"
                              ).encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 5
    print(f"Opening your browser to sign in to TikTok: {url}")
    webbrowser.open(url)
    deadline = time.time() + 300
    while not got and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if not got:
        raise SystemExit("TikTok sign-in timed out (5 min); run: clipper tiktok --sign-in")
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"TikTok sign-in failed: {got.get('error_description') or got.get('error')}")
    tok = save_token(paths, token_request(c, code=got["code"], grant_type="authorization_code",
                                          redirect_uri=redirect, code_verifier=verifier))
    if "video.upload" not in tok.get("scope", ""):
        print(f"  warning: granted scopes are {tok.get('scope')!r}; video.upload is missing")
    return tok


def access_token(paths: dict) -> str:
    p: Path = paths["tiktok_token"]
    if not p.exists():
        raise SystemExit("not signed in to TikTok; run: clipper tiktok --sign-in")
    tok = json.loads(p.read_text(encoding="utf-8"))
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    if time.time() >= tok.get("refresh_expires_at", 0):
        raise SystemExit("TikTok sign-in expired; run: clipper tiktok --sign-in")
    try:
        tok = save_token(paths, token_request(client(paths), grant_type="refresh_token",
                                              refresh_token=tok["refresh_token"]))
    except SystemExit as e:
        raise SystemExit(f"{e}; run: clipper tiktok --sign-in")
    return tok["access_token"]


def call(paths: dict, path: str, body: dict | None = None, method: str = "POST") -> dict:
    r = requests.request(method, f"{API}{path}", json=body, timeout=60,
                         headers={"Authorization": f"Bearer {access_token(paths)}",
                                  "Content-Type": "application/json; charset=UTF-8"})
    out = r.json() if r.content else {}
    err = out.get("error") or {}
    if r.status_code >= 400 or err.get("code") not in (None, "ok"):
        raise SystemExit(f"TikTok {path}: {r.status_code} {err.get('code')} {err.get('message', '')}".strip())
    return out.get("data") or {}


def whoami(cfg: dict) -> str:
    u = call(cfg["paths"], "/user/info/?fields=open_id,display_name", method="GET").get("user", {})
    return u.get("display_name") or u.get("open_id", "?")


# ---------- captions ----------

def caption(e: dict, cfg: dict) -> str:
    """Title + hashtags (Kitsu's Hashtags column, else the draft's, then [tiktok]
    hashtags), banned words masked like the rest of the show's text."""
    from .bleep import Bleeper

    sdir = cfg["paths"]["streams"] / e["stream"]
    scfg = for_stream(cfg, sdir)
    tc = scfg["tiktok"]
    m = youtube.metadata(e, cfg)
    video = Path(e["file"])
    draft_path = sdir / "metadata" / video.parent.name / f"{video.stem}.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else {}
    review_path = sdir / "review.json"
    fields = (json.loads(review_path.read_text(encoding="utf-8")).get(m["review_key"], {})
              .get("fields", {}) if review_path.exists() else {})
    tags = (fields.get("Hashtags") or (draft_fields(draft).get("Hashtags") if draft else "") or "")
    tags = [t for t in dict.fromkeys(hashtag_line(tags).split() + hashtag_line(list(tc["hashtags"])).split())
            if t.lower() != "#shorts"]  # YouTube's tag
    title = Bleeper(scfg["bleep"]).mask(m["title"])
    text = f"{title}\n\n{' '.join(tags)}".strip()
    while len(text) > tc["max_caption_chars"] and tags:
        tags.pop()
        text = f"{title}\n\n{' '.join(tags)}".strip()
    return text[:tc["max_caption_chars"]]


# ---------- sending ----------

def tiktok_of(e: dict) -> dict:
    return (e.get("platforms") or {}).get("tiktok") or {}


def sent_last_24h(sched: dict) -> int:
    since = datetime.now(UTC) - timedelta(hours=24)
    return sum(1 for e in sched["entries"] + sched.get("unscheduled", [])
               if tiktok_of(e).get("sent_at") and tiktok_of(e).get("status") != "failed"
               and datetime.fromisoformat(tiktok_of(e)["sent_at"]) > since)


def upload_file(paths: dict, video: Path) -> str:
    size = video.stat().st_size
    chunk = size if size <= SINGLE_MAX else CHUNK
    count = max(1, size // chunk)
    data = call(paths, "/post/publish/inbox/video/init/", {"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
        "total_chunk_count": count}})
    with open(video, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size if i == count - 1 else start + chunk  # the last chunk takes the rest
            f.seek(start)
            body = f.read(end - start)
            for attempt in range(4):
                r = requests.put(data["upload_url"], data=body, timeout=600, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {start}-{end - 1}/{size}"})
                if r.status_code in (200, 201, 206):
                    break
                if r.status_code < 500 or attempt == 3:
                    raise SystemExit(f"TikTok upload chunk {i + 1}/{count}: {r.status_code} {r.text[:200]}")
                time.sleep(5 * (attempt + 1))
    return data["publish_id"]


def post_status(paths: dict, publish_id: str) -> dict:
    return call(paths, "/post/publish/status/fetch/", {"publish_id": publish_id})


def notify_caption(cfg: dict, e: dict, text: str) -> None:
    from .record import Alerts

    channel = cfg["tiktok"]["caption_channel_id"]
    alerts = Alerts({**cfg, "record": {**cfg["record"], "alert_mention": "",
                                       **({"alert_channel_id": channel} if channel else {})}})
    if alerts.webhook or alerts.channel:
        import logging

        alerts(f"TikTok draft sent: {e['key']}. Caption to paste:\n```\n{text}\n```", logging.INFO)


def send(cfg: dict, items: list[str], stream: str | None = None, dry_run: bool = False) -> None:
    tc = cfg["tiktok"]
    paths = cfg["paths"]
    sched = rumble.load_schedule(cfg)
    todo = [rumble.resolve(sched, r if "/" in r or not stream else f"{stream}/{r}") for r in items]
    room = tc["max_per_24h"] - sent_last_24h(sched)
    if not dry_run:
        print(f"TikTok account: {whoami(cfg)}; {max(room, 0)} of {tc['max_per_24h']} drafts left in 24 h")
    from .notify import probe_duration

    for e in todo:
        t = tiktok_of(e)
        if t.get("status") in ("sent", "posted"):
            print(f"  {e['key']}: already {t['status']} on TikTok ({t.get('sent_at', '')})")
            continue
        if e["kind"] not in tc["kinds"]:
            print(f"  {e['key']}: a {e['kind']}, not sent ([tiktok] kinds = {tc['kinds']})")
            continue
        video = Path(e["file"])
        if not video.exists():
            print(f"  {e['key']}: video missing ({video}); rerun clipper shorts")
            continue
        text = caption(e, cfg)
        secs = probe_duration(video)
        long = f"; {secs:.0f} s is past TikTok's 3 min for most accounts" if secs > 180 else ""
        if re.search(r"\bhosts?\b", text, re.IGNORECASE):
            print(f"  {e['key']}: warning: the caption says host(s); retitle it in Kitsu first")
        if dry_run:
            print(f"  {e['key']}: would send {video.name} ({video.stat().st_size / MB:.0f} MB, "
                  f"{secs:.0f} s{long})\n    caption: {text!r}")
            continue
        if room <= 0:
            print(f"  {e['key']}: not sent, {tc['max_per_24h']} drafts in the last 24 h "
                  "([tiktok] max_per_24h; TikTok allows 5 pending)")
            continue
        if long:
            print(f"  {e['key']}: warning{long}")
        publish_id = upload_file(paths, video)
        rec = {"status": "sent", "publish_id": publish_id, "caption": text,
               "sent_at": datetime.now(UTC).isoformat(timespec="seconds")}
        rumble.save_platform(cfg, {e["key"]: rec}, "tiktok")
        room -= 1
        print(f"  {e['key']}: sent to TikTok drafts ({publish_id})\n    caption: {text}")
        try:
            notify_caption(cfg, e, text)
        except Exception as ex:  # noqa: BLE001  # the draft is sent; the caption is in schedule.json
            print(f"  warning: caption not posted to Discord: {ex}")


def status(cfg: dict, stream: str | None = None, check: bool = True) -> None:
    sched = rumble.load_schedule(cfg)
    rows = [e for e in sched["entries"] + sched.get("unscheduled", [])
            if (not stream or e["stream"] == stream) and tiktok_of(e)]
    updates = {}
    for e in rows:
        t = tiktok_of(e)
        if check and t.get("status") == "sent":
            try:
                s = post_status(cfg["paths"], t["publish_id"])
            except SystemExit as ex:
                print(f"  {e['key']}: status check failed: {ex}")
                s = {}
            new = {"PUBLISH_COMPLETE": "posted", "FAILED": "failed"}.get(s.get("status", ""))
            if new:
                t = {**t, "status": new, "tiktok_status": s["status"],
                     **({"fail_reason": s["fail_reason"]} if s.get("fail_reason") else {}),
                     "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}
                updates[e["key"]] = t
        print(f"  {t.get('sent_at', '')[:16]}  {t['status']:<7} {e['key']:<34} {e['title'][:50]}"
              + (f"  ({t['fail_reason']})" if t.get("fail_reason") else ""))
    if updates:
        rumble.save_platform(cfg, updates, "tiktok")
    print(f"{len(rows)} on TikTok; {sent_last_24h(sched)} sent in the last 24 h "
          f"(max {cfg['tiktok']['max_per_24h']})")
    if not stream:
        left = [e for e in sched["entries"] if e["kind"] in cfg["tiktok"]["kinds"] and not tiktok_of(e)]
        print(f"{len(left)} scheduled Shorts not sent yet; send with: clipper tiktok --items <key>,...")
