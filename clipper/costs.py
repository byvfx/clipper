"""`clipper costs`: what clipper spends, in one place (#50).

Two logs feed it, both written where the money is spent:
    [paths] costs       one line per model call (clipper/llm.py): provider, model, tokens,
                        $ from [llm] prices, and the stage and stream the command was for
    [paths] modal_runs  one line per Modal GPU call (clipper/modalmon.py), our estimate
Plus [costs] monthly (fixed costs: plans, subscriptions) and [costs] credits (a provider's
monthly credit, shown as credit against that provider's spend, never hidden in the total).

`clipper costs [--month 2026-10]` prints the month by kind, stage, show, stream and model;
the war room's Costs panel shows the same. `--backfill` adds model spend that earlier runs
recorded in their output files (candidates.json, segments.json, receipts.json, weekly
issue.json, one-off segments), once each and marked estimated; stages that only printed a
cost (meta, thumbs, shorts) can't be recovered. Revenue comes later, once the channel is
monetized (YouTube Analytics estimatedRevenue).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .timefmt import now_iso

log = logging.getLogger(__name__)

# What the running command is: set once by the CLI, read by every model call it makes.
CONTEXT = {"stage": "", "stream": ""}


def set_context(stage: str, stream: str = "") -> None:
    CONTEXT.update(stage=stage, stream=stream)


def record(path: Path | None, rec: dict) -> None:
    """Append one line; never fails the call it's accounting for."""
    if not path:
        return
    from . import jsonstate

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with jsonstate.locked(path), open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except (OSError, TimeoutError) as e:
        log.warning("cost not recorded in %s (%s)", path, e)


def llm_call(path: Path | None, provider: str, model: str, tokens_in: int, tokens_out: int,
             usd: float) -> None:
    record(path, {"ts": now_iso(), "kind": "llm", "provider": provider, "model": model,
                  "stage": CONTEXT["stage"], "stream": CONTEXT["stream"],
                  "in": tokens_in, "out": tokens_out, "usd": round(usd, 6)})


def read(path: Path) -> list[dict]:
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


_AVG: dict = {}
AVG_TTL_S = 600


def avg_call_usd(path: Path | None, stage_prefix: str, last: int = 50) -> float | None:
    """The mean $ of the last `last` model calls whose stage starts with stage_prefix (e.g.
    "cast" for the cast labeller), None before any; for an estimate shown before a paid
    action (#203). Cached for AVG_TTL_S: the log grows with every model call, and an estimate
    doesn't need the last minute's (the cast window polls every 2.5 s while a find runs)."""
    if not path:
        return None
    key = (str(path), stage_prefix, last)
    hit = _AVG.get(key)
    if hit and time.monotonic() - hit[0] < AVG_TTL_S:
        return hit[1]
    usd = [r["usd"] for r in read(Path(path)) if r.get("kind") == "llm"
           and str(r.get("stage", "")).startswith(stage_prefix) and isinstance(r.get("usd"), int | float)][-last:]
    val = sum(usd) / len(usd) if usd else None
    _AVG[key] = (time.monotonic(), val)
    return val


def entries(cfg: dict) -> list[dict]:
    """Every cost line, model calls and Modal runs alike: {ts, kind, stage, stream, model, usd, ...}."""
    from . import modalmon

    out = read(Path(cfg["paths"]["costs"]))
    for r in modalmon.read_runs(Path(cfg["paths"]["modal_runs"])):
        if r.get("event") != "end" or not r.get("est_usd"):
            continue
        archive = r.get("kind") == "archive"
        out.append({"ts": r["at"], "kind": "modal", "provider": "modal", "model": r.get("gpu", ""),
                    "stage": "archive" if archive else "transcribe" if r.get("kind") == "stream" else r.get("kind", ""),
                    "stream": "" if archive else r.get("what", ""),
                    "subject": r["what"].split("/")[0] if archive else "",
                    "usd": float(r["est_usd"]), "estimated": True})
    return out


def show_of(cfg: dict, stream: str, cache: dict) -> str:
    from .config import show_of_channel, stream_channel

    if not stream:
        return ""
    if stream not in cache:
        sdir = Path(cfg["paths"]["streams"]) / stream
        cache[stream] = show_of_channel(cfg, stream_channel(sdir)) if sdir.is_dir() else ""
    return cache[stream]


def _add(d: dict, key: str, usd: float) -> None:
    d[key] = round(d.get(key, 0.0) + usd, 6)


def _top(d: dict, n: int = 0) -> dict:
    items = sorted(d.items(), key=lambda kv: -kv[1])
    return {k: round(v, 2) for k, v in (items[:n] if n else items)}


