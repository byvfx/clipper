"""Fan-magazine thumbnails ([thumbnail] style = "fanmag"): the Sloth Beat look.

A late-80s teen fan magazine ('Teen, Bop) in 16:9: flat colour panels cut on diagonals with
two-ink crossover halftones, the striped bubble masthead with the mascot hanging in place of
one letter, a hand-cut paper ribbon with a tease across the top, the cover's subject as a
scissor-cut paper cut-out on the right, smaller cut-outs with red name bars down the left,
and condensed coverlines with bullet-dot sub-lines. Mockups and references:
mockups/sloth_beat/ (v7 is this layout).

The cover is an HTML page rendered by a Chromium-based browser in headless mode (Edge on
Windows, Chrome or Chromium elsewhere; [thumbnail] browser, else auto-detected). No
Playwright: one `--screenshot` call per cover. Fonts and the mascot ship in
clipper/assets/fanmag/ (all OFL).

The model's choice is the enquirer one (prompts/thumbnail_fanmag_<v>.md, same fields):
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
import html
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

W, H = 1280, 720
ASSETS = Path(__file__).parent / "assets" / "fanmag"
INK = "#1a1a1a"
PAPER = (247, 244, 236, 255)

# Three palettes (ground, column, wedge, accent), rotated per cover.
PALETTES = [("#8fe3b0", "#ff7ac3", "#ffe066", "#c9a9ff"),
            ("#c9a9ff", "#7fd8ff", "#ff7ac3", "#ffe066"),
            ("#ffe066", "#c9a9ff", "#8fe3b0", "#ff7ac3")]
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
        return tc["browser"] if Path(tc["browser"]).exists() else ""
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
               f"--screenshot={dst}"]
        # Windows: a running Edge/Chrome would take over a headless call that shares its
        # profile, so use a throwaway one. Elsewhere a profile dir makes some Chromium builds
        # hang on first-run work, and nothing else is running, so none.
        if sys.platform == "win32":
            cmd.append(f"--user-data-dir={prof}")
        elif os.geteuid() == 0:
            cmd.append("--no-sandbox")
        cmd += [*CHROME_FLAGS, page.resolve().as_uri()]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if not dst.exists():
            raise SystemExit(f"browser screenshot failed ({browser}): {r.stderr.strip()[-300:]}")


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


def person_matte(img: Image.Image) -> Image.Image | None:
    """A soft alpha mask of the people in `img` (DeepLabV3, torchvision), or None when
    torchvision is not installed (then the cut-out is the face crop's rectangle)."""
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
    mask = Image.fromarray((person.numpy() * 255).astype("uint8")).resize(img.size, Image.BILINEAR)
    if mask.getbbox() is None or sum(mask.histogram()[128:]) < 0.02 * img.width * img.height:
        return None                             # nobody found: keep the rectangle
    return mask.point(lambda v: 255 if v > 110 else 0).filter(ImageFilter.GaussianBlur(1.2))


def handcut(img: Image.Image, margin: int = 26, strokes: int = 46, wobble: float = 6.0,
            seed: int = 1) -> Image.Image:
    """Put a hand-cut paper margin around an RGBA cut-out: grow the silhouette, trace it as
    `strokes` straight scissor cuts from the figure's centre (so concavities like the neck
    are skipped, as scissors do), fill with paper, composite the photo on top."""
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
    return out.crop(out.getbbox())


def cutout(src: Path, face: dict, size: int, seed: int, dst: Path) -> Path:
    """The person at `face` in `src` as a hand-cut paper cut-out PNG about `size` px tall."""
    from .thumbs import clamp_box
    img = Image.open(src).convert("RGB")
    fw, fh = img.size
    b = clamp_box(face)
    # Head and shoulders: the face box widened and extended down to the chest.
    cx = (b["x"] + b["width"] / 2) * fw
    top = max(0, (b["y"] - b["height"] * 0.35) * fh)
    bottom = min(fh, (b["y"] + b["height"] * 2.6) * fh)
    half = min(fw / 2, max(b["width"] * fw * 1.35, (bottom - top) * 0.42))
    crop = img.crop((round(max(0, cx - half)), round(top), round(min(fw, cx + half)), round(bottom)))
    crop = crop.resize((max(1, round(crop.width * size / crop.height)), size), Image.LANCZOS)
    rgba = crop.convert("RGBA")
    mask = person_matte(crop)
    if mask is not None:
        rgba.putalpha(mask)
        rgba = rgba.crop(rgba.getbbox()) if rgba.getbbox() else rgba
    out = handcut(rgba, margin=max(10, size // 36), seed=seed)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst)
    return dst


def photo_cutout(src: Path, size: int, seed: int, dst: Path) -> Path:
    """A [thumbnail] photos portrait (already head and shoulders) as a cut-out."""
    img = Image.open(src).convert("RGBA")
    img = img.resize((max(1, round(img.width * size / img.height)), size), Image.LANCZOS)
    mask = person_matte(img)
    if mask is not None:
        img.putalpha(mask)
    out = handcut(img, margin=max(10, size // 36), seed=seed)
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
.sticker{z-index:6;object-fit:contain;object-position:bottom center;filter:drop-shadow(6px 8px 0 rgba(0,0,0,.35))}
.nametag{z-index:9;background:#e8102e;color:#fff;border:3px solid #fff;border-radius:4px;padding:4px 10px;
  font:700 18px/1 "Archivo Narrow";letter-spacing:1px;text-transform:uppercase;transform:rotate(-4deg);white-space:nowrap}
.cl{z-index:8}
.cl .head{font:700 44px/.92 "Oswald";text-transform:uppercase;letter-spacing:-.5px}
.cl .sub{font:700 21px/1.12 "Archivo Narrow";margin-top:6px;display:flex;align-items:flex-start;gap:7px}
.cl .sub i{flex:none;width:15px;height:15px;border-radius:50%;margin-top:3px;border:1.5px solid rgba(0,0,0,.25)}
.cl.boxed{background:#fff;padding:8px 12px}
.script{z-index:8;font:400 42px/1 "Lobster";text-shadow:2px 2px 0 rgba(0,0,0,.25);white-space:nowrap}
.tab{z-index:9;background:#2fd3ff;color:#1a1a1a;font:700 20px/1 "Oswald";text-transform:uppercase;padding:6px 12px;border:2px solid #1a1a1a}
.mail{z-index:12;background:#fff;color:#1a1a1a;font:12px/1.3 monospace;padding:8px 10px;width:270px;border:1px solid #bbb}
"""

GLINT = ('<svg class="glint" viewBox="0 0 60 60"><path d="M30 0 L35 25 L60 30 L35 35 L30 60 L25 35 L0 30 L25 25 Z" '
         'fill="#fff" stroke="#1a1a1a" stroke-width="2"/><path d="M30 12 L33 27 L48 30 L33 33 L30 48 L27 33 L12 30 '
         'L27 27 Z" fill="#ffe600"/></svg>')


def font_faces(fonts_dir: Path) -> str:
    faces = {"Titan One": "TitanOne.ttf", "Oswald": "Oswald.ttf", "Archivo Narrow": "ArchivoNarrow.ttf",
             "Lobster": "Lobster.ttf", "Lilita One": "LilitaOne.ttf", "Kalam": "Kalam.ttf", "Fredoka": "Fredoka.ttf"}
    return "".join(f'@font-face{{font-family:"{n}";src:url("{(fonts_dir / f).resolve().as_uri()}");'
                   f'font-weight:{"700" if n in ("Oswald", "Archivo Narrow", "Kalam", "Fredoka") else "400"}}}\n'
                   for n, f in faces.items())


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


MASCOT_JS = """
<script>
// Fit the masthead to its width, then hang the mascot in the blanked letter's slot.
(function(){
  var m=document.getElementById('mast'),f=document.getElementById('front');if(!m)return;
  var size=140;while(f.getBoundingClientRect().width>m.clientWidth&&size>70){size-=4;m.style.fontSize=size+'px';}
  var h=f.querySelector('.h'),img=document.getElementById('mascot');
  if(h&&img){var r=h.getBoundingClientRect();img.style.left=(r.left-8)+'px';img.style.top=(r.top+10)+'px';
    img.style.width=(r.width+16)+'px';img.style.height=(r.height+40)+'px';img.style.transform='rotate(2deg)';}
})();
</script>"""


def coverline(x, y, w, head, subs, color, dot, size=44, align="left", boxed=False) -> str:
    sub = "".join(f'<div class="sub" style="color:{color}"><i style="background:{dot}"></i>{esc(s)}</div>' for s in subs if s)
    return (f'<div class="cl{" boxed" if boxed else ""}" style="left:{x}px;top:{y}px;width:{w}px;text-align:{align}">'
            f'<div class="head" style="color:{color};font-size:{size}px">{head}</div>{sub}</div>')


def sticker(x, y, w, h, src: Path, name: str, rot: float, z: int = 6) -> str:
    tag = (f'<div class="nametag" style="left:{x + 4}px;top:{y + h - 38}px;z-index:{z + 3}">{esc(name)}</div>'
           if name else "")
    return (f'<img class="sticker" src="{src.resolve().as_uri()}" style="left:{x}px;top:{y}px;width:{w}px;'
            f'height:{h}px;z-index:{z};transform:rotate({rot}deg)">{tag}')


def build_page(e: dict, ctx: dict, tc: dict, date: str, cuts: dict, out_dir: Path) -> Path:
    """Write cover.html for one video and return its path. `cuts`: {"main": Path,
    "inset": Path | None, "side": [Path | None, ...]}."""
    key = e["id"]
    rnd = random.Random(key)
    palette = PALETTES[zlib.crc32(key.encode()) % len(PALETTES)]
    _, b, c, d = palette
    on_col = "#fff" if rnd.random() < 0.6 else INK
    parts = []
    # Ribbon: another cover's band line, with this cover's date as the tail.
    band = ctx.get("band") or {}
    if band.get("band_headline"):
        rib = (esc(band.get("band_kicker", "")) + " <b>" + esc(band["band_headline"]) + "</b>"
               + (f"<i></i>{esc(date.title())}" if date else ""))
    else:
        rib = "<b>" + esc(" ".join(e["headline"])) + "</b>" + (f"<i></i>{esc(date.title())}" if date else "")
    parts.append(f'<div class="ribbon" style="clip-path:polygon({ribbon_cut(key)})">{rib}</div>')
    parts.append(masthead(tc["_masthead"], tc.get("_mascot_uri", ""), int(tc.get("mascot_letter", -1))))
    parts.append(f'<div class="dateline">{esc(date)} &nbsp; {esc(tc.get("masthead_price", ""))}</div>')
    # Script line: the subhead, in the accent.
    if e.get("subhead"):
        parts.append(f'<div class="script" style="left:42px;top:262px;color:{d if d != b else c};'
                     f'transform:rotate(-2deg)">{esc(e["subhead"].lower())}</div>')
    # Left column: this cover's headline big, then the other videos as coverlines with their
    # faces as stickers beside them. Everything stays left of x=540; the main cut-out starts at 600.
    y = 322
    col_w = 490
    head = "<br>".join(esc(l) for l in e["headline"][:3])
    parts.append(coverline(44, y, col_w, head, [e.get("attribution", "")], on_col, BULLETS[0], 48))
    y += 60 * max(1, len(e["headline"][:3])) + 44
    for i, s in enumerate(ctx.get("side", [])[:3]):
        if y > 640:
            break
        cut = cuts["side"][i] if i < len(cuts["side"]) else None
        tx = 44
        if cut is not None:
            sh = 150 if i == 0 else 120
            sw = int(sh * 0.9)
            parts.append(sticker(40, y - 6, sw, sh, cut, s.get("name", ""), rnd.choice((-4, 3, -2)), 6))
            tx = 40 + sw + 16
        topic = (s.get("topic") or "also inside").upper()
        color = INK if on_col == "#fff" else "#fff"
        parts.append(coverline(tx, y, 540 - tx, esc(topic[:26]), [s["text"]], color, BULLETS[(i + 1) % 3], 34))
        y += 118 if cut is not None else 92
    # Main cut-out, right, bottom-aligned, with the subject's name bar.
    parts.append(f'<img class="main" src="{cuts["main"].resolve().as_uri()}" '
                 f'style="left:600px;top:110px;width:600px;height:610px">')
    if e.get("main_person"):
        parts.append(f'<div class="nametag" style="left:640px;top:660px;font-size:22px">{esc(e["main_person"])}</div>')
    # Reaction inset as a sticker in the bottom-right corner, under an "insider's report" tab.
    if cuts.get("inset") is not None:
        parts.append('<div class="tab" style="left:1060px;top:470px">insider\'s report</div>')
        parts.append(sticker(1080, 520, 160, 180, cuts["inset"], "", 3, 9))
    # The one box allowed over the photo: the video's title, 'Teen "FASHION" style.
    if e.get("title"):
        parts.append(coverline(600, 560, 300, esc(e["title"][:44].upper()), [], INK, BULLETS[0], 28, "left", True))
    parts.append('<div class="mail" style="left:900px;top:660px">#264B*****CAR-RT SORT** CR06<br>'
                 f'{esc(tc["_masthead"].upper())} READER &nbsp;&nbsp; TN01<br>132 BAILEY RD &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 29</div>')
    page = (f'<!doctype html><html><head><meta charset="utf-8"><title>{esc(key)}</title>'
            f'<style>{font_faces(ASSETS / "fonts")}{CSS}</style></head><body><div class="cover">'
            + panels(palette, key) + "".join(parts) + f"</div>{MASCOT_JS}</body></html>")
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "cover.html"
    dst.write_text(page, encoding="utf-8")
    return dst


# ---------- compose ----------

def compose_fanmag(stream_dir: Path, key: str, e: dict, ctx: dict, tc: dict, date: str,
                   video: Path, dst: Path) -> int:
    """Render one fan-mag cover to `dst` (JPEG under max_bytes); returns its size."""
    from .thumbs import WHOLE, grab, photo_name, save
    browser = find_browser(tc)
    if not browser:
        raise SystemExit("[thumbnail] style = \"fanmag\" needs a Chromium-based browser (Edge, Chrome, "
                         "Chromium); set [thumbnail] browser to its executable")
    fdir = stream_dir / "thumbs" / key
    work = fdir / "fanmag"

    def frame_path(n: int) -> Path:
        p = fdir / e["frames"][n - 1]["path"]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            grab(video, e["frames"][n - 1]["t"], p)
        return p

    photo = tc.get("_photos", {}).get(photo_name(tc, e.get("photo", "")))
    main = (photo_cutout(photo, 900, 1, work / "main.png") if photo
            else cutout(frame_path(e["frame"]), e["face"], 900, 1, work / "main.png"))
    inset = None
    if e.get("inset_frame"):
        inset = cutout(frame_path(e["inset_frame"]), e["inset_face"], 300, 2, work / "inset.png")
    side = []
    for i, s in enumerate(ctx.get("side", [])[:3]):
        if s.get("frame"):
            src = Path(s["frame"])
            side.append(photo_cutout(src, 260, 3 + i, work / f"side{i}.png") if s.get("face") == WHOLE
                        else cutout(src, s["face"], 260, 3 + i, work / f"side{i}.png"))
        else:
            side.append(None)
    page = build_page(e, ctx, tc, date, {"main": main, "inset": inset, "side": side}, work)
    png = work / "cover.png"
    screenshot(browser, page, png)
    img = Image.open(png).convert("RGB")
    if img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)
    return save(img, dst, tc["max_bytes"])


def fanmag_setup(tc: dict) -> dict:
    """tc with the mascot resolved to a file URI (bundled sloth when [thumbnail] mascot is
    "sloth", a path otherwise, "" for none)."""
    m = tc.get("mascot", "")
    if m == "sloth":
        m = str(ASSETS / "sloth.png")
    return {**tc, "_mascot_uri": Path(m).resolve().as_uri() if m and Path(m).exists() else ""}
