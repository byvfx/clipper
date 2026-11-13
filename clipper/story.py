"""`clipper story` (#84), phase 1: a news brief in, a research dossier out.

Usage:
    clipper story new "<brief>" [--slug X] [--days N | --since DATE] [--before DATE]
                                [--person NAME]... [--url URL]... [--max-videos N] [--max-hours H]
                                [--dry-run]
    clipper story run <slug> [--from swarm|triage|ingest|find|dossier] [--dry-run]
    clipper story add <slug> <url>...   videos to ingest whatever triage says, then run --from triage
    clipper story status [<slug>]
    clipper story trail <slug>          the swarm's task tree: who found what, from which search
    clipper story show <slug>           the dossier, re-rendered from dossier.json (no model call)

A story is the archive subject paths.archive/story-<slug>/ (catalog.json, one folder per
item, index.sqlite, as `clipper archive` keeps them) plus its own files: brief.json,
swarm.json (the trail), triage.json, moments.json, dossier.json and dossier.md.

The steps, each skipped when done (--from reruns one and everything after it):
1. Brief (code, no model): the people named in it, from studio/research_notes/streamers.json
   ([paths] streamers) and [story] people, matched by name, nickname or slug; their channels
   seed the swarm (YouTube, Kick, Odysee; Rumble and Twitch are context only). --person adds
   one the brief doesn't name. The window is --since/--before, else the last [story] days.
2. Swarm (clipper/swarm.py, [story] swarm overrides [swarm]): finds and ranks; nothing is
   downloaded. --url videos are given findings.
3. Triage ([story] triage_model, Haiku): which found videos to transcribe, within
   --max-videos / --max-hours, the people's own uploads and broadcasts first. Our own
   streams (a Kick VOD we recorded, or a segment our_streams found) are linked, not
   transcribed again: free, and outside the limits. Prints the list and the Modal estimate;
   --dry-run stops here.
4. Ingest: `archive` transcription on Modal ([story] gpu, parallel): YouTube audio, Kick
   VOD audio, Odysee; then the subject's search index. A failed item stops the run here (a
   dossier without it would read as the whole story): rerun, or --from find without it.
   YouTube's bot check is retried signed in, as pre-shows do ([story] youtube_bot_retry).
5. Find ([story] find_model, Haiku): search queries for the story, run over the story's index
   and every archive subject the swarm found something in (inside the window there), then
   the model picks the moments: event (it happening), reaction, context. A moment whose
   quote isn't in the transcript is dropped; the quote kept is the transcript's own wording.
6. Dossier ([story] dossier_model, Sonnet): summary, timeline, people, who said what,
   conflicts, coverage, gaps and angles, every claim citing moments (M01) or sources (S01).
   A cite that doesn't resolve is sent back once, then the step fails with the list.
   Speculation is labelled. dossier.json takes hand edits: `notes` (top level) and a
   source's `struck: true` survive a rerun; `story show` re-renders dossier.md.

Phase 2 (`story plan`, `story cut`: clipper/story_edit.py) turns the moments into a video for
review: the edit, the render, meta, thumbs, the Kitsu card; approved, it goes out asap.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import archive, instagram, jsonstate, swarm, ytdl
from .archive import item_dir, load_catalog, save_catalog
from .errors import ClipperError
from .llm import Budget, OverBudget, estimate

STEPS = ["swarm", "triage", "ingest", "find", "dossier"]
ROLES = ["event", "reaction", "context"]
VIDEO = ("yt:", "kick:", "od:", "ig:")
SEED_KINDS = {"youtube": "youtube_channel", "kick": "kick_videos", "odysee": "odysee_channel"}
# a web search gives the site as a video's "channel" (Brave: "YouTube", 11-04): not one
PLATFORMS = {"", "youtube", "kick", "odysee", "rumble", "twitch"}
COLD_START_S = 600   # a Modal container's start before its first item (logged 11-03: 10-11 min on L4)
UNKNOWN_S = 3600   # a video whose length the listing didn't give counts as an hour against --max-hours
UNKNOWN_REEL_S = 90   # ...an Instagram reel as a minute and a half: a reel given by its link has no length yet

TRIAGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["picks"],
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "why"],
        "properties": {"n": {"type": "integer"}, "why": {"type": "string"}}}}},
}
MOMENTS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["picks"],
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "role", "fit", "quote", "why"],
        "properties": {"n": {"type": "integer"}, "role": {"type": "string", "enum": ROLES},
                       "fit": {"type": "string", "enum": ["strong", "maybe"]},
                       "quote": {"type": "string"}, "why": {"type": "string"}}}}},
}
CITES = {"type": "array", "items": {"type": "string"}}
DOSSIER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "timeline", "people", "who_said_what", "conflicts", "coverage", "gaps", "angles"],
    "properties": {
        "summary": {"type": "string"},
        "timeline": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["date", "what", "status", "cites"],
            "properties": {"date": {"type": "string"}, "what": {"type": "string"},
                           "status": {"type": "string", "enum": ["shown", "reported", "speculation"]},
                           "cites": CITES}}},
        "people": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "role"],
            "properties": {"name": {"type": "string"}, "role": {"type": "string"}}}},
        "who_said_what": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["who", "moment", "note"],
            "properties": {"who": {"type": "string"}, "moment": {"type": "string"}, "note": {"type": "string"}}}},
        "conflicts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["what", "cites"],
            "properties": {"what": {"type": "string"}, "cites": CITES}}},
        "coverage": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "angles": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["angle", "moments"],
            "properties": {"angle": {"type": "string"}, "moments": CITES}}},
    },
}


def subject(slug: str) -> str:
    return f"story-{slug}"


def story_dir(cfg: dict, slug: str) -> Path:
    return archive.subject_dir(cfg, subject(slug))


def make_slug(brief: str) -> str:
    words = re.findall(r"[a-z0-9]+", brief.lower())
    keep = [w for w in words if w not in swarm_stop()][:6] or words[:6]
    return "-".join(keep)[:40].strip("-") or "story"


def swarm_stop() -> set[str]:
    from .investigate import STOP

    return STOP


def check_slug(slug: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", slug):
        raise ClipperError(f"story slug {slug!r}: lower-case letters, digits and hyphens, at most 40")
    return slug


def load_brief(cfg: dict, slug: str) -> dict:
    b = jsonstate.read(story_dir(cfg, slug) / "brief.json", None)
    if not b:
        raise ClipperError(f"no story {slug!r} (clipper story new \"<brief>\")")
    return b


# ---------- 1. brief: who and where ----------

def roster(cfg: dict) -> list[dict]:
    """Everyone the story step knows: `clipper people` (people.json, with streamers.json,
    [story] people and the cast library folded in)."""
    from . import people

    return [{"name": r["name"], "slug": pid, "aliases": r["aliases"], "channels": r["channels"]}
            for pid, r in people.load(cfg).items()]


def aliases(person: dict) -> set[str]:
    """"The Quartering (Jer)" -> quartering, jer, quartering jer; the slug's words and the
    person's aliases too."""
    from .people import names

    return names(person, person.get("slug") or "")


