from pathlib import Path

import pytest
from conftest import ROOT
from PIL import Image

from clipper import jsonstate
from clipper.errors import ClipperError
from clipper.prompts import Prompts
from clipper.thumbs import enquirer, pick
from clipper.thumbs import stage as thumbs_stage


class Words:
    """A Bleeper stand-in: these words are never printed."""
    def __init__(self, *bad):
        self.bad = {w.lower() for w in bad}

    def hit(self, w: str) -> bool:
        return w.lower() in self.bad


class Fake:
    """A model that gives these answers in turn (None: a refusal) and keeps each request."""
    def __init__(self, *answers):
        self.answers, self.contents = list(answers), []

    def json_call(self, model, system, content, schema, effort, fallback=False):
        self.contents.append(content)
        return self.answers.pop(0), {"refusal": "refused"}


X = {"kind": "clip", "id": "t05", "topic": "", "figures": ["Joe"], "parts": [(10.0, 70.0, 0.0)]}
INFO = {"title": "Joe won't zip it", "hook": "", "about": ""}


def retry(defaults) -> Prompts:
    p = Prompts({**defaults, "paths": {**defaults["paths"], "prompts": ROOT / "prompts"}})
    p.version = defaults["thumbnail"]["retry_prompt_version"]
    return p


def frames(tmp_path) -> list[dict]:
    out = []
    for i, t in enumerate((1.0, 2.5)):
        p = tmp_path / f"{i:02d}.jpg"
        Image.new("RGB", (64, 36), (40 * i, 60, 90)).save(p)
        out.append({"t": t, "path": p})
    return out


def test_retry_and_feedback_wording_is_what_it_was_inline(defaults):
    r, note = retry(defaults), "too dark, {face} cut off"
    assert pick.feedback_text(r, note) == ("\nThe reviewer rejected the previous thumbnail for this video: "
                                           f"{note}\nMake a new choice that fixes that.")
    assert pick.feedback_text(r, "") == ""
    assert r.get("thumbnail_retry", text="SO BAD").strip() == (
        "Your text 'SO BAD' can't be used (empty, or it has a word the channel never prints). "
        "Write different text.")
    assert r.get("thumbnail_enquirer_retry").strip() == (
        "That text can't be used (an empty headline, or a word the channel never prints). Write it again.")


def test_request_offers_photos_to_enquirer_covers_only():
    fr = [{"t": 1.0, "path": None}, {"t": 62.5, "path": None}]
    tc = {"_photos": {"Hasan Piker": Path("Hasan Piker.jpg")}}
    content, schema = pick.request(X, fr, INFO, tc, "classic", "", image=lambda p: "IMG")
    assert schema is pick.PICK_SCHEMA
    assert [c["text"] for c in content if c["type"] == "text"][:2] == ["Frame 1 (0:01.0)", "Frame 2 (1:02.5)"]
    assert content[-1]["text"] == ("Video: clip, 1.0 min\nTitle: Joe won't zip it\nPeople: Joe\n"
                                   "Pick one of frames 1-2.")
    content, schema = pick.request(X, fr, INFO, tc, "enquirer", "\nNOTE", image=lambda p: "IMG")
    assert content[-2]["text"].endswith("Frames are numbered 1-2.\nNOTE")
    assert content[-1]["text"] == "Photos on file: Hasan Piker."
    assert schema["properties"]["photo"]["enum"] == ["", "Hasan Piker"]
    assert "photo" in schema["required"] and "photo" not in pick.ENQ_SCHEMA["required"]
    assert "photo_label" not in schema["properties"]                   # no labelled photos, no label
    tc["_photos"]["Jer"] = Path("Jer/abc.jpg")
    tc["_photo_labels"] = {"Hasan Piker": [], "Jer": ["laughing", "angry"]}
    content, schema = pick.request(X, fr, INFO, tc, "enquirer", "", image=lambda p: "IMG")
    assert content[-1]["text"] == "Photos on file: Hasan Piker, Jer (laughing, angry)."
    assert schema["properties"]["teaser_photo_label"]["enum"] == ["", "angry", "laughing"]
    assert {"photo_label", "teaser_photo_label"} <= set(schema["required"])


