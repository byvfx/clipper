import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clipper import jsonstate, youtube
from clipper.errors import ClipperError

ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"


def test_sign_in_keeps_the_saved_tokens_extra_scopes(tmp_path):
    """Signing in again after a 7-day expiry mustn't drop the analytics grant."""
    token = tmp_path / "youtube_token.json"
    assert youtube.scopes(token, youtube.SCOPES) == youtube.SCOPES
    token.write_text(json.dumps({"scopes": [*youtube.SCOPES, ANALYTICS]}), encoding="utf-8")
    assert youtube.scopes(token, youtube.SCOPES) == [*youtube.SCOPES, ANALYTICS]
    token.write_text(json.dumps({"scopes": [ANALYTICS, youtube.SCOPES[0]]}), encoding="utf-8")
    assert youtube.scopes(token, youtube.SCOPES) == [*youtube.SCOPES, ANALYTICS]


# ---------- upload_due: what goes up this run (serve runs it hourly) ----------

NOW = datetime.now(UTC)


def entry(key: str, hours: float, status: str = "scheduled", **kw) -> dict:
    return {"key": key, "stream": "kick-1", "kind": "short", "id": key, "title": key, "file": "x.mp4",
            "publish_at": (NOW + timedelta(hours=hours)).isoformat(timespec="seconds"),
            "status": status, **kw}


def sched_with(tmp_path, entries: list[dict]) -> tuple[dict, object]:
    sched = {"timezone": "America/Los_Angeles", "entries": entries}
    path = tmp_path / "schedule.json"
    jsonstate.write(path, sched)
    return sched, path


@pytest.fixture
def upload_cfg(defaults, tmp_path):
    cfg = {**defaults, "youtube": {**defaults["youtube"], "upload_ahead_hours": 6, "min_lead_minutes": 20,
                                   "max_per_run": 3, "max_per_24h": 5}}
    cfg["paths"] = {**cfg["paths"], "streams": tmp_path / "streams",
                    "youtube_token": tmp_path / "youtube_token.json", "youtube_quota": tmp_path / "quota.json"}
    return cfg


def fake_uploads(monkeypatch, fail: dict | None = None) -> list[str]:
    """upload_one replaced: records the keys it was given, raises for the ones in `fail`."""
    done: list[str] = []

    def one(cfg, yt, e, sched, sched_path, sources, playlists, cache_path, tz):
        if fail and e["key"] in fail:
            raise fail[e["key"]]
        done.append(e["key"])
    monkeypatch.setattr(youtube, "upload_one", one)
    monkeypatch.setattr(youtube, "service", lambda paths, sign_in=False, creds=None: object())
    monkeypatch.setattr(youtube, "refresh_review", lambda cfg, entries: None)
    return done


def test_upload_due_takes_the_horizon_preshows_first_and_reports_missed_slots(upload_cfg, tmp_path, monkeypatch, capsys):
    sched, path = sched_with(tmp_path, [entry("a", 3), entry("late", 10), entry("pre", 8, source="asap"),
                                        entry("missed", 0.1), entry("up", 2, status="uploaded")])
    done = fake_uploads(monkeypatch)
    _, failed = youtube.upload_due(upload_cfg, sched, path, dry_run=False, limit=None)
    assert done == ["pre", "a"] and failed == []
    assert "missed slot" in capsys.readouterr().out


def test_upload_due_keeps_the_24h_cap(upload_cfg, tmp_path, monkeypatch):
    recent = (NOW - timedelta(hours=2)).isoformat(timespec="seconds")
    ups = [entry(f"u{i}", -i, status="uploaded", uploaded_at=recent) for i in range(1, 5)]
    sched, path = sched_with(tmp_path, [*ups, entry("a", 1), entry("b", 2)])
    done = fake_uploads(monkeypatch)
    youtube.upload_due(upload_cfg, sched, path, dry_run=False, limit=None)
    assert done == ["a"]  # 4 in the last 24 h, max 5: room for one


