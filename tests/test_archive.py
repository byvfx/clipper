"""`clipper archive add` of a YouTube channel: listed with approximate dates. Offline: yt-dlp
is a stand-in."""
import json

from clipper import archive


def test_youtube_listing_carries_approximate_dates(monkeypatch):
    calls = []

    class Done:
        returncode, stderr = 0, ""
        stdout = json.dumps({"channel": "Some Channel", "entries": [
            {"id": "abcdefghijk", "title": "new", "duration": 600, "timestamp": 1762819200},
            {"id": "bcdefghijkl", "title": "undated", "duration": 700}]})

    monkeypatch.setattr(archive.ytdl, "run", lambda *a: calls.append(a) or Done())
    items = archive.list_youtube("https://www.youtube.com/@x/videos")
    assert "youtubetab:approximate_date" in calls[0]
    assert [(i["id"], i["date"]) for i in items] == [("yt-abcdefghijk", "2025-11-11"), ("yt-bcdefghijkl", None)]
