"""Kick ingest: resolve a VOD, download its HLS source, and pull chat history.

yt-dlp's Kick VOD extractor 404s on new VOD URLs, so we resolve the VOD through
the channel's video list and hand the master.m3u8 to yt-dlp's generic HLS path.

New VOD URLs carry a UUIDv7 whose first 48 bits are the stream start in epoch
ms; older URLs carry the video uuid. Both are matched against the list.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import jsonstate
from .errors import ClipperError

API = "https://kick.com/api/v2"
PAGE_RETRIES = 8
CHAT_CHUNK_S = 900  # chat_parts/<epoch start>.jsonl each cover this many seconds


class RateLimiter:
    """Spaces requests across all threads, adapting to the server's limit:
    a 429 halves the rate and pauses everyone (the limit is per client, not
    per thread); each success creeps the rate back up toward `max_rps`."""

    def __init__(self, max_rps: float, min_rps: float = 0.2):
        self.max_rps, self.min_rps = max_rps, min_rps
        self.rps = max_rps
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + 1.0 / self.rps
        time.sleep(max(0.0, at - now))

    def ok(self) -> None:
        with self.lock:
            self.rps = min(self.max_rps, self.rps + 0.02)

    def throttled(self, pause: float) -> None:
        with self.lock:
            self.rps = max(self.min_rps, self.rps / 2)
            self.next_at = max(self.next_at, time.monotonic() + pause)


THROTTLED_MAX_S = 900  # one request throttled this long is a blocked IP


def _get(url: str, ua: str, limiter: RateLimiter | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    errors = 0
    t0 = time.monotonic()
    while True:
        if limiter:
            limiter.wait()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            try:
                data = json.loads(body)
            except ValueError:  # Cloudflare's challenge page, a maintenance page
                raise ClipperError(f"Kick sent a web page, not data, for {url} (a Cloudflare check?): "
                                   f"{body[:80]!r}") from None
            if limiter:
                limiter.ok()
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Kick sends Retry-After: 0, so pause at least a second.
                pause = max(float(e.headers.get("Retry-After") or 0), 1.0)
                if limiter:
                    limiter.throttled(pause)
                else:
                    time.sleep(pause)
                if time.monotonic() - t0 > THROTTLED_MAX_S:  # #77 L8: a ban, not throttling
                    raise ClipperError(f"Kick kept answering 429 for {THROTTLED_MAX_S // 60} min: {url}") from None
                continue  # throttling is expected; retry
            if e.code not in (500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError):
            pass
        errors += 1
        if errors >= PAGE_RETRIES:
            raise ClipperError(f"giving up on {url} after {errors} errors")
        time.sleep(min(2 ** errors, 60))


def _parse_ts(s: str) -> datetime:
    # API uses both "2026-10-08 00:28:50" and ISO-8601 with Z; all UTC.
    dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_url(url: str) -> tuple[str, str]:
    m = re.search(r"kick\.com/([^/]+)/videos/([0-9a-f-]{36})", url)
    if not m:
        raise ClipperError(f"not a Kick VOD url: {url} (https://kick.com/<channel>/videos/<id>)")
    return m.group(1), m.group(2)


def resolve(url: str, cfg: dict) -> dict:
    channel, vid = parse_url(url)
    ua = cfg["kick"]["user_agent"]
    videos = _get(f"{API}/channels/{channel}/videos", ua)
    v7_start = int(vid.replace("-", "")[:12], 16) / 1000
    for v in videos:
        start = _parse_ts(v["start_time"]).timestamp()
        if v["video"]["uuid"] == vid or abs(start - v7_start) < 2:
            return {
                "stream_id": f"kick-{v['id']}",
                "platform": "kick",
                "channel": channel,
                "channel_id": v["channel_id"],
                "livestream_id": v["id"],
                "video_uuid": v["video"]["uuid"],
                "url": url,
                "title": v["session_title"],
                "started_at": _parse_ts(v["start_time"]).isoformat(),
                "duration_s": v["duration"] / 1000,
                "source": v["source"],
            }
    raise ClipperError(f"{vid} not in {channel}'s recent videos ({len(videos)} listed)")


def list_videos(channel: str, cfg: dict, limiter: RateLimiter | None = None) -> list[dict]:
    """A Kick channel's past broadcasts, newest first, as archive items (#84, #109): id,
    source, url (the VOD page), title, duration_s, channel, date, plus started_at and the
    HLS master m3u8 (`m3u8`). limiter spaces each attempt, retries included."""
    out = []
    for v in _get(f"{API}/channels/{channel}/videos", cfg["kick"]["user_agent"], limiter) or []:
        uuid = (v.get("video") or {}).get("uuid")
        if not uuid or not v.get("start_time"):
            continue
        start = _parse_ts(v["start_time"])
        out.append({"id": f"kick-{v['id']}", "source": "kick", "url": f"https://kick.com/{channel}/videos/{uuid}",
                    "title": v.get("session_title") or "", "duration_s": (v.get("duration") or 0) / 1000,
                    "channel": channel, "date": start.date().isoformat(), "started_at": start.isoformat(),
                    "m3u8": v.get("source") or ""})
    out.sort(key=lambda it: it["started_at"], reverse=True)
    return out


SKIPPED = re.compile(r"Skipping fragment", re.I)


def fetch_video(stream_dir: Path, meta: dict, cfg: dict) -> Path:
    """The VOD as source.ts. yt-dlp skips a segment Kick no longer has and carries on; that is
    kept (a VOD can lose segments for good, and a gap beats no video), but counted: printed and
    stored as meta.json's skipped_fragments, since every clip over the gap jumps there."""
    out = stream_dir / "source.ts"
    if out.exists() and not (stream_dir / "source.ts.ytdl").exists():
        return out
    k = cfg["kick"]
    proc = subprocess.Popen(
        [sys.executable, "-m", "yt_dlp", "--referer", "https://kick.com/",
         "-f", f"best[height<={k['max_height']}]", "-N", str(k["download_fragments"]),
         "--hls-use-mpegts", "--no-part", "--newline", "--progress-delta", "30",
         "-o", str(out), meta["source"]],
        # "Skipping fragment" is on stdout (to_screen), its reason on stderr: both, read here.
        # (--logtostderr is only the Python API's, the command line refuses it.)
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    skipped = 0
    for line in proc.stdout:  # passed through as it comes
        sys.stdout.write(line)
        skipped += bool(SKIPPED.search(line))
    if proc.wait():
        raise subprocess.CalledProcessError(proc.returncode, proc.args)
    if skipped:
        print(f"{stream_dir.name}: warning: {skipped} video segments were missing on Kick; "
              f"the source has gaps there")
    if skipped or (stream_dir / "meta.json").exists():  # this download's count; none clears an old one
        jsonstate.merge(stream_dir / "meta.json", {"skipped_fragments": skipped} if skipped else {},
                        ["skipped_fragments"])
    return out


def _page_chunk(channel_id: int, start: float, end: float, ua: str,
                limiter: RateLimiter | None = None) -> list[dict]:
    """Page backwards from `end` until messages are older than `start`."""
    cursor = int(end * 1e6)
    out: list[dict] = []
    while True:
        data = _get(f"{API}/channels/{channel_id}/messages?cursor={cursor}", ua, limiter)["data"]
        msgs = data.get("messages") or []
        for m in msgs:
            t = _parse_ts(m["created_at"]).timestamp()
            if start <= t < end:
                out.append({
                    "id": m["id"],
                    "ts": t,
                    "user": (m.get("sender") or {}).get("username"),
                    "type": m.get("type"),
                    "content": m.get("content", ""),
                })
        nxt = int(data.get("cursor") or 0)
        if not msgs or nxt >= cursor or nxt / 1e6 < start:
            return out
        cursor = nxt


def fetch_chat(stream_dir: Path, meta: dict, cfg: dict, chunk_s: int = CHAT_CHUNK_S) -> Path:
    out = stream_dir / "chat.jsonl"
    if out.exists():
        return out
    t0 = _parse_ts(meta["started_at"]).timestamp()
    t1 = t0 + meta["duration_s"]
    chunks = [(s, min(s + chunk_s, t1)) for s in range(int(t0), int(t1) + 1, chunk_s)]
    ua = cfg["kick"]["user_agent"]
    limiter = RateLimiter(cfg["kick"]["chat_rps"])
    parts = stream_dir / "chat_parts"
    parts.mkdir(exist_ok=True)
    done = 0
    lock = threading.Lock()

    def run(c: tuple[float, float]) -> list[dict]:
        nonlocal done
        part = parts / f"{c[0]}.jsonl"
        if part.exists():
            msgs = [json.loads(line) for line in part.read_text(encoding="utf-8").splitlines()]
        else:
            msgs = _page_chunk(meta["channel_id"], c[0], c[1], ua, limiter)
            tmp = part.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs),
                           encoding="utf-8")
            tmp.replace(part)
        with lock:
            done += 1
            print(f"chat chunk {done}/{len(chunks)}: {len(msgs)} msgs "
                  f"(rate {limiter.rps:.2f}/s)", flush=True)
        return msgs

    with ThreadPoolExecutor(cfg["kick"]["chat_workers"]) as pool:
        seen: dict[str, dict] = {}
        for msgs in pool.map(run, chunks):
            for m in msgs:
                seen[m["id"]] = m
    rows = sorted(seen.values(), key=lambda m: (m["ts"], m["id"]))
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for m in rows:
            m["t"] = round(m["ts"] - t0, 3)
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    tmp.replace(out)
    print(f"{len(rows)} chat messages -> {out}")
    return out
