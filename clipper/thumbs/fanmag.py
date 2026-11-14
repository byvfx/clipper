"""Fan-magazine thumbnails ([thumbnail] style = "fanmag"): the Sloth Beat look.

A late-80s teen fan magazine ('Teen, Bop) in 16:9: flat colour panels cut on diagonals with
two-ink crossover halftones, the striped bubble masthead with the mascot hanging in place of
one letter, a hand-cut paper ribbon with a tease across the top, the cover's subject as a
scissor-cut paper cut-out on the right, smaller cut-outs with red name bars down the left,
and condensed coverlines with bullet-dot sub-lines. Mockups and references:
studio/mockups/sloth_beat/ (v7 is this layout).

The cover is an HTML page rendered by a Chromium-based browser in headless mode (Edge on
Windows, Chrome or Chromium elsewhere; [thumbnail] browser, else auto-detected). No
Playwright: one `--screenshot` call per cover. Fonts and the mascot ship in
clipper/assets/fanmag/ (all OFL).

The model's choice is the enquirer one (clipper/prompts/thumbnail_fanmag_<v>.md, same fields):
frame + face -> the main cut-out, headline -> the big coverline, subhead -> the script
line, inset -> a reaction sticker, band_kicker/band_headline -> the ribbon, and the other
videos' teasers -> the side coverlines with their faces as stickers. Coverlines live in
the left column and the main cut-out on the right, so text never sits on a face; the
one text box allowed over the photo has a solid background.

Cut-outs: a person matte from torchvision's DeepLabV3 when torchvision is installed (the
gpu extra), else the face crop as a rectangle; either way the edge gets a hand-cut paper
margin (dilate, trace as a few dozen straight scissor strokes with a little wobble, fill
with off-white paper).
"""
from __future__ import annotations

import colorsys
import hashlib
import html
import importlib.util
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from PIL.PngImagePlugin import PngInfo

from ..draw import clamp_box, save
from ..errors import ClipperError
from .enquirer import WHOLE, frame_file
from .frames import photo_file
from .pick import enquirer_lines

W, H = 1280, 720
ASSETS = Path(__file__).parent.parent / "assets" / "fanmag"
INK = "#1a1a1a"
PAPER = (247, 244, 236, 255)

# Palettes (ground, column, wedge, accent), one per cover by its id. The column is never
# yellow: the script line on it is.
PALETTES = [("#8fe3b0", "#ff7ac3", "#ffe066", "#c9a9ff"),
            ("#c9a9ff", "#7fd8ff", "#ff7ac3", "#ffe066"),
            ("#ffe066", "#c9a9ff", "#8fe3b0", "#ff7ac3"),
            ("#ff9a76", "#7fd8ff", "#c9a9ff", "#ffe066"),
            ("#7fd8ff", "#8fe3b0", "#ff7ac3", "#ffe066")]
BULLETS = ["#ff2d8a", "#2fd3ff", "#ffe600"]

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
CHROME_FLAGS = ("--disable-background-networking", "--disable-component-update", "--disable-sync",
                "--no-default-browser-check")
BROWSER_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "microsoft-edge", "msedge", "chrome")


# ---------- browser ----------

def find_browser(tc: dict) -> str:
    """The Chromium-based browser to render with: [thumbnail] browser, else the first of
    the usual install paths / PATH names that exists; "" if none."""
    if tc.get("browser"):
        b = tc["browser"]
        return b if Path(b).exists() else shutil.which(b) or ""
    for p in BROWSERS:
        if Path(p).exists():
            return p
    for n in BROWSER_NAMES:
        if shutil.which(n):
            return shutil.which(n)
    for p in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome")):
        return str(p)
    return ""


def screenshot(browser: str, page: Path, dst: Path, size: tuple[int, int] = (W, H)) -> None:
    """Render `page` (a file) to `dst` (PNG) at `size` with a headless Chromium."""
    with tempfile.TemporaryDirectory(prefix="clipper-fanmag-") as prof:
        cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
               "--disable-extensions", f"--window-size={size[0]},{size[1]}", "--virtual-time-budget=4000",
               f"--screenshot={dst.resolve()}"]
        # Windows: a running Edge/Chrome would take over a headless call that shares its
        # profile, so use a throwaway one. Elsewhere a profile dir makes some Chromium builds
        # hang on first-run work, and nothing else is running, so none.
        if sys.platform == "win32":
            cmd.append(f"--user-data-dir={prof}")
        elif os.geteuid() == 0:
            cmd.append("--no-sandbox")
        cmd += [*CHROME_FLAGS, page.resolve().as_uri()]
        dst.unlink(missing_ok=True)  # the last render's: never mistaken for this one's
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        except subprocess.TimeoutExpired:
            raise ClipperError(f"browser screenshot timed out after 120 s ({browser})") from None
        if not dst.exists():
            raise ClipperError(f"browser screenshot failed ({browser}): {r.stderr.strip()[-300:]}")


