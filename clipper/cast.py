"""`clipper cast`: the cast library (#41), a few sharp head-and-shoulders photos of each regular
in [thumbnail] photos/<Person>/, for covers, the weekly and avatars, so the right face is shown
even when someone is only talked about.

    clipper cast list                          everyone: photos kept, new, and from where
    clipper cast grab <stream> [--person X]    the sharpest faces `clipper faces` named in a stream
    clipper cast find <Person> [--source ...]  from the web: Wikimedia Commons, the person's own
                                               channels (avatar, video thumbnails), image search
    clipper cast add <Person> <file|url>       one photo
    clipper cast keep|drop <Person> <id> ...   (the war room's Cast window does these too)
    clipper cast label <Person> <id> <label>
    clipper cast set <Person> --alias A --channel URL
    clipper cast auto                          a find for everyone short of [cast] target photos
                                               (serve, every [cast] auto_hours)

Every photo is checked for the face first. With the person enrolled in faces/known.json only a
face like theirs is taken ([cast] match), and one that looks like someone else enrolled is
refused; otherwise the biggest face. It is cropped to head and shoulders at full size, labelled
by a small vision call ([cast] label_model: neutral, laughing, angry, ...) and stays "new" until
kept or dropped, unless it is sure: an enrolled person's face matched at [cast] auto_keep or
better, labelled with no doubt, while they have fewer than [cast] target kept. The worker
grabs each new stream's named faces (`cast grab --needed`, the `cast` stage).
photos/<Person>/cast.json records each photo's source, licence and face box;
a dropped photo's source is remembered so the next find doesn't bring it back. Image search
skips photo agencies ([cast] blocked_domains: their photographers file claims) and needs
$BRAVE_API_KEY or the file [paths] brave_key. A single photos/<Person>.jpg still works as before."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import jsonstate
from .errors import ClipperError

INDEX = "cast.json"
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SOURCES = ("commons", "channels", "search")
UA = "clipper-cast/1.0 (cast photo library; https://github.com/byvfx/clipper)"  # Wikimedia asks for one
MAX_BYTES = 15_000_000
SAME_PHOTO = 0.95  # two photos whose faces are this alike are one photo (a crop, a resize)


def root(cfg: dict) -> Path:
    d = cfg["thumbnail"]["photos"]
    if not d:
        raise ClipperError("[thumbnail] photos is unset: the cast library lives there")
    return Path(d)


def person_dir(cfg: dict, person: str) -> Path:
    from .faces import checked_name

    name = checked_name(person)
    if any(c in name for c in '/\\:*?"<>|') or name.startswith("."):
        raise ClipperError(f"{name!r}: a person's name can't have / \\ : * ? \" < > |")
    return root(cfg) / name


def load(d: Path) -> dict:
    data = jsonstate.read(d / INDEX, None) or {}
    return {"aliases": [], "channels": [], "photos": {}, "dropped": [], **data}


def people(cfg: dict) -> dict[str, dict]:
    """Everyone with a folder or a single photo: {name: index (+ "single": the flat file)}."""
    r = root(cfg)
    out: dict[str, dict] = {}
    if not r.is_dir():
        return out
    for p in sorted(r.iterdir()):
        if p.is_dir() and not p.name.startswith("."):
            out[p.name] = load(p)
        elif p.suffix.lower() in PHOTO_EXT:
            out.setdefault(p.stem, {"aliases": [], "channels": [], "photos": {}, "dropped": []})["single"] = p.name
    return out


def kept(data: dict) -> list[dict]:
    return [ph for ph in data["photos"].values() if ph["status"] == "kept"]


def best_photo(d: Path, label: str = "") -> Path | None:
    """A person's kept photo for a cover: the label asked for if there is one, else neutral;
    a stream, Commons, channel or file photo before one from the web (image search or a
    pasted address: its licence is unknown), then the biggest face."""
    data = load(d)
    ks = [ph for ph in kept(data) if (d / ph["file"]).exists()]
    if not ks:
        return None
    want = label or "neutral"
    ks.sort(key=lambda ph: (ph.get("label") != want, ph.get("label") != "neutral",
                            ph.get("source") == "web", -ph.get("px", 0)))
    return d / ks[0]["file"]


def labels(d: Path) -> list[str]:
    """The labels a person's kept photos have, sorted."""
    return sorted({ph["label"] for ph in kept(load(d)) if ph.get("label") and (d / ph["file"]).exists()})


