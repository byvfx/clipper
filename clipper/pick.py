"""Hand picks: Shorts from a stretch of a stream the user names.

Usage:
    clipper pick <stream> --from 4:05:00 --to 4:25:00 --about "the Quartering bit"
    clipper pick <stream> --at 4:50:00 --about "Jer's story"     (--span around it)
    clipper pick <stream> --about "the video Andy and PPP go on about"   (located first)
    clipper pick <stream> --find "quartering"       (transcript search, no model calls)

Times are H:MM:SS, H:MM (hours and minutes) or seconds, on the stream's clock: what the
Kick VOD shows. A recording that started after the stream went live, or has gaps, is
shifted to the video's clock (--clock video takes times as they are).

A model reads that stretch of the transcript with the request and returns up to
[pick] max_clips moments, each with a Short window and a horizontal window, like a
scoring Pass A record. Without a time, the stream's summary timeline (from `clipper
score`) is searched for the request first. New moments are appended to candidates.json
as Pass A records (source "hand", ids h01, h02, ...) and Pass B picks, so every existing
key (c03_t12, v03_t12) stays where it was; moments overlapping an existing pick are
skipped. Then the new picks are cut, drafted, covered, rendered as Shorts and published
for review, as the worker's fast lane does for scored picks (--no-run stops after
candidates.json). The request is kept on each record ("request").
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .config import for_stream
from .errors import ClipperError
from .llm import Claude, preflight
from .prompts import Prompts
from .scoring import signal_table
from .states import next_number, numbered_picks
from .timefmt import hms, parse_time
from .transcript import load_words, snap, transcript_lines

PICK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["clips", "note"],
    "properties": {
        "clips": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["in_s", "out_s", "short_in_s", "short_out_s", "category", "hook",
                         "title", "score", "why", "source_audio_s"],
            "properties": {
                "in_s": {"type": "number"}, "out_s": {"type": "number"},
                "short_in_s": {"type": "number"}, "short_out_s": {"type": "number"},
                "category": {"type": "string", "enum": ["fail", "win", "joke", "insight",
                                                        "reaction", "hot_take"]},
                "hook": {"type": "string"}, "title": {"type": "string"},
                "score": {"type": "integer"}, "why": {"type": "string"},
                "source_audio_s": {"type": "number"}}}},
        "note": {"type": "string"},
    },
}

LOCATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["blocks"],
    "properties": {"blocks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["start_s", "why"],
        "properties": {"start_s": {"type": "number"}, "why": {"type": "string"}}}}},
}

BLOCK_S = 900  # scoring's summary blocks


# ---------- times ----------

class Clock:
    """Stream clock (Kick's, from when the stream went live) <-> video clock (the
    recording, gaps joined). A fetched VOD is the same on both."""

    def __init__(self, meta: dict, use: str = "stream"):
        rec = meta.get("recording") or {}
        self.parts = rec.get("parts") or []
        live = rec.get("stream_started_at")
        self.live = datetime.fromisoformat(live).timestamp() if live else None
        self.on = use == "stream" and bool(self.parts) and self.live is not None

    def to_video(self, t: float) -> float:
        if not self.on:
            return t
        wall = self.live + t
        for m in reversed(self.parts):
            if wall >= m["wall_start"]:
                return m["src_start"] + min(wall - m["wall_start"], m["duration_s"])
        return 0.0

    def to_stream(self, v: float) -> float:
        if not self.on:
            return v
        for m in reversed(self.parts):
            if v >= m["src_start"]:
                return m["wall_start"] + (v - m["src_start"]) - self.live
        return self.parts[0]["wall_start"] - self.live + v

    def describe(self) -> str:
        if not self.on:
            return "times are the video's clock"
        lag = self.parts[0]["wall_start"] - self.live
        gaps = len(self.parts) - 1
        return (f"times are the stream's clock (Kick VOD); the recording starts {hms(lag)} in"
                + (f" and has {gaps} gap{'s' * (gaps > 1)}" if gaps else ""))


# ---------- helpers ----------


def worker_busy(sdir: Path, cfg: dict) -> str:
    """Who holds the stream's run lock, if it is fresh (the worker cutting or publishing it)."""
    lock = sdir / ".run.lock"
    if not lock.exists():
        return ""
    run = jsonstate.read(sdir / "run.json", {})
    beat = run.get("heartbeat")
    if beat and time.time() - datetime.fromisoformat(beat).timestamp() \
            < cfg["worker"]["stale_minutes"] * 60:
        return run.get("worker", "a worker")
    return ""


