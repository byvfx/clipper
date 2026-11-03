"""Finalize a recording: turn live/ into what `clipper fetch` produces.

    source.mkv   the recorded parts joined by stream copy (no re-encode)
    chat.jsonl   same rows as kick.fetch_chat: id, ts (server time), user, type, content, t
    meta.json    same keys as kick.resolve, plus "recording" (parts, gaps, chat coverage)
    audio.wav    16 kHz mono, as the transcribe stage writes it, so `clipper signals` runs

Video t=0 is the first recorded part's program date-time, so for a recording without gaps
chat t = ts - started_at exactly, as for a fetched VOD; with gaps the joined source is shorter
than the wall-clock span and chat t is mapped part by part (source_map, source_time).

Safe to run again (`clipper record finalize <stream>`): the parts that went into the source
are noted in the state, and a source joined by an earlier run is reused once its parts have
been moved or deleted (#77 L17)."""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from pathlib import Path

from .. import jsonstate, kick, media
from ..alerts import Alerts
from .state import LIVE_CHAT, STATE, Part, State, _iso

log = logging.getLogger("clipper.record")


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


# ---------- 1. video ----------

def join(d: Path, st: State, ext: str) -> list[Part]:
    """Stream-copy the recorded parts into source.<ext>; the parts in it ([] if nothing was
    recorded). The parts are noted in the state before the join, so a rerun after the join
    reuses the source even when its parts are gone (one .ts part is moved, not copied)."""
    src = d / f"source.{ext}"
    if st.joined and src.exists():
        log.info("%s: %s already joined", st.stream_id, src.name)
        return [p for p in st.parts if p.file in st.joined]
    parts = [p for p in st.parts if p.segments and (d / p.file).exists()]
    if not parts:
        return []
    for p in parts:
        p.duration_s = media.try_duration(d / p.file) or p.duration_s
    st.joined = [p.file for p in parts]
    st.save(d)
    if len(parts) == 1 and ext == "ts":
        (d / parts[0].file).replace(src)
    else:
        tmp = d / f"source.tmp.{ext}"
        listing = d / "live" / "concat.txt"
        listing.write_text("".join(f"file '{Path(p.file).name}'\nduration {p.duration_s}\n"
                                   for p in parts), encoding="utf-8")
        log.info("%s: joining %d part(s) -> %s", st.stream_id, len(parts), src.name)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(tmp)], check=True)
        tmp.replace(src)
    return parts


# ---------- 2. chat ----------

def gather_chat(d: Path, st: State, parts: list[Part], cfg: dict,
                alerts: Alerts) -> tuple[dict[str, dict], int, list[tuple[float, float]]]:
    """Live rows by id, plus history backfill for the time the socket was down:
    (rows by id, how many were backfilled, the holes)."""
    rc = cfg["record"]
    seen: dict[str, dict] = {}
    live = d / LIVE_CHAT
    bad = 0
    if live.exists():
        for line in live.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                m = json.loads(line)
                seen[m["id"]] = m
            except (ValueError, KeyError, TypeError):
                bad += 1  # a line cut short when the container was killed mid-write
    if bad:  # worth knowing, never worth leaving the stream stuck over
        alerts(f"{st.stream_id}: {bad} unreadable line(s) in {LIVE_CHAT} skipped")
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
    return seen, backfilled, holes


def place_chat(seen: dict[str, dict], mapping: list[dict], tail_s: float,
               duration: float) -> list[dict]:
    """The rows that land in the source, in order, each with its source time t."""
    rows = []
    for m in sorted(seen.values(), key=lambda m: (m["ts"], m["id"])):
        t = source_time(mapping, m["ts"], tail_s)
        if t is not None and 0 <= t <= duration:
            m["t"] = t
            rows.append(m)
    return rows


def write_chat(d: Path, rows: list[dict]) -> None:
    with open(d / "chat.tmp", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in rows)
    (d / "chat.tmp").replace(d / "chat.jsonl")


# ---------- 3. meta.json ----------

