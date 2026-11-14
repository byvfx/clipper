import math

import pytest

from clipper import jsonstate, shorts, tighten


@pytest.fixture
def ec(defaults):
    return {**defaults["edit"], "max_gap_s": 0.7, "keep_gap_s": 0.2, "keep_turn_s": 0.4,
            "min_cut_s": 0.3}


def word(w, s, e, speaker=None):
    return {"word": w, "start": s, "end": e, **({"speaker": speaker} if speaker else {})}


def test_cuts_a_long_pause_where_it_is_quiet(ec):
    ws = [word("so", 100.0, 100.5), word("anyway", 102.5, 103.0)]
    # The window starts at 100: the gap is 0.5-2.5 s into the Short, 0.1 s kept each side.
    assert tighten.cuts(ws, 100.0, 104.0, [(0.0, 10.0)], ec) == [
        {"from": 0.6, "to": 2.4, "before": "so", "after": "anyway"}]
    # Only the quiet part of the gap goes; noise in it (a laugh, a video) stays.
    assert tighten.cuts(ws, 100.0, 104.0, [(1.5, 10.0)], ec)[0]["from"] == 1.5
    assert tighten.cuts(ws, 100.0, 104.0, [], ec) == []


def test_short_pauses_and_slivers_stay(ec):
    ws = [word("a", 0.0, 1.0), word("b", 1.6, 2.0), word("c", 2.9, 3.0)]
    # 0.6 s is under max_gap_s; the 0.9 s gap only has 0.2 s of quiet in it.
    assert tighten.cuts(ws, 0.0, 3.0, [(0.0, 1.6), (2.2, 2.4)], ec) == []


def test_a_change_of_voice_keeps_a_longer_beat(ec):
    ws = [word("right?", 0.0, 1.0, "SPEAKER_00"), word("no", 3.0, 3.5, "SPEAKER_01")]
    assert tighten.cuts(ws, 0.0, 4.0, [(0.0, 9.0)], ec)[0] == {
        "from": 1.2, "to": 2.8, "before": "right?", "after": "no"}


def test_never_into_a_bleep(ec):
    ws = [word("x", 0.0, 1.0), word("y", 3.0, 3.5)]
    # 1.17 s, moved in to the next whole frame.
    assert tighten.cuts(ws, 0.0, 4.0, [(0.0, 9.0)], {**ec, "keep_gap_s": 0.0}, bleep_pad=0.15)[0][
        "from"] == pytest.approx(1.2)


def test_cuts_sit_on_whole_frames(ec):
    ws = [word("a", 0.0, 1.01), word("b", 2.5, 3.0)]
    got = tighten.cuts(ws, 0.0, 4.0, [(0.0, 9.0)], ec)[0]
    # 1.11-2.4 s at 30 fps: in to frames 34 and 72; at 25 fps, 28 and 60.
    assert (got["from"], got["to"]) == (1.133, 2.4)
    got = tighten.cuts(ws, 0.0, 4.0, [(0.0, 9.0)], ec, fps=25)[0]
    assert (got["from"], got["to"]) == (1.12, 2.4)
    # A pause that is min_cut_s only before it is moved in stays.
    # (1.11-1.42 s is 0.31 s; 1.133-1.4 s is 0.267 s.)
    assert tighten.cuts([word("a", 0.0, 1.01), word("b", 1.52, 2.0)], 0.0, 2.0, [(0.0, 9.0)],
                        {**ec, "max_gap_s": 0.4}) == []


def test_the_longest_pauses_go_first_within_the_limit(ec):
    ws = [word("a", 0.0, 1.0), word("b", 2.0, 3.0), word("c", 6.0, 7.0)]
    got = tighten.cuts(ws, 0.0, 7.0, [(0.0, 9.0)], ec, max_removed=3.0)
    assert [(c["from"], c["to"]) for c in got] == [(3.1, 5.9)]  # the 0.8 s one didn't fit
    assert len(tighten.cuts(ws, 0.0, 7.0, [(0.0, 9.0)], ec)) == 2