def named(brief: str, people: list[dict], extra: list[str] = ()) -> list[dict]:
    """The people the brief names, plus `extra` (--person), by any alias, whole words."""
    from .investigate import canon

    text = f" {canon(brief)} "
    want = {canon(x) for x in extra}
    out = [p for p in people if any(f" {a} " in text for a in aliases(p)) or want & aliases(p)]
    if missing := want - {a for p in out for a in aliases(p)}:
        raise ClipperError(f"--person {', '.join(sorted(missing))}: nobody by that name (clipper people list)")
    return out


def own_channels(p: dict) -> list[tuple[str, str]]:
    """(platform, id) for each of a person's channels: lists from `clipper people`, or one
    string per key in an older brief.json (youtube_live and the like are YouTube)."""
    from .people import platform_of

    out = []
    for key, val in p["channels"].items():
        if platform := platform_of(key):
            out += [(platform, v) for v in ([val] if isinstance(val, str) else val)]
    return out


def seeds(people: list[dict]) -> list[dict]:
    """Each person's own channels, as swarm tasks."""
    out = []
    for p in people:
        for platform, target in own_channels(p):
            if kind := SEED_KINDS.get(platform):
                out.append({"kind": kind, "target": target, "why": f"{p['name']}'s own {platform} channel"})
    return out


def context(b: dict) -> str:
    lines = []
    for p in b["people"]:
        chans = ", ".join(f"{k} {v}" for k, v in own_channels(p))
        lines.append(f"- {p['name']}: {chans or 'no channels on file'}")
    window = f"from {b['since']}" + (f" to before {b['before']}" if b.get("before") else " to today")
    return ((("People:\n" + "\n".join(lines) + "\n") if lines else "")
            + f"Window: {window}.\nWe want the footage: the people's own uploads and broadcasts first, "
              "then clips and reactions that show what happened, then coverage in text.")


