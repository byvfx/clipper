"""TikTok drafts: scheduled Shorts -> the TikTok app's inbox, posted by hand from the phone.

  clipper tiktok --sign-in            one-time sign-in in your browser (TikTok Login Kit,
                                      desktop PKCE); token saved in paths.tiktok_token and
                                      refreshed automatically (access 24 h, refresh 365 days)
  clipper tiktok --items s02,t20      send these as drafts; each caption goes to Discord
  clipper tiktok --items ... --resend send again drafts that expired unposted in the app
                                      (TikTok's status still says SEND_TO_USER_INBOX; the
                                      old send is kept under "previous" as "expired")
  clipper tiktok --auto               send the next unsent Shorts, in YouTube-schedule order,
                                      as far as max_per_24h allows (the bot runs this every
                                      [tiktok] auto_send_minutes)
  clipper tiktok --caption t20        post a sent draft's caption to Discord again
  clipper tiktok [stream]             sent / posted / failed per Short; asks TikTok about
                                      drafts still waiting, so posted ones are marked posted

Uses the Content Posting API's Upload route (scope video.upload): the video lands in the
app's inbox and you open the notification, add a sound if you like, paste the caption and
post. The API takes no caption for drafts, so clipper posts it to Discord ([tiktok]
caption_channel_id, else the [record] alert webhook/channel) and prints it.

Parked (user, 2026-11-01): auto-send has been off since 2026-10-28 (TikTok flagged the drafts
as bot activity) and the user posts by hand. Direct posting (video.publish) is dropped: it
needs TikTok's app audit, which refuses tools that only post to your own accounts (#62, #116).
This module still works for hand sends; picking it up again starts from the wiki's TikTok page.

TikTok allows at most 5 pending drafts in any 24 hours; [tiktok] max_per_24h (default 3)
keeps under that. --auto (user, 2026-10-22) sends every scheduled Short in the order YouTube
gets them, skipping captions that say "host(s)"; --items picks by hand. Each send is recorded in schedule.json under
"platforms": {"tiktok": {...}} (as `clipper rumble` does), so nothing goes twice.

Client key and secret: paths.tiktok_client, JSON {"client_key": ..., "client_secret": ...}
from the app at developers.tiktok.com (Login Kit desktop redirect URI
http://localhost:<redirect_port>/callback/, products Login Kit + Content Posting API).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets as pysecrets
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from . import jsonstate, media, oauth, rumble, schedule
from .config import for_stream
from .errors import ClipperError
from .states import split_key

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
        raise ClipperError(f"no TikTok client key; put {{\"client_key\": ..., \"client_secret\": ...}} in {p}")
    c = json.loads(p.read_text(encoding="utf-8-sig"))
    if not c.get("client_key") or not c.get("client_secret"):
        raise ClipperError(f"{p} needs client_key and client_secret")
    return c


def save_token(paths: dict, tok: dict) -> dict:
    now = time.time()
    tok = {**tok, "expires_at": now + int(tok["expires_in"]) - 300,
           "refresh_expires_at": now + int(tok.get("refresh_expires_in") or 0)}
    jsonstate.write(paths["tiktok_token"], tok)
    return tok


def token_request(c: dict, **form) -> dict:
    r = requests.post(f"{API}/oauth/token/", data={"client_key": c["client_key"],
                                                    "client_secret": c["client_secret"], **form},
                      timeout=30)
    body = r.json() if r.content else {}
    if r.status_code >= 400 or "access_token" not in body:
        raise ClipperError(f"TikTok token request failed: {body.get('error')} "
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
    code = oauth.browser_code(url, port, state, "TikTok", "clipper tiktok --sign-in")
    tok = save_token(paths, token_request(c, code=code, grant_type="authorization_code",
                                          redirect_uri=redirect, code_verifier=verifier))
    if "video.upload" not in tok.get("scope", ""):
        print(f"  warning: granted scopes are {tok.get('scope')!r}; video.upload is missing")
    return tok


def access_token(paths: dict) -> str:
    p: Path = paths["tiktok_token"]
    if not p.exists():
        raise ClipperError("not signed in to TikTok; run: clipper tiktok --sign-in")
    tok = jsonstate.load(p)
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    if time.time() >= tok.get("refresh_expires_at", 0):
        raise ClipperError("TikTok sign-in expired; run: clipper tiktok --sign-in")
    try:
        tok = save_token(paths, token_request(client(paths), grant_type="refresh_token",
                                              refresh_token=tok["refresh_token"]))
    except ClipperError as e:
        raise ClipperError(f"{e}; run: clipper tiktok --sign-in")
    return tok["access_token"]


def call(paths: dict, path: str, body: dict | None = None, method: str = "POST") -> dict:
    r = requests.request(method, f"{API}{path}", json=body, timeout=60,
                         headers={"Authorization": f"Bearer {access_token(paths)}",
                                  "Content-Type": "application/json; charset=UTF-8"})
    out = r.json() if r.content else {}
    err = out.get("error") or {}
    if r.status_code >= 400 or err.get("code") not in (None, "ok"):
        raise ClipperError(f"TikTok {path}: {r.status_code} {err.get('code')} {err.get('message', '')}".strip())
    return out.get("data") or {}


def whoami(cfg: dict) -> str:
    u = call(cfg["paths"], "/user/info/?fields=open_id,display_name", method="GET").get("user", {})
    return u.get("display_name") or u.get("open_id", "?")


# ---------- captions ----------

def caption(e: dict, cfg: dict) -> str:
    """Title + hashtags (Kitsu's Hashtags column, else the draft's, then [tiktok]
    hashtags), banned words masked like the rest of the show's text."""
    limit = for_stream(cfg, cfg["paths"]["streams"] / e["stream"])["tiktok"]["max_caption_chars"]
    title, tags = rumble.title_and_tags(e, cfg, "tiktok")
    text = f"{title}\n\n{' '.join(tags)}".strip()
    while len(text) > limit and tags:
        tags.pop()
        text = f"{title}\n\n{' '.join(tags)}".strip()
    return text[:limit]


