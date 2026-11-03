"""Enquirer covers ([thumbnail] style = "enquirer"): a 1990s supermarket-tabloid cover in
16:9, the show's masthead and a green teaser band across the top, three teaser boxes down
the left plugging the episode's other videos, a big face in the middle, the yellow
headline on the right, a reaction inset and the channel badge on the photo. Shorts get
the same cover at 9:16 (compose_vertical).

Every position, size, color and fixed word of both is LAYOUT below. A show changes any of
it in <paths.layouts>/<show>/enquirer.toml (the same folder as its Shorts reference
screenshots; "default" without a show), with the same tables, e.g.

    [colors]
    green = "#0b3d91"          # the band
    [cover.inset]
    size = [200, 180]
    [[cover.teasers.boxes]]    # a list replaces the whole list: give every box
    ...

then `clipper thumbs <stream> --compose-only` redraws the covers with no model calls."""

from __future__ import annotations

import re
import tomllib
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

from .. import jsonstate
from ..draw import (
    color,
    cond_font,
    face_crop,
    fit_font,
    head_font,
    italic,
    line_breaks,
    save,
    wrap_px,
)
from ..errors import ClipperError
from .frames import grab, photo_file
from .pick import clean_text, enquirer_lines

# Boxes are [x0, y0, x1, y1] unless named otherwise; "room" is what a text's width leaves
# of its box; colors are names from [colors] or "#rrggbb".
LAYOUT = {
    "colors": {"yellow": "#ffe200", "red": "#d61220", "black": "#080808", "white": "#ffffff",
               "green": "#10543e", "pink": "#f6c4d6", "cream": "#faf4de"},
    "copy": {"band_kicker": "Exclusive:",  # the band when the episode has no other story
             "no_topic": "EXCLUSIVE"},     # a topic-labelled teaser box whose video has no topic
    "cover": {  # 16:9, for the videos
        "size": [1280, 720], "noise": 20, "grain": 0.04,
        # White box, the show's name in red italics filling it; tagline and date/price at
        # [dx, dy] from its top corners; the name shrinks from name_size to fit.
        "masthead": {"box": [8, 8, 560, 118], "border": 3, "small": 15, "tagline": [12, 8],
                     "date": [8, 8], "name_size": 96, "name_min": 40, "name_room": 18,
                     "name_at": [6, 6]},
        # Green band: another story from the episode, kicker over headline, or the headline
        # alone, centred, when there is no kicker.
        "band": {"box": [568, 8, 1272, 118], "border": 3, "pad": 16, "stroke": 2,
                 "kicker_y": 6, "kicker_size": 28, "kicker_room": 34,
                 "headline_y": 36, "headline_size": 64, "headline_room": 32, "single_size": 84},
        # Side teasers down the left ([thumbnail] teasers = false: none). A box's label is
        # fixed, or its video's topic (topic_label); the photo takes `photo` of its width.
        "teasers": {"x": 8, "y": 128, "width": 300, "gap": 6, "border": 3, "label_height": 30,
                    "label_size": 22, "photo": 0.44, "text_size": 42, "text_min": 14,
                    "topic_chars": 22,
                    "boxes": [{"height": 178, "fill": "yellow", "ink": "red"},
                              {"height": 196, "fill": "pink", "ink": "black",
                               "label": "CAN YOU BELIEVE IT?", "label_fill": "yellow",
                               "label_ink": "black"},
                              {"height": 196, "fill": "cream", "ink": "red", "topic_label": True,
                               "label_fill": "red", "label_ink": "white"}]},
        # The main face, [x, y, width, height]; full: when there are no teasers.
        "photo": {"box": [314, 126, 470, 594], "full": [8, 126, 776, 594], "zoom": 2.0},
        # Reaction inset, [right, bottom] px in from the photo's corner (at least min_top
        # below its top), in a white frame pad px wide.
        "inset": {"size": [176, 158], "right": 14, "bottom": 14, "min_top": 0, "pad": 7,
                  "border": 2, "fill": 0.72},
        # Channel badge, left px into the photo, bottom px above its bottom (on_inset: above
        # the inset's bottom); as wide as the channel name plus pad.
        "badge": {"size": 30, "kicker_size": 13, "pad": 24, "left": 12, "height": 50,
                  "bottom": 12, "on_inset": False, "border": 2, "kicker_y": 4, "channel_y": 4},
        # Headline column from x to right px short of the edge, fitted to a budget of px
        # of height; then the attribution (right-aligned) and the subhead to bottom.
        "headline": {"x": 796, "right": 14, "y": 128, "size": 150, "budget": 380,
                     "attribution_size": 24, "attribution_right": 16, "attribution_y": 8,
                     "attribution_step": 40, "subhead_size": 44, "subhead_step": 44, "bottom": 12},
    },
    # 9:16, for the Shorts: masthead and band on top, the headline stacked big under them,
    # then the clip's face to the bottom with the inset and badge kept clear of the bottom,
    # where YouTube's mobile shelf lays the title over the cover. No side teasers.
    "vertical": {
        "size": [1080, 1920], "noise": 20, "grain": 0.04,
        "masthead": {"box": [16, 16, 1064, 236], "border": 4, "small": 24, "tagline": [16, 12],
                     "date": [16, 44], "name_size": 170, "name_min": 60, "name_room": 20,
                     "name_at": [6, 10]},
        "band": {"box": [16, 248, 1064, 388], "border": 4, "pad": 18, "stroke": 2,
                 "kicker_y": 8, "kicker_size": 40, "kicker_room": 36,
                 "headline_y": 54, "headline_size": 74, "headline_room": 36, "single_size": 96},
        # Up to `lines` lines, each as wide as the cover allows (at most size px), the
        # block within budget px; gap px below its ink before the attribution.
        "headline": {"x": 24, "right": 24, "y": 404, "size": 330, "lines": 4, "budget": 470,
                     "gap": 12, "attribution_size": 40, "attribution_right": 24, "attribution_y": 0,
                     "attribution_step": 52, "subhead_size": 48, "subhead_step": 52,
                     "subhead_lines": 2},
        # The face from gap px under the text to bottom px above the cover's bottom.
        "photo": {"x": 16, "right": 16, "gap": 16, "bottom": 16, "zoom": 2.5},
        "inset": {"size": [330, 296], "right": 22, "bottom": 380, "min_top": 40, "pad": 9,
                  "border": 3, "fill": 0.72},
        "badge": {"size": 52, "kicker_size": 24, "pad": 40, "left": 22, "height": 104,
                  "bottom": 0, "on_inset": True, "border": 3, "kicker_y": 8, "channel_y": 8},
    },
}
WHOLE = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}  # a photo is all face