def make_meta(st: State, mapping: list[dict], src: str, duration: float, live_n: int,
              backfilled: int, holes: list[tuple[float, float]]) -> dict:
    """meta.json, as kick.resolve writes it, plus "recording"."""
    t0 = mapping[0]["wall_start"]
    wall_end = mapping[-1]["wall_start"] + mapping[-1]["duration_s"]
    return {
        "stream_id": st.stream_id,
        "platform": "kick",
        "channel": st.channel,
        "channel_id": st.channel_id,
        "livestream_id": st.sessions[0],
        "video_uuid": None,
        "url": f"https://kick.com/{st.channel}",
        "title": st.title,
        "started_at": _iso(t0),
        "duration_s": round(duration, 3),
        "source": src,
        "recording": {
            "recorder": "clipper record",
            "stream_started_at": st.stream_started_at,
            "ended_at": _iso(wall_end),
            "sessions": st.sessions,
            "variant_max_height": st.max_height,
            "parts": mapping,
            "gaps": st.gaps,
            "missing_video_s": round(sum(max(0, g["missing_s"]) for g in st.gaps), 1),
            "chat_live_messages": live_n,
            "chat_backfilled": backfilled,
            "chat_holes": [[_iso(a), round(b - a, 1)] for a, b in holes],
            "segment_titles": st.titles,
        },
    }


# ---------- 4. on_finish ----------

def on_finish_args(template: str, **values: str) -> list[str]:
    """[record] on_finish as an argument list: split like a shell line first, then each
    {stream_id} {stream_dir} {channel} filled in, so a value with spaces stays one argument
    and none of it reaches a shell (#77 L6). For pipes or &&, say `sh -c '...'`."""
    return [a.format(**values) for a in shlex.split(template)]


def run_on_finish(d: Path, st: State, rc: dict, alerts: Alerts) -> None:
    """The recording is done by now, so a bad command is an alert, not a failed finalize."""
    try:
        args = on_finish_args(rc["on_finish"], stream_id=st.stream_id, stream_dir=str(d),
                              channel=st.channel)
        log.info("%s: on_finish: %s", st.stream_id, shlex.join(args))
        code = subprocess.run(args, check=False).returncode
    except (OSError, ValueError, KeyError, IndexError) as e:
        alerts(f"{st.stream_id}: on_finish failed: {e!r} ({rc['on_finish']})", logging.ERROR)
        return
    if code:
        alerts(f"{st.stream_id}: on_finish exited {code}: {shlex.join(args)}", logging.ERROR)


def finalize(d: Path, cfg: dict, alerts: Alerts | None = None) -> dict | None:
    """Join parts, write chat.jsonl / meta.json / audio.wav, run on_finish."""
    rc = cfg["record"]
    alerts = alerts or Alerts(cfg)
    st = State.load(d / STATE)
    st.status = "finalizing"
    st.save(d)

    # 1. video: stream-copy the parts into one file
    parts = join(d, st, rc["container"])
    if not parts:
        st.status = "failed"
        st.save(d)
        alerts(f"{st.stream_id}: nothing was recorded", logging.ERROR)
        return None
    src = d / f"source.{rc['container']}"
    duration = media.try_duration(src) or sum(p.duration_s for p in parts)
    mapping = source_map(parts)

    # 2. chat: live rows + history backfill for the time the socket was down
    seen, backfilled, holes = gather_chat(d, st, parts, cfg, alerts)
    rows = place_chat(seen, mapping, rc["chat_tail_s"], duration)
    write_chat(d, rows)

    # 3. meta.json, as kick.resolve writes it
    meta = make_meta(st, mapping, src.name, duration, len(seen) - backfilled, backfilled, holes)
    jsonstate.write(d / "meta.json", meta)

    # 4. audio.wav for signals (transcribe reuses it)
    if rc["extract_audio"] and not (d / "audio.wav").exists():
        from ..transcribe import extract_audio
        extract_audio(d)

    if not rc["keep_parts"]:
        for p in st.parts:
            (d / p.file).unlink(missing_ok=True)
    st.status = "done"
    st.save(d)
    alerts(f"{st.stream_id} recorded: {st.title!r}, {duration / 3600:.2f} h, {len(rows)} chat "
           f"messages, {len(parts)} part(s)"
           + (f", {meta['recording']['missing_video_s']:.0f} s video missing" if st.gaps else ""),
           logging.INFO)

    if rc["on_finish"]:
        run_on_finish(d, st, rc, alerts)
    return meta
