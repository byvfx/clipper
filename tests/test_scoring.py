"""Scoring (clipper/scoring.py): the paid-rerun guard, Pass B hygiene (its picks are kept
candidates, each once), the windows.json hash, and pick numbers that survive re-scores."""
import pytest

from clipper import jsonstate, scoring
from clipper.errors import ClipperError
from clipper.states import carry_numbers


def test_score_refuses_a_paid_rerun_without_force(defaults, tmp_path):
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    jsonstate.write(sdir / "candidates.json", {"pass_b": {"picks": []}})
    with pytest.raises(ClipperError, match="--force"):
        scoring.run(sdir, defaults)


class FakeClaude:
    cost = 0.0

    def __init__(self, picks):
        self.picks = picks

    def json_call(self, *a, **k):
        return {"picks": self.picks, "rejected": []}, {}


class FakePrompts:
    version = "v5"

    def get(self, name, **fill):
        return name


def test_pass_b_picks_are_kept_candidates_once(defaults, monkeypatch):
    monkeypatch.setattr(scoring, "feedback_block", lambda cfg, key: "", raising=False)
    from clipper import analytics
    monkeypatch.setattr(analytics, "feedback_block", lambda cfg, key: "")
    kept = [{"id": "t01", "in_s": 0, "out_s": 10, "short_in_s": 0, "short_out_s": 5, "category": "c",
             "score": 8, "heat": 1.0, "hook": "h", "title": "t", "why": "w"}]
    out = scoring.run_pass_b(kept, [], [], defaults, FakeClaude([{"id": "t01", "title": "a"}, {"id": "t01", "title": "b"},
                                                                 {"id": "t09", "title": "not kept"}]), FakePrompts())
    assert [p["id"] for p in out["picks"]] == ["t01"]
    assert out["dropped"] == ["t01", "t09 (not a kept candidate)"] and out["unknown_ids"] == ["t09"]


def test_file_sha_tracks_windows_json(tmp_path):
    p = tmp_path / "windows.json"
    assert scoring.file_sha(p) == ""
    p.write_text("a", encoding="utf-8")
    one = scoring.file_sha(p)
    p.write_text("b", encoding="utf-8")
    assert one and one != scoring.file_sha(p)


def test_retired_numbers_stay_reserved_and_hand_picks_stay():
    old_b = {"picks": [{"id": "t01", "n": 1}, {"id": "t02", "n": 2}, {"id": "h01", "n": 3}], "numbers": {"t05": 4}}
    fresh = scoring.retire_numbers(old_b, {"h01"})
    assert fresh["numbers"] == {"retired:t01": 1, "retired:t02": 2, "h01": 3, "retired:t05": 4}
    assert [p["id"] for p in fresh["picks"]] == ["h01"]
    picks, numbers = carry_numbers(fresh, [{"id": "t02"}, {"id": "h01"}])
    assert [(p["id"], p["n"]) for p in picks] == [("t02", 5), ("h01", 3)]  # t02 is a new moment: a new number
    again = scoring.retire_numbers({"picks": picks, "numbers": numbers}, {"h01"})
    assert again["numbers"]["retired:t02"] == 5 and again["numbers"]["retired:t01"] == 1


def test_a_rescan_that_moves_a_transcript_id_retires_its_number():
    old = [{"id": "w01", "window": [0, 60]}, {"id": "t01", "source": "transcript", "window": [100, 160]},
           {"id": "h01", "source": "hand", "window": [5, 9]}, {"id": "t02", "source": "transcript"}]
    same = [{"id": "w01", "window": [0, 60]}, {"id": "t01", "source": "transcript", "window": [100, 160]}]
    assert scoring.moved_ids(old, same) == []
    rescanned = same[:1] + [{"id": "t01", "source": "transcript", "window": [40, 90]},
                            {"id": "t02", "source": "transcript", "window": [100, 160]}]
    assert scoring.moved_ids(old, rescanned) == ["t01"]  # t02 had no window recorded: not counted
    assert scoring.moved_ids(old, same[:1]) == ["t01"]  # gone: its number goes before t01 can return
