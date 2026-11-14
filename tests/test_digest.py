"""The daily digest DM (clipper/digest.py), built from the war room's overview."""
from datetime import datetime
from zoneinfo import ZoneInfo

from clipper import digest, warroom


def test_digest_counts_today_tomorrow_the_queue_and_stuck_streams(defaults, monkeypatch):
    tz = ZoneInfo(defaults["schedule"]["timezone"])
    now = datetime(2026, 11, 8, 18, 0, tzinfo=tz)
    overview = {
        "schedule": [{"publish_at": "2026-11-08T10:00:00-08:00", "status": "uploaded", "show_name": "Kino", "title": "a"},
                     {"publish_at": "2026-11-08T22:00:00-08:00", "status": "scheduled", "show_name": "Kino", "title": "b"},
                     {"publish_at": "2026-11-09T09:00:00-08:00", "status": "scheduled", "show_name": "404",
                      "title": "tomorrow first"}],
        "counts": {"pending": 3, "changes": 1, "approved_unscheduled": 2},
        "streams": [{"id": "kick-1", "run_status": "failed"}, {"id": "kick-2", "run_status": "done"}]}
    monkeypatch.setattr(warroom, "overview", lambda cfg: overview)
    monkeypatch.setattr(digest, "related_lines", lambda cfg, now: ["Shorts to link: 2"])
    text = digest.text(defaults, now)
    assert text.startswith("Daily digest, Sun Nov 08")
    assert "Out today: 1 of 2 (Kino 1)" in text
    assert "Tomorrow: 1 scheduled, first 09:00: tomorrow first" in text
    assert "Waiting for review: 3 · changes asked: 1 · approved, no slot yet: 2" in text
    assert "Streams stuck on a failed stage: kick-1" in text and text.endswith("Shorts to link: 2")
