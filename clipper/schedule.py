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

stream_budget caps how many of each kind one stream releases (scaled by its length against
budget_hours): the best go, Shorts by score and segments and compilations by mean chat
heat over their span; pinned, pre-show and uploaded items count first. stale_days drops
whatever hasn't gone out that many days after its first allowed slot, so releases stay
current instead of piling into a backlog.

With mix_shows, the shows take turns for each kind's slots (each show keeps its own
order), so one show's backlog can't hold back another's.

Slots already assigned in the future stay put, so reruns only fill gaps; --reshuffle
re-places the automatic ones that aren't uploaded yet. The result is
schedule.json (the upload step reads it) and each item's Kitsu "Scheduled" column.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonstate
from .states import ENTITY, RELEASE_KIND, split_key

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Unscheduled reasons for items that won't go out at all (not just waiting for a slot).
DROPPED = ("stale", "over the stream's")


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


def budget_for(ssc: dict, hours: float) -> dict[str, int]:
    """A stream's per-kind release budget, scaled by its length (at least 1 of each)."""
    scale = hours / ssc["budget_hours"]
    return {k: max(1, round(n * scale)) for k, n in (ssc["stream_budget"] or {}).items()}


def segment_heat(sdir: Path, segments: list[dict]) -> dict[str, float]:
    """Mean chat heat (signals.parquet) over each segment's span, by segment id."""
    import numpy as np
    import pyarrow.parquet as pq

    path = sdir / "signals.parquet"
    if not path.exists():
        return {}
    heat = np.nan_to_num(pq.read_table(path, columns=["heat"]).column("heat").to_numpy(), nan=0.0)
    return {s["id"]: float(heat[int(s["start_s"]):int(s["end_s"])].mean())
            for s in segments if int(s["end_s"]) > int(s["start_s"])}


