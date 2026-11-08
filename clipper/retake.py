"""Retakes: act on "retake" notes by itself, then send the item back for review.

Usage:
    clipper retake                     every item set to retake with a note not handled yet
    clipper retake <stream> [--items c03] [--dry-run]
    clipper retake <stream> --items c03 --comment "start 5 s earlier" --dry-run   (try a note)

When an item is set to "retake" with a comment (in Kitsu, or with Discord's Changes
button), a model reads the note with the item's transcript, cut points and thumbnail and
returns edits from a fixed list: trim the start/end, a Short's layout, the title /
description / hashtags, a new thumbnail (picked again with the note as guidance) or new
thumbnail words. clipper applies them (re-cuts only that video, re-renders its captions
and thumbnail), publishes a new Kitsu revision waiting for approval with a "[clipper]
Retake applied: ..." note, and posts a fresh Discord card. What it can't do (cutting out
a middle part, effects) it says in a note on the task, and the item stays in retake.

Each note is handled once (retakes.json per stream records what was done). `clipper bot`
runs this after every card refresh and right after a Changes button ([retake] auto).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .config import _root, for_stream, stream_dir
from .errors import ClipperError

LOCK_STALE_S = 3600


# ---------- finding work ----------

def review_dirs(cfg: dict) -> list[Path]:
    return sorted(p.parent for p in cfg["paths"]["streams"].glob("*/review_state.json"))


def note_id(r: dict) -> str:
    """The Kitsu comment id; reviews synced before ids were kept use a hash of the text."""
    text = r.get("comment", "")
    return r.get("comment_id") or "text:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def pending(cfg: dict, stream: str | None = None) -> list[tuple[Path, str, dict]]:
    """(stream dir, item key, review.json entry) for retakes with a note not handled yet.
    Reads review.json as of the last sync."""
    dirs = [stream_dir(cfg, stream)] if stream else review_dirs(cfg)
    out = []
    for d in dirs:
        done = {h["comment_id"] for hs in jsonstate.read(d / "retakes.json", {}).values() for h in hs}
        for key, r in jsonstate.read(d / "review.json", {}).items():
            if r.get("decision") == "changes_requested" and r.get("comment") \
                    and note_id(r) not in done:
                out.append((d, key, r))
    return out


# ---------- the plan ----------

SCHEMA_BASE = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "trim", "layout", "title", "description", "hashtags",
                 "thumbnail", "unsupported"],
    "properties": {
        "summary": {"type": "string"},
        "trim": {"anyOf": [{"type": "null"}, {
            "type": "object", "additionalProperties": False, "required": ["start_s", "end_s"],
            "properties": {"start_s": {"type": "number"}, "end_s": {"type": "number"}}}]},
        "layout": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "hashtags": {"type": ["string", "null"]},
        "thumbnail": {"anyOf": [{"type": "null"}, {
            "type": "object", "additionalProperties": False,
            "required": ["redo", "guidance", "text"],
            "properties": {"redo": {"type": "boolean"}, "guidance": {"type": "string"},
                           "text": {"type": ["string", "null"]}}}]},
        "unsupported": {"type": ["string", "null"]},
    },
}


def comp_segments(sdir: Path, st: dict) -> list[dict]:
    """A compilation's segments (segments.json rows), in order."""
    data = jsonstate.read(sdir / "segments.json", {})
    name = Path(st["file"].replace("\\", "/")).name
    comp = next(c for c in data.get("compilations", []) if c["file"] == name)
    by_id = {s["id"]: s for s in data["segments"]}
    return [by_id[i] for i in comp["segments"]]


def window(sdir: Path, st: dict) -> tuple[float, float] | None:
    """The item's current in/out points in stream seconds; a compilation's are its first
    segment's start and its last segment's end."""
    kind = st["kind"]
    if kind == "pick":
        c = next(r for r in jsonstate.read(sdir / "candidates.json", {})["pass_a"] if r["id"] == st["id"])
        return c["in_s"], c["out_s"]
    if kind == "short":
        s = jsonstate.read(sdir / "shorts.json", {})[short_key(st)]
        return s["in_s"], s["out_s"]
    if kind == "segment":
        s = next(x for x in jsonstate.read(sdir / "segments.json", {})["segments"] if x["id"] == st["id"])
        return s["start_s"], s["end_s"]
    if kind == "compilation":
        segs = comp_segments(sdir, st)
        return segs[0]["start_s"], segs[-1]["end_s"]
    return None


