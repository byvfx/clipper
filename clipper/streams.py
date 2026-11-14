"""`clipper streams` (#204): what each stream folder takes on the share, and pruning the big
files of streams that are done with.

    clipper streams list [--all] [--json]
    clipper streams prune [--older-than 30] [--cuts] [--stream ID ...] [--yes]

A stream's folder keeps everything forever: the source recording (~30 GB a Kino night), the
audio for transcription, the cuts and the review previews. Nothing else frees the disk; the bot
only warns under [notify] disk_free_gb.

prune removes the source (`source.*`) and audio (`audio.*`) of streams old enough whose items
are all decided and none waits on an upload, and keeps every JSON (transcript, picks, review
state, config.toml), so the cheap stages and archive-style lookups still work. --cuts also
removes the cut video and review preview of each item that is uploaded or rejected (an approved
item the scheduler let go keeps its file: a pin can still send it); clips and Shorts stay, X
and TikTok post from them. Without --yes it only says what it would remove. What it removed is
written to the stream's pruned.json; a stage that needs the source afterwards says so.

A stream is left alone while it is recording, while the worker runs it, before it is in review,
while an item waits on a decision, is scheduled and not uploaded yet, or is held while it could
still go out ([schedule] stale_days after the stream). serve runs `prune --yes` daily when
[streams] keep_source_days is set (0 = only by hand)."""
from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .config import show_of_channel
from .errors import ClipperError
from .schedule import truthy
from .timefmt import age_s, now_iso

PRUNED = "pruned.json"
LIVE = ("recording", "paused", "finalizing")
DONE_RUNS = ("done", "skip")   # a stream with no run.json (fetched by hand) counts as done too
CUT_DIRS = ("segments", "compilations")   # the long cuts --cuts may remove (clips/, shorts/ stay)
PARTS = ("source", "audio", "cuts", "previews", "other")


NOT_MEDIA = (".json", ".txt", ".part", ".ytdl")   # source.info.json and the like stay


def _part(name: str) -> str:
    stem = name.split(".", 1)[0]
    if stem in ("source", "audio") and "." in name and not name.endswith(NOT_MEDIA):
        return stem
    if name in ("segments", "compilations", "clips", "shorts"):
        return "cuts"
    return "previews" if name == "review" else "other"


def _du(entry: os.DirEntry) -> int:
    try:
        if entry.is_dir(follow_symlinks=False):
            with os.scandir(entry.path) as it:
                return sum(_du(e) for e in it)
        return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0


def sizes(sdir: Path) -> dict[str, int]:
    """Bytes per part of a stream folder: source, audio, cuts, previews, other."""
    out = dict.fromkeys(PARTS, 0)
    with os.scandir(sdir) as it:
        for e in it:
            out[_part(e.name)] += _du(e)
    return out


def _schedule(cfg: dict) -> dict[str, dict]:
    """schedule.json's entries by key. A schedule that can't be read stops prune: without it,
    an item scheduled and not yet uploaded would look let go."""
    data = jsonstate.read(cfg["paths"]["schedule"], {})
    return {e["key"]: e for e in data.get("entries", [])}


def stream_date(sdir: Path, meta: dict) -> datetime:
    """When the stream started; the folder's own time when meta.json doesn't say."""
    try:
        t = datetime.fromisoformat(meta.get("started_at") or "")
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except ValueError:
        return datetime.fromtimestamp(sdir.stat().st_mtime, UTC)


def check(cfg: dict, sdir: Path, entries: dict[str, dict], older_than_days: float,
          now: datetime | None = None) -> dict:
    """{ok, why, date, show, done: keys whose cut may go (uploaded or rejected), files: {key:
    cut file}}. A state file that can't be read leaves the stream alone, with the reason."""
    try:
        return _check(cfg, sdir, entries, older_than_days, now or datetime.now(UTC))
    except (OSError, ValueError) as e:
        return {"ok": False, "why": f"can't read its files: {type(e).__name__}: {e}"[:160], "date": "",
                "done": [], "files": {}, "show": ""}