def short_window(c: dict) -> tuple[float, float]:
    return c.get("short_in_s", c["in_s"]), c.get("short_out_s", c["out_s"])


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Share of a covered by b."""
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / max(1e-6, a[1] - a[0])


def next_ids(pass_a: list[dict], n: int) -> list[str]:
    used = {r["id"] for r in pass_a}
    out, i = [], 1
    while len(out) < n:
        if f"h{i:02d}" not in used:
            out.append(f"h{i:02d}")
        i += 1
    return out


# ---------- finding ----------

def find(sdir: Path, cfg: dict, text: str, clock_use: str = "stream", limit: int = 60) -> None:
    """Transcript lines containing text (case-insensitive), with both clocks."""
    meta = jsonstate.read(sdir / "meta.json", {})
    clock = Clock(meta, clock_use)
    words = load_words(sdir)
    needle = text.lower()
    hits = [line for line in transcript_lines(words, 0, float("inf"), speakers=False) if needle in line.lower()]
    print(f"{len(hits)} lines with {text!r}; {clock.describe()}")
    for line in hits[:limit]:
        t = float(line[1:line.index("]")])
        print(f"  {hms(clock.to_stream(t))}  (video {hms(t)})  {line[line.index(']') + 2:][:140]}")
    if len(hits) > limit:
        print(f"  ... {len(hits) - limit} more")


def locate(sdir: Path, cfg: dict, about: str, claude: Claude, prompts: Prompts,
           clock: Clock) -> list[tuple[float, float]]:
    """Stretches (video clock) whose summary blocks match the request."""
    # this version's summary, else the newest older one (a stream scored before a prompt bump)
    summary = jsonstate.read(sdir / f"summary_{cfg['scoring']['prompt_version']}.json", None) or next(
        (jsonstate.read(p, None) for p in sorted(sdir.glob("summary_v*.json"),
                                       key=lambda p: int(p.stem[9:]) if p.stem[9:].isdigit() else 0,
                                       reverse=True)), None)
    if not summary:
        raise ClipperError("no stream summary to search (run `clipper score` first); "
                         "give --from/--to or --at instead")
    timeline = "\n".join(f"[{b['start_s'] // 60:>3} min] {b['summary']}" for b in summary)
    pc = cfg["pick"]
    out, _ = claude.json_call(pc["locate_model"], prompts.get("pick_locate"),
                              f"<timeline>\n{timeline}\n</timeline>\n\nThe editor remembers: {about}",
                              LOCATE_SCHEMA, pc["locate_effort"])
    starts = sorted({b["start_s"] for b in summary})
    found = []
    for b in (out or {}).get("blocks", [])[:pc["max_stretches"]]:
        s = max((x for x in starts if x <= b["start_s"] + 1), default=None)
        if s is not None and s not in [f[0] for f in found]:
            found.append((s, b["why"]))
            print(f"  {hms(clock.to_stream(s))}: {b['why']}")
    pad = pc["locate_pad_s"]
    return [(max(0.0, s - pad), s + BLOCK_S + pad) for s, _ in found]


def pick_stretch(sdir: Path, cfg: dict, a: float, b: float, about: str, picked: list[dict],
                 words: list[dict], sig: dict | None, claude: Claude, prompts: Prompts,
                 clock: Clock) -> tuple[list[dict], dict]:
    pc, sc = cfg["pick"], cfg["scoring"]
    lead = transcript_lines(words, max(0.0, a - sc["lead_in_s"]), a, speakers=False)
    body = transcript_lines(words, a, b, speakers=False)
    if not body:
        return [], {"note": "no speech in this stretch"}
    near = [p for p in picked if overlap(short_window(p), (a, b)) > 0]
    already = "\n".join(f"- {s:.1f}-{e:.1f} s: {p['title']}"
                        for p in near for s, e in [short_window(p)]) or "(none)"
    signals = ""
    if sig is not None:
        signals = f"<signals>\n{signal_table(sig, int(a), int(min(b, len(sig['heat']))), 30)}\n</signals>\n\n"
    user = (f"Request: {about or 'the best Shorts in this stretch'}\n"
            f"Stretch: {a:.0f}-{b:.0f} s (stream clock {hms(clock.to_stream(a))}-"
            f"{hms(clock.to_stream(b))})\n\n"
            f"<already_picked>\n{already}\n</already_picked>\n\n"
            "<lead_in>\n" + "\n".join(lead) + "\n</lead_in>\n\n"
            "<stretch>\n" + "\n".join(body) + "\n</stretch>\n\n" + signals)
    out, meta = claude.json_call(pc["model"], prompts.get("pick"), user, PICK_SCHEMA,
                                 pc["effort"], fallback=True)
    if out is None:
        return [], {"note": f"model refused ({meta.get('refusal')})", "api": meta}
    span = [w for w in words if a - sc["lead_in_s"] <= w["start"] < b + 60]
    clips = []
    for c in out["clips"][:pc["max_clips"]]:
        if not (a - 120 <= c["short_in_s"] < c["short_out_s"] <= b + 120):
            print(f"  skipped a moment outside the stretch ({c['short_in_s']:.0f}-{c['short_out_s']:.0f} s)")
            continue
        rec = dict(c)
        for x, y in (("in_s", "out_s"), ("short_in_s", "short_out_s")):
            rec[x], rec[y] = snap(span, c[x], "start"), snap(span, c[y], "end")
        if rec["out_s"] <= rec["in_s"]:
            rec["in_s"], rec["out_s"] = rec["short_in_s"], rec["short_out_s"]
        rec["duration_s"] = round(rec["out_s"] - rec["in_s"], 2)
        rec["short_duration_s"] = round(rec["short_out_s"] - rec["short_in_s"], 2)
        heat = 0.0
        if sig is not None:
            lo, hi = int(rec["short_in_s"]), max(int(rec["short_in_s"]) + 1, int(rec["short_out_s"]))
            vals = [h for h in sig["heat"][lo:hi] if not math.isnan(h)]
            heat = float(max(vals)) if vals else 0.0
        rec["heat"] = heat
        clips.append(rec)
    return clips, {"note": out["note"], "api": meta}


# ---------- stage ----------

def stretches_asked(pc: dict, clock: Clock, duration: float, about: str, start: str | None,
                    end: str | None, at: str | None) -> list[tuple[float, float]]:
    """The stretches (video clock) the command names: --at with [pick] span_s around it, or
    --from/--to. None with only --about: locate() finds them."""
    stretches: list[tuple[float, float]] = []
    if at:
        t = clock.to_video(parse_time(at))
        stretches = [(max(0.0, t - pc["span_s"] / 2), t + pc["span_s"] / 2)]
    elif start or end:
        if not (start and end):
            raise ClipperError("give both --from and --to (or --at)")
        a, b = clock.to_video(parse_time(start)), clock.to_video(parse_time(end))
        if b <= a:
            raise ClipperError("--to must be after --from")
        stretches = [(a, b)]
    elif not about:
        raise ClipperError("say where (--from/--to or --at) or what (--about)")
    stretches = [(a, min(b, duration)) for a, b in stretches]
    for a, b in stretches:
        if b - a > pc["max_span_s"]:
            raise ClipperError(f"{hms(b - a)} is longer than [pick] max_span_s ({hms(pc['max_span_s'])}); "
                             "narrow it or raise the key")
    return stretches


def keep_new(clips: list[dict], info: dict, others: list[dict], cfg: dict, clock: Clock,
             allow_overlap: bool) -> list[dict]:
    """The moments that aren't already picks (more than [pick] max_overlap of their Short
    window), each printed; others: the picks so far, the kept ones are added to it."""
    kept = []
    for c in clips:
        win = short_window(c)
        dup = next((p for p in others if overlap(win, short_window(p)) > cfg["pick"]["max_overlap"]), None)
        line = (f"{hms(clock.to_stream(win[0]))}  {c['short_duration_s']:4.0f}s  "
                f"[{c['category']}, {c['score']}]  {c['title']}")
        if dup and not allow_overlap:
            print(f"  - {line}\n      already a pick: {dup.get('key', 'another new one')} "
                  f"({dup['title']}); --allow-overlap keeps it")
            continue
        if c["source_audio_s"] > cfg["scoring"]["max_source_audio_s"]:
            line += f"  ({c['source_audio_s']:.0f} s of played-video audio)"
        print(f"  + {line}")
        kept.append({**c, "api": info["api"]})
        others.append(kept[-1])
    return kept


def save_picks(stream_dir: Path, cands: dict, found: list[dict], pc: dict, about: str) -> list[str]:
    """Append the new moments to candidates.json as Pass A records and Pass B picks (a copy
    of the file as it was is kept); their new ids."""
    cands_path = stream_dir / "candidates.json"
    picks = cands["pass_b"]["picks"]
    ids = next_ids(cands["pass_a"], len(found))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (stream_dir / f"candidates.before_pick_{stamp}.json").write_text(
        cands_path.read_text(encoding="utf-8"), encoding="utf-8")
    for pid, c in zip(ids, found):
        cands["pass_a"].append({
            "id": pid, "source": "hand", "window_rank": None,
            "window": [c["in_s"], c["out_s"]], "heat": c.pop("heat"), "pass": "A",
            "model": pc["model"], "prompt_version": f"pick_{pc['prompt_version']}",
            "request": about, "requested_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "status": "kept", "has_clip": True, "third_party_audio": False,
            "casino_gameplay": False, "needs_context": False, **c})
        n = next_number(cands["pass_b"])
        picks.append({"id": pid, "title": c["title"], "hook": c["hook"], "reason": c["why"], "n": n})
        cands["pass_b"].setdefault("numbers", {})[pid] = n
    jsonstate.write(cands_path, cands)
    keys = [f"v{n:02d}_{p['id']}" for n, p in numbered_picks(picks) if p["id"] in ids]
    print(f"added {', '.join(keys)} to candidates.json")
    return ids


def run(stream_dir: Path, cfg: dict, about: str = "", start: str | None = None,
        end: str | None = None, at: str | None = None, clock_use: str = "stream",
        dry_run: bool = False, make: bool = True, notify: bool = True,
        allow_overlap: bool = False) -> list[str]:
    """Returns the new pick ids."""
    cfg = for_stream(cfg, stream_dir)
    pc = cfg["pick"]
    cands_path = stream_dir / "candidates.json"
    if not cands_path.exists():
        raise ClipperError("no candidates.json: run `clipper score` first")
    if not dry_run and (who := worker_busy(stream_dir, cfg)):
        raise ClipperError(f"{stream_dir.name} is being processed by {who}; try again when it's done")
    meta = jsonstate.read(stream_dir / "meta.json", {})
    clock = Clock(meta, clock_use)
    print(f"{stream_dir.name}: {clock.describe()}")
    duration = float(meta.get("duration_s") or 0) or float("inf")
    stretches = stretches_asked(pc, clock, duration, about, start, end, at)

    prompts = Prompts(cfg)
    prompts.version = pc["prompt_version"]
    prompts.vars.update(pick_max_clips=pc["max_clips"], short_min_s=cfg["shorts"]["min_s"],
                        short_max_s=cfg["shorts"]["max_s"])
    prompts.vars.setdefault("audience_viewer", cfg["scoring"]["audience"])  # pre-v4 scoring prompts
    claude = Claude(cfg)
    preflight(claude, pc["model"])
    if not stretches:
        print(f"looking for {about!r} in the stream summary")
        stretches = locate(stream_dir, cfg, about, claude, prompts, clock)
        if not stretches:
            raise ClipperError("nothing in the stream summary matches; try --find with a word "
                             "from it, or give --from/--to")

    cands = jsonstate.read(cands_path, {})
    by_id = {r["id"]: r for r in cands["pass_a"]}
    picks = cands.setdefault("pass_b", {}).setdefault("picks", [])
    picked = [{**by_id[p["id"]], "title": p["title"], "key": f"v{i:02d}_{p['id']}"}
              for i, p in numbered_picks(picks) if p["id"] in by_id]
    words = load_words(stream_dir)
    sig = None
    if (stream_dir / "signals.parquet").exists():
        from .scoring import load_signals
        sig = load_signals(stream_dir)

    found: list[dict] = []
    for a, b in stretches:
        print(f"stretch {hms(clock.to_stream(a))}-{hms(clock.to_stream(b))} "
              f"(video {a:.0f}-{b:.0f} s)")
        clips, info = pick_stretch(stream_dir, cfg, a, b, about, picked + found, words, sig,
                                   claude, prompts, clock)
        print(f"  {info['note']}")
        found += keep_new(clips, info, picked + found, cfg, clock, allow_overlap)
    print(f"model cost ${claude.cost:.3f}")
    if not found or dry_run:
        if found:
            print(f"--dry-run: {len(found)} new picks not saved")
        return []

    ids = save_picks(stream_dir, cands, found, pc, about)
    if make:
        make_picks(stream_dir, cfg, ids, notify)
    else:
        print(f"next: clipper cut {stream_dir.name} picks; clipper meta {stream_dir.name} --missing; "
              f"clipper thumbs {stream_dir.name} --missing; clipper shorts {stream_dir.name} "
              f"--items {','.join(ids)}; clipper review publish {stream_dir.name} --only shorts")
    return ids


def make_picks(stream_dir: Path, cfg: dict, ids: list[str], notify: bool = True) -> None:
    """Cut, draft, cover and render the new picks, then publish them for review."""
    from . import cut, meta, shorts, thumbs
    from . import notify as discord_notify

    cut.cut_picks(stream_dir, cfg, ids=ids)
    meta.run(stream_dir, cfg, missing=True)
    thumbs.run(stream_dir, cfg, ids=ids, missing=True)
    shorts.run(stream_dir, cfg, ids=ids)
    discord_notify.publish_and_post(stream_dir, cfg, only=["picks", "shorts"], post_cards=notify)
