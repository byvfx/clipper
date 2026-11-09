"""`clipper checkup` (#185): what the alerts don't catch, from the files the services write.

The alerts (clipper/alerts.py) DM single events: a stage failing after its retries, a crash,
the recorder stalling. A problem that never raises an error stays quiet there: a stage that
hangs, a service that stops beating, a recording the worker never starts, a release that
misses its upload, a title that breaks the house rules. This looks for those:

    services   serve's, the bot's and the recorder's heartbeats
    streams    a stage running past [checkup] stuck_hours; a run whose worker stopped
               beating; a finished recording waiting queue_hours while nothing runs; a run
               that failed with no retries left
    schedule   a release not uploaded late_hours before its slot (or past it); no release
               at all in the next gap_hours
    lint       what is about to go out: [checkup] words in a title, description or
               thumbnail text, no description, a title or description over YouTube's limit

No model, no network, nothing written unless --mark. Each finding has a stable key; --mark
keeps the keys in <streams>/.checkup/state.json, so the next run can say which are new (the
claude container's nightly run uses it, deploy/claude/checkup.sh). Exit 1 when it finds anything.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import daemon, jsonstate, services, worker
from .errors import ClipperError

BAD, WARN = "bad", "warn"


def finding(key: str, check: str, level: str, text: str, look: str = "", stream: str = "") -> dict:
    """look: where to read more, or the command that fixes it."""
    return {"key": key, "check": check, "level": level, "text": text, "look": look, "stream": stream}


def _when(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso or "")
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()   # a naive time is this machine's


def _age_s(iso: str | None, now: datetime) -> float:
    t = _when(iso)
    return (now - t).total_seconds() if t else float("inf")


def _hours(s: float) -> str:
    return f"{s / 3600:.1f} h" if s >= 3600 else f"{s / 60:.0f} min"


# ---------- the checks ----------

def check_services(cfg: dict, now: datetime) -> list[dict]:
    out = []
    for name in ("serve", "bot"):
        hb = daemon.heartbeat(cfg, name, now)
        level, text = daemon.state(hb)
        if level != "ok":
            why = " (the bot sends the DMs: none arrive while it is down)" if name == "bot" else ""
            out.append(finding(f"service:{name}", "services", BAD, f"{name}: {text}{why}",
                               look="clipper services status (laptop); the container's logs"))
    rec = services.recorder(cfg, now)
    if rec.get("error") or rec["age_s"] is None or not rec["up"]:
        out.append(finding("service:recorder", "services", BAD, services.recorder_lines(rec)[0],
                           look="clipper record status; deploy/record (only when nothing is live)"))
    return out


def _running_stage(run: dict) -> tuple[str, dict]:
    return next(((s, st) for s, st in run.get("stages", {}).items() if st.get("status") == "running"),
                ("", {}))


def _waiting_since(d: Path, run: dict) -> str:
    """When a queued stream became ready: its run's last write, else the end of the recording."""
    if run.get("updated"):
        return run["updated"]
    meta = jsonstate.read_or(d / "meta.json", {})
    if meta.get("ended_at"):
        return meta["ended_at"]
    for f in (d / "live" / "state.json", d / "meta.json"):
        if f.exists():
            return datetime.fromtimestamp(f.stat().st_mtime, UTC).isoformat(timespec="seconds")
    return ""