def _check(cfg: dict, sdir: Path, entries: dict[str, dict], older_than_days: float, now: datetime) -> dict:
    meta = jsonstate.read(sdir / "meta.json", {})
    date = stream_date(sdir, meta)
    out = {"ok": False, "why": "", "date": date.date().isoformat(), "done": [], "files": {},
           "show": show_of_channel(cfg, meta.get("channel", ""))}
    live = jsonstate.read(sdir / "live" / "state.json", {}) if (sdir / "live").is_dir() else {}
    run = jsonstate.read(sdir / "run.json", {})
    days = (now - date).total_seconds() / 86400
    if sdir.name in cfg["streams"]["never_prune"]:
        out["why"] = "in [streams] never_prune"
    elif live.get("status") in LIVE:
        out["why"] = f"recording ({live['status']})"
    elif run.get("status") == "running" and age_s(run.get("heartbeat")) < cfg["worker"]["stale_minutes"] * 60:
        out["why"] = "the worker is running it"
    elif run and run.get("status") not in DONE_RUNS:
        out["why"] = f"its run is {run.get('status') or 'unfinished'} (a later stage may need the source)"
    elif not (sdir / "review_state.json").exists():
        out["why"] = "not in review yet"
    elif days < older_than_days:
        out["why"] = f"{days:.0f} days old (under {older_than_days:g})"
    if out["why"]:
        return out
    state = jsonstate.load(sdir / "review_state.json")
    decisions = jsonstate.read(sdir / "review.json", {})
    stale = cfg["schedule"]["stale_days"]
    waiting, scheduled, held = 0, 0, 0
    for key, st in state.items():
        r = decisions.get(key, {})
        decision = r.get("decision", "pending")
        entry = entries.get(f"{sdir.name}/{key}")
        out["files"][key] = st.get("file", "").replace("\\", "/")
        if decision in ("pending", "changes_requested"):
            waiting += 1
        elif decision == "rejected":
            out["done"].append(key)
        elif entry and entry.get("status") == "uploaded":
            out["done"].append(key)
        elif entry:
            scheduled += 1
        elif truthy((r.get("fields") or {}).get("Hold") or "") and (not stale or days <= stale + 1):
            held += 1
        # else approved and let go, or never scheduled (a horizontal clip): its cut stays, the
        # source isn't needed for it
    why = [f"{n} {what}" for n, what in ((waiting, "waiting on a decision"),
                                         (scheduled, "scheduled, not uploaded"), (held, "held")) if n]
    out["why"] = "; ".join(why)
    out["ok"] = not why
    return out


def _stream_dirs(cfg: dict, only: list[str] | None = None) -> list[Path]:
    root: Path = cfg["paths"]["streams"]
    if only:
        from .config import stream_dir
        return [stream_dir(cfg, s) for s in only]
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                  and ((p / "meta.json").exists() or (p / "live").is_dir())) if root.is_dir() else []


def removable(sdir: Path, c: dict, cuts: bool) -> list[Path]:
    """The files prune would remove from a stream that passed check()."""
    out = [p for p in sdir.iterdir() if p.is_file() and _part(p.name) in ("source", "audio")]
    if cuts:
        for key in c["done"]:
            f = c["files"].get(key, "")
            if f.split("/", 1)[0] in CUT_DIRS and (sdir / f).is_file():
                out.append(sdir / f)
            if (sdir / "review" / f"{key}.mp4").is_file():
                out.append(sdir / "review" / f"{key}.mp4")
    return out


def listing(cfg: dict, everything: bool = False, older_than_days: float | None = None) -> dict:
    """Every stream folder with its sizes and whether prune would take its source."""
    older = default_days(cfg) if older_than_days is None else older_than_days
    entries = _schedule(cfg)
    rows = []
    for d in _stream_dirs(cfg):
        c = check(cfg, d, entries, older)
        sz = sizes(d)
        pruned = jsonstate.read(d / PRUNED, {})
        rows.append({"stream": d.name, "show": c["show"], "date": c["date"], "sizes": sz,
                     "total": sum(sz.values()), "prunable": c["ok"], "why": c["why"],
                     "pruned": pruned.get("at", "")})
    rows.sort(key=lambda r: r["date"])
    root = cfg["paths"]["streams"]
    usage = shutil.disk_usage(root) if root.is_dir() else None
    shown = rows if everything else [r for r in rows if r["total"] >= 1e9 or r["prunable"]]
    return {"older_than_days": older, "streams": shown, "hidden": len(rows) - len(shown),
            "total": sum(r["total"] for r in rows),
            "prunable": sum(r["sizes"]["source"] + r["sizes"]["audio"] for r in rows if r["prunable"]),
            "free": usage.free if usage else None}


