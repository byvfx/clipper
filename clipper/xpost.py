"""X posts: scheduled Shorts -> the Lolcow Cinema X account as native video posts.

  clipper x --sign-in          one-time sign-in in your browser (OAuth 2.0 with PKCE); token
                               saved in paths.x_token and refreshed automatically
  clipper x --items s02,t20    post these now (a failed one is tried again)
  clipper x --auto             post the Shorts that went live on YouTube in the last
                               max_age_hours and aren't on X yet, earliest slot first, up to
                               max_per_24h (the bot runs this every
                               [x] auto_post_minutes; 0, the default, means only by hand)
  clipper x [stream]           what was posted, with links

The video goes up through the v2 chunked media upload (initialize / append / finalize, then
STATUS until X has processed it) and the post is POST /2/tweets with its media id. The text
is the Short's title and up to [x] max_hashtags hashtags, banned words masked as everywhere
else. No link goes in the text: X bills a post with a URL at $0.20 against $0.015 (pay-per-use
pricing, 2026). Each post's cost is logged to paths.costs like a model call (`clipper costs`).

X's recommended portrait size is 720x1280 and its docs cap dimensions near that, so a Short
taller than [x] max_height is re-encoded to fit first (into streams/<id>/x/, deleted after).

Each post is recorded in schedule.json under "platforms": {"x": {...}} (as `clipper tiktok`
does), so nothing goes twice. --auto skips titles that say "host(s)".

Keys: paths.x_client, JSON {"client_id": ...} for a Native App (public client), plus
"client_secret" for a Web App (confidential client), from the app's "Keys and tokens" page
at console.x.com. Its callback URL is http://127.0.0.1:<redirect_port>/callback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets as pysecrets
import subprocess
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from . import costs, jsonstate, media, oauth, rumble
from .config import for_stream
from .errors import ClipperError
from .states import split_key

AUTH_URL = "https://x.com/i/oauth2/authorize"
API = "https://api.x.com/2"
SCOPES = "tweet.read tweet.write users.read media.write offline.access"
MB = 1024 * 1024
CHUNK = 4 * MB       # X: keep each appended segment at or below 5 MB (the server takes 8)


class XError(ClipperError):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


# ---------- auth ----------

def client(paths: dict) -> dict:
    p: Path = paths["x_client"]
    if not p.exists():
        raise ClipperError(f"no X client id; put {{\"client_id\": ...}} in {p} (see SETUP.md, X)")
    c = json.loads(p.read_text(encoding="utf-8-sig"))
    if not c.get("client_id"):
        raise ClipperError(f"{p} needs client_id (and client_secret for a Web App)")
    return c


def token_request(c: dict, **form) -> dict:
    """A confidential client authenticates with HTTP Basic; a public one sends its id."""
    auth = (c["client_id"], c["client_secret"]) if c.get("client_secret") else None
    if not auth:
        form["client_id"] = c["client_id"]
    r = requests.post(f"{API}/oauth2/token", data=form, auth=auth, timeout=30)
    body = r.json() if r.content else {}
    if r.status_code >= 400 or "access_token" not in body:
        raise ClipperError(f"X token request failed: {body.get('error')} "
                           f"{body.get('error_description', r.text[:200])}")
    return body


def save_token(tok: dict, old: dict | None = None) -> dict:
    """The token as stored. Refresh tokens are single-use: keep the new one, or the old one
    if X sent none."""
    return {**tok, "refresh_token": tok.get("refresh_token") or (old or {}).get("refresh_token"),
            "expires_at": time.time() + int(tok["expires_in"]) - 300}


def sign_in(cfg: dict) -> dict:
    paths, port = cfg["paths"], cfg["x"]["redirect_port"]
    c = client(paths)
    redirect = f"http://127.0.0.1:{port}/callback"
    verifier = pysecrets.token_urlsafe(64)[:96]
    state = pysecrets.token_urlsafe(16)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": c["client_id"], "redirect_uri": redirect,
        "scope": SCOPES, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"})
    code = oauth.browser_code(url, port, state, "X", "clipper x --sign-in")
    tok = save_token(token_request(c, code=code, grant_type="authorization_code",
                                   redirect_uri=redirect, code_verifier=verifier))
    jsonstate.write(paths["x_token"], tok)
    missing = set(SCOPES.split()) - set(tok.get("scope", "").split())
    if missing:
        print(f"  warning: granted scopes are {tok.get('scope')!r}; missing {' '.join(sorted(missing))}")
    return tok


def access_token(paths: dict) -> str:
    """Under the token file's lock: two runs refreshing at once would spend the same
    single-use refresh token and sign the second one out."""
    p: Path = paths["x_token"]
    if not p.exists():
        raise ClipperError("not signed in to X; run: clipper x --sign-in")
    with jsonstate.update(p) as tok:
        if time.time() >= tok.get("expires_at", 0):
            if not tok.get("refresh_token"):
                raise ClipperError("X sign-in expired; run: clipper x --sign-in")
            try:
                new = save_token(token_request(client(paths), grant_type="refresh_token",
                                               refresh_token=tok["refresh_token"]), tok)
            except ClipperError as e:
                raise ClipperError(f"{e}; run: clipper x --sign-in")
            tok.clear()
            tok.update(new)
        return tok["access_token"]


def call(paths: dict, method: str, path: str, **kw) -> dict:
    r = requests.request(method, f"{API}{path}", timeout=kw.pop("timeout", 60),
                         headers={"Authorization": f"Bearer {access_token(paths)}"}, **kw)
    out = r.json() if r.content else {}
    if r.status_code >= 400:
        detail = (out.get("detail") or "; ".join(e.get("message", "") for e in out.get("errors") or [])
                  or out.get("title") or r.text[:200])
        hint = {402: " (out of API credits: buy some at console.x.com)",
                429: " (rate limited: try again later)"}.get(r.status_code, "")
        raise XError(f"X {method} {path.split('?')[0]}: {r.status_code} {detail}{hint}", r.status_code)
    return out


def whoami(cfg: dict) -> str:
    return call(cfg["paths"], "GET", "/users/me")["data"]["username"]


# ---------- media ----------

def fit(video: Path, sdir: Path, max_height: int) -> Path:
    """`video`, or a copy re-encoded to max_height in sdir/x/ if it is taller (H.264 High,
    AAC, as X recommends)."""
    if not max_height or media.size(video)[1] <= max_height:
        return video
    out = sdir / "x" / f"{video.stem}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", f"scale=-2:{max_height}",
                    "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "18",
                    "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
                   check=True)
    return out


def upload_video(paths: dict, video: Path) -> str:
    size = video.stat().st_size
    mid = call(paths, "POST", "/media/upload/initialize", json={
        "media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"})["data"]["id"]
    with open(video, "rb") as f:
        for i, start in enumerate(range(0, size, CHUNK)):
            f.seek(start)
            chunk = f.read(CHUNK)
            for attempt in range(4):
                try:
                    call(paths, "POST", f"/media/upload/{mid}/append", timeout=300,
                         data={"segment_index": i}, files={"media": ("chunk", chunk, "application/octet-stream")})
                    break
                except (XError, requests.RequestException) as e:
                    if attempt == 3 or 400 <= getattr(e, "status", 0) < 500:
                        raise
                    time.sleep(5 * (attempt + 1))
    info = call(paths, "POST", f"/media/upload/{mid}/finalize")["data"].get("processing_info")
    deadline = time.time() + 600
    while info and info.get("state") not in ("succeeded", None):
        if info["state"] == "failed":
            err = info.get("error") or {}
            raise ClipperError(f"X couldn't process {video.name}: {err.get('message') or err or 'failed'}")
        if time.time() > deadline:
            raise ClipperError(f"X still processing {video.name} after 10 min (media {mid})")
        time.sleep(max(1, int(info.get("check_after_secs") or 5)))
        info = call(paths, "GET", f"/media/upload?command=STATUS&media_id={mid}")["data"].get("processing_info")
    return mid


# ---------- text ----------

def post_text(title: str, tags: list[str], limit: int, max_tags: int) -> str:
    """Title, then up to max_tags hashtags, inside X's limit (tags go first, then the title
    is shortened)."""
    tags = list(dict.fromkeys(tags))[:max_tags]
    text = f"{title}\n\n{' '.join(tags)}".strip()
    while len(text) > limit and tags:
        tags.pop()
        text = f"{title}\n\n{' '.join(tags)}".strip()
    return text if len(text) <= limit else title[:limit - 1].rstrip() + "…"


def text_for(e: dict, cfg: dict) -> str:
    xc = for_stream(cfg, cfg["paths"]["streams"] / e["stream"])["x"]
    title, tags = rumble.title_and_tags(e, cfg, "x")
    return post_text(title, tags, xc["max_chars"], xc["max_hashtags"])


# ---------- posting ----------

def x_of(e: dict) -> dict:
    return (e.get("platforms") or {}).get("x") or {}


def posted_last_24h(sched: dict, now: datetime | None = None) -> int:
    since = (now or datetime.now(UTC)) - timedelta(hours=24)
    return sum(1 for e in sched["entries"] + sched.get("unscheduled", [])
               if x_of(e).get("status") == "posted"
               and datetime.fromisoformat(x_of(e)["posted_at"]) > since)


def due(sched: dict, xc: dict, now: datetime | None = None) -> list[dict]:
    """Entries --auto would post, earliest slot first: the right kind, never tried on X,
    (with after_youtube) uploaded with their slot passed, i.e. public on YouTube, and that
    slot no more than max_age_hours ago, so X stays current instead of working off a backlog."""
    now = now or datetime.now(UTC)
    oldest = now - timedelta(hours=xc["max_age_hours"]) if xc["max_age_hours"] else None

    def ready(e: dict) -> bool:
        at = datetime.fromisoformat(e["publish_at"]) if e.get("publish_at") else None
        if oldest and at and at < oldest:
            return False
        if not xc["after_youtube"]:
            return e.get("status") in ("scheduled", "uploaded")
        return e.get("status") == "uploaded" and (not at or at <= now)

    return sorted((e for e in sched["entries"] if e["kind"] in xc["kinds"] and not x_of(e) and ready(e)),
                  key=lambda e: e.get("publish_at") or "")


def post(cfg: dict, items: list[str], stream: str | None = None, dry_run: bool = False) -> None:
    xc, paths = cfg["x"], cfg["paths"]
    sched = rumble.load_schedule(cfg)
    todo = [rumble.resolve(sched, r if "/" in r or not stream else f"{stream}/{r}") for r in items]
    room = xc["max_per_24h"] - posted_last_24h(sched)
    if not dry_run:
        user = whoami(cfg)
        print(f"X account: @{user}; {max(room, 0)} of {xc['max_per_24h']} posts left in 24 h")

    for e in todo:
        x = x_of(e)
        if x.get("status") == "posted":
            print(f"  {e['key']}: already on X ({x['url']})")
            continue
        if e["kind"] not in xc["kinds"]:
            print(f"  {e['key']}: a {e['kind']}, not posted ([x] kinds = {xc['kinds']})")
            continue
        video = Path(e["file"])
        if not video.exists():
            print(f"  {e['key']}: video missing ({video}); rerun clipper shorts")
            continue
        text = text_for(e, cfg)
        if re.search(r"\bhosts?\b", text, re.IGNORECASE):
            print(f"  {e['key']}: warning: the text says host(s); retitle it in Kitsu first")
        if dry_run:
            print(f"  {e['key']}: would post {video.name} ({video.stat().st_size / MB:.0f} MB, "
                  f"{media.duration(video):.0f} s)\n    text: {text!r}")
            continue
        if room <= 0:
            print(f"  {e['key']}: not posted, {xc['max_per_24h']} posts in the last 24 h ([x] max_per_24h)")
            continue
        sdir = paths["streams"] / e["stream"]
        try:
            sendable = fit(video, sdir, xc["max_height"])
            mid = upload_video(paths, sendable)
            pid = call(paths, "POST", "/tweets", json={"text": text, "media": {"media_ids": [mid]}})["data"]["id"]
        except (ClipperError, requests.RequestException, subprocess.CalledProcessError) as ex:
            rumble.save_platform(cfg, {e["key"]: {"status": "failed", "error": str(ex)[:300], "text": text,
                                                  "failed_at": costs.now_iso()}}, "x")
            print(f"  {e['key']}: FAILED: {ex}")
            continue
        if sendable != video:
            sendable.unlink(missing_ok=True)
        url = f"https://x.com/{user}/status/{pid}"
        rumble.save_platform(cfg, {e["key"]: {"status": "posted", "post_id": pid, "media_id": mid,
                                              "url": url, "text": text, "posted_at": costs.now_iso()}}, "x")
        costs.record(paths.get("costs"), {"ts": costs.now_iso(), "kind": "x", "provider": "x",
                                          "model": "post", "stage": "x", "stream": e["stream"],
                                          "usd": xc["post_usd"]})
        room -= 1
        print(f"  {e['key']}: posted {url}\n    text: {text}")


def auto(cfg: dict, dry_run: bool = False) -> None:
    xc = cfg["x"]
    sched = rumble.load_schedule(cfg)
    room = max(xc["max_per_24h"] - posted_last_24h(sched), 0)
    queue = [e for e in due(sched, xc) if Path(e["file"]).exists()]
    print(f"X: {len(queue)} Shorts due and not posted; {room} of {xc['max_per_24h']} posts left in 24 h")
    picked = []
    for e in queue:
        if len(picked) >= room:
            break
        if re.search(r"\bhosts?\b", text_for(e, cfg), re.IGNORECASE):
            print(f"  {e['key']}: skipped, the title says host(s); retitle it in Kitsu")
            continue
        picked.append(e["key"])
    if picked:
        post(cfg, picked, dry_run=dry_run)


def status(cfg: dict, stream: str | None = None) -> None:
    sched = rumble.load_schedule(cfg)
    rows = [e for e in sched["entries"] + sched.get("unscheduled", [])
            if (not stream or split_key(e["key"])[0] == stream) and x_of(e)]
    for e in rows:
        x = x_of(e)
        when = (x.get("posted_at") or x.get("failed_at") or "")[:16]
        print(f"  {when}  {x['status']:<6} {e['key']:<34} {x.get('url') or x.get('error', '')[:80]}")
    print(f"{len(rows)} on X; {posted_last_24h(sched)} posted in the last 24 h (max {cfg['x']['max_per_24h']})")
    if not stream:
        print(f"{len(due(sched, cfg['x']))} Shorts due and not posted; post with: clipper x --items <key>,... "
              "or clipper x --auto")
