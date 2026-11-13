"""The page's data: streams and their pipeline state, the review rows, the schedule, the
services' health, the spend and analytics panels. Reads the files the pipeline writes; the
digest DM (clipper/digest.py) is built from `overview` too, so nothing here knows HTTP."""
from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .. import actions, daemon, jsonstate, review, schedule, worker
from ..config import base, for_stream
from ..timefmt import age_s
from .state import SYNC, TAILNET

LIVE_CACHE: dict = {"at": 0.0, "data": []}

def media_url(sdir: Path, p: Path | str | None) -> str:
    if not p:
        return ""
    p = Path(p)
    try:   # no resolve(): it opens the file, a round trip each on the NAS share
        rel = p.relative_to(sdir)
    except ValueError:
        return ""
    if ".." in rel.parts:
        return ""
    return f"/media/{quote(sdir.name)}/{quote(rel.as_posix())}"

def row_json(sdir: Path, r: dict, ls: review.Listing | None = None) -> dict:
    ls = ls or review.Listing()
    out = {k: v for k, v in r.items() if not isinstance(v, Path)}
    out["thumbnail_url"] = media_url(sdir, r.get("thumbnail")) + (
        f"?v={int(r['thumb_mtime'])}" if r.get("thumb_mtime") else "")
    prev = r.get("preview")
    src = prev if prev and ls.exists(prev) else r.get("file")
    out["video_url"] = media_url(sdir, src) if src and ls.exists(src) else ""
    return out

# Per-stream results, rebuilt when the stream folder's top-level entries change (any file
# written there, or a file added to a subfolder, changes a name, mtime or size), or after
# CACHE_MAX_AGE_S. Reading every item's files over the NAS share takes seconds per stream.
CACHE: dict[tuple[str, str], tuple[tuple, float, object]] = {}

CACHE_LOCK = threading.Lock()

CACHE_MAX_AGE_S = 300

def _scan(sdir: Path) -> tuple[set[str], tuple]:
    try:
        with os.scandir(sdir) as it:
            ents = sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in it)
    except OSError:
        return set(), ()
    return {e[0] for e in ents}, tuple(ents)

def _streams(cfg: dict) -> list[tuple[Path, set[str], tuple]]:
    """(stream dir, its file names, a change signature) for each stream, one scan each."""
    with os.scandir(cfg["paths"]["streams"]) as it:
        dirs = sorted(Path(e.path) for e in it if e.is_dir() and not e.name.startswith("."))
    return [(d, *_scan(d)) for d in dirs]

def _cached(sdir: Path, what: str, sig: tuple, build):
    with CACHE_LOCK:
        hit = CACHE.get((sdir.name, what))
    if hit and hit[0] == sig and time.time() - hit[1] < CACHE_MAX_AGE_S:
        return hit[2]
    val = build()
    with CACHE_LOCK:
        CACHE[(sdir.name, what)] = (sig, time.time(), val)
    return val

def _mtime(p: Path) -> int:
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return 0

def stream_info(cfg: dict, sdir: Path) -> dict:
    sc = for_stream(cfg, sdir)
    meta = jsonstate.read_or(sdir / "meta.json", {})
    run = jsonstate.read_or(sdir / "run.json", {})
    stages = {}
    for st in worker.STAGES:
        info = (run.get("stages") or {}).get(st, {})
        status = info.get("status")
        if not status:
            status = "done" if worker.output_found(sdir, st) else ""
        stages[st] = status
    live = jsonstate.read_or(sdir / "live" / "state.json", {})
    running = run.get("status") == "running" and age_s(run.get("heartbeat")) < 15 * 60
    return {"id": sdir.name, "show": sc["show"]["key"], "show_name": sc["meta"]["show_name"] or "",
            "channel": meta.get("channel") or live.get("channel", ""),
            "title": meta.get("title") or live.get("title", ""),
            "started_at": meta.get("started_at") or live.get("stream_started_at") or "",
            "duration_s": meta.get("duration_s"),
            "stages": stages, "run_status": "running" if running else run.get("status", ""),
            "run_error": run.get("error", ""), "recording": live.get("status", ""),
            "published": (sdir / "review_state.json").exists(), "faces": (sdir / "faces.json").exists()}

def shown_shows(cfg: dict) -> dict:
    """The [shows.<key>] tables the page lists: all but [warroom] hide_shows."""
    root = base(cfg)
    hide = set(root["warroom"]["hide_shows"])
    return {k: s for k, s in root["shows"].items() if k not in hide}