def test_enquirer_keeps_a_photo_label_only_if_that_person_has_it(defaults, tmp_path):
    tc = {**defaults["thumbnail"], "_photos": {"Jer": Path("Jer/a.jpg"), "Don": Path("Don/b.jpg")},
          "_photo_labels": {"Jer": ["angry"], "Don": []}}
    answer = {**pick.fallback_enquirer(X, INFO, "w"), "photo": "jer", "photo_label": "angry",
              "teaser_photo": "Don", "teaser_photo_label": "angry"}
    out = pick.pick(X, frames(tmp_path), INFO, tc, Fake(answer), "SYSTEM", Words(), retry(defaults),
                    style="enquirer")
    assert (out["photo"], out["photo_label"], out["teaser_photo"]) == ("Jer", "angry", "Don")
    assert "teaser_photo_label" not in out                              # Don has no angry photo


def test_classic_pick_asks_again_then_clamps(defaults, tmp_path):
    tc = {**defaults["thumbnail"]}
    first = {"frame": 1, "text": "badword", "highlight": "", "position": "top",
             "crop": {"x": 0, "y": 0, "width": 1}, "why": "w"}
    second = {**first, "frame": 9, "text": "Joe #won't zip", "crop": {"x": 0.9, "y": 0, "width": 0.1}}
    fake = Fake(first, second)
    out = pick.pick(X, frames(tmp_path), INFO, tc, fake, "SYSTEM", Words("badword"), retry(defaults))
    assert len(fake.contents) == 2 and len(fake.contents[1]) == len(fake.contents[0]) + 1
    assert fake.contents[1][-1]["text"].startswith("Your text 'badword' can't be used")
    assert out["text"] == "Joe won't zip" and out["frame"] == 2 and out["t"] == 2.5
    assert out["crop"] == {"x": 0.6, "y": 0, "width": 0.4}  # min_crop 0.4, kept inside


def test_enquirer_pick_falls_back_when_the_model_refuses(defaults, tmp_path):
    tc = {**defaults["thumbnail"], "_photos": {}}
    out = pick.pick(X, frames(tmp_path), INFO, tc, Fake(None), "SYSTEM", Words(), retry(defaults),
                    style="enquirer", note="too dark")
    assert out["headline"] == ["JOE!"] and out["text"] == "JOE!" and out["attribution"] == "SOURCES SAY"
    assert out["why"] == "fallback (refused)" and out["t"] == 1.0 and "photo" not in out


