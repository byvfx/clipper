"""Live recorder: watch Kick channels, record each live stream with real-time chat.

`clipper record watch` is the long-lived service (Docker on the NAS): it polls each
channel in [record] channels; when one goes live it records the HLS stream and the chat
into streams/kick-<livestream id>/, and when the stream ends (after end_grace_s with no
restart) it finalizes the directory into what `clipper fetch` produces:

    source.mkv   the recorded parts joined by stream copy (no re-encode)
    chat.jsonl   same rows as kick.fetch_chat: id, ts (server time), user, type, content, t
    meta.json    same keys as kick.resolve, plus "recording" (parts, gaps, chat coverage)
    audio.wav    16 kHz mono, as the transcribe stage writes it, so `clipper signals` runs

Video: Kick serves live HLS from AWS IVS with 2 s MPEG-TS segments, each stamped with
#EXT-X-PROGRAM-DATE-TIME. Segments are fetched directly and appended to live/partNNN.ts;
a gap in that timeline (a dropped connection, a restart within the grace period) starts
a new part. Video t=0 is the first recorded segment's program date-time, so for a
recording without gaps chat t = ts - started_at exactly, as for a fetched VOD; with gaps
the joined source is shorter than the wall-clock span and chat t is mapped part by part.

Chat: Kick's realtime service (Centrifugo since 2026; the old public Pusher key is
gone). A guest token from web.kick.com/api/v1/realtime/auth/connection, refreshed before
it expires, and a subscription to chatrooms.<chatroom id>.v2. Kick's created_at (whole
seconds) is kept as ts; our receive time goes in recv. Periods the socket was down are
backfilled from the chat-history API at finalize.

Live status: kick.com/api/v2/channels/<slug> (no credentials; also gives the playback
URL and chatroom id). The official API (api.kick.com/public/v1) needs an app token and
gives neither, and its webhooks need a public HTTPS endpoint, so it isn't used here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from . import jsonstate, kick
from .errors import ClipperError

log = logging.getLogger("clipper.record")

CHANNEL_API = "https://kick.com/api/v2/channels/{slug}"
REALTIME_API = "https://web.kick.com/api/v1/realtime"
DEFAULT_CHAT_WS = "wss://realtime.us-east-1.platform.kick.com/connection/websocket"
STATE = "live/state.json"
LIVE_CHAT = "live/chat.jsonl"


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


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


# ---------- alerts ----------

class Alerts:
    """Log every alert; also post to Discord through a webhook URL (env var named by
    alert_webhook_env) or the review bot's token into alert_channel_id."""

    def __init__(self, cfg: dict):
        rc = cfg["record"]
        self.webhook = os.environ.get(rc["alert_webhook_env"], "").strip()
        self.channel = str(rc["alert_channel_id"] or "")
        self.cfg = cfg
        self.mention = rc["alert_mention"]

    def __call__(self, text: str, level: int = logging.WARNING, kind: str | None = "auto",
                 key: str = "") -> None:
        log.log(level, "ALERT %s", text)
        if kind == "auto":  # the recorder's: warnings are DMs (#22), news like "is live" isn't
            kind = "recorder" if level >= logging.WARNING else None
        if kind:
            from . import alerts

            alerts.send(self.cfg, kind, text, key)
        body = f"{self.mention} {text}".strip() if level >= logging.WARNING else text
        try:
            if self.webhook:
                requests.post(self.webhook, json={"content": body[:1900]}, timeout=20)
            elif self.channel:
                from . import notify

                notify.Rest(self.cfg)("POST", f"/channels/{self.channel}/messages",
                                      {"content": body[:1900]})
        except (requests.RequestException, ClipperError) as e:
            log.error("alert not delivered: %s", e)


# ---------- Kick HTTP ----------

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


