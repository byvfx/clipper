import json
from datetime import UTC, datetime

from clipper import jsonstate, youtube, ytquota

YC = {"daily_quota": 10000, "upload_units": 600, "max_per_24h": 15}
NOON = datetime(2026, 10, 30, 19, 0, tzinfo=UTC)  # 12:00 Pacific


def test_calls_are_counted_per_pacific_day(tmp_path):
    q = tmp_path / "quota.json"
    for m in ("youtube.captions.insert", "youtube.videos.list", "youtube.videos.insert",
              "youtube.playlistItems.insert", "youtubeAnalytics.reports.query"):
        ytquota.spend(q, m, NOON)
    u = ytquota.used(q, NOON)
    assert u["units"] == 451 and u["uploads"] == 1
    assert "youtubeAnalytics.reports.query" not in u["calls"]
    late = datetime(2026, 10, 31, 6, 59, tzinfo=UTC)   # 23:59 Pacific: still the 30th
    assert ytquota.used(q, late)["units"] == 451
    assert ytquota.used(q, datetime(2026, 10, 31, 7, 1, tzinfo=UTC))["units"] == 0


def test_spare_keeps_room_for_the_days_uploads(tmp_path):
    q = tmp_path / "quota.json"
    assert ytquota.spare(YC, q, NOON) == 10000 - 15 * 600
    jsonstate.write(q, {"2026-10-30": {"units": 6000, "uploads": 10, "calls": {}}})
    assert ytquota.spare(YC, q, NOON) == 10000 - 6000 - 5 * 600
    jsonstate.write(q, {"2026-10-30": {"units": 9800, "uploads": 15, "calls": {}}})
    assert ytquota.spare(YC, q, NOON) == 200


def test_old_days_are_dropped(tmp_path):
    q = tmp_path / "quota.json"
    jsonstate.write(q, {f"2026-09-{d:02d}": {"units": 1, "uploads": 0, "calls": {}} for d in range(1, 21)})
    ytquota.spend(q, "youtube.videos.list", NOON)
    assert len(jsonstate.load(q)) == ytquota.KEEP_DAYS and "2026-10-30" in jsonstate.load(q)


def test_a_real_client_counts_its_calls(tmp_path):
    """The method ids googleapiclient hands the request class are the ones COST knows."""
    from googleapiclient.discovery import build
    from googleapiclient.http import HttpMockSequence

    q = tmp_path / "quota.json"
    http = HttpMockSequence([({"status": "200"}, json.dumps({"items": []}))] * 2)
    yt = build("youtube", "v3", http=http, developerKey="x", static_discovery=True,
               requestBuilder=ytquota.request_class(q))
    yt.videos().list(part="status", id="a").execute()
    yt.captions().list(part="snippet", videoId="a").execute()
    assert ytquota.used(q)["calls"] == {"youtube.videos.list": 1, "youtube.captions.list": 1}
    assert ytquota.used(q)["units"] == 51


class Req:
    def __init__(self, fail):
        self.fail = fail

    def execute(self):
        if self.fail:
            raise RuntimeError("quotaExceeded")
        return {}


class YT:
    def __init__(self, fail):
        self.fail, self.calls = fail, 0

    def captions(self):
        return self

    def insert(self, **kw):
        self.calls += 1
        return Req(self.fail)


def test_failed_captions_are_noted_then_retried(tmp_path, defaults, monkeypatch):
    """#77 L18: a captions failure goes on the entry and the hourly run retries it."""
    srt = tmp_path / "v.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    sched_path = tmp_path / "schedule.json"
    e = {"key": "s/v01_t1", "stream": "s", "kind": "short", "status": "uploaded",
         "video_id": "abcdefghijk", "url": "https://youtu.be/abcdefghijk", "file": str(tmp_path / "v.mp4")}
    jsonstate.write(sched_path, {"entries": [dict(e)]})
    yc = defaults["youtube"]
    assert not youtube.add_captions(YT(True), yc, e, srt, sched_path)
    saved = jsonstate.load(sched_path)["entries"][0]
    assert saved["captions_tries"] == 1 and "quotaExceeded" in saved["captions_error"]

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "youtube_quota": tmp_path / "q.json"}}
    monkeypatch.setattr(youtube, "metadata", lambda e, cfg: {"srt": srt})
    yt = YT(False)
    youtube.backfill_captions(cfg, jsonstate.load(sched_path), sched_path, False, yt)
    saved = jsonstate.load(sched_path)["entries"][0]
    assert yt.calls == 1 and saved["captions_set"] and "captions_error" not in saved
    youtube.backfill_captions(cfg, jsonstate.load(sched_path), sched_path, False, yt)
    assert yt.calls == 1  # done: not sent again


def test_caption_retries_wait_for_spare_quota(tmp_path, defaults, monkeypatch):
    srt = tmp_path / "v.srt"
    srt.write_text("x", encoding="utf-8")
    sched_path = tmp_path / "schedule.json"
    jsonstate.write(sched_path, {"entries": [{"key": "s/v01_t1", "stream": "s", "status": "uploaded",
                                              "video_id": "abcdefghijk", "url": "u", "file": "v.mp4",
                                              "captions_error": "x", "captions_tries": 1}]})
    q = tmp_path / "q.json"
    jsonstate.write(q, {ytquota.today(): {"units": 9999, "uploads": 0, "calls": {}}})
    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "youtube_quota": q}}
    monkeypatch.setattr(youtube, "metadata", lambda e, cfg: {"srt": srt})
    yt = YT(False)
    youtube.backfill_captions(cfg, jsonstate.load(sched_path), sched_path, False, yt)
    assert yt.calls == 0
