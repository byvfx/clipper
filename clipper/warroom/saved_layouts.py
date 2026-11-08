"""Saved panel layouts (which panel sits in which zone), per browser profile."""
from __future__ import annotations

import re
from pathlib import Path

from .. import jsonstate
from ..errors import ClipperError

LAYOUT_ZONES = ("top", "left", "middle", "right", "bottom", "hidden")

def layouts(cfg: dict) -> dict:
    return jsonstate.read(Path(cfg["paths"]["warroom_layouts"]), {"layouts": {}})

def save_layout(cfg: dict, body: dict) -> dict:
    """{name, layout} saves (or replaces) a named layout; {name, delete: true} removes it."""
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 40:
        raise ClipperError("a layout needs a name of 1-40 characters")
    lay = body.get("layout") or {}
    if not body.get("delete"):
        zones, widths = lay.get("zones"), lay.get("widths") or {}
        if not isinstance(zones, dict) or set(zones) - set(LAYOUT_ZONES) or not all(
                isinstance(v, list) and all(isinstance(x, str) and re.fullmatch(r"[a-z_]{1,30}", x) for x in v)
                for v in zones.values()):
            raise ClipperError("not a layout: zones must be lists of panel names")
        if set(widths) - {"left", "right"} or not all(isinstance(w, int) and 200 <= w <= 900 for w in widths.values()):
            raise ClipperError("not a layout: widths are left/right in px, 200-900")
        lay = {"zones": zones, "widths": widths}
    path = Path(cfg["paths"]["warroom_layouts"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with jsonstate.update(path, {"layouts": {}}) as d:
        if body.get("delete"):
            d["layouts"].pop(name, None)
        else:
            d["layouts"][name] = lay
        out = {"layouts": dict(d["layouts"])}
    return out
