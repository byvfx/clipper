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
    day = datetime.now(UTC).date().isoformat()
    jsonstate.write(sdir / "meta.json", {"started_at": f"{day}T20:00:00-08:00"})
    jsonstate.write(defaults["paths"]["schedule"], {"entries": []})
    monkeypatch.setattr(review, "connect", lambda rc: pytest.fail("status reached Kitsu"))
    monkeypatch.setattr(review, "items", lambda *a, **k: pytest.fail("status read every sidecar"))
    out = "\n".join(status.lines(defaults, days=1))
    assert f"kick-1 ({day}): 1 approved" in out


def published(cfg: dict, sid: str, day: str, decisions: dict[str, dict], channel: str = "") -> None:
    sdir = cfg["paths"]["streams"] / sid
    sdir.mkdir(parents=True)
    jsonstate.write(sdir / "review_state.json", {k: {"kind": "pick", "file": f"clips/{k}.mp4"} for k in decisions})
    jsonstate.write(sdir / "review.json", decisions)
    jsonstate.write(sdir / "meta.json", {"started_at": f"{day}T20:00:00-08:00", "title": f"night {sid}",
                                         "channel": channel})


def test_what_needs_you_comes_first_and_old_streams_are_hidden(defaults, tmp_path):
    """#199: pending and held items on top; a month-old stream with nothing waiting only under --all."""
    cfg = in_tmp(defaults, tmp_path)
    today = datetime.now(UTC).date()
    old = (today - timedelta(days=30)).isoformat()
    published(cfg, "kick-old", old, {"c01": {"decision": "approved"}})
    published(cfg, "kick-old-pending", old, {"c01": {"decision": "pending"}})
    published(cfg, "kick-new", today.isoformat(), {
        "c01": {"decision": "pending"}, "c02": {"decision": "changes_requested"},
        "c03": {"decision": "approved", "fields": {"Hold": True}}, "c04": {"decision": "rejected"}})
    out = status.lines(cfg, days=1)
    needs = out[1:out.index("")]
    assert any(x.startswith("  kick-new (") and "1 pending, 1 changes requested, 1 held" in x for x in needs)
    assert any("kick-old-pending" in x for x in needs)    # old, but still waiting: never hidden
    assert not any("kick-old " in x for x in out)
    assert "  1 older streams with nothing waiting: --all lists them" in out
    assert any(x.startswith("  kick-old (") for x in status.lines(cfg, days=1, everything=True))


def test_held_counts_only_what_could_still_go_out(defaults, tmp_path):
    """A rejected item that arrived held is done with; an approved held one in a stream past
    stale_days can't go out without a pin: neither keeps its stream in "needs you"."""
    cfg = in_tmp(defaults, tmp_path)
    cfg["schedule"]["stale_days"] = 3
    today = datetime.now(UTC).date()
    held = {"decision": "approved", "fields": {"Hold": " yes"}}   # read as the scheduler reads it
    published(cfg, "kick-rejected", today.isoformat(), {"c01": {"decision": "rejected", "fields": {"Hold": True}}})
    published(cfg, "kick-stale", (today - timedelta(days=10)).isoformat(), {"c01": held})
    published(cfg, "kick-fresh", (today - timedelta(days=1)).isoformat(), {"c01": held})
    published(cfg, "kick-baddate", "11/13/2026", {"c01": {"decision": "approved"}})   # no crash
    s = status.collect(cfg)
    assert [r["stream"] for r in s["needs_you"]["review"]] == ["kick-fresh"]
    assert {r["stream"]: r["held"] for r in s["review"]["streams"]}["kick-rejected"] == 0