def check_streams(cfg: dict, now: datetime) -> list[dict]:
    wc, ck = cfg["worker"], cfg["checkup"]
    root: Path = cfg["paths"]["streams"]
    if not root.is_dir():
        return [finding("streams:unreadable", "streams", BAD, f"can't read the streams folder {root}")]
    out, busy = [], False
    for d in sorted(p for p in root.iterdir() if (p / "run.json").exists()):
        run = jsonstate.read_or(d / "run.json", {})
        status = run.get("status")
        if status == "running":
            beat = _age_s(run.get("heartbeat"), now)
            stage, st = _running_stage(run)
            if beat < wc["stale_minutes"] * 60:
                busy = True
                took = _age_s(st.get("started"), now)
                if stage and took != float("inf") and took > ck["stuck_hours"] * 3600:
                    out.append(finding(f"stuck:{d.name}:{stage}", "streams", BAD,
                                       f"{d.name}: {stage} has been running for {_hours(took)} "
                                       f"(stuck_hours {ck['stuck_hours']}); the worker is still beating",
                                       look=f"streams/{d.name}/{st.get('log') or f'run_{stage}.log'}",
                                       stream=d.name))
            elif beat > 2 * wc["stale_minutes"] * 60:
                last = f"{_hours(beat)} ago" if beat != float("inf") else "never"
                out.append(finding(f"orphan:{d.name}", "streams", BAD,
                                   f"{d.name}: run.json says running ({stage or 'no stage'}) but the "
                                   f"worker's last heartbeat was {last}: it stopped mid-run",
                                   look="the worker container's logs; it takes the stream over on its next pass",
                                   stream=d.name))
        elif status == "failed" and run.get("attempts", 0) >= wc["max_attempts"]:
            out.append(finding(f"failed:{d.name}", "streams", BAD,
                               f"{d.name}: failed with no retries left: {run.get('error', '')[:300]}",
                               look=f"streams/{d.name}/run_*.log; then `clipper run {d.name}`",
                               stream=d.name))
    if busy:
        return out   # one worker runs one stream at a time: a queue behind it is expected
    for d in worker.pending(cfg):
        run = jsonstate.read_or(d / "run.json", {})
        waited = _age_s(_waiting_since(d, run), now)
        if waited != float("inf") and waited > ck["queue_hours"] * 3600:
            out.append(finding(f"queued:{d.name}", "streams", BAD,
                               f"{d.name}: ready for the worker for {_hours(waited)} "
                               f"({run.get('status') or 'not started'}) and nothing is running",
                               look="the worker container (clipper services status worker)", stream=d.name))
    return out


def _entries(cfg: dict) -> list[dict]:
    path: Path = cfg["paths"]["schedule"]
    return jsonstate.load(path).get("entries", []) if path.exists() else []


def check_schedule(cfg: dict, now: datetime) -> list[dict]:
    ck = cfg["checkup"]
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    entries = [(e, _when(e.get("publish_at"))) for e in _entries(cfg)]
    out = []
    for e, t in entries:
        if t is None or e.get("status") == "uploaded":
            continue
        left = (t - now).total_seconds()
        if left < ck["late_hours"] * 3600:
            when = f"was due {_hours(-left)} ago" if left < 0 else f"releases in {_hours(left)}"
            out.append(finding(f"late:{e['key']}", "schedule", BAD,
                               f"{e['key']} ({e.get('kind')}, {t.astimezone(tz):%a %H:%M}) {when} "
                               f"and isn't uploaded: {e.get('title', '')[:80]}",
                               look="serve's upload job (clipper status); `clipper upload --next`",
                               stream=e.get("stream", "")))
    if ck["gap_hours"] and not any(t and now <= t <= now + timedelta(hours=ck["gap_hours"])
                                   for _, t in entries):
        later = min((t for _, t in entries if t and t > now), default=None)
        nxt = f"; the next is {later.astimezone(tz):%a %m-%d %H:%M}" if later else "; none scheduled at all"
        out.append(finding("schedule:gap", "schedule", WARN,
                           f"nothing releases in the next {ck['gap_hours']} h{nxt}",
                           look="clipper schedule --list; review counts in clipper status"))
    return out


def lint_text(words: list[str], fields: dict[str, str]) -> list[str]:
    """What breaks the house rules in an item's text: [checkup] words (whole words, any case)."""
    if not words:
        return []
    rx = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w-])", re.IGNORECASE)
    return [f"{name} says {m.group(0)!r}" for name, text in fields.items() if (m := rx.search(text or ""))]


