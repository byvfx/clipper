"""`clipper faces`: who is on screen, by face (issue #54).

Usage:
    clipper faces <stream>                      scan the keyframes (first time only), group the
                                                faces, name the groups against the known faces
    clipper faces <stream> --rescan             scan again
    clipper faces <stream> --name F03=Jer [--enroll]
                                                name a group by hand ("F03=" clears it); --enroll
                                                keeps that face for every show
    clipper faces --enroll-photos               add the [thumbnail] photos portraits to the known faces

Every keyframe of source.* (one every ~4 s on Kick) goes through YuNet (a face detector) and SFace
(128 numbers per face), two small ONNX models OpenCV runs on the CPU (the `faces` extra); they
download once into [paths] faces/models, pinned by hash. Frames are pulled by ffmpeg while
[faces] workers processes read them. Faces at least [faces] min_px tall are grouped by
average-linkage clustering (cosine, [faces] cluster); groups under [faces] min_faces are dropped.
Each group's centroid is matched against the known faces ([paths] faces/known.json, one file for
every show: Jer turns up on Kino and Channel 404), named when the best is at least [faces] match.
Names set by hand win, and a rescan keeps them for the group that looks the same.

faces.json: the groups (F01 biggest first: count, time span, centroid, name or closest match) and
every grouped face (stream time, box 0-1 of the frame, height in px, sharpness, frontal 0-1,
similarity to its group). faces/F01.jpg: each group's best faces, then a spread over the stream,
to tell who it is. best_faces() is a person's faces in a stretch, best for a cover first.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from . import jsonstate
from .errors import ClipperError
from .timefmt import hms

MODEL = "yunet-2023mar+sface-2021dec"  # the face space; known faces from another model aren't matched
ZOO = "https://github.com/opencv/opencv_zoo/raw/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/"
MODELS = {  # file: (zoo folder, sha256)
    "face_detection_yunet_2023mar.onnx":
        ("face_detection_yunet", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx":
        ("face_recognition_sface", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
SAME_GROUP = 0.9  # a rescan's group this like a hand-named old one keeps its name
TILE = 160


def model_dir(cfg: dict) -> Path:
    return Path(cfg["paths"]["faces"]) / "models"


def known_path(cfg: dict) -> Path:
    return Path(cfg["paths"]["faces"]) / "known.json"


def ensure_models(cfg: dict) -> Path:
    """The model folder, downloading what's missing (checked against its hash)."""
    import requests

    d = model_dir(cfg)
    for name, (folder, sha) in MODELS.items():
        p = d / name
        if p.exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        print(f"downloading {name}")
        r = requests.get(ZOO + f"{folder}/{name}", timeout=120)
        r.raise_for_status()
        if hashlib.sha256(r.content).hexdigest() != sha:
            raise ClipperError(f"{name}: downloaded file doesn't match its pinned hash")
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(p)
    return d


# ---------- detection (worker processes) ----------

_det = _rec = None
_min_px = 0


def _init(models: str, min_px: int) -> None:
    import cv2

    global _det, _rec, _min_px
    cv2.setNumThreads(1)
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # OpenCV 5 warns on every model load
    _det =cv2.FaceDetectorYN.create(f"{models}/face_detection_yunet_2023mar.onnx", "", (320, 320), 0.7, 0.3, 5000)
    _rec = cv2.FaceRecognizerSF.create(f"{models}/face_recognition_sface_2021dec.onnx", "")
    _min_px = min_px


def _detect(path: str) -> list[tuple[dict, np.ndarray]]:
    """Each face at least min_px tall in one frame: (row, unit-length embedding)."""
    import cv2

    img = cv2.imread(path)
    if img is None:
        return []
    h, w = img.shape[:2]
    _det.setInputSize((w, h))
    _, found = _det.detect(img)
    out = []
    for f in found if found is not None else []:
        x, y, fw, fh = (float(v) for v in f[:4])
        if fh < _min_px:
            continue
        crop = img[max(int(y), 0):int(y + fh), max(int(x), 0):int(x + fw)]
        if not crop.size:
            continue
        gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
        eye_r, eye_l, nose = f[4:6], f[6:8], f[8:10]
        eyes = float(np.linalg.norm(eye_r - eye_l)) or 1.0
        e = _rec.feature(_rec.alignCrop(img, f)).flatten()
        row = {"box": [round(min(max(v, 0.0), 1.0), 4) for v in (x / w, y / h, fw / w, fh / h)],
               "px": int(fh), "sharp": round(float(cv2.Laplacian(gray, cv2.CV_32F).var()), 1),
               "frontal": round(max(0.0, 1 - abs(float(nose[0]) - float(eye_r[0] + eye_l[0]) / 2) / eyes), 2)}
        out.append((row, (e / np.linalg.norm(e)).astype(np.float32)))
    return out