def figures(cfg: dict) -> list[tuple[str, list[str]]]:
    """(name, aliases) of everyone with a kept photo (or a single photo file); [] when
    [thumbnail] photos is unset or not there (a machine without the photos share)."""
    if not cfg["thumbnail"].get("photos"):
        return []
    try:
        everyone = people(cfg)
    except OSError:
        return []
    return [(n, d.get("aliases", [])) for n, d in everyone.items() if kept(d) or d.get("single")]


def known_figures(configured: str, cast: list[tuple[str, list[str]]]) -> str:
    """[scoring] known_figures with the cast library's people added after it ("Jer (The
    Quartering)"), leaving out anyone already named there by name or alias."""
    have = {w.strip().lower() for w in re.split(r"[,()]", configured or "") if w.strip()}
    extra = [f"{n} ({', '.join(a)})" if a else n for n, a in cast
             if not ({n.lower(), *(x.lower() for x in a)} & have)]
    return ", ".join(x for x in [(configured or "").strip(), *extra] if x)


def photo_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:10]


# ---------- faces ----------

def head_crop(size: tuple[int, int], box: list[float]) -> tuple[int, int, int, int]:
    """Head and shoulders around a face box [x, y, w, h] (pixels), about 4:5, kept inside the
    picture: room above the hair, shoulders below."""
    W, H = size
    x, y, w, h = box
    ch = 3.3 * h
    cw = max(0.8 * ch, 2.4 * w)  # 4:5, wider for a wide face
    cx, top = x + w / 2, y - 0.8 * h
    cw, ch = min(cw, W), min(ch, H)
    left = min(max(cx - cw / 2, 0), W - cw)
    top = min(max(top, 0), H - ch)
    return round(left), round(top), round(left + cw), round(top + ch)


def choose_face(found: list[tuple[dict, list[float]]], person: str, known: dict, match: float,
                other: float) -> tuple[dict | None, float | None, str]:
    """The face in a picture that is person's: (face, its match or None, why not)."""
    import numpy as np

    if not found:
        return None, None, "no face big enough"
    people = {n.lower(): np.array(r) for n, r in known.items()}
    mine = people.get(person.lower())
    if mine is not None:
        scored = [(float((mine @ e).max()), f, e) for f, e in found]
        score, f, e = max(scored, key=lambda sf: sf[0])
        if score < match:
            return None, round(score, 3), f"no face like {person}'s (best {score:.2f})"
        # Someone else enrolled who fits this face better (or well enough): a frame of Don
        # in a Jer grab used to become Jer at 0.68 although it was Don at 0.92.
        for name, refs in people.items():
            if name == person.lower():
                continue
            s = float((refs @ e).max())
            if s >= other or s > score:
                return None, round(score, 3), f"looks like {name} ({s:.2f}) more than {person} ({score:.2f})"
        return f, round(score, 3), ""
    f, e = max(found, key=lambda fe: fe[0]["px"])
    for name, refs in people.items():
        s = float((refs @ e).max())
        if s >= other:
            return None, None, f"looks like {name} ({s:.2f}), not {person}"
    return f, None, ""