def short_key(st: dict) -> str:
    """shorts.json key (v01_t20) of a Short's review_state entry."""
    return Path(st["file"].replace("\\", "/")).stem.split("_", 2)[0] + "_" + st["id"]


def context(cfg: dict, sdir: Path, key: str, st: dict, row: dict) -> str:
    from .shorts import layouts_of
    from .transcript import load_words, transcript_lines

    kind = {"pick": "clip", "short": "Short", "segment": "segment",
            "compilation": "compilation"}[st["kind"]]
    lines = [f"Item: {kind} {key}", f"Title: {row['title']}"]
    if row.get("hook"):
        lines.append(f"Hook: {row['hook']}")
    if row.get("description"):
        lines.append(f"Description: {row['description'][:600]}")
    if row.get("hashtags"):
        lines.append(f"Hashtags: {row['hashtags']}")
    thumbs = jsonstate.read(sdir / "thumbnails.json", {})
    th = thumbs.get(f"{Path(st['file'].replace(chr(92), '/')).parent.name}/"
                    f"{Path(st['file'].replace(chr(92), '/')).stem}")
    if th:
        lines.append(f"Thumbnail: words {th['text']!r}, frame at {th['t']:.0f} s into the "
                     f"video" + (f" (picked because: {th['why']})" if th.get("why") else ""))
    elif st["kind"] != "short":
        lines.append("Thumbnail: none yet")
    if st["kind"] == "short":
        lays = layouts_of(cfg["shorts"])
        cur = jsonstate.read(sdir / "shorts.json", {}).get(short_key(st), {}).get("layout", "")
        lines.append(f"Layout: {cur}. Layouts: " + "; ".join(
            f"{n} = {v.get('description', '')}" for n, v in lays.items()))
    win = window(sdir, st)
    if win:
        a, b = win
        words = load_words(sdir)
        if st["kind"] == "compilation":
            segs = comp_segments(sdir, st)
            lines.append(f"Current cut: starts at {a:.1f} s (start of its first segment), ends at "
                         f"{b:.1f} s (end of its last segment). Segments: " + "; ".join(
                             f"{s['start_s']:.1f}-{s['end_s']:.1f} s {s['title']!r}" for s in segs))
        else:
            lines.append(f"Current cut: {a:.1f} to {b:.1f} s ({b - a:.0f} s long)")
        pad = cfg["retake"]["context_s"]
        if st["kind"] in ("segment", "compilation") and b - a > 4 * pad:
            parts = [(max(0, a - pad), a + pad, "around the start"),
                     (b - pad, b + pad, "around the end")]
        else:
            parts = [(max(0, a - pad), b + pad, "the video and around it")]
        for x, y, label in parts:
            lines.append(f"\nTranscript {label} ({x:.0f}-{y:.0f} s):")
            lines += transcript_lines(words, x, y, speakers=False)
    return "\n".join(lines)


def plan(cfg: dict, sdir: Path, key: str, st: dict, row: dict, note: str) -> tuple[dict, float]:
    from .llm import Claude
    from .prompts import Prompts
    from .shorts import layouts_of

    rc = cfg["retake"]
    prompts = Prompts(cfg)
    prompts.version = rc["prompt_version"]
    prompts.vars["shorts_max_s"] = cfg["shorts"]["max_s"]
    schema = json.loads(json.dumps(SCHEMA_BASE))
    if st["kind"] == "short":
        schema["properties"]["layout"] = {"anyOf": [{"type": "null"}, {
            "type": "string", "enum": list(layouts_of(cfg["shorts"]))}]}
    claude = Claude(cfg)
    user = context(cfg, sdir, key, st, row) + f"\n\nReviewer's retake note:\n{note}"
    out, meta = claude.json_call(rc["model"], prompts.get("retake"), user, schema, rc["effort"])
    if out is None:
        out = {"summary": "", "trim": None, "layout": None, "title": None, "description": None,
               "hashtags": None, "thumbnail": None,
               "unsupported": f"The model declined this note ({meta.get('refusal')})."}
    if st["kind"] != "short":
        out["layout"] = None
    if st["kind"] == "short":
        out["thumbnail"] = None
    return out, claude.cost