def kick_live(cfg: dict) -> list[dict]:
    """Each show's channels' live status from Kick, cached for a minute."""
    import requests

    if time.time() - LIVE_CACHE["at"] < 60:
        return LIVE_CACHE["data"]
    out = []
    for key, show in shown_shows(cfg).items():
        name = for_stream_show_name(cfg, key)
        for ch in show["channels"]:
            item = {"show": key, "show_name": name, "channel": ch, "live": False}
            try:
                r = requests.get(f"https://kick.com/api/v2/channels/{ch}", timeout=8,
                                 headers={"User-Agent": cfg["kick"]["user_agent"]})
                ls = (r.json() or {}).get("livestream") if r.ok else None
                if ls and ls.get("is_live"):
                    item.update(live=True, viewers=ls.get("viewer_count"),
                                title=ls.get("session_title", ""), since=ls.get("start_time", ""))
            except Exception as e:  # noqa: BLE001
                item["error"] = type(e).__name__
            out.append(item)
    LIVE_CACHE.update(at=time.time(), data=out)
    return out

def for_stream_show_name(cfg: dict, key: str) -> str:
    from ..config import for_show
    return for_show(cfg, key)["meta"]["show_name"] or key

def stream_rows(cfg: dict, sdir: Path) -> list[dict]:
    sc = for_stream(cfg, sdir)
    ls = review.Listing()
    rows = [{**row_json(sdir, r, ls), "show": sc["show"]["key"],
             "show_name": sc["meta"]["show_name"] or ""}
            for r in actions.describe(cfg, sdir).values()]
    for r in rows:  # #19: the card's warning badge
        r["issues"] = actions.style_issues(sc, {f: r.get(f) or "" for f in actions.STYLE_FIELDS})
    return rows

def all_rows(cfg: dict, streams: list | None = None) -> list[dict]:
    """Every published stream's rows, oldest stream first."""
    sched = _mtime(cfg["paths"]["schedule"])   # rows show scheduled/uploaded
    rows = []
    for sdir, names, sig in streams if streams is not None else _streams(cfg):
        if "review_state.json" not in names:
            continue
        try:
            rows += _cached(sdir, "rows", sig + (sched,), lambda d=sdir: stream_rows(cfg, d))
        # ClipperError too: for_stream raises it on a bad streams/<id>/config.toml
        except Exception as e:  # noqa: BLE001 - one broken stream shouldn't blank the page
            print(f"warroom: {sdir.name}: {type(e).__name__}: {e}", flush=True)
    return rows

def stream_infos(cfg: dict, streams: list) -> list[dict]:
    """stream_info for every recorded or recording stream, newest first, skipping any that
    can't be read. A stream the NAS recorder is on has only live/ until it finishes."""
    infos = []
    for d, names, sig in streams:
        if "meta.json" not in names and "live" not in names:
            continue
        try:
            infos.append(_cached(d, "info", sig, lambda d=d: stream_info(cfg, d)))
        except Exception as e:  # noqa: BLE001 - as in all_rows
            print(f"warroom: {d.name}: {type(e).__name__}: {e}", flush=True)
    return sorted(infos, key=lambda i: i["started_at"], reverse=True)

def _show_name(cfg: dict, sdir: Path) -> str:
    try:
        return for_stream(cfg, sdir)["meta"]["show_name"]
    except Exception:  # noqa: BLE001 - a bad config.toml: fall back to the base show
        return cfg["meta"]["show_name"]

def all_retakes(cfg: dict) -> list[dict]:
    """Every retake in every stream's retakes.json, newest first."""
    out = []
    for p in cfg["paths"]["streams"].glob("*/retakes.json"):
        for key, hs in jsonstate.read_or(p, {}).items():
            for h in hs:
                out.append({"stream": p.parent.name, "key": key, "comment": h.get("comment", ""),
                            "result": h.get("result", ""), "applied": h.get("applied", []),
                            "summary": (h.get("plan") or {}).get("summary", ""),
                            "unsupported": (h.get("plan") or {}).get("unsupported") or "",
                            "error": h.get("error", ""), "at": h.get("at", ""),
                            "cost_usd": h.get("cost_usd")})
    return sorted(out, key=lambda h: h["at"], reverse=True)

def retakes(cfg: dict, limit: int = 12) -> list[dict]:
    return all_retakes(cfg)[:limit]

def retake_cost(hs: list[dict], month: str) -> dict:
    """#203: what a retake costs (the mean of the last 50 that recorded one, None before any
    did) and this month's retakes, for the Retakes panel and the bulk note's warning."""
    costs = [h["cost_usd"] for h in hs if isinstance(h.get("cost_usd"), int | float)]
    recent = costs[:50]
    mine = [h for h in hs if h["at"][:7] == month]
    return {"avg_usd": round(sum(recent) / len(recent), 4) if recent else None,
            "month_n": len(mine),
            "month_usd": round(sum(h["cost_usd"] for h in mine
                                   if isinstance(h.get("cost_usd"), int | float)), 4)}