def faces_in(img, cfg: dict) -> list[tuple[dict, list[float]]]:
    """Faces at least [cast] min_px tall in a PIL image, with embeddings. The detector is one per
    process, shared with the war room's "Who's this?" (faces.pick), so under its lock."""
    import numpy as np
    from PIL import ImageOps

    from . import faces

    min_px = cfg["cast"]["min_px"]
    W, H = img.size
    for pad in (0.0, 0.3):  # a face filling a close-up is missed until there's room around it
        p = round(pad * max(W, H))
        work = ImageOps.expand(img.convert("RGB"), p, fill=(128, 128, 128)) if p else img.convert("RGB")
        with faces._PICK:
            models = str(faces.ensure_models(cfg))
            if faces._ready != models:
                faces._init(models, 0)
                faces._ready = models
            found = faces._faces(np.asarray(work)[:, :, ::-1].copy())
        out = []
        for f, e in found:
            x, y, w, h = f["box"]
            PW, PH = work.size
            box = [(x * PW - p) / W, (y * PH - p) / H, w * PW / W, h * PH / H]
            if h * PH >= min_px:
                out.append(({**f, "box": [round(min(max(v, 0.0), 1.0), 4) for v in box]}, e))
        if out:
            return out
    return []


# ---------- adding one photo ----------

def fetch(url: str, cfg: dict) -> bytes:
    import requests

    for _ in range(6):  # redirects one at a time: each place is checked like the first
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme not in ("http", "https") or not host:
            raise ClipperError(f"not a web address: {url}")
        if blocked(host, cfg):
            raise ClipperError(f"{host} is a photo agency ([cast] blocked_domains)")
        try:
            ua = UA if host.endswith(("wikimedia.org", "wikipedia.org")) else cfg["kick"]["user_agent"]
            r = requests.get(url, timeout=25, stream=True, allow_redirects=False, headers={"User-Agent": ua})
        except requests.RequestException as e:
            raise ClipperError(f"{host}: {type(e).__name__}") from e
        if not r.is_redirect:
            break
        url = urljoin(url, r.headers.get("Location", ""))
        r.close()
    else:
        raise ClipperError(f"{host}: too many redirects")
    if not r.ok:
        raise ClipperError(f"{host}: HTTP {r.status_code}")
    data = r.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(data) > MAX_BYTES:
        raise ClipperError(f"{url}: over {MAX_BYTES // 1_000_000} MB")
    return data


def blocked(host: str, cfg: dict) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in cfg["cast"]["blocked_domains"])