def test_a_failed_upload_is_noted_and_the_run_goes_on(upload_cfg, tmp_path, monkeypatch):
    sched, path = sched_with(tmp_path, [entry("a", 1), entry("b", 2)])
    done = fake_uploads(monkeypatch, {"a": ClipperError("no video file")})
    _, failed = youtube.upload_due(upload_cfg, sched, path, dry_run=False, limit=None)
    assert done == ["b"] and failed and failed[0].startswith("a: ClipperError: no video file")
    saved = {e["key"]: e for e in jsonstate.read(path)["entries"]}
    assert "no video file" in saved["a"]["last_error"] and "last_error" not in saved["b"]


def test_a_quota_refusal_stops_the_run(upload_cfg, tmp_path, monkeypatch):
    class Quota(Exception):
        reason = "quotaExceeded"
    sched, path = sched_with(tmp_path, [entry("a", 1), entry("b", 2)])
    done = fake_uploads(monkeypatch, {"a": Quota("quotaExceeded")})
    _, failed = youtube.upload_due(upload_cfg, sched, path, dry_run=False, limit=None)
    assert done == [] and len(failed) == 1  # b would fail the same way


# ---------- upload_one: the record of an upload ----------

def fake_video(monkeypatch, tmp_path, bodies: list):
    monkeypatch.setattr(youtube, "for_stream", lambda cfg, sdir: cfg)
    monkeypatch.setattr(youtube, "metadata", lambda e, cfg: {
        "title": "T", "description": "d", "tags": [], "srt": None, "thumb": None, "playlist": "", "masked": []})
    monkeypatch.setattr(youtube, "source_parents", lambda cfg, e, entries, sources: [])
    monkeypatch.setattr(youtube, "source_links", lambda cfg, e, parents, status: "")
    monkeypatch.setattr(youtube, "upload_video", lambda yt, p, body, notify: bodies.append(body) or "vid123")
    monkeypatch.setattr(youtube.ytquota, "uploaded", lambda p: None)
    video = tmp_path / "x.mp4"
    video.write_bytes(b"x")
    return str(video)


def test_upload_one_records_the_upload_before_the_extras(upload_cfg, tmp_path, monkeypatch):
    """A playlist or caption failure after the upload mustn't make the next run upload it twice."""
    sched, path = sched_with(tmp_path, [entry("kick-1/s01", 5, last_error="old", last_error_at="x")])
    e = sched["entries"][0]
    bodies: list = []
    e["file"] = fake_video(monkeypatch, tmp_path, bodies)

    def finish(*a):
        raise RuntimeError("playlist down")
    monkeypatch.setattr(youtube, "finish", finish)
    with pytest.raises(RuntimeError):
        youtube.upload_one(upload_cfg, object(), e, sched, path, {}, {}, tmp_path / "pl.json", ZoneInfo("UTC"))
    saved = jsonstate.read(path)["entries"][0]
    assert saved["status"] == "uploaded" and saved["video_id"] == "vid123" and saved["url"] == "https://youtu.be/vid123"
    assert "last_error" not in saved and "last_error_at" not in saved  # the retry that worked
    assert bodies[0]["status"]["privacyStatus"] == "private" and bodies[0]["status"]["publishAt"].endswith("Z")


@pytest.mark.parametrize("hours, manual, privacy, timed", [
    (5, False, "private", True),    # ahead of the slot: private with publishAt
    (0.1, False, "public", False),  # the slot is here: public now
    (5, True, "private", False),    # manual_publish: private, no publishAt
])
def test_upload_one_privacy_follows_the_slot(upload_cfg, tmp_path, monkeypatch, hours, manual, privacy, timed):
    cfg = {**upload_cfg, "youtube": {**upload_cfg["youtube"], "manual_publish": manual}}
    sched, path = sched_with(tmp_path, [entry("kick-1/s01", hours)])
    e = sched["entries"][0]
    bodies: list = []
    e["file"] = fake_video(monkeypatch, tmp_path, bodies)
    monkeypatch.setattr(youtube, "finish", lambda *a: None)
    youtube.upload_one(cfg, object(), e, sched, path, {}, {}, tmp_path / "pl.json", ZoneInfo("UTC"))
    assert bodies[0]["status"]["privacyStatus"] == privacy
    assert ("publishAt" in bodies[0]["status"]) == timed