def new(cfg: dict, brief: str, slug: str | None = None, days: int | None = None, since: str | None = None,
        before: str | None = None, people: list[str] = (), urls: list[str] = ()) -> str:
    """brief.json for a new story (or the same story again: the brief is kept); its slug."""
    slug = check_slug(slug or make_slug(brief))
    d = story_dir(cfg, slug)
    if (d / "brief.json").exists():
        old = load_brief(cfg, slug)
        if old["brief"] != brief:
            raise ClipperError(f"story {slug!r} already has another brief: --slug a new one, "
                               f"or clipper story run {slug}")
        return slug
    days = cfg["story"]["days"] if days is None else days
    since = since or (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    who = named(brief, roster(cfg), people)
    b = {"slug": slug, "brief": brief, "since": since, "before": before or "",
         "people": [{"name": p["name"], "channels": p["channels"]} for p in who],
         "urls": list(urls), "made_at": datetime.now(UTC).isoformat(timespec="seconds")}
    d.mkdir(parents=True, exist_ok=True)
    jsonstate.write(d / "brief.json", b)
    print(f"story {slug}: {', '.join(p['name'] for p in who) or 'nobody on file named'}; from {since}")
    return slug


def add(cfg: dict, slug: str, urls: list[str]) -> None:
    for u in urls:
        given_item(cfg, u)  # fails now, not at triage, on a url it can't ingest
    d = story_dir(cfg, slug)
    load_brief(cfg, slug)
    with jsonstate.update(d / "brief.json", {}) as b:
        b["urls"] = list(dict.fromkeys([*b.get("urls", []), *urls]))
    print(f"{slug}: {len(urls)} added; clipper story run {slug} --from triage")


# ---------- 2-3. swarm, triage ----------

def run_swarm(cfg: dict, b: dict, d: Path, claude) -> dict:
    days = max(1, (datetime.now(UTC).date() - datetime.fromisoformat(b["since"]).date()).days)
    return swarm.run(cfg, b["brief"], d / "swarm.json", context=context(b), seeds=seeds(b["people"]),
                     overrides=cfg["story"]["swarm"], claude=claude, days=days, since=b["since"],
                     before=b["before"] or None)


def streams_dir(cfg: dict) -> Path:
    return Path(cfg["paths"]["streams"])


def our_stream(cfg: dict, stream_id: str) -> dict | None:
    """One of our recorded streams as a linked item, when it is transcribed."""
    sdir = streams_dir(cfg) / stream_id
    if stream_id.startswith("story-") or not (sdir / "words.jsonl").exists():  # a story's video isn't a source
        return None
    m = jsonstate.read(sdir / "meta.json", {}) or {}
    return {"id": stream_id, "source": "kick", "url": m.get("url") or "", "title": m.get("title") or "",
            "channel": m.get("channel") or "", "date": (m.get("started_at") or "")[:10],
            "duration_s": float(m.get("duration_s") or 0), "dir": str(sdir), "ours": True}


def as_item(f: dict) -> dict | None:
    """A swarm finding as an archive item to transcribe, or None when it isn't a video."""
    k = f.get("key") or swarm.finding_key(f)
    channel = "" if (f.get("channel") or "").lower() in PLATFORMS else f["channel"]
    base = {"title": f.get("title") or "", "channel": channel, "date": f.get("date") or "",
            "duration_s": float(f.get("duration_s") or 0), "via": f.get("why") or "", "key": k}
    if k.startswith("yt:"):
        return {**base, "id": f"yt-{k[3:]}", "source": "youtube", "url": f"https://www.youtube.com/watch?v={k[3:]}"}
    if k.startswith("kick:") and f.get("m3u8") and f.get("id"):
        return {**base, "id": f["id"], "source": "kick", "url": f["url"], "m3u8": f["m3u8"]}
    if k.startswith("od:") and f.get("lbry"):
        return {**base, "id": f"od-{k[3:]}", "source": "odysee", "url": f["lbry"], "page": f["url"]}
    if k.startswith("ig:"):
        return {**base, "id": f"ig-{k[3:]}", "source": "instagram", "url": instagram.post_url(k[3:])}
    return None


def length_s(it: dict) -> float:
    """An item's length for the limits and the estimate: its own, or a guess by source."""
    return it["duration_s"] or (UNKNOWN_REEL_S if it.get("source") == "instagram" else UNKNOWN_S)


def given_item(cfg: dict, url: str) -> dict:
    """A video url given by hand (--url, `story add`) as an item."""
    from . import kick

    f = {"url": url, "why": "given by hand"}
    k = swarm.finding_key(f)
    if k.startswith(("yt:", "ig:")):  # a reel's title, length and date come with its download
        return as_item({**f, "key": k})
    if m := re.match(r"https?://(?:www\.)?kick\.com/([^/]+)/videos/([0-9a-f-]{36})", url):
        it = next((v for v in kick.list_videos(m.group(1).lower(), cfg) if v["url"].endswith(m.group(2))), None)
        if not it:
            raise ClipperError(f"{url}: not in {m.group(1)}'s Kick videos")
        return {**it, "via": f["why"], "key": swarm.finding_key(it)}
    if m := re.match(r"https?://odysee\.com/(@[^/]+/[^/?#]+)", url):
        uri = "lbry://" + m.group(1)
        c = archive.odysee("resolve", {"urls": [uri]})[uri]
        if "error" in c:
            raise ClipperError(f"{url}: {c['error'].get('name')}")
        v = c.get("value", {})
        return {"id": f"od-{c['claim_id']}", "key": f"od:{c['claim_id']}", "source": "odysee", "url": f"lbry://{c['name']}#{c['claim_id']}",
                "page": url, "title": v.get("title") or "", "channel": "", "via": f["why"],
                "duration_s": float((v.get("video") or {}).get("duration") or 0),
                "date": datetime.fromtimestamp(int(v.get("release_time") or c["timestamp"]), UTC).date().isoformat()}
    raise ClipperError(f"{url}: not a YouTube, Kick, Odysee or Instagram video url")


def candidates(cfg: dict, findings: list[dict], have: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """(our own streams to link, other videos triage may pick), newest-ranked as found."""
    ours, cands, seen = [], [], set(have)
    min_s = cfg["story"]["min_duration_s"]
    for f in findings:
        if f.get("source") == "our streams":
            sid = (f.get("url") or "").split(" ")[0]
            it = our_stream(cfg, sid) if sid and sid not in seen else None
        else:
            it = as_item(f)
            # a VOD we recorded ourselves: linked, with its VOD page rather than the channel's
            if it and it["source"] == "kick" and it["id"] not in seen and (o := our_stream(cfg, it["id"])):
                it = {**o, "url": it["url"]}
        if not it or it["id"] in seen or 0 < it["duration_s"] < min_s:
            continue
        seen.add(it["id"])
        it.update(relevance=f.get("relevance") or "", key=f.get("key") or it.get("key") or "")
        (ours if it.get("ours") else cands).append(it)
    return ours, cands


def unique(items: list[dict]) -> list[dict]:
    """The first of each id, in order."""
    out: dict[str, dict] = {}
    for it in items:
        out.setdefault(it["id"], it)
    return list(out.values())


def fit(picked: list[dict], max_videos: int, max_hours: float) -> tuple[list[dict], list[dict]]:
    """The picks, in order, that fit the limits; and the ones that didn't."""
    keep, left, hours = [], [], 0.0
    for it in picked:
        h = length_s(it) / 3600
        if len(keep) < max_videos and hours + h <= max_hours:
            keep.append(it)
            hours += h
        else:
            left.append(it)
    return keep, left


def listing(items: list[dict]) -> str:
    out = []
    for n, it in enumerate(items):
        dur = f"{it['duration_s'] / 60:.0f} min" if it["duration_s"] else "length unknown"
        out.append(f"[{n}] {it['source']} | {it['channel'] or '?'} | {it['date'] or 'undated'} | {dur} | "
                   f"{it['title']}\n    found as {it.get('relevance') or '?'}: {it.get('via') or ''}")
    return "\n".join(out)


def triage(cfg: dict, b: dict, d: Path, claude, budget: Budget, prompts) -> dict:
    sc = cfg["story"]
    trail = jsonstate.read(d / "swarm.json", {}) or {}
    cat = load_catalog(d)
    done = {i: it for i, it in cat.items() if (item_dir(d, it) / "words.jsonl").exists()}
    given = []
    for u in b.get("urls", []):
        try:
            given.append(given_item(cfg, u))
        except ClipperError as e:
            print(f"  skip {e}")
    given = unique([it for it in given if it["id"] not in done])  # one video, however its url was written
    linked = []
    for it in given:  # a Kick VOD we recorded ourselves is linked, outside the limits, as a found one is
        if it["source"] == "kick" and (o := our_stream(cfg, it["id"])):
            linked.append({**o, "url": it["url"], "key": it.get("key") or ""})
    given = [it for it in given if it["id"] not in {o["id"] for o in linked}]
    ours, cands = candidates(cfg, trail.get("findings", []),
                             {**done, **{it["id"]: it for it in given + linked}})
    ours = linked + ours
    picks = []
    if cands:
        user = (f"Story: {b['brief']}\n\n{context(b)}\n\nLimits: {sc['max_videos']} videos, "
                f"{sc['max_hours']} hours of audio.\n\n<videos>\n{listing(cands)}\n</videos>")
        out = ask(cfg, claude, budget, sc["triage_model"], sc["triage_effort"], prompts.get("story_triage"),
                  user, TRIAGE_SCHEMA, 800)
        for p in (out or {}).get("picks", []):
            if 0 <= p["n"] < len(cands):
                picks.append({**cands[p["n"]], "why": p["why"]})
    # the limits count each video once (the model may pick one twice, or pick one given by hand)
    keep, left = fit(unique(given + picks), sc["max_videos"], sc["max_hours"])
    # a rerun replaces the earlier picks still waiting; what is transcribed stays
    cat = {**done, **{it["id"]: it for it in [*ours, *keep]}}
    save_catalog(d, cat)
    data = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "candidates": len(cands),
            "linked": [it["id"] for it in ours], "picked": [it["id"] for it in keep],
            "over_limits": [it["id"] for it in left]}
    jsonstate.write(d / "triage.json", data)
    todo = [it for it in cat.values() if not (item_dir(d, it) / "words.jsonl").exists()]
    hours = sum(length_s(it) for it in todo) / 3600
    print(f"triage: {len(cands)} videos found, {len(keep)} picked, {len(ours)} of our own streams linked"
          + (f", {len(left)} over the limits" if left else ""))
    for it in ours + keep:
        tag = "ours" if it.get("ours") else it["source"]
        dur = f"{it['duration_s'] / 60:.0f} min" if it["duration_s"] else "? min"
        print(f"  [{tag}] {it['date'] or 'undated'} {it['channel'] or '?'}: {it['title'][:70]} ({dur})\n"
              f"      {it.get('page') or it['url']}")
    try:  # the GPU speeds and prices live in modal_app, which needs the modal package
        from .modal_app import GPU_PRICES

        # each container's cold start (~10 min, 11-03): it dominates a story's few short items
        starts = min(sc["parallel"], len(todo)) * COLD_START_S / 3600 * GPU_PRICES.get(sc["gpu"], 0.0)
        usd = (f"~${archive.estimate_usd(hours, sc['gpu'], cfg['archive']['diarize']) + starts:.2f}"
               f" (${starts:.2f} of it container starts)")
    except ImportError:
        usd = "no estimate (modal isn't installed: uv sync --extra modal)"
    print(f"to transcribe: {len(todo)} items, ~{hours:.1f} h on {sc['gpu']}: {usd}")
    return data


# ---------- 4. ingest ----------

def ingest(cfg: dict, d: Path) -> None:
    sc = cfg["story"]
    cat = load_catalog(d)
    todo = [it for it in cat.values() if not (item_dir(d, it) / "words.jsonl").exists()]
    if todo:
        fh = logging.FileHandler(d / "transcribe.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        archive.log.addHandler(fh)
        retry = ytdl.AGE + ytdl.BOT if sc["youtube_bot_retry"] else ytdl.AGE
        try:
            archive._transcribe_all(cfg, d, d.name, todo, sc["gpu"], sc["parallel"], retry)
        finally:
            archive.log.removeHandler(fh)
            fh.close()
    if any((item_dir(d, it) / "transcript.json").exists() for it in cat.values()):
        from . import archive_index

        archive_index.build(cfg, d.name, rebuild=False)
    failed = [it["id"] for it in todo if (d / it["id"] / "error.txt").exists()]
    if failed:
        # a dossier without them would read as the whole story
        bot = sum("not a bot" in (d / i / "error.txt").read_text(encoding="utf-8") for i in failed)
        raise ClipperError(f"ingest: {len(failed)} of {len(todo)} failed ({', '.join(failed)}; <item>/error.txt)"
                           + (f", {bot} on YouTube's bot check ([story] youtube_bot_retry)" if bot else "")
                           + ": rerun to try again, or --from find to go on without them")


# ---------- 5. find the moments ----------

def searched_subjects(cfg: dict, d: Path) -> list[str]:
    """The story's own index, then each archive subject the swarm found something in."""
    trail = jsonstate.read(d / "swarm.json", {}) or {}
    subs = [d.name] if (d / "index.sqlite").exists() else []
    for f in trail.get("findings", []):
        s = (f.get("source") or "").removeprefix("archive ").strip()
        if (f.get("source") or "").startswith("archive ") and s not in subs \
                and (archive.subject_dir(cfg, s) / "index.sqlite").exists():
            subs.append(s)
    return subs


class Transcripts:
    """Items' words, read once each, across subjects."""

    def __init__(self, cfg: dict):
        self.cfg, self.cats, self.words = cfg, {}, {}

    def item(self, sub: str, item: str) -> dict:
        if sub not in self.cats:
            self.cats[sub] = load_catalog(archive.subject_dir(self.cfg, sub))
        return self.cats[sub].get(item) or {"id": item}

    def get(self, sub: str, item: str):
        from .investigate import Words, read_words

        if (sub, item) not in self.words:
            self.words[sub, item] = Words(read_words(item_dir(archive.subject_dir(self.cfg, sub), self.item(sub, item))))
        return self.words[sub, item]


def verify(ts: Transcripts, sub: str, h: dict, quote: str, window_s: float) -> tuple[float, str] | None:
    """(when the quote starts, the transcript's own words) or None when it isn't there."""
    from .investigate import locate
    from .receipts import tokens

    ws = ts.get(sub, h["item"])
    at = locate(ws, quote, h["start"] - 5, h["start"] + window_s + 15)
    if at is None:
        return None
    return ws.starts[at], ws.text(at, len(tokens(quote)))


def find(cfg: dict, b: dict, d: Path, claude, budget: Budget, prompts) -> list[dict]:
    import sqlite3

    from . import archive_index

    sc = cfg["story"]
    subs = searched_subjects(cfg, d)
    if not subs:
        raise ClipperError(f"{d.name}: nothing transcribed to search (clipper story run {b['slug']} --from triage)")
    who = ", ".join(p["name"] for p in b["people"]) or "not on file"
    out = ask(cfg, claude, budget, sc["find_model"], sc["find_effort"],
              prompts.get("story_expand", max_queries=sc["max_queries"]),
              f"Story: {b['brief']}\nPeople: {who}", archive_index.EXPAND_SCHEMA, 800)
    queries = [q["fts"] for q in (out or {}).get("queries", [])[:sc["max_queries"]]]
    pool: dict[tuple, dict] = {}
    for sub in subs:
        db = archive_index.open_index(cfg, sub)
        own = sub == d.name  # the story's own items were picked for it: no window
        for q in queries:
            try:
                found = archive_index.hits(db, q, None if own else b["since"], None if own else b["before"] or None,
                                           sc["per_query"])
            except sqlite3.OperationalError as e:
                print(f"  skip {q!r}: {e}")
                continue
            for h in found:
                pool.setdefault((sub, h["item"], h["start"]), {**h, "subject": sub})
        db.close()
    cands = list(pool.values())[:sc["candidates"]]
    print(f"find: {len(queries)} queries over {', '.join(subs)}: {len(cands)} windows")
    out = None
    if cands:  # none: moments.json still says so, so a dossier never reads an older run's
        lst = "\n\n".join(f"[{n}] {h['date'] or 'undated'} | {h['channel'] or '?'} | {h['title']}\n{h['text']}"
                          for n, h in enumerate(cands))
        out = ask(cfg, claude, budget, sc["find_model"], sc["find_effort"], prompts.get("story_moments"),
                  f"Story: {b['brief']}\nPeople: {who}\n\n<windows>\n{lst}\n</windows>", MOMENTS_SCHEMA, 3000)
    ts, moments, dropped = Transcripts(cfg), [], 0
    for p in (out or {}).get("picks", []):
        if not 0 <= p["n"] < len(cands):
            continue
        h = cands[p["n"]]
        got = verify(ts, h["subject"], h, p["quote"], cfg["archive"]["index_window_s"])
        if not got:
            dropped += 1
            continue
        t, quote = got
        it = ts.item(h["subject"], h["item"])
        moments.append({"subject": h["subject"], "item": h["item"], "source": h["source"], "channel": h["channel"],
                        "date": h["date"], "title": h["title"], "url": h["url"], "page": it.get("page") or "",
                        "ours": bool(it.get("ours")), "t": round(t, 2),
                        "link": archive_index.link(it.get("page") or h["url"], h["source"], t),
                        "quote": quote, "role": p["role"], "fit": p["fit"], "why": p["why"]})
    moments = dedupe(moments)
    moments.sort(key=lambda m: (m["date"] or "9999", m["item"], m["t"]))
    for n, m in enumerate(moments, 1):
        m["id"] = f"M{n:02d}"
    jsonstate.write(d / "moments.json", {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                                         "queries": queries, "subjects": subs, "windows": len(cands),
                                         "unverified": dropped, "moments": moments})
    print(f"find: {len(moments)} moments" + (f" ({dropped} dropped: quote not in the transcript)" if dropped else ""))
    return moments


def dedupe(moments: list[dict], gap_s: float = 20) -> list[dict]:
    """One moment per spot: two picks in the same item within gap_s keep the stronger."""
    out: list[dict] = []
    for m in sorted(moments, key=lambda m: (m["fit"] != "strong", m["t"])):
        if not any(o["subject"] == m["subject"] and o["item"] == m["item"] and abs(o["t"] - m["t"]) < gap_s
                   for o in out):
            out.append(m)
    return out


# ---------- 6. the dossier ----------

def sources(trail: dict, n: int) -> list[dict]:
    """The swarm's findings a dossier may cite: everything but archive windows (moments
    cover those), S01 first in rank order."""
    out = []
    for f in trail.get("findings", []):
        if (f.get("source") or "").startswith("archive "):
            continue
        k = f.get("key") or ""
        kind = ("video" if k.startswith(VIDEO) else "our streams" if f.get("source") == "our streams"
                else "reddit" if f.get("source") == "reddit" else "web")
        out.append({"key": k, "kind": kind, "source": f.get("source") or "", "channel": f.get("channel") or "",
                    "date": f.get("date") or "", "title": f.get("title") or "", "url": f.get("url") or "",
                    "relevance": f["relevance"], "why": f.get("why") or "", "summary": f.get("summary") or ""})
        if len(out) >= n:
            break
    for k, s in enumerate(out, 1):
        s["id"] = f"S{k:02d}"
    return out


def unresolved(out: dict, ids: set[str], moment_ids: set[str]) -> list[str]:
    """Cites that aren't listed ids, and claims that cite nothing."""
    bad = [c for x in out["timeline"] + out["conflicts"] for c in x["cites"] if c not in ids]
    bad += [x["moment"] for x in out["who_said_what"] if x["moment"] not in moment_ids]
    bad += [c for x in out["angles"] for c in x["moments"] if c not in moment_ids]
    bad += [f"no cite: {part} {x.get('what') or x.get('angle')!r}"[:90] for part in ("timeline", "conflicts", "angles")
            for x in out[part] if not x.get("cites" if part != "angles" else "moments")]
    return sorted(set(bad))


def dossier(cfg: dict, b: dict, d: Path, claude, budget: Budget, prompts) -> dict:
    sc = cfg["story"]
    trail = jsonstate.read(d / "swarm.json", {}) or {}
    moments = (jsonstate.read(d / "moments.json", {}) or {}).get("moments", [])
    srcs = sources(trail, sc["dossier_sources"])
    old = jsonstate.read(d / "dossier.json", {}) or {}
    struck = {s["key"] for s in old.get("sources", []) if s.get("struck")}
    for s in srcs:
        s["struck"] = s["key"] in struck
    # transcribed, not only picked: a failed download stays in the catalog (--from find goes on without it)
    ingested = {it.get("key") for it in load_catalog(d).values()
                if (item_dir(d, it) / "words.jsonl").exists()} - {"", None}
    ms = "\n".join(f"{m['id']} | {m['role']}, {m['fit']} | {m['date'] or 'undated'} | {m['channel'] or '?'}"
                   f"{' (our show)' if m['ours'] else ''} | {m['title']}\n    \"{m['quote']}\"\n    {m['why']}"
                   for m in moments)
    ss = "\n".join(f"{s['id']} | {s['kind']}{' (transcribed)' if s['key'] in ingested else ''} | "
                   f"{s['date'] or 'undated'} | {s['channel'] or '?'} | {s['title']}"
                   + (f"\n    {s['summary'][:300]}" if s["summary"] else "") + f"\n    found because: {s['why']}"
                   for s in srcs if not s["struck"])
    user = (f"Story: {b['brief']}\n\n{context(b)}\n\n<moments>\n{ms or '(none)'}\n</moments>\n\n"
            f"<sources>\n{ss or '(none)'}\n</sources>")
    ids = {m["id"] for m in moments} | {s["id"] for s in srcs if not s["struck"]}
    system = prompts.get("story_dossier")
    out = ask(cfg, claude, budget, sc["dossier_model"], sc["dossier_effort"], system, user, DOSSIER_SCHEMA, 4000)
    if out is None:
        raise ClipperError("the dossier model declined to write one")
    if bad := unresolved(out, ids, {m["id"] for m in moments}):
        print(f"dossier: {len(bad)} cites don't resolve ({'; '.join(bad)}); asking once more")
        out = ask(cfg, claude, budget, sc["dossier_model"], sc["dossier_effort"], system,
                  user + f"\n\nYour last answer had cites that aren't listed ids, or claims citing nothing: "
                         f"{'; '.join(bad)}. Cite only the M and S ids above, and leave out a claim nothing "
                         "above supports.", DOSSIER_SCHEMA, 4000)
        if out is None or (bad := unresolved(out, ids, {m["id"] for m in moments})):
            raise ClipperError(f"dossier: cites still don't resolve: {'; '.join(bad) if out else 'declined'}")
    data = {"slug": b["slug"], "brief": b["brief"], "since": b["since"], "before": b["before"],
            "people": [p["name"] for p in b["people"]], "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": sc["dossier_model"], "notes": old.get("notes", ""), "dossier": out,
            "moments": moments, "sources": srcs}
    jsonstate.write(d / "dossier.json", data)
    (d / "dossier.md").write_text(render(data), encoding="utf-8")
    print(f"dossier: {d / 'dossier.md'}")
    return data


def render(data: dict) -> str:
    from .timefmt import clock

    x, srcs = data["dossier"], {s["id"]: s for s in data["sources"]}
    ms = {m["id"]: m for m in data["moments"]}

    def cite(ids: list[str]) -> str:
        shown = [f"{c} (struck)" if c in srcs and srcs[c]["struck"] else c for c in ids]
        return f" ({', '.join(shown)})" if shown else ""

    window = f"{data['since']} to {data['before'] or 'today'}"
    out = [f"# Story: {data['brief']}", "",
           (f"Made {data['made_at'][:16]} by {data['model']}; window {window}; "
           f"people: {', '.join(data['people']) or 'none on file'}."),
           ("Research, not content: every quote links to the moment it was said; [speculation] marks what "
           "no source shows."), ""]
    if data.get("notes"):
        out += [f"Notes: {data['notes']}", ""]
    out += ["## Summary", "", x["summary"], "", "## Timeline", ""]
    out += [f"- {t['date']} [{t['status']}] {t['what']}{cite(t['cites'])}" for t in x["timeline"]]
    out += ["", "## People", ""] + [f"- {p['name']}: {p['role']}" for p in x["people"]]
    out += ["", "## Who said what", ""]
    for w in x["who_said_what"]:
        m = ms[w["moment"]]
        out += [(f"- {w['who']} ({w['moment']}), {m['date'] or 'undated'}, {m['channel']} at {clock(m['t'])}: "
                f"{w['note']}"), f"  > {m['quote']}", f"  {m['link']}"]
    if x["conflicts"]:
        out += ["", "## Conflicts", ""] + [f"- {c['what']}{cite(c['cites'])}" for c in x["conflicts"]]
    out += ["", "## Coverage", "", x["coverage"]]
    if x["gaps"]:
        out += ["", "## Gaps", ""] + [f"- {g}" for g in x["gaps"]]
    out += ["", "## Angles", ""] + [f"- {a['angle']}{cite(a['moments'])}" for a in x["angles"]]
    out += ["", "## Moments", ""]
    for m in data["moments"]:
        ours = ", our show" if m["ours"] else ""
        out += [(f"- {m['id']} [{m['role']}, {m['fit']}{ours}] {m['date'] or 'undated'}, {m['channel']}, "
                f"\"{m['title']}\" at {clock(m['t'])}"), f"  > {m['quote']}", f"  {m['link']}", f"  {m['why']}"]
    out += ["", "## Sources", ""]
    for s in data["sources"]:
        mark = " (struck)" if s["struck"] else ""
        out += [(f"- {s['id']}{mark} [{s['kind']}, {s['relevance']}] {s['date'] or 'undated'}, "
                f"{s['channel'] or s['source']}: {s['title']}"), f"  {s['url']}"]
    return "\n".join(out) + "\n"


# ---------- running ----------

def ask(cfg: dict, claude, budget: Budget, model: str, effort: str, system: str, user: str, schema: dict,
        out_tokens: int) -> dict | None:
    est = estimate(cfg, model, (len(system) + len(user)) / 4, out_tokens)
    try:
        budget.reserve(est)
    except OverBudget as e:
        raise ClipperError(f"[story] {e}: raise budget_usd to go on") from None
    try:
        out, _ = claude.json_call(model, system, user, schema, effort)
    finally:
        budget.release(est)
    return out


def done(d: Path, step: str) -> bool:
    if step == "swarm":
        return bool((jsonstate.read(d / "swarm.json", {}) or {}).get("runs"))
    if step == "ingest":
        cat = load_catalog(d)
        return bool(cat) and all((item_dir(d, it) / "words.jsonl").exists() for it in cat.values()) \
            and (d / "index.sqlite").exists()
    return (d / {"triage": "triage.json", "find": "moments.json", "dossier": "dossier.json"}[step]).exists()


def run(cfg: dict, slug: str, start: str | None = None, dry_run: bool = False, claude=None) -> float:
    """Every step not done yet, from `start` (forced) on; returns what the models cost."""
    from .archive_index import quiet_http
    from .llm import Claude, preflight
    from .prompts import Prompts

    b, d = load_brief(cfg, slug), story_dir(cfg, slug)
    sc = cfg["story"]
    if start and start not in STEPS:
        raise ClipperError(f"--from {start}: one of {', '.join(STEPS)}")
    if claude is None:
        claude = Claude(cfg)
        quiet_http()
        preflight(claude, sc["triage_model"])
    prompts = Prompts(cfg)
    # --from: that step and every one after it; otherwise from the first one not done
    first = STEPS.index(start) if start else next((k for k, s in enumerate(STEPS) if not done(d, s)), len(STEPS))
    if first == len(STEPS):
        print(f"story {slug}: every step is done (--from <step> reruns one; clipper story show {slug})")
    budget = None
    try:
        for step in STEPS[first:]:
            if dry_run and STEPS.index(step) > STEPS.index("triage"):
                print("dry run: stopping before ingest")
                break
            print(f"--- {step}", flush=True)
            if step == "swarm":
                run_swarm(cfg, b, d, claude)
                continue
            budget = budget or Budget(sc["budget_usd"], claude, spent_before=claude.cost)
            if step == "triage":
                triage(cfg, b, d, claude, budget, prompts)
            elif step == "ingest":
                ingest(cfg, d)
            elif step == "find":
                find(cfg, b, d, claude, budget, prompts)
            else:
                dossier(cfg, b, d, claude, budget, prompts)
    finally:
        with jsonstate.update(d / "runs.json", []) as runs:
            runs.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "from": start or "",
                         "dry_run": dry_run, "usd": round(claude.cost, 4)})
        print(f"story {slug}: models ~${claude.cost:.3f} (Modal is logged in `clipper modal`)")
    return claude.cost