# ---------- geometry: hand-cut edges, halftones, panels ----------

def shade(hexc: str, k: float) -> str:
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, l * k)), s)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def jag(p1, p2, rnd: random.Random, seg: float = 46, amp: float = 7) -> list[tuple[float, float]]:
    """A straight edge cut by hand: short straight segments with a little sideways wobble."""
    (x1, y1), (x2, y2) = p1, p2
    length = math.hypot(x2 - x1, y2 - y1)
    n = max(2, int(length / seg))
    nx, ny = -(y2 - y1) / length, (x2 - x1) / length
    out = []
    for i in range(1, n):
        t, w = i / n, rnd.uniform(-amp, amp)
        out.append((x1 + (x2 - x1) * t + nx * w, y1 + (y2 - y1) * t + ny * w))
    return out


def poly(points) -> str:
    return " ".join(f"{x:.0f},{y:.0f}" for x, y in points)


def halftone(pid: str, polygon: str, ink_a: str, ink_b: str, x0, y0, x1, y1, axis: str = "x",
             pitch: float = 12, reverse: bool = False, lo: float = 0.04, hi: float = 0.74) -> str:
    """Two-ink crossover halftone clipped to a polygon: ink_b under ink_a dots on a 45-degree
    screen whose radius grows past the merge point, so one end is solid ink_b with tiny
    ink_a dots, the middle a checker, the far end solid ink_a with tiny ink_b holes."""
    out = [f'<clipPath id="{pid}"><polygon points="{polygon}"/></clipPath>',
           f'<g clip-path="url(#{pid})"><polygon points="{polygon}" fill="{ink_b}"/><g fill="{ink_a}">']
    cx, cy, q = (x0 + x1) / 2, (y0 + y1) / 2, pitch / math.sqrt(2)
    n = int(max(x1 - x0, y1 - y0) / q) + 4
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            x, y = cx + (i - j) * q, cy + (i + j) * q
            if not (x0 - pitch <= x <= x1 + pitch and y0 - pitch <= y <= y1 + pitch):
                continue
            t = (x - x0) / max(1, x1 - x0) if axis == "x" else (y - y0) / max(1, y1 - y0)
            t = min(1.0, max(0.0, 1 - t if reverse else t))
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{pitch * (lo + (hi - lo) * t):.2f}"/>')
    out.append("</g></g>")
    return "".join(out)


def panels(palette: tuple[str, str, str, str], seed: str) -> str:
    """The background SVG: gradient ground, a left column and two wedges cut by hand, the
    column tone-on-tone halftoned behind the masthead, the wedges as two-ink crossovers,
    and a torn paper strip along the bottom under the sticker row."""
    a, b, c, d = palette
    rnd = random.Random(seed)
    s0, s1, s2, s3 = rnd.randint(560, 700), rnd.randint(380, 460), rnd.randint(280, 320), rnd.randint(240, 300)
    col = poly([(0, 0), (560, 0)] + jag((560, 0), (470, 720), rnd) + [(470, 720), (0, 720)])
    wedge = poly([(900, 0), (1280, 0), (1280, s3)] + jag((1280, s3), (900, 0), rnd))
    wedge2 = poly([(s0, 720)] + jag((s0, 720), (1280, s1), rnd) + [(1280, s1), (1280, 720)])
    tri = poly([(0, s2)] + jag((0, s2), (330, s2 - 60), rnd) + [(330, s2 - 60)]
               + jag((330, s2 - 60), (0, s2 + 200), rnd) + [(0, s2 + 200)])
    strip = poly([(380, 740)] + jag((380, 740), (1300, 590), rnd, 40, 5) + [(1300, 590), (1300, 640)]
                 + jag((1300, 640), (380, 790), rnd, 40, 5) + [(380, 790)])
    defs = (f'<linearGradient id="ga" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{shade(a, 1.25)}"/>'
            f'<stop offset="1" stop-color="{shade(a, 0.8)}"/></linearGradient>')
    return (f'<svg class="panels" viewBox="0 0 {W} {H}" preserveAspectRatio="none"><defs>{defs}</defs>'
            f'<rect width="{W}" height="{H}" fill="url(#ga)"/>'
            + halftone("ht1", col, shade(b, .62), b, 0, 0, 560, 222, "y", 13, True, 0.0, 0.74)
            + halftone("ht3", wedge2, d, c, s0, s1, 1280, 720, "x", 12)
            + f'<polygon points="{strip}" fill="#f7f4ec" opacity=".9"/>'
            f'<polyline points="{strip}" fill="none" stroke="#bfbbb2" stroke-width="2"/>'
            f'<polygon points="{tri}" fill="{d}"/>'
            + halftone("ht2", wedge, c, d, 900, 0, 1280, s3, "y", 12)
            + "</svg>")