def health(cfg: dict) -> dict:
    """The services from their heartbeats (clipper/daemon.py), from any machine."""
    root = cfg["paths"]["streams"]
    out: dict = {"services": {}, "retake_running": (root / ".retake.lock").exists(),
                 "auto_upload_minutes": cfg["youtube"]["auto_upload_minutes"],
                 "sync": SYNC, "tailnet": TAILNET["served"]}
    for name, label in (("serve", "clipper serve"), ("bot", "Discord bot")):
        hb = daemon.heartbeat(cfg, name)
        level, text = daemon.state(hb)
        jobs = hb["data"].get("jobs") or {}
        running = sorted(j for j, st in jobs.items() if st.get("running"))
        out["services"][name] = {"label": label, "level": level, "text": text, "host": hb["host"],
                                 "age_s": hb["age_s"], "running": running,
                                 "last": {j: {"at": st.get("last_at"), "exit": st.get("last_exit"),
                                              "line": st.get("last_line", "")}
                                          for j, st in jobs.items() if st.get("last_at")}}
    return out

def upload_status(cfg: dict, sched: dict) -> dict:
    """#21: the 24 h cap like `clipper upload` counts it, the last automatic run from the
    serve log, and what's due next."""
    yc, now = cfg["youtube"], datetime.now(UTC)
    recent = sorted(datetime.fromisoformat(e["uploaded_at"]) for e in sched.get("entries", [])
                    if e.get("uploaded_at")
                    and now - datetime.fromisoformat(e["uploaded_at"]) < timedelta(hours=24))
    cap = yc["max_per_24h"]
    frees = recent[len(recent) - cap] + timedelta(hours=24) if cap and len(recent) >= cap else None
    nxt = sorted((e for e in sched.get("entries", []) if e.get("status") == "scheduled"),
                 key=lambda e: e["publish_at"])[:6]
    horizon = now + timedelta(hours=yc["upload_ahead_hours"])
    return {"last_24h": len(recent), "max_per_24h": cap, "room": max(0, cap - len(recent)),
            "frees_at": frees.isoformat() if frees else "",
            "auto_minutes": yc["auto_upload_minutes"], "last_run": last_upload_run(cfg),
            "next": [{"key": e["key"], "title": e["title"], "kind": e["kind"],
                      "publish_at": e["publish_at"], "last_error": e.get("last_error", ""),
                      "due": datetime.fromisoformat(e["publish_at"]) <= horizon} for e in nxt]}

def last_upload_run(cfg: dict) -> dict:
    """serve's last finished `clipper upload` (not the replan before it): when, exit code,
    what went up and any warnings or errors, from the tail of the serve log (and the rotated
    one before it). serve logs a job's output when it ends, so one in progress only shows
    as running."""
    serve_log = cfg["paths"]["serve_log"]
    if not serve_log or not Path(serve_log).exists():
        return {}
    lines: list[str] = []
    for p in (Path(f"{serve_log}.1"), Path(serve_log)):  # RotatingFileHandler's previous file first
        if p.exists():
            with open(p, "rb") as f:
                f.seek(max(0, p.stat().st_size - 600_000))
                lines += f.read().decode("utf-8", "replace").splitlines()
    runs, cur, prev_done = [], None, ""
    for line in lines:
        if " clipper.serve: upload: " not in line:
            continue
        at, msg = line[:19], line.split(" clipper.serve: upload: ", 1)[1]
        if msg == "starting":
            # serve replans, then uploads straight after: a start in the same minute as
            # the replan's end is the upload, even before its output is in the log.
            cur = {"at": at, "lines": [], "after_replan": prev_done[:16] == at[:16]}
        elif cur is not None and msg.startswith("done (exit"):
            prev_done = at
            cur["exit"] = msg[len("done (exit "):].rstrip(")")
            runs.append(cur)
            cur = None
        elif cur is not None:
            cur["lines"].append(msg)
    done = [r for r in runs if any(" due within " in x for x in r["lines"])]  # not the replans
    out: dict = {}
    if done:
        r = done[-1]
        keep = ("uploading ", "https://youtu.be/", "warning", "error", "Error", "failed",
                "missed slot", "uploaded in the last", " due within ", "+ ")
        out = {"at": r["at"], "exit": r["exit"],
               "lines": [x[:300] for x in r["lines"] if any(k in x for k in keep)][-14:]}
    if cur and cur["after_replan"]:
        out["running_since"] = cur["at"]
    return out

def views_by_video(cfg: dict) -> dict[str, int | None]:
    a = jsonstate.read_or(cfg["paths"]["schedule"].with_name("analytics.json"), {}) or {}
    return {vid: v.get("views") for vid, v in a.items() if isinstance(v, dict)}

