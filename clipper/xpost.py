"""X posts: scheduled Shorts -> the Lolcow Cinema X account as native video posts.

  clipper x --sign-in          one-time sign-in in your browser (OAuth 2.0 with PKCE); token
                               saved in paths.x_token and refreshed automatically
  clipper x --items s02,t20    post these now (a failed one is tried again)
  clipper x --auto             post approved, scheduled Shorts from streams that aired in
                               the last max_age_hours and aren't on X yet, newest stream
                               first, up to max_per_24h (the bot runs this every
                               [x] auto_post_minutes; 0, the default, means only by hand)
  clipper x --issue [DAY]      post a weekly issue's ten pages as a thread of 4 + 4 + 2
                               images (the newest issue, or the week holding DAY)
  clipper x [stream]           what was posted, with links

The video goes up through the v2 chunked media upload (initialize / append / finalize, then
STATUS until X has processed it) and the post is POST /2/tweets with its media id. The text
is the Short's title, hook first (no "KINO CASINO | " prefix, [x] hook_first), then up to
[x] max_hashtags hashtags and any [x] mentions, banned words masked as everywhere else. No link goes in the text: X bills a post with a URL at $0.20 against $0.015 (pay-per-use
pricing, 2026). Each post's cost is logged to paths.costs like a model call (`clipper costs`).

X's recommended portrait size is 720x1280 and its docs cap dimensions near that, so a Short
taller than [x] max_height is re-encoded to fit first (into streams/<id>/x/, deleted after).

Each post is recorded in schedule.json under "platforms": {"x": {...}} (as `clipper tiktok`
does), so nothing goes twice. A run first claims the entry there under the schedule's lock
(status "posting"), so the bot's --auto and a hand --items can't both post it or go past
max_per_24h. X doesn't wait for YouTube (a clip is worth most on X while the topic is
hot); with [x] after_youtube it does, and asks YouTube that each Short is really public
(an upload can stay private). --auto tries a failed post again up to MAX_TRIES times, and skips titles that say
"host(s)". A post call that times out is recorded "unknown" and never retried: X may have
created it, so check x.com by hand, then `clipper x --mark <key> posted[:url]|failed`.
Settings are the Short's show's ([shows.<key>.x]); the allowance is the account's.
A failed post makes the command exit non-zero, so the bot DMs it.

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
CLAIM_S = 1800       # a run's claim on an entry older than this was left by a run that died
MAX_TRIES = 3        # --auto tries a failed post again until it has failed this often
ISSUE_PAGES = 10     # a weekly issue's pages, all posted or none


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


def upload_video(paths: dict, video: Path, media_type: str = "video/mp4",
                 category: str = "tweet_video") -> str:
    size = video.stat().st_size
    mid = call(paths, "POST", "/media/upload/initialize", json={
        "media_type": media_type, "total_bytes": size, "media_category": category})["data"]["id"]
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

def post_text(title: str, tags: list[str], limit: int, max_tags: int, mentions: list[str] = ()) -> str:
    """Title, then up to max_tags hashtags and the mentions, inside X's limit: tags go
    first, then the title is shortened to leave room for the mentions (dropped only if they
    can't fit beside a readable title). Links are taken out: X bills a post with a URL at
    $0.20."""
    title = re.sub(r"\s+", " ", re.sub(r"(https?://|www\.)\S+", "", title)).strip()
    tags = list(dict.fromkeys(tags))[:max_tags]
    ats = [m if m.startswith("@") else f"@{m}" for m in mentions]

    def join(head: str) -> str:
        return f"{head}\n\n{' '.join(tags + ats)}".strip()

    text = join(title)
    while len(text) > limit and tags:
        tags.pop()
        text = join(title)
    if len(text) <= limit:
        return text
    room = limit - (len(" ".join(ats)) + 2 if ats else 0)
    if room < 20:  # the mentions alone fill the post
        ats, room = [], limit
    return join(title[:room - 1].rstrip() + "…")


def text_for(e: dict, cfg: dict) -> str:
    """The post's text. With hook_first the show prefix ("KINO CASINO | ") comes off the
    title, so the feed's first words are the hook; the hashtag or a mention names the show."""
    from .review import bare_title

    scfg = for_stream(cfg, cfg["paths"]["streams"] / e["stream"])
    xc = scfg["x"]
    title, tags = rumble.title_and_tags(e, cfg, "x")
    if xc["hook_first"]:
        title = bare_title(scfg, title)
    return post_text(title, tags, xc["max_chars"], xc["max_hashtags"], xc["mentions"])


# ---------- posting ----------

def x_of(e: dict) -> dict:
    return (e.get("platforms") or {}).get("x") or {}


def xc_of(cfg: dict):
    """Entry -> its show's [x] settings (kinds, max_height, after_youtube, ...); the 24 h
    allowance is the account's, from the base config."""
    cache: dict = {}

    def get(e: dict) -> dict:
        if e["stream"] not in cache:
            cache[e["stream"]] = for_stream(cfg, cfg["paths"]["streams"] / e["stream"])["x"]
        return cache[e["stream"]]
    return get


def active(x: dict, now: datetime) -> datetime | None:
    """When a record took a place in the 24 h allowance: a post, or a claim by a run that is
    still uploading (claims older than CLAIM_S were left by a run that died)."""
    if x.get("status") == "posted":
        return datetime.fromisoformat(x["posted_at"])
    if x.get("status") == "posting":
        at = datetime.fromisoformat(x["claimed_at"])
        return at if (now - at).total_seconds() < CLAIM_S else None
    if x.get("status") == "unknown":  # a timed-out post: X may have it, so it keeps its place
        return datetime.fromisoformat(x["failed_at"])
    return None


def posted_last_24h(sched: dict, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=24)
    return sum(1 for e in sched["entries"] + sched.get("unscheduled", [])
               if (at := active(x_of(e), now)) and at > since)


def aired_of(cfg: dict):
    """Entry -> when its stream started (meta.json), or None."""
    cache: dict = {}

    def get(e: dict) -> datetime | None:
        if e["stream"] not in cache:
            meta = jsonstate.read(cfg["paths"]["streams"] / e["stream"] / "meta.json", {})
            cache[e["stream"]] = datetime.fromisoformat(meta["started_at"]) if meta.get("started_at") else None
        return cache[e["stream"]]
    return get


def approved_of(cfg: dict):
    """Entry -> approved in Kitsu (review.json) and not on Hold right now. schedule.json
    keeps an uploaded entry even after it's rejected or held, so its status alone isn't
    approval."""
    from .schedule import truthy

    cache: dict = {}

    def get(e: dict) -> bool:
        if e["stream"] not in cache:
            cache[e["stream"]] = jsonstate.read(cfg["paths"]["streams"] / e["stream"] / "review.json", {})
        r = cache[e["stream"]].get(split_key(e["key"])[1]) or {}
        return r.get("decision") == "approved" and not truthy((r.get("fields") or {}).get("Hold"))
    return get


def due(sched: dict, xc_for, now: datetime | None = None, aired=lambda e: None,
        approved=lambda e: True) -> list[dict]:
    """Entries --auto would post: the right kind and not on X (or failed fewer than
    MAX_TRIES times) and still `approved`. By default, as soon as they are scheduled, from
    a stream that aired no more than max_age_hours ago, newest stream first: on X a clip is
    worth most while the topic is hot. With after_youtube, only once uploaded with the slot passed
    and that slot within max_age_hours, earliest first (`auto` then asks YouTube that each
    is really public)."""
    now = now or datetime.now(UTC)

    def ready(e: dict) -> bool:
        xc, x = xc_for(e), x_of(e)
        if e["kind"] not in xc["kinds"]:
            return False
        if x and not (x.get("status") == "failed" and x.get("tries", 1) < MAX_TRIES):
            return False
        if not approved(e):
            return False
        at = datetime.fromisoformat(e["publish_at"]) if e.get("publish_at") else None
        oldest = now - timedelta(hours=xc["max_age_hours"]) if xc["max_age_hours"] else None
        if xc["after_youtube"]:
            return (e.get("status") == "uploaded" and (not at or at <= now)
                    and not (oldest and at and at < oldest))
        when = aired(e) or at
        return e.get("status") in ("scheduled", "uploaded") and not (oldest and when and when < oldest)

    def order(e: dict) -> tuple:
        started = None if xc_for(e)["after_youtube"] else aired(e)
        return (-started.timestamp() if started else 0, e.get("publish_at") or "")

    return sorted((e for e in sched["entries"] if ready(e)), key=order)


def mark(cfg: dict, key: str, status: str) -> None:
    """After a hand check of an "unknown" record (a post call that timed out): `posted` (X
    has it; `posted:<url>` keeps the link) or `failed` (it isn't there; --auto may try again)."""
    status, _, url = status.partition(":")
    if status not in ("posted", "failed"):
        raise ClipperError("clipper x --mark KEY posted[:URL] or failed")
    sched = jsonstate.load(cfg["paths"]["schedule"])
    if not any(e["key"] == key for e in sched["entries"] + sched.get("unscheduled", [])):
        raise ClipperError(f"{key}: not in schedule.json (a full key, e.g. kick-1/v01_t20)")
    now = costs.now_iso()
    rec = ({"status": "posted", "posted_at": now, "url": url, "by_hand": True} if status == "posted"
           else {"status": "failed", "tries": 0, "error": "not on X (checked by hand)", "failed_at": now})
    rumble.save_platform(cfg, {key: rec}, "x")
    print(f"{key}: X record set to {status}")


def claim(cfg: dict, key: str) -> str:
    """Take `key` for this run under the schedule's lock, so an overlapping run (the bot's
    --auto and a hand --items) can't post it twice or past max_per_24h. "" when claimed,
    else why not."""
    now = datetime.now(UTC)
    with jsonstate.update(cfg["paths"]["schedule"]) as sched:
        e = next((e for e in sched["entries"] + sched.get("unscheduled", []) if e["key"] == key), None)
        if e is None:
            return "gone from schedule.json"
        x = x_of(e)
        if x.get("status") == "posted":
            return f"already on X ({x['url']})"
        if x.get("status") == "unknown":
            return "a post call timed out: check x.com by hand, then clipper x --mark <key> posted|failed"
        if active(x, now):
            return "being posted by another run"
        if used_last_24h(cfg, sched, now) >= cfg["x"]["max_per_24h"]:
            return f"not posted, {cfg['x']['max_per_24h']} posts in the last 24 h ([x] max_per_24h)"
        e.setdefault("platforms", {})["x"] = {**x, "status": "posting", "claimed_at": now.isoformat(timespec="seconds")}
    return ""


def post(cfg: dict, items: list[str], stream: str | None = None, dry_run: bool = False) -> None:
    paths, xc_for, limit = cfg["paths"], xc_of(cfg), cfg["x"]["max_per_24h"]
    sched = rumble.load_schedule(cfg)
    todo = list({e["key"]: e for e in (rumble.resolve(sched, r if "/" in r or not stream else f"{stream}/{r}")
                                       for r in items)}.values())
    if not dry_run:
        user = whoami(cfg)
        print(f"X account: @{user}; {max(limit - used_last_24h(cfg, sched), 0)} of {limit} posts left in 24 h")

    failed = []
    for e in todo:
        xc, x = xc_for(e), x_of(e)
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
        why = claim(cfg, e["key"])
        if why:
            print(f"  {e['key']}: {why}")
            continue
        sendable = video
        try:
            sendable = fit(video, paths["streams"] / e["stream"], xc["max_height"])
            mid = upload_video(paths, sendable)
            try:
                pid = call(paths, "POST", "/tweets", json={"text": text, "media": {"media_ids": [mid]}})["data"]["id"]
            except requests.Timeout as ex:
                # X may well have created the post before the answer was lost: never retried
                # (that posted and billed a Short twice); `clipper x` lists it for a hand check.
                rumble.save_platform(cfg, {e["key"]: {"status": "unknown", "error": f"timed out: {ex}"[:300],
                                                      "text": text, "media_id": mid, "failed_at": costs.now_iso()}}, "x")
                print(f"  {e['key']}: UNKNOWN: the post call timed out; check x.com by hand (not retried)")
                failed.append(f"{e['key']}: timed out, check x.com")
                continue
        except (ClipperError, requests.RequestException, subprocess.CalledProcessError) as ex:
            rumble.save_platform(cfg, {e["key"]: {"status": "failed", "error": str(ex)[:300], "text": text,
                                                  "tries": x.get("tries", 0) + 1, "failed_at": costs.now_iso()}}, "x")
            print(f"  {e['key']}: FAILED: {ex}")
            failed.append(f"{e['key']}: {ex}")
            continue
        url = f"https://x.com/{user}/status/{pid}"
        rumble.save_platform(cfg, {e["key"]: {"status": "posted", "post_id": pid, "media_id": mid,
                                              "url": url, "text": text, "posted_at": costs.now_iso()}}, "x")
        costs.record(paths.get("costs"), {"ts": costs.now_iso(), "kind": "x", "provider": "x",
                                          "model": "post", "stage": "x", "stream": e["stream"],
                                          "usd": xc["post_usd"]})
        print(f"  {e['key']}: posted {url}\n    text: {text}")
        if sendable != video:
            try:
                sendable.unlink(missing_ok=True)
            except OSError as ex:
                print(f"  warning: couldn't delete {sendable}: {ex}")
    if failed:
        raise ClipperError(f"{len(failed)} X post(s) failed: {'; '.join(failed)[:400]}")


# ---------- weekly issue ----------

def issue_folder(cfg: dict, day: str | None) -> Path:
    """weekly/<Saturday>/ of the week holding `day`, or the newest issue drafted."""
    root = Path(cfg["paths"]["weekly"])
    if day:
        d = datetime.strptime(day, "%Y-%m-%d")
        here = root / (d + timedelta(days=(5 - d.weekday()) % 7)).strftime("%Y-%m-%d")
    else:
        drafted = sorted(p.parent for p in root.glob("*/issue.json"))
        if not drafted:
            raise ClipperError(f"no weekly issue in {root}; run: clipper weekly")
        here = drafted[-1]
    if not (here / "issue.json").exists():
        raise ClipperError(f"no issue.json in {here}; run: clipper weekly --week {day}")
    return here


def caption_body(text: str) -> str:
    """A post caption without its hashtag lines and a leading "Pages 6-10:"."""
    lines = [s for s in text.splitlines() if not re.fullmatch(r"(\s*#\w+)+\s*", s)]
    return re.sub(r"^Pages \d+-\d+:\s*", "", " ".join(lines).strip())


def issue_thread(issue: dict, pages: list[Path], limit: int) -> list[tuple[str, list[Path]]]:
    """The issue as a thread: X takes four images a post, so ten pages go 4 + 4 + 2. The text is
    issue.json's post.x (a list, one per post: the place for a hand edit), else the two post
    captions without their hashtags, one hashtag naming the cover subject, and the readers' poll
    on the last post."""
    groups = [pages[i:i + 4] for i in range(0, len(pages), 4)]
    post = issue.get("post") or {}
    texts = post.get("x")
    if not texts:
        subject = re.sub(r"\W", "", (issue.get("cover") or {}).get("person", ""))
        back = issue.get("back") or {}
        names = [o.get("name") or o.get("person") for o in back.get("options") or []]
        poll = ""
        if back.get("poll") and names:
            q = back["poll"].strip()
            poll = (f"{q[0]}{q[1:].lower()} {', '.join(names[:-1])} or {names[-1]}. Reply with your pick."
                    if len(names) > 1 else f"{q[0]}{q[1:].lower()}")
        texts = [post_text(caption_body(post.get("part1", "")), [f"#{subject}"] if subject else [], limit, 1),
                 post_text(caption_body(post.get("part2", "")), [], limit, 0),
                 poll]
    return [(texts[i] if i < len(texts) else "", g) for i, g in enumerate(groups)]


def x_len(text: str) -> int:
    """Length as X counts it: most Latin and punctuation code points weigh 1, the rest
    (emoji, CJK) 2."""
    light = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
    return sum(1 if any(a <= ord(c) <= b for a, b in light) else 2 for c in text)


def issue_pages(here: Path) -> list[Path]:
    """Pages 01-10 of an issue, each exactly once: a half-finished render would otherwise go
    out as a complete thread and the missing page could never follow."""
    pages = sorted(here.glob("[0-9][0-9]_*.jpg"))
    nums = [p.name[:2] for p in pages]
    want = [f"{n:02d}" for n in range(1, ISSUE_PAGES + 1)]
    if nums != want:
        missing = sorted(set(want) - set(nums))
        extra = sorted(n for n in set(nums) if nums.count(n) > 1 or n not in want)
        raise ClipperError(f"{here} needs pages 01-{ISSUE_PAGES:02d} once each (missing {missing or '-'}, "
                           f"extra {extra or '-'}); run: clipper weekly --render")
    return pages


def issue_posts_last_24h(cfg: dict, now: datetime | None = None) -> int:
    """Weekly-issue posts in the 24 h allowance: posted ones, and the places a run still
    posting has reserved (claims older than CLAIM_S were left by a run that died)."""
    now = now or datetime.now(UTC)
    since, n = now - timedelta(hours=24), 0
    if not cfg["paths"].get("weekly"):
        return 0
    for f in Path(cfg["paths"]["weekly"]).glob("*/x.json"):
        rec = jsonstate.read(f, {})
        n += sum(1 for p in rec.get("posts") or [] if datetime.fromisoformat(p["posted_at"]) > since)
        c = rec.get("claim") or {}
        if c and (now - datetime.fromisoformat(c["claimed_at"])).total_seconds() < CLAIM_S:
            n += int(c.get("left", 0))
    return n


def used_last_24h(cfg: dict, sched: dict, now: datetime | None = None) -> int:
    """The account's posts in the last 24 h: Shorts (schedule.json) and weekly issues."""
    return posted_last_24h(sched, now) + issue_posts_last_24h(cfg, now)


def claim_issue(cfg: dict, here: Path, posts: int) -> str:
    """Take the issue for this run, reserving `posts` places in the allowance, under the
    schedule's lock (as Short claims are) and then the issue's x.json lock, so two runs can't
    post the same thread twice or go past max_per_24h. "" when claimed, else why not."""
    now = datetime.now(UTC)
    limit = cfg["x"]["max_per_24h"]
    with jsonstate.update(cfg["paths"]["schedule"]) as sched:
        room = limit - used_last_24h(cfg, sched, now)
        with jsonstate.update(here / "x.json", {}) as rec:
            c = rec.get("claim") or {}
            if c and (now - datetime.fromisoformat(c["claimed_at"])).total_seconds() < CLAIM_S:
                return "being posted by another run"
            if posts > room:
                return (f"not posted: {posts} posts needed, {max(room, 0)} of {limit} left in 24 h "
                        f"([x] max_per_24h)")
            rec["claim"] = {"claimed_at": now.isoformat(timespec="seconds"), "left": posts}
    return ""


def post_issue(cfg: dict, day: str | None = None, dry_run: bool = False) -> None:
    """Post a weekly issue's 3:4 pages as a thread. weekly/<Saturday>/x.json keeps each post as
    it goes, so a run that failed halfway carries on as replies to the last one posted. Every
    caption is checked against [x] max_chars before anything goes up."""
    from .bleep import Bleeper

    paths, xc = cfg["paths"], cfg["x"]
    here = issue_folder(cfg, day)
    issue = jsonstate.load(here / "issue.json")
    pages = issue_pages(here)
    mask = Bleeper(cfg["bleep"]).mask
    thread = [(mask(t), g) for t, g in issue_thread(issue, pages, xc["max_chars"])]
    done = jsonstate.read(here / "x.json", {}).get("posts") or []
    print(f"issue {int(issue.get('issue') or 0):02d} ({here.name}): {len(thread)} posts, {len(pages)} pages")
    if len(done) >= len(thread):
        print(f"  already on X: {done[0]['url']}")
        return
    long = [f"post {i + 1} ({x_len(t)})" for i, (t, _) in enumerate(thread) if x_len(t) > xc["max_chars"]]
    if long:
        raise ClipperError(f"over [x] max_chars {xc['max_chars']}: {', '.join(long)}; "
                           f"shorten it in issue.json post.x")
    for i, (text, group) in enumerate(thread):
        if re.search(r"\bhosts?\b", text, re.IGNORECASE):
            print(f"  warning: post {i + 1} says host(s); set issue.json post.x by hand")
        print(f"  {i + 1}/{len(thread)}{' (posted)' if i < len(done) else ''}: "
              f"{', '.join(p.name for p in group)}\n    text: {text!r}")
    if dry_run:
        return
    user = whoami(cfg)
    why = claim_issue(cfg, here, len(thread) - len(done))
    if why:
        raise ClipperError(f"issue {here.name}: {why}")
    try:
        for i, (text, group) in enumerate(thread[len(done):], len(done)):
            body = {"text": text, "media": {"media_ids": [upload_video(paths, p, "image/jpeg", "tweet_image")
                                                          for p in group]}}
            if done:
                body["reply"] = {"in_reply_to_tweet_id": done[-1]["id"]}
            try:
                pid = call(paths, "POST", "/tweets", json=body)["data"]["id"]
            except (ClipperError, requests.RequestException) as ex:
                raise ClipperError(f"X post {i + 1} of issue {here.name} failed: {ex}; run it again to carry on")
            done.append({"id": pid, "url": f"https://x.com/{user}/status/{pid}", "text": text,
                         "posted_at": costs.now_iso()})
            with jsonstate.update(here / "x.json", {}) as rec:
                rec["posts"] = done
                rec["claim"]["left"] = max(rec["claim"].get("left", 1) - 1, 0)
            costs.record(paths.get("costs"), {"ts": costs.now_iso(), "kind": "x", "provider": "x",
                                              "model": "post", "stage": "x", "stream": f"weekly/{here.name}",
                                              "usd": xc["post_usd"]})
            print(f"  {i + 1}/{len(thread)}: posted {done[-1]['url']}")
    finally:
        with jsonstate.update(here / "x.json", {}) as rec:
            rec.pop("claim", None)


def public_on_youtube(cfg: dict, entries: list[dict]) -> list[dict]:
    """The entries YouTube says are public (an upload may stay private: manual_publish, or
    made private in Studio)."""
    from . import youtube

    if not entries:
        return []
    status = youtube.privacy(youtube.service(cfg["paths"]), [e.get("video_id") for e in entries])
    return [e for e in entries if status.get(e.get("video_id"), {}).get("privacyStatus") == "public"]


def auto(cfg: dict, dry_run: bool = False) -> None:
    limit, xc_for = cfg["x"]["max_per_24h"], xc_of(cfg)
    sched = rumble.load_schedule(cfg)
    room = max(limit - used_last_24h(cfg, sched), 0)
    queue = [e for e in due(sched, xc_for, aired=aired_of(cfg), approved=approved_of(cfg))
             if Path(e["file"]).exists()]
    checked = [e for e in queue if xc_for(e)["after_youtube"]]
    if room and checked:
        public = {e["key"] for e in public_on_youtube(cfg, checked)}
        for e in checked:
            if e["key"] not in public:
                print(f"  {e['key']}: skipped, not public on YouTube")
        queue = [e for e in queue if e["key"] in public or not xc_for(e)["after_youtube"]]
    print(f"X: {len(queue)} Shorts due and not posted; {room} of {limit} posts left in 24 h")
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
    print(f"{len(rows)} on X; {used_last_24h(cfg, sched)} posted in the last 24 h (max {cfg['x']['max_per_24h']})")
    if not stream:
        n = len(due(sched, xc_of(cfg), aired=aired_of(cfg), approved=approved_of(cfg)))
        print(f"{n} Shorts due and not posted; post with: clipper x --items <key>,... "
              "or clipper x --auto")
