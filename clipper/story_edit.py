"""`clipper story plan|cut` (#84, phase 2): a story's moments -> one video, through review.

Usage:
    clipper [--show <key>] story plan <slug> [--force]   the edit -> streams/story-<slug>/story.json
    clipper story cut <slug> [--force] [--no-review]     the video, meta, thumbs, the Kitsu card

`plan` ([story] edit_model, Sonnet; ~$0.03) reads the dossier's summary and angles and the
moments (phase 1), and writes the edit: title, topic, summary, description, and the moments
in order with a chapter title each. Code bounds each part around its quote (receipts'
clip_bounds: [story] lead_s / tail_s / max_clip_s, snapped to sentence edges), drops a part
that repeats an earlier part's words (a re-upload, a stream playing the video), merges two
that overlap in one video, and stops the edit at [story] max_minutes: the parts left out stay
in story.json with `use: false` and a note. The story is a stream of its own,
paths.streams/story-<slug>/: meta.json says kind "story" and the show's Kick channel, so
config.for_stream finds the show (`--show`, else [story] show, else the only one).

story.json is the edit, by hand: set a part's `use`, reorder parts, trim `start` / `end`
(seconds in its source video), rewrite a `chapter`, the `channel` on its lower third, the
title or the description. `plan` again keeps the old one as story.<time>.json.

`cut` takes each used part from its source (our own streams from their source video on the
share; YouTube, Kick VODs and Odysee by section with yt-dlp, cached in work/ by item and
range, so a trim downloads again), re-encodes it to 1920x1080 30 fps with the channel and date
on a lower third and the corner bug, bleeps it from its item's words (receipts' renderer and
[receipts] settings), and joins them into segments/s00_<slug>.mp4: one segment, with a
chapter per part in its sidecar, and the parts' words on the video's own timeline in
words.jsonl for the captions. Then meta (the sidecar's chapters, [story] credit and a source
line per part), thumbs and the Kitsu card, as a pre-show's. Approved, it goes out as soon as
possible ([story] publish = "asap"). A part is rendered again when its bounds or its lower
third change, meta when the edit's text, chapters or video change, and the cover whenever the
video does; each is marked done (story_state.json) only once it ran (--force redoes them all).
A source struck in dossier.json keeps its moments out of the plan.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate, media, ytdl
from .archive import item_dir, load_catalog, subject_dir
from .errors import ClipperError
from .story import PLATFORMS

PREFIX = "story-"
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "topic", "summary", "description", "figures", "parts"],
    "properties": {
        "title": {"type": "string"}, "topic": {"type": "string"}, "summary": {"type": "string"},
        "description": {"type": "string"}, "figures": {"type": "array", "items": {"type": "string"}},
        "parts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["moment", "chapter", "why"],
            "properties": {"moment": {"type": "string"}, "chapter": {"type": "string"}, "why": {"type": "string"}}}},
    },
}
REPEAT = 0.7      # a part whose quote shares this much of an earlier part's words repeats it
CHAPTER_MIN_S = 10  # YouTube's shortest chapter


def stream_dir(cfg: dict, slug: str) -> Path:
    return Path(cfg["paths"]["streams"]) / f"{PREFIX}{slug}"


def is_story(sdir: Path) -> bool:
    return sdir.name.startswith(PREFIX)


def pick_show(cfg: dict, before: str = "") -> str:
    """--show, else the show the story was planned under before, else [story] show, else the only one."""
    key = cfg["show"]["key"] or before or cfg["story"]["show"]
    if not key and len(cfg["shows"]) == 1:
        key = next(iter(cfg["shows"]))
    if not key:
        raise ClipperError(f"which show does the story go out under? clipper --show <key> story plan ..., "
                           f"or [story] show ({', '.join(cfg['shows'])})")
    if key not in cfg["shows"]:
        raise ClipperError(f"no [shows.{key}]; shows: {', '.join(cfg['shows'])}")
    return key


# ---------- plan ----------

def words_of(text: str) -> set[str]:
    from .receipts import tokens

    return set(tokens(text))


def repeats(quote: str, kept: list[dict]) -> dict | None:
    """The kept part whose quote this one mostly repeats (the same words in another video)."""
    q = words_of(quote)
    for p in kept:
        k = words_of(p["quote"])
        if len(q) >= 6 and len(k) >= 6 and len(q & k) / min(len(q), len(k)) >= REPEAT:
            return p
    return None


def shape(parts: list[dict], max_s: float) -> list[dict]:
    """Code's pass over the model's order: a repeat or a part past max_s gets use = false and a
    note; a part overlapping the one before it in the same video is merged into it."""
    kept: list[dict] = []
    total = 0.0
    for p in parts:
        prev = kept[-1] if kept else None
        overlap = bool(prev) and (prev["subject"], prev["item"]) == (p["subject"], p["item"]) \
            and p["start"] < prev["end"] and prev["start"] < p["end"]
        merged = (max(prev["end"], p["end"]) - min(prev["start"], p["start"])) if overlap else 0.0
        if overlap and total - (prev["end"] - prev["start"]) + merged > max_s:
            p.update(use=False, note="past [story] max_minutes")
        elif overlap:
            total -= prev["end"] - prev["start"]
            prev["start"], prev["end"] = min(prev["start"], p["start"]), max(prev["end"], p["end"])
            prev["moments"] += p["moments"]
            prev["quote"] += " ... " + p["quote"]
            total += prev["end"] - prev["start"]
            p.update(use=False, note=f"merged into {prev['id']}")
        elif rep := repeats(p["quote"], kept):
            p.update(use=False, note=f"repeats {rep['id']} ({rep['channel']})")
        elif total + p["end"] - p["start"] > max_s:
            p.update(use=False, note="past [story] max_minutes")
        else:
            kept.append(p)
            total += p["end"] - p["start"]
    return parts


def plan(cfg: dict, slug: str, force: bool = False, claude=None) -> dict:
    from . import story
    from .config import for_show
    from .llm import Budget, Claude, preflight
    from .prompts import Prompts
    from .receipts import clip_bounds

    sdir = stream_dir(cfg, slug)
    if (sdir / "story.json").exists() and not force:
        data = jsonstate.load(sdir / "story.json")
        print(f"{sdir / 'story.json'} (already planned; --force plans again)")
        print_plan(data)
        return data
    b, d = story.load_brief(cfg, slug), story.story_dir(cfg, slug)
    key = pick_show(cfg, (jsonstate.read(sdir / "meta.json", {}) or {}).get("show", ""))
    shc = for_show(cfg, key)
    sc = shc["story"]
    moments = (jsonstate.read(d / "moments.json", {}) or {}).get("moments", [])
    if not moments:
        raise ClipperError(f"story {slug}: no moments yet (clipper story run {slug})")
    dj = jsonstate.read(d / "dossier.json", {}) or {}
    dossier = dj.get("dossier") or {}
    cats: dict[str, dict] = {}

    def item_of(m: dict) -> dict:
        if m["subject"] not in cats:
            cats[m["subject"]] = load_catalog(subject_dir(cfg, m["subject"]))
        return cats[m["subject"]].get(m["item"]) or {"id": m["item"]}

    # a source struck in dossier.json is out of the edit too: its moments aren't offered
    struck = {x["key"] for x in dj.get("sources", []) if x.get("struck") and x.get("key")}
    kept = [m for m in moments if item_of(m).get("key") not in struck]
    if len(kept) < len(moments):
        print(f"  {len(moments) - len(kept)} moments left out: their source is struck in dossier.json")
    moments = kept
    if not moments:
        raise ClipperError(f"story {slug}: every moment's source is struck in dossier.json")
    if claude is None:
        claude = Claude(cfg)
        preflight(claude, sc["edit_model"])
    prompts = Prompts(cfg)
    prompts.version = sc["prompt_version"]
    show_name = shc["meta"]["show_name"] or key
    ms = "\n".join(f"{m['id']} | {m['role']}, {m['fit']} | {m['date'] or 'undated'} | {m['channel'] or '?'}"
                   f"{f' (our show, {show_name})' if m['ours'] else ''} | {m['title']}\n    \"{m['quote']}\"\n"
                   f"    {m['why']}" for m in moments)
    angles = "\n".join(f"- {a['angle']} ({', '.join(a['moments'])})" for a in dossier.get("angles", []))
    user = (f"Story: {b['brief']}\nShow: {show_name}\n\nSummary: {dossier.get('summary') or '(no dossier)'}\n\n"
            f"Angles:\n{angles or '(none)'}\n\n<moments>\n{ms}\n</moments>")
    budget = Budget(cfg["story"]["budget_usd"], claude, spent_before=claude.cost)
    out = story.ask(cfg, claude, budget, sc["edit_model"], sc["edit_effort"],
                    prompts.get("story_edit", max_minutes=f"{sc['max_minutes']:g}",
                                part_s=f"{(sc['lead_s'] + sc['tail_s'] + 15):.0f}-{sc['max_clip_s']}"),
                    user, PLAN_SCHEMA, 2000)
    if out is None:
        raise ClipperError("the edit model declined to plan one")
    by_id, parts = {m["id"]: m for m in moments}, []
    for x in out["parts"]:
        m = by_id.get(x["moment"])
        if not m:
            print(f"  skip {x['moment']}: not a moment id")
            continue
        it = item_of(m)
        idir = item_dir(subject_dir(cfg, m["subject"]), it)
        a, z, found = clip_bounds(idir, m["t"], m["quote"], sc)
        parts.append({"id": f"P{len(parts) + 1:02d}", "use": True, "moments": [m["id"]], "chapter": x["chapter"],
                      "subject": m["subject"], "item": m["item"], "dir": str(idir), "source": m["source"],
                      "url": it.get("m3u8") or m["url"], "page": m["page"] or m["url"], "ours": m["ours"],
                      "channel": label(shc, m), "date": m["date"], "title": m["title"], "role": m["role"],
                      "start": a, "end": z, "found": found, "quote": m["quote"], "why": x["why"], "note": ""})
    shape(parts, sc["max_minutes"] * 60)
    if not any(p["use"] for p in parts):
        raise ClipperError("the edit has no parts (the model's picks weren't moments)")
    data = {"slug": slug, "brief": b["brief"], "show": key, "title": out["title"].strip(),
            "topic": out["topic"].strip(), "summary": out["summary"].strip(),
            "description": out["description"].strip(), "figures": out["figures"], "parts": parts,
            "model": sc["edit_model"], "prompt_version": prompts.version,
            "planned_at": datetime.now(UTC).isoformat(timespec="seconds"), "usd": round(claude.cost, 4)}
    sdir.mkdir(parents=True, exist_ok=True)
    if (sdir / "story.json").exists():
        (sdir / "story.json").replace(sdir / f"story.{datetime.now():%Y%m%d-%H%M%S}.json")
    jsonstate.write(sdir / "story.json", data)
    write_meta(sdir, shc, key, data)
    print_plan(data)
    print(f"{sdir / 'story.json'}; ${claude.cost:.3f}. Edit it, then: clipper story cut {slug}")
    return data


def label(shc: dict, m: dict) -> str:
    """The lower third's channel: our own show by its name, not its Kick slug; "?" for a
    site's name (`cut` fills it in from yt-dlp)."""
    if m["ours"]:
        from .config import for_show, show_of_channel

        key = show_of_channel(shc, m["channel"])
        if key:
            return for_show(shc, key)["meta"]["show_name"] or m["channel"]
    return "?" if (m["channel"] or "").lower() in PLATFORMS else m["channel"]