def pick_variant(master: str, base: str, max_height: int) -> str:
    """Highest variant at or below max_height (0 = best), by height then bandwidth."""
    best, best_key = None, None
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        url = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
        res = re.search(r"RESOLUTION=\d+x(\d+)", line)
        bw = re.search(r"BANDWIDTH=(\d+)", line)
        if not url or not res:
            continue
        h = int(res.group(1))
        key = (h, int(bw.group(1)) if bw else 0)
        if (max_height <= 0 or h <= max_height) and (best_key is None or key > best_key):
            best, best_key = requests.compat.urljoin(base, url), key
    if not best:
        raise LookupError("no video variant in master playlist")
    log.info("variant %dp, %.1f Mb/s", best_key[0], best_key[1] / 1e6)
    return best


@dataclass
class Segment:
    seq: int        # media sequence number: the segment's identity
    pdt: float      # program date-time, epoch s (IVS ingest clock: jitters +-2 s
                    # against the media durations, so it only anchors parts)
    dur: float
    url: str
    title: str
    disc: bool      # EXT-X-DISCONTINUITY before it


def parse_media(text: str, base: str) -> tuple[list[Segment], bool, float]:
    segs, pdt, dur, title, disc = [], None, None, "", False
    target, seq = 2.0, 0
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pdt = kick._parse_ts(line.split(":", 1)[1].strip()).timestamp()
        elif line.startswith("#EXTINF:"):
            d, _, title = line[8:].partition(",")
            dur = float(d)
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            disc = True
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = float(line.split(":", 1)[1])
        elif line and not line.startswith("#") and dur is not None:
            if pdt is not None:
                segs.append(Segment(seq, pdt, dur, requests.compat.urljoin(base, line), title,
                                    disc))
                pdt += dur  # until the next PROGRAM-DATE-TIME tag says otherwise
            seq += 1
            dur, title, disc = None, "", False
    return segs, "#EXT-X-ENDLIST" in text, target


# ---------- one recording ----------

@dataclass
class Part:
    file: str
    wall_start: float
    duration_s: float = 0.0
    segments: int = 0
    bytes: int = 0


