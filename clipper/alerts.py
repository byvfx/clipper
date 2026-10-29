"""Notifications (#22): Discord DMs from the bot when something needs you.

Usage:
    clipper alert test            queue a test DM (the bot sends it within a minute)
    clipper alert digest          print today's digest (--send: DM it now)

Any clipper process (the worker, the NAS recorder, uploads, the bot) calls
`alerts.send(cfg, kind, text)`, which drops one small JSON file into
streams/.notify/outbox/ (the streams share every machine already reaches). The bot, the one
process that is always up and has the Discord token, picks them up every minute and DMs
[notify] user_id (default: the first [discord] allowed user):
- only the kinds in [notify] kinds: ready (a stream is in review), upload (upload or TikTok
  failed, YouTube limits, sign-in expired), failure (a stage failed after its retries, a
  crash, a background job failing), recorder (stalls, crashes, finalize failures), disk
  (the streams share filling up), digest (daily summary at digest_time), spend (Modal's
  month passing each [modal] alert_every_usd, checked hourly);
- the same problem (same key) at most once per cooldown_hours;
- nothing during quiet_hours: held, then sent together when they end.
The bot itself adds the disk check, the digest and "can't read the streams share" (which
the outbox can't carry).
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import shutil
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("clipper.alerts")

_SEQ = itertools.count()
ICON = {"ready": "🎬", "upload": "📤", "failure": "❌", "recorder": "🔴", "disk": "💾",
        "digest": "📰", "spend": "💸", "test": "🔔"}


def outbox(cfg: dict) -> Path:
    return Path(cfg["paths"]["streams"]) / ".notify" / "outbox"


def send(cfg: dict, kind: str, text: str, key: str = "") -> None:
    """Queue a notification for the bot. Never raises: a lost alert mustn't stop the caller.
    key: what counts as "the same problem" for cooldown_hours (default: the text, digits
    ignored, so "no video for 61 s" and "... 75 s" are one)."""
    try:
        d = outbox(cfg)
        d.mkdir(parents=True, exist_ok=True)
        ev = {"kind": kind, "text": text, "key": key or f"{kind}:{re.sub(r'[0-9]+', '#', text)[:80]}",
              "at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "host": socket.gethostname()}
        # Sorts by time (then order within this process); unique across machines.
        name = f"{time.time_ns()}-{os.getpid()}-{next(_SEQ):06d}-{os.urandom(2).hex()}.json"
        tmp = d / f"{name}.tmp"  # whole files only: other machines read this folder
        tmp.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / name)
    except OSError as e:
        log.warning("notification not queued (%s): %s", e, text)


# ---------- the bot's side ----------

def _tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg["schedule"]["timezone"])


def _hm(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)


def quiet(cfg: dict, now: datetime) -> bool:
    q = cfg["notify"]["quiet_hours"].strip()
    if not q:
        return False
    a, b = (_hm(x) for x in q.split("-"))
    t = (now.hour, now.minute)
    return a <= t < b if a <= b else t >= a or t < b  # "23:00-07:00" wraps midnight


def _state_path(cfg: dict) -> Path:
    return Path(cfg["paths"]["streams"]) / ".notify" / "state.json"


def load_state(cfg: dict) -> dict:
    try:
        st = json.loads(_state_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    return {"sent": {}, "digest": "", "disk_at": 0.0, **st}


def save_state(cfg: dict, st: dict) -> None:
    now = datetime.now().astimezone()
    keep = timedelta(days=7)  # cooldowns are hours; don't let the file grow forever
    st["sent"] = {k: v for k, v in st["sent"].items() if now - datetime.fromisoformat(v) < keep}
    p = _state_path(cfg)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(p)


def checks(cfg: dict, st: dict, now: datetime) -> None:
    """The bot's own checks, queued like any other alert: the disk once an hour, the
    digest once a day at digest_time."""
    nc = cfg["notify"]
    if time.time() - st["disk_at"] >= 3600 and nc["disk_free_gb"]:
        st["disk_at"] = time.time()
        root = Path(cfg["paths"]["streams"])
        free = shutil.disk_usage(root).free / 1e9
        if free < nc["disk_free_gb"]:
            send(cfg, "disk", f"the streams share ({root}) has {free:.0f} GB free "
                              f"(warning under {nc['disk_free_gb']} GB)", key="disk")
    if nc["digest_time"] and st["digest"] != now.date().isoformat() \
            and (now.hour, now.minute) >= _hm(nc["digest_time"]):
        st["digest"] = now.date().isoformat()
        try:
            text = digest(cfg, now)
        except Exception as e:  # noqa: BLE001  # say so once, rather than every pass
            log.warning("digest: %s: %s", type(e).__name__, e)
            text = f"Daily digest failed: {type(e).__name__}: {e}"[:1900]
        send(cfg, "digest", text, key=f"digest:{now.date()}")
    if "spend" in nc["kinds"]:
        try:  # #49: once an hour, Modal's billing for the month
            from .modalmon import spend_alert

            spend_alert(cfg, st)
        except Exception as e:  # noqa: BLE001  # no modal extra, signed out, Modal down
            log.debug("modal spend check: %s", e)


def pending(cfg: dict, link: str = "") -> tuple[list[str], list[Path], dict]:
    """What to DM now: the messages (each under Discord's 2000 characters), the outbox files
    they cover and the state to save once they're sent. Nothing during quiet hours."""
    nc, tz = cfg["notify"], _tz(cfg)
    now = datetime.now(tz)
    st = load_state(cfg)
    checks(cfg, st, now)
    d = outbox(cfg)
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files or quiet(cfg, now):
        save_state(cfg, st)
        return [], [], st
    lines, linked = [], False
    for f in files:
        try:
            ev = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if ev["kind"] not in nc["kinds"] and ev["kind"] != "test":
            continue
        last = st["sent"].get(ev["key"])
        if last and ev["kind"] != "test" \
                and now - datetime.fromisoformat(last) < timedelta(hours=nc["cooldown_hours"]):
            continue
        st["sent"][ev["key"]] = now.isoformat(timespec="seconds")
        at = datetime.fromisoformat(ev["at"]).astimezone(tz)
        when = f"[{at:%a %H:%M}] " if now - at > timedelta(minutes=10) else ""  # held or late
        lines.append(f"{ICON.get(ev['kind'], '•')} {when}{ev['text']}")
        linked |= ev["kind"] in ("ready", "digest", "test")
    if lines and link and linked:
        lines.append(f"War room: {link}")
    msgs, cur = [], ""
    for line in lines:
        line = line[:1900]
        if cur and len(cur) + len(line) + 1 > 1900:
            msgs.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        msgs.append(cur)
    return msgs, files, st


def done(cfg: dict, files: list[Path], st: dict) -> None:
    """After the DMs went out: drop the outbox files, remember what was sent."""
    for f in files:
        f.unlink(missing_ok=True)
    save_state(cfg, st)


# ---------- the daily digest ----------

def digest(cfg: dict, now: datetime | None = None) -> str:
    from . import warroom

    tz = _tz(cfg)
    now = now or datetime.now(tz)
    ov = warroom.overview(cfg)
    day = lambda e: datetime.fromisoformat(e["publish_at"]).astimezone(tz).date()
    today = [e for e in ov["schedule"] if day(e) == now.date()]
    out = [e for e in today if e.get("status") == "uploaded"
           and datetime.fromisoformat(e["publish_at"]) <= now]
    tomorrow = sorted((e for e in ov["schedule"] if day(e) == now.date() + timedelta(days=1)),
                      key=lambda e: e["publish_at"])

    def by_show(es: list[dict]) -> str:
        n: dict[str, int] = {}
        for e in es:
            n[e["show_name"] or "?"] = n.get(e["show_name"] or "?", 0) + 1
        return ", ".join(f"{k} {v}" for k, v in sorted(n.items(), key=lambda kv: -kv[1]))

    c = ov["counts"]
    lines = [f"Daily digest, {now:%a %b %d}",
             f"Out today: {len(out)} of {len(today)}" + (f" ({by_show(out)})" if out else ""),
             f"Tomorrow: {len(tomorrow)} scheduled" + (
                 f", first {datetime.fromisoformat(tomorrow[0]['publish_at']).astimezone(tz):%H:%M}: "
                 f"{tomorrow[0]['title'][:80]}" if tomorrow else ""),
             (f"Waiting for review: {c['pending']} · changes asked: {c.get('changes', 0)} · "
              f"approved, no slot yet: {c['approved_unscheduled']}")]
    failed = [s["id"] for s in ov["streams"] if s["run_status"] == "failed"]
    if failed:
        lines.append(f"Streams stuck on a failed stage: {', '.join(failed)}")
    lines += related_lines(cfg, now)
    return "\n".join(lines)


def related_lines(cfg: dict, now: datetime, show: int = 5) -> list[str]:
    """Shorts whose segment is out but whose related video isn't set yet (Studio only)."""
    from . import youtube

    try:
        sched = json.loads(cfg["paths"]["schedule"].read_text(encoding="utf-8"))
        todo = youtube.related_todo(cfg, sched, now)
    except (OSError, ValueError, KeyError) as e:  # the digest still goes out without this part
        log.warning("digest related videos: %s: %s", type(e).__name__, e)
        return []
    if not todo:
        return []
    lines = [f"Shorts to link to their segment (Studio, Related video): {len(todo)}"]
    lines += [f"  <https://studio.youtube.com/video/{e['video_id']}/edit> → search {p['video_id']}"
              for e, p in todo[:show]]
    if len(todo) > show:
        lines.append(f"  +{len(todo) - show} more: clipper upload --related")
    return lines + ["  Then: clipper upload --related <keys or links>"]