def write_meta(sdir: Path, shc: dict, key: str, data: dict, **extra) -> dict:
    """meta.json: a stream of its own, kind "story", on the show's Kick channel. started_at is
    the first cut's (it dates the Kitsu episode and opens the schedule)."""
    old = jsonstate.read(sdir / "meta.json", {}) or {}
    meta = {"stream_id": sdir.name, "kind": "story", "source": "story", "story": data["slug"], "show": key,
            "channel": shc["show"]["channel"], "title": data["title"], "url": "",
            "started_at": old.get("started_at") or datetime.now(UTC).isoformat(timespec="seconds"),
            "duration_s": old.get("duration_s") or 0.0, "sources": old.get("sources", []), **extra}
    jsonstate.write(sdir / "meta.json", meta)
    return meta


def print_plan(data: dict) -> None:
    from .timefmt import clock

    used = [p for p in data["parts"] if p["use"]]
    total = sum(p["end"] - p["start"] for p in used)
    print(f"{data['title']}  ({len(used)} parts, {clock(total)})")
    for p in data["parts"]:
        mark = " " if p["use"] else "-"
        print(f" {mark}{p['id']} {'+'.join(p['moments'])} {p['date'] or 'undated'} {p['channel']}: "
              f"{clock(p['start'])}-{clock(p['end'])} ({p['end'] - p['start']:.0f} s) {p['chapter']}"
              + ("" if p["found"] else " [quote not found: the window]")
              + (f"  [{p['note']}]" if p["note"] else ""))
        print(f"      \"{p['quote'][:110]}\"")


