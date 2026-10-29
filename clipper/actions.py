"""Per-item review actions for the CLI and Discord (`clipper review <verb>`, `clipper bot`).

Kitsu stays the record of every decision. Each action writes the item's task status
(with a comment) or one of its custom columns, then refreshes that item in review.json
the way `clipper review sync` would, so `clipper schedule` treats it like an edit made
in Kitsu:
    approve   status approved
    reject    status rejected; the reason is the comment (kept for scoring examples)
    changes   status retake; the comment says what to change
    reopen    status wfa (waiting for approval), to undo any of the above
    hold      Hold column on or off (schedule skips held items)
    pin       Publish at column: "2026-10-20 15:00", a date (that day's first slot), or
              empty to unpin
    retitle   YouTube title column
    description, hashtags, tags, playlist
              YouTube description / Hashtags / Tags (comma-separated) / Playlist columns;
              checked against YouTube's limits, never emptied (an empty column falls
              back to the generated metadata)
    thumbtext Thumbnail text column (2-4 words); the sync re-renders the .jpg
Column changes also leave a "[clipper] ..." comment on the task, so Kitsu shows who
changed what.

Items are named <stream>/<key>, e.g. kick-131151926/c03_t01. The key's first part (c03,
s02) or any unique prefix works too, with the stream left out when only one stream has
that item.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonstate, review
from .config import _root, for_stream, stream_dir
from .errors import ClipperError
from .timefmt import length

OPEN = ("pending", "changes_requested")  # decisions still waiting on you


@dataclass
class Item:
    stream_dir: Path
    key: str
    state: dict  # its review_state.json entry

    @property
    def ref(self) -> str:
        return f"{self.stream_dir.name}/{self.key}"

    @property
    def short(self) -> str:
        """Stream plus the key's first part (c03, s02); unique within a stream."""
        return f"{self.stream_dir.name}/{self.key.split('_')[0]}"


def review_dirs(cfg: dict) -> list[Path]:
    """Streams published to Kitsu, oldest first."""
    return sorted(p.parent for p in cfg["paths"]["streams"].glob("*/review_state.json"))


def load_json(path: Path, default=None):
    return jsonstate.read(path, default)


def resolve(cfg: dict, ref: str, stream: str | None = None) -> Item:
    if "/" in ref:
        stream, ref = ref.rsplit("/", 1)
    dirs = [stream_dir(cfg, stream)] if stream else review_dirs(cfg)
    hits = []
    for d in dirs:
        state = load_json(d / "review_state.json", {})
        keys = ([k for k in state if k == ref] or [k for k in state if k.split("_")[0] == ref]
                or [k for k in state if k.startswith(ref)])
        hits += [Item(d, k, state[k]) for k in keys]
    if not hits:
        raise ClipperError(f"no review item {ref!r}" + (f" in {stream}" if stream else ""))
    if len(hits) > 1:
        raise ClipperError(f"{ref!r} matches {len(hits)} items: "
                         + ", ".join(h.ref for h in hits[:8]) + "; give more of the name")
    return hits[0]


# ---------- writing to Kitsu ----------

def _comment(gazu, item: Item, status_short: str | None, text: str) -> None:
    task = gazu.task.get_task(item.state["task_id"])
    status = (gazu.task.get_task_status_by_short_name(status_short) if status_short
              else gazu.task.get_task_status(task["task_status_id"]))
    gazu.task.add_comment(task, status, text)


def _set_columns(gazu, cfg: dict, item: Item, values: dict[str, str]) -> None:
    cfg = for_stream(cfg, item.stream_dir)
    proj = gazu.project.get_project_by_name(review.project_name(cfg))
    fmap = review.column_fields(gazu, proj)
    review.set_columns(gazu, fmap, review.ENTITY[item.state["kind"]], item.state["entity_id"], values,
                       strict=True)


def _status(cfg: dict, item: Item, short: str, text: str) -> None:
    cfg = for_stream(cfg, item.stream_dir)
    _comment(review.connect(cfg["review"]), item, short, text)


def _columns(cfg: dict, item: Item, values: dict[str, str], note: str) -> None:
    cfg = for_stream(cfg, item.stream_dir)
    gazu = review.connect(cfg["review"])
    _set_columns(gazu, cfg, item, values)
    _comment(gazu, item, None, f"{review.NOTE_PREFIX} {note}")


def _by(who: str) -> str:
    return f" ({who})" if who else ""


def approve(cfg: dict, item: Item, comment: str = "", who: str = "") -> dict:
    _status(cfg, item, "approved", comment or f"Approved{_by(who)}")
    return refresh(cfg, item)


