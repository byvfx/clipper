"""clipper/serve.py: the jobs table, its heartbeat, and the bot's client to it."""
import threading
from http.server import ThreadingHTTPServer

import pytest

from clipper import actions, serve, warroom
from clipper.errors import ClipperError


def cfg_in(defaults, tmp_path, **sections) -> dict:
    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    for name, values in sections.items():
        cfg[name] = {**cfg[name], **values}
    return cfg


def test_due_skips_jobs_that_are_off_or_ran_recently():
    jobs = [("upload", 3600, None, None), ("tiktok", 0, None, None), ("news", 600, None, None)]
    assert serve.due(jobs, {}, now=1000.0) == ["upload", "news"]
    assert serve.due(jobs, {"upload": 1000.0, "news": 500.0}, now=4000.0) == ["news"]
    assert serve.due(jobs, {"upload": 1000.0, "news": 3500.0}, now=4000.0) == []


def test_the_jobs_table_follows_the_config(defaults, tmp_path):
    cfg = cfg_in(defaults, tmp_path, youtube={"auto_upload_minutes": 60}, retake={"auto": True},
                 tiktok={"auto_send_minutes": 0})
    every = {name: every for name, every, *_ in serve.Serve(cfg).jobs()}
    assert set(every) == {"upload", "preshow", "retake", "tiktok", "x", "analytics", "cast", "news"}
    assert every["upload"] == 3600 and every["retake"] == serve.RETAKE_EVERY_S and every["tiktok"] == 0
    assert every["preshow"] == defaults["preshow"]["poll_minutes"] * 60


def test_status_says_when_each_job_runs_next(defaults, tmp_path, monkeypatch):
    cfg = cfg_in(defaults, tmp_path, youtube={"auto_upload_minutes": 60}, tiktok={"auto_send_minutes": 0})
    s = serve.Serve(cfg)
    monkeypatch.setattr(serve.time, "monotonic", lambda: 10_000.0)
    s.last["upload"] = 10_000.0 - 1800
    s.state["upload"] = {"running": False, "last_at": "2026-11-03T10:00:00+00:00", "last_exit": 0}
    st = s.status()
    assert st["upload"]["next_in_s"] == 1800 and st["upload"]["last_exit"] == 0
    assert st["cast"]["next_in_s"] == 0.0   # never ran: due on the next pass
    assert st["tiktok"]["next_in_s"] is None  # off


def test_url_is_the_war_room_here_unless_set(defaults):
    assert serve.url(defaults) == f"http://127.0.0.1:{defaults['warroom']['port']}/"
    assert serve.url({**defaults, "serve": {"url": "http://nas:8765"}}) == "http://nas:8765/"


def test_a_serve_that_is_down_is_one_clear_error(defaults):
    cfg = {**defaults, "serve": {"url": "http://127.0.0.1:1/"}}  # nothing listens on port 1
    with pytest.raises(ClipperError, match="isn't answering"):
        serve.replan(cfg)
    assert serve.tailnet_link(cfg) == ""


def test_a_decision_still_lands_when_serve_is_down(defaults):
    """The bot's replan goes through serve; without it the row comes back with a note."""
    def down(cfg):
        raise ClipperError("clipper serve isn't answering at http://127.0.0.1:8765/")
    row, note = actions.replan(defaults, None, "approve", {"short": "s01"}, via=down)
    assert row == {"short": "s01"} and "next hourly run" in note


def test_the_war_room_starts_retakes_on_serves_runner(defaults, tmp_path):
    calls = []
    warroom.HOOKS["retake"] = lambda: calls.append(1)
    try:
        assert warroom.start_retake(cfg_in(defaults, tmp_path)) and calls == [1]
    finally:
        warroom.HOOKS["retake"] = None


@pytest.fixture
def running(defaults, tmp_path):
    """A war room on a free port with a token, as serve runs it; the client cfg points at it."""
    cfg = cfg_in(defaults, tmp_path, warroom={"token": "k"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), warroom.make_handler(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg["serve"] = {"url": f"http://127.0.0.1:{srv.server_address[1]}/"}
    yield cfg
    srv.shutdown()


def test_the_client_passes_the_war_rooms_checks(running, monkeypatch):
    """Token in a header, no browser headers: the handler's same-origin and key checks let it in."""
    h = serve.health(running)
    assert h["services"]["serve"]["level"] == "bad"  # no heartbeat in this tmp folder
    ran = []
    monkeypatch.setattr(warroom.schedule, "run", lambda cfg, sync_first: ran.append(sync_first))
    assert serve.replan(running, sync=True) == {"ok": True} and ran == [True]
    with pytest.raises(ClipperError, match="HTTP 401"):
        serve.call({**running, "warroom": {**running["warroom"], "token": "wrong"}}, "/api/health")