def candidates(cfg: dict, streams_dir: Path, sc: dict, tz: ZoneInfo) -> tuple[list[dict], list[dict]]:
    """Approved, un-held items from every stream, plus why the others were left out."""
    from .config import for_stream, show_of_channel, stream_channel

    items, skipped = [], []
    for review_path in sorted(streams_dir.glob("*/review.json")):
        sdir = review_path.parent
        show = show_of_channel(cfg, stream_channel(sdir))
        review = jsonstate.load(review_path)
        state = jsonstate.load(sdir / "review_state.json")
        meta = jsonstate.load(sdir / "meta.json")
        started = datetime.fromisoformat(meta["started_at"])
        ssc = for_stream(cfg, sdir)["schedule"]  # a stream's config.toml may change these
        earliest = started + timedelta(seconds=meta["duration_s"], hours=ssc["kick_first_hours"])
        # A pre-show is already public on the show's own channel: no Kick-first wait.
        asap = (meta.get("kind") == "preshow"
                and for_stream(cfg, sdir)["preshow"]["publish"] == "asap")
        if meta.get("kind") == "preshow":
            earliest = started
        priority = bool(ssc["priority"])
        budget = budget_for(ssc, meta["duration_s"] / 3600)
        expires = (earliest + timedelta(days=ssc["stale_days"])
                   if ssc["stale_days"] and not asap else None)
        seg_data = jsonstate.load(sdir / "segments.json")
        # Only read for streams that are still current and have a budget to rank against.
        heat = (segment_heat(sdir, seg_data.get("segments", []))
                if {"segment", "compilation"} & budget.keys()
                and (not expires or expires > datetime.now(tz)) else {})
        approved_segments = set()
        for key, r in review.items():
            kind = RELEASE_KIND[r["kind"]]
            fields = r.get("fields", {})
            score = fields.get("Score") or (heat.get(r["id"], 0) if kind == "segment" else 0)
            item = {"key": f"{sdir.name}/{key}", "stream": sdir.name, "show": show, "kind": kind,
                    "id": r["id"], "file": str((sdir / r["file"]).resolve()),
                    "title": fields.get("YouTube title") or key,
                    "score": float(score), "stream_start": started,
                    "earliest": earliest, "entity": (ENTITY[r["kind"]], state[key]["entity_id"]),
                    "override_text": fields.get("Publish at") or "", "asap": asap,
                    "priority": priority, "budget": budget.get(kind), "expires": expires}
            if r["decision"] != "approved":
                skipped.append({"key": item["key"], "reason": r["decision"]})
            elif truthy(fields.get("Hold")):
                skipped.append({"key": item["key"], "reason": "hold"})
            else:
                items.append(item)
                if kind == "segment":
                    approved_segments.add(r["id"])
        def seg_keys(comp: dict, sdir: Path = sdir, review: dict = review) -> list[str]:
            return [f"{sdir.name}/{k}" for k, r in review.items()
                    if r["kind"] == "segment" and r["id"] in comp["segments"]]
        for comp in seg_data.get("compilations", []):
            key = f"{sdir.name}/comp-{comp['file'].removesuffix('.mp4')}"
            comp_heat = [heat[s] for s in comp["segments"] if s in heat]
            comp_score = sum(comp_heat) / len(comp_heat) if comp_heat else 0
            if split_key(key)[1] in review:
                # Published as its own Kitsu item: its decision was used above; it still
                # waits for its segments' slots.
                for it in items:
                    if it["key"] == key:
                        it["segment_keys"] = seg_keys(comp)
                        it["score"] = it["score"] or comp_score
                continue
            missing = [s for s in comp["segments"] if s not in approved_segments]
            video = sdir / "compilations" / comp["file"]
            if missing or not video.exists():
                skipped.append({"key": key, "reason": f"segments not approved: {missing}"
                                if missing else "not cut"})
                continue
            items.append({"key": key, "stream": sdir.name, "show": show, "kind": "compilation",
                          "id": comp["topic"], "file": str(video.resolve()),
                          "title": comp["title"], "score": comp_score, "stream_start": started,
                          "earliest": earliest, "entity": None, "override_text": "",
                          "segment_keys": seg_keys(comp), "priority": priority,
                          "budget": budget.get("compilation"), "expires": expires})
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
        reshuffle: bool = False, kitsu_all: bool = False) -> dict:
    """reshuffle: place every automatic, not-yet-uploaded entry afresh instead of keeping
    its slot (pinned, pre-show and uploaded entries stay). kitsu_all: read every item's
    Scheduled column in Kitsu, not only the ones whose slot changed (push_to_kitsu)."""
    sc = cfg["schedule"]
    tz = ZoneInfo(sc["timezone"])
    streams_dir: Path = cfg["paths"]["streams"]
    out_path: Path = cfg["paths"]["schedule"]
    now = datetime.now(tz)
    start = date.fromisoformat(sc["start"]) if sc["start"] else now.date() + timedelta(days=1)

    if sync_first and dry_run:  # a sync writes review.json and can post Kitsu comments
        print("dry run: decisions as last synced (review.json), Kitsu not read")
    elif sync_first:
        from . import review
        for state in sorted(streams_dir.glob("*/review_state.json")):
            review.sync(state.parent, cfg)

    items, skipped = candidates(cfg, streams_dir, sc, tz)
    # Locked from reading the old plan to writing the new one, so an upload recorded
    # meanwhile can't be planned back to "scheduled" (and uploaded twice).
    with jsonstate.locked(out_path):
        old, platforms = previous(out_path)
        placed, skipped = plan(items, skipped, old, platforms, sc, tz, now, start, reshuffle)
        entries = sorted(placed.values(), key=lambda e: e["publish_at"])
        result = {"timezone": sc["timezone"], "generated_at": now.isoformat(timespec="seconds"),
                  "entries": entries, "unscheduled": skipped}
        if not dry_run:
            jsonstate.write(out_path, result)
    if not dry_run and write_kitsu:
        push_to_kitsu(cfg, items, placed, tz, every=kitsu_all)

    print(f"{len(entries)} scheduled, {len(skipped)} not scheduled -> {out_path}"
          + (" (dry run)" if dry_run else ""))
    for e in entries:
        print(f"  {entry_line(e, tz)}")
    return result


def previous(out_path: Path) -> tuple[dict, dict]:
    """The last plan's entries by key, and other platforms' records by key."""
    old, platforms = {}, {}
    if out_path.exists():
        prev_sched = jsonstate.load(out_path)
        old = {e["key"]: e for e in prev_sched["entries"]}
        # Other platforms' records (clipper rumble), kept while an item is unscheduled too.
        platforms = {e["key"]: e["platforms"] for e in prev_sched["entries"]
                     + prev_sched.get("unscheduled", []) if e.get("platforms")}
    return old, platforms


