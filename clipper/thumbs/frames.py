"""Where a cover's pictures come from: candidate frames grabbed from the cut video (clips
around their hook and payoff plus chat-heat peaks; segments and compilations evenly spaced
plus their hottest chat moments; black or blurry frames nudged a little, then skipped),
named people's best faces from faces.json (`clipper faces`), and the [thumbnail] photos portraits."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from .. import jsonstate
from ..meta import duration


def asset_key(x: dict) -> str:
    return f"{x['video'].parent.name}/{x['video'].stem}"


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
        side = jsonstate.load(x["video"].with_suffix(".json"))
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


def face_frames(x: dict, data: dict, tc: dict, out_dir: Path, start: int) -> list[dict]:
    """faces.json (`clipper faces`): frames where a named person's face was found in this
    video, [thumbnail] face_frames of them for each of the face_people people seen most,
    each with "faces": every named face in it ({"person", "face"}). Numbered on from start."""
    from ..faces import local_time, video_faces

    out = []
    for t_src, found in video_faces(data, x["parts"], tc["face_people"], tc["face_frames"]):
        t = round(local_time(x["parts"], t_src), 2)
        dst = out_dir / f"{start + len(out):02d}_{t:.1f}_faces.jpg"
        img = grab(x["video"], t, dst)
        if img is None:
            continue
        luma, sharp = frame_stats(img)
        out.append({"t": t, "path": dst, "brightness": round(luma, 1), "sharpness": round(sharp, 1),
                    "faces": found})
    return out


# ---------- [thumbnail] photos ----------

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")


def photos(tc: dict) -> dict[str, Path]:
    """[thumbnail] photos: {person: path}, named by the file names ("Hasan Piker.jpg")."""
    d = Path(tc["photos"]) if tc.get("photos") else None
    if not d or not d.is_dir():
        return {}
    return {p.stem: p for p in sorted(d.iterdir()) if p.suffix.lower() in PHOTO_EXT}


def photo_name(tc: dict, name: str) -> str:
    """The on-file spelling of a photo's person, matched ignoring case; "" if none."""
    return next((n for n in tc.get("_photos", {}) if n.lower() == (name or "").strip().lower()), "")
