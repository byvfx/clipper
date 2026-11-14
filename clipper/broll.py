"""`clipper broll` (#229): a library of free b-roll for story cuts, explainers and the weekly,
from archives whose footage is free to use: work of the US government has no copyright
(17 U.S.C. 105), and the Internet Archive and Wikimedia Commons say each item's licence.

    clipper broll find "<query>" [--source nasa|dvids|prelinger|commons] [-n N]
    clipper broll add <url|id|file> [--from TIME] [--to TIME] [--tag X] [--credit TEXT]
    clipper broll list [--tag X] [--source X] [--status new|kept|long] [--json]
    clipper broll keep|drop <id> ...

Sources: nasa (the NASA Image and Video Library), dvids (the Defense Visual Information
Distribution Service: the armed forces' own video, its public key from $DVIDS_API_KEY or the
first line of [paths] dvids_key), prelinger (the Internet Archive's Prelinger films: ads,
industrial and educational films), commons (Wikimedia Commons video). A clip is
kept only when its licence is one of [broll] licences (public domain and CC0 at first) and
nothing in its description names an outside copyright holder; anything else is skipped with
the reason. Clips up to [broll] max_s are downloaded whole, re-encoded to at most [broll]
max_height, without sound unless [broll] audio (NASA videos often carry licensed music);
a longer one is listed as "long" with no file, and `add <id> --from --to` takes a part of it.

Everything is in [paths] broll: <id>.mp4 and broll.json, each clip's source page, media
address, title, date, credit, licence, length, tags (a find's query) and status: new until
kept or dropped; a dropped clip's file is deleted and its source remembered, so the next find
doesn't bring it back. The credit line belongs in the description of any video that uses the
clip. Nothing here may suggest an agency endorses us: no insignia or recognizable service
member on a cover or as the subject of a title.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from . import config, jsonstate
from .errors import ClipperError

INDEX = "broll.json"
SOURCES = ("nasa", "dvids", "prelinger", "commons")
STATUSES = ("new", "kept", "long")
LICENCES = ("pd", "cc0", "cc-by", "cc-by-sa")
UA = "clipper-broll/1.0 (b-roll library; https://github.com/byvfx/clipper)"  # Wikimedia asks for one
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".ogv"}

# Words in a description or credit that say someone outside the archive owns the picture.
OWNED = re.compile(r"©|\(c\) ?\d{4}|\bcopyright(ed)?\b|all rights reserved|used (with|by) permission"
                   r"|\blicensed (from|by|to)\b|\bgetty\b|shutterstock|\bistock|\bpond5\b|storyful"
                   r"|associated press|\bap (photo|video|images?|footage)\b|\breuters\b|\bafp\b", re.I)
NOT_OWNED = re.compile(r"(not subject to|no known|free of|without) (any )?copyright|\bno copyright"
                       r"|(?<!not )(?<!n't )\bcopyright[- ]free\b"  # the whole word; "not copyright-free" stays owned
                       r"|copyright\W{0,3}(status\W{0,3})?(public domain|none|expired|not renewed)", re.I)
MUSIC = re.compile(r"\bmusic\b", re.I)


def root(cfg: dict) -> Path:
    d = cfg["paths"]["broll"]
    if not d:
        raise ClipperError("[paths] broll is unset: the b-roll library lives there")
    return Path(d)


def load(cfg: dict) -> dict:
    data = jsonstate.read(root(cfg) / INDEX, None) or {}
    return {"clips": {}, "dropped": [], **data}


def _index(cfg: dict) -> Path:
    d = root(cfg)
    d.mkdir(parents=True, exist_ok=True)
    return d / INDEX


def clip_id(source: str, key: str, start: float | None = None, end: float | None = None) -> str:
    """Stable for a source item and range: the same find or add never makes a second copy."""
    rng = "" if start is None and end is None else f"|{start or 0:g}-{end or 0:g}"
    return f"{source}-{hashlib.sha1((key + rng).encode()).hexdigest()[:8]}"


def licence_code(text: str) -> str:
    """A licence as the source states it -> pd | cc0 | cc-by | cc-by-sa | pixabay | permissive | other
    (permissive: MIT, ISC, Apache, BSD, OFL; the clip art library's icon sets). Noncommercial,
    no-derivatives and GPL licences are always other."""
    t = re.sub(r"\s+", " ", (text or "").lower().replace("_", " ")).strip()  # "CC BY  NC", tabs
    if not t:
        return "other"
    if re.search(r"\b(by|cc)[- ]n[cd]\b|-n[cd]\b|non[- ]?commercial|no[- ]?deriv|\bl?gpl\b", t):
        return "other"  # noncommercial, no-derivatives or copyleft, whatever else it says
    if "publicdomain/zero" in t or re.search(r"\bcc0\b|cc zero", t):
        return "cc0"
    if "publicdomain" in t or "public domain" in t or re.match(r"pd\b|pd-", t) or "public-domain" in t:
        return "pd"
    if re.search(r"by-sa|by sa|attribution-sharealike", t):
        return "cc-by-sa"
    if re.search(r"licenses/by/|cc by\b|cc-by\b|attribution", t):
        return "cc-by"
    if "pixabay" in t:
        return "pixabay"  # the Pixabay Content License: free, commercial, no credit
    if re.search(r"\b(mit|isc|apache|unlicense|0bsd|bsd|ofl)\b", t):
        return "permissive"  # the notice stays beside copies of the files; no credit on use
    return "other"


def owned(text: str) -> str:
    """The words that say someone else owns it, or ""."""
    t = NOT_OWNED.sub("", text or "")
    m = OWNED.search(t)
    return m.group(0) if m else ""


def refused(c: dict, cfg: dict) -> str:
    """Why a candidate can't be kept, or "" when it can."""
    bc = cfg["broll"]
    code = licence_code(c.get("licence", ""))
    if code not in bc["licences"]:
        return f"licence {c.get('licence') or 'not stated'} ({code}; [broll] licences: {', '.join(bc['licences'])})"
    text = " ".join(str(c.get(k, "")) for k in ("title", "description", "credit"))
    if who := owned(text):
        return f"its description says {who!r}"
    if bc["audio"] and c["source"] == "nasa" and MUSIC.search(text):
        return "names music in its credits ([broll] audio is on)"
    return ""


def seconds(text: str | float | None) -> float | None:
    """'0:01:55', '1:55', '115.2', '115.2 s' -> seconds; None when it isn't one."""
    if text is None or text == "":
        return None
    if isinstance(text, int | float):
        return float(text)
    s = str(text).strip().removesuffix("s").strip()
    try:
        if ":" in s:
            out = 0.0
            for part in s.split(":"):
                out = out * 60 + float(part)
            return out
        return float(s)
    except ValueError:
        return None


def dvids_key(cfg: dict | None = None) -> str:
    """$DVIDS_API_KEY, else the first line of [paths] dvids_key (the public key; the second line,
    the private key, isn't needed to read); "" if neither."""
    return config.key(cfg, "DVIDS_API_KEY", "dvids_key")


def _get(url: str, params: dict | None = None, timeout: int = 20, ua: str = UA):
    import requests

    host = urlparse(url).hostname or url
    try:
        r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": ua})
    except requests.RequestException as e:
        raise ClipperError(f"{host}: {type(e).__name__}") from e
    if not r.ok:
        raise ClipperError(f"{host}: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ClipperError(f"{host}: not JSON") from e


def _text(text: str) -> str:
    """Tags stripped, entities decoded (&copy; is checked as ©), spaces squeezed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


# --- sources: each returns candidates {key, source, title, page, url, date, credit, licence,
# --- description, duration}; nothing is downloaded


def nasa(query: str, n: int, cfg: dict | None = None, nasa_id: str = "") -> list[dict]:
    """The NASA Image and Video Library (images-api.nasa.gov, no key). NASA's own work is public
    domain; an item whose description names another owner is refused by `refused`."""
    params = {"nasa_id": nasa_id} if nasa_id else {"q": query, "media_type": "video", "page_size": max(n * 3, 10)}
    js = _get("https://images-api.nasa.gov/search", params)
    out = []
    for item in (js.get("collection") or {}).get("items", []):
        d = (item.get("data") or [{}])[0]
        if d.get("media_type") != "video" or not d.get("nasa_id") or not item.get("href"):
            continue
        out.append({"key": f"https://images.nasa.gov/details/{d['nasa_id']}", "source": "nasa",
                    "title": d.get("title", ""), "page": f"https://images.nasa.gov/details/{d['nasa_id']}",
                    "manifest": item["href"], "url": "", "date": (d.get("date_created") or "")[:10],
                    "credit": "NASA" + (f" {d['center']}" if d.get("center") else "")
                              + (f" ({d['secondary_creator']})" if d.get("secondary_creator") else ""),
                    "licence": "public domain (NASA)", "description": _text(d.get("description", "")),
                    "duration": None})
    return out


def _nasa_media(c: dict) -> None:
    """The item's biggest mp4 and its length, from its asset manifest and metadata.json."""
    files = _get(c["manifest"].replace("http://", "https://"))
    mp4 = [f.replace("http://", "https://") for f in files if f.lower().endswith(".mp4")]
    pick = next((f for f in mp4 if "~orig." in f), None) or next((f for f in mp4 if "~large." in f), None)
    if not pick and mp4:
        pick = mp4[0]
    if not pick:
        raise ClipperError(f"{c['page']}: no mp4")
    c["url"] = pick
    meta = next((f for f in files if f.endswith("metadata.json")), None)
    if meta:
        try:
            m = _get(meta.replace("http://", "https://"))
            c["duration"] = seconds(m.get("QuickTime:MediaDuration") or m.get("QuickTime:Duration"))
        except ClipperError:
            pass


def prelinger(query: str, n: int, cfg: dict | None = None, identifier: str = "") -> list[dict]:
    """The Internet Archive's Prelinger collection (advancedsearch, no key). Most items say
    public domain in licenseurl; one that says nothing is refused."""
    if identifier:
        ids = [identifier]
    else:
        q = f"collection:prelinger AND mediatype:movies AND ({query})"
        js = _get("https://archive.org/advancedsearch.php",
                  {"q": q, "fl[]": ["identifier"], "rows": max(n * 3, 10), "output": "json"})
        ids = [d["identifier"] for d in (js.get("response") or {}).get("docs", []) if d.get("identifier")]
    out = []
    for ident in ids:
        try:
            m = _get(f"https://archive.org/metadata/{quote(ident)}")
        except ClipperError:
            continue
        md = m.get("metadata") or {}
        vids = [f for f in m.get("files", []) if str(f.get("name", "")).lower().endswith(".mp4")]
        if not vids:
            continue
        f = max(vids, key=lambda f: int(f.get("height") or 0))
        desc = md.get("description", "")
        desc = " ".join(map(str, desc)) if isinstance(desc, list) else str(desc)
        out.append({"key": f"https://archive.org/details/{ident}", "source": "prelinger",
                    "title": _one(md.get("title")), "page": f"https://archive.org/details/{ident}",
                    "url": f"https://archive.org/download/{quote(ident)}/{quote(f['name'])}",
                    "date": _one(md.get("date") or md.get("year"))[:10],
                    "credit": "Prelinger Archives, Internet Archive" + (f" ({_one(md.get('creator'))})"
                                                                       if md.get("creator") else ""),
                    "licence": _one(md.get("licenseurl")), "description": _text(desc),
                    "duration": seconds(f.get("length"))})
    return out


def _one(v) -> str:
    return str(v[0] if isinstance(v, list) and v else v or "")


def commons(query: str, n: int, cfg: dict | None = None, title: str = "") -> list[dict]:
    """Wikimedia Commons video files, licence and author from each file's metadata."""
    params = {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size"}
    if title:
        params["titles"] = title
    else:
        params.update(generator="search", gsrnamespace=6, gsrlimit=min(max(n * 3, 10), 50), gsrsearch=f"{query} filetype:video")
    js = _get("https://commons.wikimedia.org/w/api.php", params)
    if err := js.get("error"):
        raise ClipperError(f"Commons: {err.get('code', '')} {err.get('info', '')}".strip())
    out = []
    for pg in sorted((js.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (pg.get("imageinfo") or [None])[0]
        if not ii or not str(ii.get("mime", "")).startswith("video/"):
            continue
        meta = {k: _text(str((v or {}).get("value", ""))) for k, v in (ii.get("extmetadata") or {}).items()}
        page = ii.get("descriptionurl") or ii["url"]
        out.append({"key": page, "source": "commons", "title": meta.get("ObjectName") or pg.get("title", ""),
                    "page": page, "url": ii["url"].split("?")[0],
                    "date": (meta.get("DateTimeOriginal") or meta.get("DateTime") or "")[:10],
                    "credit": meta.get("Artist") or "Wikimedia Commons",
                    "licence": meta.get("LicenseShortName") or meta.get("License") or "",
                    "description": meta.get("ImageDescription", ""), "duration": ii.get("duration")})
    return out


def dvids(query: str, n: int, cfg: dict | None = None, asset: str = "") -> list[dict]:
    """DVIDS (api.dvidshub.net): the armed forces' video, public domain as work of the US
    government. A search is of [broll] dvids_category (B-Roll: footage without narration)
    unless that's ""; the files come from each item's asset record (`_dvids_media`)."""
    key = dvids_key(cfg)
    if not key:
        raise ClipperError("no $DVIDS_API_KEY or [paths] dvids_key file (wiki B-roll)")
    if asset:
        items = [_get("https://api.dvidshub.net/asset", {"api_key": key, "id": asset}).get("results") or {}]
    else:
        params = {"api_key": key, "type": "video", "q": query, "max_results": min(max(n * 3, 10), 50)}
        if cat := ((cfg or {}).get("broll") or {}).get("dvids_category"):
            params["category"] = cat
        items = _get("https://api.dvidshub.net/search", params).get("results") or []
    out = []
    for d in items:
        if d.get("type") != "video" or not d.get("id"):
            continue
        credit = d.get("credit")
        if isinstance(credit, list):  # the asset record: [{name, rank, ...}]
            credit = ", ".join(" ".join(x for x in (c.get("rank"), c.get("name")) if x) for c in credit)
        who = ", ".join(x for x in (credit, d.get("unit_name")) if x) or "DVIDS"
        page = d.get("url") or f"https://www.dvidshub.net/video/{d['id'].split(':')[-1]}"
        out.append({"key": page, "source": "dvids", "title": d.get("title", ""), "page": page,
                    "asset": d["id"], "url": "", "files": d.get("files"), "date": (d.get("date") or "")[:10],
                    "credit": f"{who} ({d['branch']}), via DVIDS" if d.get("branch") else f"{who}, via DVIDS",
                    "licence": "public domain (DVIDS)",
                    "description": _text(d.get("description") or d.get("short_description") or ""),
                    "duration": seconds(d.get("duration"))})
    return out


def _dvids_media(c: dict, cfg: dict) -> None:
    """The smallest mp4 at least [broll] max_height tall, else the tallest (DVIDS keeps each
    video from 4K down to 144p). The file addresses carry no key."""
    files = c.get("files")
    if not files:
        files = (dvids("", 1, cfg, asset=c["asset"]) or [{}])[0].get("files") or []
    mp4 = [f for f in files if f.get("type") == "video/mp4" and f.get("src")]
    if not mp4:
        raise ClipperError(f"{c['page']}: no mp4")
    tall = [f for f in mp4 if int(f.get("height") or 0) >= cfg["broll"]["max_height"]]
    pick = (min(tall, key=lambda f: int(f.get("size") or 0)) if tall
            else max(mp4, key=lambda f: int(f.get("height") or 0)))
    c["url"] = pick["src"]


def media(c: dict, cfg: dict) -> None:
    """A candidate's media address and length, where its search didn't give them."""
    if c.get("url"):
        return
    if c["source"] == "nasa":
        _nasa_media(c)
    elif c["source"] == "dvids":
        _dvids_media(c, cfg)


FINDERS = {"nasa": nasa, "dvids": dvids, "prelinger": prelinger, "commons": commons}


def resolve(url: str, cfg: dict | None = None) -> dict:
    """A source page's address -> its candidate (for `add`)."""
    p = urlparse(url)
    host, path = (p.hostname or "").lower(), unquote(p.path)
    found: list[dict] = []
    if host.endswith("nasa.gov") and "/details/" in path:
        found = nasa("", 1, nasa_id=path.split("/details/", 1)[1].strip("/"))
    elif host.endswith("dvidshub.net") and (m := re.search(r"/video/(\d+)", path)):
        found = dvids("", 1, cfg, asset=f"video:{m.group(1)}")
    elif host.endswith("archive.org") and "/details/" in path:
        found = prelinger("", 1, identifier=path.split("/details/", 1)[1].split("/")[0])
    elif host.endswith("wikimedia.org") and "/wiki/" in path:
        found = commons("", 1, title=path.split("/wiki/", 1)[1])
    else:
        raise ClipperError(f"{url}: an images.nasa.gov/details, dvidshub.net/video, archive.org/details or "
                           "commons.wikimedia.org/wiki "
                           "address (or a local file with --credit)")
    if not found:
        raise ClipperError(f"{url}: no video there")
    return found[0]


# --- the library


def length_of(src: str) -> float | None:
    """A video's length by ffprobe, a file or an address (read from its header, not downloaded);
    None when it can't tell."""
    ua = ["-user_agent", UA] if str(src).startswith("http") else []
    try:
        r = subprocess.run(["ffprobe", "-v", "error", *ua, "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(src)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return seconds((r.stdout or "").strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None)


def encode(cfg: dict, src: str, dst: Path, start: float | None = None, end: float | None = None) -> None:
    """src (an address or a file), or start..end of it -> dst: H.264 at most [broll] max_height,
    sound only with [broll] audio."""
    from .cut import encode_args

    bc = cfg["broll"]
    codec = bc["codec"] or cfg["cut"]["clip_codec"]
    args = encode_args(codec, bc["crf"])
    vf = f"scale=-2:'trunc(min(ih,{bc['max_height']})/2)*2':flags=lanczos"  # H.264 needs both even
    if "-vf" in args:  # vaapi uploads in its own filter: after the scale
        i = args.index("-vf")
        vf += "," + args[i + 1]
        del args[i:i + 2]
    elif codec == "h264_qsv":
        vf += ",format=nv12"
    seek = ["-ss", f"{start:.3f}"] if start else []
    length = ["-t", f"{end - (start or 0):.3f}"] if end else []
    tmp = dst.with_suffix(".part.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-user_agent", UA, *seek, "-i", src, *length,
           "-vf", vf, *args, *([] if bc["audio"] else ["-an"]), "-movflags", "+faststart", str(tmp)]
    if not str(src).startswith("http"):
        cmd.remove("-user_agent")
        cmd.remove(UA)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=bc["timeout_s"])
    except subprocess.TimeoutExpired as e:
        tmp.unlink(missing_ok=True)
        raise ClipperError(f"ffmpeg: over [broll] timeout_s ({bc['timeout_s']} s)") from e
    if r.returncode:
        tmp.unlink(missing_ok=True)
        raise ClipperError("ffmpeg: " + ((r.stderr or "").strip().splitlines() or ["failed"])[-1][:200])
    tmp.replace(dst)


def _entry(c: dict, cid: str, status: str, tags: list[str], start=None, end=None) -> dict:
    keep = ("key", "source", "title", "page", "url", "date", "credit", "licence", "description", "duration")
    e = {k: c.get(k) for k in keep}
    e.update(id=cid, status=status, tags=sorted(set(tags)), file=f"{cid}.mp4" if status != "long" else "",
             added=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if start is not None or end is not None:
        e.update(start=start or 0.0, end=end, duration=(end or c.get("duration") or 0) - (start or 0))
    return e


def _save(cfg: dict, entry: dict) -> None:
    with jsonstate.update(_index(cfg), {}) as idx:
        idx.setdefault("clips", {})[entry["id"]] = entry


def _tag(cfg: dict, cid: str, tags: list[str]) -> None:
    with jsonstate.update(_index(cfg), {}) as idx:
        e = idx.get("clips", {}).get(cid)
        if e:
            e["tags"] = sorted(set(e.get("tags", [])) | set(tags))


def take(cfg: dict, c: dict, tags: list[str], start=None, end=None, say=print) -> dict:
    """Download one candidate (or a part of it) into the library as a new clip. The same item
    and range again isn't downloaded twice: its entry keeps its status and gains the tags."""
    media(c, cfg)
    cid = clip_id(c["source"], c["key"], start, end)
    d = root(cfg)
    old = load(cfg)["clips"].get(cid)
    if old and old.get("file") and (d / old["file"]).exists():
        _tag(cfg, cid, tags)
        say(f"  {cid}: already in the library ({old['status']})")
        return load(cfg)["clips"][cid]
    d.mkdir(parents=True, exist_ok=True)
    encode(cfg, c["url"], d / f"{cid}.mp4", start, end)
    e = _entry(c, cid, "new", tags, start, end)
    if old:
        e.update(status=old["status"] if old["status"] != "long" else "new", added=old.get("added", e["added"]),
                 tags=sorted(set(old.get("tags", [])) | set(tags)))
    e["duration"] = round(length_of(str(d / f"{cid}.mp4")) or e.get("duration") or 0, 2)
    _save(cfg, e)
    say(f"  {cid}: {c['title'][:70]} ({e['duration']:.0f} s)")
    return e


def find(cfg: dict, query: str, sources: list[str], n: int | None = None, say=print) -> int:
    """Search each source for the query; new clips (or "long" entries) in the library, tagged
    with it. Returns how many were added."""
    bc = cfg["broll"]
    n = bc["per_source"] if n is None else n
    if n < 1:
        raise ClipperError("broll find: -n is the most new clips from each source, at least 1")
    query = query.strip()
    if not query:
        raise ClipperError("broll find: a search, e.g. \"hurricane from orbit\"")
    added = 0
    for src in sources:
        try:
            cands = FINDERS[src](query, n, cfg)
        except ClipperError as e:
            say(f"{src}: {e}")
            continue
        lib = load(cfg)
        have = {e["key"] for e in lib["clips"].values() if e.get("start") is None
                and not (e["status"] == "long" and _fits(e, bc["max_s"]))}  # now fits: taken
        new = skipped = 0
        for c in cands:
            if new >= n:
                break
            cid = clip_id(src, c["key"])
            if c["key"] in lib["dropped"]:
                continue
            if c["key"] in have:
                _tag(cfg, cid, [query])
                continue
            if why := refused(c, cfg):
                skipped += 1
                say(f"  skip {c['title'][:60]!r}: {why}")
                continue
            try:
                media(c, cfg)  # a long one keeps its address too, for add --from/--to
                if c.get("duration") is None:
                    c["duration"] = length_of(c["url"])
                if c["duration"] is None:
                    skipped += 1
                    say(f"  skip {c['title'][:60]!r}: its length is unknown (clipper broll add {c['page']} "
                        "--from/--to takes a part)")
                    continue
                if c["duration"] > bc["max_s"]:
                    old = lib["clips"].get(cid) or {}
                    _save(cfg, _entry(c, cid, "long", [*old.get("tags", []), query]))
                    say(f"  {cid}: {c['title'][:70]} ({c['duration'] / 60:.0f} min, long: "
                        f"clipper broll add {cid} --from 0:01:20 --to 0:01:45)")
                else:
                    take(cfg, c, [query], say=say)
            except ClipperError as e:
                say(f"  {c['title'][:60]!r}: {e}")
                continue
            new += 1
        say(f"{src}: {new} added" + (f", {skipped} skipped" if skipped else ""))
        added += new
    return added


def add(cfg: dict, target: str, start: float | None = None, end: float | None = None,
        tags: list[str] | None = None, credit: str = "", licence: str = "public domain", say=print) -> dict:
    """One clip: a library id (a part of a long one, or of any clip), a source page's address,
    or a local file (with credit: who made it)."""
    tags = tags or []
    lib = load(cfg)
    max_s = cfg["broll"]["max_s"]
    undrop = ""
    if target in lib["clips"]:
        base = lib["clips"][target]
        if start is None and end is None and base["status"] == "long" and _fits(base, max_s):
            c = dict(base)  # listed long before [broll] max_s went up: now it fits, taken whole
            if why := refused(c, cfg):
                raise ClipperError(f"{target}: {why}")
            return take(cfg, c, tags, say=say)  # take keeps the entry's own tags
        if start is None and end is None:
            raise ClipperError(f"{target} is {'long' if base['status'] == 'long' else 'already in the library'}: "
                               "--from/--to take a part of it")
        c = dict(base)
        if why := refused(c, cfg):  # the settings may have changed since it was taken ([broll] audio)
            raise ClipperError(f"{target}: {why}")
        tags = [*base.get("tags", []), *tags]
        span = base.get("duration")
        _inside(start, end, span, target)
        if base.get("start") is not None:  # a part of a part: times stay the source's
            off = base["start"]
            start, end = (start or 0) + off, off + (end if end is not None else span)
            c["duration"] = base.get("end")
    else:
        c = _candidate(cfg, target, credit, licence)
        _inside(start, end, c["duration"], c["title"][:60])
        if c["key"] in lib["dropped"]:
            undrop = c["key"]  # added by hand: no longer dropped, once the add has worked
        if start is None and end is None and c["duration"] > max_s:
            cid = clip_id(c["source"], c["key"])
            old = lib["clips"].get(cid) or {}
            if old.get("file") and (root(cfg) / old["file"]).exists():  # taken whole before (max_s was higher)
                _tag(cfg, cid, tags)
                say(f"{cid}: already in the library ({old['status']})")
                return load(cfg)["clips"][cid]
            e = _entry(c, cid, "long", [*old.get("tags", []), *tags])
            _save(cfg, e)
            _undrop(cfg, undrop)
            say(f"{e['id']}: {c['title'][:70]} ({c['duration'] / 60:.0f} min, long: add it again with --from/--to)")
            return e
    if end is None and start is not None:
        end = c["duration"]  # known here: _inside refused an open end without it
        if end - start > max_s:
            raise ClipperError(f"from {start:.0f} s to the end is over [broll] max_s ({max_s} s): give --to too")
    elif end is not None and start is None:
        start = 0.0
    e = take(cfg, c, tags, start, end, say=say)
    _undrop(cfg, undrop)
    return e


def _fits(e: dict, max_s: float) -> bool:
    """A long entry whose known length is now within max_s (an unknown one never is)."""
    return e.get("duration") is not None and e["duration"] <= max_s


def _undrop(cfg: dict, key: str) -> None:
    if key:
        with jsonstate.update(_index(cfg), {}) as idx:
            idx["dropped"] = [k for k in idx.get("dropped", []) if k != key]


def _candidate(cfg: dict, target: str, credit: str, licence: str) -> dict:
    """A video file or a source page's address -> a checked candidate, its length probed if
    its source didn't say."""
    if Path(target).suffix.lower() in VIDEO_EXT and Path(target).is_file():
        if not credit:
            raise ClipperError("a local file needs --credit (who made it, as the video's description will say)")
        p = Path(target).resolve()
        c = {"key": p.as_uri(), "source": "file", "title": p.stem, "page": "", "url": str(p), "date": "",
             "credit": credit, "licence": licence, "description": "", "duration": None}
    elif target.startswith(("http://", "https://")):
        c = resolve(target, cfg)
        media(c, cfg)
    else:
        raise ClipperError(f"{target}: not a clip id (clipper broll list), a web address or a video file")
    if why := refused(c, cfg):
        raise ClipperError(f"{c['title'][:60]!r}: {why}")
    if c.get("duration") is None:
        c["duration"] = length_of(c["url"])
    return c


def _inside(start: float | None, end: float | None, span: float | None, what: str) -> None:
    """A part's times against the clip they're taken from; with no length known, only a part
    with both ends (nothing unbounded is downloaded)."""
    if start is not None and end is not None and end <= start:
        raise ClipperError("--to must be after --from")
    if span is None:
        if end is None:
            raise ClipperError(f"{what}: its length is unknown: give --from and --to")
        return
    if (start or 0) >= span or (end is not None and end > span + 0.5):
        raise ClipperError(f"{what} is {span:.0f} s long: --from/--to must be inside it")


def set_status(cfg: dict, ids: list[str], status: str) -> None:
    """keep: status kept; drop: the file deleted and its source remembered (a part's isn't:
    other parts of the same film stay findable)."""
    ids = list(dict.fromkeys(ids))
    d = root(cfg)
    with jsonstate.update(_index(cfg), {}) as idx:
        clips = idx.setdefault("clips", {})
        missing = [i for i in ids if i not in clips]
        if missing:  # before any file is deleted
            raise ClipperError(f"no clip {', '.join(missing)} (clipper broll list)")
        for i in ids:
            if status == "drop":
                e = clips.pop(i)
                if e.get("start") is None:
                    idx.setdefault("dropped", []).append(e["key"])
                if e.get("file"):
                    (d / e["file"]).unlink(missing_ok=True)
            elif clips[i]["status"] == "long":
                raise ClipperError(f"{i} is long and has no file: add a part with --from/--to, keep that")
            else:
                clips[i]["status"] = "kept"


def select(lib: dict, tag: str = "", source: str = "", status: str = "") -> list[dict]:
    out = []
    for e in lib["clips"].values():
        if tag and not any(tag.lower() in t.lower() for t in e.get("tags", [])):
            continue
        if (source and e["source"] != source) or (status and e["status"] != status):
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.get("added", ""), reverse=True)


def show_list(cfg: dict, tag: str = "", source: str = "", status: str = "", as_json: bool = False) -> None:
    clips = select(load(cfg), tag, source, status)
    if as_json:
        d = root(cfg)
        print(json.dumps([{**e, "path": str(d / e["file"]) if e.get("file") else ""} for e in clips], indent=1))
        return
    if not clips:
        print("no clips" + (" like that" if tag or source or status else ": clipper broll find \"<search>\""))
        return
    for e in clips:
        part = f" {e['start']:.0f}-{e['end']:.0f} s of it" if e.get("start") is not None and e.get("end") else ""
        print(f"{e['id']}  {e['status']:<4}  {e.get('duration') or 0:6.0f} s  {e['title'][:60]}{part}"
              f"  [{', '.join(e.get('tags', []))}]")
        print(f"{'':14}{e['credit'][:70]} | {e['licence'][:30]} | {e['page']}")