# ---------- applying it ----------

def snap_trim(cfg: dict, sdir: Path, st: dict, trim: dict) -> tuple[float, float] | None:
    from .shorts import grow_window, snap_window
    from .transcript import load_words

    meta = jsonstate.read(sdir / "meta.json", {})
    end = float(meta.get("duration_s") or trim["end_s"])
    a = max(0.0, float(trim["start_s"]))
    b = min(float(trim["end_s"]), end)
    if b - a < 3:
        raise ValueError(f"trim {a:.1f}-{b:.1f} s is too short")
    sc = cfg["shorts"]
    words = load_words(sdir)
    cur = window(sdir, st)
    sa, sb = snap_window(words, a, b, sc["pad_s"])  # never in the middle of a word
    if st["kind"] in ("pick", "short") or not cur:
        a, b = sa, sb
    else:  # a long video's end the note didn't move isn't pulled back into a silence
        a = sa if abs(a - cur[0]) >= 0.3 else cur[0]
        b = sb if abs(b - cur[1]) >= 0.3 else cur[1]
    # #77 L16: no Short under min_s. Snapping can drop silence at the ends, so the
    # check comes after it, and the grown window is snapped again.
    if st["kind"] == "short" and b - a < sc["min_s"]:
        a, b = grow_window(words, a, b, sc["min_s"], sc["max_s"] - 2 * sc["pad_s"], 0.0, end)
        a, b = snap_window(words, a, b, sc["pad_s"])
    if st["kind"] == "short":
        b = min(b, a + sc["max_s"])
    if b - a < 3:  # snapping can shrink a window with little speech in it
        raise ValueError(f"trim {a:.1f}-{b:.1f} s is too short")
    if cur and abs(cur[0] - a) < 0.3 and abs(cur[1] - b) < 0.3:
        return None  # what it already is
    return round(a, 3), round(b, 3)


def trim_pick(cfg: dict, sdir: Path, st: dict, a: float, b: float) -> None:
    from .cut import cut_picks

    with jsonstate.update(sdir / "candidates.json", {}) as data:
        rec = next(r for r in data["pass_a"] if r["id"] == st["id"])
        rec.setdefault("orig_in_s", rec["in_s"])
        rec.setdefault("orig_out_s", rec["out_s"])
        rec["in_s"], rec["out_s"] = a, b
    cut_picks(sdir, cfg, ids=[st["id"]])


def trim_short(cfg: dict, sdir: Path, st: dict, a: float, b: float) -> None:
    from . import shorts

    with jsonstate.update(sdir / "shorts.json", {}) as data:  # the worker may be rendering
        data.setdefault(short_key(st), {})["window_override"] = [a, b]
    shorts.run(sdir, cfg, ids=[short_key(st).split("_")[0]])


def shift_chapters(chapters: list[dict], delta: float, dur: float) -> list[dict]:
    out = [{**c, "t": round(c["t"] - delta, 1)} for c in chapters]
    out = [c for c in out if -60 < c["t"] < dur - 30]
    if out:
        out[0]["t"] = 0.0
        out = [c for i, c in enumerate(out) if i == 0 or c["t"] > 0]
    return out if len(out) >= 3 else []


def rewrite_draft(cfg: dict, path: Path, chapters: list[dict]) -> None:
    from .bleep import Bleeper
    from .meta import description

    if not path.exists():
        return
    d = jsonstate.read(path, {})
    bc = cfg["bleep"]
    mask = Bleeper({**bc, "patterns": bc["patterns"] if bc["mode"] != "off" else []}).mask
    d["chapters"] = chapters
    d["description"] = mask(description(d["lead"], d["body"], chapters, d["credit"],
                                        d["hashtags"]))
    jsonstate.write(path, d)


