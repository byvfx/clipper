"""clipper/daemon.py: the heartbeat serve and the bot write, read from any machine."""
from datetime import UTC, datetime, timedelta

import pytest

from clipper import daemon, jsonstate
from clipper.errors import ClipperError


def cfg_at(tmp_path) -> dict:
    return {"paths": {"streams": tmp_path}}


def test_heartbeat_round_trip(tmp_path):
    cfg = cfg_at(tmp_path)
    daemon.beat(cfg, "serve", "2026-11-03T10:00:00+00:00",
                jobs={"upload": {"running": True, "started": "2026-11-03T10:05:00+00:00"}})
    hb = daemon.heartbeat(cfg, "serve")
    assert hb["up"] and hb["age_s"] < 5 and hb["data"]["jobs"]["upload"]["running"]
    assert daemon.state(hb)[0] == "ok"
    lines = daemon.heartbeat_lines("serve", hb)
    started = datetime.fromisoformat("2026-11-03T10:05:00+00:00").astimezone().strftime("%H:%M")  # this machine's time
    assert lines[0].startswith("serve: up on") and f"upload: running since {started}" in lines[1]


def test_a_stale_heartbeat_is_down(tmp_path):
    """A lock file would still be there; the heartbeat says the process stopped writing."""
    cfg = cfg_at(tmp_path)
    daemon.beat(cfg, "bot", "2026-11-03T10:00:00+00:00")
    later = datetime.now(UTC) + timedelta(seconds=daemon.HEARTBEAT_S + 1)
    hb = daemon.heartbeat(cfg, "bot", later)
    assert not hb["up"]
    assert daemon.state(hb) == ("bad", "down, last seen 3 min ago")
    assert "DOWN" in daemon.heartbeat_lines("bot", hb)[0]


def test_no_heartbeat(tmp_path):
    hb = daemon.heartbeat(cfg_at(tmp_path), "serve")
    assert hb["age_s"] is None and daemon.state(hb)[0] == "bad"
    assert "no heartbeat" in daemon.heartbeat_lines("serve", hb)[0]


def test_single_instance_refuses_a_second(tmp_path):
    cfg = cfg_at(tmp_path)
    first = daemon.single_instance(cfg, "serve")
    with pytest.raises(ClipperError, match="another clipper serve"):
        daemon.single_instance(cfg, "serve")
    first.close()
    daemon.single_instance(cfg, "serve").close()  # free again once the holder is gone


def test_a_heartbeat_missed_for_one_read_is_read_again(tmp_path, monkeypatch):
    """The writer replaces the file every few seconds; a read over the share can land in the gap
    (2026-11-08: `clipper record status` said "no heartbeat" mid-show once)."""
    (tmp_path / ".serve").mkdir()
    path = tmp_path / ".serve" / "status.json"
    jsonstate.write(path, {"at": "x"})
    real, misses = jsonstate.read, [None]            # the first read finds no file
    monkeypatch.setattr(daemon.jsonstate, "read", lambda p, d=None: misses.pop() if misses else real(p, d))
    slept = []
    monkeypatch.setattr(daemon.time, "sleep", slept.append)
    assert daemon.read_heartbeat(path) == {"at": "x"} and slept == [0.3]


def test_no_heartbeat_is_read_again_only_where_the_service_has_run(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(daemon.time, "sleep", slept.append)
    assert daemon.read_heartbeat(tmp_path / ".bot" / "status.json") == {} and slept == []   # never ran: no folder
    (tmp_path / ".serve").mkdir()
    assert daemon.read_heartbeat(tmp_path / ".serve" / "status.json") == {} and slept == [0.3, 0.3]
    (tmp_path / ".serve" / "status.json").write_text("not json", encoding="utf-8")
    assert daemon.read_heartbeat(tmp_path / ".serve" / "status.json") == {} and slept == [0.3] * 4
