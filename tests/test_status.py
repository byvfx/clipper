"""`clipper status`: one screen from the files the services write, nothing called."""
from datetime import UTC, datetime, timedelta

import pytest

from clipper import jsonstate, status


def in_tmp(cfg: dict, tmp_path) -> dict:
    """The defaults resolve [paths] against the working directory: point the ones status reads
    at tmp_path, so a test never writes into the checkout."""
    for key, name in (("streams", "streams"), ("schedule", "schedule.json"), ("costs", "costs.jsonl"),
                      ("modal_runs", "modal_runs.jsonl")):
        cfg["paths"][key] = tmp_path / name
    return cfg


def test_status_reads_the_files_and_names_what_is_missing(defaults, tmp_path):
    defaults = in_tmp(defaults, tmp_path)
    root = defaults["paths"]["streams"]
    (root / "kick-1").mkdir(parents=True)
    jsonstate.write(root / "kick-1" / "run.json", {"status": "running", "stages": {"score": {"status": "done"}}})
    soon = (datetime.now(UTC) + timedelta(hours=3)).isoformat(timespec="seconds")
    defaults["paths"]["schedule"].parent.mkdir(parents=True, exist_ok=True)
    jsonstate.write(defaults["paths"]["schedule"], {"entries": [
        {"key": "kick-1/s02", "stream": "kick-1", "status": "scheduled", "publish_at": soon,
         "kind": "segment", "source": "kick-1", "title": "The one about the thing"}]})
    out = "\n".join(status.lines(defaults, days=1))
    assert "serve: no heartbeat" in out and "recorder: no heartbeat" in out
    assert "kick-1" in out and "last=score:done" in out
    assert "releases in the next 1 day" in out and "The one about the thing" in out
    assert "so far" in out   # the month's spend, $0.00 with no log


def test_status_with_nothing_at_all(defaults, tmp_path):
    out = status.lines(in_tmp(defaults, tmp_path), days=3)
    assert "  none in progress" in out and "  none scheduled" in out


def test_status_counts_reviews_from_files_without_kitsu(defaults, tmp_path, monkeypatch):
    """An old publish saved no Kitsu links; plain `clipper status` still never asks Kitsu for them."""
    from clipper import review

    defaults = in_tmp(defaults, tmp_path)
    sdir = defaults["paths"]["streams"] / "kick-1"
    (sdir / "clips").mkdir(parents=True)
    jsonstate.write(sdir / "review_state.json", {"c01_t20": {
        "kind": "pick", "id": "t20", "file": "clips/01_t20.mp4", "task_id": "T", "entity_id": "E", "fields": {}}})
    jsonstate.write(sdir / "review.json", {"c01_t20": {"decision": "approved", "status": "approved", "fields": {}}})
    jsonstate.write(sdir / "meta.json", {"started_at": "2026-11-07T20:00:00-08:00"})
    jsonstate.write(defaults["paths"]["schedule"], {"entries": []})
    monkeypatch.setattr(review, "connect", lambda rc: pytest.fail("status reached Kitsu"))
    out = "\n".join(status.lines(defaults, days=1))
    assert "kick-1 (2026-11-07): 1 approved" in out and "worker" not in out.split("streams")[0]
