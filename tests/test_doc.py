from pathlib import Path

import pytest

from clipper import doc, jsonstate, story_edit
from clipper.errors import ClipperError

PLAN = """
title = "The Lighthouse"
credits = ["Footage: the keeper's own uploads"]
pinned = "Thanks for watching."
description = "A keeper and his lamp."

[clips.wave]
reel = "reel_a"
from = 10.0
to = 14.0

[[music]]
file = "cue1"
at = "OPEN"
credit = "Music: made for this piece"

[[music]]
file = "cue2"
at = "2 THE LAMP"
tail = true

[room_tone]
file = "tone"
credit = "Room tone: CC0"

[[seq]]
section = "OPEN"

[[seq]]
card = "card_open"
seconds = 2
silent = true

[[seq]]
say = "He climbed the stairs every night for forty years."
take = "open"
shots = [["reel_a", 1.0], ["reel_b", 5, "every night"]]

[[seq]]
clip = "wave"

[[seq]]
say = "Nobody asked him to."
take = "open"
shots = [["reel_b", 20]]

[[seq]]
section = "2 THE LAMP"

[[seq]]
say = "The lamp went dark in March."
take = "open"
shots = [["reel_a", 30]]

[[seq]]
section = "CLOSE"
chapter = "What is left"

[[seq]]
clip = "end"
reel = "reel_b"
from = 40
to = 52
mute = [[44.0, 45.0]]
"""