class Placed:
    """The plan so far: entries by key, and the times taken (for the spacing rules)."""

    def __init__(self, old: dict, platforms: dict):
        self.old, self.platforms = old, platforms
        self.taken: list[datetime] = []
        self.entries: dict[str, dict] = {}

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def place(self, it: dict, when: datetime, source: str) -> None:
        when = when.replace(second=0, microsecond=0)
        self.taken.append(when)
        prev = self.old.get(it["key"], {})
        self.entries[it["key"]] = {
            "key": it["key"], "stream": it["stream"], "kind": it["kind"],
            "id": it["id"], "title": it["title"], "file": it["file"],
            "publish_at": when.isoformat(), "source": source,
            "status": prev.get("status", "scheduled"),
            # the last upload failure (youtube.upload_one) until one works
            **{k: prev[k] for k in ("last_error", "last_error_at") if k in prev},
            **({"platforms": self.platforms[it["key"]]} if it["key"] in self.platforms else {})}


def plan(items: list[dict], skipped: list[dict], old: dict, platforms: dict, sc: dict,
         tz: ZoneInfo, now: datetime, start: date, reshuffle: bool) -> tuple[dict, list]:
    """Place items into slots: (entries by key, the ones left out)."""
    p = Placed(old, platforms)
    keep_uploaded(p, items)
    place_pinned(p, items, skipped, sc, tz, now)
    place_asap(p, items, now)
    over_budget(p, items, skipped)
    jump = keep_earlier(p, items, tz, now, reshuffle)
    queue = queues(p, items, skipped, sc, tz, now)
    # Freed slots can be today's: refill from today then, or they stay empty. With
    # stale_days, today's open slots count too: windows are short.
    all_slots = slots(sc, tz, min(start, now.date()) if jump or sc["stale_days"] else start)
    fill(p, queue, all_slots, ("clip", "short", "segment"), sc, now)
    after_segments(p, items, queue, skipped)
    fill(p, queue, all_slots, ("compilation",), sc, now)
    for kind, rest in queue.items():
        skipped += [{"key": it["key"], "reason":
                     f"stale: no free {kind} slot by {it['expires'].astimezone(tz):%m-%d %H:%M}"
                     if it.get("expires")
                     else f"no free {kind} slot in {sc['horizon_days']} days"} for it in rest]
    keep_platforms(p, platforms, skipped)
    return p.entries, skipped


def current(it: dict, when: datetime) -> bool:
    """Not past its stale_days cutoff at that time."""
    return not it.get("expires") or when <= it["expires"]


def keep_uploaded(p: Placed, items: list[dict]) -> None:
    """0. Already on YouTube: fixed there, so kept exactly as recorded (with its video id),
    even if it has since been held or rejected; that needs a hand in YouTube Studio."""
    eligible = {it["key"] for it in items}
    for key, prev in p.old.items():
        if prev.get("status") == "uploaded":
            p.taken.append(datetime.fromisoformat(prev["publish_at"]))
            p.entries[key] = dict(prev)
            if key not in eligible:
                p.entries[key]["note"] = "uploaded, but no longer approved or now on hold: check YouTube Studio"
                print(f"  note: {key} is uploaded but no longer approved/held")


def place_pinned(p: Placed, items: list[dict], skipped: list[dict], sc: dict, tz: ZoneInfo,
                 now: datetime) -> None:
    """1. Pinned by you in Kitsu. A Publish at that doesn't parse marks the item bad."""
    for it in items:
        if it["key"] in p:
            continue
        first = sc[f"{it['kind']}_times"][0]
        when = parse_when(it["override_text"], tz, first)
        if it["override_text"] and not when:
            skipped.append({"key": it["key"], "reason": f"bad Publish at {it['override_text']!r}"})
            it["bad"] = True
        elif when and when <= now:  # #77 M7: a pin that has passed would sit there forever
            print(f"  note: {it['key']} was pinned for {when:%a %m-%d %H:%M}, which has passed: "
                  "it takes the next free slot (clear or change Publish at in Kitsu)")
        elif when:
            if not fits(when, p.taken, sc):
                print(f"  note: {it['key']} pinned at {when:%a %m-%d %H:%M} breaks the spacing rules")
            p.place(it, when, "pinned")


def place_asap(p: Placed, items: list[dict], now: datetime) -> None:
    """1b. Pre-shows: out as soon as they are approved (the next upload publishes them)."""
    for it in items:
        if it["key"] not in p and it.get("asap") and not it.get("bad"):
            p.place(it, max(now, it["earliest"]), "asap")


