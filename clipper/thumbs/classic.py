"""The classic cover ([thumbnail] style = "classic"): the chosen frame cropped, graded and
shaded, the text big in the [thumbnail] brand style (font, colors, outline, shadow, badge
or logo, stripe), under max_bytes."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from ..draw import color, font_at_cap, font_path, line_breaks, save
from .pick import clamp_crop

W, H = 1280, 720


def fit_text(words: list[str], tc: dict, path: str, max_w: int) -> tuple[list[list[str]], ImageFont.FreeTypeFont]:
    """The line breaks that allow the biggest type (capped at text_height); ties go to fewer
    lines, then to the most even line lengths."""
    best = None
    for lines in line_breaks(words, tc["max_lines"]):
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
