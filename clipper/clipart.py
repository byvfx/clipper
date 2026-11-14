"""`clipper clipart`: a library of free clip art, engravings, icons and emoji for covers, the
weekly issue, story cuts and explainers, each file with where it came from, its credit and its
licence. The b-roll library's still-picture sibling (`clipper broll`).

    clipper clipart find "<query>" [--source commons|met|cleveland|iconify|pixabay] [-n N]
    clipper clipart add <url|set:icon|file|folder> [--tag X] [--credit TEXT] [--licence TEXT]
    clipper clipart list [--tag X] [--source X] [--status new|kept] [--json]
    clipper clipart keep|drop <id> ...

Sources: commons (Wikimedia Commons drawings and pictures; SVGs come as PNG renders), met (The
Met's open access: public-domain works, CC0), cleveland (the Cleveland Museum of Art's open
access, CC0), iconify (emoji and icon sets through api.iconify.design, each set's licence from
its own record; logo and brand sets never), pixabay (illustrations and vectors under the Pixabay
Content License; a free key, $PIXABAY_API_KEY or the first line of [paths] pixabay_key). A file is
kept only when its licence is one of [clipart] licences (public domain, CC0, permissive software
licences such as MIT and Apache, and Pixabay's at first), nothing in its title, description or
credit names an outside owner, and Commons doesn't mark it trademarked or a person's likeness.

Everything is in [paths] clipart: <id>.<ext> and clipart.json, each file's source page, address,
title, credit, licence, size, tags (a find's query) and status: new until kept or dropped; a
dropped file is deleted and its source remembered, so the next find doesn't bring it back.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from . import broll, config, jsonstate, wiki
from .errors import ClipperError

INDEX = "clipart.json"
SOURCES = ("commons", "met", "cleveland", "iconify", "pixabay")
STATUSES = ("new", "kept")
LICENCES = ("pd", "cc0", "permissive", "pixabay", "cc-by", "cc-by-sa")
UA = "clipper-clipart/1.0 (clip art library; https://github.com/byvfx/clipper)"  # Wikimedia asks for one
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
            "image/svg+xml": ".svg"}
ICON = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*):([a-z0-9]+(?:-[a-z0-9]+)*)$")  # an Iconify name, set:icon
# One icon drawn in several weights or styles (ph:cow, ph:cow-bold, ph:cow-fill ...): a find keeps the first.
VARIANT = re.compile(r"(-(bold|fill|filled|light|thin|duotone|duocolor|outline|outlined|regular|solid|sharp|"
                     r"round|rounded|twotone|line|linear|broken|bulk|flat|\d+))+$")
PIXABAY_CACHE = ".pixabay_cache.json"  # Pixabay's API terms: keep each response 24 hours
licence_code = broll.licence_code  # pd | cc0 | permissive | pixabay | cc-by | cc-by-sa | other


class TooBig(ClipperError):
    """A download over [clipart] max_mb."""


def root(cfg: dict) -> Path:
    d = cfg["paths"]["clipart"]
    if not d:
        raise ClipperError("[paths] clipart is unset: the clip art library lives there")
    return Path(d)


def load(cfg: dict) -> dict:
    data = jsonstate.read(root(cfg) / INDEX, None) or {}
    return {"art": {}, "dropped": [], **data}


def _index(cfg: dict) -> Path:
    d = root(cfg)
    d.mkdir(parents=True, exist_ok=True)
    return d / INDEX


def art_id(source: str, key: str) -> str:
    """Stable for a source item: the same find or add never makes a second copy."""
    return f"{source}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def refused(c: dict, cfg: dict) -> str:
    """Why a candidate can't be kept, or "" when it can."""
    allowed = cfg["clipart"]["licences"]
    code = licence_code(c.get("licence", ""))
    if code not in allowed:
        return f"licence {c.get('licence') or 'not stated'} ({code}; [clipart] licences: {', '.join(allowed)})"
    if c.get("restrictions"):
        return f"Commons marks it {c['restrictions']!r}"
    if who := broll.owned(" ".join(str(c.get(k, "")) for k in ("title", "description", "credit"))):
        return f"its description says {who!r}"
    return ""