def trim_segment(cfg: dict, sdir: Path, st: dict, a: float, b: float) -> list[str]:
    """Re-cut the segment, shift its chapters, re-cut the compilations it is part of.
    Returns the compilation item keys that changed."""
    return trim_segments(cfg, sdir, {st["id"]: (a, b)})


def trim_segments(cfg: dict, sdir: Path, wins: dict[str, tuple[float, float]]) -> list[str]:
    """trim_segment for several segments at once: each compilation is re-cut only once."""
    from .cut import cut_compilations, cut_segments

    if (jsonstate.read(sdir / "meta.json", {}) or {}).get("kind") == "story":
        story = sdir.name.removeprefix("story-")
        raise ClipperError(f"a story's video is an edit of other videos: trim its parts in {sdir.name}/story.json, "
                           f"then clipper story cut {story}")
    deltas = {}
    with jsonstate.update(sdir / "segments.json", {}) as data:
        for seg in data["segments"]:
            if seg["id"] in wins:
                a, b = wins[seg["id"]]
                deltas[seg["id"]] = a - seg["start_s"]
                seg.setdefault("orig_start_s", seg["start_s"])
                seg.setdefault("orig_end_s", seg["end_s"])
                seg["start_s"], seg["end_s"], seg["duration_s"] = a, b, round(b - a, 1)
    cut_segments(sdir, cfg, ids=list(wins))
    for seg in data["segments"]:
        draft = sdir / "metadata" / "segments" / f"{Path(seg['file']).stem}.json"
        if seg["id"] in wins and draft.exists():
            rewrite_draft(cfg, draft, shift_chapters(jsonstate.read(draft, {}).get("chapters", []),
                                                     deltas[seg["id"]], seg["duration_s"]))
    comps = [c for c in data.get("compilations", []) if set(wins) & set(c["segments"])]
    if not comps:
        return []
    cut_compilations(sdir, cfg, topics=[c["topic"] for c in comps])
    for c in comps:  # its chapters: each segment's title at its offset, then its chapters
        side = jsonstate.read(sdir / "compilations" / c["file"].replace(".mp4", ".json"), {})
        ch = []
        for part in side.get("chapters", []):
            ch.append({"t": part["start_s"], "title": part["title"]})
            sd = next((s for s in data["segments"] if s["id"] == part["id"]), None)
            sdraft = jsonstate.read(sdir / "metadata" / "segments" / f"{Path(sd['file']).stem}.json", {}) if sd else {}
            ch += [{"t": round(part["start_s"] + x["t"], 1), "title": x["title"]}
                   for x in sdraft.get("chapters", [])[1:]]
        rewrite_draft(cfg, sdir / "metadata" / "compilations" / c["file"].replace(".mp4", ".json"), ch)
    return [f"comp-{c['file'].removesuffix('.mp4')}" for c in comps]


def comp_edits(segs: list[dict], a: float, b: float) -> list[tuple[dict, float, float]]:
    """(segment, new start, new end) for a compilation trimmed to a-b: its start is the first
    segment's, its end the last's; the segments between stay as they are."""
    first, last = segs[0], segs[-1]
    out = []
    if abs(a - first["start_s"]) >= 0.3:
        out.append((first, a, b if first is last else first["end_s"]))
    if abs(b - last["end_s"]) >= 0.3 and not (out and first is last):
        out.append((last, last["start_s"], b))
    for seg, x, y in out:
        if y - x < 3:
            raise ClipperError(f"the trim would cut away all of {seg['id']}; retake the "
                               f"segment itself or drop it from the compilation")
    return out


