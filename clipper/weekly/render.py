"""The pages: 1080x1440 pages 01-10 from issue.json (or the same layout on the square
canvas for YouTube Posts), the contact sheet and post_text.md. The look (canvas, palette,
the cover's geometry, the fixed copy) is weekly/style.py, overridable by style.toml."""

from __future__ import annotations

import itertools
import math
import random
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from ..errors import ClipperError
from . import style as weekly_style
from .faces import Faces
from .gather import hm, next_week

PAGES = ["01_cover", "02_contents", "03_feature", "04_article", "05_article", "06_brief", "07_quote",
         "08_numbers", "09_market", "10_back"]


# ---------- fonts and drawing ----------

class Fonts:
    def __init__(self, wc: dict):
        self.paths = {}
        for key in ("headline", "condensed", "serif_italic", "serif_bold_italic", "black", "bold"):
            p = next((f for f in wc[f"{key}_fonts"] if Path(f).exists()), None)
            if not p:
                raise ClipperError(f"[weekly] {key}_fonts: none of {wc[f'{key}_fonts']} exist (clipper doctor)")
            self.paths[key] = p

    def impact(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["headline"], size)

    def bahn(self, size: int, style: str = "Bold Condensed") -> ImageFont.FreeTypeFont:
        f = ImageFont.truetype(self.paths["condensed"], size)
        try:
            f.set_variation_by_name(style)
        except (OSError, ValueError):
            pass
        return f

    def serif(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["serif_bold_italic" if bold else "serif_italic"], size)

    def black(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["black"], size)

    def bold(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["bold"], size)


def wrap(d: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=f) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    return lines + [cur] if cur else lines


def para(d, xy, text, f, width, fill, lh) -> int:
    """Draw wrapped text; a blank line in the text is a paragraph break (half a line of air)."""
    x, y = xy
    for i, chunk in enumerate(t for t in re.split(r"\n\s*\n", text) if t.strip()):
        if i:
            y += lh // 2
        for line in wrap(d, chunk, f, width):
            d.text((x, y), line, font=f, fill=fill)
            y += lh
    return y


def fit(text: str, make, max_w: int, hi: int, max_h: int = 0) -> int:
    lo, best = 8, 8
    while lo <= hi:
        mid = (lo + hi) // 2
        b = make(mid).getbbox(text)
        if b[2] - b[0] <= max_w and (not max_h or b[3] - b[1] <= max_h):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def rounded(img: Image.Image, r: int) -> Image.Image:
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, img.width - 1, img.height - 1], r, fill=255)
    img = img.convert("RGBA")
    img.putalpha(m)
    return img


def sheared(img: Image.Image, k: float = 0.18) -> Image.Image:
    w, h = img.size
    extra = int(h * k)
    return img.transform((w + extra, h), Image.AFFINE, (1, k, -extra, 0, 1, 0), Image.BICUBIC)


def shadow_text(base, xy, text, f, fill, stroke=0, stroke_fill=weekly_style.DEFAULT.palette.ink, shadow=(5, 6),
                blur=3, anchor="la"):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((xy[0] + shadow[0], xy[1] + shadow[1]), text, font=f, fill=(0, 0, 0, 200),
           stroke_width=stroke, stroke_fill=(0, 0, 0, 200), anchor=anchor)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(layer)
    ImageDraw.Draw(base).text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill, anchor=anchor)


def rich_line(d, x, y, parts, f) -> float:
    for t, c in parts:
        d.text((x, y), t, font=f, fill=c)
        x += d.textlength(t, font=f)
    return x


def up(s: str) -> str:
    return (s or "").upper()


# ---------- renderer ----------