def ribbon_cut(seed: str) -> str:
    rnd = random.Random("rib" + seed)
    pts = [(0, 0), (1320, 0), (1320, 50)] + jag((1320, 50), (0, 58), rnd, 38, 5) + [(0, 58)]
    return ", ".join(f"{x:.0f}px {y:.0f}px" for x, y in pts)


# ---------- cut-outs ----------

_matte_model = None


def person_matte(img: Image.Image, face: tuple[int, int, int, int] | None = None) -> Image.Image | None:
    """A soft alpha mask of the person in `img` (DeepLabV3, torchvision), or None when
    torchvision is not installed or the matte doesn't hold (then the cut-out is the face
    crop's rectangle). face: the head's box in img's pixels, to keep the right figure."""
    global _matte_model
    try:
        import torch
        from torchvision.models.segmentation import (
            DeepLabV3_ResNet50_Weights,
            deeplabv3_resnet50,
        )
    except ImportError:
        return None
    if _matte_model is None:
        weights = DeepLabV3_ResNet50_Weights.DEFAULT
        _matte_model = (deeplabv3_resnet50(weights=weights).eval(), weights.transforms())
    model, transform = _matte_model
    with torch.no_grad():
        out = model(transform(img.convert("RGB")).unsqueeze(0))["out"][0]
    person = torch.softmax(out, 0)[15]          # VOC class 15 = person
    prob = Image.fromarray((person.numpy() * 255).astype("uint8")).resize(img.size, Image.BILINEAR)
    return clean_matte(prob, face)


def clean_matte(prob: Image.Image, face: tuple[int, int, int, int] | None = None,
                cover: tuple[float, float] = (0.08, 0.92)) -> Image.Image | None:
    """The person-probability map as a cut-out mask: thresholded, only the figure that holds
    the middle of the face (the largest without a face), holes filled (a mic or a hand in
    front of the chest), softened. None when it doesn't hold: no figure at the face, or the
    figure covers less or more of the crop than `cover` (a webcam box the matte reads as a
    person, a dark room where it finds nothing)."""
    import cv2
    import numpy as np

    hard = (np.asarray(prob) > 110).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(hard, connectivity=8)
    if n <= 1:
        return None
    if face:
        x0, y0, x1, y1 = face
        dx, dy = (x1 - x0) // 4, (y1 - y0) // 4   # the middle half: the box has hair and background
        mid = labels[max(0, y0 + dy):max(1, y1 - dy), max(0, x0 + dx):max(1, x1 - dx)]
        counts = np.bincount(mid.ravel(), minlength=n)
        counts[0] = 0
        keep = int(counts.argmax())
        if not mid.size or counts[keep] < 0.3 * mid.size:
            return None
    else:
        keep = 1 + int(stats[1:, cv2.CC_STAT_AREA].argmax())
    m = (labels == keep).astype(np.uint8)
    flood = np.pad(m, 1)
    cv2.floodFill(flood, np.zeros((flood.shape[0] + 2, flood.shape[1] + 2), np.uint8), (0, 0), 1)
    m |= (flood[1:-1, 1:-1] == 0).astype(np.uint8)
    if not cover[0] <= m.mean() <= cover[1]:
        return None
    return Image.fromarray(m * 255).filter(ImageFilter.GaussianBlur(1.2))


def scissor(img: Image.Image, seed: int, bleed: bool = False) -> Image.Image:
    """Cut the photo's own edges by hand where the figure runs into them (a body the crop cuts
    at the shoulders, or the whole rectangle when there is no matte): each such side becomes
    a slightly slanted line of short scissor strokes inside the edge, never one long straight
    line. bleed keeps the bottom edge straight (it runs off the cover)."""
    import numpy as np

    rnd = random.Random(f"scissor{seed}")
    w, h = img.size
    a = np.asarray(img.split()[3]) > 60
    if not a.any():
        return img
    ins = max(5, round(min(w, h) * 0.035))

    def side(n: int) -> float:  # how far in this side's cut runs at one end
        return rnd.uniform(ins * 0.6, ins * 2.6)

    left, right, top = a[:, 0].any(), a[:, -1].any(), a[0, :].any()
    bottom = a[-1, :].any() and not bleed
    # Corners (x, y) of the cut quadrilateral; an untouched side stays on the edge.
    tl = (side(0) if left else 0, side(0) if top else 0)
    tr = (w - (side(0) if right else 0), side(0) if top else 0)
    br = (w - (side(0) if right else 0), h - (side(0) if bottom else 0))
    bl = (side(0) if left else 0, h - (side(0) if bottom else 0))

    def edge(p, q, cut: bool) -> list[tuple[float, float]]:
        if not cut:
            return [p]
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        return [p, *jag(p, q, rnd, seg=max(36, length / 7), amp=max(4, length / 70))]

    pts = edge(tl, tr, top) + edge(tr, br, right) + edge(br, bl, bottom) + edge(bl, tl, left)
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    out = img.copy()
    out.putalpha(Image.fromarray(np.minimum(np.asarray(img.split()[3]), np.asarray(m))))
    return out