# ---------- the layout ----------

def merge_layout(base: dict, over: dict, where: str = "") -> dict:
    """over's values on base's, table by table (a list replaces the whole list). A key base
    doesn't have is an error: a typo would otherwise change nothing, silently."""
    out = dict(base)
    for k, v in over.items():
        name = f"{where}.{k}" if where else k
        if k not in base:
            raise ClipperError(f"unknown key {name} (known: {', '.join(base)})")
        if isinstance(base[k], dict) and not isinstance(v, dict):
            raise ClipperError(f"{name} is a table")
        out[k] = merge_layout(base[k], v, name) if isinstance(base[k], dict) else v
    return out


def layout_path(cfg: dict) -> Path:
    """<paths.layouts>/<show>/enquirer.toml, beside the show's Shorts layout screenshots."""
    return cfg["paths"]["layouts"] / ((cfg.get("show") or {}).get("key") or "default") / "enquirer.toml"


def layout(cfg: dict) -> dict:
    """LAYOUT with this show's enquirer.toml over it, if it has one."""
    path = layout_path(cfg)
    if not path.exists():
        return LAYOUT
    try:
        return merge_layout(LAYOUT, tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, ClipperError) as e:
        raise ClipperError(f"{path}: {e}") from None


def palette(L: dict):
    """A color lookup: a [colors] name or "#rrggbb" -> (r, g, b)."""
    named = {k: color(v) for k, v in L["colors"].items()}
    return lambda c: color(c) if c.startswith("#") else named[c]


# ---------- what the cover plugs ----------

