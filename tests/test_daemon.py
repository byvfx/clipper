"""clipper/daemon.py: the heartbeat serve and the bot write, read from any machine."""
from datetime import UTC, datetime, timedelta

import pytest

from clipper import daemon
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