def test_enquirer_uses_the_found_face_of_main_person(defaults, tmp_path):
    tc = {**defaults["thumbnail"], "_photos": {}}
    fr = frames(tmp_path)
    jer = {"x": 0.5, "y": 0.1, "width": 0.2, "height": 0.4}
    fr[1]["faces"] = [{"person": "Andy", "face": {"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.2}},
                      {"person": "Jer", "face": jer}]
    def answer(who):
        return {**pick.fallback_enquirer(X, INFO, "w"), "main_person": who, "frame": 2,
                "headline": ["JER'S", "BAD DAY!"]}
    fake = Fake(answer("jer"))
    out = pick.pick(X, fr, INFO, tc, fake, "SYSTEM", Words(), retry(defaults), style="enquirer")
    labels = [c["text"] for c in fake.contents[0] if c["type"] == "text"][:2]
    assert labels == ["Frame 1 (0:01.0)", "Frame 2 (0:02.5), faces found: Andy, Jer"]
    assert out["face"] == jer and out["face_found"]
    fake = Fake(answer("Mel"))  # not found in that frame: the model's box stays
    out = pick.pick(X, fr, INFO, tc, fake, "SYSTEM", Words(), retry(defaults), style="enquirer")
    assert out["face"] == pick.fallback_enquirer(X, INFO, "w")["face"] and "face_found" not in out


# ---------- the cover layout ----------

def test_no_layout_file_is_the_default(defaults, tmp_path):
    cfg = {**defaults, "paths": {**defaults["paths"], "layouts": tmp_path}, "show": {"key": "kino"}}
    assert enquirer.layout(cfg) is enquirer.LAYOUT


def test_a_show_file_changes_only_what_it_names(defaults, tmp_path):
    cfg = {**defaults, "paths": {**defaults["paths"], "layouts": tmp_path}, "show": {"key": "kino"}}
    (tmp_path / "kino").mkdir()
    (tmp_path / "kino" / "enquirer.toml").write_text(
        '[colors]\ngreen = "#0b3d91"\n[cover.inset]\nsize = [200, 180]\n'
        '[[cover.teasers.boxes]]\nheight = 300\nfill = "pink"\nink = "black"\n', encoding="utf-8")
    L, D = enquirer.layout(cfg), enquirer.LAYOUT
    assert L["colors"]["green"] == "#0b3d91" and L["colors"]["red"] == D["colors"]["red"]
    assert L["cover"]["inset"]["size"] == [200, 180] and L["cover"]["inset"]["pad"] == D["cover"]["inset"]["pad"]
    assert len(L["cover"]["teasers"]["boxes"]) == 1 and L["vertical"] == D["vertical"]
    assert D["colors"]["green"] == "#10543e" and len(D["cover"]["teasers"]["boxes"]) == 3  # untouched
    assert enquirer.layout({**cfg, "show": {"key": "c404"}}) is D


def test_layout_typos_are_errors(defaults, tmp_path):
    with pytest.raises(ClipperError, match="unknown key cover.insett"):
        enquirer.merge_layout(enquirer.LAYOUT, {"cover": {"insett": {"pad": 3}}})
    with pytest.raises(ClipperError, match="cover.inset is a table"):
        enquirer.merge_layout(enquirer.LAYOUT, {"cover": {"inset": 5}})
    cfg = {**defaults, "paths": {**defaults["paths"], "layouts": tmp_path}}
    (tmp_path / "default").mkdir()
    (tmp_path / "default" / "enquirer.toml").write_text("[colors\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="enquirer.toml"):
        enquirer.layout(cfg)


def test_default_layout_is_the_old_geometry():
    c, v = enquirer.LAYOUT["cover"], enquirer.LAYOUT["vertical"]
    # 16:9: inset 176x158, 14 px in from the photo's bottom-right corner (the cover's bottom)
    assert enquirer.inset_box(c["inset"], *c["photo"]["box"]) == (314 + 470 - 176 - 14, 720 - 158 - 14, 176, 158)
    # 9:16: 380 px above the bottom margin for the mobile title, but at least 40 px into the photo
    for py in (900, 1500):
        assert enquirer.inset_box(v["inset"], 16, py, 1048, 1920 - 16 - py) == (
            1080 - 16 - 330 - 22, max(py + 40, 1920 - 16 - 296 - 380), 330, 296)
    ink = enquirer.palette(enquirer.LAYOUT)
    assert ink("green") == (16, 84, 62) and ink("red") == (214, 18, 32) and ink("#ffffff") == (255, 255, 255)


def test_a_cover_goes_stale_on_the_cut_not_the_file(tmp_path):
    video = tmp_path / "c.mp4"
    video.write_bytes(b"x")
    jsonstate.write(video.with_suffix(".json"), {"in_s": 10.0, "out_s": 40.0})
    key = thumbs_stage.cut_key(video)
    assert key == "10.0-40.0"
    seg = tmp_path / "s01.mp4"
    seg.write_bytes(b"x")
    jsonstate.write(seg.with_suffix(".json"), {"start_s": 100.0, "end_s": 400.0, "cut_start_s": 98.5})
    assert thumbs_stage.cut_key(seg) == "98.5-400.0"  # a segment's bounds, from its sidecar
    video.write_bytes(b"re-encoded")  # same cut, new file: still fresh
    assert not thumbs_stage.cut_changed({"cut_key": key, "video_mtime": 0}, video)
    jsonstate.write(video.with_suffix(".json"), {"in_s": 12.0, "out_s": 40.0})
    assert thumbs_stage.cut_changed({"cut_key": key}, video)
    # a cover made before cut_key existed still goes by the mtime
    assert thumbs_stage.cut_changed({"video_mtime": 0}, video)
    assert not thumbs_stage.cut_changed({"video_mtime": video.stat().st_mtime}, video)