def reject(cfg: dict, item: Item, reason: str, who: str = "") -> dict:
    if not reason.strip():
        raise ClipperError("reject needs a reason (it becomes a scoring example)")
    _status(cfg, item, "rejected", reason.strip())
    return refresh(cfg, item)


def changes(cfg: dict, item: Item, what: str, who: str = "") -> dict:
    if not what.strip():
        raise ClipperError("say what to change")
    _status(cfg, item, "retake", what.strip())
    return refresh(cfg, item)


def reopen(cfg: dict, item: Item, who: str = "") -> dict:
    _status(cfg, item, "wfa", f"Reopened for review{_by(who)}")
    return refresh(cfg, item)


def restore(cfg: dict, item: Item, status_short: str, who: str = "") -> dict:
    """Put the task back to an earlier status (the war room's undo). The comment is a
    clipper note, so the decision's reason stays the earlier comment (a retake isn't
    planned again, a reject keeps its scoring example)."""
    _status(cfg, item, status_short, f"{review.NOTE_PREFIX} Undo: back to {status_short}{_by(who)}")
    return refresh(cfg, item)


def hold(cfg: dict, item: Item, on: bool = True, who: str = "") -> dict:
    _columns(cfg, item, {"Hold": "true" if on else "false"},
             f"Hold {'on' if on else 'off'}{_by(who)}")
    return refresh(cfg, item)


def pin(cfg: dict, item: Item, when: str, who: str = "") -> dict:
    cfg = for_stream(cfg, item.stream_dir)
    from .schedule import parse_when

    when = (when or "").strip()
    sc = cfg["schedule"]
    if when and not parse_when(when, ZoneInfo(sc["timezone"]), "00:00"):
        raise ClipperError(f"Publish at must be YYYY-MM-DD HH:MM or YYYY-MM-DD, not {when!r}")
    _columns(cfg, item, {"Publish at": when},
             (f"Publish at {when}" if when else "Unpinned") + _by(who))
    return refresh(cfg, item)


def check_title(title: str) -> str:
    title = " ".join((title or "").split())
    if not title:
        raise ClipperError("title is empty")
    if len(title) > 100:
        raise ClipperError(f"YouTube titles are at most 100 characters ({len(title)} given)")
    return title


def retitle(cfg: dict, item: Item, title: str, who: str = "") -> dict:
    return edit_meta(cfg, item, {"YouTube title": title}, who)


TAG_BUDGET = 480  # what clipper/youtube.py keeps of YouTube's ~500-character tag limit


def _no_empty(name: str, value: str) -> str:
    if not value:  # publish refills an empty column, and upload falls back to the draft
        raise ClipperError(f"{name} is empty; an empty column falls back to the generated one")
    return value


def _yt_bytes(text: str) -> int:
    """Size as uploaded: clipper/youtube.py swaps < and > for 3-byte look-alikes."""
    return len(text.replace("<", "‹").replace(">", "›").encode("utf-8"))


def check_description(text: str) -> str:
    text = "\n".join(line.rstrip() for line in (text or "").strip().splitlines())
    size = _yt_bytes(text)
    if size > 5000:
        raise ClipperError(f"YouTube descriptions are at most 5000 bytes ({size} given)")
    return _no_empty("description", text)


def check_hashtags(text: str) -> str:
    return _no_empty("hashtags", review.hashtag_line(text))


def check_tags(text: str) -> str:
    tags = [" ".join(t.split()) for t in (text or "").split(",")]
    tags = [t for t in tags if t]
    long = [t for t in tags if len(t) > 100]
    if long:
        raise ClipperError(f"a tag is at most 100 characters: {long[0][:40]!r}…")
    total = sum(len(t) + 1 for t in tags)
    if total > TAG_BUDGET:
        raise ClipperError(f"tags come to {total} characters; YouTube allows about 500 and "
                         f"upload keeps the first {TAG_BUDGET}, so drop some")
    return _no_empty("tags", ", ".join(tags))


def check_playlist(text: str) -> str:
    return _no_empty("playlist", " ".join((text or "").split()))


STYLE_FIELDS = {"title": "title", "description": "description", "hashtags": "hashtags",
                "tags": "tags", "thumb_text": "thumbnail words"}