@dataclass
class State:
    stream_id: str
    channel: str
    channel_id: int
    user_id: int
    chatroom_id: int
    sessions: list[int]
    title: str
    stream_started_at: str
    max_height: int
    status: str = "recording"          # recording | paused | finalizing | done | failed
    parts: list[Part] = field(default_factory=list)
    chat: list[list[float]] = field(default_factory=list)   # [from, to] socket coverage
    gaps: list[dict] = field(default_factory=list)
    titles: dict[str, int] = field(default_factory=dict)    # EXTINF titles seen
    last_seen: float = 0.0             # last time a new segment arrived
    last_end: float = 0.0              # program date-time where the last segment ended
    last_seq: int = -1                 # media sequence of the last segment written
    variant: str = ""

    @classmethod
    def load(cls, path: Path) -> State:
        d = jsonstate.load(path)
        d["parts"] = [Part(**p) for p in d["parts"]]
        return cls(**d)

    def dump(self) -> dict:
        d = dict(self.__dict__)
        d["parts"] = [dict(p.__dict__) for p in self.parts]
        return d


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
        self.ended = False             # playlist said ENDLIST
        self.new_part = True           # next segment opens a new part
        (d / "live").mkdir(parents=True, exist_ok=True)

    # -- state --
    def save(self) -> None:
        with self.lock:
            _write_json(self.d / STATE, self.st.dump())

    @property
    def last_seen(self) -> float:
        return self.st.last_seen

    def start(self) -> None:
        self.stop_evt.clear()
        self.ended = False
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

    def _video(self) -> None:
        media, fails, alerted, saved = None, 0, False, time.monotonic()
        began = _now()  # #77 M10: a stream that never sends a segment still counts as stalled
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
                    code, text = self.kick.text(media)
                    if code != 200:
                        raise LookupError(f"media playlist {code}")
                    segs, endlist, target = parse_media(text, media)
                except (requests.RequestException, LookupError, ValueError) as e:
                    fails += 1
                    if fails >= 3:
                        media = None   # re-resolve: token expired or stream restarted
                    stalled = _now() - (self.st.last_seen or began)
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
                    last, end = self.st.last_seq, self.st.last_end
                    if last < 0 and not end:
                        missing, gap = 0, 0.0          # first segment of the recording
                    elif last >= 0 and last < s.seq:
                        missing = s.seq - last - 1     # same broadcast
                        gap = max(0.0, s.pdt - end) if missing else 0.0
                    else:
                        missing, gap = -1, max(0.0, s.pdt - end)  # sequence reset: new broadcast
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
        """Already written? By sequence number; program date-times only decide whether
        a lower sequence is a new broadcast (sequence reset) rather than a repeat."""
        last, end = self.st.last_seq, self.st.last_end
        if last >= 0:
            return s.seq <= last and s.pdt < end + 30
        return bool(end) and s.pdt + s.dur <= end + 0.5   # state from before last_seq

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
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect

        backoff = 1
        rows = open(self.d / LIVE_CHAT, "a", encoding="utf-8")  # noqa: SIM115 (closed in finally)
        try:
            while not self.stop_evt.is_set():
                span = None
                try:
                    ws_url = DEFAULT_CHAT_WS
                    try:
                        conns = self.kick.post(f"/channels/{self.st.channel_id}/chat/connection",
                                               {"client": {"id": str(uuid.uuid4()), "type": "web"},
                                                "capabilities": {"accepted_providers":
                                                                 [{"provider": "centrifugo"}]}})
                        for c in conns["data"]["connections"]:
                            if c["provider"] == "centrifugo":
                                ws_url = c["credentials"]["url"]
                    except (requests.RequestException, KeyError, ValueError) as e:
                        log.warning("%s: chat connection lookup failed (%s); using default",
                                    self.st.stream_id, e)
                    client_id = str(uuid.uuid4())
                    token = self.kick.post("/auth/connection", {"client_id": client_id})["data"]["token"]
                    with connect(ws_url, open_timeout=20, close_timeout=5,
                                 additional_headers={"Origin": "https://kick.com",
                                                     "User-Agent": self.cfg["kick"]["user_agent"]}) as ws:
                        ws.send(json.dumps({"id": 1, "connect": {"token": token, "name": "js"}}))
                        reply = json.loads(ws.recv(timeout=20).split("\n")[0])
                        if "connect" not in reply:
                            raise ConnectionError(f"chat connect refused: {reply}")
                        ttl = reply["connect"].get("ttl") or 0
                        refresh_at = time.monotonic() + max(60, ttl - 300) if ttl else None
                        channel = f"chatrooms.{self.st.chatroom_id}.v2"
                        ws.send(json.dumps({"id": 2, "subscribe": {"channel": channel}}))
                        cmd_id = 3
                        log.info("%s: chat connected (%s)", self.st.stream_id, channel)
                        while not self.stop_evt.is_set():
                            if span:
                                span[1] = _now()
                            if refresh_at and time.monotonic() > refresh_at:
                                token = self.kick.post("/auth/connection",
                                                       {"client_id": client_id})["data"]["token"]
                                ws.send(json.dumps({"id": cmd_id, "refresh": {"token": token}}))
                                cmd_id += 1
                                refresh_at = time.monotonic() + max(60, ttl - 300)
                            try:
                                frame = ws.recv(timeout=5)
                            except TimeoutError:
                                continue
                            for raw in frame.split("\n"):
                                if raw == "{}":
                                    ws.send("{}")  # Centrifugo ping
                                    continue
                                if not raw:
                                    continue
                                msg = json.loads(raw)
                                if msg.get("id") == 2:
                                    if "error" in msg:
                                        raise ConnectionError(f"subscribe refused: {msg['error']}")
                                    span = [_now(), _now()]
                                    with self.lock:
                                        self.st.chat.append(span)
                                    backoff = 1
                                elif "error" in msg:
                                    raise ConnectionError(f"chat error: {msg['error']}")
                                row = self._chat_row(msg)
                                if row:
                                    rows.write(json.dumps(row, ensure_ascii=False) + "\n")
                                    rows.flush()
                except (WebSocketException, OSError, ConnectionError, requests.RequestException,
                        ValueError, KeyError) as e:
                    if not self.stop_evt.is_set():
                        log.warning("%s: chat dropped: %r; reconnecting in %d s",
                                    self.st.stream_id, e, backoff)
                        if backoff >= 60:
                            self.alert(f"{self.st.stream_id}: chat keeps dropping ({e!r}); "
                                       "the gaps are backfilled at finalize")
                    self.stop_evt.wait(backoff)
                    backoff = min(backoff * 2, 120)
                finally:
                    if span:
                        span[1] = _now()
                        self.save()
        except Exception as e:
            log.exception("%s: chat thread crashed", self.st.stream_id)
            self.alert(f"{self.st.stream_id}: chat thread crashed: {e!r}", logging.ERROR)
        finally:
            rows.close()

    @staticmethod
    def _chat_row(msg: dict) -> dict | None:
        pub = (msg.get("push") or {}).get("pub") or {}
        ev = pub.get("data") or {}
        if not isinstance(ev, dict) or ev.get("event") != "App\\Events\\ChatMessageEvent":
            return None
        m = ev["data"]
        if isinstance(m, str):
            m = json.loads(m)
        return {"id": m["id"],
                "ts": kick._parse_ts(m["created_at"]).timestamp(),
                "user": (m.get("sender") or {}).get("username"),
                "type": m.get("type"),
                "content": m.get("content", ""),
                "recv": round(_now(), 3)}