def trim_compilation(cfg: dict, sdir: Path, st: dict, a: float, b: float) -> list[tuple[str, str]]:
    """Trim the compilation's first and/or last segment (which re-cuts the compilation).
    Returns (item key, thumbs asset id) of every video that changed."""
    edits = comp_edits(comp_segments(sdir, st), a, b)
    comps = trim_segments(cfg, sdir, {seg["id"]: (x, y) for seg, x, y in edits})
    return [(Path(seg["file"]).stem, seg["id"]) for seg, _, _ in edits] + [(k, k) for k in comps]


def thumb_asset_id(st: dict) -> str:
    return st["id"]  # thumbs asset ids: s02, t20, comp-<topic>


def apply(cfg: dict, sdir: Path, key: str, st: dict, p: dict, who: str) -> tuple[list[str], list[str]]:  # noqa: C901
    """Make the plan's edits. Returns (what changed, item keys whose video changed)."""
    from . import actions, shorts, thumbs
    from .meta import captions_only

    done, videos, thumb_ids, feedback = [], [], [], {}
    item = actions.Item(sdir, key, st)
    if p.get("trim"):
        win = snap_trim(cfg, sdir, st, p["trim"])
        if win:
            old = window(sdir, st)
            a, b = win
            if st["kind"] == "pick":
                trim_pick(cfg, sdir, st, a, b)
            elif st["kind"] == "short":
                trim_short(cfg, sdir, st, a, b)
            elif st["kind"] == "segment":
                for comp in trim_segment(cfg, sdir, st, a, b):
                    videos.append(comp)
                    thumb_ids.append(comp)
            elif st["kind"] == "compilation":
                for k, tid in trim_compilation(cfg, sdir, st, a, b):
                    if k != key:
                        videos.append(k)
                        thumb_ids.append(tid)
            videos.append(key)
            if st["kind"] != "short":
                thumb_ids.append(thumb_asset_id(st))
            done.append(f"cut {old[0]:.1f}-{old[1]:.1f} s -> {a:.1f}-{b:.1f} s "
                        f"({b - a:.0f} s)")
    if p.get("layout") and st["kind"] == "short":
        cur = jsonstate.read(sdir / "shorts.json", {}).get(short_key(st), {}).get("layout")
        if p["layout"] != cur:
            shorts.run(sdir, cfg, ids=[short_key(st).split("_")[0]], layout=p["layout"])
            videos.append(key)
            done.append(f"layout {cur} -> {p['layout']}")
    meta_values = {col: p[k].strip() for k, col in (("title", "YouTube title"),
                                                   ("description", "YouTube description"),
                                                   ("hashtags", "Hashtags")) if p.get(k)}
    if meta_values:
        actions.edit_meta(cfg, item, meta_values, who)
        done.append("new " + ", ".join(c.replace("YouTube ", "").lower() for c in meta_values))
    th = p.get("thumbnail") or {}
    if th.get("redo") and st["kind"] != "short":
        if thumb_asset_id(st) not in thumb_ids:
            thumb_ids.append(thumb_asset_id(st))
        feedback[thumb_asset_id(st)] = th.get("guidance") or ""
    if videos and st["kind"] != "short":
        captions_only(sdir, cfg)
    if thumb_ids:
        thumbs.run(sdir, cfg, ids=thumb_ids, force=bool(feedback), kitsu=False, feedback=feedback)
        if feedback:
            done.append("new thumbnail")
        elif thumb_ids:
            done.append("thumbnail re-picked for the new cut")
    if th.get("text") and st["kind"] != "short":
        actions.thumbtext(cfg, item, th["text"], who)
        done.append(f"thumbnail words {th['text']!r}")
    return done, videos


def record(sdir: Path, key: str, entry: dict) -> None:
    with jsonstate.update(sdir / "retakes.json", {}) as data:
        data.setdefault(key, []).append(entry)


def discord_message(cfg: dict, sdir: Path, text: str) -> None:
    from . import actions, notify

    if not cfg["discord"]["enabled"] or not cfg["discord"]["review_channel_id"]:
        return
    api = notify.Rest(cfg)
    dpath = sdir / "discord.json"
    dstate = jsonstate.read(dpath, {})
    thread = notify.ensure_thread(api, cfg, sdir, dstate, list(actions.describe(cfg, sdir).values()))
    notify.save_state(dpath, dstate)
    api("POST", f"/channels/{thread}/messages", {"content": text[:1900]})


