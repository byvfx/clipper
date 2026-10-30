import json
from datetime import UTC, datetime, timedelta

import pytest

from clipper import services
from clipper.errors import ClipperError

NOW = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)


def cfg_with(tmp_path, beat: dict | None) -> dict:
    if beat is not None:
        (tmp_path / ".record").mkdir()
        (tmp_path / ".record" / "status.json").write_text(json.dumps(beat), encoding="utf-8")
    return {"paths": {"streams": tmp_path}, "cut": {"nas_host": ""}}


def beat(ago_s: float, **channels) -> dict:
    return {"at": (NOW - timedelta(seconds=ago_s)).isoformat(), "channels": channels}


def test_recorder_from_its_heartbeat(tmp_path):
    rec = services.recorder(cfg_with(tmp_path, beat(3, kino="kick-1 recording", don="offline")), NOW)
    assert rec["up"] and rec["live"] == ["kino"]
    assert "RECORDING kino" in services.recorder_lines(rec)[0]


def test_a_stale_heartbeat_is_down_and_not_live(tmp_path):
    """A recorder that died mid-show isn't recording: deploying it is what's needed."""
    rec = services.recorder(cfg_with(tmp_path, beat(600, kino="kick-1 recording")), NOW)
    assert not rec["up"] and rec["live"] == []
    assert "DOWN" in services.recorder_lines(rec)[0]


def test_no_heartbeat(tmp_path):
    rec = services.recorder(cfg_with(tmp_path, None), NOW)
    assert rec["age_s"] is None and not rec["live"]
    assert "no heartbeat" in services.recorder_lines(rec)[0]


def test_record_status_exits_3_while_live(tmp_path):
    """deploy/record/deploy.sh refuses on exit 3."""
    with pytest.raises(ClipperError) as e:
        services.record_status(cfg_with(tmp_path, beat(1, kino="kick-1 paused")), NOW)
    assert e.value.status == 3


def test_recorder_restart_refused_while_live(tmp_path, monkeypatch):
    cfg = cfg_with(tmp_path, {})
    monkeypatch.setattr(services, "recorder", lambda cfg: {"live": ["kino"]})
    called = []
    monkeypatch.setattr(services, "recorder_compose", lambda action: called.append(action) or 0)
    with pytest.raises(ClipperError, match="recording kino now"):
        services.run(cfg, "restart", "recorder")
    assert services.run(cfg, "restart", "recorder", force=True) == 0 and called == ["restart"]


def test_all_never_touches_the_recorder(tmp_path, monkeypatch):
    done = []
    monkeypatch.setattr(services, "run_task", lambda name, action: done.append((name, action)) or 0)
    monkeypatch.setattr(services, "recorder_compose", lambda action: pytest.fail("recorder touched"))
    services.run(cfg_with(tmp_path, {}), "stop", "all")
    assert done == [("worker", "stop"), ("bot", "stop")]