def style_issues(cfg: dict, values: dict[str, str]) -> list[dict]:
    """Warnings for viewer-facing text (#19), shown in the war room while editing and as a
    badge on cards. Never raises. values: title / description / hashtags / tags / thumb_text;
    cfg: the item's show config (its title prefix)."""
    from .bleep import Bleeper

    out: list[dict] = []
    bleeper = Bleeper({**cfg["bleep"], "mode": "bleep"})
    for field, text in values.items():
        text = text or ""
        name = STYLE_FIELDS.get(field, field)
        m = re.search(r"\bhosts?\b", text, re.IGNORECASE)
        if m:
            out.append({"field": field, "msg": f'{name} says "{m.group(0)}": name Andy and PPP '
                                                 "(or the show) instead"})
        bad = sorted({bleeper.mask(w) for w in re.findall(r"[\w'@$*-]+", text) if bleeper.hit(w)})
        if bad:
            out.append({"field": field, "msg": f"{name} has {', '.join(bad)} unmasked "
                                                 "(upload masks it; better fixed here)"})
    title = " ".join((values.get("title") or "").split())
    if title:
        full = review.full_title(cfg, title)
        if len(full) > 100:
            out.append({"field": "title", "msg": f"{len(full)} characters with the show prefix; "
                                                 "YouTube cuts titles at 100"})
    desc = (values.get("description") or "").strip()
    tags_line = review.hashtag_line(values.get("hashtags") or "")
    if desc and _yt_bytes(f"{desc}\n\n{tags_line}") > 5000:
        out.append({"field": "description", "msg": f"{_yt_bytes(desc)} bytes plus hashtags; "
                                                   "YouTube allows 5000"})
    tags = [t.strip() for t in (values.get("tags") or "").split(",") if t.strip()]
    if sum(len(t) + 1 for t in tags) > TAG_BUDGET:
        out.append({"field": "tags", "msg": f"tags come to {sum(len(t) + 1 for t in tags)} "
                                            f"characters; upload keeps the first {TAG_BUDGET}"})
    return out


# Kitsu column: (check, what the "[clipper] ..." note calls it)
META = {"YouTube title": (check_title, "Retitled"),
        "YouTube description": (check_description, "Description"),
        "Hashtags": (check_hashtags, "Hashtags"),
        "Tags": (check_tags, "Tags"),
        "Playlist": (check_playlist, "Playlist")}


def edit_meta(cfg: dict, item: Item, values: dict[str, str], who: str = "") -> dict:
    """Write some of the META columns (checked and normalized) in one Kitsu update."""
    cfg = for_stream(cfg, item.stream_dir)
    values = {name: META[name][0](v) for name, v in values.items()}
    if not values:
        raise ClipperError("nothing to change")
    if "YouTube description" in values or "Hashtags" in values:
        row = describe(cfg, item.stream_dir)[item.key]
        desc = values.get("YouTube description", row["description"])
        tags_line = values.get("Hashtags", row["hashtags"])
        size = _yt_bytes(f"{desc}\n\n{tags_line}")
        if size > 5000:  # upload adds the hashtag line to the description
            raise ClipperError(f"description plus hashtag line is {size} bytes; "
                             "YouTube allows 5000")
    notes = []
    for name, v in values.items():
        label = META[name][1]
        notes.append(f"{label} edited" if "\n" in v or len(v) > 120 else f"{label}: {v}")
    _columns(cfg, item, values, "; ".join(notes) + _by(who))
    return refresh(cfg, item)


def description(cfg: dict, item: Item, text: str, who: str = "") -> dict:
    return edit_meta(cfg, item, {"YouTube description": text}, who)


def hashtags(cfg: dict, item: Item, text: str, who: str = "") -> dict:
    return edit_meta(cfg, item, {"Hashtags": text}, who)


def tags(cfg: dict, item: Item, text: str, who: str = "") -> dict:
    return edit_meta(cfg, item, {"Tags": text}, who)


def playlist(cfg: dict, item: Item, text: str, who: str = "") -> dict:
    return edit_meta(cfg, item, {"Playlist": text}, who)


def thumbtext(cfg: dict, item: Item, text: str, who: str = "") -> dict:
    """The refresh's sync sees the new column value and re-renders the thumbnail."""
    cfg = for_stream(cfg, item.stream_dir)
    from .thumbs import check_text, video_key

    text = check_text(cfg, " ".join(text.split()))
    thumbs = load_json(item.stream_dir / "thumbnails.json", {})
    if video_key(item.stream_dir / item.state["file"].replace("\\", "/")) not in thumbs:
        raise ClipperError(f"{item.ref} has no thumbnail yet; run clipper thumbs first")
    _columns(cfg, item, {review.THUMB_COL: text}, f"Thumbnail text: {text}{_by(who)}")
    return refresh(cfg, item)