def cover_context(key: str, state: dict, stream_dir: Path, tc: dict, own_band: str = "") -> dict:
    """The band story and three side teasers for one cover, from the episode's other
    videos: different topics first, compilations and segments before clips."""
    me = state[key]
    rank = {"compilation": 0, "segment": 1, "clip": 2}
    others = [(k, e) for k, e in state.items()
              if k != key and e.get("style") == "enquirer" and e.get("teaser")]
    others.sort(key=lambda ke: (rank.get(ke[1]["kind"], 3), -ke[1].get("duration_s", 0), ke[0]))
    # Rotate per cover (stable for a given video) so covers plug different stories;
    # other topics still come before this cover's own.
    diff = [ke for ke in others if ke[1].get("topic") != me.get("topic")]
    same = [ke for ke in others if ke[1].get("topic") == me.get("topic")]
    if diff:
        r = zlib.crc32(key.encode()) % len(diff)
        diff = diff[r:] + diff[:r]
    others = diff + same
    band = others[0][1] if others else None
    picks, topics = [], {me.get("topic"), band.get("topic") if band else None}
    for k, e in others[1:]:  # distinct topics first, then anything left
        if e.get("topic") not in topics:
            picks.append((k, e))
            topics.add(e.get("topic"))
    picks += [ke for ke in others[1:] if ke not in picks]
    side = []
    for k, e in picks[:3]:
        photo = photo_file(tc, e.get("teaser_photo", ""), e.get("teaser_photo_label", ""))
        path = photo or stream_dir / "thumbs" / k / e["frames"][e["frame"] - 1]["path"]
        side.append({"text": e["teaser"], "topic": e.get("topic") or "", "frame": path,
                     "face": WHOLE if photo else e["face"]})
    if not others:
        return own_context(key, me, stream_dir, own_band)
    return {"band": band, "side": side}


FILLER = {"the", "and", "for", "but", "was", "are", "his", "her", "its", "with", "from", "that",
          "this", "just", "into", "out", "over", "plus", "all", "you", "not", "has", "had"}


def own_context(key: str, me: dict, stream_dir: Path, own_band: str = "") -> dict:
    """A cover with no other videos in its stream (a pre-show): its own band line (or
    own_band, e.g. "Pre-show 10/19!"), and text-only side teasers from its own teaser
    and chapter titles."""
    band = ({"band_kicker": "", "band_headline": own_band} if own_band
            else {"band_kicker": me["band_kicker"], "band_headline": me["band_headline"]}
            if me.get("band_headline") else None)
    texts = [me["teaser"]] if me.get("teaser") else []
    draft = stream_dir / "metadata" / f"{key}.json"
    if draft.exists():
        chapters = jsonstate.load(draft).get("chapters") or []
        texts += [clean_text(c["title"], 12) for c in chapters]

    def words(t: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2 and w not in FILLER}

    # Skip stories the headline or band already tell, and titles too long for a box.
    told = [words(me.get("text") or " ".join(me.get("headline") or [])), words(me.get("subhead") or "")]
    if band:
        told.append(words(band["band_headline"]))
    side = []
    for t in texts:
        if t and len(t.split()) <= 10 and all(len(words(t) & w) < 2 for w in told):
            side.append({"text": t, "topic": "", "frame": None, "face": None})
            told.append(words(t))
    return {"band": band, "side": side[:3]}


def enquirer_setup(stream_dir: Path, cfg: dict, tc: dict) -> tuple[dict, str, str]:
    """(tc with the masthead and badge names and the layout, the cover date, the pre-show
    band or "")."""
    meta_path = stream_dir / "meta.json"
    smeta = jsonstate.read(meta_path, {})
    started = smeta.get("started_at", "")
    own_band = ""
    if smeta.get("kind") == "preshow" and cfg["preshow"].get("teasers") is not None:
        tc = {**tc, "teasers": cfg["preshow"]["teasers"]}  # e.g. one story on every pre-show cover
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(started)
        date = f"{dt:%b} {dt.day}, {dt.year}".upper()
        if smeta.get("kind") == "preshow":  # the green band names the pre-show and its air date
            local = dt.astimezone(ZoneInfo(cfg["schedule"]["timezone"]))
            own_band = cfg["preshow"]["band"].format(date=f"{local.month}/{local.day}")
    except ValueError:
        date = ""
    tc_enq = {**tc, "_masthead": (tc["masthead"] or cfg["meta"]["show_name"] or "").upper(),
              "_channel": (tc["badge_channel"] or cfg["scoring"]["channel_name"] or "").upper(),
              "_layout": layout(cfg)}
    return tc_enq, date, own_band


# ---------- drawing ----------

def frame_file(stream_dir: Path, key: str, e: dict, n: int, video: Path) -> Path:
    """Candidate frame n of a cover, grabbed again if it was cleaned up."""
    p = stream_dir / "thumbs" / key / e["frames"][n - 1]["path"]
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        grab(video, e["frames"][n - 1]["t"], p)
    return p