def pixabay_key(cfg: dict | None = None) -> str:
    """$PIXABAY_API_KEY, else the first line of [paths] pixabay_key; "" if neither."""
    return config.key(cfg, "PIXABAY_API_KEY", "pixabay_key")


def _get(url: str, params: dict | None = None, timeout: int = 20):
    return broll._get(url, params, timeout, ua=UA)


def _text(text) -> str:
    return broll._text(str(text or ""))


# --- sources: each returns candidates {key, source, title, page, url, alt, credit, licence,
# --- description, restrictions}; alt is a smaller copy for when url is over [clipart] max_mb


def commons(query: str, n: int, cfg: dict, title: str = "") -> Iterator[dict]:
    """Wikimedia Commons drawings (SVG) first, then, with [clipart] commons_pictures and only
    when a find still wants more, pictures (photos, scans); licence, author and restrictions
    from each file's metadata. An SVG, or a picture wider than [clipart] max_px, comes as
    Commons' own render at about that width (Commons rounds it to one of its standard sizes)."""
    if title:
        return iter(_commons_files(_commons_query(cfg, titles=title), cfg))
    limit = min(max(n * 3, 10), 50)
    drawings = _commons_files(_commons_query(cfg, gsrsearch=f"{query} filetype:drawing", gsrlimit=limit), cfg)
    return _commons_then_pictures(drawings, query, limit, cfg)


def _commons_then_pictures(drawings: list[dict], query: str, limit: int, cfg: dict) -> Iterator[dict]:
    yield from drawings
    if not cfg["clipart"]["commons_pictures"]:
        return
    # asked only once the drawings ran out; find says it if this fails, the drawings stay taken
    yield from _commons_files(_commons_query(cfg, gsrsearch=f"{query} filetype:bitmap", gsrlimit=limit), cfg)


def _commons_query(cfg: dict, **search) -> dict:
    params = {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size",
              "iiurlwidth": cfg["clipart"]["max_px"], **search}
    if "gsrsearch" in search:
        params.update(generator="search", gsrnamespace=6)
    js = _get("https://commons.wikimedia.org/w/api.php", params)
    if err := js.get("error"):
        raise ClipperError(f"Commons: {err.get('code', '')} {err.get('info', '')}".strip())
    return js