def report(cfg: dict, month: str = "") -> dict:
    """The month's spend (default: this month, UTC) and the all-time total."""
    cc = cfg["costs"]
    month = month or datetime.now(UTC).strftime("%Y-%m")
    rows = entries(cfg)
    shows: dict = {}
    by_kind, by_stage, by_show, by_stream, by_model = {}, {}, {}, {}, {}
    estimated = all_time = 0.0
    calls = 0
    for r in rows:
        usd = float(r.get("usd") or 0)
        all_time += usd
        if not str(r.get("ts", "")).startswith(month):
            continue
        calls += 1
        _add(by_kind, r["kind"], usd)
        _add(by_stage, r.get("stage") or "other", usd)
        _add(by_model, r.get("model") or "(not recorded)", usd)
        show = (f"archive: {r['subject']}" if r.get("subject")
                else show_of(cfg, r.get("stream", ""), shows) or ("(no stream)" if not r.get("stream") else "(no show)"))
        _add(by_show, show, usd)
        if r.get("stream"):
            _add(by_stream, r["stream"], usd)
        if r.get("estimated"):
            estimated += usd
    fixed = {k: float(v) for k, v in (cc["monthly"] or {}).items()}
    for usd in fixed.values():
        _add(by_kind, "fixed", usd)
    credits = {}
    for kind, amount in (cc["credits"] or {}).items():  # a credit covers that kind's spend, at most
        credits[kind] = round(min(float(amount), by_kind.get(kind, 0.0)), 2)
    gross = sum(by_kind.values())
    return {"month": month, "gross_usd": round(gross, 2), "credits": credits,
            "net_usd": round(gross - sum(credits.values()), 2),
            "estimated_usd": round(estimated, 2), "calls": calls,
            "by_kind": _top(by_kind), "fixed": fixed, "by_stage": _top(by_stage),
            "by_show": _top(by_show), "by_stream": _top(by_stream, 10), "by_model": _top(by_model),
            "all_time_usd": round(all_time + sum(fixed.values()), 2),
            "since": min((r["ts"] for r in rows if r.get("ts")), default="")[:10]}


def print_report(rep: dict) -> None:
    usd = lambda v: f"${v:,.2f}"
    print(f"{rep['month']}: {usd(rep['net_usd'])} net"
          + (f" ({usd(rep['gross_usd'])} before credits {', '.join(f'{k} {usd(v)}' for k, v in rep['credits'].items())})"
             if rep["credits"] else "")
          + f", {rep['calls']} calls/runs; {usd(rep['estimated_usd'])} of it estimated")
    for title, key in (("kind", "by_kind"), ("stage", "by_stage"), ("show", "by_show"),
                       ("stream", "by_stream"), ("model", "by_model")):
        if rep[key]:
            print(f"  by {title}: " + ", ".join(f"{k} {usd(v)}" for k, v in rep[key].items()))
    if rep["fixed"]:
        print("  fixed: " + ", ".join(f"{k} {usd(v)}" for k, v in rep["fixed"].items()))
    print(f"all time (since {rep['since'] or '-'}, fixed costs this month only): {usd(rep['all_time_usd'])}")


# ---------- backfill ----------

def _sources(cfg: dict):
    """(id, stage, stream, file, usd) for model spend earlier runs wrote into their outputs."""
    streams = Path(cfg["paths"]["streams"])
    for sdir in sorted(p for p in streams.iterdir() if p.is_dir()):
        for stage, name, keys in (("score", "candidates.json", ("cost_usd",)),
                                  ("segments", "segments.json", ("cost_usd",)),
                                  ("receipts", "receipts.json", ("cost_usd",))):
            p = sdir / name
            if not p.exists():
                continue
            try:
                d = jsonstate.load(p)
            except (OSError, ValueError):
                continue
            usd = sum(float(d.get(k) or 0) for k in keys) if isinstance(d, dict) else 0
            if usd:
                yield f"{sdir.name}/{name}", stage, sdir.name, p, usd
    weekly = Path(cfg["paths"]["weekly"])
    if weekly.is_dir():
        for p in sorted(weekly.glob("*/issue.json")):
            try:
                d = jsonstate.load(p)
            except (OSError, ValueError):
                continue
            usd = float(d.get("cost_usd") or 0) + float(d.get("articles_cost_usd") or 0)
            if usd:
                yield f"weekly/{p.parent.name}", "weekly", "", p, usd


def backfill(cfg: dict, dry_run: bool = False) -> int:
    path = Path(cfg["paths"]["costs"])
    rows = read(path)
    have = {r.get("id") for r in rows if r.get("id")}
    # Files written since the ledger went live are in it already, call by call.
    live = min((r["ts"] for r in rows if not r.get("backfill") and r.get("ts")), default="9")
    added = 0
    for sid, stage, stream, p, usd in _sources(cfg):
        bid = f"backfill:{sid}"
        ts = datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(timespec="seconds")
        if bid in have or ts >= live:
            continue
        print(f"  {ts[:10]} {stage:<9} {stream or sid:<28} ${usd:.2f}")
        if not dry_run:
            record(path, {"id": bid, "ts": ts, "kind": "llm", "provider": "anthropic", "model": "",
                          "stage": stage, "stream": stream, "usd": round(usd, 4),
                          "estimated": True, "backfill": True})
        added += 1
    print(f"{added} earlier costs {'would be ' if dry_run else ''}added to {path}")
    return added
