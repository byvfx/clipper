"""Instagram as an archive source: a reel by its link, a profile's videos listed signed in.

A reel or video post (`instagram.com/reel/<code>/`, `/p/<code>/`, with or without the user
name in front) is one item, `ig-<code>`; yt-dlp reads it, anonymously first and then signed
in when Instagram asks for a login (`ytdl.run_instagram`).

A profile (`instagram.com/<name>/`) can only be listed signed in: Instagram turns anonymous
clients away and yt-dlp's profile extractor is broken (2026-11). `list_profile` asks the two
endpoints instagram.com's own pages use, with the cookies in [paths] instagram_cookies
(a browser export, as for YouTube): the profile's id, then its posts a page at a time, paced
by [archive] instagram_pace_s and stopped at instagram_max_items, so a listing looks like
someone scrolling a profile and not like a scraper. Photos and carousels are skipped: only a
post that is one video is an item. It reads; it never likes, follows or posts.

The account is the user's own: every signed-in request is one Instagram can hold against
it, so nothing here runs on a schedule or in a loop of its own.
"""
from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import requests

from . import ytdl
from .errors import ClipperError

API = "https://www.instagram.com/api/v1"
APP_ID = "936619743392459"   # instagram.com's own web app id, sent by its pages with every request
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
PAGE = 33                    # posts a request, what the site's own profile page asks for
POST = re.compile(r"instagram\.com/(?:[\w.]+/)?(reels?|p|tv)/([\w-]+)")
NOT_A_PROFILE = {"reel", "reels", "p", "tv", "explore", "accounts", "stories", "direct", "about", "legal"}
VIDEO, CAROUSEL = 2, 8       # media_type; 1 is a photo


def shortcode(url: str) -> str | None:
    """A reel or post link's code, or None when the url isn't one."""
    m = POST.search(url or "")
    return m.group(2) if m else None


def username(url: str) -> str | None:
    """A profile link's user name, or None when the url isn't a profile."""
    p = urlsplit(url if "://" in url else f"https://{url}")
    if p.netloc.lower().removeprefix("www.") != "instagram.com":
        return None
    parts = [s for s in p.path.split("/") if s]
    if len(parts) != 1 or parts[0].lower() in NOT_A_PROFILE:
        return None
    return parts[0].lstrip("@").lower()


def post_url(code: str, reel: bool = True) -> str:
    return f"https://www.instagram.com/{'reel' if reel else 'p'}/{code}/"


def title_of(caption: str) -> str:
    """A post has no title: its caption's first line, cut short."""
    line = next((s.strip() for s in (caption or "").splitlines() if s.strip()), "")
    return line[:100]


def item(node: dict, channel: str = "") -> dict | None:
    """One post of a profile's feed as a catalog item; None when it isn't a single video."""
    if node.get("media_type") != VIDEO or not node.get("code"):
        return None
    caption = (node.get("caption") or {}).get("text") or ""
    user = node.get("user") or {}
    taken = node.get("taken_at")
    reel = node.get("product_type") == "clips"
    return {"id": f"ig-{node['code']}", "source": "instagram", "url": post_url(node["code"], reel),
            "title": title_of(caption), "duration_s": float(node.get("video_duration") or 0),
            "channel": channel or user.get("full_name") or user.get("username") or "",
            "date": datetime.fromtimestamp(int(taken), UTC).date().isoformat() if taken else "",
            "kind": "reel" if reel else "post"}


def session(cfg: dict) -> requests.Session:
    """A signed-in session from [paths] instagram_cookies; says how to make the file when it
    is missing or holds no sign-in."""
    f = ytdl.instagram_cookies_file(cfg)
    how = (f"export instagram.com's cookies from a signed-in browser to {cfg['paths']['instagram_cookies']} "
           "(wiki Archives, Instagram)")
    if not f.is_file():
        raise ClipperError(f"an Instagram profile is listed signed in, and there is no cookies file: {how}")
    cookies = ytdl.live([c for c in ytdl.read_cookies(f) if c.domain.lstrip(".").endswith("instagram.com")])
    if not any(c.name == "sessionid" for c in cookies):
        raise ClipperError(f"{f.name} holds no live Instagram sign-in: {how}")
    s = requests.Session()
    for c in cookies:
        s.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
    csrf = next((c.value for c in cookies if c.name == "csrftoken"), "")
    s.headers.update({"User-Agent": UA, "X-IG-App-ID": APP_ID, "X-Requested-With": "XMLHttpRequest",
                      "X-CSRFToken": csrf, "Accept": "*/*", "Referer": "https://www.instagram.com/"})
    return s


