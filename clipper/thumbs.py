"""Thumbnails: one 1280x720 JPEG per cut asset (clips/, segments/, compilations/).

  frames    ffmpeg grabs candidate frames: clips around their hook (short_in_s) and payoff
            (short_out_s) plus chat-heat peaks inside the clip; segments and compilations
            evenly spaced plus their hottest chat moments (signals.parquet). Black or
            blurry frames are nudged a little, then skipped.
  pick      one vision call per asset: the frame with the strongest readable expression or
            moment, 2-4 words of text, the word to highlight, and top/bottom placement
  compose   Pillow: the frame graded and shaded, the text in the [thumbnail] brand style
            (font, colors, outline, shadow, badge or logo, stripe), under max_bytes

Writes <video>.jpg beside each video, and thumbnails.json in the stream dir with every
choice (frame time, text, why). To change one by hand, edit its "text", "highlight",
"position" or "frame" there and rerun with --compose-only (no model calls); the same
flag re-renders everything after changing the brand settings. Text is checked against
[bleep] patterns and never printed with a listed word. Reviewers change the text without
touching files through the Kitsu "Thumbnail text" column or Discord's Thumbnail text button;
`clipper review sync` writes it here and re-renders (set_texts).

Enquirer covers can use [thumbnail] photos ("<Person>.jpg" portraits) for people talked
about but not on screen: the model is offered the names and may set "photo" (the main
face) or "teaser_photo" (shown beside this video's teaser on the other covers);
`--photo` / `--teaser-photo` set them by hand (set_photos).

Shorts get a 9:16 version of their clip's enquirer cover beside them (shorts/<name>.jpg):
compose_vertical, from the clip's thumbnails.json choice, no model calls. `clipper thumbs`
draws them with the clip covers, `clipper shorts` right after rendering a Short, and
`clipper thumbs <stream> --shorts` draws just those (e.g. for streams made before this).

`clipper upload` sets the .jpg as the YouTube thumbnail; with [thumbnail] kitsu on, it is
attached to each item's latest Kitsu revision and becomes the item's Kitsu thumbnail.
"""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .config import for_stream
from .meta import duration, load_assets
from .scoring import Claude, Prompts, load_signals, preflight

W, H = 1280, 720
PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "frame": {"type": "integer"},
        "text": {"type": "string"},
        "highlight": {"type": "string"},
        "position": {"type": "string", "enum": ["top", "bottom"]},
        "crop": {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                           "width": {"type": "number"}},
            "required": ["x", "y", "width"],
            "additionalProperties": False,
        },
        "why": {"type": "string"},
    },
    "required": ["frame", "text", "highlight", "position", "crop", "why"],
    "additionalProperties": False,
}