def add(cfg: dict, person: str, data: bytes, source: dict, claude=None, say=print,
        box: list[float] | None = None) -> str | None:
    """One picture as a new photo of person, if a face in it is theirs: its id, or None (why is
    printed). source: {"key" (what makes it this photo), "kind", "url", "page", "licence",
    "author"}. box [x, y, w, h] (0-1): only the face there (a stream frame's named face)."""
    from PIL import Image, ImageOps

    from .faces import inside, load_known

    d = person_dir(cfg, person)
    pid = photo_id(source["key"])
    have = load(d)
    if pid in have["photos"] or source["key"] in have["dropped"]:
        return None
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:  # noqa: BLE001  # PIL raises many kinds on a bad file
        say(f"  {source.get('url') or source['key']}: not a picture ({type(e).__name__})")
        return None
    cc = cfg["cast"]
    found = faces_in(img, cfg)
    if box:  # the identity checks still apply to it
        found = [fe for fe in found if inside(fe[0]["box"], box) >= 0.5]
    face, score, why = choose_face(found, person, load_known(cfg)["people"], cc["match"], cfg["faces"]["match"])
    if not face:
        say(f"  {source.get('url') or source['key']}: {why}")
        return None
    emb = next(e for f, e in found if f is face)
    for other, ph in have["photos"].items():  # Commons keeps a photo and its "(cropped)" copy
        if ph.get("emb") and float(emb @ ph["emb"]) >= SAME_PHOTO:
            say(f"  {source.get('url') or source['key']}: the same photo as {other}")
            return None
    for old in have.get("dropped_embs") or []:  # a dropped photo back under another file name
        if float(emb @ old) >= SAME_PHOTO:
            say(f"  {source.get('url') or source['key']}: the same as a photo you dropped")
            return None
    W, H = img.size
    bx = [face["box"][0] * W, face["box"][1] * H, face["box"][2] * W, face["box"][3] * H]
    l, t, r, b = head_crop((W, H), bx)
    crop = img.crop((l, t, r, b))
    if max(crop.size) > cc["max_px"]:
        crop.thumbnail((cc["max_px"], cc["max_px"]), Image.LANCZOS)
    s = crop.width / (r - l)
    fb = {"x": round((bx[0] - l) * s / crop.width, 4), "y": round((bx[1] - t) * s / crop.height, 4),
          "width": round(bx[2] * s / crop.width, 4), "height": round(bx[3] * s / crop.height, 4)}
    d.mkdir(parents=True, exist_ok=True)
    crop.save(d / f"{pid}.jpg", quality=92)
    try:
        label = label_photo(cfg, d / f"{pid}.jpg", claude) if claude else {}
    except Exception as e:  # noqa: BLE001  # the photo is still worth keeping; label it by hand
        label = {"label": "", "flag": f"no label: {type(e).__name__}: {e}"[:200]}
    entry = {"file": f"{pid}.jpg", "status": "new", "label": label.get("label", ""),
             "flag": label.get("flag", ""), "source": source["kind"], "key": source["key"],
             "url": source.get("url", ""), "page": source.get("page", ""),
             "licence": source.get("licence", ""), "author": source.get("author", ""),
             "face": fb, "px": int(face["px"]), "match": score,
             "added": datetime.now(UTC).isoformat(timespec="seconds"),
             "emb": [round(float(v), 4) for v in emb]}
    single = any((d.parent / f"{d.name}{ext}").exists() for ext in PHOTO_EXT)  # a flat <Person>.jpg
    with jsonstate.update(d / INDEX, {}) as idx:
        if pid in idx.get("photos", {}) or source["key"] in idx.get("dropped", []):
            return None  # another find or grab added (or dropped) it meanwhile: leave it as it is
        if sure(cc, score, entry) and len(kept({"photos": idx.get("photos", {})})) + single < cc["target"]:
            entry["status"], entry["auto_kept"] = "kept", True
        idx.setdefault("photos", {})[pid] = entry
    say(f"  + {person} {pid} {entry['label'] or ''} ({source['kind']}, face {face['px']} px"
          f"{f', like them {score:.2f}' if score is not None else ''})"
          + (" kept" if entry["status"] == "kept" else ""))
    return pid


def sure(cc: dict, score: float | None, entry: dict) -> bool:
    """A photo to keep without asking: from [cast] auto_keep_sources (not the web: its licence is
    unknown), an enrolled person's face matched at [cast] auto_keep or better (0 = never), and
    labelled with no doubt."""
    return bool(cc["auto_keep"] and entry["source"] in cc["auto_keep_sources"] and score is not None
                and score >= cc["auto_keep"] and entry["label"] and not entry["flag"])


def count(data: dict | None) -> int:
    """Photos a person has or may soon have: kept, new, and a single file."""
    data = data or {"photos": {}}
    return sum(ph["status"] in ("kept", "new") for ph in data["photos"].values()) + bool(data.get("single"))


LABEL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["label", "usable", "why"],
    "properties": {"label": {"type": "string"}, "usable": {"type": "boolean"}, "why": {"type": "string"}},
}


def label_photo(cfg: dict, path: Path, claude) -> dict:
    """{"label": one of [cast] labels, "flag": why it may not suit a cover, or ""}."""
    from .prompts import Prompts

    cc = cfg["cast"]
    prompts = Prompts(cfg)
    prompts.version = cc["prompt_version"]
    schema = {**LABEL_SCHEMA, "properties": {**LABEL_SCHEMA["properties"],
                                            "label": {"type": "string", "enum": list(cc["labels"])}}}
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(path.read_bytes()).decode()}}]
    out, _ = claude.json_call(cc["label_model"], prompts.get("cast_label", labels=", ".join(cc["labels"])),
                              content, schema, cc["label_effort"])
    if not out:
        return {"label": "", "flag": "the model wouldn't label it"}
    return {"label": out["label"], "flag": "" if out["usable"] else out["why"][:200]}