def status(cfg: dict, slug: str | None = None) -> None:
    root = Path(cfg["paths"]["archive"])
    dirs = [story_dir(cfg, slug)] if slug else sorted(root.glob("story-*"))
    if slug and not (dirs[0] / "brief.json").exists():
        raise ClipperError(f"no story {slug!r}")
    for d in dirs:
        b = jsonstate.read(d / "brief.json", None)
        if not b:
            continue
        cat = load_catalog(d)
        have = sum((item_dir(d, it) / "words.jsonl").exists() for it in cat.values())
        n_m = len((jsonstate.read(d / "moments.json", {}) or {}).get("moments", []))
        usd = sum(r["usd"] for r in jsonstate.read(d / "runs.json", []) or [])
        steps = " ".join(s if done(d, s) else f"({s})" for s in STEPS)
        print(f"{b['slug']}: {b['brief'][:80]}\n  {steps}; {have}/{len(cat)} items transcribed, "
              f"{n_m} moments, models ~${usd:.2f}")


def trail(cfg: dict, slug: str) -> None:
    swarm.print_trail(story_dir(cfg, slug) / "swarm.json")


def show(cfg: dict, slug: str) -> None:
    d = story_dir(cfg, slug)
    data = jsonstate.read(d / "dossier.json", None)
    if not data:
        raise ClipperError(f"{slug}: no dossier yet (clipper story run {slug})")
    text = render(data)
    (d / "dossier.md").write_text(text, encoding="utf-8")
    print(text)