def scan_frames(src: Path, frames: Path, fc: dict, models: Path) -> tuple[list[float], list[dict], np.ndarray]:
    """Every keyframe through the detector while ffmpeg is still pulling them: (the keyframe
    times, the faces with "t" and "i", the frame number, and their embeddings)."""
    log = frames / "ffmpeg.log"
    with open(log, "w", encoding="utf-8") as err:
        proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-skip_frame", "nokey", "-i", str(src),
                                 "-an", "-fps_mode", "passthrough", "-vf", "showinfo", "-q:v", "3",
                                 str(frames / "%05d.jpg")], stderr=err)
        n = fc["workers"] or max(1, (os.cpu_count() or 2) // 2)
        pending, i, t0 = [], 1, time.time()
        try:
            with ProcessPoolExecutor(n, initializer=_init, initargs=(str(models), fc["min_px"])) as ex:
                while True:
                    running = proc.poll() is None
                    # a frame is whole once the next one is started, or ffmpeg is done
                    while (frames / f"{i:05d}.jpg").exists() and (not running or (frames / f"{i + 1:05d}.jpg").exists()):
                        pending.append(ex.submit(_detect, str(frames / f"{i:05d}.jpg")))
                        i += 1
                        if i % 1000 == 0:
                            print(f"  {i} frames, {time.time() - t0:.0f}s")
                    if not running:
                        break
                    time.sleep(0.5)
                found = [fut.result() for fut in pending]
        finally:  # a failed worker (or Ctrl-C) mustn't leave ffmpeg reading the source
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    if proc.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
        raise ClipperError(f"ffmpeg failed on {src.name}: {' '.join(tail)}")
    times = [float(t) for t in re.findall(r"pts_time:\s*(-?[\d.]+)",
                                          log.read_text(encoding="utf-8", errors="replace"))]
    if len(times) != len(found):
        raise ClipperError(f"{src.name}: {len(found)} keyframes but {len(times)} times from ffmpeg")
    rows, embs = [], []
    for k, (t, faces) in enumerate(zip(times, found), 1):
        for row, e in faces:
            rows.append({"t": round(t, 2), "i": k, **row})
            embs.append(e)
    print(f"{len(found)} keyframes, {len(rows)} faces in {time.time() - t0:.0f}s ({n} workers)")
    return times, rows, np.array(embs, dtype=np.float32).reshape(-1, 128)


# ---------- grouping and names ----------

def unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def cluster(emb: np.ndarray, threshold: float, max_n: int = 8000) -> np.ndarray:
    """A group label per embedding (0 = none). Average linkage over at most max_n of them,
    evenly picked (the distance matrix grows with the square); the rest join the group whose
    centroid they're most like, if at least threshold."""
    from scipy.cluster.hierarchy import fcluster, linkage

    if len(emb) < 2:
        return np.ones(len(emb), dtype=int)
    idx = np.arange(0, len(emb), max(1, math.ceil(len(emb) / max_n)))
    picked = fcluster(linkage(emb[idx], "average", metric="cosine"), t=1 - threshold, criterion="distance")
    cents = np.array([unit(emb[idx][picked == g].mean(0)) for g in range(1, picked.max() + 1)])
    sims = emb @ cents.T
    out = np.where(sims.max(1) >= threshold, sims.argmax(1) + 1, 0)
    out[idx] = picked
    return out


def build_groups(rows: list[dict], emb: np.ndarray, fc: dict) -> tuple[dict, list[dict]]:
    """(groups F01.. biggest first, the faces in them in time order with "group" and "sim")."""
    lab = cluster(emb, fc["cluster"])
    keep = [g for g, n in Counter(lab[lab > 0].tolist()).most_common() if n >= fc["min_faces"]]
    groups, faces = {}, []
    for k, g in enumerate(keep, 1):
        idx = np.flatnonzero(lab == g)
        c = unit(emb[idx].mean(0))
        sims = emb[idx] @ c
        key = f"F{k:02d}"
        ts = [rows[i]["t"] for i in idx]
        groups[key] = {"n": len(idx), "first_s": min(ts), "last_s": max(ts),
                       "median_px": int(np.median([rows[i]["px"] for i in idx])),
                       "cohesion": round(float(sims.mean()), 3),
                       "floor": round(float(np.percentile(sims, 25)), 3),  # best_faces skips below this
                       "centroid": [round(float(v), 4) for v in c]}
        faces += [{**rows[i], "group": key, "sim": round(float(s), 3)} for i, s in zip(idx, sims)]
    faces.sort(key=lambda f: f["t"])
    return groups, faces


def carry_names(groups: dict, old: dict) -> None:
    """Hand names from an earlier scan, onto the new group most like each named one."""
    if not groups:
        return
    keys = list(groups)
    cents = np.array([groups[k]["centroid"] for k in keys])
    for g in old.values():
        if g.get("by") == "hand" and g.get("centroid"):
            sims = cents @ np.array(g["centroid"])
            if sims.max() >= SAME_GROUP:
                groups[keys[int(sims.argmax())]].update(name=g["name"], by="hand")


def load_known(cfg: dict) -> dict:
    try:
        return jsonstate.load(known_path(cfg))
    except FileNotFoundError:
        return {"model": MODEL, "people": {}}


def closest(centroid: list[float], people: dict) -> tuple[str, float]:
    """The known person most like this centroid, and how much (best of their samples)."""
    c, best, score = np.array(centroid), "", 0.0
    for name, refs in people.items():
        s = float((np.array(refs) @ c).max())
        if s > score:
            best, score = name, s
    return best, score


def name_groups(groups: dict, people: dict, match: float) -> None:
    """Each group's closest known face; named by it when at least match, unless named by hand."""
    for g in groups.values():
        who, score = closest(g["centroid"], people) if people else ("", 0.0)
        g.update(closest=who, match=round(score, 3))
        if g.get("by") != "hand":
            g.pop("name", None)
            g.pop("by", None)
            if who and score >= match:
                g.update(name=who, by="face")


def apply(sdir: Path, cfg: dict) -> dict:
    """Name the groups in faces.json against the known faces; the groups."""
    data = jsonstate.load(sdir / "faces.json")
    known = load_known(cfg)
    if known["people"] and known["model"] != data["model"]:
        print(f"known faces are from {known['model']}, this stream from {data['model']}: not matching")
        known = {"people": {}}
    name_groups(data["groups"], known["people"], cfg["faces"]["match"])
    data["known"] = str(known_path(cfg))
    jsonstate.write(sdir / "faces.json", data, indent=None)
    return data["groups"]


def quality(f: dict) -> float:
    """How good a face is for a cover: sharp, facing the camera, big (up to 200 px)."""
    return f["sharp"] * f["frontal"] * min(f["px"], 200) / 200


def best_faces(data: dict, person: str, a: float = 0.0, b: float = math.inf, n: int = 8,
               gap: float = 30.0) -> list[dict]:
    """A person's faces in [a, b) of stream time, best first, at least gap seconds apart (a
    still on screen for a minute is one pick, not eight). The quarter of each group least like
    it is skipped: strays that slipped in (a cartoon, a lookalike)."""
    who = person.strip().lower()
    groups = {k: g for k, g in data["groups"].items() if (g.get("name") or "").lower() == who}
    fs = [f for f in data["faces"]
          if f["group"] in groups and a <= f["t"] < b and f["sim"] >= groups[f["group"]]["floor"]]
    out: list[dict] = []
    for f in sorted(fs, key=quality, reverse=True):
        if len(out) >= n:
            break
        if all(abs(f["t"] - o["t"]) >= gap for o in out):
            out.append(f)
    return out


def head_box(box: list[float], size: tuple[int, int] | None = None) -> dict:
    """A detected face box [x, y, w, h] (0-1 of the source frame) as the cover's face box:
    the whole head (hair, a hat) as thumbnails frames.grab crops the frame to 16:9."""
    x, y, w, h = box
    x, y, w, h = x - 0.2 * w, y - 0.55 * h, 1.4 * w, 1.75 * h  # face -> head (hair, a hat)
    if size:  # grab keeps the middle 16:9 of a frame that isn't
        sw, sh = size
        cw, ch = min(sw, sh * 16 / 9) / sw, min(sh, sw * 9 / 16) / sh
        x, y, w, h = (x - (1 - cw) / 2) / cw, (y - (1 - ch) / 2) / ch, w / cw, h / ch
    x0, y0 = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    x1, y1 = min(max(x + w, 0.0), 1.0), min(max(y + h, 0.0), 1.0)
    return {"x": round(x0, 4), "y": round(y0, 4), "width": round(x1 - x0, 4), "height": round(y1 - y0, 4)}


def local_time(parts: list[tuple], t: float) -> float | None:
    """Stream time t in a cut video with these parts [(src_start, src_end, offset)]; None if cut out."""
    return next((t - a + off for a, b, off in parts if a <= t < b), None)


def video_faces(data: dict, parts: list[tuple], people: int, per: int) -> list[tuple[float, list[dict]]]:
    """Frames of a cut video worth offering for its cover: for the `people` named people seen
    most in it, their `per` best faces. Each: (stream time, every named face in that frame
    as {"person", "face" (head box)}), best first."""
    groups = data["groups"]
    named = {k: g["name"] for k, g in groups.items() if g.get("name")}
    inside = [f for f in data["faces"] if f["group"] in named and local_time(parts, f["t"]) is not None]
    seen = Counter(named[f["group"]] for f in inside)
    picks: list[dict] = []
    for person, _ in seen.most_common(people):
        best = [f for a, b, _ in parts for f in best_faces(data, person, a, b, per)]
        picks += sorted(best, key=quality, reverse=True)[:per]
    at: dict[float, list[dict]] = {}
    for f in inside:
        at.setdefault(f["t"], []).append({"person": named[f["group"]],
                                          "face": head_box(f["box"], data.get("size"))})
    out, done = [], set()
    for f in sorted(picks, key=quality, reverse=True):
        if f["t"] not in done:
            done.add(f["t"])
            out.append((f["t"], at[f["t"]]))
    return out


def group_faces(data: dict, key: str) -> dict:
    """faces.json-shaped data with key's group as the person "#key", for best_faces()."""
    return {"groups": {key: {**data["groups"][key], "name": f"#{key}"}}, "faces": data["faces"]}


# ---------- contact sheets ----------

def draw_sheet(faces: list[dict], frames: Path, dst: Path, per_row: int = 8) -> None:
    """Square crops around each face, labeled with stream time and height, 8 to a row."""
    from PIL import Image, ImageDraw

    tiles = []
    for f in faces:
        im = Image.open(frames / f"{f['i']:05d}.jpg").convert("RGB")
        x, y, w, h = f["box"]
        cx, cy, s = (x + w / 2) * im.width, (y + h / 2) * im.height, max(w * im.width, h * im.height) * 1.6
        tile = im.crop((int(cx - s / 2), int(cy - s / 2), int(cx + s / 2), int(cy + s / 2))).resize((TILE, TILE))
        ImageDraw.Draw(tile).text((4, TILE - 14), f"{hms(f['t'])} {f['px']}px", fill=(255, 255, 0),
                                  stroke_width=1, stroke_fill=(0, 0, 0))
        tiles.append(tile)
    rows = -(-len(tiles) // per_row)
    sheet = Image.new("RGB", (TILE * per_row, TILE * rows))
    for k, tile in enumerate(tiles):
        sheet.paste(tile, (TILE * (k % per_row), TILE * (k // per_row)))
    sheet.save(dst, quality=85)


def sheet_faces(data: dict, key: str, n: int = 8) -> list[dict]:
    """A group's n best faces, then n spread over the stream (to catch a group that's two people)."""
    mine = [f for f in data["faces"] if f["group"] == key]
    spread = mine[::max(1, len(mine) // n)][:n]
    return best_faces(group_faces(data, key), f"#{key}", n=n) + spread


# ---------- the command ----------

def scan(sdir: Path, cfg: dict) -> dict:
    """Scan the stream's keyframes, group the faces, draw sheets, write faces.json, name the groups."""
    from PIL import Image

    from .media import source_file

    fc = cfg["faces"]
    src = source_file(sdir)
    models = ensure_models(cfg)
    try:
        old = jsonstate.load(sdir / "faces.json")["groups"]
    except FileNotFoundError:
        old = {}
    print(f"scanning {src.name} keyframes for faces")
    with tempfile.TemporaryDirectory(prefix="clipper-faces-") as tmp:
        frames = Path(tmp)
        times, rows, emb = scan_frames(src, frames, fc, models)
        groups, faces = build_groups(rows, emb, fc)
        carry_names(groups, old)
        size = Image.open(frames / "00001.jpg").size if (frames / "00001.jpg").exists() else None
        data = {"model": MODEL, "source": src.name, "frames": len(times), "size": size,
                "every_s": round(float(np.median(np.diff(times))), 2) if len(times) > 1 else 0,
                "groups": groups, "faces": faces}
        out = sdir / "faces"
        out.mkdir(exist_ok=True)
        for old_sheet in out.glob("F*.jpg"):
            old_sheet.unlink()
        for key in [k for k in groups][:fc["sheets"]] + [k for k, g in groups.items() if g.get("name")]:
            if not (out / f"{key}.jpg").exists():
                draw_sheet(sheet_faces(data, key), frames, out / f"{key}.jpg")
    for f in faces:
        del f["i"]
    jsonstate.write(sdir / "faces.json", data, indent=None)
    print(f"{len(rows)} faces -> {len(groups)} groups of {fc['min_faces']}+")
    return apply(sdir, cfg)


def set_names(sdir: Path, names: dict[str, str]) -> None:
    """Hand names ({"F03": "Jer"}; "" clears one) kept in faces.json for apply()."""
    path = sdir / "faces.json"
    data = jsonstate.load(path)
    for key, name in names.items():
        if key not in data["groups"]:
            raise ClipperError(f"no group {key} in {sdir.name} (F01-F{len(data['groups']):02d})")
        g = data["groups"][key]
        g.pop("name", None)
        g.pop("by", None)
        if name:
            g.update(name=name, by="hand")
    jsonstate.write(path, data, indent=None)


def add_known(cfg: dict, name: str, centroid: list[float]) -> int:
    """Keep one more sample of name's face; how many they have now."""
    known = load_known(cfg)
    if known["people"] and known["model"] != MODEL:
        raise ClipperError(f"{known_path(cfg)} holds {known['model']} faces; this is {MODEL}")
    known["model"] = MODEL
    refs = known["people"].setdefault(name, [])
    refs.append(centroid)
    del refs[:-cfg["faces"]["max_refs"]]
    path = known_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonstate.write(path, known, indent=None)
    return len(refs)


def enroll(sdir: Path, cfg: dict, keys: list[str]) -> None:
    """Add these groups' faces to the known faces under their names."""
    data = jsonstate.load(sdir / "faces.json")
    if data["model"] != MODEL:
        raise ClipperError(f"{sdir.name} was scanned with {data['model']}: clipper faces {sdir.name} --rescan")
    for key in keys:
        g = data["groups"][key]
        if not g.get("name"):
            raise ClipperError(f"{key}: name it first (--name {key}=Name)")
        n = add_known(cfg, g["name"], g["centroid"])
        print(f"enrolled {g['name']} from {sdir.name} {key} ({n} sample(s))")


def enroll_photos(cfg: dict) -> None:
    """Each [thumbnail] photos portrait's biggest face, under the file's name."""
    from .thumbs import photos

    found = photos(cfg["thumbnail"])
    if not found:
        raise ClipperError("no [thumbnail] photos to enroll")
    _init(str(ensure_models(cfg)), 0)
    for name, path in found.items():
        faces = _detect(str(path))
        if not faces:
            print(f"{path.name}: no face found")
            continue
        _, e = max(faces, key=lambda fe: fe[0]["px"])
        print(f"enrolled {name} from {path.name} ({add_known(cfg, name, [round(float(v), 4) for v in e])} sample(s))")


def show(sdir: Path, cfg: dict, groups: dict) -> None:
    """The known faces, then each group with a sheet or a name: who, how many, when, how big."""
    people = load_known(cfg)["people"]
    if people:
        print("known faces: " + ", ".join(f"{n} ({len(r)})" for n, r in sorted(people.items())))
    else:
        print("no known faces yet: name groups with --name F01=Name --enroll (look at faces/F01.jpg)")
    sheets = sdir / "faces"
    listed = [k for k, g in groups.items() if (sheets / f"{k}.jpg").exists() or g.get("name")]
    for key in listed:
        g = groups[key]
        if g.get("name"):
            score = f", {g['match']:.2f}" if g["by"] == "face" else ""
            who = f"{g['name']} ({g['by']}{score})"
        else:
            who = f"? (closest {g['closest']} {g['match']:.2f})" if g.get("closest") else "?"
        print(f"{key:<4} {who:<32} {g['n']:>5} faces  {hms(g['first_s'])}-{hms(g['last_s'])}  "
              f"{g['median_px']} px  cohesion {g['cohesion']:.2f}")
    if len(groups) > len(listed):
        print(f"... and {len(groups) - len(listed)} smaller groups")
    print(f"sheets: {sheets}")