BOX = {
    "type": "object",
    "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                   "width": {"type": "number"}, "height": {"type": "number"}},
    "required": ["x", "y", "width", "height"],
    "additionalProperties": False,
}
ENQ_SCHEMA = {
    "type": "object",
    "properties": {
        "main_person": {"type": "string"},
        "frame": {"type": "integer"},
        "face": BOX,
        "headline": {"type": "array", "items": {"type": "string"}},
        "attribution": {"type": "string"},
        "subhead": {"type": "string"},
        "inset_frame": {"type": "integer"},
        "inset_face": BOX,
        "teaser": {"type": "string"},
        "band_kicker": {"type": "string"},
        "band_headline": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["main_person", "frame", "face", "headline", "attribution", "subhead", "inset_frame",
                 "inset_face", "teaser", "band_kicker", "band_headline", "why"],
    "additionalProperties": False,
}
ENQ_TEXT = ("headline", "attribution", "subhead", "teaser", "band_kicker", "band_headline")


def asset_key(x: dict) -> str:
    return f"{x['video'].parent.name}/{x['video'].stem}"


# ---------- candidate frames ----------

def heat_peaks(sig: dict, a: float, b: float, n: int, gap: float) -> list[float]:
    """The n hottest seconds in [a, b) of stream time, at least gap apart."""
    t, heat = sig["t"], np.nan_to_num(sig["heat"], nan=-1e9)
    idx = np.flatnonzero((t >= a) & (t < b))
    out: list[float] = []
    for i in idx[np.argsort(heat[idx])[::-1]]:
        if len(out) >= n:
            break
        if all(abs(t[i] - o) >= gap for o in out):
            out.append(float(t[i]))
    return out


def candidate_times(x: dict, sig: dict | None, tc: dict) -> list[float]:
    """Video-local times to try, most promising first."""
    dur = duration(x)
    if x["kind"] == "clip":
        side = json.loads(x["video"].with_suffix(".json").read_text(encoding="utf-8"))
        a = side["in_s"]
        hook, payoff = side["short_in_s"] - a, side["short_out_s"] - a
        ts = [hook + 1.0, payoff - 1.5, (hook + payoff) / 2, hook + 3.0, payoff - 4.0]
        if sig is not None:
            ts += [p - a for p in heat_peaks(sig, side["in_s"], side["out_s"], 3, 4)]
        n, gap = tc["clip_frames"], min(3.0, dur / (2 * tc["clip_frames"]))
        ts += [dur * (k + 0.5) / n for k in range(n)]
    else:
        n, gap = tc["even_frames"] + tc["heat_frames"], tc["min_gap_s"]
        ts = []
        if sig is not None:
            per = max(1, -(-tc["heat_frames"] // len(x["parts"])))
            peaks = [(float(np.nanmax(sig["heat"][int(p):int(p) + 1])), p - a + off)
                     for a, b, off in x["parts"]
                     for p in heat_peaks(sig, a + 15, b - 15, per, tc["min_gap_s"])]
            ts += [t for _, t in sorted(peaks, reverse=True)[:tc["heat_frames"]]]
        ts += [dur * (k + 0.5) / tc["even_frames"] for k in range(tc["even_frames"])]
    out: list[float] = []
    for t in ts:
        t = min(max(t, 0.2), dur - 0.5)
        if all(abs(t - o) >= gap for o in out):
            out.append(round(t, 2))
    return out[:n]


def grab(video: Path, t: float, dst: Path) -> Image.Image | None:
    """Full source resolution, cropped to 16:9, so a zoomed crop stays sharp."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}",
                    "-i", str(video), "-frames:v", "1", "-vf",
                    "crop='min(iw,ih*16/9)':'min(ih,iw*9/16)'", "-q:v", "2", str(dst)],
                   check=False)
    return Image.open(dst).convert("RGB") if dst.exists() else None


def frame_stats(img: Image.Image) -> tuple[float, float]:
    """(mean luma 0-255, variance of the Laplacian at 640 px wide)."""
    g = np.asarray(img.convert("L").resize((640, 360)), dtype=np.float32)
    lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1])
    return float(g.mean()), float(lap.var())


def frames(x: dict, sig: dict | None, tc: dict, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()
    dur, kept, rejected = duration(x), [], []
    for t0 in candidate_times(x, sig, tc):
        for t in (t0, t0 + 0.7, t0 - 0.7):  # a nudge often clears a blink or motion blur
            if not 0 <= t < dur:
                continue
            dst = out_dir / f"{len(kept) + len(rejected):02d}_{t:.1f}.jpg"
            img = grab(x["video"], t, dst)
            if img is None:
                continue
            luma, sharp = frame_stats(img)
            f = {"t": round(t, 2), "path": dst, "brightness": round(luma, 1),
                 "sharpness": round(sharp, 1)}
            if luma >= tc["min_brightness"] and sharp >= tc["min_sharpness"]:
                kept.append(f)
                break
            rejected.append(f)
            dst.unlink(missing_ok=True)
    if not kept:  # nothing passed: keep the sharpest few rather than nothing
        for f in sorted(rejected, key=lambda f: -f["sharpness"])[:3]:
            grab(x["video"], f["t"], f["path"])
            kept.append(f)
    return kept


# ---------- pick ----------

def b64_jpeg(path: Path, width: int = 768) -> str:
    """The frame as the model sees it: downscaled, with a faint 0.1 grid labeled along the
    top and left edges, so its crop coordinates land where it means them to."""
    img = Image.open(path).convert("RGB")
    img = img.resize((width, round(width * img.height / img.width)))
    w, h = img.size
    grid = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(grid)
    for k in range(1, 10):
        x, y = round(w * k / 10), round(h * k / 10)
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 60))
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 60))
        d.text((x + 2, 2), f".{k}", fill=(255, 255, 0, 220), stroke_width=2, stroke_fill=(0, 0, 0, 220))
        d.text((2, y + 2), f".{k}", fill=(255, 255, 0, 220), stroke_width=2, stroke_fill=(0, 0, 0, 220))
    img = Image.alpha_composite(img.convert("RGBA"), grid).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def clean_text(text: str, max_words: int) -> str:
    text = re.sub(r"[\"“”#]|[^\w\s'’!?.,%$&:-]", "", text).strip()
    return " ".join(text.split()[:max_words])


def unsafe(text: str, bleeper) -> bool:
    return any(bleeper.hit(w) for w in text.split())


def fallback_text(x: dict) -> str:
    return (x.get("topic") or (x["figures"] or [""])[0] or "").upper()[:24]


def feedback_text(note: str) -> str:
    return (f"\nThe reviewer rejected the previous thumbnail for this video: {note}\n"
            "Make a new choice that fixes that.") if note else ""


def pick(x: dict, fr: list[dict], info: dict, tc: dict, claude: Claude, system: str,
         bleeper, note: str = "") -> dict:
    content: list[dict] = []
    for i, f in enumerate(fr, 1):
        content += [{"type": "text", "text": f"Frame {i} ({f['t'] // 60:.0f}:{f['t'] % 60:04.1f})"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": b64_jpeg(f["path"])}}]
    content.append({"type": "text", "text": (
        f"Video: {x['kind']}, {duration(x) / 60:.1f} min\nTitle: {info['title']}\n"
        + (f"Hook: {info['hook']}\n" if info["hook"] else "")
        + (f"Topic: {x['topic']}\n" if x["topic"] else "")
        + (f"People: {', '.join(x['figures'])}\n" if x["figures"] else "")
        + (f"About: {info['about']}\n" if info["about"] else "")
        + f"Pick one of frames 1-{len(fr)}." + feedback_text(note))})
    out, meta = None, {}
    for attempt in range(2):
        out, meta = claude.json_call(tc["model"], system, content, PICK_SCHEMA, tc["effort"],
                                     fallback=True)
        if out is None:
            break
        out["text"] = clean_text(out["text"], tc["max_words"])
        if out["text"] and not unsafe(out["text"], bleeper):
            break
        content = content + [{"type": "text", "text": (
            f"Your text {out['text']!r} can't be used (empty, or it has a word the channel "
            "never prints). Write different text.")}]
        out = None
    if out is None:
        out = {"frame": 1, "text": fallback_text(x), "highlight": "", "position": "bottom",
               "crop": {"x": 0, "y": 0, "width": 1}, "why": f"fallback ({meta.get('refusal') or 'no usable text'})"}
    out["frame"] = min(max(int(out["frame"]), 1), len(fr))
    out["t"] = fr[out["frame"] - 1]["t"]
    out["crop"] = clamp_crop(out["crop"], tc["min_crop"])
    return out


def clamp_box(b: dict) -> dict:
    x = min(max(float(b.get("x", 0)), 0.0), 0.98)
    y = min(max(float(b.get("y", 0)), 0.0), 0.98)
    return {"x": round(x, 3), "y": round(y, 3),
            "width": round(min(max(float(b.get("width", 0.3)), 0.02), 1 - x), 3),
            "height": round(min(max(float(b.get("height", 0.4)), 0.02), 1 - y), 3)}


def enquirer_lines(text: str, max_lines: int = 3) -> list[str]:
    """Split headline text into up to max_lines lines of similar length."""
    words = text.upper().split()
    if len(words) <= 1:
        return words
    best = None
    for k in range(1, min(max_lines, len(words)) + 1):
        for lines in layouts(words, k):
            lens = [len(" ".join(l)) for l in lines]
            score = (max(lens), -k)  # shortest longest line, then more lines
            if best is None or score < best[0]:
                best = (score, [" ".join(l) for l in lines])
    return best[1]


def pick_enquirer(x: dict, fr: list[dict], info: dict, tc: dict, claude: Claude, system: str,
                  bleeper, note: str = "") -> dict:
    content: list[dict] = []
    for i, f in enumerate(fr, 1):
        content += [{"type": "text", "text": f"Frame {i} ({f['t'] // 60:.0f}:{f['t'] % 60:04.1f})"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": b64_jpeg(f["path"])}}]
    content.append({"type": "text", "text": (
        f"Video: {x['kind']}, {duration(x) / 60:.1f} min\nTitle: {info['title']}\n"
        + (f"Hook: {info['hook']}\n" if info["hook"] else "")
        + (f"Topic: {x['topic']}\n" if x["topic"] else "")
        + (f"People: {', '.join(x['figures'])}\n" if x["figures"] else "")
        + (f"About: {info['about']}\n" if info["about"] else "")
        + f"Frames are numbered 1-{len(fr)}." + feedback_text(note))})
    schema, names = ENQ_SCHEMA, list(tc.get("_photos", {}))
    if names:
        content.append({"type": "text", "text": f"Photos on file: {', '.join(names)}."})
        pick_one = {"type": "string", "enum": ["", *names]}
        schema = {**ENQ_SCHEMA, "properties": {**ENQ_SCHEMA["properties"], "photo": pick_one,
                                               "teaser_photo": pick_one},
                  "required": [*ENQ_SCHEMA["required"], "photo", "teaser_photo"]}
    out, meta = None, {}
    for attempt in range(2):
        out, meta = claude.json_call(tc["model"], system, content, schema, tc["effort"],
                                     fallback=True)
        if out is None:
            break
        out["headline"] = [clean_text(l, 4).upper() for l in out["headline"] if clean_text(l, 4)][:3]
        if len(out["headline"]) == 1:
            out["headline"] = enquirer_lines(out["headline"][0])
        for k in ("attribution", "band_kicker", "band_headline"):
            out[k] = clean_text(out[k], 8).upper() if k != "band_kicker" else clean_text(out[k], 5)
        out["subhead"] = clean_text(out["subhead"], 14)
        out["teaser"] = clean_text(out["teaser"], 6)
        flat = " ".join(" ".join(v) if isinstance(v, list) else v for v in (out[k] for k in ENQ_TEXT))
        if out["headline"] and not unsafe(flat, bleeper):
            break
        content = content + [{"type": "text", "text": (
            "That text can't be used (an empty headline, or a word the channel never prints). "
            "Write it again.")}]
        out = None
    if out is None:
        name = fallback_text(x) or "EXCLUSIVE"
        out = {"main_person": "", "frame": 1, "face": {"x": 0.3, "y": 0.15, "width": 0.4, "height": 0.7},
               "headline": enquirer_lines(name + "!"), "attribution": "SOURCES SAY",
               "subhead": info["title"][:80], "inset_frame": 1,
               "inset_face": {"x": 0.3, "y": 0.15, "width": 0.4, "height": 0.7},
               "teaser": info["title"][:30], "band_kicker": "Exclusive:",
               "band_headline": name + "!",
               "why": f"fallback ({meta.get('refusal') or 'no usable text'})"}
    for f_key, b_key in (("frame", "face"), ("inset_frame", "inset_face")):
        out[f_key] = min(max(int(out[f_key]), 1), len(fr))
        out[b_key] = clamp_box(out[b_key])
    for k in ("photo", "teaser_photo"):  # kept only when set, so older entries read the same
        name = photo_name(tc, out.pop(k, ""))
        if name:
            out[k] = name
    out["t"] = fr[out["frame"] - 1]["t"]
    out["text"] = " ".join(out["headline"])  # what reviewers edit (Kitsu, Discord)
    return out


def clamp_crop(c: dict, min_width: float) -> dict:
    w = min(max(float(c.get("width", 1)), min_width), 1.0)
    return {"x": round(min(max(float(c.get("x", 0)), 0), 1 - w), 3),
            "y": round(min(max(float(c.get("y", 0)), 0), 1 - w), 3), "width": round(w, 3)}


# ---------- compose ----------

def color(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def font_path(tc: dict, key: str = "font") -> str:
    for p in [tc.get(key) or tc["font"], *tc["fonts"]]:
        if p and Path(p).exists():
            return str(p)
    raise SystemExit("thumbnail: no font found; set [thumbnail] font to a .ttf path")


def font_at_cap(path: str, cap: float) -> ImageFont.FreeTypeFont:
    probe = ImageFont.truetype(path, 200)
    _, t, _, b = probe.getbbox("H")
    return ImageFont.truetype(path, max(8, int(200 * cap / (b - t))))


def layouts(words: list[str], max_lines: int) -> list[list[list[str]]]:
    """Every way to break words into 1..max_lines lines, in order."""
    out = []
    n = len(words)
    for k in range(1, min(max_lines, n) + 1):
        def rec(start: int, left: int) -> list[list[list[str]]]:
            if left == 1:
                return [[words[start:]]]
            return [[words[start:i]] + rest for i in range(start + 1, n - left + 2)
                    for rest in rec(i, left - 1)]
        out += rec(0, k)
    return out


def fit_text(words: list[str], tc: dict, path: str, max_w: int) -> tuple[list[list[str]], ImageFont.FreeTypeFont]:
    """The line breaks that allow the biggest type (capped at text_height); ties go to fewer
    lines, then to the most even line lengths."""
    best = None
    for lines in layouts(words, tc["max_lines"]):
        cap = tc["text_height"] * H
        while True:
            f = font_at_cap(path, cap)
            widest = max(f.getlength(" ".join(l)) for l in lines)
            if widest <= max_w or cap < 20:
                break
            cap *= 0.94
        rank = (round(cap), -len(lines), -widest)
        if best is None or rank > best[0]:
            best = (rank, lines, f)
    return best[1], best[2]


def shade(img: Image.Image, position: str, strength: float) -> Image.Image:
    if strength <= 0:
        return img
    ramp = np.clip(1 - np.arange(H) / (H * 0.6), 0, 1) ** 1.4 * strength * 255
    if position == "bottom":
        ramp = ramp[::-1]
    alpha = Image.fromarray(np.repeat(ramp[:, None], W, axis=1).astype(np.uint8), "L")
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, alpha)


def compose(frame: Path, choice: dict, tc: dict, dst: Path) -> int:
    img = Image.open(frame).convert("RGB")
    c = clamp_crop(choice.get("crop") or {}, tc["min_crop"])
    fw, fh = img.size
    img = img.crop((round(c["x"] * fw), round(c["y"] * fh), round((c["x"] + c["width"]) * fw),
                    round((c["y"] + c["width"]) * fh))).resize((W, H), Image.LANCZOS)
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(tc["saturation"])).enhance(tc["contrast"])
    img = shade(img, choice["position"], tc["shade"])
    m, accent = tc["margin_px"], color(tc["accent_color"])

    # Brand: accent stripe along the bottom, badge or logo top-left.
    draw = ImageDraw.Draw(img)
    if tc["stripe_px"]:
        draw.rectangle([0, H - tc["stripe_px"], W, H], fill=accent)
    top_used = 0
    if tc["logo"] and Path(tc["logo"]).exists():
        logo = Image.open(tc["logo"]).convert("RGBA")
        logo = logo.resize((round(logo.width * 84 / logo.height), 84))
        img.paste(logo, (m // 2, m // 2), logo)
        top_used = m // 2 + 84
    elif tc["badge_text"]:
        bf = font_at_cap(font_path(tc, "badge_font"), 26)
        tw = bf.getlength(tc["badge_text"])
        box = [m // 2, m // 2, m // 2 + tw + 32, m // 2 + 50]
        draw.rounded_rectangle(box, radius=8, fill=accent)
        draw.text((box[0] + 16, box[1] + 25), tc["badge_text"], font=bf, anchor="lm",
                  fill=color(tc["outline_color"]))
        top_used = box[3]

    # Text on its own layer, so the shadow can be blurred from its alpha.
    text = choice["text"].upper() if tc["uppercase"] else choice["text"]
    words = text.split()
    if not words:
        return save(img, dst, tc["max_bytes"])
    path = font_path(tc)
    lines, f = fit_text(words, tc, path, W - 2 * m)
    _, t, _, b = f.getbbox("H")
    cap, gap = b - t, (b - t) * 0.22
    block = len(lines) * cap + (len(lines) - 1) * gap
    y0 = (max(m, top_used + m // 2) if choice["position"] == "top"
          else H - tc["stripe_px"] - m - block)
    hl = re.sub(r"\W", "", (choice.get("highlight") or "").upper())
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    space = f.getlength(" ")
    for i, line in enumerate(lines):
        x = (W - f.getlength(" ".join(line))) / 2
        y = y0 + i * (cap + gap) - t  # top of the cap height at y0 + i * (cap + gap)
        for w in line:
            fill = accent if hl and re.sub(r"\W", "", w.upper()) == hl else color(tc["text_color"])
            ld.text((x, y), w, font=f, fill=fill, stroke_width=tc["outline_px"],
                    stroke_fill=color(tc["outline_color"]))
            x += f.getlength(w) + space
    if tc["shadow_px"]:
        a = layer.getchannel("A").point(lambda v: v * 0.7)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shadow.putalpha(a.filter(ImageFilter.GaussianBlur(tc["shadow_px"] * 0.8)))
        img.paste(shadow, (tc["shadow_px"], tc["shadow_px"]), shadow)
    img.paste(layer, (0, 0), layer)
    return save(img, dst, tc["max_bytes"])


def save(img: Image.Image, dst: Path, max_bytes: int) -> int:
    for q in (92, 88, 84, 80, 75, 70, 60):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=0 if q >= 88 else 2)
        if buf.tell() <= max_bytes:
            break
    # Unchanged output keeps its mtime, so Kitsu doesn't get the same picture again.
    if not dst.exists() or dst.read_bytes() != buf.getvalue():
        dst.write_bytes(buf.getvalue())
    return buf.tell()


# ---------- enquirer style ----------
# A 1990s supermarket-tabloid cover in 16:9: the show's masthead and a green teaser band
# across the top, three teaser boxes down the left, a big face in the middle, the yellow
# headline on the right, a reaction inset and the channel badge on the photo.

YEL, RED, BLK, WHT = (255, 226, 0), (214, 18, 32), (8, 8, 8), (255, 255, 255)
GREEN, PINK, CREAM = (16, 84, 62), (246, 196, 214), (250, 244, 222)


def cond_font(tc: dict, size: int) -> ImageFont.FreeTypeFont:
    """The condensed face for teasers, subheads and labels; the headline font if none."""
    for p in tc["condensed_fonts"]:
        if p and Path(p).exists():
            f = ImageFont.truetype(p, size)
            try:
                f.set_variation_by_name(tc["condensed_variation"])
            except (OSError, ValueError, AttributeError):
                pass  # not a variable font, or no such instance
            return f
    return ImageFont.truetype(font_path(tc), size)


def head_font(tc: dict, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(tc), size)


def fit_font(make, text: str, max_w: float, start: int, lo: int = 10) -> ImageFont.FreeTypeFont:
    for s in range(start, lo, -1):
        f = make(s)
        if f.getlength(text) <= max_w:
            return f
    return make(lo)


def wrap_px(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    lines: list[str] = []
    for w in text.split():
        if lines and f.getlength(lines[-1] + " " + w) <= max_w:
            lines[-1] += " " + w
        else:
            lines.append(w)
    return lines


PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")
WHOLE = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}  # a photo is all face


def photos(tc: dict) -> dict[str, Path]:
    """[thumbnail] photos: {person: path}, named by the file names ("Hasan Piker.jpg")."""
    d = Path(tc["photos"]) if tc.get("photos") else None
    if not d or not d.is_dir():
        return {}
    return {p.stem: p for p in sorted(d.iterdir()) if p.suffix.lower() in PHOTO_EXT}


def photo_name(tc: dict, name: str) -> str:
    """The on-file spelling of a photo's person, matched ignoring case; "" if none."""
    return next((n for n in tc.get("_photos", {}) if n.lower() == (name or "").strip().lower()), "")


def face_crop(path: Path, face: dict, size: tuple[int, int], fill: float = 0.72,
              max_zoom: float = 2.0) -> Image.Image:
    """Crop around a face box so the head fills `fill` of the photo's height, the face a
    little above center, then cover-fit to size, enlarging the source at most max_zoom."""
    img = Image.open(path).convert("RGB")
    fw, fh = img.size
    tw, th = size
    b = clamp_box(face)
    cx, cy = (b["x"] + b["width"] / 2) * fw, (b["y"] + b["height"] / 2) * fh
    h = min(fh, max(b["height"] * fh / fill, th / max_zoom))
    w = h * tw / th
    if w > fw:
        w, h = fw, fw * th / tw
    x0 = min(max(cx - w / 2, 0), fw - w)
    y0 = min(max(cy - h * 0.45, 0), fh - h)
    img = img.crop((round(x0), round(y0), round(x0 + w), round(y0 + h))).resize(size, Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Color(img).enhance(1.25)
    return ImageEnhance.Sharpness(img).enhance(1.4)


def italic(text: str, f: ImageFont.FreeTypeFont, fill, shadow=BLK, shear: float = 0.22,
           pad: int = 12) -> Image.Image:
    x0, y0, x1, y1 = f.getbbox(text)
    h = y1 - y0 + pad * 2
    im = Image.new("RGBA", (x1 - x0 + pad * 2 + int(h * shear), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((pad - x0 + 5, pad - y0 + 5), text, font=f, fill=shadow)
    d.text((pad - x0, pad - y0), text, font=f, fill=fill)
    return im.transform(im.size, Image.AFFINE, (1, shear, -shear * h * 0.5, 0, 1, 0),
                        resample=Image.BICUBIC)


def teaser_box(img: Image.Image, tc: dict, box: tuple[int, int, int, int], bg, ink, label,
               label_bg, label_ink, text: str, photo: Image.Image | None) -> None:
    x, y, w, h = box
    d = ImageDraw.Draw(img)
    d.rectangle((x, y, x + w, y + h), fill=bg, outline=BLK, width=3)
    top = y
    if label:
        d.rectangle((x, y, x + w, y + 30), fill=label_bg)
        lf = fit_font(lambda s: cond_font(tc, s), label, w - 12, 22)
        d.text((x + w / 2, y + 15), label, font=lf, fill=label_ink, anchor="mm")
        top += 30
    pw = int(w * 0.44) if photo is not None else 0
    if photo is not None:
        img.paste(photo, (x + w - pw - 6, top + 6))
    tw, th = w - pw - 18, y + h - top - 12
    size = 42
    while size > 14:
        f = cond_font(tc, size)
        lines = wrap_px(text, f, tw)
        if len(lines) * size * 0.98 <= th and all(f.getlength(l) <= tw for l in lines):
            break
        size -= 1
    ty = top + 6
    for line in lines:
        d.text((x + 8, ty), line, font=f, fill=ink)
        ty += int(size * 0.98)


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
    import zlib
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
        photo = tc.get("_photos", {}).get(photo_name(tc, e.get("teaser_photo", "")))
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
        chapters = json.loads(draft.read_text(encoding="utf-8")).get("chapters") or []
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


def compose_enquirer(stream_dir: Path, key: str, e: dict, ctx: dict, tc: dict, date: str,
                     video: Path, dst: Path) -> int:
    img = Image.new("RGB", (W, H), BLK)
    d = ImageDraw.Draw(img)
    fdir = stream_dir / "thumbs" / key

    def frame_path(n: int) -> Path:
        p = fdir / e["frames"][n - 1]["path"]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            grab(video, e["frames"][n - 1]["t"], p)
        return p

    # Masthead: white box, the show's name in red italics filling it.
    d.rectangle((8, 8, 560, 118), fill=WHT, outline=BLK, width=3)
    small = cond_font(tc, 15)
    d.text((20, 16), tc["masthead_tagline"], font=small, fill=RED)
    d.text((552, 16), f"{date}  ·  {tc['masthead_price']}".strip(" ·"), font=small,
           fill=BLK, anchor="ra")
    size = 96
    while True:
        name = italic(tc["_masthead"], head_font(tc, size), RED)
        if name.width <= 534 or size <= 40:
            break
        size -= 2
    img.paste(name, (14, 118 - name.height + 6), name)

    # Green band: another story from the episode.
    band = ctx["band"] or {"band_kicker": "Exclusive:", "band_headline": " ".join(e["headline"])}
    d.rectangle((568, 8, W - 8, 118), fill=GREEN, outline=BLK, width=3)
    if band["band_kicker"]:
        d.text((584, 14), band["band_kicker"], font=fit_font(lambda s: cond_font(tc, s),
                                                             band["band_kicker"], W - 610, 28), fill=YEL)
        bf = fit_font(lambda s: head_font(tc, s), band["band_headline"], W - 8 - 600, 64)
        d.text((584, 44), band["band_headline"], font=bf, fill=WHT, stroke_width=2, stroke_fill=BLK)
    else:  # one line, centred in the band
        bf = fit_font(lambda s: head_font(tc, s), band["band_headline"], W - 8 - 600, 84)
        d.text((584, 63), band["band_headline"], font=bf, fill=WHT, stroke_width=2, stroke_fill=BLK,
               anchor="lm")

    # Side teasers.
    styles = [(YEL, RED, None, None, None), (PINK, BLK, "CAN YOU BELIEVE IT?", YEL, BLK),
              (CREAM, RED, "", RED, WHT)]
    y, heights = 128, (178, 196, 196)
    for i, h in enumerate(heights):
        bg, ink, label, lbg, lin = styles[i]
        if i < len(ctx["side"]):
            s = ctx["side"][i]
            if label == "":
                label = s["topic"].upper()[:22] or "EXCLUSIVE"
            photo = (face_crop(s["frame"], s["face"], (int(300 * 0.44), h - (30 if label else 0) - 12))
                     if s["frame"] else None)
            teaser_box(img, tc, (8, y, 300, h), bg, ink, label, lbg, lin, s["text"], photo)
        else:
            d.rectangle((8, y, 308, y + h), fill=bg, outline=BLK, width=3)
        y += h + 6

    # Main face, bleeding to the bottom; reaction inset and channel badge on it.
    mx, my, mw, mh = 314, 126, 470, H - 126
    photo = tc.get("_photos", {}).get(photo_name(tc, e.get("photo", "")))
    img.paste(face_crop(photo, WHOLE, (mw, mh)) if photo
              else face_crop(frame_path(e["frame"]), e["face"], (mw, mh)), (mx, my))
    iw, ih = 176, 158
    ix, iy = mx + mw - iw - 14, H - ih - 14
    d.rectangle((ix - 7, iy - 7, ix + iw + 7, iy + ih + 7), fill=WHT, outline=BLK, width=2)
    img.paste(face_crop(frame_path(e["inset_frame"]), e["inset_face"], (iw, ih), fill=0.72), (ix, iy))
    if tc["_channel"]:
        bf2, sf = cond_font(tc, 30), cond_font(tc, 13)
        bw = int(bf2.getlength(tc["_channel"])) + 24
        bx, by = mx + 12, H - 62
        d.rectangle((bx, by, bx + bw, H - 12), fill=RED, outline=WHT, width=2)
        d.text((bx + bw / 2, by + 4), tc["badge_kicker"], font=sf, fill=YEL, anchor="ma")
        d.text((bx + bw / 2, H - 16), tc["_channel"], font=bf2, fill=WHT, anchor="mb")

    # Headline column: lines as wide as the column allows, scaled to a height budget.
    lines = e["headline"] if e["text"] == " ".join(e["headline"]) else enquirer_lines(e["text"])
    hx, hw = 796, W - 796 - 14
    sizes = [fit_font(lambda s: head_font(tc, s), l, hw, 150).size for l in lines]
    k = min(1.0, 380 / (sum(sizes) * 0.93)) if sizes else 1.0
    y = 128
    for line, s in zip(lines, sizes):
        f = head_font(tc, int(s * k))
        d.text((hx, y - int(f.size * 0.04)), line, font=f, fill=YEL)
        y += int(f.size * 0.93)
    if e.get("attribution"):
        af = fit_font(lambda s: cond_font(tc, s), f"— {e['attribution']}", hw, 24)
        d.text((W - 16, y + 8), f"— {e['attribution']}", font=af, fill=YEL, anchor="ra")
        y += 40
    sf2 = cond_font(tc, 44)
    for line in wrap_px(e.get("subhead", ""), sf2, hw)[: max(0, (H - 12 - y) // 44)]:
        d.text((hx, y), line, font=sf2, fill=WHT)
        y += 44

    noise = Image.effect_noise((W, H), 20).convert("RGB")
    return save(Image.blend(img, noise, 0.04), dst, tc["max_bytes"])


# ---------- vertical (Shorts) ----------

# The same tabloid cover at 9:16 for a Short, from its clip's thumbnails.json choice:
# masthead and green band on top, the headline stacked big under them, then the clip's
# face to the bottom with the reaction inset and channel badge kept clear of the bottom,
# where YouTube's mobile shelf lays the title over the cover. No side teasers.
SW, SH = 1080, 1920


def fill_lines(text: str, tc: dict, width: int, budget: int, max_lines: int = 4,
               biggest: int = 330) -> list[tuple[str, int]]:
    """Headline lines and font sizes for a tall cover: each line as wide as width, the
    whole block within budget px of height, splitting where the smallest line comes out
    biggest. (enquirer_lines balances line lengths for the 16:9 column instead.)"""
    words = text.upper().split()
    probe = head_font(tc, 100)
    best = None
    for k in range(1, min(max_lines, len(words)) + 1):
        for split in layouts(words, k):
            lines = [" ".join(w) for w in split]
            sizes = [min(biggest, int(width * 100 / max(probe.getlength(ln), 1))) for ln in lines]
            scale = min(1.0, budget / (sum(sizes) * 0.93))
            sizes = [int(s * scale) for s in sizes]
            if best is None or min(sizes) > best[0]:
                best = (min(sizes), list(zip(lines, sizes)))
    return best[1] if best else []


def compose_vertical(stream_dir: Path, key: str, e: dict, ctx: dict, tc: dict, date: str,
                     video: Path, dst: Path) -> int:
    img = Image.new("RGB", (SW, SH), BLK)
    d = ImageDraw.Draw(img)
    fdir = stream_dir / "thumbs" / key
    m = 16

    def frame_path(n: int) -> Path:
        p = fdir / e["frames"][n - 1]["path"]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            grab(video, e["frames"][n - 1]["t"], p)
        return p

    # Masthead.
    d.rectangle((m, m, SW - m, 236), fill=WHT, outline=BLK, width=4)
    small = cond_font(tc, 24)
    d.text((m + 16, m + 12), tc["masthead_tagline"], font=small, fill=RED)
    d.text((SW - m - 16, m + 44), f"{date}  ·  {tc['masthead_price']}".strip(" ·"), font=small,
           fill=BLK, anchor="ra")
    size = 170
    while True:
        name = italic(tc["_masthead"], head_font(tc, size), RED)
        if name.width <= SW - 2 * m - 20 or size <= 60:
            break
        size -= 2
    img.paste(name, (m + 6, 236 - name.height + 10), name)

    # Green band: another story from the episode.
    band = ctx["band"] or {"band_kicker": "Exclusive:", "band_headline": " ".join(e["headline"])}
    d.rectangle((m, 248, SW - m, 388), fill=GREEN, outline=BLK, width=4)
    bw = SW - 2 * m - 36
    if band["band_kicker"]:
        d.text((m + 18, 256), band["band_kicker"],
               font=fit_font(lambda s: cond_font(tc, s), band["band_kicker"], bw, 40), fill=YEL)
        bf = fit_font(lambda s: head_font(tc, s), band["band_headline"], bw, 74)
        d.text((m + 18, 302), band["band_headline"], font=bf, fill=WHT, stroke_width=2, stroke_fill=BLK)
    else:
        bf = fit_font(lambda s: head_font(tc, s), band["band_headline"], bw, 96)
        d.text((m + 18, 318), band["band_headline"], font=bf, fill=WHT, stroke_width=2,
               stroke_fill=BLK, anchor="lm")

    # Headline, as wide as the cover allows, scaled to a height budget; attribution, subhead.
    hx, hw = m + 8, SW - 2 * m - 16
    y = 404
    bottom = y
    for line, s in fill_lines(e["text"] or " ".join(e["headline"]), tc, hw, 470):
        f = head_font(tc, s)
        d.text((hx, y - int(f.size * 0.04)), line, font=f, fill=YEL)
        bottom = d.textbbox((hx, y - int(f.size * 0.04)), line, font=f)[3]
        y += int(f.size * 0.93)
    y = max(y, bottom + 12)
    if e.get("attribution"):
        af = fit_font(lambda s: cond_font(tc, s), f"— {e['attribution']}", hw, 40)
        d.text((SW - m - 8, y), f"— {e['attribution']}", font=af, fill=YEL, anchor="ra")
        y += 52
    sf = cond_font(tc, 48)
    for line in wrap_px(e.get("subhead", ""), sf, hw)[:2]:
        d.text((hx, y), line, font=sf, fill=WHT)
        y += 52

    # The face to the bottom; inset and badge above the bottom title overlay.
    py = y + 16
    pw, ph = SW - 2 * m, SH - m - py
    photo = tc.get("_photos", {}).get(photo_name(tc, e.get("photo", "")))
    img.paste(face_crop(photo, WHOLE, (pw, ph)) if photo
              else face_crop(frame_path(e["frame"]), e["face"], (pw, ph), max_zoom=2.5), (m, py))
    iw, ih = 330, 296
    ix, iy = SW - m - iw - 22, max(py + 40, SH - m - ih - 380)
    d.rectangle((ix - 9, iy - 9, ix + iw + 9, iy + ih + 9), fill=WHT, outline=BLK, width=3)
    img.paste(face_crop(frame_path(e["inset_frame"]), e["inset_face"], (iw, ih), fill=0.72), (ix, iy))
    if tc["_channel"]:
        bf2, kf = cond_font(tc, 52), cond_font(tc, 24)
        bwid = int(bf2.getlength(tc["_channel"])) + 40
        bx, bb = m + 22, iy + ih
        d.rectangle((bx, bb - 104, bx + bwid, bb), fill=RED, outline=WHT, width=3)
        d.text((bx + bwid / 2, bb - 96), tc["badge_kicker"], font=kf, fill=YEL, anchor="ma")
        d.text((bx + bwid / 2, bb - 8), tc["_channel"], font=bf2, fill=WHT, anchor="mb")

    noise = Image.effect_noise((SW, SH), 20).convert("RGB")
    return save(Image.blend(img, noise, 0.04), dst, tc["max_bytes"])


def short_covers(stream_dir: Path, state: dict, tc_enq: dict, date: str, own_band: str, bleeper,
                 clip_ids: set[str] | None = None, short_keys: set[str] | None = None) -> list[dict]:
    """A vertical cover beside each Short (shorts/<name>.jpg) whose clip has an enquirer
    choice in thumbnails.json; clip_ids / short_keys limit which. Returns what was written."""
    shorts_path = stream_dir / "shorts.json"
    if not shorts_path.exists():
        return []
    by_clip = {e["id"]: (k, e) for k, e in state.items()
               if e.get("kind") == "clip" and e.get("style") == "enquirer"}
    out = []
    for skey, s in json.loads(shorts_path.read_text(encoding="utf-8")).items():
        cid = skey.split("_", 1)[1] if "_" in skey else skey  # v01_t20 -> t20
        video = stream_dir / s["file"]
        if (clip_ids is not None and cid not in clip_ids) or (short_keys is not None and skey not in short_keys):
            continue
        if cid not in by_clip or not video.exists():
            continue
        ckey, e = by_clip[cid]
        e = printable(e, bleeper)
        dst = video.with_suffix(".jpg")
        size = compose_vertical(stream_dir, ckey, e, cover_context(ckey, state, stream_dir, tc_enq, own_band),
                                tc_enq, date, stream_dir / f"{ckey}.mp4", dst)
        out.append({**e, "key": f"shorts/{video.stem}", "short": skey, "jpg": dst, "bytes": size})
    return out


def shorts_only(stream_dir: Path, cfg: dict, short_keys: set[str] | None = None,
                kitsu: bool = False) -> int:
    """Vertical covers for these Shorts (all if None), no model calls: `clipper shorts`
    right after it renders them, `clipper thumbs --shorts` for a stream's existing ones
    (kitsu: attach just those). 0 if the stream has no enquirer thumbnails yet."""
    cfg = for_stream(cfg, stream_dir)
    from .bleep import Bleeper

    tc = {**cfg["thumbnail"]}
    state_path = stream_dir / "thumbnails.json"
    if tc["style"] != "enquirer" or not state_path.exists():
        return 0
    tc["_photos"] = photos(tc)
    tc_enq, date, own_band = enquirer_setup(stream_dir, cfg, tc)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    done = short_covers(stream_dir, state, tc_enq, date, own_band,
                        Bleeper({**cfg["bleep"], "mode": "bleep"}), short_keys=short_keys)
    for r in done:
        print(f"  cover {r['jpg'].relative_to(stream_dir)}  {r['bytes'] / 1e3:.0f} KB  {r['text']}")
    if done and kitsu and tc["kitsu"] and (stream_dir / "review_state.json").exists():
        from . import review as kitsu_review
        kitsu_review.attach_thumbnails(stream_dir, cfg, keys=[r["short"] for r in done])
    return len(done)


def printable(e: dict, bleeper) -> dict:
    """The entry with its text made safe: a hand edit with a listed word falls back to
    the original headline (enquirer) or no text (classic)."""
    if not unsafe(e["text"], bleeper):
        return e
    if e.get("style") == "enquirer":
        print(f"  {e['id']}: text has a banned word; using the original headline")
        return {**e, "text": " ".join(e["headline"])}
    print(f"  {e['id']}: text {e['text']!r} has a banned word; printing none")
    return {**e, "text": ""}


def enquirer_setup(stream_dir: Path, cfg: dict, tc: dict) -> tuple[dict, str, str]:
    """(tc with the masthead and badge names, the cover date, the pre-show band or "")."""
    meta_path = stream_dir / "meta.json"
    smeta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    started = smeta.get("started_at", "")
    own_band = ""
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
              "_channel": (tc["badge_channel"] or cfg["scoring"]["channel_name"] or "").upper()}
    return tc_enq, date, own_band


# ---------- stage ----------

def asset_info(stream_dir: Path, x: dict, review: dict) -> dict:
    """Title as it will upload (Kitsu edit > metadata draft > cut title), hook, about."""
    draft = stream_dir / "metadata" / x["video"].parent.name / f"{x['video'].stem}.json"
    d = json.loads(draft.read_text(encoding="utf-8")) if draft.exists() else {}
    rel = x["video"].relative_to(stream_dir).as_posix()
    fields = next((r.get("fields") or {} for r in review.values()
                   if r.get("file", "").replace("\\", "/") == rel), {})
    return {"title": fields.get("YouTube title") or d.get("title") or x["title"],
            "hook": x.get("hook", ""), "playlist": d.get("playlist") or x.get("topic") or "",
            "about": (d.get("lead") or x.get("summary") or x.get("body") or "")[:600]}


def video_key(video: Path) -> str:
    """thumbnails.json key for a cut video (asset_key without loading the asset)."""
    return f"{video.parent.name}/{video.stem}"


def check_text(cfg: dict, text: str) -> str:
    """Reviewer text for a thumbnail, cleaned like the model's; SystemExit if it's empty
    or has a word the channel never prints."""
    from .bleep import Bleeper

    tc = cfg["thumbnail"]
    out = clean_text(text, tc["max_words"])
    if not out:
        raise SystemExit("thumbnail text is empty")
    if len(text.split()) > tc["max_words"]:
        raise SystemExit(f"thumbnail text is at most {tc['max_words']} words")
    bleeper = Bleeper({**cfg["bleep"], "mode": "bleep"})
    if unsafe(out, bleeper):
        raise SystemExit(f"thumbnail text {bleeper.mask(out)!r} has a word the channel never prints")
    return out


def set_texts(stream_dir: Path, cfg: dict, texts: dict[str, str]) -> dict[str, str]:
    """New text for thumbnails ({thumbnails.json key: text}, already check_text'ed), then
    re-render them (and re-attach them in Kitsu) without model calls. The highlight is
    kept only while its word is still in the text. Returns the keys changed."""
    state_path = stream_dir / "thumbnails.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    missing = [k for k in texts if k not in state]
    if missing:
        raise SystemExit(f"no thumbnail yet for {missing}; run clipper thumbs first")
    norm = lambda w: re.sub(r"\W", "", w.upper())  # as compose() matches the highlight
    changed = {}
    for key, text in texts.items():
        e = state[key]
        if e["text"] == text:
            continue
        e["text"] = text
        if e.get("highlight") and norm(e["highlight"]) not in {norm(w) for w in text.split()}:
            e["highlight"] = ""
        changed[key] = text
    if changed:
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        run(stream_dir, cfg, ids=[state[k]["id"] for k in changed], compose_only=True)
    return changed


def set_photos(stream_dir: Path, cfg: dict, ids: list[str], photo: str | None = None,
               teaser_photo: str | None = None, kitsu: bool = True) -> list[str]:
    """Put a [thumbnail] photos portrait on enquirer covers by hand ("" clears): `photo`
    as each one's main face, `teaser_photo` beside its teaser on the other covers. Then
    re-render the whole stream (teasers show on every cover) without model calls, and
    re-attach in Kitsu. Returns the keys changed."""
    tc = {**for_stream(cfg, stream_dir)["thumbnail"]}
    tc["_photos"] = photos(tc)
    state_path = stream_dir / "thumbnails.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    keys = [k for k, e in state.items() if e["id"] in ids]
    if missing := sorted(set(ids) - {state[k]["id"] for k in keys}):
        raise SystemExit(f"no thumbnail yet for {missing}; run clipper thumbs first")
    changed = []
    for field, name in (("photo", photo), ("teaser_photo", teaser_photo)):
        if name is None:
            continue
        on_file = photo_name(tc, name)
        if name and not on_file:
            raise SystemExit(f"no photo of {name!r} in [thumbnail] photos "
                             f"({tc['photos'] or 'unset'}): {', '.join(tc['_photos']) or 'none'}")
        for k in keys:
            if state[k].get("style") != "enquirer":
                raise SystemExit(f"{state[k]['id']}: photos are for enquirer covers only")
            if state[k].get(field, "") != on_file:
                if on_file:
                    state[k][field] = on_file
                else:
                    state[k].pop(field, None)
                changed.append(k)
    if changed:
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        run(stream_dir, cfg, compose_only=True, kitsu=kitsu)
    return sorted(set(changed))


def run(stream_dir: Path, cfg: dict, ids: list[str] | None = None, force: bool = False,
        compose_only: bool = False, kitsu: bool = True,
        feedback: dict[str, str] | None = None, missing: bool = False) -> list[dict]:
    """feedback: {asset id: what was wrong with its thumbnail}, for a re-pick (retakes).
    missing: only videos with no thumbnail choice yet; the others' covers (and their
    Shorts' covers) are not re-rendered."""
    cfg = for_stream(cfg, stream_dir)
    from .bleep import Bleeper

    tc = {**cfg["thumbnail"]}
    tc["_photos"] = photos(tc) if tc["style"] == "enquirer" else {}
    assets = [x for x in load_assets(stream_dir) if not ids or x["id"] in ids]
    if not assets:
        raise SystemExit("no cut assets" + (f" matching {ids}" if ids else "; run `clipper cut` first"))
    state_path = stream_dir / "thumbnails.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if missing:
        kept = len(assets)
        assets = [x for x in assets if asset_key(x) not in state]
        if not assets:
            print(f"all {kept} videos have a thumbnail")
            return []
        print(f"{len(assets)} videos without a thumbnail ({kept - len(assets)} kept)")
    review_path = stream_dir / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    # Banned words are always enforced on thumbnail text, whatever [bleep] mode is.
    bleeper = Bleeper({**cfg["bleep"], "mode": "bleep"})

    style = tc["style"]
    todo = [x for x in assets if force or asset_key(x) not in state
            or state[asset_key(x)].get("video_mtime") != x["video"].stat().st_mtime
            or state[asset_key(x)].get("style", "classic") != style]
    if compose_only:
        missing = [asset_key(x) for x in todo if asset_key(x) not in state]
        if missing:
            raise SystemExit(f"--compose-only: no choice yet for {missing}; run without it first")
        todo = []
    claude = None
    if todo:
        claude = Claude(cfg["scoring"]["workspace_id"])
        preflight(claude, tc["model"])
        prompts = Prompts(cfg)
        prompts.version = tc["enquirer_prompt_version"] if style == "enquirer" else tc["prompt_version"]
        prompts.vars.update(show_name=cfg["meta"]["show_name"] or "the show",
                            max_words=tc["max_words"], max_words_min=min(2, tc["max_words"]),
                            min_crop=tc["min_crop"])
        system = prompts.get("thumbnail_enquirer" if style == "enquirer" else "thumbnail")
        sig = load_signals(stream_dir) if (stream_dir / "signals.parquet").exists() else None

        def one(x: dict) -> tuple[str, dict]:
            t = time.time()
            note = (feedback or {}).get(x["id"], "")
            # A reviewer turned the last pick down: look through more of the video.
            ftc = {**tc, "even_frames": tc["even_frames"] * 3, "heat_frames": tc["heat_frames"] * 2,
                   "clip_frames": tc["clip_frames"] * 2} if note else tc
            fr = frames(x, sig, ftc, stream_dir / "thumbs" / asset_key(x))
            info = asset_info(stream_dir, x, review)
            choice = (pick_enquirer if style == "enquirer" else pick)(
                x, fr, info, tc, claude, system, bleeper, note)
            print(f"{x['id']:<22} {len(fr)} frames -> #{choice['frame']} at {choice['t']:.1f}s: "
                  f"{choice['text']} ({time.time() - t:.0f}s)", flush=True)
            return asset_key(x), {
                "kind": x["kind"], "id": x["id"], "video": x["video"].name,
                "video_mtime": x["video"].stat().st_mtime, "title": info["title"],
                "frames": [{**f, "path": f["path"].name} for f in fr], **choice,
                "style": style, "topic": info["playlist"], "duration_s": round(duration(x), 1),
                "model": tc["model"], "prompt_version": prompts.version,
                **({"feedback": note} if note else {})}

        with ThreadPoolExecutor(tc["concurrency"]) as pool:
            for key, entry in pool.map(one, todo):
                state[key] = entry
                state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    tc_enq, date, own_band = enquirer_setup(stream_dir, cfg, tc)
    results = []
    for x in assets:
        e = printable(state[asset_key(x)], bleeper)
        frame = stream_dir / "thumbs" / asset_key(x) / e["frames"][e["frame"] - 1]["path"]
        if not frame.exists():  # edited "frame"/"t" by hand, or candidates cleaned up
            frame.parent.mkdir(parents=True, exist_ok=True)
            grab(x["video"], e["t"], frame)
        if e.get("style") == "enquirer":
            size = compose_enquirer(stream_dir, asset_key(x), e,
                                    cover_context(asset_key(x), state, stream_dir, tc, own_band),
                                    tc_enq, date, x["video"], x["video"].with_suffix(".jpg"))
        else:
            size = compose(frame, e, tc, x["video"].with_suffix(".jpg"))
        results.append({"key": asset_key(x), "jpg": x["video"].with_suffix(".jpg"), "bytes": size, **e})
    # Shorts get a vertical cover from their clip's choice (no model calls).
    results += short_covers(stream_dir, state, tc_enq, date, own_band, bleeper,
                            clip_ids={x["id"] for x in assets if x["kind"] == "clip"})
    print(f"{len(results)} thumbnails ({len(todo)} picked"
          + (f", cost ${claude.cost:.3f}" if claude else "") + ")")
    for r in results:
        print(f"  {r['jpg'].relative_to(stream_dir)}  {r['bytes'] / 1e3:.0f} KB  {r['text']}")

    if kitsu and tc["kitsu"] and (stream_dir / "review_state.json").exists():
        from . import review as kitsu_review
        kitsu_review.attach_thumbnails(stream_dir, cfg)
    return results
