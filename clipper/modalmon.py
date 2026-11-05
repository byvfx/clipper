"""`clipper modal`: what Modal is running and what it costs (#49).

Usage:
    clipper modal               containers up, runs going now, recent runs and failures, this
                                month's spend and each archive's progress
    clipper modal --backfill    add runs from before the log (each transcript.json's "modal"
                                record) to it, once

Every Modal call clipper makes (a stream's transcription, an archive item) writes a start and
an end line to [paths] modal_runs, so a run in progress and a failure with its error show up
without asking Modal. Spend is Modal's own billing (Workspace.billing), which counts what the
per-run estimates in those lines miss: cold starts, idle scaledown, parallel containers. The
war room's Modal panel shows the same (/api/modal), and the bot DMs (kind "spend") each time
the month's cost passes a multiple of [modal] alert_every_usd.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import jsonstate

log = logging.getLogger(__name__)

RUN_TIMEOUT = timedelta(hours=4, minutes=30)  # the app's call timeout, plus slack
_WRITE = threading.Lock()
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------- the run log ----------

def append(path: Path | None, rec: dict) -> None:
    """One line in the run log. Never raises: a lost line mustn't stop a transcription."""
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("modal run log not written (%s)", e)


@contextmanager
def tracked(path: Path | None, kind: str, what: str, gpu: str, audio_s: float):
    """Log a Modal call's start and end. The caller fills the yielded dict with the cost
    record (transcribe.modal_cost) and the audio length it got back."""
    run = {"run": os.urandom(6).hex(), "kind": kind, "what": what, "gpu": gpu or "",
           "audio_s": round(audio_s, 1), "host": os.environ.get("COMPUTERNAME") or socket.gethostname()}
    append(path, {**run, "event": "start", "at": now_iso()})
    end: dict = {}
    try:
        yield end
    except BaseException as e:
        append(path, {**run, **end, "event": "end", "at": now_iso(), "ok": False,
                      "error": f"{type(e).__name__}: {e}"[:300]})
        raise
    append(path, {**run, **end, "event": "end", "at": now_iso(), "ok": True})


def read_runs(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a line cut short by a crash
    except OSError:
        pass
    return out


def _when(r: dict) -> datetime:
    return datetime.fromisoformat(r["at"])


def runs_summary(runs: list[dict], now: datetime) -> dict:
    starts = {r["run"]: r for r in runs if r.get("event") == "start"}
    ends = [r for r in runs if r.get("event") == "end"]
    ended = {r["run"] for r in ends}
    running = sorted((s for k, s in starts.items() if k not in ended and now - _when(s) < RUN_TIMEOUT),
                     key=_when)
    month = now.strftime("%Y-%m")
    day = [r for r in ends if now - _when(r) < timedelta(hours=24)]
    return {
        "running": [{**s, "elapsed_s": round((now - _when(s)).total_seconds())} for s in running],
        "recent": [r for r in sorted(ends, key=_when)[-25:]][::-1],
        "failures_24h": [r for r in day if not r.get("ok")][-10:][::-1],
        "runs_24h": len(day),
        "est_month_usd": round(sum(r.get("est_usd", 0) for r in ends if r["at"][:7] == month), 2),
        "est_24h_usd": round(sum(r.get("est_usd", 0) for r in day), 2),
        "audio_h_24h": round(sum(r.get("audio_s", 0) for r in day if r.get("ok")) / 3600, 1),
    }


# ---------- backfill ----------

MODAL_RE = re.compile(r'"modal":\s*(\{[^{}]*\})')


def head_record(path: Path) -> dict | None:
    """The "modal" cost record at the top of a transcript.json, without reading megabytes
    of segments (it is written before them)."""
    try:
        with open(path, encoding="utf-8") as f:
            m = MODAL_RE.search(f.read(4096))
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def backfill(cfg: dict) -> None:
    path = Path(cfg["paths"]["modal_runs"])
    have = {r.get("what") for r in read_runs(path) if r.get("event") == "end"}
    todo = []
    streams = Path(cfg["paths"]["streams"])
    for t in sorted(streams.glob("*/transcript.json")):
        todo.append(("stream", t.parent.name, t, None))
    root = Path(cfg["paths"]["archive"])
    for cat_path in sorted(root.glob("*/catalog.json")):
        cat = jsonstate.load(cat_path)
        for iid, it in cat.items():
            todo.append(("archive", f"{cat_path.parent.name}/{iid}",
                         cat_path.parent / iid / "transcript.json", it.get("duration_s")))
    added = 0
    for kind, what, t, dur in todo:
        if what in have:
            continue
        rec = head_record(t)
        if not rec:
            continue
        try:
            at = datetime.fromtimestamp(t.stat().st_mtime, UTC).isoformat(timespec="seconds")
        except OSError:
            continue
        if dur is None:
            wav = t.with_name("audio.wav")
            dur = (wav.stat().st_size - 44) / 32000 if wav.exists() else 0
        append(path, {"run": f"bf-{what}", "kind": kind, "what": what, "gpu": rec.get("gpu", ""),
                      "audio_s": round(dur or 0, 1), "event": "end", "at": at, "ok": True,
                      "backfill": True, **rec})
        added += 1
    print(f"{added} runs added to {path} ({len(todo)} transcripts looked at)")


# ---------- live state and spend ----------

def cached(key: str, ttl: float, build):
    """build() at most once per ttl seconds per key; the last good value if it fails."""
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    try:
        value = build()
    except Exception as e:  # noqa: BLE001
        if hit:
            return {**hit[1], "stale": f"{type(e).__name__}: {e}"[:200]} if isinstance(hit[1], dict) else hit[1]
        return {"error": f"{type(e).__name__}: {e}"[:300]}
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
    return value


def containers() -> dict:
    """Running containers per app. Modal's Python API has no public listing; the CLI does."""
    r = subprocess.run([sys.executable, "-m", "modal", "container", "list", "--json"],
                       check=False, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1:] or "modal container list failed")
    out: dict[str, list[str]] = {}
    for c in json.loads(r.stdout or "[]"):
        out.setdefault(c.get("app_name") or c.get("app_id", "?"), []).append(c.get("start_time") or "")
    return {"apps": {k: sorted(v) for k, v in out.items()}}


