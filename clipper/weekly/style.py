"""The issue's look as data: canvas, palette, the cover's geometry and the fixed copy.

Defaults are here; <paths.weekly>/style.toml overrides any of them with the same tables and
keys, e.g.

  [palette]
  red = [200, 20, 30]
  [cover]
  subject_height = 900
  [copy]
  lolcow = "COW OF THE WEEK"

so a cover tweak is a TOML edit. A colour is [r, g, b] or the name of a [palette] colour.
Lists replace the default whole; an unknown table or key is refused.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from types import SimpleNamespace

from ..errors import ClipperError

FILE = "style.toml"

DEFAULTS: dict = {
    # Pages are width x height; the square (YouTube Posts) canvas is height x height, the same
    # layout with wider columns, scaled to 1080x1080.
    "canvas": {"width": 1080, "height": 1440, "margin": 56},
    "palette": {
        "paper": [246, 243, 237], "ink": [18, 18, 18], "muted": [112, 108, 100], "card": [236, 232, 224],
        "rule": [214, 208, 198], "red": [226, 30, 38], "yellow": [255, 226, 0], "white": [255, 255, 255],
        "grey": [170, 166, 158], "navy": [14, 26, 64],
        "initials": [[120, 60, 160], [30, 90, 160], [14, 120, 60], [200, 120, 20], [160, 40, 40], [40, 110, 110]],
    },
    # 01 cover: y values under the top strip count from main_top; x values are for the 1080
    # canvas (the square one scales the horizontal ones).
    "cover": {
        "margin": 14,             # white border round the page
        "main_top": 336,          # where the photo panel starts, under the teaser strip
        "tint": [20, 40, 120],    # blended over the blurred room behind the subject
        "background_tint": [16, 30, 96],  # ...over an issue.json cover.background picture
        "shade": [6, 10, 30],     # the gradient to the bottom of the panel
        "strip_height": 312,      # the three teasers
        "teaser_widths": [340, 370],  # the third takes the rest
        "teasers": [{"bg": [30, 70, 190], "kicker": "yellow", "headline": "white", "framed": False},
                    {"bg": "white", "kicker": "red", "headline": "ink", "framed": True},
                    {"bg": [14, 120, 60], "kicker": "yellow", "headline": "white", "framed": False}],
        "teaser_kicker_height": 50,
        "teaser_photo_width": 0.44,  # of the panel
        "masthead_y": 58,         # the chrome word, below main_top
        "masthead_chrome": [[0.0, [255, 255, 255]], [0.42, [226, 232, 240]], [0.50, [120, 132, 150]],
                            [0.56, [205, 212, 222]], [1.0, [250, 252, 255]]],  # top-to-bottom gradient stops
        "kicker_y": 334,          # the red kicker box, below main_top
        "kicker_width": 520,      # at least
        "kicker_height": 118,
        "headline_width": 620,    # kept on the square canvas: the subject needs the room
        "headline_size": 230,     # at most
        "subject_height": 940,    # the cut-out (issue.json cover.subject_height wins)
        "inset_size": 205,        # the circle inset
        "inset_x": 548,
        "burst_x": 112,
        "burst_y": 240,           # below main_top
        "burst_radius": 86,
        "bullet_row": 58,
        "barcode_width": 196,
        "barcode_height": 212,
    },
    "copy": {
        "weekly": "WEEKLY",       # after the masthead in the page header
        "swipe": "SWIPE",
        "follow": "FOLLOW",       # the last page's
        "week_of": "WEEK OF",     # on the cover's barcode block
        "barcode": "7  25274 405",  # then the issue number and "  1"
        "contents_section": "CONTENTS",
        "contents_title": "IN THIS ISSUE",
        "brief": "NEWS IN BRIEF",
        "quote": "QUOTE OF THE WEEK",
        "runners_up": "RUNNERS-UP",
        "numbers_section": "BY THE NUMBERS",
        "numbers_title": "THE WEEK IN NUMBERS",
        "screen_time": "SCREEN TIME",
        "screen_time_sub": "HOURS ON AIR THIS WEEK",
        "lolcow": "LOLCOW OF THE WEEK",
        "market_section": "MARKETPLACE",
        "market_title": "THE MARKETPLACE",
        "market_badge": "AD  ·  PARODY",
        "next_week": "NEXT WEEK",
        "poll": "READERS' POLL",
    },
}


def merge(base: dict, over: dict, where: str = "") -> dict:
    """base with over's values; tables merge, anything else replaces. Unknown keys are refused."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        name = f"{where}.{k}" if where else k
        if k not in base:
            raise ClipperError(f"weekly {FILE}: unknown setting [{name}]")
        out[k] = merge(base[k], v, name) if isinstance(base[k], dict) and isinstance(v, dict) else v
    return out


class Style:
    """DEFAULTS merged with an override, as attributes: style.canvas.width, style.palette.red
    (a tuple), style.cover.teasers, style.copy.lolcow."""

    def __init__(self, data: dict):
        self.data = data
        pal = {k: self.colour(k) for k in data["palette"] if k != "initials"}
        self.palette = SimpleNamespace(**pal, initials=[self.colour(c) for c in data["palette"]["initials"]])
        self.canvas = SimpleNamespace(**data["canvas"])
        self.copy = SimpleNamespace(**data["copy"])
        cover = dict(data["cover"])
        for k in ("tint", "background_tint", "shade"):
            cover[k] = self.colour(cover[k])
        cover["teasers"] = [{**t, **{c: self.colour(t[c]) for c in ("bg", "kicker", "headline")}}
                            for t in cover["teasers"]]
        cover["masthead_chrome"] = [(t, self.colour(c)) for t, c in cover["masthead_chrome"]]
        self.cover = SimpleNamespace(**cover)

    def colour(self, v, seen: tuple = ()) -> tuple:
        """[r, g, b], or a [palette] name, followed through names that name names."""
        if isinstance(v, str):
            if v not in self.data["palette"] or v == "initials":
                raise ClipperError(f"weekly {FILE}: no [palette] colour {v!r}")
            if v in seen:
                raise ClipperError(f"weekly {FILE}: [palette] colours name each other: {' -> '.join((*seen, v))}")
            return self.colour(self.data["palette"][v], (*seen, v))
        if (not isinstance(v, (list, tuple)) or len(v) != 3
                or not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in v)):
            raise ClipperError(f"weekly {FILE}: {v!r} is not a colour: [r, g, b] (0-255) or a [palette] name")
        return tuple(v)


DEFAULT = Style(DEFAULTS)


def load(cfg: dict) -> Style:
    """The defaults, with <paths.weekly>/style.toml over them when it exists."""
    path = Path(cfg["paths"]["weekly"]) / FILE
    if not path.exists():
        return DEFAULT
    try:
        over = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ClipperError(f"{path}: {e}") from None
    try:
        return Style(merge(DEFAULTS, over))
    except (TypeError, ValueError, KeyError, AttributeError) as e:  # a value of the wrong shape
        raise ClipperError(f"{path}: a value has the wrong shape ({type(e).__name__}: {e})") from None


def masthead(wc: dict) -> tuple[str, str] | None:
    """The cover's two words ([weekly] masthead: the red one, then the chrome one), stripped, or
    None when the setting isn't two non-blank strings: the weekly then refuses to draft and
    `clipper doctor` warns (Copilot on #178: a blank word must not reach the paid draft)."""
    words = wc.get("masthead") or []
    if (not isinstance(words, list | tuple) or len(words) != 2
            or not all(isinstance(w, str) and w.strip() for w in words)):
        return None
    return words[0].strip(), words[1].strip()
