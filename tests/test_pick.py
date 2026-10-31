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


def test_pick_numbers_survive_a_rescore():
    """#77 L13: a re-score that drops or adds picks doesn't shift the vNN of the others."""
    from clipper.states import carry_numbers, numbered_picks
    old = [{"id": "w01"}, {"id": "t04"}, {"id": "w07"}, {"id": "h01", "n": 4}]  # file from before "n"
    assert [n for n, _ in numbered_picks(old)] == [1, 2, 3, 4]
    new = carry_numbers(old, [{"id": "t04"}, {"id": "t09"}, {"id": "w01"}, {"id": "h01", "n": 4}])
    assert [(p["id"], p["n"]) for p in new] == [("t04", 2), ("t09", 5), ("w01", 1), ("h01", 4)]
    assert [(n, p["id"]) for n, p in numbered_picks(new)] == [(2, "t04"), (5, "t09"), (1, "w01"), (4, "h01")]
    assert [p["n"] for p in carry_numbers([], [{"id": "a"}, {"id": "b"}])] == [1, 2]


def test_a_rescore_keeps_hand_picks(tmp_path):
    """#77 L13: `clipper score` again doesn't drop what `clipper pick` added."""
    import json
    from types import SimpleNamespace

    from clipper import scoring
    old = {"pass_a": [{"id": "w01", "status": "kept"}, {"id": "h01", "source": "hand", "status": "kept"}],
           "pass_b": {"picks": [{"id": "w01", "title": "a"}, {"id": "h01", "title": "b", "n": 2}]}}
    (tmp_path / "candidates.json").write_text(json.dumps(old), encoding="utf-8")
    out = scoring._finish(tmp_path, {}, {}, SimpleNamespace(version="v5"), SimpleNamespace(cost=0.0),
                          [], [], [{"id": "w01", "status": "rejected"}], tmp_path / "ckpt.json", 0.0)
    assert [r["id"] for r in out["pass_a"]] == ["w01", "h01"]
    assert out["pass_b"]["picks"] == [{"id": "h01", "title": "b", "n": 2}]


def test_save_picks_numbers_after_the_highest(tmp_path):
    cands = {"pass_a": [{"id": "w01"}, {"id": "t02"}],
             "pass_b": {"picks": [{"id": "t02", "n": 5}, {"id": "w01", "n": 1}]}}
    (tmp_path / "candidates.json").write_text("{}", encoding="utf-8")
    found = [{"in_s": 1.0, "out_s": 20.0, "heat": 0, "title": "x", "hook": "h", "why": "w"}]
    ids = pick.save_picks(tmp_path, cands, found, {"model": "m", "prompt_version": "v1"}, "about")
    assert cands["pass_b"]["picks"][-1] == {"id": ids[0], "title": "x", "hook": "h", "reason": "w", "n": 6}
