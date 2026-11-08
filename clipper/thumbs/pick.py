"""The model's pick: one vision call per asset over its candidate frames. Classic: the
frame with the strongest readable expression or moment, 2-4 words of text, the word to
highlight, top/bottom placement and a crop. Enquirer: the main face and a reaction inset,
the headline and the cover's other lines, and maybe a [thumbnail] photos portrait (and, from
the cast library, which of the person's labelled photos). Frames from faces.json say whose
face was found; when the main face is one, its found box is used.
Text that comes back empty or with a word the channel never prints is asked for once more
(prompts/thumbnail_retry_<v>.md); a reviewer's note on a retake goes in with the request
(prompts/thumbnail_feedback_<v>.md)."""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from PIL import Image, ImageDraw

from ..draw import clamp_box, line_breaks
from ..llm import Claude
from ..meta import duration
from ..prompts import Prompts
from .frames import photo_name

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


def enquirer_lines(text: str, max_lines: int = 3) -> list[str]:
    """Split headline text into up to max_lines lines of similar length."""
    words = text.upper().split()
    if len(words) <= 1:
        return words
    best = None
    for k in range(1, min(max_lines, len(words)) + 1):
        for lines in line_breaks(words, k):
            lens = [len(" ".join(l)) for l in lines]
            score = (max(lens), -k)  # shortest longest line, then more lines
            if best is None or score < best[0]:
                best = (score, [" ".join(l) for l in lines])
    return best[1]


def clamp_crop(c: dict, min_width: float) -> dict:
    w = min(max(float(c.get("width", 1)), min_width), 1.0)
    return {"x": round(min(max(float(c.get("x", 0)), 0), 1 - w), 3),
            "y": round(min(max(float(c.get("y", 0)), 0), 1 - w), 3), "width": round(w, 3)}


# ---------- the request ----------

def describe(x: dict, info: dict, n: int, style: str) -> str:
    """What the model is told about the video, after its frames."""
    return (f"Video: {x['kind']}, {duration(x) / 60:.1f} min\nTitle: {info['title']}\n"
            + (f"Hook: {info['hook']}\n" if info["hook"] else "")
            + (f"Topic: {x['topic']}\n" if x["topic"] else "")
            + (f"People: {', '.join(x['figures'])}\n" if x["figures"] else "")
            + (f"About: {info['about']}\n" if info["about"] else "")
            + (f"Frames are numbered 1-{n}." if style == "enquirer" else f"Pick one of frames 1-{n}."))


def feedback_text(retry: Prompts, note: str) -> str:
    """A reviewer's note on the last thumbnail, appended to the request; "" without one."""
    return "\n" + retry.get("thumbnail_feedback", note=note).strip() if note else ""


