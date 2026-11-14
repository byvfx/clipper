"""`clipper status`: the operator's one screen. The audit of 2026-11-08 (item 16) found that
"what is going on" took five commands (`services status`, `worker --status`, `review status`,
`schedule --list`, `upload --next`) and `costs` for the money. This reads the same files they
do, in one go: serve's and the bot's heartbeats and the recorder's (the worker keeps its
heartbeat in each stream's run.json, so its streams are its status), the review counts, the
next releases and this month's spend. Nothing is started, written or asked over the network;
only --sync calls Kitsu, for fresh decisions and links.

What needs a person comes first (#199): streams with items waiting on a decision or held, and
checkup's findings on the services, the worker's runs and the schedule. Below it, only streams
from the last RECENT_DAYS or still unfinished; --all lists every one. Review counts come from
review.json alone (actions.stream_counts), not every item's sidecar: a minute over SMB before."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from . import actions, checkup, costs, daemon, schedule, services, worker

SERVICES = ("serve", "bot")   # the worker has no heartbeat file: see its streams
RECENT_DAYS = 7
CHECKS = ("services", "streams", "schedule")   # checkup's lint reads every item: not here


def _age_days(day: str, now: datetime) -> int | None:
    """Days since a stream's YYYY-MM-DD date; None when it has none or it doesn't parse."""
    try:
        return now.date().toordinal() - datetime.strptime(day, "%Y-%m-%d").toordinal()
    except (TypeError, ValueError):
        return None


def collect(cfg: dict, days: int = 2, sync: bool = False, everything: bool = False,
            now: datetime | None = None) -> dict:
    """Everything the screen shows, as data (`clipper status --json`)."""
    now = now or datetime.now(UTC)
    recent = None if everything else RECENT_DAYS
    review = actions.status(cfg, sync=sync, links=sync)
    stale = cfg["schedule"]["stale_days"]

    def waiting(r: dict) -> bool:
        """Undecided items always; held ones only while they could still go out (an approved
        item isn't scheduled stale_days after its stream, held or not: a pin sends it anyway)."""
        age = _age_days(r["date"], now)
        return bool(r["open"]) or bool(r["held"] and (not stale or age is None or age <= stale + 1))

    rows = review["streams"]
    if recent is not None:
        rows = [r for r in rows if waiting(r) or (_age_days(r["date"], now) or 0) <= recent]
    found = checkup.run(cfg, only=list(CHECKS), now=now)["findings"]
    return {
        "at": now.isoformat(timespec="seconds"),
        "needs_you": {"review": [r for r in review["streams"] if waiting(r)], "findings": found},
        "services": {name: daemon.heartbeat(cfg, name, now) for name in SERVICES},
        "recorder": services.recorder(cfg, now),
        "worker": worker.stream_rows(cfg, recent),
        "review": {**review, "streams": rows, "hidden": len(review["streams"]) - len(rows),
                   "synced": sync},
        "releases": {"days": days, "entries": schedule.upcoming(cfg, days)},
        "spend": costs.report(cfg),
        "recent_days": recent,
    }


def needs_lines(s: dict) -> list[str]:
    out = []
    for r in s["needs_you"]["review"]:
        c = r["counts"]
        parts = [f"{c[k]} {k.replace('_', ' ')}" for k in actions.OPEN if c.get(k)]
        if r["held"]:
            parts.append(f"{r['held']} held")
        out.append(f"{r['stream']} ({r['date'] or '?'}): {', '.join(parts)}"
                   + (f": {r['title'][:50]}" if r["title"] else ""))
    if s["needs_you"]["review"]:
        out.append("  (war room, or `clipper review list <stream>`)")
    for f in s["needs_you"]["findings"]:
        out.append(f"{'BAD ' if f['level'] == checkup.BAD else 'warn'} {f['text']}")
        if f["look"]:
            out.append(f"     look: {f['look']}")
    return out or ["nothing"]


def lines(cfg: dict, days: int = 2, sync: bool = False, everything: bool = False) -> list[str]:
    return render(collect(cfg, days=days, sync=sync, everything=everything), cfg)


def render(s: dict, cfg: dict) -> list[str]:
    out = ["needs you"] + ["  " + x for x in needs_lines(s)]
    out += ["", "services"]
    for name, hb in s["services"].items():
        out += ["  " + x for x in daemon.heartbeat_lines(name, hb)]
    out += ["  " + x for x in services.recorder_lines(s["recorder"])]
    recent = s["recent_days"]
    span = f", last {recent} days and unfinished" if recent is not None else ""
    out += ["", f"streams (the worker{span})"]
    out += ["  " + worker.row_line(r) for r in s["worker"]] or ["  none in progress"]
    rv = s["review"]
    out += ["", "review" + (" (synced from Kitsu)" if rv["synced"] else " (as of the last sync; --sync reads Kitsu)")]
    out += ["  " + x for x in actions.status_lines(rv)]
    if rv["hidden"]:
        out.append(f"  {rv['hidden']} older streams with nothing waiting: --all lists them")
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    days = s["releases"]["days"]
    out += ["", f"releases in the next {days} day{'s' if days != 1 else ''}"]
    out += ["  " + schedule.entry_line(e, tz) for e in s["releases"]["entries"]] or ["  none scheduled"]
    out += ["", "spend", "  " + spend_line(s["spend"])]
    return out


def spend_line(rep: dict) -> str:
    top = ", ".join(f"{k} ${v:.2f}" for k, v in list(rep["by_stage"].items())[:5])
    return (f"{rep['month']}: ${rep['net_usd']:.2f} so far" + (f" ({top})" if top else "")
            + "; `clipper costs` has the detail")
