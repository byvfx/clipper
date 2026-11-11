"""`clipper audit`: the weekly digest, and the brief the monthly deep audit starts from.

The nightly `clipper checkup` asks "what is wrong right now". This asks "how did the week
go": what ran and failed, what went out, what it cost, whether backups are current, what is
parked, what did best. No model and nothing written unless --save; a section that can't be
read says so and the rest still run.

    clipper audit                 the digest, at most three things to do
    clipper audit --all           every thing to do, with where to look
    clipper audit --save          also keep it as <streams>/.audit/weekly-<date>.json
    clipper audit --brief         the saved weeks side by side, repeat problems first
    clipper audit --keep-deep F   keep F as this month's deep audit (deep-<YYYY-MM>.md)

Each thing to do has a `pattern`: its key with the stream taken out, so the same trouble on
two streams counts as one problem. The monthly run (deploy/claude/audit.sh) gives the brief
to a read-only `claude -p`, which follows the repeat problems into the logs and the code.
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import checkup, costs, jsonstate, wiki
from .errors import ClipperError

BAD, WARN = checkup.BAD, checkup.WARN
TOP = 3             # things to do in the DM
DM_MAX = 1800       # under Discord's 2000 characters
DEEP_KEPT = 6000    # how much of last month's deep audit goes in the brief
REPO = wiki.URL.removeprefix("https://github.com/").removesuffix("/wiki")


def folder(cfg: dict) -> Path:
    return Path(cfg["paths"]["streams"]) / ".audit"


def _tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg["schedule"]["timezone"])


def _within(iso: str | None, now: datetime, days: int) -> bool:
    t = checkup._when(iso)
    return t is not None and now - timedelta(days=days) <= t <= now


def issue(key: str, level: str, text: str, look: str = "", stream: str = "") -> dict:
    """A thing to do. pattern: key without the stream, what repeats from week to week."""
    pattern = re.sub(re.escape(stream) + r"[^:]*", "*", key) if stream else key
    return {"key": key, "pattern": pattern, "level": level, "text": text, "look": look, "stream": stream}


# ---------- the sections ----------

def check_runs(cfg: dict, now: datetime, days: int) -> dict:
    """The worker's week. A stream that failed and then finished shows as finished: run.json
    keeps only the last result (the stage logs have the attempts)."""
    out = {"done": 0, "partial": 0, "failed": []}
    root = Path(cfg["paths"]["streams"])
    for d in sorted(p for p in root.iterdir() if (p / "run.json").exists()):
        run = jsonstate.read_or(d / "run.json", {})
        if not _within(run.get("finished") or run.get("updated"), now, days):
            continue
        status = run.get("status")
        if status in ("done", "partial"):
            out[status] += 1
        elif status == "failed":
            stage = next((s for s, st in run.get("stages", {}).items() if st.get("status") == "failed"), "")
            out["failed"].append({"stream": d.name, "stage": stage, "error": (run.get("error") or "")[:300]})
    return out


def _shape(error: str) -> str:
    """An error's last line with its numbers and ids gone, to tell repeats apart."""
    last = error.rsplit(" | ", 1)[-1]
    return re.sub(r"[0-9a-f]{6,}|\d+", "#", last).strip()[:60]


def check_uploads(cfg: dict, now: datetime, days: int) -> dict:
    due = [e for e in checkup._entries(cfg) if _within(e.get("publish_at"), now, days)]
    kinds: dict[str, int] = {}
    for e in due:
        if e.get("status") == "uploaded":
            kinds[e.get("kind") or "?"] = kinds.get(e.get("kind") or "?", 0) + 1
    return {"uploaded": sum(kinds.values()), "kinds": kinds, "missed": len(due) - sum(kinds.values())}


def check_spend(cfg: dict, now: datetime, days: int) -> dict:
    stages: dict[str, float] = {}
    for r in costs.entries(cfg):
        if _within(r.get("ts"), now, days):
            stages[r.get("stage") or "other"] = stages.get(r.get("stage") or "other", 0.0) + float(r.get("usd") or 0)
    top = sorted(stages.items(), key=lambda kv: -kv[1])
    return {"usd": round(sum(stages.values()), 2), "stages": {k: round(v, 2) for k, v in top}}


