"""Fan-magazine covers ([thumbnail] style = "fanmag"): the pure parts. The browser render
itself is not run here (no browser on CI)."""
from pathlib import Path

import numpy as np
from PIL import Image

from clipper.thumbs import fanmag, pick
from clipper.thumbs import stage as thumbs_stage


def prob_map(w: int = 200, h: int = 200) -> np.ndarray:
    """A person: a head and body in one blob, with a hole (a mic) in the chest, plus a
    separate blob (a chair) in the corner."""
    a = np.zeros((h, w), np.uint8)
    a[30:90, 70:130] = 230        # head
    a[90:200, 40:160] = 230       # body, touching the bottom
    a[130:150, 90:110] = 0        # the mic in front of the chest
    a[5:25, 170:195] = 230        # the chair
    return a


def test_clean_matte_keeps_the_figure_at_the_face_and_fills_holes():
    m = fanmag.clean_matte(Image.fromarray(prob_map()), face=(70, 30, 130, 90))
    a = np.asarray(m) > 127
    assert a[140, 100]            # the hole is filled
    assert not a[15, 180]         # the chair is gone
    assert a[60, 100] and a[190, 100]


def test_clean_matte_without_a_face_keeps_the_largest():
    a = np.asarray(fanmag.clean_matte(Image.fromarray(prob_map()))) > 127
    assert a[60, 100] and not a[15, 180]


def test_clean_matte_refuses_what_does_not_hold():
    p = prob_map()
    # The face is on the chair, or nowhere: the rectangle instead.
    assert fanmag.clean_matte(Image.fromarray(p), face=(0, 150, 30, 190)) is None
    assert fanmag.clean_matte(Image.fromarray(np.zeros((50, 50), np.uint8))) is None
    # The whole crop reads as a person (a webcam box): too much to be a head and shoulders.
    assert fanmag.clean_matte(Image.fromarray(np.full((50, 50), 230, np.uint8))) is None


def test_handcut_bleeds_off_the_bottom_only_when_asked():
    img = Image.new("RGBA", (100, 120), (0, 0, 0, 0))
    img.paste((200, 50, 50, 255), (20, 10, 80, 120))   # touches the bottom row
    cut = fanmag.handcut(img, margin=10, seed=1)
    bled = fanmag.handcut(img, margin=10, seed=1, bleed=True)
    assert bled.height < cut.height
    assert bled.getpixel((bled.width // 2, bled.height - 1))[3] == 255   # photo right to the edge
    floating = Image.new("RGBA", (100, 120), (0, 0, 0, 0))
    floating.paste((200, 50, 50, 255), (20, 10, 80, 90))
    assert fanmag.handcut(floating, margin=10, seed=1, bleed=True).size == \
        fanmag.handcut(floating, margin=10, seed=1).size


def test_head_box_maps_the_face_into_the_crop():
    crop, (x0, y0, x1, y1) = fanmag.head_box(Image.new("RGB", (1280, 720)),
                                             {"x": 0.4, "y": 0.2, "width": 0.1, "height": 0.2}, 300)
    assert crop.height == 300
    assert 0 <= x0 < x1 <= crop.width and 0 <= y0 < y1 <= crop.height
    assert abs((x0 + x1) / 2 - crop.width / 2) <= 2      # centred on the face


def test_cover_title_whole_or_nothing():
    assert fanmag.cover_title("Kino Casino | Blade in Vegas") == "BLADE IN VEGAS"
    assert fanmag.cover_title("A title far too long to print over the photo without cutting it") == ""
    assert fanmag.cover_title("") == ""


def test_masthead_blanks_the_mascot_letter():
    html = fanmag.masthead("Sloth Beat", "file:///sloth.png", 4)
    assert '<span class="h">H</span>' in html and 'id="mascot"' in html
    plain = fanmag.masthead("Sloth Beat", "file:///sloth.png", 5)    # the space: no mascot
    assert 'class="h"' not in plain and "mascot" not in plain


def test_build_page(tmp_path: Path):
    png = tmp_path / "x.png"
    Image.new("RGBA", (10, 10)).save(png)
    e = {"id": "s03", "headline": ["ANDY", "SPILLS", "IT ALL"], "attribution": "Blade's night",
         "subhead": "What happens in Vegas!", "main_person": "Andy", "band_kicker": "",
         "band_headline": "", "title": "Kino Casino | Blade <in> Vegas"}
    ctx = {"band": {"band_kicker": "Inside:", "band_headline": "JIM'S PANTRY"},
           "side": [{"text": "Is Jer a top marketer?", "topic": "The Quartering", "name": "Jer"},
                    {"text": "Jim's coffee stash", "topic": "Jim Bakker", "name": "Jim"},
                    {"text": "A third story", "topic": "", "name": ""}]}
    tc = {"_masthead": "SLOTH BEAT", "_mascot_uri": "", "mascot_letter": 4, "masthead_price": "$1.39"}
    page = fanmag.build_page(e, ctx, tc, "OCT 8, 2026", {"main": png, "inset": png, "side": [png, png]},
                             tmp_path / "out").read_text(encoding="utf-8")
    assert "ANDY<br>SPILLS<br>IT ALL" in page
    assert "BLADE &lt;IN&gt; VEGAS" in page                  # escaped, prefix dropped
    assert "JIM&#x27;S PANTRY" in page or "JIM'S PANTRY" in page
    assert page.count('class="cl "') == 3                     # the three side coverlines
    assert 'class="stk gap"' in page and 'class="stk inset"' in page
    assert "document.fonts.ready.then(fit)" in page
    # Every palette's column is light enough for ink and never the script's yellow.
    assert all(fanmag.luminance(b) > 0.5 and b != "#ffe066" for _, b, _, _ in fanmag.PALETTES)


def test_fanmag_shares_the_enquirer_pick():
    assert "fanmag" in pick.COVER_STYLES
    assert thumbs_stage.prompt_name("fanmag") == "thumbnail_fanmag"
    assert thumbs_stage.prompt_name("classic") == "thumbnail"
    _, schema = pick.request({"kind": "clip", "topic": "", "figures": [], "parts": [(0, 30, 0)]},
                             [], {"title": "t", "hook": "", "about": ""}, {}, "fanmag", "",
                             image=lambda p: "")
    assert schema is pick.ENQ_SCHEMA


def test_find_browser_honours_the_setting(tmp_path: Path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    assert fanmag.find_browser({"browser": str(exe)}) == str(exe)
    assert fanmag.find_browser({"browser": str(tmp_path / "missing.exe")}) == ""