def masthead(img: Image.Image, d: ImageDraw.ImageDraw, tc: dict, m: dict, date: str, ink) -> None:
    x0, y0, x1, y1 = m["box"]
    d.rectangle((x0, y0, x1, y1), fill=ink("white"), outline=ink("black"), width=m["border"])
    small = cond_font(tc, m["small"])
    d.text((x0 + m["tagline"][0], y0 + m["tagline"][1]), tc["masthead_tagline"], font=small, fill=ink("red"))
    d.text((x1 - m["date"][0], y0 + m["date"][1]), f"{date}  ·  {tc['masthead_price']}".strip(" ·"),
           font=small, fill=ink("black"), anchor="ra")
    size = m["name_size"]
    while True:
        name = italic(tc["_masthead"], head_font(tc, size), ink("red"), ink("black"))
        if name.width <= x1 - x0 - m["name_room"] or size <= m["name_min"]:
            break
        size -= 2
    img.paste(name, (x0 + m["name_at"][0], y1 - name.height + m["name_at"][1]), name)


def band_strip(d: ImageDraw.ImageDraw, tc: dict, b: dict, band: dict, ink) -> None:
    x0, y0, x1, y1 = b["box"]
    d.rectangle((x0, y0, x1, y1), fill=ink("green"), outline=ink("black"), width=b["border"])
    x = x0 + b["pad"]
    if band["band_kicker"]:
        d.text((x, y0 + b["kicker_y"]), band["band_kicker"],
               font=fit_font(lambda s: cond_font(tc, s), band["band_kicker"], x1 - x0 - b["kicker_room"],
                             b["kicker_size"]), fill=ink("yellow"))
        bf = fit_font(lambda s: head_font(tc, s), band["band_headline"], x1 - x0 - b["headline_room"],
                      b["headline_size"])
        d.text((x, y0 + b["headline_y"]), band["band_headline"], font=bf, fill=ink("white"),
               stroke_width=b["stroke"], stroke_fill=ink("black"))
    else:  # one line, centred in the band
        bf = fit_font(lambda s: head_font(tc, s), band["band_headline"], x1 - x0 - b["headline_room"],
                      b["single_size"])
        d.text((x, (y0 + y1) // 2), band["band_headline"], font=bf, fill=ink("white"),
               stroke_width=b["stroke"], stroke_fill=ink("black"), anchor="lm")


def teaser_box(img: Image.Image, tc: dict, t: dict, box: tuple[int, int, int, int], bg, ink, label,
               label_bg, label_ink, text: str, photo: Image.Image | None, outline) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    d.rectangle((x, y, x + w, y + h), fill=bg, outline=outline, width=t["border"])
    top = y
    if label:
        lh = t["label_height"]
        d.rectangle((x, y, x + w, y + lh), fill=label_bg)
        lf = fit_font(lambda s: cond_font(tc, s), label, w - 12, t["label_size"])
        d.text((x + w / 2, y + lh // 2), label, font=lf, fill=label_ink, anchor="mm")
        top += lh
    pw = int(w * t["photo"]) if photo is not None else 0
    if photo is not None:
        img.paste(photo, (x + w - pw - 6, top + 6))
    tw, th = w - pw - 18, y + h - top - 12
    size = t["text_size"]
    while size > t["text_min"]:
        f = cond_font(tc, size)
        lines = wrap_px(text, f, tw)
        if len(lines) * size * 0.98 <= th and all(f.getlength(l) <= tw for l in lines):
            break
        size -= 1
    ty = top + 6
    for line in lines:
        d.text((x + 8, ty), line, font=f, fill=ink)
        ty += int(size * 0.98)


def inset_box(i: dict, px: int, py: int, pw: int, ph: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the reaction inset on a photo at (px, py, pw, ph)."""
    iw, ih = i["size"]
    return px + pw - iw - i["right"], max(py + i["min_top"], py + ph - ih - i["bottom"]), iw, ih


def badge_box(tc: dict, b: dict, x: int, bottom: int) -> tuple[int, int, int, int]:
    bw = int(cond_font(tc, b["size"]).getlength(tc["_channel"])) + b["pad"]
    return x, bottom - b["height"], x + bw, bottom


def badge(d: ImageDraw.ImageDraw, tc: dict, b: dict, box: tuple[int, int, int, int], ink) -> None:
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=ink("red"), outline=ink("white"), width=b["border"])
    d.text((x0 + (x1 - x0) / 2, y0 + b["kicker_y"]), tc["badge_kicker"], font=cond_font(tc, b["kicker_size"]),
           fill=ink("yellow"), anchor="ma")
    d.text((x0 + (x1 - x0) / 2, y1 - b["channel_y"]), tc["_channel"], font=cond_font(tc, b["size"]),
           fill=ink("white"), anchor="mb")


def photo_and_inset(img: Image.Image, d: ImageDraw.ImageDraw, tc: dict, c: dict, e: dict,
                    frame, box: tuple[int, int, int, int], ink) -> None:
    """The main face in box (x, y, w, h), the reaction inset and channel badge on it, the
    face kept clear of both."""
    px, py, pw, ph = box
    i, b = c["inset"], c["badge"]
    ix, iy, iw, ih = inset_box(i, px, py, pw, ph)
    pad = i["pad"]
    avoid = [(ix - pad - px, iy - pad - py, ix + iw + pad - px, iy + ih + pad - py)]
    if tc["_channel"]:
        bbox = badge_box(tc, b, px + b["left"], (iy + ih if b["on_inset"] else py + ph) - b["bottom"])
        avoid.append((bbox[0] - px, bbox[1] - py, bbox[2] - px, bbox[3] - py))
    photo = photo_file(tc, e.get("photo", ""), e.get("photo_label", ""))
    img.paste(face_crop(photo, WHOLE, (pw, ph)) if photo
              else face_crop(frame(e["frame"]), e["face"], (pw, ph), max_zoom=c["photo"]["zoom"], avoid=avoid),
              (px, py))
    d.rectangle((ix - pad, iy - pad, ix + iw + pad, iy + ih + pad), fill=ink("white"), outline=ink("black"),
                width=i["border"])
    img.paste(face_crop(frame(e["inset_frame"]), e["inset_face"], (iw, ih), fill=i["fill"]), (ix, iy))
    if tc["_channel"]:
        badge(d, tc, b, bbox, ink)


def finish(img: Image.Image, c: dict, dst: Path, tc: dict) -> int:
    noise = Image.effect_noise(tuple(c["size"]), c["noise"]).convert("RGB")
    return save(Image.blend(img, noise, c["grain"]), dst, tc["max_bytes"])


def compose_enquirer(stream_dir: Path, key: str, e: dict, ctx: dict, tc: dict, date: str,
                     video: Path, dst: Path) -> int:
    L = tc.get("_layout") or LAYOUT
    c, ink = L["cover"], palette(L)
    W, H = c["size"]
    img = Image.new("RGB", (W, H), ink("black"))
    d = ImageDraw.Draw(img)
    masthead(img, d, tc, c["masthead"], date, ink)
    band_strip(d, tc, c["band"], ctx["band"] or {"band_kicker": L["copy"]["band_kicker"],
                                                 "band_headline": " ".join(e["headline"])}, ink)

    # Side teasers; or none ([thumbnail] teasers = false: one big moment, the photo
    # fills the left of the cover).
    t = c["teasers"]
    y = t["y"]
    for i, box in enumerate(t["boxes"] if tc["teasers"] else ()):
        h, label = box["height"], box.get("label", "")
        if i < len(ctx["side"]):
            s = ctx["side"][i]
            if box.get("topic_label"):
                label = s["topic"].upper()[:t["topic_chars"]] or L["copy"]["no_topic"]
            photo = (face_crop(s["frame"], s["face"], (int(t["width"] * t["photo"]),
                                                       h - (t["label_height"] if label else 0) - 12))
                     if s["frame"] else None)
            teaser_box(img, tc, t, (t["x"], y, t["width"], h), ink(box["fill"]), ink(box["ink"]), label,
                       ink(box["label_fill"]) if label else None, ink(box["label_ink"]) if label else None,
                       s["text"], photo, ink("black"))
        else:
            d.rectangle((t["x"], y, t["x"] + t["width"], y + h), fill=ink(box["fill"]), outline=ink("black"),
                        width=t["border"])
        y += h + t["gap"]

    # Main face, bleeding to the bottom; reaction inset and channel badge on it.
    photo_and_inset(img, d, tc, c, e, lambda n: frame_file(stream_dir, key, e, n, video),
                    tuple(c["photo"]["box"] if tc["teasers"] else c["photo"]["full"]), ink)

    # Headline column: lines as wide as the column allows, scaled to a height budget.
    hd = c["headline"]
    lines = e["headline"] if e["text"] == " ".join(e["headline"]) else enquirer_lines(e["text"])
    hx, hw = hd["x"], W - hd["x"] - hd["right"]
    sizes = [fit_font(lambda s: head_font(tc, s), l, hw, hd["size"]).size for l in lines]
    k = min(1.0, hd["budget"] / (sum(sizes) * 0.93)) if sizes else 1.0
    y = hd["y"]
    for line, s in zip(lines, sizes):
        f = head_font(tc, int(s * k))
        d.text((hx, y - int(f.size * 0.04)), line, font=f, fill=ink("yellow"))
        y += int(f.size * 0.93)
    if e.get("attribution"):
        af = fit_font(lambda s: cond_font(tc, s), f"— {e['attribution']}", hw, hd["attribution_size"])
        d.text((W - hd["attribution_right"], y + hd["attribution_y"]), f"— {e['attribution']}", font=af,
               fill=ink("yellow"), anchor="ra")
        y += hd["attribution_step"]
    sf2 = cond_font(tc, hd["subhead_size"])
    for line in wrap_px(e.get("subhead", ""), sf2, hw)[: max(0, (H - hd["bottom"] - y) // hd["subhead_step"])]:
        d.text((hx, y), line, font=sf2, fill=ink("white"))
        y += hd["subhead_step"]
    return finish(img, c, dst, tc)


# ---------- vertical (Shorts) ----------

def fill_lines(text: str, tc: dict, width: int, budget: int, max_lines: int = 4,
               biggest: int = 330) -> list[tuple[str, int]]:
    """Headline lines and font sizes for a tall cover: each line as wide as width, the
    whole block within budget px of height, splitting where the smallest line comes out
    biggest. (enquirer_lines balances line lengths for the 16:9 column instead.)"""
    words = text.upper().split()
    probe = head_font(tc, 100)
    best = None
    for k in range(1, min(max_lines, len(words)) + 1):
        for split in line_breaks(words, k):
            lines = [" ".join(w) for w in split]
            sizes = [min(biggest, int(width * 100 / max(probe.getlength(ln), 1))) for ln in lines]
            scale = min(1.0, budget / (sum(sizes) * 0.93))
            sizes = [int(s * scale) for s in sizes]
            if best is None or min(sizes) > best[0]:
                best = (min(sizes), list(zip(lines, sizes)))
    return best[1] if best else []


def compose_vertical(stream_dir: Path, key: str, e: dict, ctx: dict, tc: dict, date: str,
                     video: Path, dst: Path) -> int:
    """The tabloid cover at 9:16 for a Short, from its clip's thumbnails.json choice."""
    L = tc.get("_layout") or LAYOUT
    c, ink = L["vertical"], palette(L)
    SW, SH = c["size"]
    img = Image.new("RGB", (SW, SH), ink("black"))
    d = ImageDraw.Draw(img)
    masthead(img, d, tc, c["masthead"], date, ink)
    band_strip(d, tc, c["band"], ctx["band"] or {"band_kicker": L["copy"]["band_kicker"],
                                                 "band_headline": " ".join(e["headline"])}, ink)

    # Headline, as wide as the cover allows, scaled to a height budget; attribution, subhead.
    hd = c["headline"]
    hx, hw = hd["x"], SW - hd["x"] - hd["right"]
    y = hd["y"]
    bottom = y
    for line, s in fill_lines(e["text"] or " ".join(e["headline"]), tc, hw, hd["budget"], hd["lines"],
                              hd["size"]):
        f = head_font(tc, s)
        d.text((hx, y - int(f.size * 0.04)), line, font=f, fill=ink("yellow"))
        bottom = d.textbbox((hx, y - int(f.size * 0.04)), line, font=f)[3]
        y += int(f.size * 0.93)
    y = max(y, bottom + hd["gap"])
    if e.get("attribution"):
        af = fit_font(lambda s: cond_font(tc, s), f"— {e['attribution']}", hw, hd["attribution_size"])
        d.text((SW - hd["attribution_right"], y + hd["attribution_y"]), f"— {e['attribution']}", font=af,
               fill=ink("yellow"), anchor="ra")
        y += hd["attribution_step"]
    sf = cond_font(tc, hd["subhead_size"])
    for line in wrap_px(e.get("subhead", ""), sf, hw)[:hd["subhead_lines"]]:
        d.text((hx, y), line, font=sf, fill=ink("white"))
        y += hd["subhead_step"]

    # The face to the bottom, clear of the inset and badge; those above the bottom title overlay.
    p = c["photo"]
    py = y + p["gap"]
    photo_and_inset(img, d, tc, c, e, lambda n: frame_file(stream_dir, key, e, n, video),
                    (p["x"], py, SW - p["x"] - p["right"], SH - p["bottom"] - py), ink)
    return finish(img, c, dst, tc)