# ---------- cut ----------

def load_plan(cfg: dict, slug: str) -> tuple[Path, dict]:
    sdir = stream_dir(cfg, slug)
    data = jsonstate.read(sdir / "story.json", None)
    if not data:
        raise ClipperError(f"story {slug}: no edit yet (clipper story plan {slug})")
    return sdir, data


def fetch(cfg: dict, p: dict, dst: Path) -> str:
    """A part's section of a video we don't have, by yt-dlp, start to end. Returns the
    uploader yt-dlp saw ("" when it didn't say), kept beside the section."""
    seen = dst.with_name(dst.name.removesuffix(".src.mp4") + ".channel.txt")
    if dst.exists():
        return seen.read_text(encoding="utf-8").strip() if seen.exists() else ""
    fmt = {"youtube": cfg["receipts"]["format"], "kick": cfg["story"]["kick_format"],
           "odysee": cfg["story"]["odysee_format"]}.get(p["source"])
    if not fmt:
        raise ClipperError(f"{p['id']}: can't download from {p['source']} ({p['url']})")
    tmp = dst.with_name(dst.stem + ".dl.mp4")
    args = ["--download-sections", f"*{p['start']:.2f}-{p['end']:.2f}", "--force-keyframes-at-cuts",
            "-f", fmt, "--merge-output-format", "mp4", "--no-playlist", "-o", str(tmp),
            "--print-to-file", "%(channel,uploader|)s", str(seen)]
    if p["source"] == "kick":
        args += ["--referer", "https://kick.com/", "--hls-use-mpegts"]
    url = p["url"]
    if p["source"] == "odysee" and url.startswith("lbry://"):
        url = p["page"]
    retry = ytdl.AGE + ytdl.BOT if cfg["story"]["youtube_bot_retry"] else ytdl.AGE
    res, _ = ytdl.run_retrying(cfg, *args, url, retry_on=retry)
    if res.returncode or not tmp.exists():
        raise ClipperError(f"{p['id']} yt-dlp: {ytdl.last_error(res)}")
    tmp.replace(dst)
    return seen.read_text(encoding="utf-8").strip() if seen.exists() else ""