def overview(cfg: dict) -> dict:
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = datetime.now(tz)
    streams_dir: Path = cfg["paths"]["streams"]
    streams = _streams(cfg)
    infos = stream_infos(cfg, streams)
    rows = all_rows(cfg, streams)
    counts = {"pending": 0, "changes": 0, "approved": 0, "approved_unscheduled": 0,
              "rejected": 0, "held": 0}
    name = {"changes_requested": "changes"}
    for r in rows:
        d = name.get(r["decision"], r["decision"])
        counts[d] = counts.get(d, 0) + 1
        if d == "approved" and not r["scheduled"] and not r["uploaded"] and not r.get("dropped"):
            counts["approved_unscheduled"] += 1
        counts["held"] += bool(r["hold"])
    sched = jsonstate.read_or(cfg["paths"]["schedule"], {}) or {}
    by_ref = {r["ref"]: r for r in rows}
    entries, views = [], views_by_video(cfg)
    for e in sched.get("entries", []):
        when = datetime.fromisoformat(e["publish_at"]).astimezone(tz)
        if when < now - timedelta(days=2) or when > now + timedelta(days=21):
            continue
        r = by_ref.get(e["key"], {})
        sd = streams_dir / e["stream"]
        entries.append({**{k: e.get(k) for k in ("key", "stream", "kind", "id", "title", "status",
                                                    "source", "url", "video_id")},
                        "publish_at": when.isoformat(), "show_name": r.get("show_name") or
                        _show_name(cfg, sd),
                        "thumbnail_url": r.get("thumbnail_url", ""),
                        "views": views.get(e.get("video_id") or "")})
    entries.sort(key=lambda e: e["publish_at"])
    sc = cfg["schedule"]
    # #20: the next 14 days' open slots (drop targets) and the spacing rules the page checks.
    taken = {e["publish_at"][:16] for e in entries}
    free = [{"at": when.isoformat(), "kind": kind}
            for when, kind in schedule.slots({**sc, "horizon_days": 14}, tz, now.date())
            if when > now + timedelta(minutes=30) and when.isoformat()[:16] not in taken]
    hs = all_retakes(cfg)
    return {"generated_at": now.isoformat(), "timezone": sc["timezone"],
            "free_slots": free, "rules": {"max_per_day": sc["max_per_day"], "min_gap_minutes": sc["min_gap_minutes"]},
            "shows": [{"key": k, "name": for_stream_show_name(cfg, k), "channels": s["channels"]}
                      for k, s in shown_shows(cfg).items()],
            "counts": {**counts, "scheduled_7d": sum(
                1 for e in entries if now <= datetime.fromisoformat(e["publish_at"]) <= now + timedelta(days=7)),
                "uploaded": sum(e.get("status") == "uploaded" for e in sched.get("entries", []))},
            "streams": infos[:20],
            "items": rows, "schedule": entries, "schedule_generated_at": sched.get("generated_at"),
            "retakes": hs[:12], "retake_cost": {**retake_cost(hs, datetime.now(UTC).strftime("%Y-%m")),
                                                "auto": bool(cfg["retake"]["auto"])},
            "health": health(cfg), "stages": worker.STAGES,
            "uploads": upload_status(cfg, sched)}

def analytics_status(cfg: dict, month: str = "") -> dict:
    """#42: the Analytics panel, the same numbers as `clipper analytics --report`."""
    try:
        from .. import analytics

        return analytics.report(cfg, month)
    except Exception as e:  # noqa: BLE001  # a panel, not a reason to fail the page
        return {"error": f"{type(e).__name__}: {e}"[:300]}

def costs_status(cfg: dict) -> dict:
    """#50: the Costs panel, month to date (clipper/costs.py)."""
    try:
        from .. import costs

        return costs.report(cfg)
    except Exception as e:  # noqa: BLE001  # a panel, not a reason to fail the page
        return {"error": f"{type(e).__name__}: {e}"[:300]}

def modal_status(cfg: dict) -> dict:
    """#49: the Modal panel (clipper/modalmon.py). Modal's token stays on this machine."""
    try:
        from .. import modalmon

        return modalmon.status(cfg)
    except Exception as e:  # noqa: BLE001  # no modal extra installed, or signed out
        return {"error": f"{type(e).__name__}: {e}"[:300]}

def usage_status(cfg: dict) -> dict:
    """#72: the API usage panel, the Claude API's and OpenRouter's own numbers (clipper/usage.py)."""
    try:
        from .. import usage

        return usage.status(cfg)
    except Exception as e:  # noqa: BLE001  # a panel, not a reason to fail the page
        return {"error": f"{type(e).__name__}: {e}"[:300]}
