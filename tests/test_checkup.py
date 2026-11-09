"""`clipper checkup` (#185): the problems alerts miss, found from the files alone."""
from datetime import UTC, datetime, timedelta

from clipper import checkup, cli, daemon, jsonstate

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)


def iso(delta: timedelta) -> str:
    return (NOW + delta).isoformat(timespec="seconds")


def in_tmp(cfg: dict, tmp_path) -> dict:
    cfg["paths"]["streams"] = tmp_path / "streams"
    cfg["paths"]["schedule"] = tmp_path / "schedule.json"
    cfg["paths"]["streams"].mkdir()
    return cfg


def stream(cfg: dict, name: str, run: dict | None = None, files: dict | None = None) -> object:
    d = cfg["paths"]["streams"] / name
    d.mkdir()
    if run is not None:
        jsonstate.write(d / "run.json", run)
    for rel, data in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        jsonstate.write(p, data)
    return d


def keys(found: list[dict]) -> set[str]:
    return {f["key"] for f in found}


def test_services_without_heartbeats_and_with_fresh_ones(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    found = checkup.check_services(cfg, NOW)
    assert keys(found) == {"service:serve", "service:bot", "service:recorder"}
    assert "none arrive" in next(f["text"] for f in found if f["key"] == "service:bot")
    for name in ("serve", "bot", "record"):
        p = cfg["paths"]["streams"] / f".{name}" / "status.json"
        p.parent.mkdir()
        jsonstate.write(p, {"at": iso(-timedelta(seconds=30)), "host": "nas"})
    assert checkup.check_services(cfg, NOW) == []
    jsonstate.write(daemon.heartbeat_path(cfg, "serve"), {"at": iso(-timedelta(minutes=20))})
    assert keys(checkup.check_services(cfg, NOW)) == {"service:serve"}


def test_a_stage_running_too_long_is_stuck_and_a_silent_worker_is_an_orphan(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    beat = iso(-timedelta(minutes=1))
    stream(cfg, "kick-1", {"status": "running", "heartbeat": beat, "stages": {
        "score": {"status": "done"}, "cut": {"status": "running", "started": iso(-timedelta(hours=7)),
                                              "log": "run_cut.log"}}})
    stream(cfg, "kick-2", {"status": "running", "heartbeat": beat, "stages": {
        "cut": {"status": "running", "started": iso(-timedelta(hours=2))}}})
    stream(cfg, "kick-3", {"status": "running", "heartbeat": iso(-timedelta(hours=1)), "stages": {}})
    found = checkup.check_streams(cfg, NOW)
    assert keys(found) == {"stuck:kick-1:cut", "orphan:kick-3"}
    stuck = next(f for f in found if f["key"] == "stuck:kick-1:cut")
    assert "7.0 h" in stuck["text"] and stuck["look"] == "streams/kick-1/run_cut.log"


def test_failed_with_no_retries_left(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    stream(cfg, "kick-1", {"status": "failed", "attempts": 3, "error": "segments exited 1: boom",
                           "updated": iso(-timedelta(hours=1))})
    stream(cfg, "kick-2", {"status": "failed", "attempts": 1, "updated": iso(-timedelta(minutes=5))})
    found = checkup.check_streams(cfg, NOW)
    assert keys(found) == {"failed:kick-1"} and "boom" in found[0]["text"]


def test_a_finished_recording_nobody_picked_up(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    # worker.pending compares against the real clock, so the recording ends relative to it
    real = datetime.now(UTC)
    ended = (real - timedelta(hours=5)).isoformat(timespec="seconds")
    stream(cfg, "kick-1", None, {"live/state.json": {"status": "done", "channel": "kino"},
                                  "meta.json": {"ended_at": ended}})
    found = checkup.check_streams(cfg, real)
    assert keys(found) == {"queued:kick-1"} and "not started" in found[0]["text"]
    # a stream the worker is busy with: the queue behind it is expected
    stream(cfg, "kick-2", {"status": "running", "heartbeat": real.isoformat(timespec="seconds"),
                           "stages": {"cut": {"status": "running", "started": real.isoformat()}}})
    assert checkup.check_streams(cfg, real) == []


def test_schedule_late_uploads_and_gaps(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    e = lambda key, delta, status="scheduled": {"key": key, "stream": key.split("/")[0], "kind": "segment",
                                                "title": key, "publish_at": iso(delta), "status": status}
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        e("kick-1/s01", timedelta(minutes=30)), e("kick-1/s02", -timedelta(hours=2)),
        e("kick-1/s03", timedelta(minutes=10), "uploaded"), e("kick-1/s04", timedelta(hours=5))]})
    found = checkup.check_schedule(cfg, NOW)
    assert keys(found) == {"late:kick-1/s01", "late:kick-1/s02"}
    assert "releases in 30 min" in found[0]["text"] and "was due 2.0 h ago" in found[1]["text"]

    jsonstate.write(cfg["paths"]["schedule"], {"entries": [e("kick-1/s05", timedelta(days=3))]})
    found = checkup.check_schedule(cfg, NOW)
    assert keys(found) == {"schedule:gap"} and found[0]["level"] == checkup.WARN
    cfg["checkup"]["gap_hours"] = 0
    assert checkup.check_schedule(cfg, NOW) == []


def test_lint_text_matches_whole_words_only():
    words = ["hosts", "co-host", "co-hosts"]
    assert checkup.lint_text(words, {"title": "The Hosts lose it"}) == ["title says 'Hosts'"]
    assert checkup.lint_text(words, {"t": "Andy's co-host quits"}) == ["t says 'co-host'"]
    assert checkup.lint_text(words, {"t": "Ghosts, a host, hostess, co-hosted"}) == []
    assert checkup.lint_text([], {"t": "hosts"}) == []


def test_lint_reads_what_upload_would_send(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    stream(cfg, "kick-1", None, {"review.json": {
        "s01_x": {"fields": {"YouTube title": "The hosts lose it", "YouTube description": "",
                             "Thumbnail text": "CO-HOST QUITS"}},
        "s02_y": {"fields": {"YouTube title": "Fine " * 30, "YouTube description": "All good here."}},
        "s03_z": {"fields": {"YouTube title": "Fine", "YouTube description": "All good here."}}}})
    entry = lambda k, status="scheduled": {"key": f"kick-1/{k}", "stream": "kick-1", "kind": "segment", "title": k,
                                           "file": f"kick-1/segments/{k}.mp4", "status": status,
                                           "publish_at": iso(timedelta(hours=5))}
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        entry("s01_x"), entry("s02_y"), entry("s03_z"), {**entry("s01_x"), "key": "kick-1/s01_x", "status": "uploaded"}]})
    found = {f["key"]: f["text"] for f in checkup.check_lint(cfg, NOW)}
    assert set(found) == {"lint:kick-1/s01_x", "lint:kick-1/s02_y"}
    one = found["lint:kick-1/s01_x"]
    assert "title says 'hosts'" in one and "thumbnail text says 'CO-HOST'" in one and "no description" in one
    assert "title is over YouTube's limit" in found["lint:kick-1/s02_y"]


