"""heinetwork.tv, On Cinema's own site, as a `clipper archive` source (members only).

The site is WordPress. Its sections (/on-cinema-at-the-cinema/, /oscar-specials/, /decker/,
/more/) link index pages (a season, a series) and item pages; a member's item page carries
the video as `<mux-video src="https://stream.mux.com/<id>.m3u8">` (plain HLS, no DRM) and a
`data-player-info` block: series, title, "Air Date: MM/DD/YYYY". Signed out, the same page
says "You need to be a HEINetwork Member" and has no stream. So listing needs a member's
cookies ([paths] hei_cookies: a Netscape cookies.txt or a browser extension's JSON export);
the stream itself needs none, and its media playlist gives the duration. About a quarter of
the streams (seasons 12-14, some specials and extras; 2026-11-11) are Mux signed playback: the
src carries `?token=<JWT>` that expires in about two days, so `fresh_m3u8` fetches the page
again for a new one before a download. Never print a token: `scrub` one out of any message.
"""
from __future__ import annotations

import base64
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .errors import ClipperError

SITE = "https://www.heinetwork.tv"
SECTIONS = ("/on-cinema-at-the-cinema/", "/oscar-specials/", "/decker/", "/more/")
LOCKED = "need to be a HEINetwork Member"
SIGNED_IN = "wordpress_logged_in_"  # WordPress's sign-in cookie name prefix
MUX = re.compile(r'https://stream\.mux\.com/[A-Za-z0-9]+\.m3u8[^"\'\s<]*')
HREF = re.compile(r'href="([^"#?]+)')
INFO = re.compile(r"data-player-info>(.*?)</div>\s*</div>", re.S)
DIV = re.compile(r"<div[^>]*>(.*?)</div>", re.S)
AIR = re.compile(r"Air Date:\s*(\d\d)/(\d\d)/(\d{4})")
SHORT = {"on-cinema-at-the-cinema": "oc", "oscar-specials": "oscar"}


def session(cookies: Path) -> requests.Session:
    """A requests session holding the member cookies in `cookies`."""
    from . import ytdl

    if not cookies.is_file():
        raise ClipperError(f"no HEI cookies at {cookies}: sign in to heinetwork.tv and export them there")
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (clipper archive)"
    for c in ytdl.live(ytdl.read_cookies(cookies)):
        s.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
    if not any(n.startswith(SIGNED_IN) for n in s.cookies.keys()):
        raise ClipperError(f"{cookies.name} holds no live heinetwork.tv sign-in: export the cookies again")
    return s


def item_id(path: str) -> str:
    """A folder-safe id from an item page's path: /on-cinema-at-the-cinema/season-16/hamnet/
    -> hei-oc-s16-hamnet."""
    parts = [SHORT.get(p, p) for p in path.strip("/").split("/")]
    parts = [re.sub(r"^season-(\d+)$", r"s\1", p) for p in parts]
    return "hei-" + "-".join(parts)


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_item(page: str, url: str) -> dict | None:
    """An item page's catalog entry (without its duration), or None when it has no stream."""
    m = MUX.search(page)
    if not m:
        return None
    info = INFO.search(page)
    rows = [text(d) for d in DIV.findall(info.group(1))] if info else []
    series = rows[0] if len(rows) > 1 else ""
    title = rows[1] if len(rows) > 1 else (rows[0] if rows else "")
    if not title:
        og = re.search(r'property="og:title" content="([^"]*)"', page)
        title = html.unescape(og.group(1)).removesuffix(" - HEI Network").strip() if og else ""
    air = AIR.search(info.group(1) if info else page)
    path = urlparse(url).path
    return {"id": item_id(path), "source": "hei", "url": url, "m3u8": html.unescape(m.group(0)),
            "title": title, "series": series, "channel": "HEI Network",
            "date": f"{air[3]}-{air[1]}-{air[2]}" if air else None,
            "kind": path.strip("/").split("/")[0]}


def children(page: str, path: str) -> list[str]:
    """The paths this page links below its own (an index's seasons, series and items)."""
    out = []
    for href in HREF.findall(page):
        p = urlparse(urljoin(SITE, href))
        if p.netloc.endswith("heinetwork.tv") and p.path.startswith(path) and p.path.rstrip("/") != path.rstrip("/"):
            child = p.path if p.path.endswith("/") else p.path + "/"
            if child not in out:
                out.append(child)
    return out


def duration_s(s: requests.Session, m3u8: str) -> float:
    """The stream's length: the first variant's media playlist, its segments summed."""
    master = s.get(m3u8, timeout=60)
    master.raise_for_status()
    variant = next((ln for ln in master.text.splitlines() if ln and not ln.startswith("#")), "")
    if not variant:
        return 0.0
    media = s.get(urljoin(m3u8, variant), timeout=60)
    media.raise_for_status()
    return sum(float(ln[8:].split(",")[0]) for ln in media.text.splitlines() if ln.startswith("#EXTINF:"))


def list_items(cookies: Path, pace_s: float = 1.0, sections: tuple[str, ...] = SECTIONS,
               get=None) -> list[dict]:
    """Every item under `sections`, newest first. `get(path) -> html` stands in for the site in
    tests; pace_s is the pause between page fetches."""
    s = session(cookies) if get is None else None

    def fetch(path: str) -> str:
        if get is not None:
            return get(path)
        time.sleep(pace_s)
        r = s.get(SITE + path, timeout=60)
        r.raise_for_status()
        return r.text

    items, seen, todo = {}, set(), list(sections)
    while todo:
        path = todo.pop(0)
        if path in seen:
            continue
        seen.add(path)
        page = fetch(path)
        if it := parse_item(page, SITE + path):
            items.setdefault(it["id"], it)
            continue
        if LOCKED in page:
            raise ClipperError(f"{path} is members only and the cookies aren't signed in: export them again")
        todo += [c for c in children(page, path) if c not in seen]
    for it in items.values():
        it["duration_s"] = round(duration_s(s, it["m3u8"]), 1) if s else it.get("duration_s", 0.0)
    return sorted(items.values(), key=lambda it: (it["date"] or "", it["id"]), reverse=True)


TOKEN = re.compile(r"token=([\w.-]+)")


def token_expiry(m3u8: str) -> float | None:
    """When a signed stream url's token expires (epoch seconds); None for a public one."""
    m = TOKEN.search(m3u8)
    if not m:
        return None
    try:
        payload = m[1].split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, ValueError, KeyError):
        return 0.0  # unreadable: treat as expired


def fresh_m3u8(cookies: Path, it: dict, margin_s: float = 3600, now: float | None = None) -> str:
    """The item's stream url, its page fetched again for a new token when the one in the
    catalog expires within margin_s (a download of a long special takes a while)."""
    exp = token_expiry(it["m3u8"])
    if exp is None or exp > (time.time() if now is None else now) + margin_s:
        return it["m3u8"]
    r = session(cookies).get(it["url"], timeout=60)
    r.raise_for_status()
    got = parse_item(r.text, it["url"])
    if not got:
        raise ClipperError(f"{it['id']}: no stream on its page (signed out? export the HEI cookies again)")
    return got["m3u8"]


def scrub(s: str) -> str:
    return TOKEN.sub("token=...", s)


def is_url(url: str) -> bool:
    return urlparse(url).netloc.endswith("heinetwork.tv")
