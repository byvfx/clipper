"""One recording: the video and chat threads for a stream directory.

Video: Kick serves live HLS from AWS IVS with 2 s MPEG-TS segments, each stamped with
#EXT-X-PROGRAM-DATE-TIME. Segments are fetched directly and appended to live/partNNN.ts;
a gap in that timeline (a dropped connection, a restart within the grace period) starts
a new part."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import requests

from ..alerts import Alerts
from . import chat, hls
from .client import Kick
from .hls import Segment, parse_media, pick_variant
from .state import Part, State, _iso, _now

log = logging.getLogger("clipper.record")


class Recording:
    """Video + chat threads for one stream directory. pause() stops both (stream went
    offline); resume() starts them again into a new part (restart within the grace)."""

    def __init__(self, d: Path, state: State, cfg: dict, alerts: Alerts, rc: dict):
        self.d, self.st, self.cfg, self.alert, self.rc = d, state, cfg, alerts, rc
        self.kick = Kick(cfg["kick"]["user_agent"])
        self.chat_http = Kick(cfg["kick"]["user_agent"])  # sessions aren't thread-safe
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.threads: list[threading.Thread] = []
        self.ended = False             # playlist said ENDLIST (or the video thread died: crashed)
        self.crashed = False
        self.new_part = True           # next segment opens a new part
        (d / "live").mkdir(parents=True, exist_ok=True)

    # -- state --
    def save(self) -> None:
        with self.lock:
            self.st.save(self.d)

    @property
    def last_seen(self) -> float:
        return self.st.last_seen

    def start(self) -> None:
        self.stop_evt.clear()
        self.ended = False
        self.crashed = False
        self.new_part = True
        self.st.status = "recording"
        self.save()
        self.threads = [threading.Thread(target=self._video, name=f"video-{self.st.stream_id}",
                                         daemon=True),
                        threading.Thread(target=self._chat, name=f"chat-{self.st.stream_id}",
                                         daemon=True)]
        for t in self.threads:
            t.start()

    def pause(self) -> None:
        self.stop_evt.set()
        for t in self.threads:
            t.join(timeout=30)
        self.threads = []
        self.st.status = "paused"
        self.save()

    # -- video --
    def _resolve(self) -> str:
        info = self.kick.channel(self.st.channel)
        if not info or not info.get("playback_url"):
            raise LookupError("no playback url")
        url = info["playback_url"]
        code, master = self.kick.text(url)
        if code != 200:
            raise LookupError(f"master playlist {code}")
        log.info("%s: resolving playback", self.st.stream_id)
        return pick_variant(master, url, self.st.max_height)

    def _video(self) -> None:  # noqa: C901
        media, fails, alerted, saved = None, 0, False, time.monotonic()
        began = _now()  # #77 M10: a stream that never sends a segment still counts as stalled
        resolved = time.monotonic()
        out = None
        # A segment that fails to download stays in the live playlist for a while: hold the
        # line there (later segments wait, so the part stays in order) and retry it on the
        # next playlist polls for up to segment_retry_s, with fresh signed URLs after a few
        # seconds, before writing past it as a gap.
        retry: dict[int, float] = {}  # sequence -> when it first failed
        try:
            while not self.stop_evt.is_set():
                try:
                    if media is None:
                        media = self._resolve()
                        resolved = time.monotonic()
                    code, text = self.kick.text(media)
                    if code != 200:
                        raise LookupError(f"media playlist {code}")
                    segs, endlist, target = parse_media(text, media)
                except (requests.RequestException, LookupError, ValueError) as e:
                    fails += 1
                    if fails >= 3:
                        media = None   # re-resolve: token expired or stream restarted
                    stalled = _now() - max(self.st.last_seen, began)
                    if stalled > self.rc["stall_alert_s"] and not alerted:
                        self.alert(f"{self.st.stream_id}: no video for {stalled:.0f} s ({e})")
                        alerted = True
                    self.stop_evt.wait(min(2 * fails, 15))
                    continue
                fails = 0
                retry_soon = False
                for s in segs:
                    if self._have(s):
                        continue
                    if self.stop_evt.is_set():
                        break
                    data = self._download(s.url)
                    if data is None:
                        first = retry.setdefault(s.seq, time.monotonic())
                        waited = time.monotonic() - first
                        if waited < self.rc["segment_retry_s"]:
                            log.info("%s: segment %d failed, retrying (%.0f s)",
                                     self.st.stream_id, s.seq, waited)
                            if waited > 6:
                                media = None  # signed segment URLs may have expired
                            retry_soon = True
                            break
                        log.warning("%s: segment %d lost after %.0f s of retries",
                                    self.st.stream_id, s.seq, waited)
                        continue  # lost; the next segment's sequence shows the gap
                    if s.seq in retry:
                        log.info("%s: segment %d recovered after %.0f s", self.st.stream_id,
                                 s.seq, time.monotonic() - retry[s.seq])
                    retry = {q: t for q, t in retry.items() if q > s.seq}
                    missing, gap = hls.place(s, self.st.last_seq, self.st.last_end)
                    if self.new_part or missing or s.disc:
                        if out:
                            out.close()
                        out = self._open_part(s, gap, missing)
                    out.write(data)
                    out.flush()
                    with self.lock:
                        p = self.st.parts[-1]
                        p.duration_s = round(p.duration_s + s.dur, 3)
                        p.segments += 1
                        p.bytes += len(data)
                        self.st.titles[s.title or "-"] = self.st.titles.get(s.title or "-", 0) + 1
                        self.st.last_seen = _now()
                        self.st.last_seq = s.seq
                        self.st.last_end = s.pdt + s.dur
                    if alerted:
                        self.alert(f"{self.st.stream_id}: video back", logging.INFO)
                        alerted = False
                # #77 M10: the playlist answers but nothing new is written (it stopped moving,
                # or every segment fails): alert too, and resolve the playback again, at most
                # once per stall_alert_s.
                stalled = _now() - max(self.st.last_seen, began)
                if stalled > self.rc["stall_alert_s"] and not endlist:
                    if not alerted:
                        self.alert(f"{self.st.stream_id}: no video for {stalled:.0f} s "
                                   "(the playlist has no new segments)")
                        alerted = True
                    if time.monotonic() - resolved > self.rc["stall_alert_s"]:
                        media = None
                if time.monotonic() - saved > 10:
                    self.save()
                    saved = time.monotonic()
                if endlist:
                    log.info("%s: playlist ended", self.st.stream_id)
                    self.ended = True
                    break
                self.stop_evt.wait(1.0 if retry_soon else max(1.0, target / 2))
        except Exception as e:
            log.exception("%s: video thread crashed", self.st.stream_id)
            self.alert(f"{self.st.stream_id}: video thread crashed: {e!r}", logging.ERROR)
            self.crashed = True
            self.ended = True
        finally:
            if out:
                out.close()
            self.save()

    def _download(self, url: str) -> bytes | None:
        for i in range(3):
            try:
                r = self.kick.s.get(url, timeout=20)
                if r.status_code == 200 and r.content:
                    return r.content
            except requests.RequestException:
                pass
            if self.stop_evt.wait(0.5 * (i + 1)):
                break
        return None

    def _have(self, s: Segment) -> bool:
        return hls.have(s, self.st.last_seq, self.st.last_end)

    def _open_part(self, s: Segment, gap: float, missing: int):
        with self.lock:
            if self.st.parts and (missing or gap > 0.5):
                g = {"at_wall": _iso(self.st.last_end), "missing_s": round(gap, 2),
                     "missing_segments": missing}   # -1: the broadcast restarted
                self.st.gaps.append(g)
                self.alert(f"{self.st.stream_id}: video gap of {gap:.0f} s"
                           + (f" ({missing} segment(s) lost)" if missing > 0 else
                              " (stream restarted)" if missing < 0 else "")
                           + f"; part {len(self.st.parts) + 1} starts")
            name = f"live/part{len(self.st.parts) + 1:03d}.ts"
            self.st.parts.append(Part(file=name, wall_start=round(s.pdt, 3)))
            self.new_part = False
        self.save()
        return open(self.d / name, "ab")

    # -- chat --
    def _chat(self) -> None:
        chat.listen(self)