def test_mark_remembers_first_seen_and_forgets_what_cleared(defaults, tmp_path, monkeypatch):
    cfg = in_tmp(defaults, tmp_path)
    now = {"found": [checkup.finding("a:1", "streams", checkup.BAD, "one"),
                     checkup.finding("b:2", "streams", checkup.BAD, "two")]}
    monkeypatch.setattr(checkup, "CHECKS", {"streams": lambda c, t: list(map(dict, now["found"])),
                                            "lint": lambda c, t: [checkup.finding("l:1", "lint", "warn", "x")]})
    first = checkup.run(cfg, mark=True, now=NOW)
    assert all(f["new"] for f in first["findings"])
    now["found"] = now["found"][:1]
    later = NOW + timedelta(hours=1)
    second = checkup.run(cfg, only=["streams"], mark=True, now=later)
    assert [(f["key"], f["new"], f["first_seen"]) for f in second["findings"]] == [("a:1", False, first["at"])]
    seen = jsonstate.load(cfg["paths"]["streams"] / ".checkup" / "state.json")["seen"]
    assert set(seen) == {"a:1", "l:1"}   # b:2 cleared; lint wasn't run, so its finding stays
    now["found"].append(checkup.finding("b:2", "streams", checkup.BAD, "two"))
    assert [f["new"] for f in checkup.run(cfg, only=["streams"], now=later)["findings"]] == [False, True]


def test_a_check_that_crashes_is_a_finding_and_the_rest_still_run(defaults, tmp_path, monkeypatch):
    cfg = in_tmp(defaults, tmp_path)

    def boom(c, t):
        raise RuntimeError("broken")

    monkeypatch.setattr(checkup, "CHECKS", {"lint": boom, "schedule": checkup.check_schedule})
    cfg["checkup"]["gap_hours"] = 0
    report = checkup.run(cfg, now=NOW)
    assert [f["key"] for f in report["findings"]] == ["check:lint"]
    assert "RuntimeError: broken" in report["findings"][0]["text"]
    text = "\n".join(checkup.lines(report, checkup.ZoneInfo("UTC")))
    assert "1 finding" in text and "BAD  lint" in text


def test_lines_when_all_is_good():
    report = {"at": NOW.isoformat(), "checks": ["services", "lint"], "findings": []}
    assert checkup.lines(report, checkup.ZoneInfo("UTC")) == ["checkup 11-09 20:00: all good (services, lint)"]


def test_alert_send_queues_a_checkup_dm(defaults, tmp_path, capsys):
    from clipper import alerts

    cfg = in_tmp(defaults, tmp_path)
    args = cli.build_parser().parse_args(["alert", "send", "Checkup: kick-1 is stuck in cut"])
    cli.cmd_alert(cfg, args)
    (f,) = alerts.outbox(cfg).glob("*.json")
    ev = jsonstate.load(f)
    assert ev["kind"] == "checkup" and ev["text"] == "Checkup: kick-1 is stuck in cut"
    assert "queued; the bot DMs it" in capsys.readouterr().out


def test_a_streams_own_config_can_allow_a_longer_stage(defaults, tmp_path):
    cfg = in_tmp(defaults, tmp_path)
    d = stream(cfg, "kick-1", {"status": "running", "heartbeat": iso(-timedelta(minutes=1)), "stages": {
        "cut": {"status": "running", "started": iso(-timedelta(hours=7))}}})
    (d / "config.toml").write_text("[checkup]\nstuck_hours = 12\n", encoding="utf-8")
    assert checkup.check_streams(cfg, NOW) == []


def test_alert_send_reads_cut_utf8_from_stdin_and_trims_to_discord(defaults, tmp_path, monkeypatch, capsys):
    import io
    import sys

    from clipper import alerts

    cfg = in_tmp(defaults, tmp_path)
    raw = ("é" * 1950).encode() + "—".encode()[:2]   # over 1900 characters, cut mid-character
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    cli.cmd_alert(cfg, cli.build_parser().parse_args(["alert", "send", "-"]))
    (f,) = alerts.outbox(cfg).glob("*.json")
    text = jsonstate.load(f)["text"]
    assert text.startswith("é" * 10) and len(text) <= 1900
    assert "rest is cut" in capsys.readouterr().err