def spend() -> dict:
    """This month from Modal's billing: metered, credits, billed, per app, and the last hours."""
    import modal

    ws = modal.Workspace.from_context()
    s = ws.billing.summary(cycle="this month")
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Reports hold full intervals only and hourly ones span 7 days at most: whole days of
    # the month daily, then today (UTC) and the last 24 h hourly.
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour = now.replace(minute=0, second=0, microsecond=0)
    by_app: dict[str, float] = {}
    if today > start:
        for it in ws.billing.report(start=start, end=today, resolution="d"):
            by_app[it.description] = by_app.get(it.description, 0.0) + float(it.cost)
    hours: dict[datetime, float] = {}
    for it in ws.billing.report(start=min(today, hour - timedelta(hours=24)), end=hour, resolution="h"):
        hours[it.interval_start] = hours.get(it.interval_start, 0.0) + float(it.cost)
        if it.interval_start >= today:
            by_app[it.description] = by_app.get(it.description, 0.0) + float(it.cost)
    within = lambda n: sum(v for h, v in hours.items() if h >= hour - timedelta(hours=n))
    return {"workspace": ws.name or "", "metered": round(float(s.metered_cost), 2),
            "billed": round(float(s.billed_cost), 2),
            "credits": round(-float(s.adjustments.get("Credits", 0)), 2),
            "by_app": {k: round(v, 2) for k, v in sorted(by_app.items(), key=lambda kv: -kv[1])},
            "last_24h": round(within(24), 2),
            "per_hour": round(within(3) / 3, 2),  # the last 3 full hours, idle ones count as $0
            "last_cost_at": max(hours).isoformat() if hours else ""}


def archives(cfg: dict, runs: list[dict]) -> list[dict]:
    """Each archive subject: items done / left, hours, cost so far (our estimates), and an
    ETA from the last two hours' pace."""
    root = Path(cfg["paths"]["archive"])
    est: dict[str, float] = {}
    for r in runs:
        if r.get("event") == "end" and r.get("kind") == "archive":
            subj = r["what"].split("/", 1)[0]
            est[subj] = est.get(subj, 0.0) + r.get("est_usd", 0.0)
    out = []
    for cat_path in sorted(root.glob("*/catalog.json")):
        d = cat_path.parent
        cat = jsonstate.load(cat_path)
        done_n = errors = 0
        done_h = total_h = recent_h = recent_n = 0.0
        for iid, it in cat.items():
            dur = it.get("duration_s") or 0
            total_h += dur / 3600
            try:
                mtime = (d / iid / "words.jsonl").stat().st_mtime
            except OSError:
                errors += (d / iid / "error.txt").exists()
                continue
            done_n += 1
            done_h += dur / 3600
            if time.time() - mtime < 7200:
                recent_h += dur / 3600
                recent_n += 1
        left_h = total_h - done_h
        pace = recent_h / 2  # audio hours per hour
        out.append({"subject": d.name, "items": len(cat), "done": done_n, "errors": errors,
                    "left": len(cat) - done_n - errors, "hours_done": round(done_h, 1),
                    "hours_total": round(total_h, 1), "est_usd": round(est.get(d.name, 0.0), 2),
                    "items_per_hour": round(recent_n / 2, 1), "audio_h_per_hour": round(pace, 1),
                    "eta_h": round(left_h / pace, 1) if pace and left_h > 0 else None})
    return out