def over_budget(p: Placed, items: list[dict], skipped: list[dict]) -> None:
    """1c. Each stream's budget per kind: the best go; what's placed already counts first.
    The rest are marked "over"."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        if it.get("budget") is not None and not it.get("bad"):
            groups.setdefault((it["stream"], it["kind"]), []).append(it)
    for (_, kind), group in groups.items():
        room = group[0]["budget"] - sum(it["key"] in p for it in group)
        rest = sorted((it for it in group if it["key"] not in p),
                      key=lambda it: (-it["score"], it["key"]))
        for it in rest[max(room, 0):]:
            it["over"] = True
            skipped.append({"key": it["key"],
                            "reason": f"over the stream's {kind} budget ({group[0]['budget']})"})


def keep_earlier(p: Placed, items: list[dict], tz: ZoneInfo, now: datetime,
                 reshuffle: bool) -> set[str]:
    """2. Keep earlier assignments that are still ahead of us (or already uploaded) and
    current. A newly approved priority item frees the automatic slots of other streams'
    items of its kind. Returns those kinds."""
    jump = {it["kind"] for it in items if it["priority"] and it["key"] not in p.old}
    for it in items:
        prev = p.old.get(it["key"])
        if it["key"] in p or it.get("bad") or it.get("over") or not prev or (prev["source"] == "auto" and (
                reshuffle or (it["kind"] in jump and not it["priority"]))):
            continue
        when = datetime.fromisoformat(prev["publish_at"]).astimezone(tz)
        if when > now and when >= it["earliest"] and current(it, when):
            p.place(it, when, prev["source"] if prev["source"] != "pinned" else "auto")
    return jump


def queues(p: Placed, items: list[dict], skipped: list[dict], sc: dict, tz: ZoneInfo,
           now: datetime) -> dict[str, list[dict]]:
    """3. What still needs a slot, per kind, in the order it gets one: priority items
    first, then [schedule] clip_order, the shows taking turns with mix_shows."""
    def order(it: dict):
        if it["kind"] in ("clip", "short") and sc["clip_order"] == "score":
            return (-it["score"], it["stream_start"], it["key"])
        return (it["stream_start"], it["key"])
    queue = {}
    for k in ("clip", "short", "segment", "compilation"):
        waiting = [it for it in items if it["kind"] == k and it["key"] not in p
                   and not it.get("bad") and not it.get("over")]
        # Out of date already: leave them out of the queues, or the shows' turns skew
        # toward whichever show has fewer of them.
        for it in [it for it in waiting if not current(it, now)]:
            waiting.remove(it)
            skipped.append({"key": it["key"],
                            "reason": f"stale: not out by {it['expires'].astimezone(tz):%m-%d %H:%M}"})
        parts = [sorted((it for it in waiting if it["priority"] == pr), key=order) for pr in (True, False)]
        queue[k] = [it for q in parts for it in (take_turns(q) if sc["mix_shows"] else q)]
    return queue


def fill(p: Placed, queue: dict[str, list[dict]], all_slots: list[tuple[datetime, str]],
         kinds: tuple[str, ...], sc: dict, now: datetime) -> None:
    """Open slots of these kinds, in time order, each to the first queued item allowed
    and still current then."""
    for when, kind in all_slots:
        if kind not in kinds or when <= now or not queue[kind] or not fits(when, p.taken, sc):
            continue
        pick = next((it for it in queue[kind]
                     if it["earliest"] <= when and current(it, when)), None)
        if pick:
            queue[kind].remove(pick)
            p.place(pick, when, "auto")


def after_segments(p: Placed, items: list[dict], queue: dict[str, list[dict]],
                   skipped: list[dict]) -> None:
    """Compilations go out only after every one of their segments has, except segments that
    won't go at all (over budget or stale): the compilation is where those get seen."""
    let_go = {it["key"] for it in items if it.get("over")} | {
        it["key"] for it in queue["segment"] if it.get("expires")}
    for it in list(queue["compilation"]):
        keys = it["segment_keys"]
        if any(k not in p and k not in let_go for k in keys):
            queue["compilation"].remove(it)
            skipped.append({"key": it["key"], "reason": "a segment has no slot yet"})
            continue
        last = max((datetime.fromisoformat(p.entries[k]["publish_at"]) for k in keys if k in p),
                   default=None)
        if last:
            it["earliest"] = max(it["earliest"], last + timedelta(minutes=1))