def part_words(p: dict, at: float, seg0: int) -> tuple[list[dict], int]:
    """The part's words moved onto the video's timeline (at = where it starts there), each
    transcript segment renumbered after seg0 so lines never run across two parts."""
    lines = (Path(p["dir"]) / "words.jsonl").read_text(encoding="utf-8").splitlines()
    out, segs = [], {}
    for w in map(json.loads, lines):
        if w["start"] < p["start"] or w["end"] > p["end"]:
            continue
        n = segs.setdefault(w.get("seg"), seg0 + len(segs))
        out.append({"word": w["word"], "start": round(w["start"] - p["start"] + at, 3),
                    "end": round(w["end"] - p["start"] + at, 3), "score": w.get("score"), "speaker": None,
                    "seg": n})
    return out, seg0 + len(segs)


def chapters_of(parts: list[dict], starts: list[float], total: float) -> list[dict]:
    """A chapter per part, the first at 0, none shorter than YouTube's 10 s (the last one
    included: a short last part folds into the one before); fewer than three is none at all."""
    out: list[dict] = []
    for p, t in zip(parts, starts):
        if out and t - out[-1]["t"] < CHAPTER_MIN_S:
            continue
        out.append({"t": round(t, 1), "title": p["chapter"]})
    if len(out) > 1 and total - out[-1]["t"] < CHAPTER_MIN_S:
        out.pop()
    return out if len(out) >= 3 else []


