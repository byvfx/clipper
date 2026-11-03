"""Audit 11-03, groups 3-6: scheduler and upload consistency, input edges, Pass B hygiene,
cast and weekly."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clipper import config, jsonstate, schedule, scoring, timefmt
from clipper.cast import choose_face
from clipper.errors import ClipperError
from clipper.weekly import draft, gather

# ---------- scheduler and upload ----------

def test_a_pinned_item_is_never_stale():
    """An explicit Publish at is a decision; once passed it takes the next slot, it isn't dropped."""
    gone = datetime(2026, 11, 1, tzinfo=UTC)
    it = {"expires": gone, "override_text": ""}
    assert not schedule.current(it, gone + timedelta(days=1))
    assert schedule.current({**it, "override_text": "2026-11-01 20:00"}, gone + timedelta(days=1))


def test_fill_leaves_slots_inside_the_upload_lead_time():
    """Upload calls a slot within min_lead_minutes missed; the scheduler mustn't hand one out."""
    now = datetime(2026, 11, 3, 11, 50, tzinfo=UTC)
    sc = {"max_per_day": 10, "min_gap_minutes": 0}
    slots = [(now + timedelta(minutes=10), "short"), (now + timedelta(hours=2), "short")]
    placed = []

    class P:
        def __init__(self):
            self.taken = []

        def __contains__(self, key):
            return False

        def place(self, it, when, how):
            placed.append(when)
    item = {"earliest": now - timedelta(days=1), "key": "k"}
    schedule.fill(P(), {"short": [item]}, slots, ("short",), sc, now + timedelta(minutes=20))
    assert placed == [now + timedelta(hours=2)]


# ---------- input edges ----------

def test_a_missing_stream_is_one_error(defaults, tmp_path):
    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    with pytest.raises(ClipperError, match="no stream 'kick-nope'"):
        config.stream_dir(cfg, "kick-nope")
    (tmp_path / "kick-1").mkdir()
    assert config.stream_dir(cfg, "kick-1") == tmp_path / "kick-1"


def test_effort_values_are_checked(defaults):
    cfg = {**defaults, "meta": {**defaults["meta"], "effort": "med"}}
    with pytest.raises(ClipperError, match=r"\[meta\] effort = 'med'"):
        config.check_choices(cfg)


def test_a_loose_key_in_the_toml_is_a_warning_not_a_crash(tmp_path):
    path = tmp_path / "clipper.toml"
    path.write_text('stale = 1\n[meta]\nshow_name = "x"\n', encoding="utf-8")
    cfg = config.load(path, use_local=False)
    assert any("outside any [section]" in p for p in cfg["_unknown"])
    path.write_text("[shows]\nfoo = 1\n", encoding="utf-8")
    with pytest.raises(ClipperError, match=r"\[shows\] holds"):
        config.load(path, use_local=False)


def test_bad_times_are_clipper_errors():
    with pytest.raises(ClipperError, match="not a time"):
        timefmt.parse_time("abc")
    with pytest.raises(ClipperError, match="--week"):
        gather.week_of({"schedule": {"timezone": "UTC"}}, "11/07")


# ---------- Pass B hygiene and positional ids ----------

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


# ---------- cast and weekly ----------

def test_an_enrolled_face_that_fits_someone_else_better_is_refused():
    import numpy as np

    jer, don = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    face = ({"px": 100, "box": [0, 0, 1, 1]}, np.array([0.6, 0.8]))  # 0.6 like Jer, 0.8 like Don
    known = {"Jer": [jer.tolist()], "Don": [don.tolist()]}
    f, score, why = choose_face([face], "Jer", known, match=0.5, other=0.65)
    assert f is None and "looks like don" in why
    f, score, why = choose_face([face], "Don", known, match=0.5, other=0.65)
    assert f is not None and score == 0.8


def test_a_redraft_keeps_its_issue_number(tmp_path):
    cfg = {"paths": {"weekly": tmp_path}, "weekly": {"first_issue": 2}}
    (tmp_path / "2026-10-31").mkdir()
    jsonstate.write(tmp_path / "2026-10-31" / "issue.json", {"issue": 2})
    here = tmp_path / "2026-11-07"
    here.mkdir()
    assert gather.next_issue_number(cfg, here) == 3
    jsonstate.write(here / "issue.before_2026-11-08_1000.json", {"issue": 3})  # a redraft's backup
    (tmp_path / "2026-11-14").mkdir()
    jsonstate.write(tmp_path / "2026-11-14" / "issue.json", {"issue": 4})
    assert gather.next_issue_number(cfg, here) == 3


def test_check_issue_wants_a_one_or_two_line_headline():
    issue = {page: dict.fromkeys(keys, "x") for page, keys in draft.REQUIRED.items()}
    art = dict.fromkeys(draft.ARTICLE, "x")
    issue["feature"] = {**art, "headline": "ONE STRING"}
    issue["articles"] = [{**art, "headline": ["A", "B"]}, {**art, "headline": ["A", "B", "C"]}]
    missing = draft.check_issue(issue)
    assert missing == ["feature.headline (one or two lines)", "articles[1].headline (one or two lines)"]


def test_stream_dir_still_accepts_a_directory(tmp_path):
    d = tmp_path / "anywhere"
    d.mkdir()
    assert config.stream_dir({"paths": {"streams": Path("/nope")}}, str(d)) == d