def status(cfg: dict) -> dict:
    """Everything the war room panel and `clipper modal` show. Modal is asked at most every
    30 s (containers) and 5 min (billing); the archive folders every 2 min."""
    from .modal_app import APP_NAME, MAX_CONTAINERS

    now = datetime.now(UTC)
    runs = read_runs(Path(cfg["paths"]["modal_runs"]))
    sp = cached("spend", 300, spend)
    ws = sp.get("workspace", "")
    apps = sorted({APP_NAME, *sp.get("by_app", {}), *cached("containers", 30, containers).get("apps", {})})
    budget = cfg["modal"]["monthly_budget_usd"]
    return {
        "at": now.isoformat(timespec="seconds"), "app": APP_NAME, "max_containers": MAX_CONTAINERS,
        "containers": cached("containers", 30, containers), "spend": sp, "budget": budget,
        "links": {a: f"https://modal.com/apps/{ws}/main/deployed/{a}" for a in apps} if ws else {},
        **runs_summary(runs, now),
        "archives": cached("archives", 120, lambda: {"list": archives(cfg, runs)}).get("list", []),
    }


def spend_alert(cfg: dict, st: dict) -> None:
    """Called by the bot's checks once an hour: DM when the month passes the next multiple
    of [modal] alert_every_usd (once per multiple), with the burn rate."""
    from . import alerts

    step = cfg["modal"]["alert_every_usd"]
    if not step or time.time() - st.get("modal_at", 0) < 3600:
        return
    st["modal_at"] = time.time()
    sp = spend()
    month = datetime.now(UTC).strftime("%Y-%m")
    level = int(sp["metered"] // step * step)
    seen = st.get("modal_level", {})
    if level <= 0 or seen.get("month") == month and seen.get("level", 0) >= level:
        return
    st["modal_level"] = {"month": month, "level": level}
    budget = cfg["modal"]["monthly_budget_usd"]
    apps = ", ".join(f"{k} ${v:.2f}" for k, v in list(sp["by_app"].items())[:3])
    alerts.send(cfg, "spend", f"Modal this month: ${sp['metered']:.2f} metered, ${sp['billed']:.2f} "
                              f"billed after ${sp['credits']:.0f} credit; ${sp['per_hour']:.2f}/h lately"
                              + (f"; budget ${budget:.0f}" if budget else "") + f" ({apps})",
                key=f"spend:modal:{month}:{level}")


# ---------- CLI ----------

def ago(iso: str, now: datetime) -> str:
    s = (now - datetime.fromisoformat(iso)).total_seconds()
    return f"{s / 60:.0f} min" if s < 5400 else f"{s / 3600:.1f} h"


def print_status(cfg: dict) -> None:
    s = status(cfg)
    now = datetime.now(UTC)
    sp = s["spend"]
    if "error" in sp:
        print(f"billing: {sp['error']}")
    else:
        print(f"Modal ({sp['workspace']}) this month: ${sp['metered']:.2f} metered, ${sp['billed']:.2f} "
              f"billed after ${sp['credits']:.2f} credit" + (f", budget ${s['budget']:.0f}" if s["budget"] else ""))
        print(f"  last 24 h ${sp['last_24h']:.2f}, ${sp['per_hour']:.2f}/h lately; "
              + ", ".join(f"{k} ${v:.2f}" for k, v in sp["by_app"].items()))
        print(f"  our estimates: ${s['est_month_usd']:.2f} this month, ${s['est_24h_usd']:.2f} in 24 h "
              f"({s['runs_24h']} runs, {s['audio_h_24h']} h audio)")
    c = s["containers"]
    if "error" in c:
        print(f"containers: {c['error']}")
    for app, starts in c.get("apps", {}).items():
        print(f"{app}: {len(starts)} containers up" + (f" (max {s['max_containers']})" if app == s["app"] else "")
              + (f", oldest since {starts[0][:16]}" if starts else ""))
    for r in s["running"]:
        print(f"  running {r['kind']} {r['what']} on {r['gpu'] or 'default'} for {r['elapsed_s'] / 60:.0f} min "
              f"({r['audio_s'] / 3600:.1f} h audio)")
    for r in s["failures_24h"]:
        print(f"  FAILED {ago(r['at'], now)} ago: {r['what']}: {r.get('error', '')}")
    for a in s["archives"]:
        eta = f", ETA {a['eta_h']:.1f} h" if a["eta_h"] else ""
        print(f"archive {a['subject']}: {a['done']}/{a['items']} items ({a['hours_done']:.0f}/{a['hours_total']:.0f} h), "
              f"{a['errors']} errors, {a['items_per_hour']}/h{eta}, ~${a['est_usd']:.2f} estimated")
    if s["links"]:
        print(f"dashboard: {s['links'].get(s['app'], '')}")
