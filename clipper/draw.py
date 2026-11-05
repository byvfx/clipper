"""Pillow helpers for covers: colors, fonts, fitting text to a width, italic slanted type,
cropping around a face box, and saving a JPEG under a size limit. Used by the thumbnail
stage (clipper/thumbs/); nothing here knows a cover's layout."""

from __future__ import annotations

import io
import os
import sys
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .errors import ClipperError


def color(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


# ---------- fonts ----------
# tc is a [thumbnail]-like table: font, fonts, condensed_fonts, condensed_variation.

def font_dirs() -> list[Path]:
    """Where this OS keeps fonts."""
    if sys.platform == "win32":
        return [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    if sys.platform == "darwin":
        return [Path("/Library/Fonts"), Path("/System/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]


@cache
def installed_fonts() -> dict[str, Path]:
    """This machine's font files by lower-case file name."""
    return {f.name.lower(): f for d in font_dirs() if d.exists() for f in sorted(d.rglob("*"))
            if f.suffix.lower() in (".ttf", ".otf", ".ttc")}


def first_font(paths: list[str]) -> str | None:
    """The first of paths that exists here, as given or as an installed font with its file
    name. The defaults name Windows paths first; a Linux machine (the NAS containers) that has
    the same files in a font folder (/usr/local/share/fonts) finds them by name."""
    for p in filter(None, paths):
        if Path(p).exists():
            return str(p)
        if hit := installed_fonts().get(Path(p.replace("\\", "/")).name.lower()):
            return str(hit)
    return None


def font_path(tc: dict, key: str = "font") -> str:
    p = first_font([tc.get(key) or tc["font"], *tc["fonts"]])
    if not p:
        raise ClipperError("thumbnail: no font found; set [thumbnail] font to a .ttf path")
    return p


def font_at_cap(path: str, cap: float) -> ImageFont.FreeTypeFont:
    probe = ImageFont.truetype(path, 200)
    _, t, _, b = probe.getbbox("H")
    return ImageFont.truetype(path, max(8, int(200 * cap / (b - t))))


def cond_font(tc: dict, size: int) -> ImageFont.FreeTypeFont:
    """The condensed face for teasers, subheads and labels; the headline font if none."""
    p = first_font(tc["condensed_fonts"])
    if not p:
        return ImageFont.truetype(font_path(tc), size)
    f = ImageFont.truetype(p, size)
    try:
        f.set_variation_by_name(tc["condensed_variation"])
    except (OSError, ValueError, AttributeError):
        pass  # not a variable font, or no such instance
    return f


def head_font(tc: dict, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(tc), size)


# ---------- fitting text ----------

def line_breaks(words: list[str], max_lines: int) -> list[list[list[str]]]:
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


def italic(text: str, f: ImageFont.FreeTypeFont, fill, shadow=(8, 8, 8), shear: float = 0.22,
           pad: int = 12) -> Image.Image:
    x0, y0, x1, y1 = f.getbbox(text)
    h = y1 - y0 + pad * 2
    im = Image.new("RGBA", (x1 - x0 + pad * 2 + int(h * shear), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((pad - x0 + 5, pad - y0 + 5), text, font=f, fill=shadow)
    d.text((pad - x0, pad - y0), text, font=f, fill=fill)
    return im.transform(im.size, Image.AFFINE, (1, shear, -shear * h * 0.5, 0, 1, 0),
                        resample=Image.BICUBIC)


# ---------- face crops ----------

def clamp_box(b: dict) -> dict:
    x = min(max(float(b.get("x", 0)), 0.0), 0.98)
    y = min(max(float(b.get("y", 0)), 0.0), 0.98)
    return {"x": round(x, 3), "y": round(y, 3),
            "width": round(min(max(float(b.get("width", 0.3)), 0.02), 1 - x), 3),
            "height": round(min(max(float(b.get("height", 0.4)), 0.02), 1 - y), 3)}


def crop_window(fw: int, fh: int, b: dict, size: tuple[int, int], fill: float, max_zoom: float,
                ax: float = 0.5, ay: float = 0.45) -> tuple[float, float, float, float]:
    """The source window (x0, y0, w, h) that puts face box b's center at (ax, ay) of the
    output with the head filling `fill` of its height, kept inside the source."""
    tw, th = size
    cx, cy = (b["x"] + b["width"] / 2) * fw, (b["y"] + b["height"] / 2) * fh
    h = min(fh, max(b["height"] * fh / fill, th / max_zoom))
    w = h * tw / th
    if w > fw:
        w, h = fw, fw * th / tw
    return min(max(cx - w * ax, 0), fw - w), min(max(cy - h * ay, 0), fh - h), w, h


def clear_window(fw: int, fh: int, b: dict, size: tuple[int, int], fill: float, max_zoom: float,
                 avoid: list[tuple[int, int, int, int]]) -> tuple[float, float, float, float]:
    """The crop window that keeps the head out of the avoid boxes (output pixels: the
    inset, the badge) and inside the photo: the biggest head that clears them, moved as
    little as it can be; failing that, the one least covered."""
    tw, th = size
    best = None
    for f in (fill, fill * 0.94, fill * 0.88, fill * 0.82, fill * 0.76, fill * 0.7):
        for dx in (0, -0.06, 0.06, -0.12, 0.12, -0.18, 0.18):
            for dy in (0, -0.05, -0.1, -0.15):
                x0, y0, w, _ = win = crop_window(fw, fh, b, size, f, max_zoom, 0.5 + dx, 0.45 + dy)
                s = tw / w
                fx0, fy0 = (b["x"] * fw - x0) * s, (b["y"] * fh - y0) * s
                fx1, fy1 = fx0 + b["width"] * fw * s, fy0 + b["height"] * fh * s
                area = (fx1 - fx0) * (fy1 - fy0)
                covered = area - max(0, min(fx1, tw) - max(fx0, 0)) * max(0, min(fy1, th) - max(fy0, 0))
                for ax0, ay0, ax1, ay1 in avoid:
                    covered += max(0, min(fx1, ax1) - max(fx0, ax0)) * max(0, min(fy1, ay1) - max(fy0, ay0))
                rank = (round(covered / area, 2), -f, abs(dx) + abs(dy))
                if best is None or rank < best[0]:
                    best = (rank, win)
    return best[1]


def face_crop(path: Path, face: dict, size: tuple[int, int], fill: float = 0.72,
              max_zoom: float = 2.0, avoid: list[tuple[int, int, int, int]] = ()) -> Image.Image:
    """Crop around a face box so the head fills `fill` of the photo's height, the face a
    little above center, then cover-fit to size, enlarging the source at most max_zoom.
    With avoid (boxes drawn over the photo later), the head is moved or made smaller to
    stay clear of them."""
    img = Image.open(path).convert("RGB")
    fw, fh = img.size
    b = clamp_box(face)
    x0, y0, w, h = (clear_window(fw, fh, b, size, fill, max_zoom, list(avoid)) if avoid
                    else crop_window(fw, fh, b, size, fill, max_zoom))
    img = img.crop((round(x0), round(y0), round(x0 + w), round(y0 + h))).resize(size, Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Color(img).enhance(1.25)
    return ImageEnhance.Sharpness(img).enhance(1.4)


# ---------- output ----------

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