# ---------- finalize ----------

def _probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)],
                             check=True, capture_output=True, text=True).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None


def _uncovered(spans: list[list[float]], a: float, b: float, min_s: float) -> list[tuple[float, float]]:
    out, cur = [], a
    for s, e in sorted(spans):
        if s > cur + min_s:
            out.append((cur, min(s, b)))
        cur = max(cur, e)
        if cur >= b:
            break
    if b > cur + min_s:
        out.append((cur, b))
    return [(x, y) for x, y in out if y > x]


def source_map(parts: list[Part]) -> list[dict]:
    """Where each part starts on the wall clock and in the joined source."""
    mapping, at = [], 0.0
    for p in parts:
        mapping.append({"file": p.file, "wall_start": p.wall_start, "src_start": round(at, 3),
                        "duration_s": round(p.duration_s, 3)})
        at += p.duration_s
    return mapping


def source_time(mapping: list[dict], ts: float, tail_s: float) -> float | None:
    """Wall-clock ts as source time. Chat trails the video by a few seconds, so tail_s
    past each part's end still counts; messages from inside a gap have no video to
    belong to (None)."""
    for m in reversed(mapping):
        if ts >= m["wall_start"]:
            off = ts - m["wall_start"]
            if off <= m["duration_s"] + tail_s:
                return round(m["src_start"] + off, 3)
            return None
    return None