def test_timeline_moves_times_scenes_and_words():
    tl = tighten.Timeline([{"from": 2.0, "to": 3.0}, {"from": 5.0, "to": 5.5}], 10.0)
    assert (tl.removed, tl.length) == (1.5, 8.5)
    assert [tl(t) for t in (1.0, 2.5, 3.0, 4.0, 6.0, 10.0)] == [1.0, 2.0, 2.0, 3.0, 4.5, 8.5]
    assert tl.keeps() == [(0.0, 2.0), (3.0, 5.0), (5.5, 10.0)]
    # By frame: a frame is kept when it starts in a stretch, the edges half a frame early.
    assert tl.select() == ("gte(t,-0.0167)*lt(t,1.9833)+gte(t,2.9833)*lt(t,4.9833)"
                           "+gte(t,5.4833)*lt(t,9.9833)")
    assert tl.scenes([[0.0, 2.5, "cams"], [2.5, 3.0, "watch"], [3.0, 10.0, "cams"]]) == [
        [0.0, 2.0, "cams"], [2.0, 2.0, "watch"], [2.0, 8.5, "cams"]]
    assert tl.words([word("w", 104.0, 104.5)], 100.0) == [word("w", 103.0, 103.5)]


def test_label():
    assert tighten.label([]) == ""
    assert tighten.label([{"from": 1.0, "to": 2.25}]) == "1 pause, 1.2 s"
    assert tighten.label([{"from": 1.0, "to": 2.0}, {"from": 3.0, "to": 4.5}]) == "2 pauses, 2.5 s"


@pytest.fixture
def three_shorts(tmp_path, monkeypatch, defaults):
    """A stream with three picks and shorts.run's rendering faked: (stream, cfg, {key: tight})."""
    sd = tmp_path / "s1"
    sd.mkdir()
    picks = [{"id": f"t{i}", "title": f"T{i}"} for i in (1, 2, 3)]
    jsonstate.write(sd / "candidates.json", {
        "pass_a": [{"id": p["id"], "in_s": 100.0 * i, "out_s": 100.0 * i + 30} for i, p in enumerate(picks)],
        "pass_b": {"picks": picks}})
    jsonstate.write(sd / "meta.json", {"duration_s": 1000})
    defaults["paths"]["schedule"] = tmp_path / "schedule.json"
    monkeypatch.setattr(shorts, "for_stream", lambda c, s: c)
    monkeypatch.setattr(shorts, "load_words", lambda s: [])
    monkeypatch.setattr(shorts, "source_file", lambda s: s / "src.mp4")
    monkeypatch.setattr(shorts, "short_window", lambda c, *_: (c["in_s"], c["out_s"]))
    monkeypatch.setattr(shorts, "with_examples", lambda layouts, c: layouts)
    seen = {}

    def render(stream_dir, cfg, src, words, bl, layouts, scenes, a, b, work, key, dst, codec, faces, tight):
        seen[key] = tight
        dst.write_bytes(b"x")
        return 0, 0, {"input_lufs": -20.0}, [], round(b - a, 3)
    monkeypatch.setattr(shorts, "cut_and_render", render)
    from clipper import thumbs
    monkeypatch.setattr(thumbs, "shorts_only", lambda *a, **k: 0)
    return sd, defaults, seen


def test_a_plain_run_leaves_each_shorts_override_alone(three_shorts):
    sd, cfg, seen = three_shorts
    shorts.run(sd, cfg)  # tighten off: no Short is marked
    assert not any("tighten" in e for e in jsonstate.read(sd / "shorts.json", {}).values())
    cfg["edit"]["tighten"] = True
    shorts.run(sd, cfg, ids=["v02"], tight=False)  # --items v02 --no-tighten
    seen.clear()
    shorts.run(sd, cfg, force=True)
    assert seen == {"v01_t1": True, "v02_t2": False, "v03_t3": True}
    assert jsonstate.read(sd / "shorts.json", {})["v02_t2"]["tighten"] is False
    shorts.run(sd, cfg, ids=["v03"], tight=False, check=True)  # --check writes nothing
    assert "tighten" not in jsonstate.read(sd / "shorts.json", {})["v03_t3"]


def test_quiet_spans_parses_silencedetect(monkeypatch, ec, tmp_path):
    log = ("[silencedetect @ 0x1] silence_start: -0.01\n[silencedetect @ 0x1] silence_end: 1.2 | "
           "silence_duration: 1.2\n[silencedetect @ 0x1] silence_start: 8.5\n")

    class R:
        stderr = log
    monkeypatch.setattr(tighten.subprocess, "run", lambda *a, **k: R())
    assert tighten.quiet_spans(tmp_path / "x.mp4", ec) == [(0.0, 1.2), (8.5, math.inf)]
