"""Kick over HTTP for the recorder: the channel API (live status, playback URL, chatroom id),
playlists and segments, and the realtime service's auth."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("clipper.record")

CHANNEL_API = "https://kick.com/api/v2/channels/{slug}"
REALTIME_API = "https://web.kick.com/api/v1/realtime"


class Kick:
    def __init__(self, ua: str):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": ua, "Referer": "https://kick.com/",
                               "Origin": "https://kick.com"})

    def channel(self, slug: str) -> dict | None:
        """Channel info, or None if Kick didn't answer (unknown, not offline)."""
        try:
            r = self.s.get(CHANNEL_API.format(slug=slug), timeout=20,
                           headers={"Accept": "application/json"})
            if r.status_code == 200:
                return r.json()
            log.warning("%s: channel api %s", slug, r.status_code)
        except (requests.RequestException, ValueError) as e:
            log.warning("%s: channel api %s", slug, e)
        return None

    def text(self, url: str, timeout: float = 15) -> tuple[int, str]:
        r = self.s.get(url, timeout=timeout)
        return r.status_code, r.text

    def post(self, path: str, body: dict) -> dict:
        r = self.s.post(REALTIME_API + path, json=body, timeout=20,
                        headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()
