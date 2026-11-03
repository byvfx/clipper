"""Fixes from the 2026-11-03 code audit: guards that keep a quiet failure from staying
quiet, and cost guards on the paid stages."""
import pytest

from clipper import config, cut, jsonstate, llm, scoring, stages
from clipper.errors import ClipperError
from clipper.thumbs import stage as thumbs_stage

# ---------- quiet failures ----------

def test_notify_times_and_budget_are_checked_at_load(defaults):
    """A typo in [notify] used to kill the bot's DM loop every minute with no DM to say so."""
    cfg = {**defaults, "notify": {**defaults["notify"], "quiet_hours": "9:00pm-07:00", "digest_time": "09:00"}}
    assert [p for p in config.problems(cfg) if "quiet_hours" in p] == ["[notify] quiet_hours = '9:00pm-07:00': HH:MM-HH:MM (or empty)"]
    cfg["notify"]["quiet_hours"], cfg["notify"]["digest_time"] = "23:00-07:00", "9:5"
    assert [p for p in config.problems(cfg) if "notify" in p] == ["[notify] digest_time = '9:5': HH:MM (or empty)"]
    cfg["notify"]["digest_time"] = ""
    assert not [p for p in config.problems(cfg) if "notify" in p]
    cfg["schedule"] = {**defaults["schedule"], "budget_hours": 0}
    assert [p for p in config.problems(cfg) if "budget_hours" in p]


def test_a_bad_notify_time_is_a_clear_error_in_the_loop():
    from clipper import alerts

    with pytest.raises(ClipperError, match="isn't HH:MM"):
        alerts._hm("9:00pm")
    assert alerts._hm(" 07:30 ") == (7, 30)


# ---------- cost guards ----------

def test_score_refuses_a_paid_rerun_without_force(defaults, tmp_path):
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    jsonstate.write(sdir / "candidates.json", {"pass_b": {"picks": []}})
    with pytest.raises(ClipperError, match="--force"):
        scoring.run(sdir, defaults)


def test_a_forced_run_passes_force_to_the_stages_that_refuse_reruns(defaults):
    wc = defaults["worker"]
    assert stages.argv("score", "kick-1", wc) == ["score", "kick-1"]
    assert stages.argv("score", "kick-1", wc, force=True) == ["score", "kick-1", "--force"]
    assert "--force" in stages.argv("cut", "kick-1", wc, force=True)
    assert "--force" not in stages.argv("meta", "kick-1", wc, force=True)


def test_an_unchanged_pick_is_not_cut_again(tmp_path):
    dst = tmp_path / "03_t03_x.mp4"
    p, c = {"id": "t03"}, {"in_s": 10.0, "out_s": 40.0}
    assert not cut.unchanged_pick(dst, p, c)  # nothing cut yet
    dst.write_bytes(b"x")
    jsonstate.write(dst.with_suffix(".json"), {"id": "t03", "in_s": 10.0, "out_s": 40.0})
    assert cut.unchanged_pick(dst, p, c)
    assert not cut.unchanged_pick(dst, p, {"in_s": 12.0, "out_s": 40.0})  # a re-score moved it


def test_a_cover_goes_stale_on_the_cut_not_the_file(tmp_path):
    video = tmp_path / "c.mp4"
    video.write_bytes(b"x")
    jsonstate.write(video.with_suffix(".json"), {"in_s": 10.0, "out_s": 40.0})
    key = thumbs_stage.cut_key(video)
    assert key == "10.0-40.0"
    video.write_bytes(b"re-encoded")  # same cut, new file: still fresh
    assert not thumbs_stage.cut_changed({"cut_key": key, "video_mtime": 0}, video)
    jsonstate.write(video.with_suffix(".json"), {"in_s": 12.0, "out_s": 40.0})
    assert thumbs_stage.cut_changed({"cut_key": key}, video)
    # a cover made before cut_key existed still goes by the mtime
    assert thumbs_stage.cut_changed({"video_mtime": 0}, video)
    assert not thumbs_stage.cut_changed({"video_mtime": video.stat().st_mtime}, video)


def test_prices_match_the_longest_listed_prefix():
    prices = {"claude-sonnet-5-5": [2.0, 10.0], "claude-sonnet-5-5-fast": [4.0, 20.0], "gpt-5": [1.0, 2.0]}
    assert llm.price_of(prices, "claude-sonnet-5-5") == (2.0, 10.0)
    assert llm.price_of(prices, "claude-sonnet-5-5-20260601") == (2.0, 10.0)
    assert llm.price_of(prices, "claude-sonnet-5-5-fast-1") == (4.0, 20.0)
    assert llm.price_of(prices, "openai:gpt-5-mini") == (1.0, 2.0)
    assert llm.price_of(prices, "claude-opus-5-5") is None