class Renderer:
    def __init__(self, cfg: dict, here: Path, week: dict, stats: dict, issue: dict, square: bool = False,
                 style: weekly_style.Style | None = None):
        """square: the canvas is height x height (the same vertical layout, wider columns) for
        the YouTube pages, scaled to 1080x1080 in youtube/."""
        self.cfg, self.here, self.week, self.stats, self.issue = cfg, here, week, stats, issue
        self.wc = cfg["weekly"]
        self.S = style or weekly_style.load(cfg)
        self.P, self.T = self.S.palette, self.S.copy
        self.square = square
        self.H, self.MX = self.S.canvas.height, self.S.canvas.margin
        self.W = self.H if square else self.S.canvas.width
        self.CW = self.W - 2 * self.MX
        self.F = Fonts(self.wc)
        self.faces = Faces(cfg, week, issue.get("faces"), here, palette=self.P)
        self.faces.fonts = self.F
        self.ISSUE = f"ISSUE {int(issue.get('issue') or 1):02d}"
        self.WEEK = issue.get("week") or week["label"]
        self.TOTAL = len(PAGES)
        self.warnings: list[str] = []

    # chrome
    def page(self, dark=False) -> Image.Image:
        return Image.new("RGBA", (self.W, self.H), self.P.ink if dark else self.P.paper)

    def chrome(self, p, n, section, dark=False, cta=""):
        W, H, MX = self.W, self.H, self.MX
        P, F = self.P, self.F
        cta = cta or self.T.swipe
        d = ImageDraw.Draw(p)
        fg, sub = (P.white, P.grey) if dark else (P.ink, P.muted)
        f = F.bahn(36)
        m1, m2 = self.wc["masthead"]
        d.text((MX, 38), m1, font=f, fill=P.red)
        d.text((MX + d.textlength(m1 + " ", font=f), 38), f"{m2} {self.T.weekly}", font=f, fill=fg)
        d.text((W - MX, 48), f"{section}  ·  {self.ISSUE}", font=F.bahn(22, "SemiBold"), fill=sub, anchor="ra")
        d.line([MX, 90, W - MX, 90], fill=fg, width=3)
        d.line([MX, H - 84, W - MX, H - 84], fill=P.rule if not dark else (60, 60, 60), width=2)
        ff = F.bahn(26)
        d.text((MX, H - 66), f"{n:02d} / {self.TOTAL:02d}", font=ff, fill=fg)
        d.text((W // 2, H - 62), self.WEEK, font=F.bahn(22, "SemiBold"), fill=sub, anchor="ma")
        tw = d.textlength(cta, font=ff)
        d.text((W - MX - 44 - tw, H - 66), cta, font=ff, fill=fg)
        ax, ay = W - MX - 30, H - 52
        d.line([ax - 6, ay, ax + 26, ay], fill=P.red, width=5)
        d.polygon([(ax + 30, ay), (ax + 16, ay - 10), (ax + 16, ay + 10)], fill=P.red)
        return d

    def kicker_bar(self, d, x, y, parts, size=34, pad=14) -> int:
        f = self.F.bahn(size)
        w = sum(d.textlength(t, font=f) for t, _ in parts)
        d.rectangle([x, y, x + w + 2 * pad, y + size + 18], fill=self.P.red)
        cx = x + pad
        for t, c in parts:
            d.text((cx, y + 7), t, font=f, fill=c)
            cx += d.textlength(t, font=f)
        return y + size + 18

    def title(self, d, text, y, size=132, fill=None, max_w=0) -> int:
        max_w = max_w or self.CW
        size = min(size, fit(text, self.F.impact, max_w, size))
        d.text((self.MX - 4, y), text, font=self.F.impact(size), fill=fill or self.P.ink)
        return y + int(size * 1.08)

    def highlight_text(self, d, xy, parts, f):
        P = self.P
        x, y = xy
        for t, hl in parts:
            w = d.textlength(t, font=f)
            if hl:
                b = f.getbbox(t)
                d.rectangle([x - 6, y + b[1] + 6, x + w + 6, y + b[3] + 4], fill=P.yellow)
            d.text((x, y), t, font=f, fill=P.ink)
            x += w

    def bullet(self, d, x, y, size=18):
        d.rectangle([x, y, x + size, y + size], fill=self.P.yellow, outline=self.P.ink, width=2)

    def tag(self, p, d, x, y, text):
        tf = self.F.bahn(24)
        tw = d.textlength(text, font=tf)
        d.rounded_rectangle([x, y, x + tw + 28, y + 40], 20, fill=self.P.red)
        d.text((x + 14, y + 6), text, font=tf, fill=self.P.white)

    def overflow(self, n: int, end: int, bottom: int = 0) -> int:
        """end: the y after the last text line (its line height included), vs the footer rule.
        How many px the text runs past it (0 when it fits)."""
        bottom = bottom or self.H - 84 - 8
        if end - 8 > bottom:
            self.warnings.append(f"page {n:02d}: text runs {end - bottom}px past the footer")
            return end - bottom
        return 0

    # 01 cover: the photo panel under three teasers, the masthead, the subject cut out over
    # them, then the kicker, headline, inset, starburst, date, bullets and barcode on top.
    # Geometry is style.cover; k scales its horizontal values on the wider square canvas.
    def p_cover(self) -> Image.Image:
        c = self.issue["cover"]
        page = Image.new("RGBA", (self.W, self.H), (*self.P.white, 255))
        self.cover_background(page, c)
        self.cover_teasers(page, c)
        self.cover_masthead(page)
        self.cover_subject(page, c)
        ky = self.cover_kicker(page, c)
        self.cover_headline(page, c, ky)
        self.cover_inset(page, c)
        self.cover_burst(page, c)
        self.cover_date(page)
        self.cover_bullets(page, c)
        self.cover_barcode(page)
        return page

    @property
    def k(self) -> float:
        return self.W / self.S.canvas.width

    def cover_background(self, page, c):
        W, H, L = self.W, self.H, self.S.cover
        M, MAIN_TOP = L.margin, L.main_top
        room = self.faces.frame(c["person"])
        bg_size = (W - 2 * M, H - MAIN_TOP - M)
        if c.get("background"):
            # a picture of the editor's choosing as a shadow behind the subject: grey, dark, navy
            bgp = Path(c["background"])
            bg = Image.open(bgp if bgp.is_absolute() else self.here / bgp).convert("L")
            cx, cy = c.get("background_focus", [0.5, 0.35])
            bg = ImageOps.autocontrast(ImageOps.fit(bg, bg_size, Image.LANCZOS, centering=(cx, cy)), cutoff=1)
            bg = ImageEnhance.Brightness(bg.convert("RGB")).enhance(0.8).filter(ImageFilter.GaussianBlur(1.2))
            bg = Image.blend(bg, Image.new("RGB", bg.size, L.background_tint), 0.35)
            grad = Image.linear_gradient("L").resize(bg.size)
            bg = Image.composite(Image.new("RGB", bg.size, L.shade), bg, grad.point(lambda v: int(v * 0.45)))
        elif room is None:
            bg = Image.new("RGB", bg_size, self.P.navy)
        else:
            w, h = room.size
            bg = ImageOps.fit(room.crop((0, int(0.12 * h), int(0.74 * w), int(0.96 * h))), bg_size, Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(9))
            bg = ImageEnhance.Brightness(bg).enhance(0.55)
            bg = Image.blend(bg, Image.new("RGB", bg.size, L.tint), 0.45)
            grad = Image.linear_gradient("L").resize(bg.size)
            bg = Image.composite(Image.new("RGB", bg.size, L.shade), bg, grad.point(lambda v: int(v * 0.55)))
        page.paste(bg, (M, MAIN_TOP))

    def cover_teasers(self, page, c):
        """The top strip: three teaser panels, kicker, headline, a photo and a label each."""
        W, P, F, L, k = self.W, self.P, self.F, self.S.cover, self.k
        M = L.margin
        STRIP_Y, STRIP_H = M, L.strip_height
        widths = [int(w * k) for w in L.teaser_widths]
        widths.append(W - 2 * M - sum(widths) - 8 * len(widths))
        styles = [(t["bg"], t["kicker"], t["headline"], t.get("framed", False)) for t in L.teasers]
        d = ImageDraw.Draw(page)
        KH, PW = L.teaser_kicker_height, L.teaser_photo_width
        panels = []
        x = M
        for t, wdt, (bgc, kc, hc, framed) in zip(c["teasers"], widths, styles):
            panels.append({"x": x, "w": wdt, "bg": bgc, "kc": kc, "hc": hc, "framed": framed, **t})
            x += wdt + 8
        k_size = min(fit(p["kicker"], lambda s: F.bahn(s), p["w"] - 28, 36, 36) for p in panels)
        h_size = min(fit(up(line), lambda s: F.bahn(s), p["w"] - int(p["w"] * PW) - 34, 90, (STRIP_H - KH - 22) // 4)
                     for p in panels for line in p["headline"])
        h_size = min(h_size, int((STRIP_H - KH - 22) / max(len(p["headline"]) for p in panels) / 0.95))  # all lines fit the strip
        kf, hf = F.bahn(k_size), F.bahn(h_size)
        for p in panels:
            x, w = p["x"], p["w"]
            h = STRIP_H + (46 if p["framed"] else 0)
            d.rectangle([x, STRIP_Y, x + w, STRIP_Y + h], fill=p["bg"])
            d.text((x + 14, STRIP_Y + 10), p["kicker"], font=kf, fill=p["kc"])
            d.line([x + 14, STRIP_Y + KH - 6, x + 14 + d.textlength(p["kicker"], font=kf), STRIP_Y + KH - 6], fill=p["kc"], width=3)
            pw = int(w * PW)
            ph = self.faces.scene(p["person"], (pw, h - KH - 12), centering=(0.5, 0.3))
            page.paste(ph, (x + w - pw - 10, STRIP_Y + KH))
            lf = F.bahn(22)
            lw = d.textlength(up(p["label"]), font=lf) + 14
            d.rectangle([x + w - lw - 10, STRIP_Y + h - 42, x + w - 10, STRIP_Y + h - 12], fill=P.ink)
            d.text((x + w - lw - 3, STRIP_Y + h - 40), up(p["label"]), font=lf, fill=P.white)
            y = STRIP_Y + KH + 2
            for line in p["headline"]:
                d.text((x + 14, y), up(line), font=hf, fill=p["hc"])
                y += int(h_size * 0.95)
            if p["framed"]:
                d.rectangle([x - 4, STRIP_Y - 4, x + w + 4, STRIP_Y + h + 4], outline=P.yellow, width=12)

    def cover_masthead(self, page):
        """The red word sheared over the panel, the chrome word below it."""
        W, P, F, L = self.W, self.P, self.F, self.S.cover
        M, MAIN_TOP = L.margin, L.main_top
        m1, m2 = self.wc["masthead"]
        lol = Image.new("RGBA", (420, 70), (0, 0, 0, 0))
        ImageDraw.Draw(lol).text((4, 0), m1, font=F.black(56), fill=P.red, stroke_width=2, stroke_fill=P.white)
        page.alpha_composite(sheared(lol), (50, MAIN_TOP + 6))
        MW = W - 2 * M - 60
        mf = F.black(fit(m2, F.black, MW, 190))
        bb = mf.getbbox(m2)
        mw, mh = bb[2] - bb[0] + 20, bb[3] - bb[1] + 20
        mask = Image.new("L", (mw, mh), 0)
        ImageDraw.Draw(mask).text((10 - bb[0], 10 - bb[1]), m2, font=mf, fill=255)
        stops = L.masthead_chrome
        chrome = Image.new("RGB", (mw, mh))
        cd = ImageDraw.Draw(chrome)
        for yy in range(mh):
            t = yy / (mh - 1)
            for (t0, c0), (t1, c1) in itertools.pairwise(stops):
                if t0 <= t <= t1:
                    u = (t - t0) / (t1 - t0)
                    cd.line([(0, yy), (mw, yy)], fill=tuple(int(a + (b - a) * u) for a, b in zip(c0, c1)))
                    break
        mast = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        mast.paste((20, 24, 40, 255), (0, 0), mask.filter(ImageFilter.MaxFilter(9)))
        mast.paste(chrome, (0, 0), mask)
        mast = sheared(mast)
        mshadow = Image.new("RGBA", mast.size, (0, 0, 0, 0))
        mshadow.paste((0, 0, 0, 170), (0, 0), mast.split()[3])
        mshadow = mshadow.filter(ImageFilter.GaussianBlur(6))
        MAST_Y = MAIN_TOP + L.masthead_y
        page.alpha_composite(mshadow, (M + 26 + 8, MAST_Y + 10))
        page.alpha_composite(mast, (M + 26, MAST_Y))

    def cover_subject(self, page, c):
        """The subject cut out (with a soft shadow) inside the white border, else a photo panel."""
        W, H, L = self.W, self.H, self.S.cover
        M = L.margin
        subject = c["person"]
        clip = Image.new("L", page.size, 0)
        ImageDraw.Draw(clip).rectangle([M, M, W - M - 1, H - M - 1], fill=255)
        cut = self.faces.cutout(subject, self.here)
        if cut is not None:
            bbox = cut.split()[3].getbbox()
            cut = cut.crop(bbox)
            scale = int(c.get("subject_height", L.subject_height)) / cut.height  # issue.json cover.subject_height
            cut = cut.resize((int(cut.width * scale), int(cut.height * scale)), Image.LANCZOS)
            cx = min(W - M - cut.width + 300, W - M - int(cut.width * 0.72))
            cy = H - M - cut.height + 10
            glow = Image.new("RGBA", page.size, (0, 0, 0, 0))
            glow.paste((0, 0, 0, 150), (cx + 10, cy + 8), cut.split()[3])
            glow = glow.filter(ImageFilter.GaussianBlur(12))
            glow.putalpha(Image.composite(glow.split()[3], Image.new("L", page.size, 0), clip))
            page.alpha_composite(glow)
            layer = Image.new("RGBA", page.size, (0, 0, 0, 0))
            layer.paste(cut, (cx, cy), cut)
            layer.putalpha(Image.composite(layer.split()[3], Image.new("L", page.size, 0), clip))
            page.alpha_composite(layer)
        else:
            self.warnings.append("cover: no cut-out (torch missing or no person found); using a panel")
            ph = rounded(self.faces.scene(subject, (430, 560), centering=(0.5, 0.3)), 24)
            page.alpha_composite(ph, (W - M - 430 - 20, H - M - 560 - 240))

    def cover_kicker(self, page, c) -> int:
        """The red kicker box; its top y."""
        P, F, L = self.P, self.F, self.S.cover
        M = L.margin
        d = ImageDraw.Draw(page)
        KY = L.main_top + L.kicker_y
        kf = F.bahn(50)
        kw = max(d.textlength(c["kicker"] + " " + c["kicker_hot"], font=kf), d.textlength(c["kicker_line2"], font=kf)) + 40
        d.rectangle([M + 26, KY, M + 26 + max(int(L.kicker_width * self.k), int(kw)), KY + L.kicker_height], fill=P.red)
        rich_line(d, M + 44, KY + 6, [(c["kicker"] + " ", P.white), (c["kicker_hot"], P.yellow)], kf)
        rich_line(d, M + 44, KY + 60, [(c["kicker_line2"], P.white)], kf)
        return KY

    def cover_headline(self, page, c, KY: int):
        """The headline from under the kicker down to the bullets, as big as the lines allow."""
        H, P, F, L = self.H, self.P, self.F, self.S.cover
        M = L.margin
        lines = [up(line) for line in c["headline"]]
        HX, HY, HW = M + 26, KY + 134, L.headline_width  # the headline keeps its width on the square canvas: the subject needs the room
        HB = H - M - 3 * L.bullet_row - 18 - 14
        size = min(min(fit(line, F.impact, HW, L.headline_size) for line in lines), int((HB - HY) / len(lines) / 0.96 / 1.02))
        hf = F.impact(size)
        y = HY
        for line in lines:
            shadow_text(page, (HX, y), line, hf, P.yellow, stroke=6, stroke_fill=P.ink, shadow=(7, 8), blur=4)
            y += int(size * 0.96)

    def cover_inset(self, page, c):
        """A second person in a circle, with name and role on a strip under it."""
        H, P, F, L = self.H, self.P, self.F, self.S.cover
        M = L.margin
        d = ImageDraw.Draw(page)
        D = L.inset_size
        inset = self.faces.square(c["inset_person"], D)
        circ = Image.new("L", (D, D), 0)
        ImageDraw.Draw(circ).ellipse([0, 0, D - 1, D - 1], fill=255)
        # the role under the name may run to two lines ("H3 co-founder, soon to be ex-wife":
        # split at ", " or " | "); the strip widens and the inset moves up to make room
        role = [r.strip() for r in re.split(r"\s*\|\s*|,\s+", c["inset_role"]) if r.strip()][:2]
        IX, IY = int(L.inset_x * self.k), H - M - D - 66 - (24 if len(role) > 1 else 0)
        d.ellipse([IX - 9, IY - 9, IX + D + 9, IY + D + 9], fill=P.white)
        page.paste(inset, (IX, IY), circ)
        nf, sf = F.bahn(30), F.bahn(22)
        sw = max([d.textlength(up(c["inset_name"]), font=nf)] + [d.textlength(r, font=sf) for r in role]) + 28
        sw = max(sw, D - 40)
        d.rectangle([IX + D // 2 - sw / 2, IY + D - 6, IX + D // 2 + sw / 2, IY + D + 28 + 24 * len(role)], fill=P.ink)
        d.text((IX + D // 2, IY + D - 2), up(c["inset_name"]), font=nf, fill=P.white, anchor="ma")
        for i, r in enumerate(role):
            d.text((IX + D // 2, IY + D + 26 + 24 * i), r, font=sf, fill=P.yellow, anchor="ma")

    def cover_burst(self, page, c):
        """The starburst by the masthead, its lines tilted."""
        P, F, L = self.P, self.F, self.S.cover
        SBX, SBY, R = L.burst_x, L.main_top + L.burst_y, L.burst_radius
        pts = []
        for i in range(48):
            r = R if i % 2 == 0 else R * 0.86
            a = math.pi * 2 * i / 48
            pts.append((SBX + r * math.cos(a), SBY + r * math.sin(a)))
        burst = Image.new("RGBA", page.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(burst)
        bd.polygon([(x + 5, y + 6) for x, y in pts], fill=(0, 0, 0, 120))
        burst = burst.filter(ImageFilter.GaussianBlur(4))
        bd = ImageDraw.Draw(burst)
        bd.polygon(pts, fill=P.white)
        bd.polygon([(SBX + (x - SBX) * 0.9, SBY + (y - SBY) * 0.9) for x, y in pts], fill=P.red)
        txt = Image.new("RGBA", (2 * R, 2 * R), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        bl = [up(line) for line in c["burst"]]
        bf = F.bahn(min(24, fit(max(bl, key=len), lambda s: F.bahn(s), 2 * R - 40, 24)))
        y0 = R - len(bl) * 25 / 2 + 2
        for i, line in enumerate(bl):
            td.text((R, y0 + i * 25), line, font=bf, fill=P.white, anchor="ma")
        burst.alpha_composite(txt.rotate(-10, resample=Image.BICUBIC), (SBX - R, SBY - R))
        page.alpha_composite(burst)

    def cover_date(self, page):
        """The issue's Saturday, on a strip above the barcode block."""
        W, H, P, L = self.W, self.H, self.P, self.S.cover
        M, BH = L.margin, L.barcode_height
        d = ImageDraw.Draw(page)
        df = self.F.bahn(24)
        stamp = (datetime.strptime(self.issue["end"], "%Y-%m-%d")).strftime("%b %d, %Y").upper()
        sw = d.textlength(stamp, font=df) + 30
        d.rectangle([W - M - sw - 8, H - M - BH - 8 - 46, W - M - 8, H - M - BH - 8 - 12], fill=P.ink)
        d.text((W - M - 8 - sw / 2, H - M - BH - 8 - 43), stamp, font=df, fill=P.white, anchor="ma")

    def cover_bullets(self, page, c):
        H, P, L = self.H, self.P, self.S.cover
        M, row = L.margin, L.bullet_row
        d = ImageDraw.Draw(page)
        bf = self.F.bahn(40)
        BY = H - M - 3 * row - 18
        for i, b in enumerate(c["bullets"]):
            parts = [(b["text"] + " ", P.white), (up(b["hot"]), P.yellow)] if b.get("hot") else [(b["text"], P.white)]
            y = BY + i * row
            wtxt = sum(d.textlength(t, font=bf) for t, _ in parts)
            d.rectangle([M + 26, y, M + 26 + 46 + wtxt + 18, y + 50], fill=P.red)
            d.rectangle([M + 38, y + 14, M + 60, y + 36], fill=P.yellow)
            rich_line(d, M + 72, y + 3, parts, bf)

    def cover_barcode(self, page):
        """The barcode block: week, price, bars seeded by the issue number, the masthead."""
        W, H, P, F, L, T = self.W, self.H, self.P, self.F, self.S.cover, self.T
        M = L.margin
        d = ImageDraw.Draw(page)
        n = int(self.issue.get("issue") or 1)
        BW, BH = L.barcode_width, L.barcode_height
        bx, by = W - M - BW - 8, H - M - BH - 8
        d.rectangle([bx, by, bx + BW, by + BH], fill=P.white)
        week = f"{T.week_of} {self.WEEK}"
        d.text((bx + BW // 2, by + 8), week, font=F.bahn(min(17, fit(week, lambda s: F.bahn(s), BW - 10, 17))), fill=P.ink, anchor="ma")
        d.text((bx + BW // 2, by + 30), self.wc["price"], font=F.bahn(15), fill=P.ink, anchor="ma")
        random.seed(n + 40)
        x = bx + 14
        while x < bx + BW - 16:
            w = random.choice([1, 1, 2, 2, 3, 4])
            d.rectangle([x, by + 52, x + w - 1, by + 52 + 112], fill=P.ink)
            x += w + random.choice([1, 2, 2, 3])
        d.text((bx + 10, by + 168), f"{T.barcode}{n:02d}  1", font=F.bold(15), fill=P.ink)
        d.text((bx + BW - 12, by + 52), f"{n:02d}>", font=F.bold(14), fill=P.ink, anchor="ra")
        m1, m2 = self.wc["masthead"]
        d.rectangle([bx, by + BH - 26, bx + BW, by + BH], fill=P.ink)
        d.text((bx + BW // 2, by + BH - 23), f"{m1} {m2}", font=F.bahn(19), fill=P.white, anchor="ma")

    # 02 contents
    def p_contents(self) -> Image.Image:
        W, MX = self.W, self.MX
        P = self.P
        F, c = self.F, self.issue["contents"]
        p = self.page()
        d = self.chrome(p, 2, self.T.contents_section)
        y = self.title(d, self.T.contents_title, 104)
        st = self.stats
        strap = f"{st['shows']} SHOWS  ·  {st['preshows']} PRE-SHOWS  ·  " if st["preshows"] else f"{st['shows']} SHOWS  ·  "
        self.kicker_bar(d, MX, y + 4, [(strap, P.white), (f"{round(st['hours'])} HOURS LIVE", P.yellow)], size=30)
        y0, rh = y + 86, 136
        rows = c["rows"][:7]
        for i, r in enumerate(rows):
            ry = y0 + i * rh
            d.text((MX, ry + 30), str(i + 1), font=F.serif(60), fill=P.red)
            p.alpha_composite(self.faces.avatar(r["person"], 96), (MX + 64, ry + 16))
            tx = MX + 184
            head = up(r["headline"])
            hf = F.bahn(min(42, fit(head, lambda s: F.bahn(s), W - MX - 100 - tx, 42)))
            d.text((tx, ry + 22), head, font=hf, fill=P.ink)
            line = r["line"]
            lf = F.serif(26)
            while d.textlength(line, font=lf) > W - MX - 100 - tx and len(line) > 10:
                line = line[:-4].rstrip(" ,.") + "…"
            d.text((tx, ry + 74), line, font=lf, fill=P.muted)
            pill_w = 84
            d.rounded_rectangle([W - MX - pill_w, ry + 26, W - MX, ry + 64], 19, fill=P.ink)
            d.text((W - MX - pill_w // 2, ry + 45), up(r["day"])[:3], font=F.bahn(24), fill=P.white, anchor="mm")
            d.text((W - MX - pill_w // 2, ry + 74), f"p. {r['page']}", font=F.serif(26), fill=P.red, anchor="ma")
            if i < len(rows) - 1:
                d.line([MX, ry + rh - 4, W - MX, ry + rh - 4], fill=P.rule, width=2)
        return p

    # 03-05 articles
    def p_article(self, n: int, a: dict, feature: bool = False) -> tuple[Image.Image, int]:
        """The page, and how many px its text runs past the footer (0 when it fits)."""
        MX, CW = self.MX, self.CW
        P = self.P
        F = self.F
        p = self.page()
        d = self.chrome(p, n, up(a["section"]))
        y = self.kicker_bar(d, MX, 112, [(up(a["kicker"]) + "  ·  ", P.white), (up(a["topic"]), P.yellow)], size=30)
        # the cover story gets a shorter photo and smaller type: room for twice the words
        ph_h = 230 if feature else 360
        ph = self.faces.scene(a["person"], (CW, ph_h), centering=(0.5, 0.35))
        p.alpha_composite(rounded(ph, 22), (MX, y + 18))
        self.tag(p, d, MX + 18, y + 36, up(a["watch"]))
        y += 18 + ph_h + (22 if feature else 18)
        l1, l2 = up(a["headline"][0]), up(a["headline"][1])
        hi = up(a.get("highlight") or "")
        cap = 84 if feature else 104
        hf = F.impact(min(cap, fit(l1, F.impact, CW, cap), fit(l2, F.impact, CW, cap)))
        d.text((MX - 2, y), l1, font=hf, fill=P.ink)
        y += int(hf.size * 1.04)
        if hi and hi in l2:
            i = l2.index(hi)
            parts = [(l2[:i], False), (hi, True), (l2[i + len(hi):], False)]
        else:
            parts = [(l2, False)]
        self.highlight_text(d, (MX - 2, y), [(t, h) for t, h in parts if t], hf)
        y += int(hf.size * 1.14)
        y = para(d, (MX, y), a["deck"], F.serif(27 if feature else 32), CW, P.ink, 34 if feature else 40) + 8
        d.text((MX, y), self.wc["byline"], font=F.bahn(20, "SemiBold"), fill=P.red)
        y += 40
        colw = (CW - 40) // 2
        bf, lh = (F.bahn(22, "Regular"), 28) if feature else (F.bahn(25, "Regular"), 34)
        end1 = para(d, (MX, y), a["col1"], bf, colw, P.ink, lh)
        x2 = MX + colw + 40
        qy = para(d, (x2 + 22, y), f"“{a['quote']}”", F.serif(31), colw - 22, P.ink, 39)
        d.rectangle([x2, y + 4, x2 + 6, qy + 30], fill=P.red)
        d.text((x2 + 22, qy + 2), up(a["quote_by"]), font=F.bahn(20, "SemiBold"), fill=P.muted)
        end2 = para(d, (x2, qy + 52), a["col2"], bf, colw, P.ink, lh)
        return p, self.overflow(n, max(end1, end2))

    # 06 brief
    def p_brief(self) -> Image.Image:
        H, MX, CW = self.H, self.MX, self.CW
        P = self.P
        F, c = self.F, self.issue["brief"]
        p = self.page()
        d = self.chrome(p, 6, self.T.brief)
        y = self.title(d, self.T.brief, 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=P.muted)
        gx, gy, gap = MX, y + 54, 22
        cw, ch = (CW - gap) // 2, (H - 84 - 24 - (y + 54) - 2 * gap) // 3
        for i, card in enumerate(c["cards"][:6]):
            x0, y0 = gx + (i % 2) * (cw + gap), gy + (i // 2) * (ch + gap)
            ad = "PARODY" in up(card["label"]) or "SPONSORED" in up(card["label"])
            d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 22, fill=P.ink if ad else P.card)
            p.alpha_composite(self.faces.avatar(card["person"], 84), (x0 + 18, y0 + 18))
            self.bullet(d, x0 + 124, y0 + 34, 16)
            lf = F.bahn(min(22, fit(up(card["label"]), lambda s: F.bahn(s, "SemiBold"), cw - 170, 22)), "SemiBold")
            d.text((x0 + 150, y0 + 30), up(card["label"]), font=lf, fill=P.yellow if ad else P.red)
            hl = wrap(d, up(card["headline"]), F.bahn(44), cw - 44)
            hs = 44 if len(hl) <= 2 else 36
            yy = para(d, (x0 + 22, y0 + 122), up(card["headline"]), F.bahn(hs), cw - 44, P.white if ad else P.ink, hs + 4) + 8
            end = para(d, (x0 + 22, yy), card["body"], F.bahn(26, "Regular"), cw - 44, P.grey if ad else P.muted, 34)
            if end > y0 + ch - 6:
                self.warnings.append(f"page 06: card {i + 1} text runs {end - (y0 + ch - 6)}px past its card")
        return p

    # 07 quote
    def p_quote(self) -> Image.Image:
        W, MX, CW = self.W, self.MX, self.CW
        P = self.P
        F, c = self.F, self.issue["quote"]
        p = self.page(dark=True)
        d = self.chrome(p, 7, self.T.quote, dark=True)
        d.text((MX - 6, 92), "“", font=F.serif(260, bold=True), fill=P.yellow)
        text = c["text"]
        qs = 74
        while len(wrap(d, text, F.serif(qs), CW)) > 5 and qs > 44:
            qs -= 4
        y = para(d, (MX, 318), text, F.serif(qs), CW, P.white, int(qs * 1.22)) + 34
        p.alpha_composite(self.faces.avatar(c["person"], 112, ring=P.yellow), (MX, y))
        d.text((MX + 140, y + 16), up(c["name"]), font=F.bahn(46), fill=P.white)
        d.text((MX + 140, y + 70), c["where"], font=F.bahn(25, "Regular"), fill=P.grey)
        y += 170
        d.line([MX, y, W - MX, y], fill=(70, 70, 70), width=2)
        y += 34
        d.rectangle([MX, y + 8, MX + 40, y + 14], fill=P.yellow)
        d.text((MX + 54, y - 4), self.T.runners_up, font=F.bahn(28), fill=P.yellow)
        y += 56
        for r in c["runners_up"][:3]:
            y = para(d, (MX, y), f"“{r['text']}”", F.serif(39), CW, P.white, 50) + 4
            d.text((MX, y), r["by"], font=F.bahn(25, "SemiBold"), fill=P.grey)
            y += 56
        self.overflow(7, y - 20)
        return p

    # 08 numbers
    def p_numbers(self) -> Image.Image:
        W, H, MX, CW = self.W, self.H, self.MX, self.CW
        P = self.P
        F, c, st = self.F, self.issue["numbers"], self.stats
        p = self.page()
        d = self.chrome(p, 8, self.T.numbers_section)
        y = self.title(d, self.T.numbers_title, 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=P.muted)
        y = self.kicker_bar(d, MX, y + 50, [(f"{self.T.screen_time}  ·  ", P.white), (self.T.screen_time_sub, P.yellow)], size=28)
        mins = st["screen_minutes"]
        cast = [m for m in c["cast"] if mins.get(m["topic"])][:6]
        cast.sort(key=lambda m: -mins[m["topic"]])
        if not cast:
            cast = [{"person": t, "name": up(t), "topic": t} for t in list(mins)[:6]]
        top = max(mins[m["topic"]] for m in cast)
        rows_y, rh = y + 18, 66
        bx0, bx1 = MX + 300, W - MX - 130
        for i, m in enumerate(cast):
            ry = rows_y + i * rh
            p.alpha_composite(self.faces.avatar(m["person"], 52), (MX, ry + 2))
            d.text((MX + 72, ry + 10), up(m["name"]), font=F.bahn(min(32, fit(up(m["name"]), lambda s: F.bahn(s), 215, 32))), fill=P.ink)
            bw = max(8, int((bx1 - bx0) * mins[m["topic"]] / top))
            d.rounded_rectangle([bx0, ry + 12, bx1, ry + 48], 8, fill=P.card)
            d.rounded_rectangle([bx0, ry + 12, bx0 + bw, ry + 48], 8, fill=P.red if i == 0 else P.ink)
            d.text((W - MX, ry + 12), hm(mins[m["topic"]]), font=F.bahn(30, "SemiBold"), fill=P.red if i == 0 else P.ink, anchor="ra")
        cx, cy = MX + 30, rows_y - 14
        d.polygon([(cx - 22, cy + 18), (cx - 22, cy), (cx - 11, cy + 9), (cx, cy - 6), (cx + 11, cy + 9),
                   (cx + 22, cy), (cx + 22, cy + 18)], fill=P.yellow, outline=P.ink, width=2)
        y = rows_y + len(cast) * rh + 8
        rest = sum(mins[m["topic"]] for m in cast[1:])
        line = c["lolcow_line"]
        if "put together" in line.lower() and mins[cast[0]["topic"]] <= rest:
            line = f"More airtime than anyone else this week: {hm(mins[cast[0]['topic']])} of it."
            self.warnings.append("page 08: 'put together' line was false; replaced")
        lf = F.bahn(36)
        label = f"{self.T.lolcow}: {up(c['lolcow'] or cast[0]['name'])}"
        d.rectangle([MX - 6, y + 4, MX + d.textlength(label, font=lf) + 6, y + 44], fill=P.yellow)
        d.text((MX, y), label, font=lf, fill=P.ink)
        y = para(d, (MX, y + 50), line, F.serif(29), CW, P.ink, 36) + 20
        gap = 22
        cw = (CW - gap) // 2
        ch = (H - 84 - 24 - y - gap) // 2
        for i, card in enumerate(c["cards"][:4]):
            x0, y0 = MX + (i % 2) * (cw + gap), y + (i // 2) * (ch + gap)
            d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 22, fill=P.card)
            nf = F.impact(min(96, fit(card["number"], F.impact, cw - 48, 96)))
            d.text((x0 + 24, y0 + 14), card["number"], font=nf, fill=P.red if card.get("red") else P.ink)
            d.text((x0 + 24, y0 + ch - 84), up(card["label"]), font=F.bahn(min(28, fit(up(card["label"]), lambda s: F.bahn(s), cw - 48, 28))), fill=P.ink)
            d.text((x0 + 24, y0 + ch - 46), card["sub"], font=F.bahn(min(23, fit(card["sub"], lambda s: F.bahn(s, "Regular"), cw - 48, 23)), "Regular"), fill=P.muted)
        return p

    # 09 marketplace
    def p_market(self) -> Image.Image:
        W, H, MX, CW = self.W, self.H, self.MX, self.CW
        P = self.P
        F, c = self.F, self.issue["market"]
        p = self.page()
        d = self.chrome(p, 9, self.T.market_section)
        y = self.title(d, self.T.market_title, 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=P.muted)
        y += 54
        bh = 400
        d.rounded_rectangle([MX, y, W - MX, y + bh], 24, fill=P.ink)
        d.rounded_rectangle([MX + 30, y + 30, MX + 190, y + 70], 20, fill=P.yellow)
        d.text((MX + 110, y + 50), self.T.market_badge, font=F.bahn(22), fill=P.ink, anchor="mm")
        name = up(c["big_name"])
        nf = F.impact(min(96, fit(name, F.impact, 600, 96)))
        d.text((MX + 30, y + 96), name, font=nf, fill=P.white)
        para(d, (MX + 32, y + 214), c["big_body"], F.bahn(32, "Regular"), 560, P.grey, 42)
        d.text((MX + 32, y + bh - 64), c["big_tag"], font=F.serif(28), fill=P.yellow)
        # product: a price starburst on a yellow badge
        bx, by = W - MX - 290, y + 70
        pts = []
        for i in range(32):
            r = 130 if i % 2 == 0 else 104
            a = math.pi * 2 * i / 32
            pts.append((bx + 145 + r * math.cos(a), by + 140 + r * math.sin(a)))
        d.polygon(pts, fill=P.yellow, outline=P.white, width=4)
        price = c["big_price"]
        pf = F.impact(min(84, fit(price, F.impact, 190, 84)))
        d.text((bx + 145, by + 140), price, font=pf, fill=P.ink, anchor="mm")
        y += bh + 22
        gap = 22
        cw, ch = (CW - gap) // 2, (H - 84 - 24 - y - gap) // 2
        for i, ad in enumerate(c["ads"][:4]):
            x0, y0 = MX + (i % 2) * (cw + gap), y + (i // 2) * (ch + gap)
            d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 22, fill=P.card)
            d.text((x0 + 22, y0 + 22), f"AD  ·  {up(ad['label'])}", font=F.bahn(20, "SemiBold"), fill=P.red)
            p.alpha_composite(self.faces.avatar(ad["person"], 76), (x0 + cw - 100, y0 + 16))
            yy = para(d, (x0 + 22, y0 + 60), up(ad["name"]), F.bahn(38), cw - 140, P.ink, 42) + 6
            end = para(d, (x0 + 22, max(yy, y0 + 112)), ad["body"], F.bahn(24, "Regular"), cw - 44, P.muted, 31)
            if end > y0 + ch - 6:
                self.warnings.append(f"page 09: ad {i + 1} text runs {end - (y0 + ch - 6)}px past its card")
        return p

    # 10 next week + poll
    def p_back(self) -> Image.Image:
        W, H, MX, CW = self.W, self.H, self.MX, self.CW
        P = self.P
        F, c = self.F, self.issue["back"]
        p = self.page()
        d = self.chrome(p, 10, self.T.next_week, cta=self.T.follow)
        y = self.title(d, self.T.next_week, 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=P.muted)
        y += 50
        sched = next_week(self.cfg, self.week)
        rh = 74
        for i, (day, date, show, t) in enumerate(sched[:6]):
            ry = y + i * rh
            d.text((MX, ry + 6), day, font=F.impact(58), fill=P.red)
            d.text((MX + 140, ry + 14), date, font=F.serif(34), fill=P.ink)
            d.text((MX + 300, ry + 16), up(show), font=F.bahn(36), fill=P.ink)
            d.text((W - MX, ry + 20), t, font=F.bahn(28, "SemiBold"), fill=P.muted, anchor="ra")
            d.line([MX, ry + rh - 2, W - MX, ry + rh - 2], fill=P.rule, width=2)
        y += max(len(sched[:6]), 3) * rh + 30
        bottom = H - 84 - 24
        d.rounded_rectangle([MX, y, W - MX, bottom], 26, fill=P.ink)
        d.rounded_rectangle([MX + 30, y + 30, MX + 230, y + 70], 20, fill=P.yellow)
        d.text((MX + 130, y + 50), self.T.poll, font=F.bahn(22), fill=P.ink, anchor="mm")
        q = up(c["poll"])
        d.text((MX + 30, y + 92), q, font=F.impact(min(84, fit(q, F.impact, CW - 60, 84))), fill=P.white)
        ow = (CW - 60 - 2 * 18) // 3
        oy = y + 200
        oh = min(220, bottom - oy - 70)
        for i, o in enumerate(c["options"][:3]):
            ox = MX + 30 + i * (ow + 18)
            d.rounded_rectangle([ox, oy, ox + ow, oy + oh], 18, fill=(44, 44, 44))
            av = min(128, oh - 70)
            p.alpha_composite(self.faces.avatar(o["person"], av, ring=P.yellow), (ox + (ow - av - 8) // 2, oy + 18))
            d.text((ox + ow // 2, oy + oh - 34), up(o["name"]), font=F.bahn(min(40, fit(up(o["name"]), lambda s: F.bahn(s), ow - 20, 40))), fill=P.white, anchor="mm")
        d.text((MX + 30, oy + oh + 30), c["note"], font=F.bahn(28, "Regular"), fill=P.grey)
        return p

    def render(self, only: set[str] | None = None) -> list[Path]:
        """The pages; on the square canvas scaled to 1080x1080 in youtube/, for YouTube Posts."""
        square = self.square
        fns = {"01_cover": self.p_cover, "02_contents": self.p_contents,
               "03_feature": lambda: self.p_article(3, self.issue["feature"], feature=True)[0],
               "04_article": lambda: self.p_article(4, self.issue["articles"][0])[0],
               "05_article": lambda: self.p_article(5, self.issue["articles"][1])[0],
               "06_brief": self.p_brief, "07_quote": self.p_quote, "08_numbers": self.p_numbers,
               "09_market": self.p_market, "10_back": self.p_back}
        out = []
        for name in PAGES:
            if only and name[:2] not in only and name not in only:
                continue
            self.faces.used.clear()
            img = fns[name]().convert("RGB")
            if square:
                img = img.resize((1080, 1080), Image.LANCZOS)
                (self.here / "youtube").mkdir(exist_ok=True)
            dst = self.here / ("youtube" if square else "") / f"{name}.jpg"
            img.save(dst, quality=92)
            out.append(dst)
            print(f"  {dst.name}")
        return out


def contact_sheet(pages: list[Path], dst: Path) -> None:
    first = Image.open(pages[0])
    tw, th = 360, int(360 * first.height / first.width)
    cols = 5
    rows = (len(pages) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 10) + 10, rows * (th + 10) + 10), (40, 40, 40))
    for i, p in enumerate(pages):
        im = Image.open(p).convert("RGB").resize((tw, th), Image.LANCZOS)
        sheet.paste(im, (10 + (i % cols) * (tw + 10), 10 + (i // cols) * (th + 10)))
    sheet.save(dst, quality=85)


def post_text(issue: dict, here: Path) -> None:
    n = int(issue.get("issue") or 1)
    post = issue.get("post") or {}
    text = (f"# {issue.get('week')}: issue {n:02d}\n\n"
            f"Pages: 01-10 (1080x1440). TikTok: one carousel of all ten. YouTube Posts show images square: "
            f"use youtube/01-10 (1080x1080), 5 per post: part 1 = 01-05, part 2 = 06-10.\n\n"
            f"## Part 1 (01-05)\n\n{post.get('part1', '')}\n\n## Part 2 (06-10)\n\n{post.get('part2', '')}\n")
    (here / "post_text.md").write_text(text, encoding="utf-8")