def source_link(p: dict) -> str:
    """Where the part is in its source: YouTube at the second (our pre-shows too); our own Kick
    streams have no VOD to link, so their channel."""
    from .archive_index import link

    url = p["page"] or p["url"]
    if "youtube.com/watch" in url:
        return link(url, "youtube", p["start"])
    return url if p["ours"] else link(url, p["source"], p["start"])


def source_line(p: dict, t: float) -> dict:
    return {"t": round(t, 1), "channel": p["channel"], "date": p["date"] or "", "title": p["title"],
            "link": source_link(p)}


def signature(data: dict, used: list[dict]) -> list:
    return [[p["item"], p["start"], p["end"], p["channel"], p["date"], p["title"]] for p in used]


def part_file(work: Path, p: dict) -> Path:
    """A part's render, named by its bounds and its lower third's text: a changed channel,
    date or title renders it again."""
    h = hashlib.sha1(json.dumps([p["channel"], p["date"], p["title"]]).encode()).hexdigest()[:8]
    return work / f"{p['id']}_{p['item']}_{p['start']:.2f}-{p['end']:.2f}_{h}.mp4"


def retire(sdir: Path, keep: str) -> None:
    """An older s00 (a story planned before under another name) leaves segments/ and its
    draft: meta, thumbs and review read every sidecar there."""
    for d in (sdir / "segments", sdir / "metadata" / "segments"):
        for f in d.glob("s00_*"):
            if f.stem.split(".")[0] != keep:
                f.unlink()


