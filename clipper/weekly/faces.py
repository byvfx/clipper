"""Faces for the pages: person -> a face crop, a wide scene, a cut-out, a photo on file or
initials, from the frames the week's thumbnail stage picked (thumbnails.json)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..errors import ClipperError
from . import style


class Faces:
    """person -> a square face crop, a frame, or initials; from the week's thumbnails.json."""

    def __init__(self, cfg: dict, week: dict, pinned: dict | None = None, here: Path | None = None,
                 palette=None):
        from ..thumbs import photos as photo_dir

        self.P = palette or style.DEFAULT.palette

        self.by_person: dict[str, list[dict]] = defaultdict(list)
        for s in week["streams"].values():
            for f in s["faces"]:
                self.by_person[f["person"].lower()].append(f)
        for v in self.by_person.values():
            v.sort(key=lambda f: -f["size"])
        self.photos = {k.lower(): v for k, v in photo_dir(cfg["thumbnail"]).items()}
        root = Path(cfg["paths"]["streams"])
        for person, pin in (pinned or {}).items():
            # issue.json "faces": {"Name": "kick-1/segments/s16_x"} picks that stream's thumbnail
            # frame; {"Name": {"frame": "kick-1/thumbs/.../05_1.0.jpg", "face": {x, y, width, height}}}
            # pins any frame with a box the crops centre on, and "bottom" (0-1) where the cut-out
            # stops, to keep a caption out of it.
            if isinstance(pin, dict) and pin.get("photo"):
                # {"Name": {"photo": "hila_klein.png"}}: a portrait in the issue folder, the whole
                # picture is the face (as [thumbnail] photos)
                photo = Path(pin["photo"])
                self.photos[person.lower()] = photo if photo.is_absolute() else (here or Path()) / photo
                self.by_person.pop(person.lower(), None)
                continue
            if isinstance(pin, str):
                sid, _, key = pin.partition("/")
                hit = next((f for f in week["streams"].get(sid, {}).get("faces", []) if f["key"] == key), None)
                if not hit:
                    raise ClipperError(f"issue.json faces: no thumbnail {key!r} in {sid}")
                pin = {"frame": hit["frame"], "face": hit["face"]}
            frame = Path(pin["frame"])
            if not frame.is_absolute():
                frame = root / frame
            self.by_person[person.lower()].insert(0, {"person": person, "key": "pinned", "frame": str(frame),
                                                      "face": pin["face"], "size": pin["face"]["height"],
                                                      "bottom": pin.get("bottom", 1.0)})  # cut-out stops here (a caption below)
        self.colours: dict[str, tuple] = {}
        self.used: dict[str, int] = Counter()

    def best(self, person: str, nth: int = 0) -> dict | None:
        fs = self.by_person.get((person or "").lower())
        if not fs:
            return None
        return fs[min(nth, len(fs) - 1)]

    def square(self, person: str, D: int, nth: int | None = None) -> Image.Image:
        """A DxD face crop (face a little above centre), the photo on file, or initials."""
        key = (person or "").lower()
        fs = self.by_person.get(key) or []
        pinned = bool(fs) and fs[0]["key"] == "pinned"
        f = self.best(person, 0 if pinned else (self.used[key] if nth is None else nth))
        if f and Path(f["frame"]).exists():
            self.used[key] += 1
            im = Image.open(f["frame"]).convert("RGB")
            b = f["face"]
            w, h = im.size
            cx, cy = (b["x"] + b["width"] / 2) * w, (b["y"] + b["height"] / 2) * h
            r = max(b["width"] * w, b["height"] * h) * 0.68
            r = min(r, cx, cy, w - cx, h - cy) if r > min(cx, cy, w - cx, h - cy) else r
            crop = im.crop((int(cx - r), int(cy - r * 1.05), int(cx + r), int(cy + r * 0.95)))
            return ImageOps.fit(crop, (D, D), Image.LANCZOS)
        if key in self.photos:
            im = Image.open(self.photos[key]).convert("RGB")
            r = int(im.width * 0.42)
            cx, cy = im.width // 2, int(im.height * 0.45)
            return im.crop((cx - r, cy - r, cx + r, cy + r)).resize((D, D), Image.LANCZOS)
        return self.initials(person, D)

    def initials(self, person: str, D: int) -> Image.Image:
        col = self.colours.setdefault(person, self.P.initials[len(self.colours) % len(self.P.initials)])
        txt = "".join(p[0] for p in (person or "?").split()[:2]).upper() or "?"
        img = Image.new("RGB", (D, D), col)
        ImageDraw.Draw(img).text((D // 2, D // 2), txt, font=self.fonts.bahn(int(D * 0.46)), fill=self.P.white, anchor="mm")
        return img

    def avatar(self, person: str, D: int, ring=None) -> Image.Image:
        img = self.square(person, D)
        m = Image.new("L", (D, D), 0)
        ImageDraw.Draw(m).ellipse([0, 0, D - 1, D - 1], fill=255)
        out = Image.new("RGBA", (D + 8, D + 8), (0, 0, 0, 0))
        if ring:
            ImageDraw.Draw(out).ellipse([0, 0, D + 7, D + 7], fill=ring)
        out.paste(img, (4, 4), m)
        return out

    def frame(self, person: str) -> Image.Image | None:
        f = self.best(person)
        if f and Path(f["frame"]).exists():
            return Image.open(f["frame"]).convert("RGB")
        return None

    def scene(self, person: str, size: tuple[int, int], centering=(0.5, 0.4)) -> Image.Image:
        """A wide photo around the person's face for article headers."""
        f = self.best(person)
        if not f and (person or "").lower() in self.photos:
            return ImageOps.fit(Image.open(self.photos[person.lower()]).convert("RGB"), size, Image.LANCZOS,
                                centering=(0.5, 0.3))
        if not (f and Path(f["frame"]).exists()):
            im = Image.new("RGB", size, self.P.card)
            av = self.avatar(person, min(size) - 80)
            im.paste(av, ((size[0] - av.width) // 2, (size[1] - av.height) // 2), av)
            return im
        im = Image.open(f["frame"]).convert("RGB")
        b = f["face"]
        w, h = im.size
        cx, cy = (b["x"] + b["width"] / 2) * w, (b["y"] + b["height"] / 2) * h
        fh = b["height"] * h
        hb = h * f.get("bottom", 1.0)  # a pinned frame may end above a burned-in caption
        ch = min(hb, max(fh * 3.2, size[1] * 0.6))
        cw = min(w, ch * size[0] / size[1])
        ch = cw * size[1] / size[0]
        x0 = min(max(cx - cw / 2, 0), w - cw)
        y0 = min(max(cy - ch * 0.42, 0), hb - ch)
        return im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize(size, Image.LANCZOS)

    def cutout(self, person: str, here: Path) -> Image.Image | None:
        """RGBA cut-out of the person (DeepLabV3, cached in the issue folder); None without torch."""
        f = self.best(person)
        if not (f and Path(f["frame"]).exists()):
            return None
        cache = here / f"cutout_{re.sub(r'[^a-z0-9]+', '-', person.lower())}_{Path(f['frame']).stem}.png"
        if cache.exists():
            return Image.open(cache).convert("RGBA")
        try:
            import numpy as np
            import torch
            from torchvision.models.segmentation import (
                DeepLabV3_ResNet101_Weights,
                deeplabv3_resnet101,
            )
        except ImportError:
            return None
        im = Image.open(f["frame"]).convert("RGB")
        w, h = im.size
        b = f["face"]
        fw, fh = b["width"] * w, b["height"] * h
        cx, top = (b["x"] + b["width"] / 2) * w, b["y"] * h
        box = (int(max(0, cx - fw * 1.8)), int(max(0, top - fh * 0.5)),
               int(min(w, cx + fw * 1.8)), int(min(h * f.get("bottom", 1.0), top + fh * 4.2)))
        crop = im.crop(box)
        weights = DeepLabV3_ResNet101_Weights.DEFAULT
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        model = deeplabv3_resnet101(weights=weights).eval().to(dev)
        x = weights.transforms()(crop).unsqueeze(0).to(dev)
        with torch.no_grad():
            logits = model(x)["out"][0]
        prob = torch.softmax(logits, 0)[15]  # VOC class 15: person
        prob = torch.nn.functional.interpolate(prob[None, None], size=crop.size[::-1], mode="bilinear")[0, 0]
        mask = Image.fromarray((prob.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8))
        mask = mask.point(lambda v: 255 if v > 140 else (0 if v < 90 else int((v - 90) * 255 / 50)))
        mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
        if np.asarray(mask).mean() < 8:  # nothing found
            return None
        crop.putalpha(mask)
        crop.save(cache)
        return crop

