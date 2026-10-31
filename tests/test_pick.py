import pytest

from clipper import pick
from clipper.errors import ClipperError

PC = {"span_s": 600, "max_span_s": 2700, "max_overlap": 0.5}
CLOCK = pick.Clock({})   # no recording parts: the video's clock


def asked(about="", start=None, end=None, at=None, duration=float("inf")):
    return pick.stretches_asked(PC, CLOCK, duration, about, start, end, at)


def test_stretches_asked():
    assert asked(at="1:00:00") == [(3300.0, 3900.0)]
    assert asked(at="0:02:00") == [(0.0, 420.0)]
    assert asked(start="0:30:00", end="0:45:00", duration=2000) == [(1800.0, 2000)]
    assert asked(about="that bit") == []       # located from the summary instead
    for kw, msg in (({"start": "0:30:00"}, "both --from and --to"),
                    ({"start": "0:40:00", "end": "0:30:00"}, "after --from"),
                    ({"start": "0:00:00", "end": "1:00:00"}, "longer than"),
                    ({}, "say where")):
        with pytest.raises(ClipperError, match=msg):
            asked(**kw)


def moment(t, title, dur=30.0, audio=0.0):
    return {"short_in_s": t, "short_out_s": t + dur, "in_s": t, "out_s": t + dur,
            "short_duration_s": dur, "category": "joke", "score": 7, "title": title,
            "source_audio_s": audio}


def test_keep_new_skips_moments_already_picked(capsys):
    cfg = {"pick": PC, "scoring": {"max_source_audio_s": 20}}
    old = [{**moment(100, "old"), "key": "v01_t3"}]
    others = list(old)
    clips = [moment(110, "mostly the old one"), moment(300, "new", audio=45),
             moment(310, "mostly the new one")]
    kept = pick.keep_new(clips, {"api": {"n": 1}}, others, cfg, CLOCK, allow_overlap=False)
    assert [c["title"] for c in kept] == ["new"] and kept[0]["api"] == {"n": 1}
    assert others == old + kept
    out = capsys.readouterr().out
    assert "already a pick: v01_t3 (old)" in out and "already a pick: another new one (new)" in out
    assert "45 s of played-video audio" in out
    assert len(pick.keep_new(clips, {"api": {}}, list(old), cfg, CLOCK, allow_overlap=True)) == 3