def process(cfg: dict, sdir: Path, key: str, note: str, comment_id: str,
            dry_run: bool = False) -> dict:
    from . import actions, review

    cfg = for_stream(cfg, sdir)
    state = jsonstate.read(sdir / "review_state.json", {})
    st = state[key]
    row = actions.describe(cfg, sdir)[key]
    short = row["short"]
    t = time.time()
    p, cost = plan(cfg, sdir, key, st, row, note)
    print(f"{short}: {note!r}\n  plan: {json.dumps(p, ensure_ascii=False)}  (${cost:.3f})", flush=True)
    entry = {"comment_id": comment_id, "comment": note,
             "at": datetime.now(UTC).isoformat(timespec="seconds"),
             "plan": p, "cost_usd": round(cost, 4)}
    if dry_run:
        return entry
    who = "clipper retake"
    try:
        done, videos = apply(cfg, sdir, key, st, p, who)
    except Exception as e:
        entry.update(result="error", error=f"{type(e).__name__}: {e}")
        record(sdir, key, entry)
        gazu = review.connect(cfg["review"])
        actions._comment(gazu, actions.Item(sdir, key, st), None,
                         f"{review.NOTE_PREFIX} Retake failed: {type(e).__name__}: {e}")
        discord_message(cfg, sdir, f"⚠️ Retake for `{short}` failed: {type(e).__name__}: {e}")
        raise
    unsupported = (p.get("unsupported") or "").strip()
    gazu = review.connect(cfg["review"])
    item = actions.Item(sdir, key, st)
    if not done:
        msg = unsupported or "Nothing in the note maps to an edit clipper can make."
        actions._comment(gazu, item, None, f"{review.NOTE_PREFIX} Retake not applied: {msg}")
        discord_message(cfg, sdir, f"🔁 Retake for `{short}` not applied: {msg}\n> {note[:300]}")
        entry.update(result="unsupported", applied=[])
        record(sdir, key, entry)
        print(f"  not applied: {msg}")
        return entry
    # #75: recorded as handled now, before Kitsu and Discord: if they're down, the note isn't
    # planned and re-cut again on every pass; finish_pending() retries what follows.
    entry.update(result="applied", applied=done, publish="pending", videos=sorted(videos),
                 unsupported=unsupported, summary=p.get("summary") or "", seconds=round(time.time() - t))
    record(sdir, key, entry)
    print(f"  applied: {'; '.join(done)} ({time.time() - t:.0f}s)")
    publish_retake(cfg, sdir, key, entry)
    return entry


def publish_retake(cfg: dict, sdir: Path, key: str, entry: dict) -> None:
    """The Kitsu revision, notes and Discord card for an applied retake; marks the entry done."""
    from . import actions, notify, review

    cfg = for_stream(cfg, sdir)
    st = jsonstate.read(sdir / "review_state.json", {})[key]
    short = f"{sdir.name}/{key.split('_')[0]}"
    done, note, unsupported = entry["applied"], entry["comment"], entry.get("unsupported", "")
    summary = entry.get("summary") or "; ".join(done)
    gazu = review.connect(cfg["review"])
    before = st.get("revision")
    review.publish(sdir, cfg)
    st = jsonstate.read(sdir / "review_state.json", {})[key]
    text = (f"{review.NOTE_PREFIX} Retake applied: {summary}\n"
            + "\n".join(f"- {d}" for d in done)
            + (f"\nNot done: {unsupported}" if unsupported else "") + f"\nNote: {note}")
    # A new revision is already waiting for approval; otherwise put it back there.
    actions._comment(gazu, actions.Item(sdir, key, st),
                     None if st.get("revision") != before else "wfa", text)
    for k in entry.get("videos", []):
        if k != key and k in jsonstate.read(sdir / "review_state.json", {}):
            actions._comment(gazu, actions.Item(sdir, k, jsonstate.read(sdir / "review_state.json", {})[k]),
                             None, f"{review.NOTE_PREFIX} Re-cut because {short} was trimmed "
                                   f"(retake: {note[:200]})")
    review.sync(sdir, cfg, keys=[key], quiet=True)
    if cfg["discord"]["enabled"] and cfg["discord"]["review_channel_id"]:
        notify.post(sdir, cfg, keys=[key], force=True,
                    note=f"🔁 **Retake** `{short}`: {summary}"
                         + (f"\nNot done: {unsupported}" if unsupported else ""))
    with jsonstate.update(sdir / "retakes.json", {}) as data:
        for h in data.get(key, []):
            if h["comment_id"] == entry["comment_id"]:
                h.update(publish="done", revision=st.get("revision"))
    print(f"  {short}: in Kitsu as rev {st.get('revision')}")