def request(x: dict, fr: list[dict], info: dict, tc: dict, style: str, feedback: str,
            image=b64_jpeg) -> tuple[list[dict], dict]:
    """(the user content, the output schema): each frame with its time, what the video is,
    and for enquirer covers the photos on file the model may choose."""
    content: list[dict] = []
    for i, f in enumerate(fr, 1):
        found = sorted({x["person"] for x in f.get("faces", [])})
        label = f"Frame {i} ({f['t'] // 60:.0f}:{f['t'] % 60:04.1f})" + (
            f", faces found: {', '.join(found)}" if found else "")
        content += [{"type": "text", "text": label},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": image(f["path"])}}]
    content.append({"type": "text", "text": describe(x, info, len(fr), style) + feedback})
    if style != "enquirer":
        return content, PICK_SCHEMA
    schema, names = ENQ_SCHEMA, list(tc.get("_photos", {}))
    if names:
        labels = tc.get("_photo_labels") or {}
        listed = [f"{n} ({', '.join(labels[n])})" if labels.get(n) else n for n in names]
        content.append({"type": "text", "text": f"Photos on file: {', '.join(listed)}."})
        pick_one = {"type": "string", "enum": ["", *names]}
        extra = {"photo": pick_one, "teaser_photo": pick_one}
        if every := sorted({lb for v in labels.values() for lb in v}):  # #41: which of their photos
            pick_label = {"type": "string", "enum": ["", *every]}
            extra |= {"photo_label": pick_label, "teaser_photo_label": pick_label}
        schema = {**ENQ_SCHEMA, "properties": {**ENQ_SCHEMA["properties"], **extra},
                  "required": [*ENQ_SCHEMA["required"], *extra]}
    return content, schema


# ---------- each style's answer ----------
# clean: tidy the model's text in place, True if it can be printed; fallback: the choice
# when the model refused or both tries failed; finish: frames and boxes clamped to range.

def clean_classic(out: dict, tc: dict, bleeper) -> bool:
    out["text"] = clean_text(out["text"], tc["max_words"])
    return bool(out["text"]) and not unsafe(out["text"], bleeper)


def clean_enquirer(out: dict, tc: dict, bleeper) -> bool:
    out["headline"] = [clean_text(l, 4).upper() for l in out["headline"] if clean_text(l, 4)][:3]
    if len(out["headline"]) == 1:
        out["headline"] = enquirer_lines(out["headline"][0])
    for k in ("attribution", "band_kicker", "band_headline"):
        out[k] = clean_text(out[k], 8).upper() if k != "band_kicker" else clean_text(out[k], 5)
    out["subhead"] = clean_text(out["subhead"], 14)
    out["teaser"] = clean_text(out["teaser"], 6)
    flat = " ".join(" ".join(v) if isinstance(v, list) else v for v in (out[k] for k in ENQ_TEXT))
    return bool(out["headline"]) and not unsafe(flat, bleeper)


def fallback_classic(x: dict, info: dict, why: str) -> dict:
    return {"frame": 1, "text": fallback_text(x), "highlight": "", "position": "bottom",
            "crop": {"x": 0, "y": 0, "width": 1}, "why": why}


def fallback_enquirer(x: dict, info: dict, why: str) -> dict:
    name = fallback_text(x) or "EXCLUSIVE"
    return {"main_person": "", "frame": 1, "face": {"x": 0.3, "y": 0.15, "width": 0.4, "height": 0.7},
            "headline": enquirer_lines(name + "!"), "attribution": "SOURCES SAY",
            "subhead": info["title"][:80], "inset_frame": 1,
            "inset_face": {"x": 0.3, "y": 0.15, "width": 0.4, "height": 0.7},
            "teaser": info["title"][:30], "band_kicker": "Exclusive:",
            "band_headline": name + "!", "why": why}


def finish_classic(out: dict, fr: list[dict], tc: dict) -> dict:
    out["frame"] = min(max(int(out["frame"]), 1), len(fr))
    out["t"] = fr[out["frame"] - 1]["t"]
    out["crop"] = clamp_crop(out["crop"], tc["min_crop"])
    return out


def finish_enquirer(out: dict, fr: list[dict], tc: dict) -> dict:
    for f_key, b_key in (("frame", "face"), ("inset_frame", "inset_face")):
        out[f_key] = min(max(int(out[f_key]), 1), len(fr))
        out[b_key] = clamp_box(out[b_key])
    labels = tc.get("_photo_labels") or {}
    for k in ("photo", "teaser_photo"):  # kept only when set, so older entries read the same
        name, label = photo_name(tc, out.pop(k, "")), out.pop(f"{k}_label", "")
        if name:
            out[k] = name
            if label in labels.get(name, []):  # a label that person's photos have
                out[f"{k}_label"] = label
    found = found_face(fr[out["frame"] - 1], out["main_person"])
    if found:  # the face recognition found, not the model's guess at the box
        out["face"], out["face_found"] = found, True
    out["t"] = fr[out["frame"] - 1]["t"]
    out["text"] = " ".join(out["headline"])  # what reviewers edit (Kitsu, Discord)
    return out


def found_face(f: dict, person: str) -> dict | None:
    """The head box faces.json found for person in this candidate frame, if any (the biggest)."""
    who = (person or "").strip().lower()
    boxes = [x["face"] for x in f.get("faces", []) if who and x["person"].lower() == who]
    return max(boxes, key=lambda b: b["height"], default=None)


# style: (clean, the retry prompt, fallback, finish)
STYLES = {"classic": (clean_classic, "thumbnail_retry", fallback_classic, finish_classic),
          "enquirer": (clean_enquirer, "thumbnail_enquirer_retry", fallback_enquirer, finish_enquirer)}


def pick(x: dict, fr: list[dict], info: dict, tc: dict, claude: Claude, system: str, bleeper,
         retry: Prompts, style: str = "classic", note: str = "") -> dict:
    """The model's choice for one asset in this cover style; note: what a reviewer said
    was wrong with the last one. retry: Prompts at [thumbnail] retry_prompt_version."""
    clean, again, fallback, finish = STYLES["enquirer" if style == "enquirer" else "classic"]
    content, schema = request(x, fr, info, tc, style, feedback_text(retry, note))
    out, meta = None, {}
    for _attempt in range(2):
        out, meta = claude.json_call(tc["model"], system, content, schema, tc["effort"],
                                     fallback=True)
        if out is None:
            break
        if clean(out, tc, bleeper):
            break
        content = content + [{"type": "text", "text": retry.get(again, text=out.get("text", "")).strip()}]
        out = None
    if out is None:
        out = fallback(x, info, f"fallback ({meta.get('refusal') or 'no usable text'})")
    return finish(out, fr, tc)