def handcut(img: Image.Image, margin: int = 26, strokes: int = 46, wobble: float = 6.0,
            seed: int = 1, bleed: bool = False) -> Image.Image:
    """Put a hand-cut paper margin around an RGBA cut-out: grow the silhouette, trace it as
    `strokes` straight scissor cuts from the figure's centre (so concavities like the neck
    are skipped, as scissors do), fill with paper, composite the photo on top. bleed: when
    the figure reaches the photo's bottom edge (a body the frame cuts off), end the cut-out
    there, straight, so it runs off the bottom of the cover instead of floating."""
    rnd = random.Random(seed)
    pad = margin + 24
    canvas = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    canvas.paste(img, (pad, pad))
    a = canvas.split()[3].point(lambda v: 255 if v > 60 else 0)
    grown = a.filter(ImageFilter.MaxFilter(2 * margin + 1))
    px, (gw, gh) = grown.load(), grown.size
    bbox = a.getbbox() or (0, 0, gw, gh)
    cx, cy = (bbox[0] + bbox[2]) / 2, bbox[1] + (bbox[3] - bbox[1]) * 0.45
    pts = []
    for i in range(strokes):
        ang = 2 * math.pi * i / strokes + rnd.uniform(-0.02, 0.02)
        dx, dy = math.cos(ang), math.sin(ang)
        far = r = 0.0
        while True:
            x, y = int(cx + dx * r), int(cy + dy * r)
            if x < 0 or y < 0 or x >= gw or y >= gh:
                break
            if px[x, y]:
                far = r
            r += 1.0
        far += rnd.uniform(-wobble, wobble)
        pts.append((cx + dx * far, cy + dy * far))
    paper = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(paper)
    d.polygon(pts, fill=PAPER)
    d.line(pts + [pts[0]], fill=(190, 186, 178, 255), width=2)
    out = Image.alpha_composite(paper, canvas)
    left, top, right, bottom = out.getbbox()
    if bleed and bbox[3] >= pad + img.height:      # the figure touches the photo's bottom row
        bottom = pad + img.height
    return out.crop((left, top, right, bottom))


def head_box(img: Image.Image, face: dict, size: int, head_only: bool = False
             ) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Head and shoulders around `face` (fractions of img), scaled to `size` px tall: the face
    box widened and extended down to the chest; head_only: just the head and a little neck.
    Returns the crop and the face's box in it."""
    fw, fh = img.size
    b = clamp_box(face)
    cx = (b["x"] + b["width"] / 2) * fw
    top = max(0, (b["y"] - b["height"] * (0.2 if head_only else 0.35)) * fh)
    bottom = min(fh, (b["y"] + b["height"] * (1.25 if head_only else 2.6)) * fh)
    half = min(fw / 2, b["width"] * fw * 0.75 if head_only
               else max(b["width"] * fw * 1.35, (bottom - top) * 0.42))
    box = (round(max(0, cx - half)), round(top), round(min(fw, cx + half)), round(bottom))
    crop = img.crop(box)
    k = size / crop.height
    crop = crop.resize((max(1, round(crop.width * k)), size), Image.LANCZOS)
    head = (b["x"] * fw - box[0], b["y"] * fh - box[1], (b["x"] + b["width"]) * fw - box[0],
            (b["y"] + b["height"]) * fh - box[1])
    x0, y0, x1, y1 = (max(0, round(v * k)) for v in head)
    return crop, (x0, y0, x1, y1)


MESSY = 0.4  # a head-and-shoulders matte covering more of its crop than this has hands, a chair...