def cut(cfg: dict, slug: str, force: bool = False, review: bool = True) -> Path:
    from . import meta as meta_stage
    from . import review as review_stage
    from . import thumbs
    from .bleep import Bleeper
    from .config import for_show, for_stream
    from .cut import write_sidecar
    from .receipts import clip, first_font, join
    from .segments import slug as file_slug
    from .timefmt import clock

    sdir, data = load_plan(cfg, slug)
    if bad := check_parts(data):
        raise ClipperError(f"story.json: {'; '.join(bad)}")
    cfg = for_stream(cfg, sdir)
    rc = cfg["receipts"]
    used = [p for p in data["parts"] if p["use"]]
    if not used:
        raise ClipperError(f"story {slug}: no part has use = true in story.json")
    work = sdir / "work"
    work.mkdir(parents=True, exist_ok=True)
    (sdir / "segments").mkdir(exist_ok=True)
    final = sdir / "segments" / f"s00_{file_slug(slug)}.mp4"   # one name, whatever the topic
    retire(sdir, final.stem)
    old = jsonstate.read(final.with_suffix(".json"), {}) or {}
    same = final.exists() and old.get("signature") == signature(data, used) and not force
    bleeps = jsonstate.read(work / "bleeps.json", {}) or {}   # by part render, kept or new
    if same:
        print(f"{final.name}: the edit's parts are unchanged, video kept (--force re-cuts)")
    else:
        fonts = (first_font(rc["fonts"]), first_font(rc["text_fonts"]))
        bug = rc["bug"] or cfg["scoring"]["channel_name"]
        bleeper = Bleeper(cfg["bleep"])
        rendered = []
        for p in used:
            print(f"{p['id']} {p['date'] or 'undated'} {p['channel']} {clock(p['start'])}-{clock(p['end'])}",
                  flush=True)
            src = work / f"{p['item']}_{p['start']:.2f}-{p['end']:.2f}.src.mp4"
            if not p["ours"]:
                seen = fetch(cfg, p, src)
                if seen and p["channel"].lower() in PLATFORMS | {"?"}:  # the search's "YouTube"
                    print(f"  channel: {p['channel']} -> {seen} (kept in story.json)")
                    for x in data["parts"]:
                        if x["item"] == p["item"] and x["channel"].lower() in PLATFORMS | {"?"}:
                            x["channel"] = seen
                    jsonstate.write(sdir / "story.json", data)
            dst = part_file(work, p)
            if force or not dst.exists():
                if p["ours"]:  # our own stream: its source video on the share, read from start
                    bleeps[dst.name] = clip(cfg, Path(p["dir"]), p, media.source_file(Path(p["dir"])), fonts,
                                            bug, bleeper, dst, label="", seek=True)
                else:
                    bleeps[dst.name] = clip(cfg, Path(p["dir"]), p, src, fonts, bug, bleeper, dst, label="")
                jsonstate.write(work / "bleeps.json", bleeps)
            rendered.append(dst)
        join(rendered, work / "concat.txt", final)
    # where each part starts in the video, from the rendered parts' own lengths
    starts, t = [], 0.0
    for p in used:
        starts.append(t)
        t += media.duration(part_file(work, p))
    words, seg0 = [], 0
    for p, at in zip(used, starts):
        w, seg0 = part_words(p, at, seg0)
        words += w
    (sdir / "words.jsonl").write_text("".join(json.dumps(w) + "\n" for w in words), encoding="utf-8")
    seg = {"id": "s00", "kind": "commentary", "story": True, "title": data["title"], "topic": data["topic"],
           "summary": data["summary"], "description": data["description"], "figures": data["figures"],
           "source_share": "most", "standalone": True, "start_s": 0.0, "end_s": round(t, 3),
           "duration_s": round(t, 1), "file": final.name, "cut": True, "skip_reasons": [],
           "chapters": chapters_of(used, starts, t),
           "bleeps": sum(bleeps.get(part_file(work, p).name, 0) for p in used),
           "signature": signature(data, used)}
    write_sidecar(final, seg)
    jsonstate.write(sdir / "segments.json", {"stream_id": sdir.name, "story": slug, "gaps": [],
                                             "segments": [{k: v for k, v in seg.items() if k != "signature"}],
                                             "compilations": []})
    write_meta(sdir, for_show(cfg, data["show"]), data["show"], data, duration_s=round(t, 1),
               sources=[source_line(p, at) for p, at in zip(used, starts)])
    print(f"{final} ({clock(t)}, {len(used)} parts, {seg['bleeps']} bleeped)")
    if not review:
        print(f"--no-review: meta, thumbs and Kitsu not run (clipper story cut {slug} again to do them)")
        return final
    # each stage is marked done for this edit only once it has run, so --no-review or a failure
    # leaves it to do next time; thumbs are picked again whenever the video changed
    video = seg["signature"]
    text = [video, *(seg[k] for k in ("title", "description", "summary", "figures", "chapters"))]
    done = jsonstate.read(sdir / "story_state.json", {}) or {}
    meta_stage.run(sdir, cfg, missing=done.get("meta") == text)
    done["meta"] = text
    jsonstate.write(sdir / "story_state.json", done)
    fresh = done.get("thumbs") == video
    thumbs.run(sdir, cfg, force=not fresh, missing=fresh)
    done["thumbs"] = video
    jsonstate.write(sdir / "story_state.json", done)
    review_stage.publish(sdir, cfg, only=["segments"])
    return final


def check_parts(data: dict) -> list[str]:
    """What's wrong with a hand-edited story.json (cut refuses it)."""
    bad, seen = [], set()
    for p in data.get("parts", []):
        if not re.fullmatch(r"P\d+", str(p.get("id", ""))) or p["id"] in seen:
            bad.append(f"part id {p.get('id')!r} (P01, P02 ..., each once)")
        elif p["use"] and not 0 <= p["start"] < p["end"]:
            bad.append(f"{p['id']}: start {p['start']} / end {p['end']}")
        seen.add(p.get("id"))
    return bad