def refresh(cfg: dict, item: Item) -> dict:
    """Re-read this one item from Kitsu into review.json; returns its card data."""
    cfg = for_stream(cfg, item.stream_dir)
    review.sync(item.stream_dir, cfg, keys=[item.key], quiet=True)
    return describe(cfg, item.stream_dir)[item.key]


# ---------- one entry point for the bot and the war room ----------

ACTIONS = ("approve", "reject", "changes", "reopen", "hold", "pin", "edit", "thumbtext")
REPLANS = ("approve", "reject", "changes", "reopen", "hold", "pin")  # these move the schedule


def decide(cfg: dict, item: Item, action: str, who: str, text: str = "", on: bool | None = None,
           values: dict[str, str] | None = None) -> dict:
    """One review action on one item; returns its card data. text: the note, reason, pin
    or thumbnail text; on: hold or release (None flips it); values: {Kitsu column: new
    value} for "edit"."""
    if action == "hold":
        return hold(cfg, item, (not refresh(cfg, item)["hold"]) if on is None else on, who)
    fn = {"approve": lambda: approve(cfg, item, text, who),
          "reject": lambda: reject(cfg, item, text, who),
          "changes": lambda: changes(cfg, item, text, who),
          "reopen": lambda: reopen(cfg, item, who),
          "pin": lambda: pin(cfg, item, text, who),
          "edit": lambda: edit_meta(cfg, item, values or {}, who),
          "thumbtext": lambda: thumbtext(cfg, item, text, who)}.get(action)
    if not fn:
        raise ClipperError(f"unknown action {action!r}")
    return fn()


def replan(cfg: dict, item: Item, action: str, row: dict) -> tuple[dict, str]:
    """After an action that moves the schedule ([discord] replan on): `clipper schedule`
    without a Kitsu sync, then the item's card data again and a note on where it landed."""
    from . import schedule

    if not (cfg["discord"]["replan"] and action in REPLANS):
        return row, ""
    schedule.run(cfg, sync_first=False)
    row = describe(cfg, item.stream_dir)[item.key]
    if row["scheduled"]:
        return row, f"scheduled {row['scheduled'][:16].replace('T', ' ')}"
    if row["decision"] == "approved":
        return row, "held, not scheduled" if row["hold"] else "no free slot yet"
    return row, ""


def clipper_cmd(cfg: dict) -> list[str]:
    """The command line for a background `clipper` run with this config."""
    cmd = [sys.executable, "-m", "clipper.cli"]
    if _root(cfg).get("_file"):
        cmd += ["--config", str(_root(cfg)["_file"])]
    return cmd


# ---------- reading ----------

def schedule_entries(cfg: dict) -> dict[str, dict]:
    data = load_json(cfg["paths"]["schedule"], {}) or {}
    return {e["key"]: e for e in data.get("entries", [])}


def describe(cfg: dict, sdir: Path, gazu=None) -> dict[str, dict]:
    """Everything a card or list row shows, per item, from local files (review.json is
    as of the last sync). Kitsu is only asked for links older publishes didn't save."""
    cfg = for_stream(cfg, sdir)
    state = review.ensure_links(sdir, cfg, gazu)
    decisions = load_json(sdir / "review.json", {})
    sched = schedule_entries(cfg)
    try:
        outputs = {it["key"]: it for it in review.items(sdir, cfg["review"]["include"], cfg)}
    except FileNotFoundError:
        outputs = {}
    meta = load_json(sdir / "meta.json", {})
    ls = review.Listing()
    out = {}
    for key, st in state.items():
        r = decisions.get(key, {})
        fields = r.get("fields") or {}
        proposed = st.get("fields", {})
        it = outputs.get(key, {})
        entry = sched.get(f"{sdir.name}/{key}")
        jpg = (sdir / st["file"].replace("\\", "/")).with_suffix(".jpg")
        out[key] = {
            "ref": f"{sdir.name}/{key}", "short": f"{sdir.name}/{key.split('_')[0]}",
            "stream": sdir.name, "date": meta.get("started_at", "")[:10],
            "key": key, "kind": st["kind"],
            "title": fields.get("YouTube title") or proposed.get("YouTube title") or key,
            "hook": fields.get("Hook") or proposed.get("Hook") or "",
            "category": fields.get("Category") or proposed.get("Category") or "",
            "score": fields.get("Score") or proposed.get("Score") or "",
            "found_by": fields.get("Found by") or proposed.get("Found by") or "",
            "topic": fields.get("Topic") or proposed.get("Topic") or "",
            "duration_s": it.get("duration_s"),
            "decision": r.get("decision", "pending"), "status": r.get("status", "wfa"),
            "comment": r.get("comment", ""),
            "hold": str(fields.get("Hold") or "").lower() in ("true", "1", "yes", "on"),
            "publish_at": fields.get("Publish at") or "",
            "scheduled": entry["publish_at"] if entry else "",
            "uploaded": bool(entry and entry.get("status") == "uploaded"),
            "youtube_url": fields.get("YouTube URL") or "",
            "kitsu_url": st["kitsu_url"], "episode_url": st["episode_url"],
            "thumb_text": fields.get(review.THUMB_COL) or proposed.get(review.THUMB_COL) or "",
            **{k: fields.get(n) or proposed.get(n) or "" for k, n in
               (("description", "YouTube description"), ("hashtags", "Hashtags"),
                ("tags", "Tags"), ("playlist", "Playlist"))},
            "thumbnail": jpg if ls.exists(jpg) else None,
            "thumb_mtime": ls.mtime(jpg),
            "file": sdir / st["file"], "preview": sdir / "review" / f"{key}.mp4",
            "revision": st.get("revision"), "synced_at": r.get("updated_at"),
        }
    return out