def _commons_files(js: dict, cfg: dict) -> list[dict]:
    out = []
    for pg in sorted((js.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (pg.get("imageinfo") or [None])[0]
        if not ii or not str(ii.get("mime", "")).startswith("image/"):
            continue
        meta = {k: _text((v or {}).get("value", "")) for k, v in (ii.get("extmetadata") or {}).items()}
        original = ii["url"].split("?")[0]
        render = (ii.get("thumburl") or "").split("?")[0]
        svg = ii.get("mime") == "image/svg+xml"
        url = render if render and (svg or int(ii.get("width") or 0) > cfg["clipart"]["max_px"]) else original
        page = ii.get("descriptionurl") or original
        out.append({"key": page, "source": "commons", "title": meta.get("ObjectName") or pg.get("title", ""),
                    "page": page, "url": url, "alt": render if url != render else "",
                    "credit": (meta.get("Artist") or "unknown") + ", via Wikimedia Commons",
                    "licence": meta.get("LicenseShortName") or meta.get("License") or "",
                    "description": meta.get("ImageDescription", ""), "restrictions": meta.get("Restrictions", "")})
    return out


def met(query: str, n: int, cfg: dict, object_id: str = "") -> Iterator[dict]:
    """The Metropolitan Museum of Art's open access (collectionapi.metmuseum.org, no key): only
    works it marks public domain, which it releases as CC0. A search returns ids; each object is
    one more request (the Met asks for under 80 a second), so candidates come one at a time and
    a find stops asking once it has enough."""
    if object_id:
        ids = [object_id]
    else:  # searched now, so a failing search is the call's error, not the first candidate's
        js = _get("https://collectionapi.metmuseum.org/public/collection/v1.1/search",
                  {"q": query, "hasImages": "true", "limit": max(n * 4, 12)})
        ids = js.get("objectIDs") or []
    return _met_objects(ids)


def _met_objects(ids: list) -> Iterator[dict]:
    for oid in ids:
        try:
            d = _get(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{quote(str(oid))}")
        except ClipperError:
            continue
        if not d.get("isPublicDomain") or not (d.get("primaryImage") or d.get("primaryImageSmall")):
            continue
        page = d.get("objectURL") or f"https://www.metmuseum.org/art/collection/search/{oid}"
        who = d.get("artistDisplayName") or d.get("culture") or ""
        yield {"key": page, "source": "met", "title": d.get("title", ""), "page": page,
               "url": d.get("primaryImage") or d["primaryImageSmall"],
               "alt": d.get("primaryImageSmall") if d.get("primaryImage") else "",
               "credit": f"{who + ', ' if who else ''}The Metropolitan Museum of Art",
               "licence": "CC0 (The Met Open Access)",
               "description": _text("; ".join(str(x) for x in (d.get("objectDate"), d.get("medium"),
                                                                d.get("rightsAndReproduction")) if x)),
               "restrictions": ""}


def cleveland(query: str, n: int, cfg: dict, accession: str = "") -> list[dict]:
    """The Cleveland Museum of Art's open access (openaccess-api.clevelandart.org, no key): only
    works it marks CC0, its 3400-pixel print copy."""
    base = "https://openaccess-api.clevelandart.org/api/artworks/"
    if accession:
        items = [_get(base + quote(accession)).get("data") or {}]
    else:
        items = _get(base, {"q": query, "cc0": 1, "has_image": 1, "limit": max(n * 3, 10)}).get("data") or []
    out = []
    for d in items:
        imgs = d.get("images") or {}
        big = (imgs.get("print") or {}).get("url") or ""
        web = (imgs.get("web") or {}).get("url") or ""
        if d.get("share_license_status") != "CC0" or not (big or web):
            continue
        page = d.get("url") or f"https://clevelandart.org/art/{d.get('accession_number', '')}"
        who = next((c.get("description") for c in d.get("creators") or [] if c.get("description")), "")
        out.append({"key": page, "source": "cleveland", "title": d.get("title", ""), "page": page,
                    "url": big or web, "alt": web if big else "",
                    "credit": f"{who + ', ' if who else ''}The Cleveland Museum of Art", "licence": "CC0",
                    "description": _text(" ".join(str(x) for x in (d.get("tombstone"), d.get("copyright")) if x)),
                    "restrictions": ""})
    return out


def _sets(cfg: dict) -> dict:
    """The Iconify sets a search may use: [clipart] iconify_sets ([] = all of them), less
    any logo or brand set (trademarks, whatever the licence says)."""
    want = list(cfg["clipart"]["iconify_sets"])
    info = _get("https://api.iconify.design/collections", {"prefixes": ",".join(want)} if want else None)
    return {p: i for p, i in info.items()
            if isinstance(i, dict) and not re.search(r"logo|brand", str(i.get("category", "")), re.I)}


def iconify(query: str, n: int, cfg: dict, icon: str = "") -> list[dict]:
    """Emoji and icons through api.iconify.design (no key), each set's licence from its own
    record (Fluent Emoji MIT, Noto Apache, Tabler MIT, ...). Files are SVG."""
    sets = _sets(cfg)
    if icon:
        names = [icon]
    else:
        params = {"query": query, "limit": min(max(32, n * 4), 999)}
        if cfg["clipart"]["iconify_sets"]:
            if not sets:
                raise ClipperError("none of [clipart] iconify_sets is an Iconify set (or each is a logo set)")
            params["prefixes"] = ",".join(sets)
        names = _get("https://api.iconify.design/search", params).get("icons") or []
    out = []
    seen = set()
    for name in names:
        prefix, _, ic = name.partition(":")
        info = sets.get(prefix)
        if not info or not ic or (not icon and (prefix, VARIANT.sub("", ic)) in seen):
            continue
        seen.add((prefix, VARIANT.sub("", ic)))
        lic = info.get("license") or {}
        author = (info.get("author") or {}).get("name", "")
        page = f"https://icon-sets.iconify.design/{prefix}/{ic}/"
        out.append({"key": f"iconify:{name}", "source": "iconify", "title": f"{info.get('name', prefix)}: {ic}",
                    "page": page, "alt": "",  # sized: the bare SVG says 1em, which a renderer reads as 16 px
                    "url": f"https://api.iconify.design/{prefix}/{ic}.svg?height={cfg['clipart']['max_px']}",
                    "credit": f"{info.get('name', prefix)}{' by ' + author if author else ''}, via Iconify",
                    "licence": " ".join(x for x in (lic.get("title"), f"({lic['spdx']})" if lic.get("spdx") else "")
                                        if x),
                    "description": "", "restrictions": ""})
    return out


def pixabay(query: str, n: int, cfg: dict, pid: str = "") -> list[dict]:
    """Pixabay's illustrations or vectors ([clipart] pixabay_type) through its API (a free key).
    The Pixabay Content License needs no credit; it bans selling a file unaltered and using
    one that shows a brand, or a person in a bad light. The API allows 100 requests a minute and
    no hotlinking: files are downloaded, at most 1280 pixels without Pixabay's full access."""
    key = pixabay_key(cfg)
    if not key:
        raise ClipperError(f"no $PIXABAY_API_KEY or [paths] pixabay_key file ({wiki.page('Clip-art', 'sources')})")
    cc = cfg["clipart"]
    params: dict = {}
    if pid:
        params["id"] = pid
    else:
        params.update(q=query[:100], image_type=cc["pixabay_type"], per_page=min(max(n * 3, 3), 200))
        if cc["pixabay_transparent"]:
            params["colors"] = "transparent"
    hits = _pixabay_hits(cfg, key, params)
    out = []
    for h in hits:
        url = h.get("largeImageURL") or h.get("webformatURL")
        if not url or not h.get("pageURL"):
            continue
        out.append({"key": h["pageURL"], "source": "pixabay", "title": h.get("tags", ""), "page": h["pageURL"],
                    "url": url, "alt": h.get("webformatURL") if url != h.get("webformatURL") else "",
                    "credit": f"{h.get('user') or 'unknown'} on Pixabay", "licence": "Pixabay Content License",
                    "description": "", "restrictions": ""})
    return out


def _pixabay_hits(cfg: dict, key: str, params: dict) -> list[dict]:
    """One Pixabay API response, from the library's cache when the same request (the key left
    out) was made in the last 24 hours, as Pixabay's API terms ask."""
    req = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    path = _index(cfg).with_name(PIXABAY_CACHE)
    now = time.time()
    cached = (jsonstate.read(path, None) or {}).get(req) or {}
    if now - cached.get("t", 0) < 86400:
        return cached.get("hits") or []
    try:
        hits = _get("https://pixabay.com/api/", {"key": key, **params}).get("hits") or []
    except ClipperError as e:  # the address carries the key: never let it into a message
        raise ClipperError(str(e).replace(key, "***")) from None
    with jsonstate.update(path, {}) as cache:
        for k in [k for k, v in cache.items() if now - v.get("t", 0) >= 86400]:
            del cache[k]
        cache[req] = {"t": now, "hits": hits}
    return hits


FINDERS = {"commons": commons, "met": met, "cleveland": cleveland, "iconify": iconify, "pixabay": pixabay}


def resolve(target: str, cfg: dict) -> dict:
    """A source page's address, or an Iconify name (set:icon) -> its candidate (for `add`)."""
    if ICON.match(target):
        found = iconify("", 1, cfg, icon=target)
        if not found:
            raise ClipperError(f"{target}: no such icon in an allowed set ([clipart] iconify_sets, no logo sets)")
        return found[0]
    p = urlparse(target)
    host, path = (p.hostname or "").lower(), unquote(p.path)
    found: list[dict] = []
    if host.endswith("wikimedia.org") and "/wiki/" in path:
        found = list(commons("", 1, cfg, title=path.split("/wiki/", 1)[1]))
    elif host.endswith("metmuseum.org") and (m := re.search(r"/search/(\d+)", path)):
        found = list(met("", 1, cfg, object_id=m.group(1)))
    elif host.endswith("clevelandart.org") and "/art/" in path:
        found = cleveland("", 1, cfg, accession=path.split("/art/", 1)[1].strip("/"))
    elif host.endswith("pixabay.com") and (m := re.search(r"-(\d+)/?$", path)):
        found = pixabay("", 1, cfg, pid=m.group(1))
    elif host == "icon-sets.iconify.design" and (m := re.match(r"/([^/]+)/([^/]+)", path)):
        return resolve(f"{m.group(1)}:{m.group(2)}", cfg)
    else:
        raise ClipperError(f"{target}: a Commons File: page, a metmuseum.org/art/collection/search, clevelandart.org/art "
                           "or pixabay.com address, an Iconify name like fluent-emoji:cow-face, or a picture "
                           "file or folder (with --credit)")
    if not found:
        raise ClipperError(f"{target}: no picture there that is free to use")
    return found[0]


# --- the library


def download(url: str, stem: Path, max_mb: float, timeout: int) -> Path:
    """url -> stem + its extension (from the address, else the content type), no bigger than
    max_mb; written beside it first, so a failed download leaves nothing."""
    import requests

    host = urlparse(url).hostname or url
    try:
        r = requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": UA})
    except requests.RequestException as e:
        raise ClipperError(f"{host}: {type(e).__name__}") from e
    with r:
        if not r.ok:
            raise ClipperError(f"{host}: HTTP {r.status_code}")
        mime = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not mime.startswith("image/"):
            raise ClipperError(f"{host}: not a picture ({mime or 'no content type'})")
        ext = Path(urlparse(url).path).suffix.lower().replace(".jpeg", ".jpg")
        ext = MIME_EXT.get(mime) or (ext if ext in IMAGE_EXT else "")  # what was sent, over what the address says
        if not ext:
            raise ClipperError(f"{host}: {mime} isn't a picture clipart keeps")
        if (size := int(r.headers.get("Content-Length") or 0)) > max_mb * 1e6:
            raise TooBig(f"{host}: {size / 1e6:.0f} MB, over [clipart] max_mb ({max_mb})")
        dst = stem.with_suffix(ext)
        tmp = stem.with_suffix(ext + ".part")
        got = 0
        try:
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1 << 16):
                    got += len(chunk)
                    if got > max_mb * 1e6:
                        raise TooBig(f"{host}: over [clipart] max_mb ({max_mb} MB)")
                    f.write(chunk)
            tmp.replace(dst)
        except requests.RequestException as e:
            raise ClipperError(f"{host}: {type(e).__name__}") from e
        except OSError as e:  # the share or the disk
            raise ClipperError(f"{dst.name}: {e.strerror or type(e).__name__}") from e
        finally:
            tmp.unlink(missing_ok=True)  # gone already when replaced
        return dst


def size_of(path: Path) -> tuple[int, int] | None:
    """A raster picture's width and height; None for an SVG or a file Pillow can't read."""
    if path.suffix.lower() == ".svg":
        return None
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - any unreadable picture is just unmeasured
        return None


def _entry(c: dict, aid: str, path: Path, tags: list[str]) -> dict:
    keep = ("key", "source", "title", "page", "url", "credit", "licence", "description")
    e = {k: c.get(k) for k in keep}
    wh = size_of(path)
    e.update(id=aid, status="new", tags=sorted(set(tags)), file=path.name,
             width=wh[0] if wh else None, height=wh[1] if wh else None,
             added=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return e


def _tag(cfg: dict, aid: str, tags: list[str]) -> None:
    with jsonstate.update(_index(cfg), {}) as idx:
        e = idx.get("art", {}).get(aid)
        if e:
            e["tags"] = sorted(set(e.get("tags", [])) | set(tags))


def take(cfg: dict, c: dict, tags: list[str], say=print) -> dict:
    """One candidate into the library as new art. The same item again isn't downloaded twice:
    its entry keeps its status and gains the tags."""
    aid = art_id(c["source"], c["key"])
    d = root(cfg)
    old = load(cfg)["art"].get(aid)
    if old and old.get("file") and (d / old["file"]).exists():
        _tag(cfg, aid, tags)
        say(f"  {aid}: already in the library ({old['status']})")
        return load(cfg)["art"][aid]
    d.mkdir(parents=True, exist_ok=True)
    cc = cfg["clipart"]
    if c["source"] == "file":
        path = d / f"{aid}{Path(c['url']).suffix.lower().replace('.jpeg', '.jpg')}"
        try:
            shutil.copyfile(c["url"], path)
        except OSError as e:
            path.unlink(missing_ok=True)
            raise ClipperError(f"{c['url']}: {e.strerror or type(e).__name__}") from e
    else:
        try:
            path = download(c["url"], d / aid, cc["max_mb"], cc["timeout_s"])
        except TooBig:
            if not c.get("alt"):
                raise
            path = download(c["alt"], d / aid, cc["max_mb"], cc["timeout_s"])
    e = _entry(c, aid, path, tags)
    if old:
        e.update(status=old["status"], added=old.get("added", e["added"]),
                 tags=sorted(set(old.get("tags", [])) | set(tags)))
    with jsonstate.update(_index(cfg), {}) as idx:
        idx.setdefault("art", {})[aid] = e
    size = f"{e['width']}x{e['height']}" if e["width"] else path.suffix[1:]
    say(f"  {aid}: {(c['title'] or path.stem)[:70]} ({size})")
    return e


def find(cfg: dict, query: str, sources: list[str], n: int | None = None, say=print) -> int:
    """Search each source for the query; new art in the library, tagged with it. Returns how
    many were added."""
    n = cfg["clipart"]["per_source"] if n is None else n
    if n < 1:
        raise ClipperError("clipart find: -n is the most new pictures from each source, at least 1")
    query = query.strip()
    if not query:
        raise ClipperError("clipart find: a search, e.g. \"cow engraving\"")
    added = 0
    for src in sources:
        try:
            cands = FINDERS[src](query, n, cfg)
        except ClipperError as e:
            say(f"{src}: {e}")
            continue
        lib = load(cfg)
        have = {e["key"] for e in lib["art"].values()}
        new = skipped = 0
        stopped: list[str] = []
        for c in _until_error(cands, stopped):  # a lazy source (met, commons) fetches only what this takes
            if c["key"] in lib["dropped"]:
                continue
            if c["key"] in have:
                _tag(cfg, art_id(src, c["key"]), [query])
                continue
            if why := refused(c, cfg):
                skipped += 1
                say(f"  skip {c['title'][:60]!r}: {why}")
                continue
            try:
                take(cfg, c, [query], say=say)
            except ClipperError as e:
                say(f"  {c['title'][:60]!r}: {e}")
                continue
            new += 1
            if new >= n:
                break
        say(f"{src}: {new} added" + (f", {skipped} skipped" if skipped else "")
            + (f", stopped early: {stopped[0]}" if stopped else ""))
        added += new
    return added


def _until_error(cands, errors: list[str]) -> Iterator[dict]:
    """A source's candidates until it raises while fetching more (a lazy source's later
    request); the error goes in errors and what came before stays."""
    it = iter(cands)
    while True:
        try:
            yield next(it)
        except StopIteration:
            return
        except ClipperError as e:
            errors.append(str(e))
            return


def add(cfg: dict, target: str, tags: list[str] | None = None, credit: str = "", licence: str = "public domain",
        say=print) -> list[dict]:
    """A source page's address, an Iconify name, or a picture file or a folder of them (with
    credit: who made it; a folder's pictures, its subfolders too, all with the same credit and
    licence, e.g. a Kenney pack)."""
    tags = tags or []
    p = Path(target)
    if p.is_dir() or (p.suffix.lower() in IMAGE_EXT and p.is_file()):
        if not credit:
            raise ClipperError("a picture file or folder needs --credit (who made it, as a description would say)")
        files = sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in IMAGE_EXT) if p.is_dir() else [p]
        if not files:
            raise ClipperError(f"{target}: no pictures ({', '.join(sorted(IMAGE_EXT))}) in it")
        cands = [{"key": f.resolve().as_uri(), "source": "file", "title": f.stem, "page": "", "url": str(f.resolve()),
                  "credit": credit, "licence": licence, "description": "", "restrictions": ""} for f in files]
    elif target.startswith(("http://", "https://")) or ICON.match(target):
        cands = [resolve(target, cfg)]
    else:
        raise ClipperError(f"{target}: not a web address, an Iconify name (set:icon), or a picture file or folder")
    if why := refused(cands[0], cfg):  # one licence and credit for a whole folder
        raise ClipperError(f"{cands[0]['title'][:60]!r}: {why}")
    if len(cands) == 1:
        out = [take(cfg, cands[0], tags, say=say)]
    else:  # a folder: one unreadable file is said and skipped, the rest still go in
        out, failed = [], 0
        for c in cands:
            try:
                out.append(take(cfg, c, tags, say=say))
            except ClipperError as e:
                failed += 1
                say(f"  {c['title'][:60]!r}: {e}")
        say(f"{len(out)} pictures added from {target}" + (f", {failed} failed" if failed else ""))
    keys = {e["key"] for e in out}
    with jsonstate.update(_index(cfg), {}) as idx:  # added by hand: no longer dropped
        if keys & set(idx.get("dropped", [])):
            idx["dropped"] = [k for k in idx["dropped"] if k not in keys]
    return out


def set_status(cfg: dict, ids: list[str], status: str) -> None:
    """keep: status kept; drop: the file deleted and its source remembered."""
    ids = list(dict.fromkeys(ids))
    d = root(cfg)
    with jsonstate.update(_index(cfg), {}) as idx:
        art = idx.setdefault("art", {})
        missing = [i for i in ids if i not in art]
        if missing:  # before any file is deleted
            raise ClipperError(f"no art {', '.join(missing)} (clipper clipart list)")
        for i in ids:
            if status == "drop":
                e = art.pop(i)
                idx.setdefault("dropped", []).append(e["key"])
                if e.get("file"):
                    (d / e["file"]).unlink(missing_ok=True)
            else:
                art[i]["status"] = "kept"


def select(lib: dict, tag: str = "", source: str = "", status: str = "") -> list[dict]:
    out = []
    for e in lib["art"].values():
        if tag and not any(tag.lower() in t.lower() for t in e.get("tags", [])):
            continue
        if (source and e["source"] != source) or (status and e["status"] != status):
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.get("added", ""), reverse=True)


def show_list(cfg: dict, tag: str = "", source: str = "", status: str = "", as_json: bool = False) -> None:
    art = select(load(cfg), tag, source, status)
    if as_json:
        d = root(cfg)
        print(json.dumps([{**e, "path": str(d / e["file"])} for e in art], indent=1))
        return
    if not art:
        print("no art" + (" like that" if tag or source or status else ": clipper clipart find \"<search>\""))
        return
    for e in art:
        size = f"{e['width']}x{e['height']}" if e.get("width") else Path(e["file"]).suffix[1:]
        print(f"{e['id']}  {e['status']:<4}  {size:>9}  {(e['title'] or '')[:60]}  [{', '.join(e.get('tags', []))}]")
        print(f"{'':18}{e['credit'][:70]} | {e['licence'][:30]} | {e['page']}")
