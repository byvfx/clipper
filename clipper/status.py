"""`clipper status`: the operator's one screen. The audit of 2026-11-08 (item 16) found that
"what is going on" took five commands (`services status`, `worker --status`, `review status`,
`schedule --list`, `upload --next`) and `costs` for the money. This reads the same files they
do, in one go: serve's and the bot's heartbeats and the recorder's (the worker keeps its
heartbeat in each stream's run.json, so its streams are its status), the review counts, the
next releases and this month's spend. Nothing is started, written or asked over the network;
only --sync calls Kitsu, for fresh decisions and links."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from . import actions, costs, daemon, schedule, services, worker

SERVICES = ("serve", "bot")   # the worker has no heartbeat file: see its streams


def lines(cfg: dict, days: int = 2, sync: bool = False) -> list[str]:
    out = ["services"]
    for name in SERVICES:
        out += ["  " + x for x in daemon.heartbeat_lines(name, daemon.heartbeat(cfg, name))]
    out += ["  " + x for x in services.recorder_lines(services.recorder(cfg))]
    streams = worker.status_lines(cfg)
    out += ["", "streams (the worker)"] + (["  " + x for x in streams] or ["  none in progress"])
    out += ["", "review" + (" (synced from Kitsu)" if sync else " (as of the last sync; --sync reads Kitsu)")]
    out += ["  " + x for x in actions.status_lines(actions.status(cfg, sync=sync, links=sync))]
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    coming = schedule.upcoming(cfg, days)
    plural = "s" if days != 1 else ""
    out += ["", f"releases in the next {days} day{plural}"]
    out += ["  " + schedule.entry_line(e, tz) for e in coming] or ["  none scheduled"]
    out += ["", "spend", "  " + spend_line(cfg)]
    return out


def spend_line(cfg: dict) -> str:
    rep = costs.report(cfg)
    top = ", ".join(f"{k} ${v:.2f}" for k, v in list(rep["by_stage"].items())[:5])
    return (f"{rep['month']}: ${rep['net_usd']:.2f} so far" + (f" ({top})" if top else "")
            + "; `clipper costs` has the detail")
