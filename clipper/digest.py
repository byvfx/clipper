"""The daily digest DM (`clipper alert digest`; the bot queues it once a day at [notify]
digest_time): what went out today, what is scheduled for tomorrow, what waits for review,
which streams are stuck on a failed stage, and the Shorts still to link to their segment in
Studio. Built from the war room's overview so it reads like the page. alerts.py only carries
it: the bot hands `text` to alerts.pending, so the outbox module never reads the war room."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import jsonstate

log = logging.getLogger("clipper.digest")


def text(cfg: dict, now: datetime | None = None) -> str:
    from . import warroom

    tz = ZoneInfo(cfg["schedule"]["timezone"])
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
        sched = jsonstate.load(cfg["paths"]["schedule"])
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
