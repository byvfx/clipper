"""The war room's cover editor (#18): one cover's fields, checked changes, a preview render,
and a save that re-renders without model calls."""

from __future__ import annotations

from pathlib import Path

from .. import jsonstate
from ..config import for_stream
from ..errors import ClipperError
from ..meta import load_assets
from .classic import compose
from .enquirer import compose_enquirer, cover_context, enquirer_setup
from .frames import asset_key, grab, photo_labels, photo_name, photos
from .pick import enquirer_lines, unsafe
from .stage import check_text, printable, run

COVER_TEXT = ("attribution", "subhead", "band_kicker", "band_headline", "teaser")
PHOTO_KEYS = ("photo", "photo_label", "teaser_photo", "teaser_photo_label")  # dropped when ""


def cover_info(stream_dir: Path, cfg: dict, key: str) -> dict:
    """What the editor shows for one cover: its fields, candidate frames and the photos."""
    cfg = for_stream(cfg, stream_dir)
    state = jsonstate.read(stream_dir / "thumbnails.json", {}) or {}
    if key not in state:
        raise ClipperError("no cover yet for this item (a Short's cover follows its clip: edit the clip's)")
    e = dict(state[key])
    if e.get("style") == "enquirer" and e.get("headline") and e["text"] != " ".join(e["headline"]):
        e["headline"] = enquirer_lines(e["text"])  # a hand edit of the words: what the cover prints
    tc = {**cfg["thumbnail"]}
    tc["_photos"] = photos(tc)
    return {"key": key, "style": e.get("style", "classic"), "id": e["id"],
            "frames": [{"n": i, "t": f["t"], "path": f"thumbs/{key}/{f['path']}"}
                       for i, f in enumerate(e["frames"], 1)],
            "fields": {k: e.get(k) for k in ("frame", "face", "inset_frame", "inset_face", *PHOTO_KEYS,
                                              "headline", "text", *COVER_TEXT)},
            "photos": sorted(tc["_photos"]), "photo_labels": photo_labels(tc), "max_words": tc["max_words"]}


def _clean_changes(cfg: dict, e: dict, changes: dict) -> dict:  # noqa: C901
    """The editor's changes, checked: frame numbers in range, face boxes inside the frame,
    photos on file, text without words the channel never prints."""
    from ..bleep import Bleeper

    tc = {**cfg["thumbnail"]}
    tc["_photos"] = photos(tc)
    labels = photo_labels(tc)
    bleeper = Bleeper({**cfg["bleep"], "mode": "bleep"})
    out: dict = {}
    n = len(e["frames"])
    for k in ("frame", "inset_frame"):
        if k in changes and changes[k] is not None:
            v = int(changes[k])
            if not 1 <= v <= n:
                raise ClipperError(f"{k} must be 1-{n}")
            out[k] = v
    for k in ("face", "inset_face"):
        if changes.get(k):
            b = {d: round(float(changes[k][d]), 4) for d in ("x", "y", "width", "height")}
            if not (0 <= b["x"] < 1 and 0 <= b["y"] < 1 and 0.03 <= b["width"] <= 1 and 0.03 <= b["height"] <= 1):
                raise ClipperError(f"{k} box is outside the frame")
            out[k] = b
    for k in ("photo", "teaser_photo"):
        if k in changes:
            name = (changes[k] or "").strip()
            if name and not photo_name(tc, name):
                raise ClipperError(f"no photo of {name!r} in [thumbnail] photos")
            out[k] = photo_name(tc, name) if name else ""
            out[f"{k}_label"] = ""  # a new person: their best photo unless a label comes with it
        if f"{k}_label" in changes:
            who, label = out.get(k, e.get(k, "")), (changes[f"{k}_label"] or "").strip()
            if label and label not in labels.get(who, []):
                raise ClipperError(f"no {label!r} photo of {who or 'nobody'} in the cast library")
            out[f"{k}_label"] = label if who else ""
    if "headline" in changes:
        lines = [" ".join(str(x).split()).upper() for x in changes["headline"] if str(x).strip()][:3]
        if not lines:
            raise ClipperError("the headline is empty")
        out["headline"], out["text"] = lines, " ".join(lines)
    elif "text" in changes:
        out["text"] = check_text(cfg, changes["text"])
    for k in COVER_TEXT:
        if k in changes:
            out[k] = " ".join(str(changes[k] or "").split())
    for k in ("text", "attribution", "subhead", "band_kicker", "band_headline", "teaser"):
        if out.get(k) and unsafe(out[k], bleeper):
            raise ClipperError(f"{k} {bleeper.mask(out[k])!r} has a word the channel never prints")
    return out


def edit_cover(stream_dir: Path, cfg: dict, key: str, changes: dict, preview: bool = False) -> Path:
    """Apply the editor's changes to one cover. preview: render to thumbs/<key>/_preview.jpg and
    keep thumbnails.json as it is. Otherwise save them and re-render without model calls
    (the whole stream when the teaser or its photo changed: it shows on the other covers),
    which re-attaches in Kitsu. Returns the rendered .jpg."""
    cfg = for_stream(cfg, stream_dir)
    state_path = stream_dir / "thumbnails.json"
    state = jsonstate.read(state_path, {}) or {}
    if key not in state:
        raise ClipperError("no cover yet for this item")
    e = {**state[key], **_clean_changes(cfg, state[key], changes)}
    for k in PHOTO_KEYS:
        if e.get(k) == "":
            e.pop(k)
    if "frame" in changes:
        e["t"] = e["frames"][e["frame"] - 1]["t"]
    x = next((a for a in load_assets(stream_dir) if asset_key(a) == key), None)
    if x is None:
        raise ClipperError(f"{key}: the cut video is gone")
    if preview:
        from ..bleep import Bleeper

        tc = {**cfg["thumbnail"]}
        tc["_photos"] = photos(tc)
        tc_enq, date, own_band = enquirer_setup(stream_dir, cfg, tc)
        e = printable(e, Bleeper({**cfg["bleep"], "mode": "bleep"}))
        frame = stream_dir / "thumbs" / key / e["frames"][e["frame"] - 1]["path"]
        if not frame.exists():
            grab(x["video"], e["t"], frame)
        dst = stream_dir / "thumbs" / key / "_preview.jpg"
        if e.get("style") == "enquirer":
            compose_enquirer(stream_dir, key, e, cover_context(key, {**state, key: e}, stream_dir, tc, own_band),
                             tc_enq, date, x["video"], dst)
        else:
            compose(frame, e, tc, dst)
        return dst
    with jsonstate.update(state_path, {}) as fresh:
        fresh[key] = {**fresh.get(key, state[key]), **{k: v for k, v in e.items()}}
        for k in PHOTO_KEYS:
            if k not in e:
                fresh[key].pop(k, None)
    wide = any(k in changes for k in ("teaser", "teaser_photo", "teaser_photo_label"))
    run(stream_dir, cfg, ids=None if wide else [state[key]["id"]], compose_only=True)
    return x["video"].with_suffix(".jpg")