def ask(s: requests.Session, path: str, params: dict) -> dict:
    """One GET of Instagram's web API as JSON; a refusal as a ClipperError that says what to do."""
    try:
        r = s.get(f"{API}/{path}", params=params, timeout=30, allow_redirects=False)
    except requests.RequestException as e:
        raise ClipperError(f"Instagram: {type(e).__name__}") from e
    if r.status_code == 429:
        raise ClipperError("Instagram is rate-limiting this account or address: stop, and try again in a few hours")
    if r.status_code in (301, 302, 401, 403):  # sent to the login page, or the session refused
        raise ClipperError(f"Instagram refused the sign-in (http {r.status_code}): open instagram.com in the "
                           "browser, check the account isn't asked to confirm anything, export the cookies again")
    if r.status_code == 404:
        raise ClipperError("Instagram: no such profile")
    try:
        data = r.json()
    except ValueError as e:
        raise ClipperError(f"Instagram answered http {r.status_code} without JSON") from e
    if r.status_code != 200 or data.get("status") == "fail":
        raise ClipperError(f"Instagram: {data.get('message') or f'http {r.status_code}'}")
    return data


def list_profile(cfg: dict, url: str, limit: int | None = None, s: requests.Session | None = None,
                 sleep=time.sleep) -> tuple[list[dict], int]:
    """A profile's video posts, newest first, and how many other posts (photos, carousels) were
    passed over. limit: stop after about that many posts read; [archive] instagram_max_items
    stops it anyway."""
    name = username(url)
    if not name:
        raise ClipperError(f"{url}: not an Instagram profile or reel link")
    ac = cfg["archive"]
    limit = min(limit or ac["instagram_max_items"], ac["instagram_max_items"])
    s = s or session(cfg)
    user = (ask(s, "users/web_profile_info/", {"username": name}).get("data") or {}).get("user") or {}
    if not user.get("id"):
        raise ClipperError(f"Instagram: no profile {name!r}")
    if user.get("is_private") and not user.get("followed_by_viewer"):
        raise ClipperError(f"Instagram: {name} is private, and the signed-in account doesn't follow it")
    channel = user.get("full_name") or name
    items, other, read, max_id = [], 0, 0, None
    while read < limit:
        sleep(ac["instagram_pace_s"])
        page = ask(s, f"feed/user/{user['id']}/", {"count": PAGE, **({"max_id": max_id} if max_id else {})})
        nodes = page.get("items") or []
        for node in nodes:
            it = item(node, channel)
            if it:
                items.append(it)
            else:
                other += 1
        read += len(nodes)
        max_id = page.get("next_max_id")
        if not nodes or not page.get("more_available") or not max_id:
            break
    return items, other


def probe(cfg: dict, url: str) -> dict:
    """One reel or video post, by its link, as a catalog item: yt-dlp reads its page (no
    download), signed in only when Instagram asks."""
    code = shortcode(url)
    if not code:
        raise ClipperError(f"{url}: not an Instagram reel or post link")
    page = post_url(code, "/p/" not in url)
    r, _ = ytdl.run_instagram(cfg, "-J", "--no-playlist", page)
    if r.returncode:
        raise ClipperError(f"{page}: {ytdl.last_error(r)}")
    info = json.loads(r.stdout)
    if info.get("_type") == "playlist":  # a carousel: its videos are entries, not one item
        raise ClipperError(f"{page}: a post of several photos or videos, not one video")
    ts = info.get("timestamp")
    return {"id": f"ig-{code}", "source": "instagram", "url": page,
            "title": title_of(info.get("description") or "") or info.get("title") or "",
            "duration_s": float(info.get("duration") or 0),
            "channel": info.get("channel") or info.get("uploader") or "",
            "date": datetime.fromtimestamp(ts, UTC).date().isoformat() if ts else "", "kind": "reel"}


def list_url(cfg: dict, url: str) -> tuple[list[dict], int]:
    """`clipper archive add`'s Instagram urls: one reel, or a whole profile."""
    if shortcode(url):
        return [probe(cfg, url)], 0
    return list_profile(cfg, url)