def check_reviews(cfg: dict, now: datetime, days: int) -> dict:
    """The week's decisions, and the reasons given for the rejected ones."""
    counts: dict[str, int] = {}
    reasons = []
    for f in sorted(Path(cfg["paths"]["streams"]).glob("*/review.json")):
        for key, item in jsonstate.read_or(f, {}).items():
            if not _within(item.get("updated_at"), now, days) or item.get("decision") == "pending":
                continue
            counts[item.get("decision") or "?"] = counts.get(item.get("decision") or "?", 0) + 1
            if item.get("decision") == "rejected" and item.get("comment"):
                reasons.append({"item": f"{f.parent.name}/{key}", "comment": item["comment"][:160]})
    return {"counts": counts, "rejected_because": reasons}


def check_disk(cfg: dict, now: datetime, days: int) -> dict:
    return {"free_gb": round(shutil.disk_usage(cfg["paths"]["streams"]).free / 1e9)}


def check_backups(cfg: dict, now: datetime, days: int) -> dict:
    """The newest thing in [audit] backup_folder. Relative: beside schedule.json."""
    name = cfg["audit"]["backup_folder"]
    if not name:
        return {"set_up": False}
    where = Path(cfg["paths"]["schedule"]).parent / name
    times = [p.stat().st_mtime for p in where.iterdir()] if where.is_dir() else []
    return {"set_up": True, "age_days": round((now.timestamp() - max(times)) / 86400, 1) if times else None}


