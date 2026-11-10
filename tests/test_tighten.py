import math

import pytest

from clipper import tighten


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
    assert tighten.cuts(ws, 0.0, 4.0, [(0.0, 9.0)], {**ec, "keep_gap_s": 0.0}, bleep_pad=0.15)[0][
        "from"] == pytest.approx(1.17)


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
    assert tl.select() == "between(t,0.000,2.000)+between(t,3.000,5.000)+between(t,5.500,10.000)"
    assert tl.scenes([[0.0, 2.5, "cams"], [2.5, 3.0, "watch"], [3.0, 10.0, "cams"]]) == [
        [0.0, 2.0, "cams"], [2.0, 2.0, "watch"], [2.0, 8.5, "cams"]]
    assert tl.words([word("w", 104.0, 104.5)], 100.0) == [word("w", 103.0, 103.5)]


def test_label():
    assert tighten.label([]) == ""
    assert tighten.label([{"from": 1.0, "to": 2.25}]) == "1 pause, 1.2 s"
    assert tighten.label([{"from": 1.0, "to": 2.0}, {"from": 3.0, "to": 4.5}]) == "2 pauses, 2.5 s"


def test_quiet_spans_parses_silencedetect(monkeypatch, ec, tmp_path):
    log = ("[silencedetect @ 0x1] silence_start: -0.01\n[silencedetect @ 0x1] silence_end: 1.2 | "
           "silence_duration: 1.2\n[silencedetect @ 0x1] silence_start: 8.5\n")

    class R:
        stderr = log
    monkeypatch.setattr(tighten.subprocess, "run", lambda *a, **k: R())
    assert tighten.quiet_spans(tmp_path / "x.mp4", ec) == [(0.0, 1.2), (8.5, math.inf)]