def heard_for(text, start=0.5, each=0.3, gap=0.1, pauses=()):
    """A transcript of `text`: every word `each` long, `gap` apart, a longer pause before the
    word indexes in `pauses` ({index: seconds})."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        t += dict(pauses).get(i, 0)
        out.append([w, round(t, 2), round(t + each, 2)])
        t += each + gap
    return out


@pytest.fixture
def plan():
    p = doc.parse_plan(PLAN)
    p["fps"] = 30
    return p


@pytest.fixture
def dc(defaults):
    return defaults["doc"]


def test_plan_reads_every_kind_of_entry(plan):
    kinds = [e["kind"] for e in plan["seq"]]
    assert kinds == ["section", "card", "say", "clip", "say", "section", "say", "section", "clip"]
    say = plan["seq"][2]
    assert say["shots"][1] == {"reel": "reel_b", "at": 5.0, "on": "every night", "over": "", "word": 4}
    assert plan["seq"][3] | {"note": ""} == plan["seq"][3] and plan["seq"][3]["reel"] == "reel_a"
    assert plan["seq"][8]["mute"] == [[44.0, 45.0]] and plan["seq"][8]["start"] == 40.0
    assert doc.needed(plan) == {"card_open", "reel_a", "reel_b", "cue1", "cue2", "tone"}


@pytest.mark.parametrize("bad, says", [
    ('[[seq]]\nsay = "One two three."\nshots = [["r", 0], ["r", 1, "four"]]', "line 1: cue 'four'"),
    ('[[seq]]\nsay = "One two three."\nshots = [["r", 0, "two"]]', "first shot"),
    ('[[seq]]\nsay = "One two one."\nshots = [["r", 0], ["r", 1, "two"], ["r", 2, "two"]]', "cue 'two'"),
    ('[[seq]]\ncard = "c"', "needs seconds"),
    ('\n\n[[seq]]\nclip = "x"\nreel = "r"', "line 3: clip 'x' needs reel, from and to"),
    ('[[seq]]\nclip = "x"\ncard = "c"', "one of section, card, clip or say"),
    ('[[seq]]\nsection = "A"', "no card, clip or say"),
    ('[[music]]\nfile = "m"\nat = "NOPE"\n[[seq]]\ncard = "c"\nseconds = 1', "section names"),
    ("[[seq", "plan.toml:"),
])
def test_a_plan_mistake_is_one_error_naming_its_line(bad, says):
    with pytest.raises(ClipperError, match=says):
        doc.parse_plan(bad)


def test_lines_sharing_a_take_are_one_read(plan):
    (t,) = doc.takes(plan)
    assert t["says"] == [2, 4, 6]
    assert t["text"] == ("He climbed the stairs every night for forty years. Nobody asked him to. "
                         "The lamp went dark in March.")
    assert t["joined"] == [False, True]   # a clip between the first two; a section isn't something between
    lone = doc.parse_plan('[[seq]]\nsay = "One."\nshots = [["r", 0]]\n[[seq]]\nsay = "Two."\nshots = [["r", 0]]')
    assert [x["id"] for x in doc.takes(lone)] == ["#1", "#2"]


def test_align_takes_heard_times_and_spreads_the_words_between():
    script = doc.tokens("He owes 800 dollars to the bank.")
    heard = [["He", 0.0, 0.2], ["owes", 0.3, 0.5], ["eight", 0.6, 0.8], ["hundred", 0.8, 1.2], ["dollars", 1.4, 1.8],
             ["to", 1.9, 2.0], ["the", 2.0, 2.1], ["bank.", 2.2, 2.6]]
    times, share = doc.align(script, heard)
    assert times[1] == (0.3, 0.5) and times[3] == (1.4, 1.8) and times[-1] == (2.2, 2.6)
    assert times[2] == (0.5, 1.4)     # "800" wasn't heard as written: it fills the gap
    assert share == pytest.approx(6 / 7)
    with pytest.raises(ClipperError):
        doc.align(script, [])


def test_a_take_is_cut_in_the_pause_before_each_line():
    lines = ["One two three.", "Four five.", "Six seven eight."]
    heard = heard_for(" ".join(lines), pauses={3: 1.0, 5: 0.6})
    times, share = doc.align(doc.tokens(" ".join(lines)), heard)
    assert share == 1
    a, b, c = doc.spans([3, 2, 3], times, 6.0, [True, False])
    # straight on: one cut in the middle of the pause
    assert a == (0.0, pytest.approx((heard[2][2] + heard[3][1]) / 2)) and b[0] == a[1]
    # something between them: air after the last word and before the first, never past the middle
    assert b[1] == pytest.approx(heard[4][2] + 0.25) and c[0] == pytest.approx(heard[5][1] - 0.15)
    assert c[1] == 6.0
    tight = doc.spans([3, 2], times[:5], 3.0, [False], out_pad=5, in_pad=5)
    assert tight[0][1] == tight[1][0]


def test_pauses_are_capped_not_removed():
    log = ("silence_start: -0.01\nsilence_end: 0.4 |\nsilence_start: 3.0\nsilence_end: 5.0 |\n"
           "silence_start: 7.0\nsilence_end: 7.4 |\nsilence_start: 9.5\nsilence_end: 10.0 |")
    sil = doc.parse_silences(log)
    assert sil == [(-0.01, 0.4), (3.0, 5.0), (7.0, 7.4), (9.5, 10.0)]
    cuts = doc.pause_cuts(sil, 10.0, keep=0.5)
    assert cuts == [(0.0, pytest.approx(0.35)), (3.25, 4.75), (pytest.approx(9.62), 10.0)]


def test_a_take_is_bought_again_only_when_its_words_or_voice_change(plan, dc):
    nc = {"voice_id": "v1", "tts_model": "m", "tts_settings": {}}
    (t,) = doc.takes(plan)
    raw, done = doc.take_keys(t, nc, dc)
    assert doc.take_keys(t, nc, {**dc, "vo_filter": ""}) == (raw, doc.take_keys(t, nc, {**dc, "vo_filter": ""})[1])
    assert doc.take_keys(t, nc, {**dc, "vo_filter": ""})[1] != done        # the compressor: processed again, not bought
    assert doc.take_keys(t, {**nc, "voice_id": "v2"}, dc)[0] != raw
    assert doc.take_keys({**t, "text": t["text"] + " More."}, nc, dc)[0] != raw


DUR = {"reel_a": 120.0, "reel_b": 90.0, "cue1": 60.0, "cue2": 30.0, "card_open": 20.0, "tone": 30.0}


def narration_of(plan):
    (t,) = doc.takes(plan)
    heard = heard_for(t["text"], pauses={9: 0.8, 13: 0.8})
    times, _ = doc.align(doc.tokens(t["text"]), heard)
    counts = [len(doc.tokens(x)) for x in t["texts"]]
    out, at = {}, 0
    for n, (a, b), c in zip(t["says"], doc.spans(counts, times, heard[-1][2] + 0.3, t["joined"]), counts):
        out[n] = {"file": "vo_take", "start": a, "end": b, "words": times[at:at + c]}
        at += c
    return out


def test_layout_puts_everything_on_its_track_at_its_frame(plan, dc):
    vo = narration_of(plan)
    b = doc.layout(plan, vo, DUR, dc, {"reel_a": "label_reel_a"})
    assert doc.picture_gaps(b) == []
    assert [s["name"] for s in b["sections"]] == ["OPEN", "2 THE LAMP", "CLOSE"]
    assert b["sections"][0]["frame"] == 0 and b["markers"][0]["color"] == "Red"
    card = b["v1"][0]
    assert (card["clip"], card["record"], card["end"]) == ("card_open", 0, 60)
    assert b["quiet"] == [[0, 60]] and b["dead"] == [[0, 60]]
    # the second shot cuts in a moment before its cue words
    first, second = b["v1"][1], b["v1"][2]
    cue = vo[2]["words"][4][0]
    assert second["clip"] == "reel_b" and second["record"] - first["record"] == round((cue - 0.1) * 30) + 3
    assert second["start"] == 150
    # the narration: one file, three pieces; the last two run straight on with no fade between
    takes = [r for r in b["audio"] if r["track"] == doc.A_VO]
    assert [r["clip"] for r in takes] == ["vo_take"] * 3
    assert takes[1]["end"] == takes[2]["start"] and (takes[1]["fade_out"], takes[2]["fade_in"]) == (0, 0)
    assert takes[1]["record"] + takes[1]["end"] - takes[1]["start"] == takes[2]["record"]
    # the clip's sound comes up under the line before it and fades out under the line after
    kinds = sorted((x["kind"], x["reel"]) for x in b["blend"])
    assert kinds == [("in", "reel_a"), ("in", "reel_b"), ("out", "reel_a")]
    # a muted clip: its picture whole, its sound in two pieces around the cut
    last = b["sync"][-1]
    assert last["type"] == doc.VIDEO and last["end"] - last["start"] == 360
    pieces = [r for r in b["audio"] if r["track"] == doc.A_CLIP]
    assert [(r["start"], r["end"]) for r in pieces] == [(1200, 1320), (1350, 1560)]
    assert pieces[1]["record"] - pieces[0]["record"] == 150 and (pieces[0]["fade_out"], pieces[1]["fade_in"]) == (2, 2)
    assert b["total"] == last["record"] + 360
    # labels: only the reel that has one, on V2
    assert {r["clip"] for r in b["v2"]} == {"label_reel_a"} and all(r["track"] == doc.V_LABELS for r in b["v2"])


def test_a_cue_that_cant_be_placed_stops_the_layout(plan, dc):
    vo = narration_of(plan)
    with pytest.raises(ClipperError, match="which is 1.0 s long"):
        doc.layout(plan, vo, {**DUR, "reel_b": 1.0}, dc, {})
    vo[2]["words"][4] = (vo[2]["start"], vo[2]["start"])   # the cue lands on the line's first frame
    with pytest.raises(ClipperError, match="two cues land on one frame"):
        doc.layout(plan, vo, DUR, dc, {})
    with pytest.raises(ClipperError, match="clip 'end' runs to 52.0 s; reel_b is 50.0 s long"):
        doc.layout(plan, narration_of(plan), {**DUR, "reel_b": 50.0}, dc, {})


def test_score_crosses_over_at_the_section_and_levels_follow_the_dialogue(plan, dc):
    b = doc.layout(plan, narration_of(plan), DUR, dc, {})
    doc.add_score(b, plan, DUR, dc)
    one, two = (r for r in b["audio"] if r.get("music"))
    lamp = b["sections"][1]["frame"]
    # the first cue starts under the silent opening card: it waits for the card to end
    assert (one["record"], one["track"], two["track"]) == (60, doc.A_SCORE, doc.A_SCORE + 1)
    assert not any(r["record"] < 60 for r in b["audio"] if r.get("music"))
    assert one["record"] + one["end"] - one["start"] == lamp + 60 and two["record"] == lamp   # a 2 s crossover
    assert two["end"] == 900 and two["record"] + two["end"] - two["start"] <= b["total"]      # tail: its own ending
    tone = [r for r in b["audio"] if r.get("tone")]
    assert [(r["record"], r["record"] + r["end"] - r["start"]) for r in tone] == [(60, b["total"])]

    def measure(clip, start, length):
        return {"vo_take": (-22.0, -6.0), "cue1": (-10.0, -1.0), "cue2": (-10.0, -1.0),
                "roomtone_bed": (-30.0, -20.0)}.get(clip, (-30.0, -3.0) if start is not None else (-70.0, 0.0))

    doc.set_levels(b, measure, dc)
    assert {r["gain"] for r in b["audio"] if r["track"] == doc.A_VO} == {5.0}     # -22 -> -16, capped by the -1 peak
    assert one["gain"] == two["gain"] == -22.0                                    # 16 LU under the dialogue
    assert tone[0]["gain"] == -24.0
    assert b["sync"][0]["gain"] == 2.0                                            # the peak ceiling holds it back
    assert all(r["gain"] == 2.0 for r in b["audio"] if r["track"] == doc.A_UNDER)  # a fade takes its clip's gain
    assert doc.gain(-60.0, -40.0, -16.0, -1.0) == 0.0


def test_mutes_are_taken_out_as_one_union():
    assert doc.kept(300, 420, [(330, 405), (360, 390)]) == [(300, 330), (405, 420)]      # nested
    assert doc.kept(300, 420, [(100, 200), (410, 500)]) == [(300, 410)]                  # outside, and past the end
    assert doc.kept(300, 420, [(250, 310), (305, 320)]) == [(320, 420)]                  # overlapping, from before
    assert doc.kept(300, 420, [(0, 999)]) == [] and doc.kept(300, 420, []) == [(300, 420)]


def test_a_fade_is_named_for_the_file_it_is_cut_from(plan, dc):
    one = doc.layout(plan, narration_of(plan), DUR, dc, {}, {"reel_a": ["a.mp4", 10, 1], "reel_b": ["b.mp4", 20, 1]})
    two = doc.layout(plan, narration_of(plan), DUR, dc, {}, {"reel_a": ["a.mp4", 10, 2], "reel_b": ["b.mp4", 20, 1]})
    a1, a2 = ({x["name"] for x in b["blend"] if x["reel"] == "reel_a"} for b in (one, two))
    b1, b2 = ({x["name"] for x in b["blend"] if x["reel"] == "reel_b"} for b in (one, two))
    assert not a1 & a2 and b1 == b2      # reel_a's file changed, reel_b's didn't


def test_a_changed_file_is_measured_and_converted_again(tmp_path, monkeypatch):
    import os

    from clipper import media as media_mod

    f = tmp_path / "music" / "theme.mp3"
    f.parent.mkdir()
    f.write_bytes(b"1234")
    seen = []
    monkeypatch.setattr(media_mod, "duration", lambda p: seen.append(p) or 12.0)
    assert doc.lengths(tmp_path, {"theme": f}, {"theme"}) == {"theme": 12.0}
    doc.lengths(tmp_path, {"theme": f}, {"theme"})
    assert len(seen) == 1                                   # probed once
    monkeypatch.setattr(doc, "_run", lambda cmd: Path(cmd[-1]).write_bytes(b"wav"))
    one = doc.as_wav(tmp_path, f)
    f.write_bytes(b"abcd")                                  # replaced: the same size
    os.utime(f, ns=(1, 1))
    doc.lengths(tmp_path, {"theme": f}, {"theme"})
    assert len(seen) == 2 and doc.as_wav(tmp_path, f) != one


def test_a_still_becomes_a_movie_as_long_as_its_longest_use(tmp_path, monkeypatch):
    png, mp4 = tmp_path / "label.png", tmp_path / "reel.mp4"
    png.write_bytes(b"png")
    mp4.write_bytes(b"mp4")
    made = []
    monkeypatch.setattr(doc, "_run", lambda cmd: made.append(cmd) or Path(cmd[-1]).write_bytes(b"mov"))
    b = {"fps": 30, "sync": [], "v1": [{"clip": "reel", "start": 0, "end": 900}], "v3": [],
         "v2": [{"clip": "label", "start": 0, "end": 60}, {"clip": "label", "start": 0, "end": 150}]}
    media = {"label": png, "reel": mp4}
    doc.still_movies(tmp_path, b, media)
    assert media["reel"] == mp4 and media["label"].suffix == ".mov" and media["label"].name.endswith("_7.mov")
    assert made[0][made[0].index("-t") + 1] == "7" and len(made) == 1
    media["label"] = png
    doc.still_movies(tmp_path, b, media)
    assert len(made) == 1          # kept


def test_master_gain_and_loudness_parsing():
    g, filt = doc.master_filter(-20.3, -14.0, -1.0)
    assert g == 6.3 and filt.startswith("volume=6.3dB,alimiter=limit=0.8128")
    log = "  Integrated loudness:\n    I:         -19.8 LUFS\n  True peak:\n    Peak:       -2.1 dBFS\n"
    assert doc.parse_loudness(log) == (-19.8, -2.1)
    assert doc.parse_loudness("nothing") == (-70.0, 0.0)


def test_chapters_come_from_the_sections():
    secs = [{"name": "COLD OPEN", "frame": 150, "chapter": True}, {"name": "1 WHAT YOU SEE", "frame": 1800, "chapter": True},
            {"name": "ASIDE", "frame": 1950, "chapter": True}, {"name": "note to self", "frame": 3000, "chapter": False},
            {"name": "2 Where it goes", "frame": 6000, "chapter": True}, {"name": "CLOSE", "frame": 9000, "chapter": "The end"}]
    assert doc.chapters(secs, 30, 12000) == [(0.0, "Cold open"), (60.0, "What you see"), (200.0, "Where it goes"),
                                             (300.0, "The end")]
    assert doc.chapters(secs[:2], 30, 12000) == []   # YouTube wants three
    # the last would be five seconds long: left out; and then two aren't enough
    assert [t for _, t in doc.chapters(secs, 30, 9150)] == ["Cold open", "What you see", "Where it goes"]
    assert doc.chapters([secs[0], secs[1], secs[5]], 30, 9150) == []


def test_kit_has_chapters_credits_and_disclosures(plan, dc):
    b = doc.layout(plan, narration_of(plan), DUR, dc, {})
    b["sections"] = [{"name": "OPEN", "frame": 0, "chapter": True}, {"name": "2 THE LAMP", "frame": 600, "chapter": True},
                     {"name": "CLOSE", "frame": 1200, "chapter": "What is left"}]
    b["total"] = 1800
    text = doc.kit_text(plan, b, dc["disclosures"])
    assert "0:00 Open\n0:20 The lamp\n0:40 What is left" in text
    assert "Credits\nFootage: the keeper's own uploads\nMusic: made for this piece\nRoom tone: CC0" in text
    assert "The narration is an AI voice." in text and "Thanks for watching." in text
    assert "hosts" not in text.lower()


def test_banned_file_and_leaks(tmp_path):
    f = tmp_path / "banned.txt"
    f.write_text("# names\nHarbor House\nMarlowe   # the keeper\nAnn\n!marlow street\n", encoding="utf-8")
    terms, allowed = doc.read_banned(f)
    assert terms == ["Harbor House", "Marlowe", "Ann"] and allowed == {"marlowstreet"}
    words = [(w, i * 0.5) for i, w in enumerate(
        "he stayed at harbour house, then Harborhouse again. Marlow said planning an annual trip "
        "down Marlow Street with Ann's dog".split())]
    hits = doc.leaks(words, terms, allowed, 0.82)
    got = [(h["term"], h["heard"], h["exact"]) for h in hits]
    assert ("Harbor House", "harbour house,", False) in got     # a transcript's spelling
    assert ("Harbor House", "Harborhouse", True) in got          # joined
    assert ("Marlowe", "Marlow", False) in got
    assert ("Ann", "Ann's", True) in got                         # a possessive
    assert doc.leaks([("Anna", 0), ("Annex", 1), ("Anns", 2)], ["Ann"], set(), 0.82) == []   # other words, not hers
    joined = doc.leaks([("on", 0), ("HarborHouseRoad", 1.5)], ["Harbor House Road"], set(), 1.0)
    assert [(h["heard"], h["exact"], h["t"]) for h in joined] == [("HarborHouseRoad", True, 1.5)]
    assert not any(h["term"] == "Ann" and "annual" in h["heard"] or "planning" in h["heard"] for h in hits)
    assert not any("Street" in h["heard"] for h in hits)         # marked fine in the file
    assert len([h for h in hits if h["term"] == "Marlowe"]) == 1
    assert next(h["t"] for h in hits if h["heard"] == "Harborhouse") == 3.0
    assert doc.leaks(words, [], set(), 0.82) == []
    assert doc.read_banned(tmp_path / "none.txt") == ([], set())


def piece(defaults, tmp_path, text=PLAN):
    defaults["paths"]["documentaries"] = tmp_path / "documentaries"
    d = doc.new(defaults, "lighthouse", "The Lighthouse")
    (d / doc.PLAN).write_text(text, encoding="utf-8")
    return d


def test_new_makes_a_piece_that_parses(defaults, tmp_path):
    defaults["paths"]["documentaries"] = tmp_path / "documentaries"
    d = doc.new(defaults, "lighthouse", 'The "Lighthouse"')
    assert (d / "banned.txt").exists() and (d / "reels").is_dir()
    with pytest.raises(ClipperError, match="no card, clip or say"):
        doc.load_plan(defaults, "lighthouse")           # the starter is all comments but a section
    with pytest.raises(ClipperError, match="already exists"):
        doc.new(defaults, "lighthouse")
    with pytest.raises(ClipperError, match="lower-case"):
        doc.piece_dir(defaults, "../x")
    with pytest.raises(ClipperError, match="no piece"):
        doc.load_plan(defaults, "nope")


def test_voice_refuses_a_banned_word_and_prices_a_dry_run(defaults, tmp_path, capsys, monkeypatch):
    from clipper import narrate

    d = piece(defaults, tmp_path)
    monkeypatch.setattr(narrate, "speak", lambda *a, **k: pytest.fail("a dry run buys nothing"))
    doc.voice(defaults, "lighthouse", dry_run=True)
    out = capsys.readouterr().out
    assert "1 takes, 1 to buy: 100 characters" in out and "open: 100 characters, 3 line(s)" in out
    (d / "banned.txt").write_text("the stairs\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="the narration has what banned.txt lists: 'the stairs'"):
        doc.voice(defaults, "lighthouse", dry_run=True)
    (d / "banned.txt").write_text("", encoding="utf-8")
    plan = doc.load_plan(defaults, "lighthouse")[1]
    with pytest.raises(ClipperError, match="isn't voiced yet"):
        doc.narration(d, plan, doc.narrator(defaults, plan), defaults["doc"])
    with pytest.raises(ClipperError, match="names media that isn't in reels, overlays, music, sfx: card_open, cue1"):
        doc.build(defaults, "lighthouse", dry_run=True)


def test_leak_check_fails_on_a_heard_term_and_reads_the_kit(defaults, tmp_path, monkeypatch, capsys):
    d = piece(defaults, tmp_path)
    video = d / "renders" / "cut.master.mp4"
    video.write_bytes(b"mp4")
    with pytest.raises(ClipperError, match="lists nothing"):
        doc.leak(defaults, "lighthouse")
    (d / "banned.txt").write_text("Harbor House\n", encoding="utf-8")
    heard = [("he", 1.0), ("left", 1.5), ("Harbour", 62.0), ("House", 62.4)]
    monkeypatch.setattr(doc, "heard_words", lambda cfg, d, video, dry_run=False: heard)
    with pytest.raises(ClipperError, match="leak check failed: 1 in cut.master.mp4, 0 in kit.md"):
        doc.leak(defaults, "lighthouse")
    assert "1:02  heard 'Harbour House': sounds like a banned term" in capsys.readouterr().out
    monkeypatch.setattr(doc, "heard_words", lambda cfg, d, video, dry_run=False: heard[:2])
    doc.leak(defaults, "lighthouse")
    assert "leak check passed: 2 words heard" in capsys.readouterr().out
    (d / "kit.md").write_text("Filmed near Harbor House.", encoding="utf-8")
    with pytest.raises(ClipperError, match="0 in cut.master.mp4, 1 in kit.md"):
        doc.leak(defaults, "lighthouse")


def test_kit_is_written_from_the_last_build(defaults, tmp_path):
    d = piece(defaults, tmp_path)
    with pytest.raises(ClipperError, match="no build yet"):
        doc.kit(defaults, "lighthouse")
    plan = doc.load_plan(defaults, "lighthouse")[1]
    jsonstate.write(d / "work" / "build.json", doc.layout(plan, narration_of(plan), DUR, defaults["doc"], {}))
    assert "## Pinned comment" in doc.kit(defaults, "lighthouse").read_text(encoding="utf-8")
    (d / "banned.txt").write_text("keeper\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="it has what banned.txt lists"):
        doc.kit(defaults, "lighthouse")


def test_a_storys_loudness_is_signed_only_when_it_differs():
    assert story_edit.loud(-16, -16) is None and story_edit.loud(-14.0, -16) == ["lufs", -14.0]
    part = {"id": "p01", "item": "yt-x", "start": 1.0, "end": 9.0, "channel": "C", "date": "2026-01-01", "title": "T"}
    work = Path("work")
    assert story_edit.part_file(work, part) == story_edit.part_file(work, part, None)      # as before the setting
    assert story_edit.part_file(work, part, ["lufs", -14.0]) != story_edit.part_file(work, part)
    assert story_edit.part_file(work, part, ["lufs", -14.0]) != story_edit.part_file(work, part, ["lufs", -12.0])
    assert story_edit.signature({}, [], lufs=["lufs", -14.0]) == [["lufs", -14.0]]
