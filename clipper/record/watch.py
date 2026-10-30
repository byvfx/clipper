"""The watcher: poll [record] channels, one Recording per live channel, finalize after
end_grace_s with no restart. Writes a heartbeat to streams/.record/status.json every pass.

Live status: kick.com/api/v2/channels/<slug> (no credentials; also gives the playback
URL and chatroom id). The official API (api.kick.com/public/v1) needs an app token and
gives neither, and its webhooks need a public HTTPS endpoint, so it isn't used here."""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path
from typing import Any

import requests

from .. import jsonstate, kick
from ..alerts import Alerts
from .client import Kick
from .finalize import finalize
from .recording import Recording
from .state import STATE, State, _iso, _now

log = logging.getLogger("clipper.record")


def channel_list(rc: dict) -> list[dict]:
    """[record] channels: slugs, or tables {slug = "...", <record key> = override}."""
    raw = rc["channels"]
    if isinstance(raw, str):  # CLIPPER_RECORD_CHANNELS="a,b"
        raw = raw.replace(",", " ").split()
    out = []
    for c in raw:
        c = {"slug": c} if isinstance(c, str) else dict(c)
        if c.get("enabled", True):
            out.append({**{k: v for k, v in rc.items() if k != "channels"}, **c})
    return out


class Watcher:
    """Polls channels; one Recording per live channel; finalizes after end_grace_s."""

    def __init__(self, cfg: dict, channels: list[dict], max_s: float | None = None,
                 once: bool = False):
        self.cfg, self.channels, self.max_s, self.once = cfg, channels, max_s, once
        self.root: Path = cfg["paths"]["streams"]
        self.root.mkdir(parents=True, exist_ok=True)
        self.alert = Alerts(cfg)
        self.kick = Kick(cfg["kick"]["user_agent"])
        self.recs: dict[str, Recording] = {}      # slug -> active or paused recording
        self.paused_at: dict[str, float] = {}
        self.started_at: dict[str, float] = {}
        self.finalizing: list[threading.Thread] = []
        self.next_poll: dict[str, float] = {}
        self.adopt()

    def adopt(self) -> None:
        """Pick up recordings a previous run left unfinished."""
        slugs = {c["slug"] for c in self.channels}
        for f in sorted(self.root.glob(f"*/{STATE}")):
            try:
                st = State.load(f)
            except (ValueError, TypeError, KeyError) as e:
                log.error("unreadable %s: %s", f, e)
                continue
            d = f.parent.parent
            if st.status in ("done", "failed"):
                continue
            if st.status == "finalizing" or st.channel not in slugs or st.channel in self.recs:
                log.info("%s: unfinished (%s), finalizing", st.stream_id, st.status)
                self._finalize_async(d)
                continue
            c = next(c for c in self.channels if c["slug"] == st.channel)
            self.recs[st.channel] = Recording(d, st, self.cfg, self.alert, c)
            self.paused_at[st.channel] = st.last_seen or f.stat().st_mtime
            st.status = "paused"
            log.info("%s: adopted unfinished recording of %s", st.stream_id, st.channel)

    def _finalize_async(self, d: Path) -> None:
        def run():
            try:
                finalize(d, self.cfg, self.alert)
            except Exception as e:
                log.exception("finalize %s", d.name)
                self.alert(f"{d.name}: finalize failed: {e!r} (retry: clipper record "
                           f"finalize {d.name})", logging.ERROR)
                try:
                    st = State.load(d / STATE)
                    st.status = "failed"
                    st.save(d)
                except (OSError, ValueError):
                    pass
        t = threading.Thread(target=run, name=f"finalize-{d.name}")
        t.start()
        self.finalizing.append(t)

    @staticmethod
    def _taken(d: Path) -> bool:
        if (d / STATE).exists():
            return State.load(d / STATE).status in ("done", "failed", "finalizing")
        return (d / "meta.json").exists()   # e.g. a `clipper fetch` of the same stream

    def _new(self, c: dict, info: dict) -> Recording:
        ls = info["livestream"]
        sid = f"kick-{ls['id']}"
        d, n = self.root / sid, 1
        while self._taken(d):            # never record into a finished stream dir
            n += 1
            sid = f"kick-{ls['id']}-{n}"
            d = self.root / sid
        if (d / STATE).exists():
            st = State.load(d / STATE)   # same livestream, e.g. after a restart
        else:
            st = State(stream_id=sid, channel=c["slug"], channel_id=info["id"],
                       user_id=info["user_id"], chatroom_id=info["chatroom"]["id"],
                       sessions=[ls["id"]], title=ls.get("session_title") or "",
                       stream_started_at=_iso(kick._parse_ts(ls.get("start_time")
                                                             or ls["created_at"]).timestamp()),
                       max_height=int(c["max_height"]))
        rec = Recording(d, st, self.cfg, self.alert, c)
        self.alert(f"{c['slug']} is live: {st.title!r} -> {sid}", logging.INFO)
        return rec

    def tick(self) -> None:
        now = _now()
        for c in self.channels:
            slug = c["slug"]
            rec = self.recs.get(slug)
            active = rec is not None and rec.st.status == "recording"
            if active and rec.ended:
                rec.pause()
                self.paused_at[slug] = now
                active = False
            if active and self.max_s and now - self.started_at.get(slug, now) > self.max_s:
                log.info("%s: max duration reached", slug)
                rec.pause()
                self._finish(slug)
                continue
            interval = c["poll_s"] if not rec else min(c["poll_s"], 30)
            if now < self.next_poll.get(slug, 0):
                continue
            self.next_poll[slug] = now + interval
            info = self.kick.channel(slug)
            if info is None:
                continue  # unknown; try again next poll
            ls = info.get("livestream")
            live = bool(ls and ls.get("is_live", True))
            if live and rec is None:
                rec = self.recs[slug] = self._new(c, info)
                self.started_at[slug] = now
                rec.start()
            elif live and not active and ls["id"] not in rec.st.sessions and \
                    now - self.paused_at.get(slug, now) > c["end_grace_s"]:
                self._finish(slug)       # an old recording; this is a new stream
                self.next_poll[slug] = 0
            elif live and not active and rec.ended and ls["id"] in rec.st.sessions \
                    and not self._playable(info):
                pass  # the playlist ended; Kick's API says live for a minute or two longer
            elif live and not active:
                if ls["id"] not in rec.st.sessions:
                    rec.st.sessions.append(ls["id"])
                rec.st.title = ls.get("session_title") or rec.st.title
                log.info("%s: live again, resuming %s", slug, rec.st.stream_id)
                self.started_at.setdefault(slug, now)
                rec.start()
            elif live and active:
                if ls["id"] not in rec.st.sessions:
                    rec.st.sessions.append(ls["id"])  # restarted under our feet
                rec.st.title = ls.get("session_title") or rec.st.title
            elif not live and active and now - (rec.last_seen or self.started_at.get(slug, now)) > 20:
                log.info("%s: offline; waiting %d s for a restart", slug, c["end_grace_s"])
                rec.pause()
                self.paused_at[slug] = now
            elif not live and rec is not None and not active:
                if now - self.paused_at.get(slug, now) >= c["end_grace_s"]:
                    self._finish(slug)

    def _playable(self, info: dict) -> bool:
        try:
            return self.kick.text(info.get("playback_url") or "")[0] == 200
        except requests.RequestException:
            return False

    def _finish(self, slug: str) -> None:
        if self.once:   # `record now`: one recording per channel
            self.channels = [c for c in self.channels if c["slug"] != slug]
        rec = self.recs.pop(slug)
        self.paused_at.pop(slug, None)
        self.started_at.pop(slug, None)
        self._finalize_async(rec.d)

    def heartbeat(self) -> None:
        hb = self.root / ".record"
        hb.mkdir(exist_ok=True)
        jsonstate.write(hb / "status.json", {
            "at": _iso(_now()),
            "channels": {c["slug"]: (self.recs[c["slug"]].st.stream_id + " "
                                     + self.recs[c["slug"]].st.status
                                     if c["slug"] in self.recs else "offline")
                         for c in self.channels},
        })

    def run(self, until_done: bool = False) -> None:
        """Forever, or (until_done) until one recording has been made and finalized."""
        log.info("watching %s", ", ".join(c["slug"] for c in self.channels))
        recorded = False
        try:
            signal.signal(signal.SIGTERM, _sigterm)   # docker stop
        except ValueError:
            pass  # not the main thread
        try:
            while True:
                try:
                    self.tick()
                    self.heartbeat()
                except Exception as e:
                    log.exception("watcher tick")
                    self.alert(f"watcher error: {e!r}", logging.ERROR)
                self.finalizing = [t for t in self.finalizing if t.is_alive()]
                recorded = recorded or bool(self.recs)
                if until_done and recorded and not self.recs and not self.finalizing:
                    return
                time.sleep(2)
        except KeyboardInterrupt:
            log.info("stopping: pausing recordings (they resume or finalize on next start)")
            for rec in self.recs.values():
                if rec.st.status == "recording":
                    rec.pause()
            for t in self.finalizing:
                t.join()


def _sigterm(*_: Any) -> None:
    raise KeyboardInterrupt


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