def finalize(d: Path, cfg: dict, alerts: Alerts | None = None) -> dict | None:
    """Join parts, write chat.jsonl / meta.json / audio.wav, run on_finish."""
    rc = cfg["record"]
    alerts = alerts or Alerts(cfg)
    st = State.load(d / STATE)
    st.status = "finalizing"
    _write_json(d / STATE, st.dump())
    parts = [p for p in st.parts if p.segments and (d / p.file).exists()]
    if not parts:
        st.status = "failed"
        _write_json(d / STATE, st.dump())
        alerts(f"{st.stream_id}: nothing was recorded", logging.ERROR)
        return None

    # 1. video: stream-copy the parts into one file
    for p in parts:
        p.duration_s = _probe_duration(d / p.file) or p.duration_s
    ext = rc["container"]
    src = d / f"source.{ext}"
    tmp = d / f"source.tmp.{ext}"
    if len(parts) == 1 and ext == "ts":
        (d / parts[0].file).replace(src)
    else:
        listing = d / "live" / "concat.txt"
        listing.write_text("".join(f"file '{Path(p.file).name}'\nduration {p.duration_s}\n"
                                   for p in parts), encoding="utf-8")
        log.info("%s: joining %d part(s) -> %s", st.stream_id, len(parts), src.name)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(tmp)], check=True)
        tmp.replace(src)
    duration = _probe_duration(src) or sum(p.duration_s for p in parts)

    mapping = source_map(parts)
    t0 = parts[0].wall_start
    wall_end = parts[-1].wall_start + parts[-1].duration_s

    def to_t(ts: float) -> float | None:
        return source_time(mapping, ts, rc["chat_tail_s"])

    # 2. chat: live rows + history backfill for the time the socket was down
    seen: dict[str, dict] = {}
    live = d / LIVE_CHAT
    if live.exists():
        for line in live.read_text(encoding="utf-8").splitlines():
            if line.strip():
                m = json.loads(line)
                seen[m["id"]] = m
    holes = [h for p in parts for h in _uncovered(st.chat, p.wall_start,
                                                  p.wall_start + p.duration_s,
                                                  rc["backfill_min_s"])]
    backfilled = 0
    if holes and rc["backfill_chat"]:
        limiter = kick.RateLimiter(cfg["kick"]["chat_rps"])
        for a, b in holes:
            try:
                for m in kick._page_chunk(st.channel_id, a, b, cfg["kick"]["user_agent"], limiter):
                    if m["id"] not in seen:
                        seen[m["id"]] = m
                        backfilled += 1
            except (RuntimeError, OSError) as e:
                alerts(f"{st.stream_id}: chat backfill {_iso(a)}..{_iso(b)} failed: {e}")
    rows = []
    for m in sorted(seen.values(), key=lambda m: (m["ts"], m["id"])):
        t = to_t(m["ts"])
        if t is not None and 0 <= t <= duration:
            m["t"] = t
            rows.append(m)
    with open(d / "chat.tmp", "w", encoding="utf-8") as f:
        for m in rows:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    (d / "chat.tmp").replace(d / "chat.jsonl")

    # 3. meta.json, as kick.resolve writes it
    lsid = st.sessions[0]
    meta = {
        "stream_id": st.stream_id,
        "platform": "kick",
        "channel": st.channel,
        "channel_id": st.channel_id,
        "livestream_id": lsid,
        "video_uuid": None,
        "url": f"https://kick.com/{st.channel}",
        "title": st.title,
        "started_at": _iso(t0),
        "duration_s": round(duration, 3),
        "source": src.name,
        "recording": {
            "recorder": "clipper record",
            "stream_started_at": st.stream_started_at,
            "ended_at": _iso(wall_end),
            "sessions": st.sessions,
            "variant_max_height": st.max_height,
            "parts": mapping,
            "gaps": st.gaps,
            "missing_video_s": round(sum(max(0, g["missing_s"]) for g in st.gaps), 1),
            "chat_live_messages": len(seen) - backfilled,
            "chat_backfilled": backfilled,
            "chat_holes": [[_iso(a), round(b - a, 1)] for a, b in holes],
            "segment_titles": st.titles,
        },
    }
    _write_json(d / "meta.json", meta)

    # 4. audio.wav for signals (transcribe reuses it)
    if rc["extract_audio"] and not (d / "audio.wav").exists():
        from .transcribe import extract_audio
        extract_audio(d)

    if not rc["keep_parts"]:
        for p in st.parts:
            (d / p.file).unlink(missing_ok=True)
    st.status = "done"
    _write_json(d / STATE, st.dump())
    alerts(f"{st.stream_id} recorded: {st.title!r}, {duration / 3600:.2f} h, {len(rows)} chat "
           f"messages, {len(parts)} part(s)"
           + (f", {meta['recording']['missing_video_s']:.0f} s video missing" if st.gaps else ""),
           logging.INFO)

    if rc["on_finish"]:
        cmd = rc["on_finish"].format(stream_id=st.stream_id, stream_dir=str(d), channel=st.channel)
        log.info("%s: on_finish: %s", st.stream_id, cmd)
        r = subprocess.run(cmd, shell=True, check=False)
        if r.returncode:
            alerts(f"{st.stream_id}: on_finish exited {r.returncode}: {cmd}", logging.ERROR)
    return meta


# ---------- watcher ----------

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
                    _write_json(d / STATE, st.dump())
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
        _write_json(hb / "status.json", {
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
