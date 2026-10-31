"""YouTube Data API quota, counted per day (#77 M12).

A Cloud project gets 10,000 units a day for every call except uploads (videos.insert has
its own bucket of 100 a day since 2026-06), reset at midnight Pacific. Every request made
through a client from youtube.service() is counted in [paths] youtube_quota by what it
costs. Uploads come first: the hourly backfills (source links, captions) only spend what
is left above the units the day's remaining uploads will need ([youtube] daily_quota,
upload_units). An upload's thumbnail, captions and playlist adds are ~550 units.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonstate

PACIFIC = ZoneInfo("America/Los_Angeles")  # when Google resets the quota
KEEP_DAYS = 14
# https://developers.google.com/youtube/v3/determine_quota_cost; anything not listed is
# counted as a write (50), or a read (1) if its name ends in .list.
COST = {
    "youtube.videos.insert": 0,  # its own bucket, counted in "uploads"
    "youtube.videos.update": 50,
    "youtube.thumbnails.set": 50,
    "youtube.captions.insert": 400,
    "youtube.captions.update": 450,
    "youtube.captions.list": 50,
    "youtube.playlists.insert": 50,
    "youtube.playlistItems.insert": 50,
}


def cost(method: str) -> int:
    if method in COST:
        return COST[method]
    return 1 if method.endswith(".list") else 50


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(PACIFIC)).astimezone(PACIFIC).date().isoformat()


def spend(path: Path, method: str, now: datetime | None = None) -> None:
    """Count one call. Never raises: a count lost to a full disk shouldn't stop an upload."""
    if not method.startswith("youtube."):  # youtubeAnalytics has its own quota
        return
    day = today(now)
    try:
        with jsonstate.update(path, {}) as data:
            d = data.setdefault(day, {"units": 0, "uploads": 0, "calls": {}})
            d["units"] += cost(method)
            d["uploads"] += method == "youtube.videos.insert"
            d["calls"][method] = d["calls"].get(method, 0) + 1
            for old in sorted(data)[:-KEEP_DAYS]:
                del data[old]
    except Exception as e:  # noqa: BLE001
        logging.getLogger("clipper.ytquota").warning("quota count not written: %s", e)


def used(path: Path, now: datetime | None = None) -> dict:
    """Today's (Pacific) count: {"units", "uploads", "calls"}."""
    return jsonstate.read(path, {}).get(today(now)) or {"units": 0, "uploads": 0, "calls": {}}


def spare(yc: dict, path: Path, now: datetime | None = None) -> int:
    """Units the backfills may still spend today: what's left of daily_quota after
    upload_units for each upload max_per_24h still allows today."""
    u = used(path, now)
    reserve = max(0, yc["max_per_24h"] - u["uploads"]) * yc["upload_units"]
    return max(0, yc["daily_quota"] - u["units"] - reserve)


def summary(yc: dict, path: Path) -> str:
    u = used(path)
    return f"YouTube quota today: {u['units']:,} of {yc['daily_quota']:,} units, {u['uploads']} uploads"


def request_class(path: Path):
    """An HttpRequest that counts itself in path once, when it's first sent (a retried
    request is counted again: Google charges failed calls too)."""
    from googleapiclient.http import HttpRequest

    class Counted(HttpRequest):
        def execute(self, http=None, num_retries=0):
            if not self.resumable:  # an upload's execute() goes through next_chunk()
                spend(path, self.methodId or "")
            return super().execute(http=http, num_retries=num_retries)

        def next_chunk(self, http=None, num_retries=0):
            if self.resumable_uri is None:  # the first chunk starts the upload
                spend(path, self.methodId or "")
            return super().next_chunk(http=http, num_retries=num_retries)

    return Counted