def check_lint(cfg: dict, now: datetime) -> list[dict]:
    from . import youtube

    words = cfg["checkup"]["words"]
    out = []
    for e in _entries(cfg):
        t = _when(e.get("publish_at"))
        if e.get("status") == "uploaded" or t is None or t < now - timedelta(days=1):
            continue
        try:
            m = youtube.metadata(e, cfg)
        except (ClipperError, OSError, ValueError, KeyError) as err:
            out.append(finding(f"lint:{e['key']}:unreadable", "lint", BAD,
                               f"{e['key']}: its title and description can't be read: {err}",
                               stream=e.get("stream", "")))
            continue
        problems = lint_text(words, {"title": m["title"], "description": m["description"],
                                     "thumbnail text": m["thumb_text"]})
        problems += [f"{n} is over YouTube's limit and will be cut" for n in m["cut"]]
        if not m["description"].strip():
            problems.append("no description")
        if problems:
            out.append(finding(f"lint:{e['key']}", "lint", WARN,
                               f"{e['key']} ({t:%m-%d}): " + "; ".join(problems) + f": {m['title'][:80]}",
                               look="edit it in Kitsu (or the war room) before it uploads",
                               stream=e.get("stream", "")))
    return out


CHECKS: dict[str, Callable[[dict, datetime], list[dict]]] = {
    "services": check_services, "streams": check_streams, "schedule": check_schedule, "lint": check_lint}


# ---------- running them ----------

def _state_path(cfg: dict) -> Path:
    return cfg["paths"]["streams"] / ".checkup" / "state.json"


def run(cfg: dict, only: list[str] | None = None, mark: bool = False,
        now: datetime | None = None) -> dict:
    """{at, checks, findings}; each finding also says when it was first seen and whether it is
    new since the last --mark. A check that crashes is a finding too, so the rest still run."""
    now = now or datetime.now(UTC)
    names = only or list(CHECKS)
    found = []
    for name in names:
        try:
            found += CHECKS[name](cfg, now)
        except Exception as e:  # noqa: BLE001  # one broken check mustn't hide the others' findings
            found.append(finding(f"check:{name}", name, BAD, f"the {name} check failed: {type(e).__name__}: {e}"))
    seen = jsonstate.read_or(_state_path(cfg), {}).get("seen", {})
    at = now.isoformat(timespec="seconds")
    for f in found:
        f["new"] = f["key"] not in seen
        f["first_seen"] = seen.get(f["key"], {}).get("first_seen", at)
    if mark:
        # Only what is still wrong is kept, so a problem that clears and comes back is new
        # again; a run of some checks (--only) leaves the others' findings as they were.
        kept = {k: v for k, v in seen.items() if v.get("check") not in names}
        path = _state_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        jsonstate.write(path, {"at": at, "seen": {
            **kept, **{f["key"]: {"first_seen": f["first_seen"], "check": f["check"]} for f in found}}}, indent=1)
    return {"at": at, "checks": names, "findings": found}


def lines(report: dict, tz: ZoneInfo) -> list[str]:
    fs = report["findings"]
    at = datetime.fromisoformat(report["at"]).astimezone(tz)
    if not fs:
        return [f"checkup {at:%m-%d %H:%M}: all good ({', '.join(report['checks'])})"]
    new = sum(f["new"] for f in fs)
    out = [f"checkup {at:%m-%d %H:%M}: {len(fs)} finding{'s' if len(fs) != 1 else ''}"
           + (f" ({new} new)" if new != len(fs) else "")]
    for f in sorted(fs, key=lambda f: (f["level"] != BAD, list(CHECKS).index(f["check"])
                                       if f["check"] in CHECKS else 99)):
        tag = "BAD " if f["level"] == BAD else "warn"
        since = "" if f["new"] else f" (since {datetime.fromisoformat(f['first_seen']).astimezone(tz):%m-%d %H:%M})"
        out.append(f"  {tag} {f['check']:<9} {f['text']}{since}")
        if f["look"]:
            out.append(f"       look: {f['look']}")
    return out