# ---------- sending ----------

def tiktok_of(e: dict) -> dict:
    return (e.get("platforms") or {}).get("tiktok") or {}


def sent_last_24h(sched: dict, exclude: set[str] = frozenset()) -> int:
    """Drafts sent in the last 24 h that may still be pending (`exclude`: keys about to be
    sent again, whose old drafts have expired)."""
    since = datetime.now(UTC) - timedelta(hours=24)
    return sum(1 for e in sched["entries"] + sched.get("unscheduled", [])
               if e["key"] not in exclude
               and tiktok_of(e).get("sent_at") and tiktok_of(e).get("status") != "failed"
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
                    raise ClipperError(f"TikTok upload chunk {i + 1}/{count}: {r.status_code} {r.text[:200]}")
                time.sleep(5 * (attempt + 1))
    return data["publish_id"]


def post_status(paths: dict, publish_id: str) -> dict:
    return call(paths, "/post/publish/status/fetch/", {"publish_id": publish_id})


def notify_caption(cfg: dict, e: dict, text: str) -> None:
    from .alerts import Alerts

    channel = cfg["tiktok"]["caption_channel_id"]
    alerts = Alerts({**cfg, "record": {**cfg["record"], "alert_mention": "",
                                       **({"alert_channel_id": channel} if channel else {})}})
    if alerts.webhook or alerts.channel:
        import logging

        # Two messages, so a long-press Copy Text on the phone copies just the caption.
        alerts(f"TikTok draft sent ({e['key']}): open the TikTok inbox notification, paste the "
               "caption below, post.", logging.INFO)
        alerts(text, logging.INFO)
    else:
        print("  caption not posted to Discord: set [tiktok] caption_channel_id (or a [record] alert channel)")


def send(cfg: dict, items: list[str], stream: str | None = None, dry_run: bool = False,
         resend: bool = False) -> None:
    """Send `items` to the TikTok inbox. `resend`: items already sent go again (their draft
    expired unposted in the app); the old record is kept under "previous"."""
    tc = cfg["tiktok"]
    paths = cfg["paths"]
    sched = rumble.load_schedule(cfg)
    todo = [rumble.resolve(sched, r if "/" in r or not stream else f"{stream}/{r}") for r in items]
    again = {e["key"] for e in todo if resend and tiktok_of(e).get("status") == "sent"}
    room = tc["max_per_24h"] - sent_last_24h(sched, exclude=again)
    if not dry_run:
        print(f"TikTok account: {whoami(cfg)}; {max(room, 0)} of {tc['max_per_24h']} drafts left in 24 h")

    for e in todo:
        t = tiktok_of(e)
        if t.get("status") in ("sent", "posted") and e["key"] not in again:
            print(f"  {e['key']}: already {t['status']} on TikTok ({t.get('sent_at', '')})")
            continue
        if e["kind"] not in tc["kinds"]:
            print(f"  {e['key']}: a {e['kind']}, not sent ([tiktok] kinds = {tc['kinds']})")
            continue
        video = schedule.video(cfg, e)
        if not video.exists():
            print(f"  {e['key']}: video missing ({video}); rerun clipper shorts")
            continue
        text = caption(e, cfg)
        secs = media.duration(video)
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
        if e["key"] in again:
            old = {k: v for k, v in t.items() if k != "previous"}
            rec["previous"] = (t.get("previous") or []) + [{**old, "status": "expired"}]
        rumble.save_platform(cfg, {e["key"]: rec}, "tiktok")
        room -= 1
        print(f"  {e['key']}: {'sent again' if e['key'] in again else 'sent'} to TikTok drafts "
              f"({publish_id})\n    caption: {text}")
        try:
            notify_caption(cfg, e, text)
        except Exception as ex:  # noqa: BLE001  # the draft is sent; the caption is in schedule.json
            print(f"  warning: caption not posted to Discord: {ex}")


def auto(cfg: dict, dry_run: bool = False) -> None:
    """Send the next unsent scheduled Shorts, earliest YouTube slot first, up to the room
    max_per_24h leaves. A caption saying host(s) is skipped until it's retitled in Kitsu."""
    tc = cfg["tiktok"]
    status(cfg, check=not dry_run, quiet=True)  # mark drafts posted since the last run
    sched = rumble.load_schedule(cfg)
    room = max(tc["max_per_24h"] - sent_last_24h(sched), 0)
    queue = sorted((e for e in sched["entries"] if e["kind"] in tc["kinds"] and not tiktok_of(e)
                    and e.get("status") in ("scheduled", "uploaded") and schedule.video(cfg, e).exists()),
                   key=lambda e: e.get("publish_at") or "")
    print(f"TikTok: {len(queue)} Shorts not sent; {room} of {tc['max_per_24h']} drafts left in 24 h")
    picked = []
    for e in queue:
        if len(picked) >= room:
            break
        if re.search(r"\bhosts?\b", caption(e, cfg), re.IGNORECASE):
            print(f"  {e['key']}: skipped, the caption says host(s); retitle it in Kitsu")
            continue
        picked.append(e["key"])
    if picked:
        send(cfg, picked, dry_run=dry_run)


def repost_caption(cfg: dict, ref: str) -> None:
    """Post a sent draft's caption to Discord again."""
    e = rumble.resolve(rumble.load_schedule(cfg), ref)
    t = tiktok_of(e)
    if not t.get("caption"):
        raise ClipperError(f"{e['key']} hasn't been sent to TikTok")
    notify_caption(cfg, e, t["caption"])
    print(f"{e['key']}: caption posted\n    {t['caption']}")


def status(cfg: dict, stream: str | None = None, check: bool = True, quiet: bool = False) -> None:
    sched = rumble.load_schedule(cfg)
    rows = [e for e in sched["entries"] + sched.get("unscheduled", [])
            if (not stream or split_key(e["key"])[0] == stream) and tiktok_of(e)]
    updates = {}
    for e in rows:
        t = tiktok_of(e)
        if check and t.get("status") == "sent":
            try:
                s = post_status(cfg["paths"], t["publish_id"])
            except ClipperError as ex:
                print(f"  {e['key']}: status check failed: {ex}")
                s = {}
            new = {"PUBLISH_COMPLETE": "posted", "FAILED": "failed"}.get(s.get("status", ""))
            if new:
                t = {**t, "status": new, "tiktok_status": s["status"],
                     **({"fail_reason": s["fail_reason"]} if s.get("fail_reason") else {}),
                     "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}
                updates[e["key"]] = t
        if not quiet or e["key"] in updates:
            print(f"  {t.get('sent_at', '')[:16]}  {t['status']:<7} {e['key']:<34} {e['title'][:50]}"
                  + (f"  ({t['fail_reason']})" if t.get("fail_reason") else ""))
    if updates:
        rumble.save_platform(cfg, updates, "tiktok")
    if quiet:
        return
    print(f"{len(rows)} on TikTok; {sent_last_24h(sched)} sent in the last 24 h "
          f"(max {cfg['tiktok']['max_per_24h']})")
    if not stream:
        left = [e for e in sched["entries"] if e["kind"] in cfg["tiktok"]["kinds"] and not tiktok_of(e)]
        print(f"{len(left)} scheduled Shorts not sent yet; send with: clipper tiktok --items <key>,...")