def check_repo(cfg: dict, now: datetime, days: int) -> dict:
    """Open PRs and branches besides main, from GitHub."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}

    def get(what: str) -> list[dict]:
        r = requests.get(f"https://api.github.com/repos/{REPO}/{what}", headers=headers,
                         params={"per_page": 100}, timeout=20)
        r.raise_for_status()
        return r.json()

    stale = cfg["audit"]["stale_pr_days"]
    prs = [{"number": p["number"], "title": p["title"], "days": (now - checkup._when(p["updated_at"])).days}
           for p in get("pulls")]
    return {"open": len(prs), "stale": [p for p in prs if p["days"] >= stale],
            "branches": sum(b["name"] != "main" for b in get("branches"))}


def check_reminders(cfg: dict, now: datetime, days: int) -> list[str]:
    """[audit] reminders ("2026-11-16 compare v8 with v7") due within the window, or overdue."""
    today, out = now.astimezone(_tz(cfg)).date(), []
    for line in cfg["audit"]["reminders"]:
        when, _, what = line.partition(" ")
        try:
            if date.fromisoformat(when) <= today + timedelta(days=days):
                out.append(f"{when}: {what}")
        except ValueError:
            out.append(f"[audit] reminders has a line with no date first: {line[:80]}")
    return out


def check_best(cfg: dict, now: datetime, days: int) -> list[dict]:
    """The most-watched videos published this week ([paths] schedule's analytics.json)."""
    from . import analytics

    rows = [r for r in analytics.load(cfg).values() if _within(r.get("publish_at"), now, days) and not r.get("missing")]
    rows.sort(key=lambda r: -r.get("views", 0))
    return [{"views": r.get("views", 0), "kind": r.get("kind", ""), "title": r.get("title", "")} for r in rows[:3]]


def check_checkup(cfg: dict, now: datetime, days: int) -> list[dict]:
    return checkup.run(cfg, now=now)["findings"]


SECTIONS: dict[str, Callable[[dict, datetime, int], object]] = {
    "checkup": check_checkup, "runs": check_runs, "uploads": check_uploads, "spend": check_spend,
    "reviews": check_reviews, "disk": check_disk, "backups": check_backups, "repo": check_repo,
    "reminders": check_reminders, "best": check_best}


def issues(snap: dict, max_days: int) -> list[dict]:
    """The things to do, what is breaking now first."""
    runs, found = snap.get("runs") or {}, snap.get("checkup") or []
    failed = {r["stream"] for r in runs.get("failed", [])}
    out = [issue(f["key"], BAD, f["text"], f["look"], f["stream"]) for f in found
           if f["level"] == BAD and not (f["key"].startswith("failed:") and f["stream"] in failed)]
    out += [issue(f"fail:{r['stream']}:{r['stage']}:{_shape(r['error'])}", BAD,
                  f"{r['stream']}: {r['stage'] or 'the run'} failed: {r['error'][:140]}",
                  f"streams/{r['stream']}/run_{r['stage']}.log" if r["stage"] else "", r["stream"])
            for r in runs.get("failed", [])]
    out += [issue(f"section:{name}", WARN, f"the {name} section couldn't be read: {err}")
            for name, err in snap["errors"].items()]
    b = snap.get("backups")
    if b and not b["set_up"]:
        out.append(issue("backups:none", BAD, "nothing backs up the taste files, secrets and state yet",
                         look=wiki.page("Audit", "backups")))
    elif b and (b["age_days"] is None or b["age_days"] > max_days):
        age = "it is empty" if b["age_days"] is None else f"the newest is {b['age_days']:.0f} days old"
        out.append(issue("backups:old", BAD, f"backups have stopped: {age}", look="[audit] backup_folder"))
    out += [issue(f["key"], WARN, f["text"], f["look"], f["stream"]) for f in found if f["level"] != BAD]
    out += [issue(f"reminder:{r}", WARN, r) for r in snap.get("reminders") or []]
    out += [issue("pr:stale", WARN, f"PR {p['number']} has sat for {p['days']} days: {p['title'][:90]}")
            for p in (snap.get("repo") or {}).get("stale", [])]
    return out


def collect(cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    ac = cfg["audit"]
    snap = {"at": now.isoformat(timespec="seconds"), "days": ac["window_days"], "errors": {}}
    for name, fn in SECTIONS.items():
        try:
            snap[name] = fn(cfg, now, ac["window_days"])
        except Exception as e:  # noqa: BLE001  # one unreadable section mustn't lose the week
            snap[name] = None
            snap["errors"][name] = f"{type(e).__name__}: {e}"[:200]
    snap["issues"] = issues(snap, ac["backup_max_days"])
    return snap


# ---------- the digest ----------

def _money(spend: dict) -> str:
    top = ", ".join(f"{k} ${v:.2f}" for k, v in list(spend["stages"].items())[:3])
    return f"${spend['usd']:.2f}" + (f" ({top})" if top else "")


def text(snap: dict, tz: ZoneInfo, limit: int = TOP) -> str:
    """The digest. limit: how many things to do (0 = all, with where to look)."""
    at = datetime.fromisoformat(snap["at"]).astimezone(tz)
    todo = snap["issues"]
    lines = [f"Weekly audit, {at - timedelta(days=snap['days']):%b %d} to {at:%b %d}"]
    lines.append("To do:" if todo else "Nothing to do this week.")
    for n, it in enumerate(todo[:limit] if limit else todo, 1):
        lines.append(f"{n}. {it['text'][:170]}")
        if it["look"] and not limit:
            lines.append(f"   look: {it['look']}")
    if limit and len(todo) > limit:
        lines.append(f"+{len(todo) - limit} more: clipper audit --all")
    lines.append("")
    if (r := snap.get("runs")) is not None:
        lines.append(f"Ran: {r['done']} done, {r['partial']} partial, {len(r['failed'])} failed")
    if (u := snap.get("uploads")) is not None:
        kinds = ", ".join(f"{v} {k}" for k, v in sorted(u["kinds"].items(), key=lambda kv: -kv[1]))
        lines.append(f"Out: {u['uploaded']}" + (f" ({kinds})" if kinds else "") + (f", {u['missed']} missed" if u["missed"] else ""))
    if (s := snap.get("spend")) is not None:
        lines.append(f"Spend: {_money(s)}")
    if (v := snap.get("reviews")) is not None and v["counts"]:
        lines.append("Review: " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(v["counts"].items())))
    if (d := snap.get("disk")) is not None:
        lines.append(f"Disk: {d['free_gb']} GB free")
    if (g := snap.get("repo")) is not None:
        lines.append(f"Repo: {g['open']} open PRs, {g['branches']} branches besides main")
    for b in (snap.get("best") or [])[:1]:
        lines.append(f"Best this week: {b['views']:,} views, {b['title'][:80]}")
    return "\n".join(lines)[:DM_MAX if limit else None]


def save(cfg: dict, snap: dict, tz: ZoneInfo) -> Path:
    """One file per day: a second run the same day replaces the first."""
    path = folder(cfg) / f"weekly-{datetime.fromisoformat(snap['at']).astimezone(tz):%Y-%m-%d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonstate.write(path, snap, indent=1)
    return path


# ---------- the monthly brief ----------

def snapshots(cfg: dict, weeks: int) -> list[tuple[Path, dict]]:
    """The last weeks saved, oldest first. A file that can't be read is left out."""
    got = [(p, jsonstate.read_or(p, {})) for p in sorted(folder(cfg).glob("weekly-*.json"))[-weeks:]]
    return [(p, s) for p, s in got if s]


def recurring(snaps: list[dict]) -> list[dict]:
    """Each problem once, most weeks first: {pattern, weeks, of, text, look, streams}."""
    seen: dict[str, dict] = {}
    for s in snaps:
        for pattern in {it["pattern"] for it in s.get("issues", [])}:
            seen.setdefault(pattern, {"pattern": pattern, "weeks": 0, "of": len(snaps), "streams": []})["weeks"] += 1
        for it in s.get("issues", []):
            r = seen[it["pattern"]]
            r.update(text=it["text"], look=it["look"])       # the latest wording
            if it["stream"] and it["stream"] not in r["streams"]:
                r["streams"].append(it["stream"])
    return sorted(seen.values(), key=lambda r: -r["weeks"])


def _last_deep(cfg: dict, month: str) -> str:
    """The latest deep audit from before this month, to check what came of it."""
    older = [p for p in sorted(folder(cfg).glob("deep-*.md")) if p.stem != f"deep-{month}"]
    return older[-1].read_text(encoding="utf-8", errors="replace")[:DEEP_KEPT] if older else ""


def brief(cfg: dict, weeks: int | None = None, now: datetime | None = None) -> str:
    """What the deep audit starts from; "" when no week has been saved yet."""
    tz = _tz(cfg)
    now = (now or datetime.now(UTC)).astimezone(tz)
    kept = snapshots(cfg, weeks or cfg["audit"]["deep_weeks"])
    if not kept:
        return ""
    snaps = [s for _, s in kept]
    out = [f"# Deep audit brief, {now:%Y-%m}: {len(snaps)} weekly digests", "", "## Problems, most weeks first", ""]
    for r in recurring(snaps) or []:
        where = f" Streams: {', '.join(r['streams'][:8])}." if r["streams"] else ""
        look = f" Look: {r['look']}." if r["look"] else ""
        out.append(f"- {r['weeks']} of {r['of']} weeks, `{r['pattern']}`: {r['text']}{where}{look}")
    out += ["", "## Week by week", "", "| Week ending | Done | Failed | Out | Missed | Spend | Rejected | Disk GB |",
            "|---|---|---|---|---|---|---|---|"]
    for s in snaps:
        r, u, m, v, d = (s.get(k) or {} for k in ("runs", "uploads", "spend", "reviews", "disk"))
        out.append(f"| {s['at'][:10]} | {r.get('done', '?')} | {len(r.get('failed', []))} | {u.get('uploaded', '?')} "
                   f"| {u.get('missed', '?')} | ${m.get('usd', 0):.2f} | {(v.get('counts') or {}).get('rejected', 0)} "
                   f"| {d.get('free_gb', '?')} |")
    reasons = [x for s in snaps for x in (s.get("reviews") or {}).get("rejected_because", [])]
    if reasons:
        out += ["", "## Why picks were rejected", ""] + [f"- {x['item']}: {x['comment']}" for x in reasons[-30:]]
    out += ["", "## The weekly files (the full data)", ""] + [f"- {p}" for p, _ in kept]
    if last := _last_deep(cfg, f"{now:%Y-%m}"):
        out += ["", "## The last deep audit: did its fixes happen?", "", last]
    return "\n".join(out)


def keep_deep(cfg: dict, source: Path, now: datetime | None = None) -> Path:
    report = source.read_text(encoding="utf-8", errors="replace").strip()
    if not report:
        raise ClipperError(f"audit: {source} is empty, so no deep audit was kept")
    path = folder(cfg) / f"deep-{(now or datetime.now(UTC)).astimezone(_tz(cfg)):%Y-%m}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report + "\n", encoding="utf-8")
    return path