def keep_platforms(p: Placed, platforms: dict, skipped: list[dict]) -> None:
    """Other platforms' records stay on an item that lost its slot (or left review)."""
    for key, plats in platforms.items():
        if key in p:
            continue
        hit = next((s for s in skipped if s["key"] == key), None)
        if not hit:
            skipped.append(hit := {"key": key, "reason": "no longer in review"})
        hit["platforms"] = plats
        up = [name for name, r in plats.items() if r.get("status") == "uploaded"]
        if up:
            print(f"  note: {key} is on {', '.join(up)} but no longer scheduled ({hit['reason']})")


def upcoming(cfg: dict, days: int = 7) -> list[dict]:
    """schedule.json entries releasing in the next `days` days (no replanning)."""
    path: Path = cfg["paths"]["schedule"]
    if not path.exists():
        return []
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = datetime.now(tz)
    entries = jsonstate.load(path)["entries"]
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


def needs_push(value: str, seen: str | None, last: dict | None, eid: str) -> bool:
    """Whether an item's Scheduled column has to be read and maybe written. seen: the
    column as the last sync read it into review.json (None: never read); last: what an
    earlier push wrote there ({"entity_id", "value", "over": seen at that time})."""
    known = seen  # what the column shows, as far as we know
    if last and last.get("entity_id") == eid and last.get("over") == seen:
        known = last.get("value")  # we wrote it after that sync, and none has run since
    return known is None or known != value


def push_to_kitsu(cfg: dict, items: list[dict], placed: dict, tz: ZoneInfo,
                  every: bool = False) -> None:
    """Write each item's slot to its Kitsu "Scheduled" column (blank when unscheduled).
    Only the slots that changed: what the column shows is known from the last sync
    (review.json) or, when a push wrote it after that sync, from schedule_kitsu.json
    (beside schedule.json); an item whose column already shows its slot needs no Kitsu
    call. A value someone typed into the column is put right once a sync has read it.
    every: read every item's column from Kitsu and write the ones that differ."""
    from . import review
    from .config import for_stream

    by_entity = {it["entity"]: it for it in items if it["entity"]}
    # Also clear entities that were scheduled before but are no longer eligible.
    streams_dir: Path = cfg["paths"]["streams"]
    seen: dict[str, str] = {}  # item key -> Scheduled as the last sync read it
    for state_path in streams_dir.glob("*/review_state.json"):
        sname = state_path.parent.name
        for key, st in jsonstate.load(state_path).items():
            ent = (ENTITY[st["kind"]], st["entity_id"])
            by_entity.setdefault(ent, {"key": f"{sname}/{key}", "entity": ent})
        for key, r in jsonstate.read(state_path.with_name("review.json"), {}).items():
            if "Scheduled" in (r.get("fields") or {}):
                seen[f"{sname}/{key}"] = r["fields"]["Scheduled"] or ""
    memo_path = cfg["paths"]["schedule"].with_name("schedule_kitsu.json")
    memo = jsonstate.read(memo_path, {})
    todo = []
    for (etype, eid), it in by_entity.items():
        e = placed.get(it["key"])
        value = (f"{datetime.fromisoformat(e['publish_at']).astimezone(tz):%Y-%m-%d %H:%M}"
                 if e else "")
        if every or needs_push(value, seen.get(it["key"]), memo.get(it["key"]), eid):
            todo.append((etype, eid, it["key"], value))
    if not todo:
        return
    gazu = review.connect(cfg["review"])
    # Column ids differ per Kitsu production, and each show has its own production.
    fmaps: dict[str, dict] = {}

    def fmap_of(stream: str) -> dict:
        name = review.project_name(for_stream(cfg, streams_dir / stream))
        if name not in fmaps:
            fmaps[name] = review.column_fields(gazu, gazu.project.get_project_by_name(name))
        return fmaps[name]

    done: dict[str, dict] = {}  # what the column now shows, for the next run
    wrote = 0
    try:
        for etype, eid, key, value in todo:
            field = fmap_of(split_key(key)[0]).get((etype, "Scheduled"))
            if not field:
                continue
            if (review.entity_data(gazu, etype, eid).get(field) or "") != value:
                review.write_data(gazu, etype, eid, {field: value})
                wrote += 1
            done[key] = {"entity_id": eid, "value": value, "over": seen.get(key)}
    finally:
        if done:  # kept even when a call failed part way, so those aren't read again
            keys = {it["key"] for it in by_entity.values()}
            with jsonstate.update(memo_path, {}) as m:
                m.update(done)
                for k in [k for k in m if k not in keys]:  # no longer in review
                    del m[k]
        print(f"Kitsu Scheduled: {wrote} written, {len(done)} read, "
              f"{len(by_entity) - len(todo)} unchanged since the last sync or push")