def cutout(src: Path, face: dict, size: int, seed: int, dst: Path, bleed: bool = False,
           crop_to: str = "auto") -> Path:
    """The person at `face` in `src` as a hand-cut paper cut-out PNG about `size` px tall.
    crop_to: "body" (head and shoulders), "head", or "auto": head and shoulders unless the
    matte is messy (raised hands, a chair or the room leaking in) or doesn't hold, then the
    head alone, which reads better than a cluttered body."""
    frame = Image.open(src).convert("RGB")
    crop, head = head_box(frame, face, size, crop_to == "head")
    mask = person_matte(crop, head)
    import numpy as np
    if crop_to == "auto" and (mask is None or (np.asarray(mask) > 127).mean() > MESSY):
        crop_to = "head"
        crop, head = head_box(frame, face, size, True)
        mask = person_matte(crop, head)
    bleed = bleed and crop_to != "head"   # a head floats, cut all round
    rgba = crop.convert("RGBA")
    if mask is not None:
        rgba.putalpha(mask)
        rgba = rgba.crop(rgba.getbbox()) if rgba.getbbox() else rgba
    rgba = scissor(rgba, seed, bleed)
    out = handcut(rgba, margin=max(10, size // 36), seed=seed, bleed=bleed)
    dst.parent.mkdir(parents=True, exist_ok=True)
    meta = PngInfo()
    meta.add_text("crop", crop_to)        # compose reads it: a head floats, a body sits on the edge
    out.save(dst, pnginfo=meta)
    return dst


CUT_VERSION = 5  # bump when the matte or the paper edge changes, so cached cut-outs are redone


def cached_cut(cache: Path, src: Path, face: dict, size: int, seed: int, bleed: bool = False,
               crop_to: str = "auto") -> Path:
    """cutout (or photo_cutout for a whole-picture face) kept in `cache` under what made it:
    the source file and its mtime, the box, the size, the seed, whether torchvision was
    there. A video's teaser cut-out is then made once for all the covers it appears on, and
    --compose-only redraws without redoing a matte."""
    made = repr((str(src.resolve()), src.stat().st_mtime_ns, face, size, seed, bleed, crop_to, CUT_VERSION,
                 importlib.util.find_spec("torchvision") is not None))
    dst = cache / f"{hashlib.sha1(made.encode()).hexdigest()[:16]}.png"
    if not dst.exists():
        cache.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp.png")
        if face == WHOLE:
            photo_cutout(src, size, seed, tmp, bleed)
        else:
            cutout(src, face, size, seed, tmp, bleed, crop_to)
        tmp.replace(dst)
    return dst


def photo_cutout(src: Path, size: int, seed: int, dst: Path, bleed: bool = False) -> Path:
    """A [thumbnail] photos portrait (already head and shoulders) as a cut-out."""
    img = Image.open(src).convert("RGBA")
    img = img.resize((max(1, round(img.width * size / img.height)), size), Image.LANCZOS)
    mask = person_matte(img)
    if mask is not None:
        img.putalpha(mask)
        img = img.crop(img.getbbox()) if img.getbbox() else img
    img = scissor(img, seed, bleed)
    out = handcut(img, margin=max(10, size // 36), seed=seed, bleed=bleed)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst)
    return dst


# ---------- the page ----------

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1280px;height:720px;overflow:hidden;background:#222}
.cover{position:relative;width:1280px;height:720px;overflow:hidden;font-family:"Oswald";color:#1a1a1a}
.cover>*{position:absolute}
.panels{inset:0;width:100%;height:100%;z-index:0}
.ribbon{left:-20px;right:-20px;top:0;height:64px;z-index:9;background:#ffe600;color:#1a1a1a;text-align:center;
  font:400 36px/50px "Kalam";white-space:nowrap;overflow:hidden;filter:drop-shadow(0 4px 0 rgba(0,0,0,.25))}
.ribbon b{font-family:"Titan One";font-weight:400;color:#e8102e;font-size:34px;-webkit-text-stroke:1px #1a1a1a}
.ribbon i{display:inline-block;width:12px;height:12px;border-radius:50%;background:#ff2d8a;margin:0 12px 4px;vertical-align:middle}
.mast{left:34px;top:54px;width:860px;height:170px;z-index:4;font:400 140px/1 "Titan One";white-space:nowrap}
.mast .layer{position:absolute;left:0;top:0;-webkit-text-stroke:3px #1a1a1a}
.mast .h{visibility:hidden}
.mast .l4{transform:translate(28px,28px);color:#fff}.mast .l3{transform:translate(22px,22px);color:#ff2d8a}
.mast .l2{transform:translate(16px,16px);color:#fff}.mast .l1{transform:translate(10px,10px);color:#ff2d8a}
.mast .l0{transform:translate(5px,5px);color:#fff}
.mast .front{background:repeating-linear-gradient(180deg,#ff4fa6 0 10px,#fff 10px 15px,#ff4fa6 15px 25px,#fff 25px 29px);
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.glint{position:absolute;width:64px;left:118px;top:-18px;transform:rotate(15deg)}
.mascot{z-index:7;filter:drop-shadow(3px 5px 0 rgba(0,0,0,.35))}
.dateline{left:44px;top:232px;z-index:8;font:700 16px/1 "Archivo Narrow";letter-spacing:1px;text-transform:uppercase}
.main{z-index:5;object-fit:contain;object-position:bottom center;filter:drop-shadow(9px 11px 0 rgba(0,0,0,.32))}
.stk{position:relative;flex:none;z-index:6}
.cover>.stk{position:absolute}
.stk.gap{z-index:7}.stk.inset{z-index:9}
.stk img{display:block;object-fit:contain;object-position:bottom center;filter:drop-shadow(6px 8px 0 rgba(0,0,0,.35))}
.stk .nametag{position:absolute;left:2px;bottom:4px}
.nametag{z-index:9;background:#e8102e;color:#fff;border:3px solid #fff;border-radius:4px;padding:4px 10px;
  font:700 18px/1 "Archivo Narrow";letter-spacing:1px;text-transform:uppercase;transform:rotate(-4deg);white-space:nowrap}
.nametag.big{font-size:22px}
.col{left:40px;top:256px;bottom:12px;z-index:8;display:flex;flex-direction:column;gap:8px}
.row{display:flex;gap:14px;align-items:flex-start;margin-top:8px;min-height:0}
.sides{display:flex;flex-direction:column;gap:12px;flex:1;min-width:0}
.cl .head{font:700 44px/.92 "Oswald";text-transform:uppercase;letter-spacing:-.5px}
.cl .sub{font:700 21px/1.12 "Archivo Narrow";margin-top:6px;display:flex;align-items:flex-start;gap:7px}
.cl .sub i{flex:none;width:15px;height:15px;border-radius:50%;margin-top:3px;border:1.5px solid rgba(0,0,0,.25)}
.cl.boxed{background:#fff;padding:8px 12px;max-width:290px}
.script{font:400 42px/1.1 "Lobster";color:#ffe600;-webkit-text-stroke:1.5px #1a1a1a;text-shadow:3px 3px 0 #1a1a1a;
  white-space:nowrap;transform-origin:left center}
.top{display:flex;gap:14px;align-items:flex-start}
.own{flex:none}.own .head{white-space:nowrap;overflow:hidden}
.stk.big{margin-top:-6px}
.foot{bottom:14px;z-index:9;display:flex;flex-direction:column;align-items:flex-start;gap:8px}
.tab{z-index:9;background:#2fd3ff;color:#1a1a1a;font:700 20px/1 "Oswald";text-transform:uppercase;padding:6px 12px;border:2px solid #1a1a1a}
.mail{z-index:12;background:#fff;color:#1a1a1a;font:12px/1.3 monospace;padding:8px 10px;width:270px;border:1px solid #bbb}
"""

GLINT = ('<svg class="glint" viewBox="0 0 60 60"><path d="M30 0 L35 25 L60 30 L35 35 L30 60 L25 35 L0 30 L25 25 Z" '
         'fill="#fff" stroke="#1a1a1a" stroke-width="2"/><path d="M30 12 L33 27 L48 30 L33 33 L30 48 L27 33 L12 30 '
         'L27 27 Z" fill="#ffe600"/></svg>')


# The bundled fonts (all OFL), by the family name the CSS uses.
FONTS = {"Titan One": "TitanOne.ttf", "Oswald": "Oswald.ttf", "Archivo Narrow": "ArchivoNarrow.ttf",
         "Lobster": "Lobster.ttf", "Lilita One": "LilitaOne.ttf", "Kalam": "Kalam.ttf", "Fredoka": "Fredoka.ttf"}


def font_faces(fonts_dir: Path) -> str:
    return "".join(f'@font-face{{font-family:"{n}";src:url("{(fonts_dir / f).resolve().as_uri()}");'
                   f'font-weight:{"700" if n in ("Oswald", "Archivo Narrow", "Kalam", "Fredoka") else "400"}}}\n'
                   for n, f in FONTS.items())


def esc(t: str) -> str:
    return html.escape(t or "", quote=False)


def masthead(text: str, mascot: str, letter: int) -> str:
    """The stacked striped masthead; with a mascot, letter #`letter` is blanked (its advance
    kept) and the mascot hung in its slot."""
    text = text.upper()
    if mascot and 0 <= letter < len(text) and text[letter] != " ":
        word = esc(text[:letter]) + f'<span class="h">{esc(text[letter])}</span>' + esc(text[letter + 1:])
    else:
        word, mascot = esc(text), ""
    layers = "".join(f'<span class="layer l{i}">{word}</span>' for i in range(5))
    return (f'<div class="mast" id="mast">{layers}<span class="layer front" id="front">{word}</span>{GLINT}</div>'
            + (f'<img class="mascot" id="mascot" src="{mascot}">' if mascot else ""))


FIT_JS = """
<script>
// After the fonts load: fit the masthead to its width and hang the mascot in the blanked
// letter's slot; then drop side coverlines (last first) that would run past the column's
// bottom, and shrink or drop the column's sticker the same way, so a long teaser never
// lands on anything.
function fit(){
  var m=document.getElementById('mast'),f=document.getElementById('front');
  if(m){var size=140;while(f.getBoundingClientRect().width>m.clientWidth&&size>70){size-=4;m.style.fontSize=size+'px';}
    var h=f.querySelector('.h'),img=document.getElementById('mascot');
    if(h&&img){var r=h.getBoundingClientRect();img.style.left=(r.left-8)+'px';img.style.top=(r.top+10)+'px';
      img.style.width=(r.width+16)+'px';img.style.height=(r.height+40)+'px';img.style.transform='rotate(2deg)';}}
  var own=document.querySelector('.own .head');
  if(own){var fs=parseFloat(own.style.fontSize);while(own.scrollWidth>own.clientWidth&&fs>28){fs-=2;own.style.fontSize=fs+'px';}}
  var col=document.getElementById('col');if(!col)return;
  var lim=col.getBoundingClientRect().bottom,cls=col.querySelectorAll('.sides .cl');
  for(var i=cls.length-1;i>=0;i--){if(cls[i].getBoundingClientRect().bottom>lim)cls[i].style.display='none';}
  var stk=col.querySelector('.row .stk');
  if(stk){var room=lim-stk.getBoundingClientRect().top;
    if(room<90)stk.style.display='none';else if(room<stk.offsetHeight)stk.querySelector('img').style.height=(room-4)+'px';}
}
document.fonts.ready.then(fit);
</script>"""


def cl(head: str, subs: list[str], color: str, dot: str, size: int = 44, cls: str = "", style: str = "") -> str:
    """A coverline: the condensed head (already escaped) and bullet-dot sub-lines."""
    sub = "".join(f'<div class="sub" style="color:{color}"><i style="background:{dot}"></i>{esc(s)}</div>'
                  for s in subs if s)
    return (f'<div class="cl {cls}" style="{style}">'
            f'<div class="head" style="color:{color};font-size:{size}px">{head}</div>{sub}</div>')


def sticker(src: Path, name: str, rot: float, w: int, h: int, cls: str = "stk", style: str = "") -> str:
    """A small cut-out with its red name bar."""
    tag = f'<div class="nametag">{esc(name)}</div>' if name else ""
    return (f'<div class="{cls}" style="width:{w}px;{style}"><img src="{src.resolve().as_uri()}" '
            f'style="width:{w}px;height:{h}px;transform:rotate({rot}deg)">{tag}</div>')


def cover_title(title: str, limit: int = 48) -> str:
    """The video's title for the box over the photo, without a "Show | " prefix; "" (no box)
    when it is longer than `limit` characters: a title cut short reads wrong, and YouTube
    shows the whole one under the thumbnail anyway."""
    t = (title or "").rsplit(" | ", 1)[-1].strip().upper()
    return t if len(t) <= limit else ""


def cover_lines(e: dict) -> list[str]:
    """The coverline's lines: the model's headline, or a reviewer's edit of the text (Kitsu's
    Thumbnail text, the war room) broken into lines, as the enquirer cover does."""
    return e["headline"] if e.get("text", "") in ("", " ".join(e["headline"])) else enquirer_lines(e["text"])


def build_page(e: dict, ctx: dict, tc: dict, date: str, cuts: dict, out_dir: Path) -> Path:
    """Write cover.html for one video and return its path. `cuts`: {"main": Path,
    "inset": Path | None, "side": [Path | None, ...]}.

    The left column (x 40-545) is a flex stack: the script line, this cover's coverline with
    a big cut-out of the second story's face beside it, then the other videos as coverlines
    beside the first one's sticker; FIT_JS shrinks the coverline to its width and drops what
    would run off the bottom. The main cut-out is on the right from x 600: a body runs off
    the bottom with the solid title box and the name bar over it; a head alone (a messy
    matte, or "main_crop": "head") floats above the name bar, with no title box."""
    key = e["id"]
    rnd = random.Random(key)
    palette = PALETTES[zlib.crc32(key.encode()) % len(PALETTES)]
    _, b, c, d = palette
    parts = []
    # Ribbon: another cover's band line, with this cover's date as the tail.
    band = ctx.get("band") or {}
    tail = f"<i></i>{esc(date.title())}" if date else ""
    if band.get("band_headline"):
        rib = esc(band.get("band_kicker", "")) + " <b>" + esc(band["band_headline"]) + "</b>" + tail
    else:
        rib = "<b>" + esc(" ".join(e["headline"])) + "</b>" + tail
    parts.append(f'<div class="ribbon" style="clip-path:polygon({ribbon_cut(key)})">{rib}</div>')
    parts.append(masthead(tc["_masthead"], tc.get("_mascot_uri", ""), int(tc.get("mascot_letter", -1))))
    parts.append(f'<div class="dateline">{esc(date)} &nbsp; {esc(tc.get("masthead_price", ""))}</div>')
    # The left column.
    col = []
    if e.get("subhead"):
        col.append(f'<div class="script" style="transform:rotate({rnd.uniform(-3, -1):.1f}deg)">'
                   f'{esc(e["subhead"].lower())}</div>')
    head = "<br>".join(esc(line) for line in cover_lines(e)[:3])
    side = ctx.get("side", [])[:3]
    scuts = list(cuts.get("side") or []) + [None] * 3
    big = len(side) > 1 and scuts[1] is not None
    own = cl(head, [e.get("attribution", "")], INK, BULLETS[0], 50, "own", f"width:{275 if big else 490}px")
    # Beside the coverline, a big cut-out of the second story's face: the column is never empty.
    col.append(f'<div class="top">{own}' + (sticker(scuts[1], side[1].get("name", ""), rnd.choice((-3, 2, 4)),
                                                    200, 232, "stk big") if big else "") + "</div>")
    lines = []
    for i, s in enumerate(side):
        topic = esc((s.get("topic") or s.get("name") or "also inside").upper()[:24])
        lines.append(cl(topic, [s["text"]], INK, BULLETS[(i + 1) % 3], 32))
    row = ""
    if scuts[0] is not None:
        row += sticker(scuts[0], side[0].get("name", ""), rnd.choice((-4, -2, 3)), 160, 185)
    if lines:
        row += f'<div class="sides">{"".join(lines)}</div>'
    if row:
        col.append(f'<div class="row">{row}</div>')
    parts.append(f'<div class="col" id="col" style="width:505px">{"".join(col)}</div>')
    # Main cut-out, right: a body runs off the bottom; a head alone floats above the name bar.
    bleed = not cuts.get("main_head")
    box = "left:600px;top:96px;width:620px;height:624px" if bleed else "left:620px;top:168px;width:600px;height:500px"
    parts.append(f'<img class="main" src="{cuts["main"].resolve().as_uri()}" style="{box}">')
    # Over the photo: the name bar and (on a body, never a face) the title box, at the bottom.
    foot = ""
    if e.get("main_person"):
        foot += f'<div class="nametag big">{esc(e["main_person"])}</div>'
    if bleed and (title := cover_title(e.get("title", ""))):
        foot += cl(esc(title), [], INK, BULLETS[0], 26, "boxed")
    if foot:
        parts.append(f'<div class="foot" style="left:{rnd.randint(610, 640)}px">{foot}</div>')
    # Reaction inset as a sticker in the bottom-right corner, under an "insider's report" tab.
    if cuts.get("inset") is not None:
        parts.append('<div class="tab" style="left:1052px;top:452px">insider\'s report</div>')
        parts.append(sticker(cuts["inset"], "", 3, 190, 200, "stk inset", "left:1066px;top:496px"))
    parts.append('<div class="mail" style="left:920px;top:664px">#264B*****CAR-RT SORT** CR06<br>'
                 f'{esc(tc["_masthead"].upper())} READER &nbsp;&nbsp; TN01<br>132 BAILEY RD &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 29</div>')
    page = (f'<!doctype html><html><head><meta charset="utf-8"><title>{esc(key)}</title>'
            f'<style>{font_faces(ASSETS / "fonts")}{CSS}</style></head><body><div class="cover">'
            + panels(palette, key) + "".join(parts) + f"</div>{FIT_JS}</body></html>")
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "cover.html"
    dst.write_text(page, encoding="utf-8")
    return dst


# ---------- compose ----------

def compose_fanmag(stream_dir: Path, key: str, e: dict, ctx: dict, tc: dict, date: str,
                   video: Path, dst: Path) -> int:
    """Render one fan-mag cover to `dst` (JPEG under max_bytes); returns its size."""
    browser = find_browser(tc)
    if not browser:
        raise ClipperError("[thumbnail] style = \"fanmag\" needs a Chromium-based browser (Edge, Chrome, "
                         "Chromium); set [thumbnail] browser to its executable")
    work = stream_dir / "thumbs" / key / "fanmag"
    cache = stream_dir / "thumbs" / "_fanmag"

    def frame_path(n: int) -> Path:
        return frame_file(stream_dir, key, e, n, video)

    photo = photo_file(tc, e.get("photo", ""), e.get("photo_label", ""))
    main = cached_cut(cache, photo or frame_path(e["frame"]), WHOLE if photo else e["face"], 900, 1, True,
                      e.get("main_crop") or "auto")
    inset = None
    if e.get("inset_frame"):
        inset = cached_cut(cache, frame_path(e["inset_frame"]), e["inset_face"], 300, 2)
    side = [cached_cut(cache, Path(s["frame"]), s["face"], 260, 3) if s.get("frame") else None
            for s in ctx.get("side", [])[:3]]
    page = build_page(e, ctx, tc, date, {"main": main, "inset": inset, "side": side,
                                          "main_head": head_only(main)}, work)
    png = work / "cover.png"
    screenshot(browser, page, png)
    img = Image.open(png).convert("RGB")
    if img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)
    return save(img, dst, tc["max_bytes"])


def head_only(cut: Path) -> bool:
    """Whether cutout() made this a head alone (it floats) rather than head and shoulders."""
    with Image.open(cut) as img:
        return img.info.get("crop") == "head"


def fanmag_setup(tc: dict) -> dict:
    """tc with the mascot resolved to a file URI (bundled sloth when [thumbnail] mascot is
    "sloth", a path otherwise, "" for none)."""
    m = tc.get("mascot", "")
    if m == "sloth":
        m = str(ASSETS / "sloth.png")
    return {**tc, "_mascot_uri": Path(m).resolve().as_uri() if m and Path(m).exists() else ""}