def test_a_pinned_hold_or_a_show_without_stale_days_keeps_waiting(defaults, tmp_path):
    """The scheduler's rules, not the base stale_days alone (Copilot on #247): a pinned item
    never goes stale, and a show with stale_days = 0 never lets its items go."""
    cfg = in_tmp(defaults, tmp_path)
    cfg["schedule"]["stale_days"] = 3
    cfg["shows"] = {"c404": {"channels": ["donflurgundy"], "overrides": {"schedule": {"stale_days": 0}}}}
    cfg["_root"] = cfg
    old = (datetime.now(UTC).date() - timedelta(days=40)).isoformat()
    published(cfg, "kick-pinned", old, {"c01": {"decision": "approved",
                                                "fields": {"Hold": True, "Publish at": "2026-12-01 10:00"}}})
    published(cfg, "kick-c404", old, {"c01": {"decision": "approved", "fields": {"Hold": True}}},
              channel="donflurgundy")
    published(cfg, "kick-gone", old, {"c01": {"decision": "approved", "fields": {"Hold": True}}})
    s = status.collect(cfg)
    assert [r["stream"] for r in s["needs_you"]["review"]] == ["kick-c404", "kick-pinned"]


def test_status_json_keeps_stdout_for_the_document(defaults, tmp_path, monkeypatch, capsys):
    import json

    from clipper import cli

    cfg = in_tmp(defaults, tmp_path)
    real = status.collect

    def noisy(*a, **k):
        print("thumbnail text updated")   # what a sync can print
        return real(*a, **k)

    monkeypatch.setattr(status, "collect", noisy)
    cli.cmd_status(cfg, cli.build_parser().parse_args(["status", "--json"]))
    out = capsys.readouterr()
    assert json.loads(out.out)["recent_days"] == status.RECENT_DAYS and "thumbnail" in out.err


def test_status_json_is_plain_data(defaults, tmp_path):
    import json

    cfg = in_tmp(defaults, tmp_path)
    published(cfg, "kick-1", datetime.now(UTC).date().isoformat(), {"c01": {"decision": "pending"}})
    s = json.loads(json.dumps(status.collect(cfg), default=str))
    assert s["needs_you"]["review"][0]["stream"] == "kick-1" and s["needs_you"]["review"][0]["open"] == 1
    assert {f["check"] for f in s["needs_you"]["findings"]} <= set(status.CHECKS)


def test_worker_rows_carry_show_and_date_and_drop_old_finished_runs(defaults, tmp_path):
    from clipper import worker

    cfg = in_tmp(defaults, tmp_path)
    cfg["shows"] = {"kino": {"channels": ["kinocasinogaming"], "overrides": {}}}
    cfg["_root"] = cfg
    root = cfg["paths"]["streams"]
    long_ago = (datetime.now(UTC) - timedelta(days=20)).isoformat(timespec="seconds")
    for sid, run in (("kick-done", {"status": "done", "updated": long_ago}),
                     ("kick-failed", {"status": "failed", "updated": long_ago, "error": "cut: boom"})):
        (root / sid).mkdir(parents=True)
        jsonstate.write(root / sid / "run.json", run)
        jsonstate.write(root / sid / "meta.json", {"channel": "kinocasinogaming", "started_at": "2026-10-24T20:00:00Z"})
    for sid, last_seen, channel in (("kick-rec-old", 0, "someoneelse"),
                                    ("kick-rec-new", datetime.now(UTC).timestamp(), "someoneelse"),
                                    ("kick-rec-queued", 0, "kinocasinogaming")):
        (root / sid / "live").mkdir(parents=True)          # recorded only: no run.json
        jsonstate.write(root / sid / "live" / "state.json",
                        {"status": "done", "last_seen": last_seen, "channel": channel})
    rows = {r["stream"]: r for r in worker.stream_rows(cfg, recent_days=7)}
    # unfinished stays, however old: kick-rec-queued is still the worker's to take (Copilot on #247)
    assert set(rows) == {"kick-failed", "kick-rec-new", "kick-rec-queued"}
    line = worker.row_line(rows["kick-failed"])
    assert "kino" in line and "10-24" in line and "cut: boom" in line
    assert {r["stream"] for r in worker.stream_rows(cfg)} == {"kick-done", "kick-failed", "kick-rec-old",
                                                              "kick-rec-new", "kick-rec-queued"}