def unpublished(cfg: dict) -> list[tuple[Path, str, dict]]:
    """Retakes applied while Kitsu or Discord was down, not yet published."""
    return [(d, key, h) for d in review_dirs(cfg)
            for key, hs in jsonstate.read(d / "retakes.json", {}).items()
            for h in hs if h.get("publish") == "pending"]


def finish_pending(cfg: dict) -> None:
    """Publish the unpublished retakes now (no new plan). Call it holding the Lock, or two
    runs can publish the same one."""
    for d, key, h in unpublished(cfg):
        try:
            publish_retake(cfg, d, key, h)
        except Exception as e:  # noqa: BLE001  # try again next run
            print(f"{d.name}/{key}: retake still not in Kitsu: {type(e).__name__}: {e}")


# ---------- command ----------

class Lock:
    """One retake run at a time (the bot starts them in the background)."""

    def __init__(self, cfg: dict):
        self.path = cfg["paths"]["streams"] / ".retake.lock"
        self.token = f"{socket.gethostname()} {os.getpid()} {time.time()}"

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - self.path.stat().st_mtime < LOCK_STALE_S:
                raise ClipperError("another clipper retake is running") from None
            self.path.unlink(missing_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, self.token.encode())
        os.close(fd)
        return self

    def touch(self) -> None:
        """Still running: a long run's lock isn't taken for stale (#77 L1)."""
        os.utime(self.path)

    def __exit__(self, *exc):
        try:  # only our own lock: one taken over as stale belongs to the newer run
            if self.path.read_text(encoding="utf-8") == self.token:
                self.path.unlink()
        except OSError:
            pass


def run(cfg: dict, stream: str | None = None, item: str | None = None,
        comment: str | None = None, dry_run: bool = False, sync: bool = True) -> list[dict]:
    from . import actions, review

    root = _root(cfg)
    if item:
        it = actions.resolve(root, item, stream)
        if comment is None:
            if sync:
                review.sync(it.stream_dir, root, keys=[it.key], quiet=True)
            r = jsonstate.read(it.stream_dir / "review.json", {}).get(it.key, {})
            if not r.get("comment"):
                raise ClipperError(f"{it.ref} has no retake note; pass --comment to try one")
            comment, cid = r["comment"], note_id(r)
        else:
            cid = f"manual:{int(time.time())}"
        todo = [(it.stream_dir, it.key, {"comment": comment, "comment_id": cid})]
    else:
        if sync:
            for d in ([stream_dir(root, stream)] if stream else review_dirs(root)):
                try:
                    review.sync(d, root, quiet=True)
                except ClipperError as e:
                    print(f"{d.name}: not synced ({e})")
        todo = pending(root, stream)
    finish = not item and not dry_run and unpublished(root)
    if not todo and not finish:
        print("no retakes waiting")
        return []
    out = []
    with Lock(root) as lock:
        if finish:
            finish_pending(root)
        for sdir, key, r in todo[:root["retake"]["max_per_run"]]:
            lock.touch()
            try:
                out.append(process(root, sdir, key, r["comment"], note_id(r), dry_run))
            except Exception as e:  # noqa: BLE001 - one bad item shouldn't stop the rest
                print(f"{sdir.name}/{key}: retake failed: {type(e).__name__}: {e}")
    return out