def list_items(cfg: dict, stream: str | None = None, everything: bool = False,
               sync: bool = True) -> list[dict]:
    """Items still waiting on a decision (or all of them), oldest stream first."""
    dirs = [stream_dir(cfg, stream)] if stream else review_dirs(cfg)
    rows = []
    for d in dirs:
        if sync:
            review.sync(d, cfg, quiet=True)
        rows += [r for r in describe(cfg, d).values() if everything or r["decision"] in OPEN]
    return rows


def status(cfg: dict, sync: bool = False) -> dict:
    """Per stream: decision counts; plus what schedule.json has coming up."""
    streams = []
    for d in review_dirs(cfg):
        if sync:
            review.sync(d, cfg, quiet=True)
        rows = describe(cfg, d).values()
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["decision"]] = counts.get(r["decision"], 0) + 1
        meta = load_json(d / "meta.json", {})
        streams.append({"stream": d.name, "date": meta.get("started_at", "")[:10],
                        "title": meta.get("title", ""), "counts": counts,
                        "held": sum(r["hold"] for r in rows),
                        "episode_url": next(iter(rows))["episode_url"] if rows else ""})
    data = load_json(cfg["paths"]["schedule"], {}) or {}
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = datetime.now(tz)
    upcoming = [e for e in data.get("entries", [])
                if now <= datetime.fromisoformat(e["publish_at"]) <= now + timedelta(days=7)]
    return {"streams": streams, "generated_at": data.get("generated_at"),
            "next_7_days": len(upcoming),
            "uploaded": sum(e.get("status") == "uploaded" for e in data.get("entries", [])),
            "next": upcoming[0] if upcoming else None}


# ---------- text for the CLI (and Discord's lists) ----------

def decision_text(r: dict) -> str:
    out = r["decision"].replace("_", " ")
    if r["hold"]:
        out += ", held"
    if r["publish_at"]:
        out += f", pinned {r['publish_at']}"
    return out


def row_line(r: dict) -> str:
    extra = f" | hook: {r['hook']}" if r["hook"] else ""
    return (f"{r['short']:<22} {r['kind']:<7} {length(r['duration_s']):>7}  "
            f"[{decision_text(r)}] {r['title']}{extra}")


def print_rows(rows: list[dict]) -> None:
    if not rows:
        print("nothing waiting for review")
    for r in rows:
        print(row_line(r))
        if r["comment"]:
            print(f"{'':24}note: {r['comment'][:150]}")
        if r["scheduled"]:
            print(f"{'':24}scheduled {r['scheduled']}")
        print(f"{'':24}{r['kitsu_url']}")
        print(f"{'':24}{r['preview'] if r['preview'].exists() else r['file']}")


def status_lines(s: dict) -> list[str]:
    lines = []
    for st in s["streams"]:
        c = st["counts"]
        parts = [f"{c.get(k, 0)} {k.replace('_', ' ')}" for k in
                 ("pending", "changes_requested", "approved", "rejected") if c.get(k)]
        held = f", {st['held']} held" if st["held"] else ""
        lines.append(f"{st['stream']} ({st['date']}): {', '.join(parts) or 'empty'}{held}")
    nxt = s["next"]
    lines.append(f"schedule: {s['next_7_days']} releases in the next 7 days, "
                 f"{s['uploaded']} uploaded"
                 + (f"; next {nxt['publish_at'][:16].replace('T', ' ')} {nxt['title']}"
                    if nxt else ""))
    return lines
