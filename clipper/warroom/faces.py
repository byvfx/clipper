"""The faces window (#54): a stream's face groups to name, a box drawn on a paused video,
enrolling and forgetting people."""
from __future__ import annotations

import base64
import os
import re
import tempfile
from pathlib import Path

from .. import actions, jsonstate
from ..config import for_stream
from ..errors import ClipperError
from .data import media_url
from .review import checked_ref

GROUP_KEY = re.compile(r"F\d{2,4}")

def stream_path(cfg: dict, stream) -> Path:
    stream = checked_ref(stream)
    sdir = cfg["paths"]["streams"] / stream
    if "/" in stream or not (sdir / "meta.json").exists():
        raise ClipperError(f"no stream {stream!r}")
    return sdir

def faces_get(cfg: dict, stream: str) -> dict:
    """The known faces, and the stream's groups with a sheet or a name, biggest first."""
    from .. import faces

    sdir = stream_path(cfg, stream)
    known = faces.load_known(for_stream(cfg, sdir))
    out = {"stream": sdir.name, "known": {n: len(r) for n, r in sorted(known["people"].items())},
           "scanned": False, "groups": [], "more": 0}
    data = jsonstate.read_or(sdir / "faces.json", None)
    if not data:
        return out
    try:  # one listing for every sheet's mtime, not a stat each over the share
        with os.scandir(sdir / "faces") as it:
            sheets = {e.name: int(e.stat().st_mtime) for e in it}
    except OSError:
        sheets = {}
    for key, g in data["groups"].items():
        sheet = sheets.get(f"{key}.jpg")
        if sheet or g.get("name"):
            out["groups"].append({"key": key, **{k: g.get(k) for k in (
                "n", "first_s", "last_s", "median_px", "cohesion", "name", "by", "closest", "match")},
                "sheet_url": media_url(sdir, sdir / "faces" / f"{key}.jpg") + f"?v={sheet}" if sheet else ""})
    out.update(scanned=True, more=len(data["groups"]) - len(out["groups"]), frames=data.get("frames"),
               faces=len(data["faces"]))
    return out

def faces_name(cfg: dict, body: dict) -> dict:
    """{"stream", "names": {"F03": "Jer", "F07": ""}, "enroll"}: hand names ("" clears one),
    then the groups named again; enroll keeps the named groups' faces for every show."""
    from .. import faces

    sdir = stream_path(cfg, body.get("stream"))
    scfg = for_stream(cfg, sdir)
    names = body.get("names")
    if not isinstance(names, dict) or not names:
        raise ClipperError("no names")
    clean = {}
    for key, name in names.items():
        if not isinstance(key, str) or not GROUP_KEY.fullmatch(key):
            raise ClipperError(f"not a face group: {str(key)[:20]!r}")
        clean[key] = faces.checked_name(name) if name else ""
    faces.set_names(sdir, clean)
    faces.apply(sdir, scfg)
    if body.get("enroll") and (keys := [k for k, v in clean.items() if v]):
        faces.enroll(sdir, scfg, keys)
        faces.apply(sdir, scfg)  # the new samples may name other groups
    return faces_get(cfg, sdir.name)

def checked_box(b) -> list[float]:
    try:
        box = [float(b[k]) for k in ("x", "y", "width", "height")]
    except (KeyError, TypeError, ValueError):
        raise ClipperError("no box") from None
    if not (0 <= box[0] < 1 and 0 <= box[1] < 1 and 0.01 <= box[2] <= 1 and 0.01 <= box[3] <= 1):
        raise ClipperError("the box is outside the frame")
    return box

def faces_pick(cfg: dict, body: dict) -> dict:
    """{"ref", "t", "box"}: the face in that box of the item's video at t seconds (box 0-1 of
    the frame) and who it looks like. Its embedding comes back for faces_enroll."""
    from .. import faces

    item = actions.resolve(cfg, checked_ref(body.get("ref", "")))
    sdir = item.stream_dir
    try:
        t = float(body.get("t"))
    except (TypeError, ValueError):
        t = -1.0
    if not 0 <= t < 86400:
        raise ClipperError("no time in the video")
    box = checked_box(body.get("box"))
    # the cut, full size, on the preview's timeline; but a compilation's preview joins its
    # segments' previews whole while the cut trims their overlaps: the preview watched, then
    video = sdir / item.state["file"].replace("\\", "/")
    preview = sdir / "review" / f"{item.key}.mp4"
    if item.state.get("kind") == "compilation" and preview.exists():
        video = preview
    with tempfile.TemporaryDirectory(prefix="clipper-face-") as tmp:
        found = faces.pick(faces.grab(video, t, Path(tmp) / "frame.jpg"), box, for_stream(cfg, sdir))
    found["tile"] = "data:image/jpeg;base64," + base64.b64encode(found["tile"]).decode()
    return {**found, "stream": sdir.name}

def faces_enroll(cfg: dict, body: dict) -> dict:
    """{"stream", "name", "embedding"}: keep a picked face as name's, then name the stream's groups again."""
    from .. import faces

    sdir = stream_path(cfg, body.get("stream"))
    n = faces.enroll_pick(sdir, for_stream(cfg, sdir), body.get("name"), body.get("embedding"))
    return {**faces_get(cfg, sdir.name), "samples": n}

def faces_forget(cfg: dict, body: dict) -> dict:
    """{"stream", "name"}: drop name from the known faces, then name the stream's groups again."""
    from .. import faces

    sdir = stream_path(cfg, body.get("stream"))
    scfg = for_stream(cfg, sdir)
    faces.forget(scfg, faces.checked_name(body.get("name")))
    if (sdir / "faces.json").exists():
        faces.apply(sdir, scfg)
    return faces_get(cfg, sdir.name)
