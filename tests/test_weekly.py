"""The weekly issue: prompts through Prompts, the look as data (style.toml), and the article
page reporting how far its text overflows (what the writer pass sends back to be cut)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from clipper.errors import ClipperError
from clipper.weekly import draft, style
from clipper.weekly.render import Fonts, Renderer

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@pytest.fixture
def cfg(defaults, tmp_path):
    cfg = copy.deepcopy(defaults)
    cfg["paths"]["weekly"] = tmp_path
    cfg["scoring"]["house_style"] = ["Never say the word on the banned list.", "No emoji."]
    return cfg


def versions(name: str) -> list[str]:
    return sorted(p.stem.removeprefix(name + "_") for p in PROMPTS.glob(f"{name}_v*.md"))


def test_weekly_prompt_is_the_file_and_house_style(cfg):
    """The draft prompt, as weekly.py read it by path before it went through Prompts."""
    for v in versions("weekly"):
        cfg["weekly"]["prompt_version"] = v
        old = (PROMPTS / f"weekly_{v}.md").read_text(encoding="utf-8").rstrip()
        old += "\n\nHouse style for everything on the page:\n- Never say the word on the banned list.\n- No emoji.\n"
        assert draft.draft_system(cfg) == old, v


def test_article_prompt_is_the_file_filled_and_house_style(cfg):
    example = (PROMPTS / cfg["weekly"]["article_example"]).read_text(encoding="utf-8")
    for v in versions("weekly_article"):
        cfg["weekly"]["article_prompt_version"] = v
        for budget in draft.BUDGET.values():
            old = (PROMPTS / f"weekly_article_{v}.md").read_text(encoding="utf-8").format(
                example=example, col1_words=budget[0], col2_words=budget[1], deck_chars=budget[2]).rstrip()
            old += "\n\nHouse style:\n- Never say the word on the banned list.\n- No emoji.\n"
            assert draft.article_system(cfg, budget) == old, v


def test_no_house_style_no_header(cfg):
    cfg["scoring"]["house_style"] = []
    assert "House style" not in draft.draft_system(cfg)
    assert "House style" not in draft.article_system(cfg, draft.BUDGET["article"])


def test_style_defaults_without_a_file(cfg):
    s = style.load(cfg)
    assert s is style.DEFAULT
    assert (s.canvas.width, s.canvas.height, s.canvas.margin) == (1080, 1440, 56)
    assert s.palette.red == (226, 30, 38) and s.palette.initials[0] == (120, 60, 160)
    assert s.cover.teasers[1]["bg"] == (255, 255, 255) and s.cover.teasers[1]["framed"]
    assert s.copy.lolcow == "LOLCOW OF THE WEEK"


def test_style_toml_overrides_what_it_names(cfg):
    (cfg["paths"]["weekly"] / "style.toml").write_text(
        '[palette]\nred = [200, 20, 30]\n[cover]\nsubject_height = 900\n'
        'teasers = [{bg = "navy", kicker = "red", headline = [1, 2, 3]}]\n'
        '[copy]\nlolcow = "COW OF THE WEEK"\n', encoding="utf-8")
    s = style.load(cfg)
    assert s.palette.red == (200, 20, 30) and s.palette.ink == (18, 18, 18)
    assert s.cover.subject_height == 900 and s.cover.main_top == 336
    assert s.cover.teasers == [{"bg": (14, 26, 64), "kicker": (200, 20, 30), "headline": (1, 2, 3)}]
    assert s.copy.lolcow == "COW OF THE WEEK" and s.copy.poll == "READERS' POLL"
    assert style.DEFAULTS["palette"]["red"] == [226, 30, 38]  # the defaults are untouched


def test_style_palette_names_follow_through(cfg):
    (cfg["paths"]["weekly"] / "style.toml").write_text(
        '[palette]\nred = "navy"\nnavy = [1, 2, 3]\ninitials = ["red", [4, 5, 6]]\n'
        '[cover]\nmasthead_chrome = [[0.0, "red"], [1.0, [7, 8, 9]]]\n', encoding="utf-8")
    s = style.load(cfg)
    assert s.palette.red == (1, 2, 3) and s.palette.initials == [(1, 2, 3), (4, 5, 6)]
    assert s.cover.masthead_chrome == [(0.0, (1, 2, 3)), (1.0, (7, 8, 9))]
    assert s.cover.teasers[1]["kicker"] == (1, 2, 3)


@pytest.mark.parametrize("toml", ['[cover]\nsubjct_height = 900\n', '[colours]\nred = [1, 2, 3]\n',
                                  '[cover]\ntint = "pink"\n', '[cover\n', '[cover]\ntint = 123\n',
                                  '[cover]\ntint = [1, 2]\n', '[palette]\nred = "navy"\nnavy = "red"\n',
                                  '[cover]\nteasers = [{bg = "red"}]\n', '[cover]\nmasthead_chrome = [1]\n'])
def test_style_toml_refuses_what_it_does_not_know(cfg, toml):
    (cfg["paths"]["weekly"] / "style.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(ClipperError):
        style.load(cfg)


@pytest.fixture
def renderer(cfg):
    try:
        Fonts(cfg["weekly"])
    except ClipperError:
        pytest.skip("the weekly fonts aren't installed here")
    week = {"label": "OCT 18-24, 2026", "streams": {}}
    return lambda **kw: Renderer(cfg, cfg["paths"]["weekly"], week, {}, {"issue": 2}, **kw)


ARTICLE = {"section": "Feature", "kicker": "Exclusive", "topic": "Topic", "person": "Nobody Here",
           "watch": "Watch: Mon", "headline": ["A HEADLINE", "IN TWO LINES"], "highlight": "TWO",
           "deck": "A short deck.", "col1": "Short.", "quote": "A quote.", "quote_by": "Someone", "col2": "Short."}


def test_article_page_reports_its_overflow(renderer):
    r = renderer()
    page, over = r.p_article(4, ARTICLE)
    assert page.size == (1080, 1440) and over == 0 and not r.warnings
    long = {**ARTICLE, "col1": "words that run on " * 400}
    _, over = r.p_article(4, long)
    assert over > 0 and r.warnings == [f"page 04: text runs {over}px past the footer"]


def test_square_canvas_is_passed_not_global(renderer):
    tall, square = renderer(), renderer(square=True)
    assert (tall.W, tall.H, tall.CW) == (1080, 1440, 968)
    assert (square.W, square.H, square.CW) == (1440, 1440, 1328)
    assert renderer().W == 1080  # nothing left behind
    assert square.p_article(4, ARTICLE)[0].size == (1440, 1440)


def test_found_faces_come_from_faces_json_and_go_first(cfg, tmp_path, monkeypatch):
    from clipper import faces as face_mod
    from clipper import jsonstate
    from clipper.weekly import gather
    from clipper.weekly.faces import Faces

    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    (sdir / "source.mkv").write_bytes(b"")
    box = [0.4, 0.3, 0.1, 0.15]
    jsonstate.write(sdir / "faces.json", {
        "groups": {"F01": {"name": "Jer", "floor": 0.0}, "F02": {"floor": 0.0}},  # F02 nobody named
        "faces": [{"t": 100.01, "group": "F01", "sim": 0.9, "sharp": 80, "frontal": 1.0, "px": 150, "box": box},
                  {"t": 200.0, "group": "F02", "sim": 0.9, "sharp": 99, "frontal": 1.0, "px": 200, "box": box}]})
    grabbed = []
    monkeypatch.setattr(face_mod, "grab", lambda src, t, dst: grabbed.append(t) or dst.write_bytes(b"jpg") or dst)
    here = tmp_path / "issue"
    found = gather.found_faces(sdir, here, 3)
    assert [(f["person"], f["found"]) for f in found] == [("Jer", True)] and grabbed == [100.01]
    assert Path(found[0]["frame"]) == here / "faces" / "kick-1_100.01.jpg"
    assert found[0]["key"] == "faces-F01@100.01" and found[0]["face"] == face_mod.head_box(box)
    (sdir / "source.mkv").unlink()
    assert gather.found_faces(sdir, here, 3) == found  # kept from the first time, source or not
    assert grabbed == [100.01]
    assert gather.found_faces(sdir, here, 0) == []

    guess = {"person": "Jer", "key": "s01", "frame": "x.jpg", "face": {}, "size": 0.5, "photo": ""}
    week = {"streams": {"kick-1": {"faces": [guess] + found}}}
    assert Faces(cfg, week).best("Jer")["found"]  # named by face beats a bigger thumbnail guess


def test_a_new_worker_gets_the_faces_extra():
    from clipper import setup
    assert "faces" in setup.extras_for(["worker"], "modal") and "faces" not in setup.extras_for(["bot"], "modal")
    assert setup.EXTRA_MODULE["faces"] == "cv2"


def test_photos_by_label_for_people_only_talked_about(cfg, tmp_path):
    from PIL import Image

    from clipper import jsonstate
    from clipper.weekly.faces import Faces

    jer = tmp_path / "photos" / "Jer"
    jer.mkdir(parents=True)
    for pid in ("a", "b"):
        Image.new("RGB", (10, 10)).save(jer / f"{pid}.jpg")
    jsonstate.write(jer / "cast.json", {"photos": {
        "a": {"file": "a.jpg", "status": "kept", "label": "neutral", "px": 200},
        "b": {"file": "b.jpg", "status": "kept", "label": "angry", "px": 100}}})
    cfg["thumbnail"]["photos"] = str(tmp_path / "photos")
    week = {"streams": {}}
    assert Faces(cfg, week).photos["jer"] == jer / "a.jpg"                       # neutral by default
    assert Faces(cfg, week, labels={"Jer": "angry"}).photos["jer"] == jer / "b.jpg"
    assert Faces(cfg, week, labels=["junk"]).photos["jer"] == jer / "a.jpg"      # a bad field is ignored
    stats = {"shows": 0, "preshows": 0, "hours": 0, "hours_with_preshows": 0, "segments": 0, "shorts": 0,
             "chat_messages": 0, "chatters": 0, "laughs": 0, "peak_per_min": 0, "peak_stream": "",
             "top_emotes": [], "fucks": 0, "fuck_every_s": 0, "screen_minutes": {}, "said": {}}
    user = draft.draft_user({"label": "Oct 25-31", "streams": {}}, stats, "", 4, {"Jer": jer / "a.jpg", "Don": Path("Don.jpg")},
                            {"Jer": ["angry", "neutral"], "Don": []})
    assert user.endswith("Portrait photos on file (also usable): Don, Jer (angry, neutral)")