def gb(n: int | float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def listing_lines(s: dict) -> list[str]:
    out = [f"{'stream':<18} {'show':<6} {'date':<10} {'total':>9} {'source':>9} {'audio':>8} "
           f"{'cuts':>9} {'previews':>9}  prune?"]
    for r in s["streams"]:
        z = r["sizes"]
        state = (f"pruned {r['pruned'][:10]}" if r["pruned"] and not z["source"] else
                 "yes" if r["prunable"] else f"no: {r['why']}")
        out.append(f"{r['stream']:<18} {r['show'] or '-':<6} {r['date']:<10} {gb(r['total']):>9} "
                   f"{gb(z['source']):>9} {gb(z['audio']):>8} {gb(z['cuts']):>9} {gb(z['previews']):>9}  {state}")
    if s["hidden"]:
        out.append(f"({s['hidden']} small streams not shown: --all lists them)")
    free = f", {gb(s['free'])} free on the share" if s["free"] is not None else ""
    out.append(f"streams take {gb(s['total'])}{free}; `clipper streams prune` (older than "
               f"{s['older_than_days']:g} days) would free {gb(s['prunable'])} of source and audio")
    return out


def default_days(cfg: dict) -> float:
    return cfg["streams"]["keep_source_days"] or 30


def prune(cfg: dict, older_than_days: float | None = None, cuts: bool = False,
          only: list[str] | None = None, yes: bool = False) -> list[str]:
    """Remove what check() allows; without yes, only say what would go. Returns the lines."""
    older = default_days(cfg) if older_than_days is None else older_than_days
    if older < 1:
        raise ClipperError("--older-than must be at least 1 day")
    entries = _schedule(cfg)
    out, freed = [], 0
    for d in _stream_dirs(cfg, only):
        c = check(cfg, d, entries, older)
        if not c["ok"]:
            if only:
                out.append(f"{d.name}: left alone: {c['why']}")
            continue
        files = removable(d, c, cuts)
        if not files:
            continue
        sized = [(f, f.stat().st_size) for f in files]
        size = sum(n for _, n in sized)
        names = sorted({f.relative_to(d).parts[0] for f in files})
        line = len(out)
        out.append(f"{d.name} ({c['date']}): {'removed' if yes else 'would remove'} {gb(size)}: "
                   f"{', '.join(names)}" + (f" ({len(files)} files)" if len(files) > len(names) else ""))
        if not yes:
            freed += size
            continue
        gone, done = [], 0
        for f, n in sized:
            try:
                f.unlink()
                gone.append(f.relative_to(d).as_posix())
                done += n
            except OSError as e:
                out.append(f"  couldn't remove {f.name}: {e}")
        if done != size:
            out[line] = out[line].replace(f"removed {gb(size)}", f"removed {gb(done)} of {gb(size)}")
        freed += done
        old = jsonstate.read(d / PRUNED, {})
        jsonstate.write(d / PRUNED, {"at": now_iso(), "removed": sorted(set(old.get("removed", [])) | set(gone)),
                                     "freed_bytes": old.get("freed_bytes", 0) + done}, indent=1)
    out.append(f"{'freed' if yes else 'would free'} {gb(freed)}"
               + ("" if yes or not freed else "; --yes removes them"))
    return out


def pruned_note(sdir: Path) -> str:
    """For an error about a missing source: " (removed by clipper streams prune on <date>)"."""
    p = jsonstate.read(Path(sdir) / PRUNED, {})
    return f" (removed by `clipper streams prune` on {p['at'][:10]})" if p.get("at") else ""
