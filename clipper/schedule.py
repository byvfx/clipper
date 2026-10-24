"""Release scheduling: approved items -> publish slots -> schedule.json (+ Kitsu).

What gets scheduled, across every stream with review decisions:
    clip         approved shot (Pass B pick)
    short        approved vertical Short of a pick (`clipper shorts`)
    segment      approved topic segment
    compilation  a topic compilation whose segments are all approved
A pre-show (clipper/preshow.py) is a segment too; with [preshow] publish = "asap" it is
placed at the moment of this run as soon as it is approved, outside the slots and spacing
rules, and moves along with each rerun until `clipper upload` publishes it.
Held items (Kitsu "Hold") are skipped. "Publish at" in Kitsu ("2026-10-20 15:00", or a
date for that day's first slot) pins an item; everything else fills the [schedule]
slots: clip/short/segment/compilation days and times, at most max_per_day releases,
min_gap_minutes apart, never before kick_first_hours after the stream ended.

With mix_shows, the shows take turns for each kind's slots (each show keeps its own
order), so one show's backlog can't hold back another's.

Slots already assigned in the future stay put, so reruns only fill gaps; --reshuffle
re-places the automatic ones that aren't uploaded yet. The result is
schedule.json (the upload step reads it) and each item's Kitsu "Scheduled" column.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
KINDS = {"pick": "clip", "segment": "segment", "compilation": "compilation", "short": "short"}
ENTITY = {"clip": "Shot", "segment": "Sequence", "compilation": "Sequence", "short": "Shot"}


def truthy(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes", "on")


def parse_when(text: str, tz: ZoneInfo, first_time: str) -> datetime | None:
    text = (text or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            dt = datetime.combine(dt.date(), time.fromisoformat(first_time))
        return dt.replace(tzinfo=tz)
    return None


def candidates(cfg: dict, streams_dir: Path, sc: dict, tz: ZoneInfo) -> tuple[list[dict], list[dict]]:
    """Approved, un-held items from every stream, plus why the others were left out."""
    from .config import for_stream, show_of_channel, stream_channel

    items, skipped = [], []
    for review_path in sorted(streams_dir.glob("*/review.json")):
        sdir = review_path.parent
        show = show_of_channel(cfg, stream_channel(sdir))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        state = json.loads((sdir / "review_state.json").read_text(encoding="utf-8"))
        meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
        started = datetime.fromisoformat(meta["started_at"])
        earliest = started + timedelta(seconds=meta["duration_s"], hours=sc["kick_first_hours"])
        # A pre-show is already public on the show's own channel: no Kick-first wait.
        asap = (meta.get("kind") == "preshow"
                and for_stream(cfg, sdir)["preshow"]["publish"] == "asap")
        if meta.get("kind") == "preshow":
            earliest = started
        priority = bool(for_stream(cfg, sdir)["schedule"]["priority"])
        approved_segments = set()
        for key, r in review.items():
            kind = KINDS[r["kind"]]
            fields = r.get("fields", {})
            item = {"key": f"{sdir.name}/{key}", "stream": sdir.name, "show": show, "kind": kind,
                    "id": r["id"], "file": str((sdir / r["file"]).resolve()),
                    "title": fields.get("YouTube title") or key,
                    "score": float(fields.get("Score") or 0), "stream_start": started,
                    "earliest": earliest, "entity": (ENTITY[kind], state[key]["entity_id"]),
                    "override_text": fields.get("Publish at") or "", "asap": asap,
                    "priority": priority}
            if r["decision"] != "approved":
                skipped.append({"key": item["key"], "reason": r["decision"]})
            elif truthy(fields.get("Hold")):
                skipped.append({"key": item["key"], "reason": "hold"})
            else:
                items.append(item)
                if kind == "segment":
                    approved_segments.add(r["id"])
        seg_data = json.loads((sdir / "segments.json").read_text(encoding="utf-8"))
        def seg_keys(comp: dict, sdir: Path = sdir, review: dict = review) -> list[str]:
            return [f"{sdir.name}/{k}" for k, r in review.items()
                    if r["kind"] == "segment" and r["id"] in comp["segments"]]
        for comp in seg_data.get("compilations", []):
            key = f"{sdir.name}/comp-{comp['file'].removesuffix('.mp4')}"
            if key.split("/", 1)[1] in review:
                # Published as its own Kitsu item: its decision was used above; it still
                # waits for its segments' slots.
                for it in items:
                    if it["key"] == key:
                        it["segment_keys"] = seg_keys(comp)
                continue
            missing = [s for s in comp["segments"] if s not in approved_segments]
            video = sdir / "compilations" / comp["file"]
            if missing or not video.exists():
                skipped.append({"key": key, "reason": f"segments not approved: {missing}"
                                if missing else "not cut"})
                continue
            items.append({"key": key, "stream": sdir.name, "show": show, "kind": "compilation",
                          "id": comp["topic"], "file": str(video.resolve()),
                          "title": comp["title"], "score": 0, "stream_start": started,
                          "earliest": earliest, "entity": None, "override_text": "",
                          "segment_keys": seg_keys(comp), "priority": priority})
    return items, skipped


def slots(sc: dict, tz: ZoneInfo, start: date) -> list[tuple[datetime, str]]:
    out = []
    for i in range(sc["horizon_days"]):
        day = start + timedelta(days=i)
        name = DAYS[day.weekday()]
        for kind in ("clip", "short", "segment", "compilation"):
            if name in sc[f"{kind}_days"]:
                out += [(datetime.combine(day, time.fromisoformat(t), tz), kind)
                        for t in sc[f"{kind}_times"]]
    return sorted(out)


def fits(when: datetime, taken: list[datetime], sc: dict) -> bool:
    same_day = sum(1 for t in taken if t.date() == when.date())
    gap = timedelta(minutes=sc["min_gap_minutes"])
    return same_day < sc["max_per_day"] and all(abs(t - when) >= gap for t in taken)


def take_turns(items: list[dict]) -> list[dict]:
    """One queue per show, merged so the shows alternate (each keeps its own order)."""
    by_show: dict[str, list[dict]] = {}
    for it in items:
        by_show.setdefault(it["show"], []).append(it)
    queues = list(by_show.values())
    return [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]


def run(cfg: dict, sync_first: bool = True, write_kitsu: bool = True, dry_run: bool = False,
        reshuffle: bool = False) -> dict:
    """reshuffle: place every automatic, not-yet-uploaded entry afresh instead of keeping
    its slot (pinned, pre-show and uploaded entries stay)."""
    sc = cfg["schedule"]
    tz = ZoneInfo(sc["timezone"])
    streams_dir: Path = cfg["paths"]["streams"]
    out_path: Path = cfg["paths"]["schedule"]
    now = datetime.now(tz)
    start = date.fromisoformat(sc["start"]) if sc["start"] else now.date() + timedelta(days=1)

    if sync_first:
        from . import review
        for state in sorted(streams_dir.glob("*/review_state.json")):
            review.sync(state.parent, cfg)

    items, skipped = candidates(cfg, streams_dir, sc, tz)
    old, platforms = {}, {}
    if out_path.exists():
        prev_sched = json.loads(out_path.read_text(encoding="utf-8"))
        old = {e["key"]: e for e in prev_sched["entries"]}
        # Other platforms' records (clipper rumble), kept while an item is unscheduled too.
        platforms = {e["key"]: e["platforms"] for e in prev_sched["entries"]
                     + prev_sched.get("unscheduled", []) if e.get("platforms")}

    taken: list[datetime] = []
    placed: dict[str, dict] = {}

    def place(it: dict, when: datetime, source: str) -> None:
        when = when.replace(second=0, microsecond=0)
        taken.append(when)
        placed[it["key"]] = {"key": it["key"], "stream": it["stream"], "kind": it["kind"],
                             "id": it["id"], "title": it["title"], "file": it["file"],
                             "publish_at": when.isoformat(), "source": source,
                             "status": old.get(it["key"], {}).get("status", "scheduled"),
                             **({"platforms": platforms[it["key"]]} if it["key"] in platforms else {})}

    # 0. Already on YouTube: fixed there, so kept exactly as recorded (with its video id),
    #    even if it has since been held or rejected; that needs a hand in YouTube Studio.
    eligible = {it["key"] for it in items}
    for key, prev in old.items():
        if prev.get("status") == "uploaded":
            taken.append(datetime.fromisoformat(prev["publish_at"]))
            placed[key] = dict(prev)
            if key not in eligible:
                placed[key]["note"] = "uploaded, but no longer approved or now on hold: check YouTube Studio"
                print(f"  note: {key} is uploaded but no longer approved/held")

    # 1. Pinned by you in Kitsu.
    for it in items:
        if it["key"] in placed:
            continue
        first = sc[f"{it['kind']}_times"][0]
        when = parse_when(it["override_text"], tz, first)
        if it["override_text"] and not when:
            skipped.append({"key": it["key"], "reason": f"bad Publish at {it['override_text']!r}"})
            it["bad"] = True
        elif when:
            if not fits(when, taken, sc):
                print(f"  note: {it['key']} pinned at {when:%a %m-%d %H:%M} breaks the spacing rules")
            place(it, when, "pinned")
    # 1b. Pre-shows: out as soon as they are approved (the next upload publishes them).
    for it in items:
        if it["key"] not in placed and it.get("asap") and not it.get("bad"):
            place(it, max(now, it["earliest"]), "asap")
    # 2. Keep earlier assignments that are still ahead of us (or already uploaded). A newly
    #    approved priority item frees the automatic slots of other streams' items of its kind.
    jump = {it["kind"] for it in items if it["priority"] and it["key"] not in old}
    for it in items:
        prev = old.get(it["key"])
        if it["key"] in placed or it.get("bad") or not prev or (prev["source"] == "auto" and (
                reshuffle or (it["kind"] in jump and not it["priority"]))):
            continue
        when = datetime.fromisoformat(prev["publish_at"]).astimezone(tz)
        if when > now and when >= it["earliest"]:
            place(it, when, prev["source"] if prev["source"] != "pinned" else "auto")
    # 3. Fill open slots in order.
    def order(it: dict):
        if it["kind"] in ("clip", "short") and sc["clip_order"] == "score":
            return (-it["score"], it["stream_start"], it["key"])
        return (it["stream_start"], it["key"])
    queue = {}
    for k in ("clip", "short", "segment", "compilation"):
        waiting = [it for it in items if it["kind"] == k and it["key"] not in placed and not it.get("bad")]
        parts = [sorted((it for it in waiting if it["priority"] == p), key=order) for p in (True, False)]
        queue[k] = [it for q in parts for it in (take_turns(q) if sc["mix_shows"] else q)]
    all_slots = slots(sc, tz, start)

    def fill(kinds: tuple[str, ...]) -> None:
        for when, kind in all_slots:
            if kind not in kinds or when <= now or not queue[kind] or not fits(when, taken, sc):
                continue
            pick = next((it for it in queue[kind] if it["earliest"] <= when), None)
            if pick:
                queue[kind].remove(pick)
                place(pick, when, "auto")

    fill(("clip", "short", "segment"))
    # Compilations go out only after every one of their segments has.
    for it in list(queue["compilation"]):
        parts = [placed.get(k) for k in it["segment_keys"]]
        if not all(parts):
            queue["compilation"].remove(it)
            skipped.append({"key": it["key"], "reason": "a segment has no slot yet"})
            continue
        last = max(datetime.fromisoformat(p["publish_at"]) for p in parts)
        it["earliest"] = max(it["earliest"], last + timedelta(minutes=1))
    fill(("compilation",))
    for kind, rest in queue.items():
        skipped += [{"key": it["key"], "reason": f"no free {kind} slot in {sc['horizon_days']} days"}
                    for it in rest]
    for key, plats in platforms.items():
        if key in placed:
            continue
        hit = next((s for s in skipped if s["key"] == key), None)
        if not hit:
            skipped.append(hit := {"key": key, "reason": "no longer in review"})
        hit["platforms"] = plats
        up = [p for p, r in plats.items() if r.get("status") == "uploaded"]
        if up:
            print(f"  note: {key} is on {', '.join(up)} but no longer scheduled ({hit['reason']})")

    entries = sorted(placed.values(), key=lambda e: e["publish_at"])
    result = {"timezone": sc["timezone"], "generated_at": now.isoformat(timespec="seconds"),
              "entries": entries, "unscheduled": skipped}
    if not dry_run:
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        if write_kitsu:
            push_to_kitsu(cfg, items, placed, tz)

    print(f"{len(entries)} scheduled, {len(skipped)} not scheduled -> {out_path}"
          + (" (dry run)" if dry_run else ""))
    for e in entries:
        print(f"  {entry_line(e, tz)}")
    return result


def upcoming(cfg: dict, days: int = 7) -> list[dict]:
    """schedule.json entries releasing in the next `days` days (no replanning)."""
    path: Path = cfg["paths"]["schedule"]
    if not path.exists():
        return []
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = datetime.now(tz)
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    return [e for e in entries
            if now <= datetime.fromisoformat(e["publish_at"]) <= now + timedelta(days=days)]


def entry_line(e: dict, tz: ZoneInfo) -> str:
    t = datetime.fromisoformat(e["publish_at"]).astimezone(tz)
    up = " (uploaded)" if e.get("status") == "uploaded" else ""
    return f"{t:%a %m-%d %H:%M}  {e['kind']:<11} {e['source']:<6} {e['title'][:70]}{up}"


def show(cfg: dict, days: int = 7) -> list[dict]:
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    entries = upcoming(cfg, days)
    print(f"{len(entries)} releases in the next {days} days ({cfg['paths']['schedule']})")
    for e in entries:
        print(f"  {entry_line(e, tz)}")
    return entries


def push_to_kitsu(cfg: dict, items: list[dict], placed: dict, tz: ZoneInfo) -> None:
    """Write each item's slot to its Kitsu "Scheduled" column (blank when unscheduled)."""
    from . import review
    from .config import for_stream

    gazu = review.connect(cfg["review"])
    by_entity = {it["entity"]: it for it in items if it["entity"]}
    # Also clear entities that were scheduled before but are no longer eligible.
    streams_dir: Path = cfg["paths"]["streams"]
    for state_path in streams_dir.glob("*/review_state.json"):
        for key, st in json.loads(state_path.read_text(encoding="utf-8")).items():
            ent = (ENTITY[KINDS[st["kind"]]], st["entity_id"])
            by_entity.setdefault(ent, {"key": f"{state_path.parent.name}/{key}", "entity": ent})
    # Column ids differ per Kitsu production, and each show has its own production.
    fmaps: dict[str, dict] = {}

    def fmap_of(stream: str) -> dict:
        name = review.project_name(for_stream(cfg, streams_dir / stream))
        if name not in fmaps:
            fmaps[name] = review.column_fields(gazu, gazu.project.get_project_by_name(name))
        return fmaps[name]

    for (etype, eid), it in by_entity.items():
        get, update = ((gazu.shot.get_sequence, gazu.shot.update_sequence_data)
                       if etype == "Sequence" else (gazu.shot.get_shot, gazu.shot.update_shot_data))
        field = fmap_of(it["key"].split("/", 1)[0]).get((etype, "Scheduled"))
        if not field:
            continue
        e = placed.get(it["key"])
        value = (f"{datetime.fromisoformat(e['publish_at']).astimezone(tz):%Y-%m-%d %H:%M}"
                 if e else "")
        if ((get(eid).get("data") or {}).get(field) or "") != value:
            update(eid, {field: value})