def add_file(cfg: dict, person: str, src: str, claude=None, say=print) -> str | None:
    if re.match(r"https?://", src):
        return add(cfg, person, fetch(src, cfg), {"key": src, "kind": "web", "url": src}, claude, say)
    p = Path(src)
    if not p.is_file():
        raise ClipperError(f"no file {src}")
    data = p.read_bytes()  # keyed by content: two photo.jpg files are two photos, one file twice is one
    return add(cfg, person, data, {"key": f"file:{hashlib.sha1(data).hexdigest()}", "kind": "file",
                                   "page": p.name}, claude, say)


# ---------- from a stream ----------

def grab(cfg: dict, sdir: Path, person: str | None, n: int, claude=None, say=print,
         needed: bool = False) -> int:
    """Each named person's (or person's) n best faces in the stream's faces.json, the frame at
    full size from the source. needed (the worker's `cast` stage): only people with fewer than
    [cast] target photos and only up to it, and no faces.json or no [thumbnail] photos is not an
    error; for everyone (no person), what it added goes in the stream's cast_grab.json, the
    stage's done marker."""
    import tempfile

    from . import faces
    from .media import source_file

    data = jsonstate.read(sdir / "faces.json", None)
    marker = needed and not person
    if needed and not (data and cfg["thumbnail"]["photos"]):
        say("no faces.json or no [thumbnail] photos: nothing to grab")
        if marker:
            jsonstate.write(sdir / "cast_grab.json", {"at": now(), "added": {}})
        return 0
    if not data:
        raise ClipperError(f"{sdir.name}: no faces.json (clipper faces {sdir.name})")
    names = sorted({g["name"] for g in data["groups"].values() if g.get("name")})
    if person:
        names = [nm for nm in names if nm.lower() == person.lower()]
        if not names:
            raise ClipperError(f"{sdir.name}: nobody named {person} in faces.json")
    room = {}  # needed: how many more each person may get
    if needed:
        everyone = people(cfg)
        room = {nm: cfg["cast"]["target"] - count(everyone.get(nm)) for nm in names}
        names = [nm for nm in names if room[nm] > 0]
    if not names:  # before the source is looked for: an old stream whose source is gone is fine then
        return 0
    src, added, by = source_file(sdir), 0, {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            for f in faces.best_faces(data, name, n=n, gap=120.0):
                if needed and by.get(name, 0) >= room[name]:
                    break
                frame = faces.grab(src, f["t"], Path(tmp) / "frame.jpg")
                key = f"stream:{sdir.name}@{f['t']}"
                if add(cfg, name, frame.read_bytes(), {"key": key, "kind": "stream", "page": key},
                       claude, say, box=f["box"]):
                    added += 1
                    by[name] = by.get(name, 0) + 1
    if marker:
        jsonstate.write(sdir / "cast_grab.json", {"at": now(), "added": by})
    return added


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def auto(cfg: dict, claude=None, say=print) -> int:
    """`clipper cast auto`: a find from every source for each person (a cast folder or an
    enrolled face) with fewer than [cast] target photos. Someone whose last automatic find
    added nothing is tried again only after [cast] retry_days."""
    from .faces import load_known

    cc, added = cfg["cast"], 0
    everyone = people(cfg)
    for name in sorted(set(everyone) | set(load_known(cfg)["people"]), key=str.lower):
        data = everyone.get(name)
        if count(data) >= cc["target"]:
            continue
        last = (data or {}).get("auto") or {}
        if last.get("at") and not last.get("added") and \
                datetime.now(UTC) - datetime.fromisoformat(last["at"]) < timedelta(days=cc["retry_days"]):
            continue
        say(f"{name} ({count(data)} of {cc['target']}):")
        n = find(cfg, name, list(SOURCES), claude, say, limit=cc["target"] - count(data))
        d = person_dir(cfg, name)
        d.mkdir(parents=True, exist_ok=True)
        with jsonstate.update(d / INDEX, {}) as idx:
            idx["auto"] = {"at": now(), "added": n}
        added += n
    return added


# ---------- from the web ----------

def find(cfg: dict, person: str, sources: list[str], claude=None, say=print,
         limit: int | None = None) -> int:
    """New photos of person from these sources, at most [cast] per_source from each and, with
    limit (`cast auto`: what the person still needs), at most limit in all."""
    d = person_dir(cfg, person)
    data = load(d)
    if not d.exists():
        d.mkdir(parents=True)
    per = cfg["cast"]["per_source"]
    added = 0
    for kind in sources:
        if limit is not None and added >= limit:
            break
        if kind == "search" and not search_key(cfg):
            say("image search: no $BRAVE_API_KEY or [paths] brave_key file, skipped")
            continue
        try:
            found = {"commons": commons, "channels": channel_pictures, "search": image_search}[kind](
                cfg, person, data, say)
        except ClipperError as e:
            say(f"{kind}: {e}")
            continue
        have = load(d)
        found = [s for s in found if photo_id(s["key"]) not in have["photos"] and s["key"] not in have["dropped"]]
        say(f"{kind}: {len(found)} to look at")
        n = 0
        for i, src in enumerate(found):
            if n >= per or (limit is not None and added + n >= limit):
                break
            if i and kind == "commons":
                time.sleep(1.0)  # Wikimedia answers 429 to quick runs of downloads
            try:
                n += bool(add(cfg, person, fetch(src["url"], cfg), src, claude, say))
            except ClipperError as e:
                say(f"  {src['url']}: {e}")
        added += n
    return added


def commons(cfg: dict, person: str, data: dict, say=print) -> list[dict]:
    """Wikimedia Commons: the person's Wikidata picture first, then a file search. Freely
    licensed; licence and author kept."""
    import requests

    api = "https://commons.wikimedia.org/w/api.php"
    titles: list[str] = []
    try:
        r = requests.get("https://www.wikidata.org/w/api.php", timeout=20, headers={"User-Agent": UA},
                         params={"action": "wbsearchentities", "search": person, "language": "en",
                                 "type": "item", "limit": 1, "format": "json"})
        hit = (r.json().get("search") or [None])[0] if r.ok else None
        if hit:
            r = requests.get("https://www.wikidata.org/w/api.php", timeout=20, headers={"User-Agent": UA},
                             params={"action": "wbgetclaims", "entity": hit["id"], "property": "P18",
                                     "format": "json"})
            for c in (r.json().get("claims", {}).get("P18") or []) if r.ok else []:
                titles.append("File:" + c["mainsnak"]["datavalue"]["value"])
        params = {"action": "query", "format": "json", "prop": "imageinfo",
                  "iiprop": "url|extmetadata|mime", "iiurlwidth": cfg["cast"]["max_px"]}
        out = []
        if titles:
            r = requests.get(api, timeout=20, headers={"User-Agent": UA}, params={**params, "titles": "|".join(titles)})
            out += _commons_pages(r.json() if r.ok else {})
        r = requests.get(api, timeout=20, headers={"User-Agent": UA}, params={
            **params, "generator": "search", "gsrnamespace": 6, "gsrlimit": 20,
            "gsrsearch": f'"{person}" filetype:bitmap'})
        out += _commons_pages(r.json() if r.ok else {})
    except (requests.RequestException, ValueError, KeyError) as e:
        raise ClipperError(f"Wikimedia: {type(e).__name__}") from e
    seen, uniq = set(), []
    for o in out:
        if o["key"] not in seen:
            seen.add(o["key"])
            uniq.append(o)
    return uniq


def _commons_pages(js: dict) -> list[dict]:
    out = []
    for pg in (js.get("query") or {}).get("pages", {}).values():
        ii = (pg.get("imageinfo") or [None])[0]
        if not ii or not str(ii.get("mime", "")).startswith("image/"):
            continue
        meta = ii.get("extmetadata") or {}
        author = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()
        out.append({"key": ii.get("descriptionurl") or ii["url"], "kind": "commons",
                    "url": ii.get("thumburl") or ii["url"], "page": ii.get("descriptionurl", ""),
                    "licence": (meta.get("LicenseShortName") or {}).get("value", ""), "author": author[:120]})
    return out


def channel_pictures(cfg: dict, person: str, data: dict, say=print) -> list[dict]:
    """The person's own channels (cast.json "channels"): the avatar, then recent video
    thumbnails (YouTube), or the profile picture (Kick). A channel that fails is skipped."""
    import requests

    if not data["channels"]:
        raise ClipperError(f'no channels for {person}: clipper cast set "{person}" --channel URL')
    out = []
    for ch in data["channels"]:
        try:
            out += _channel(cfg, ch, say)
        except (requests.RequestException, ValueError, AttributeError) as e:
            say(f"  {ch}: {type(e).__name__}")
    return out


def _channel(cfg: dict, ch: str, say) -> list[dict]:
    import json

    import requests

    from . import ytdl

    host = urlparse(ch).hostname or ""
    if host.endswith("kick.com"):
        slug = urlparse(ch).path.strip("/").split("/")[0]
        r = requests.get(f"https://kick.com/api/v2/channels/{slug}", timeout=15,
                         headers={"User-Agent": cfg["kick"]["user_agent"]})
        pic = ((r.json() or {}).get("user") or {}).get("profile_pic") if r.ok else None
        return [{"key": pic, "kind": "channel", "url": pic, "page": ch}] if pic else []
    if not host.endswith(("youtube.com", "youtu.be")):
        say(f"  {ch}: only YouTube and Kick channels so far")
        return []
    page = ch.rstrip("/")
    r = ytdl.run("--flat-playlist", "-J", "--playlist-end", str(cfg["cast"]["videos"]),
                 page if page.endswith("/videos") else page + "/videos")
    if r.returncode:
        say(f"  {ch}: {ytdl.last_error(r)}")
        return []
    info = json.loads(r.stdout)
    out = [{"key": t["url"], "kind": "channel", "url": t["url"], "page": ch}
           for t in info.get("thumbnails") or [] if t.get("id") == "avatar_uncropped"][:1]
    for e in info.get("entries") or []:
        if e.get("id"):
            url = f"https://i.ytimg.com/vi/{e['id']}/maxresdefault.jpg"
            out.append({"key": url, "kind": "channel", "url": url, "page": f"https://www.youtube.com/watch?v={e['id']}"})
    return out


def search_key(cfg: dict | None = None) -> str:
    """$BRAVE_API_KEY, else the file [paths] brave_key (secrets/brave_secret.txt); "" if neither."""
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    path = (cfg or {}).get("paths", {}).get("brave_key")
    if key or not path:
        return key
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def image_search(cfg: dict, person: str, data: dict, say=print) -> list[dict]:
    """Brave image search for the person's name (and their first alias), photo agencies
    skipped. Licence unknown: covers prefer stream and Commons photos."""
    import requests

    cc = cfg["cast"]
    q = person + (f" ({data['aliases'][0]})" if data["aliases"] else "")
    try:
        r = requests.get("https://api.search.brave.com/res/v1/images/search", timeout=20,
                         params={"q": q, "count": cc["search_count"], "safesearch": "strict"},
                         headers={"Accept": "application/json", "X-Subscription-Token": search_key(cfg)})
    except requests.RequestException as e:
        raise ClipperError(f"Brave: {type(e).__name__}") from e
    if not r.ok:
        raise ClipperError(f"Brave: HTTP {r.status_code}")
    try:
        results = r.json().get("results") or []
    except ValueError as e:
        raise ClipperError("Brave: not JSON") from e
    out = []
    for res in results:
        url = (res.get("properties") or {}).get("url") or ""
        page = res.get("url") or ""
        if not url or blocked(urlparse(url).hostname or "", cfg) or blocked(urlparse(page).hostname or "", cfg):
            continue
        out.append({"key": url, "kind": "web", "url": url, "page": page})
    return out


# ---------- keeping ----------

def set_status(cfg: dict, person: str, ids: list[str], status: str) -> None:
    """keep: status kept; drop: the file deleted and its source remembered."""
    d = person_dir(cfg, person)
    ids = list(dict.fromkeys(ids))
    with jsonstate.update(d / INDEX, {}) as idx:
        photos = idx.setdefault("photos", {})
        missing = [pid for pid in ids if pid not in photos]
        if missing:  # before any file is deleted: a raise here leaves cast.json as it was
            raise ClipperError(f"{person}: no photo {', '.join(missing)}")
        for pid in ids:
            if status == "drop":
                ph = photos.pop(pid)
                idx.setdefault("dropped", []).append(ph["key"])
                if ph.get("emb"):  # so the same picture isn't "new" again under another name
                    idx.setdefault("dropped_embs", []).append(ph["emb"])
                (d / ph["file"]).unlink(missing_ok=True)
            else:
                photos[pid]["status"] = "kept"


def set_label(cfg: dict, person: str, pid: str, label: str) -> None:
    if label not in cfg["cast"]["labels"]:
        raise ClipperError(f"label {label!r}: one of {', '.join(cfg['cast']['labels'])}")
    d = person_dir(cfg, person)
    with jsonstate.update(d / INDEX, {}) as idx:
        if pid not in idx.get("photos", {}):
            raise ClipperError(f"{person}: no photo {pid}")
        idx["photos"][pid].update(label=label, flag="")


def set_info(cfg: dict, person: str, aliases: list[str] | None, channels: list[str] | None) -> None:
    for ch in channels or []:
        if not re.match(r"https://(www\.)?(youtube\.com|kick\.com)/", ch):
            raise ClipperError(f"{ch}: a https://www.youtube.com/@name or https://kick.com/name address")
    d = person_dir(cfg, person)
    d.mkdir(parents=True, exist_ok=True)
    with jsonstate.update(d / INDEX, {}) as idx:
        if aliases is not None:
            idx["aliases"] = [a.strip() for a in aliases if a.strip()]
        if channels is not None:
            idx["channels"] = channels


def show_list(cfg: dict) -> None:
    from .faces import load_known

    known = load_known(cfg)["people"]
    everyone = people(cfg)
    for name in sorted(set(everyone) | set(known), key=str.lower):
        data = everyone.get(name, {"photos": {}, "aliases": []})
        k = len(kept(data)) + bool(data.get("single"))
        new = sum(ph["status"] == "new" for ph in data["photos"].values())
        mark = "" if k >= cfg["cast"]["min_photos"] else "  (fewer than " + str(cfg["cast"]["min_photos"]) + ")"
        aka = f" aka {', '.join(data['aliases'])}" if data.get("aliases") else ""
        print(f"{name}{aka}: {k} kept, {new} new{'' if name in known else ', face not enrolled'}{mark}")


def overview(cfg: dict, person: str = "") -> dict:
    """The war room's Cast window: everyone (enrolled faces too) with counts, and one person's
    photos, new ones first (flagged ones after the rest)."""
    from .faces import load_known

    cc = cfg["cast"]
    known = load_known(cfg)["people"]
    everyone = people(cfg)
    out = {"labels": cc["labels"], "min_photos": cc["min_photos"], "search": bool(search_key(cfg)), "people": []}
    for name in sorted(set(everyone) | set(known), key=str.lower):
        data = everyone.get(name, {"photos": {}, "aliases": []})
        out["people"].append({"name": name, "kept": len(kept(data)) + bool(data.get("single")),
                              "new": sum(ph["status"] == "new" for ph in data["photos"].values()),
                              "enrolled": name in known, "aliases": data.get("aliases", [])})
    if person:
        d = person_dir(cfg, person)
        data = load(d)
        photos = [{"id": pid, **{k: v for k, v in ph.items() if k != "emb"}} for pid, ph in data["photos"].items()]
        photos.sort(key=lambda ph: (ph["status"] != "new", bool(ph.get("flag")), ph.get("added", "")))
        out["person"] = {"name": d.name, "aliases": data["aliases"], "channels": data["channels"],
                         "enrolled": d.name in known, "photos": photos,
                         "single": everyone.get(d.name, {}).get("single", "")}
    return out
