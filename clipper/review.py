"""Review in Kitsu: publish a stream's outputs as previews, read decisions back.

Kitsu layout (a TV-show production; everything lives inside the show's production):
    episode    one per stream (review.episode_name, default its date)
    sequence   one per topic segment, e.g. s02_the-quartering; its Segment task holds the
               segment preview, status and comments
    shot       one per clip, inside the segment its start falls in (s00_other if none),
               e.g. s10_the-quartering/c01_t20; its Horizontal task holds the preview
    shot       one per vertical Short too (`clipper shorts`), beside its clip, e.g.
               s10_the-quartering/v01_t20; its Vertical task holds the 9:16 preview and
               its "Layout" column names the crop layout (edit it, rerun clipper shorts)
    sequence   one per compilation too, e.g. comp-the-quartering; its Compilation task
               holds the whole long video (its segments' previews joined), its own status,
               metadata columns and thumbnail
    playlists  per episode: "Clips" (every clip), "Shorts" (every Short) and "Compilation: <topic>" (that topic's
               segment sequences in order, so the long video plays back to back)
The production is built from the "Clipping" project template (TEMPLATE below, created
or updated on every publish): TV-show type, 30 fps 1280x720 like the previews, Segment
and Horizontal task types, the review statuses, and custom columns (COLUMNS) on
sequences and shots. Publish fills a column when it is empty or still shows what publish
last wrote there (so a re-run of `clipper meta` reaches Kitsu), and never overwrites an
edit made in Kitsu or Discord; sync reads them back into review.json and lists which ones
were edited. The upload metadata lives in the YouTube title / description, Hashtags,
Tags (comma-separated) and Playlist columns, and `clipper upload` takes it from there,
falling back to the metadata JSON. Task types and
statuses are studio-wide definitions in Kitsu; only the template and this production
use ours.

Publishing uploads a low-res preview as a new revision with status "wfa" (waiting for
approval). The comment is the upload metadata draft (metadata/, from `clipper meta`) when
there is one, else the cut's own title and description. In Kitsu, set "approved" (or
"done") to approve, "retake" with a comment to ask for changes, or "rejected" to drop it;
`sync` writes those to review.json. The YouTube thumbnail (`clipper thumbs`, the .jpg
beside the video) is added to the same revision as a second preview file, and set as
the item's Kitsu thumbnail; edit its words in the "Thumbnail text" column and sync
re-renders the .jpg and attaches the new one. A compilation published as its own item
("compilations" in review.include) ships on its own approval; one that isn't ships when all
its segments are approved. The same decisions can be made from the CLI or
Discord (clipper/actions.py, `clipper bot`); those write to Kitsu too.

Auth: a Kitsu bot token in $KITSU_TOKEN (Kitsu > Bots); the bot needs a role that can
create projects, shots and tasks (Admin, or Studio Manager on an existing project).
"""

from __future__ import annotations

import bisect
import os
import subprocess
import time
from pathlib import Path

from . import jsonstate
from .config import for_stream
from .errors import ClipperError
from .segments import slug
from .states import ENTITY, Decision

TASK_TYPES = {"Segment": ("#48bfe3", "Sequence"), "Horizontal": ("#5e60ce", "Shot"),
              "Compilation": ("#f77f00", "Sequence"), "Vertical": ("#ef476f", "Shot")}
FOLDERS = {"pick": "clips", "segment": "segments", "compilation": "compilations",
           "short": "shorts"}
OTHER_SEQUENCE = "s00_other"
# Kitsu task status (short name) -> review.json decision; any other status is pending.
DECISIONS = {"approved": Decision.APPROVED, "done": Decision.APPROVED, "retake": Decision.CHANGES,
             "rejected": Decision.REJECTED, "wfa": Decision.PENDING}

# The "Clipping" project template: production settings, task types, statuses and the
# custom columns (metadata descriptors) every clipping production gets.
TEMPLATE = {
    "name": "Clipping",
    "description": "Stream clipping: episodes are streams, sequences are topic segments, "
                   "shots are clips. Managed by clipper (clipper/review.py).",
    "fps": "30", "ratio": "16:9", "resolution": "1280x720",  # matches review previews
    "production_type": "tvshow", "production_style": "motion-design",
}
TEMPLATE_SETTINGS = {"homepage": "sequences", "is_preview_download_allowed": True}
# (entity type, column name, data type, choices) -> filled by publish, read back by sync
COLUMNS = [
    ("Sequence", "YouTube title", "string", None),
    ("Sequence", "YouTube description", "textarea", None),
    ("Sequence", "Topic", "string", None),
    ("Sequence", "Source audio", "list", ["none", "some", "most"]),
    ("Shot", "YouTube title", "string", None),
    ("Shot", "YouTube description", "textarea", None),
    # The rest of the upload metadata (metadata/<folder>/<video>.json from `clipper meta`):
    # hashtags go at the end of the description, tags are comma-separated.
    ("Sequence", "Hashtags", "string", None),
    ("Sequence", "Tags", "textarea", None),
    ("Sequence", "Playlist", "string", None),
    ("Shot", "Hashtags", "string", None),
    ("Shot", "Tags", "textarea", None),
    ("Shot", "Playlist", "string", None),
    ("Shot", "Hook", "string", None),
    ("Shot", "Category", "list", ["fail", "win", "joke", "insight", "reaction", "hot_take"]),
    ("Shot", "Score", "number", None),
    ("Shot", "Found by", "list", ["chat", "transcript"]),
    # Shorts: the crop layout (a name from [shorts] layouts); edit it and rerun clipper shorts.
    ("Shot", "Layout", "string", None),
    # Scheduling (clipper schedule): Publish at and Hold are yours to set; Scheduled is
    # written by clipper with the slot it assigned.
    ("Sequence", "Publish at", "string", None),
    ("Sequence", "Hold", "boolean", None),
    ("Sequence", "Scheduled", "string", None),
    ("Shot", "Publish at", "string", None),
    ("Shot", "Hold", "boolean", None),
    ("Shot", "Scheduled", "string", None),
    # Written by clipper upload.
    ("Sequence", "YouTube URL", "url", None),
    ("Shot", "YouTube URL", "url", None),
    # Written by clipper rumble; set either one here to mark an item uploaded to Rumble.
    ("Sequence", "Rumble", "list", ["ready", "uploaded"]),
    ("Shot", "Rumble", "list", ["ready", "uploaded"]),
    ("Sequence", "Rumble URL", "url", None),
    ("Shot", "Rumble URL", "url", None),
    # The words on the YouTube thumbnail (thumbnails.json "text"); edit it and sync
    # re-renders the .jpg and re-attaches it here.
    ("Sequence", "Thumbnail text", "string", None),
    ("Shot", "Thumbnail text", "string", None),
]
THUMB_COL = "Thumbnail text"
SEQUENCE_KINDS = ("segment", "compilation")
# Comments clipper adds to record a column change (Hold, Publish at, title); sync never
# takes one of these as the reason for a decision.
NOTE_PREFIX = "[clipper]"


class Listing:
    """File existence and mtimes from one directory scan per folder, not one round trip
    per file: on the NAS share each stat costs tens of milliseconds."""

    def __init__(self):
        self._dirs: dict[Path, dict[str, os.DirEntry]] = {}

    def entry(self, p: Path) -> os.DirEntry | None:
        d = p.parent
        if d not in self._dirs:
            try:
                with os.scandir(d) as it:
                    self._dirs[d] = {e.name: e for e in it}
            except OSError:
                self._dirs[d] = {}
        return self._dirs[d].get(p.name)

    def exists(self, p: Path) -> bool:
        return self.entry(Path(p)) is not None

    def mtime(self, p: Path) -> float | None:
        e = self.entry(Path(p))
        return e.stat().st_mtime if e else None   # cached by the scan on Windows


def connect(rc: dict):
    import gazu

    if not rc["host"] or "<" in rc["host"]:
        raise ClipperError("set [review] host to your Kitsu API URL (Kitsu URL + /api) in clipper.toml")
    token = os.environ.get(rc["token_env"], "")
    if not token:
        raise ClipperError(f"set ${rc['token_env']} to a Kitsu bot token (Kitsu > Bots)")
    gazu.cache.disable()  # cached "not found" lookups would hide what we just created
    gazu.set_host(rc["host"])
    gazu.set_token(token)
    if not gazu.client.host_is_up():
        raise ClipperError(f"Kitsu not reachable at {rc['host']}")
    return gazu


def sequence_name(seg: dict) -> str:
    return f"{seg['id']}_{slug(seg['topic'])}"


def full_title(cfg: dict | None, title: str) -> str:
    """The title in [meta] title_format, e.g. "KINO CASINO | <title>". Left as it is when
    it is already in that format, or when the format would pass YouTube's 100 characters."""
    title = " ".join((title or "").split())
    mc = (cfg or {}).get("meta", {})
    show, fmt = mc.get("show_name") or "", mc.get("title_format") or "{title}"
    prefix = fmt.split("{title}")[0].format(show=show, SHOW=show.upper()).strip()
    if not show or (prefix and title.lower().startswith(prefix.lower())):
        return title
    out = fmt.format(show=show, SHOW=show.upper(), title=title)
    return out if len(out) <= 100 else title


def bare_title(cfg: dict | None, title: str) -> str:
    """The title without [meta] title_format's show prefix ("KINO CASINO | "), for feeds
    that show only the first few words."""
    title = " ".join((title or "").split())
    mc = (cfg or {}).get("meta", {})
    show, fmt = mc.get("show_name") or "", mc.get("title_format") or "{title}"
    prefix = fmt.split("{title}")[0].format(show=show, SHOW=show.upper()).strip()
    if show and prefix and title.lower().startswith(prefix.lower()):
        return title[len(prefix):].strip() or title
    return title


def hashtag_line(text: str | list[str]) -> str:
    """'#A #B' from a list, or from text with or without the #s (commas allowed)."""
    words = text if isinstance(text, list) else (text or "").replace(",", " ").split()
    return " ".join(w if w.startswith("#") else f"#{w}" for w in (w.strip() for w in words) if w)


def draft_fields(draft: dict) -> dict:
    """Kitsu column values from an upload metadata draft. The description column holds the
    description without its closing hashtag line, which has a column of its own."""
    tags = hashtag_line(draft.get("hashtags", []))
    desc = draft["description"].rstrip()
    if tags and desc.endswith(tags):
        desc = desc[:-len(tags)].rstrip()
    return {"YouTube title": draft["title"], "YouTube description": desc, "Hashtags": tags,
            "Tags": ", ".join(draft.get("tags", [])), "Playlist": draft.get("playlist") or ""}


def items(stream_dir: Path, include: list[str], cfg: dict | None = None) -> list[dict]:
    """Every reviewable output of a stream that has been cut: segments, then clips. cfg
    gives titles the show name ([meta] title_format)."""
    cfg = for_stream(cfg, stream_dir) if cfg else cfg
    seg_data = jsonstate.load(stream_dir / "segments.json")
    cut_segs = sorted((s for s in seg_data["segments"] if s["cut"]), key=lambda s: s["start_s"])
    starts = [s["start_s"] for s in cut_segs]

    def segment_of(t: float) -> dict | None:
        i = bisect.bisect_right(starts, t) - 1
        return cut_segs[i] if i >= 0 and t < cut_segs[i]["end_s"] else None

    out = []
    if "segments" in include:
        for side in sorted((stream_dir / "segments").glob("*.json")):
            d = jsonstate.load(side)
            out.append({"kind": "segment", "key": sequence_name(d), "seq": sequence_name(d),
                        "seq_title": d["title"], "type": "Segment",
                        "file": side.with_suffix(".mp4"), "title": d["title"],
                        "description": d["description"],
                        "duration_s": d["end_s"] - d.get("cut_start_s", d["start_s"]),
                        "fields": {"Topic": d["topic"], "Source audio": d["source_share"]},
                        "meta": d})
    if "picks" in include:
        for side in sorted((stream_dir / "clips").glob("*.json")):
            d = jsonstate.load(side)
            seg = segment_of(d["in_s"])
            n = side.name.split("_", 1)[0]  # clips/03_t12_<slug>.json: the pick's number (#77 L13)
            out.append({"kind": "pick", "key": f"c{n}_{d['id']}", "shot": f"c{n}_{d['id']}",
                        "seq": sequence_name(seg) if seg else OTHER_SEQUENCE,
                        "seq_title": seg["title"] if seg else "Clips outside a segment",
                        "type": "Horizontal", "file": side.with_suffix(".mp4"),
                        "title": d["title"], "description": d["reason"],
                        "duration_s": d["out_s"] - d["in_s"],
                        "fields": {"Hook": d["hook"], "Category": d.get("category"),
                                   "Score": d.get("score_a"),
                                   "Found by": found_by(d["id"])},
                        "meta": d})
    if "shorts" in include:
        for side in sorted((stream_dir / "shorts").glob("v*.json")):
            d = jsonstate.load(side)
            seg = segment_of(d["in_s"])
            out.append({"kind": "short", "key": d["key"], "shot": d["key"],
                        "seq": sequence_name(seg) if seg else OTHER_SEQUENCE,
                        "seq_title": seg["title"] if seg else "Clips outside a segment",
                        "type": "Vertical", "file": side.with_suffix(".mp4"),
                        "title": d["title"], "description": d.get("reason", ""),
                        "duration_s": d["duration_s"],
                        "fields": {"Hook": d.get("hook"), "Category": d.get("category"),
                                   "Score": d.get("score_a"), "Layout": d["layout"],
                                   "Found by": found_by(d["id"])},
                        "meta": d})
    if "compilations" in include:
        for side in sorted((stream_dir / "compilations").glob("*.json")):
            d = jsonstate.load(side)
            key = f"comp-{side.stem}"
            out.append({"kind": "compilation", "key": key, "seq": key,
                        "seq_title": d["title"], "type": "Compilation",
                        "file": side.with_suffix(".mp4"), "title": d["title"],
                        "description": d["description"], "duration_s": d["duration_s"],
                        "fields": {"Topic": d["topic"]}, "meta": {**d, "id": key},
                        "segments": d["segments"]})
    ls = Listing()
    out = [it for it in out if ls.exists(it["file"])]
    thumbs_path = stream_dir / "thumbnails.json"
    thumbs = jsonstate.read(thumbs_path, {})
    for it in out:
        it["thumb_key"] = f"{it['file'].parent.name}/{it['file'].stem}"
        if it["thumb_key"] in thumbs:
            it["fields"][THUMB_COL] = thumbs[it["thumb_key"]]["text"]
        # Prefer the upload metadata draft for what reviewers read and edit.
        draft = stream_dir / "metadata" / FOLDERS[it["kind"]] / f"{it['file'].stem}.json"
        if ls.exists(draft):
            upload = draft_fields(jsonstate.load(draft))
        else:
            upload = {"YouTube title": it["title"], "YouTube description": it["description"]}
        upload["YouTube title"] = full_title(cfg, upload["YouTube title"])
        it["title"], it["description"] = upload["YouTube title"], upload["YouTube description"]
        tags = upload.get("Hashtags")
        it["text"] = f"{it['title']}\n\n{it['description']}" + (f"\n\n{tags}" if tags else "")
        it["fields"] = {**upload, **{k: v for k, v in it["fields"].items() if v is not None}}
    return out


def project_name(cfg: dict) -> str:
    return cfg["review"]["project"] or cfg.get("meta", {}).get("show_name") or "Clipper"


def episode_name(stream_dir: Path, rc: dict, tz: str = "America/Los_Angeles") -> str:
    """{date} is the local air date (schedule.timezone), not UTC: an evening show in
    Pacific time starts on the next UTC day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    meta = jsonstate.load(stream_dir / "meta.json")
    aired = datetime.fromisoformat(meta["started_at"]).astimezone(ZoneInfo(tz))
    return rc["episode_name"].format(date=f"{aired:%Y-%m-%d}", stream_id=stream_dir.name,
                                     title=meta.get("title", ""))


def found_by(pick_id: str) -> str:
    return {"t": "transcript", "h": "hand"}.get(pick_id[:1], "chat")


def render_preview(src: Path, dst: Path, rc: dict, codec: str) -> Path:
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    from .cut import encode_args

    vf = f"scale=-2:{rc['preview_height']}" + (",format=nv12" if codec == "h264_qsv" else "")
    args = [a if a != "192k" else "128k" for a in encode_args(codec, rc["preview_quality"])]
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-vf", vf, "-r", str(rc["preview_fps"]), *args,
                    "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(dst)
    return dst


def render_to_size(src: Path, big: Path, duration_s: float, rc: dict, codec: str) -> Path:
    """A preview that fits Kitsu's upload limit (its proxy rejects bigger files): 540p at
    the bitrate that lands ~90% of max_upload_mb. Kept as <preview>.small.mp4 and reused
    while it's newer than the full preview."""
    dst = big.with_name(big.stem + ".small.mp4")
    if dst.exists() and dst.stat().st_mtime >= big.stat().st_mtime:
        return dst
    audio_kbps = 64
    video_kbps = max(150, int(rc["max_upload_mb"] * 8e3 * 0.9 / duration_s) - audio_kbps)
    vf = "scale=-2:540" + (",format=nv12" if codec == "h264_qsv" else "")
    venc = "libx264" if codec in ("copy", "") else codec
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    print(f"preview {big.stem}: {big.stat().st_size / 1e6:.0f} MB is over "
          f"{rc['max_upload_mb']} MB; re-encoding at {video_kbps} kbps", flush=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-vf", vf, "-r", str(rc["preview_fps"]), "-c:v", venc,
                    "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps * 2}k",
                    "-bufsize", f"{video_kbps * 4}k", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
                    "-ac", "1", "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(dst)
    return dst


def concat_previews(parts: list[Path], dst: Path) -> Path:
    """Join segment previews (same encoder settings) into one, without re-encoding."""
    if dst.exists() and all(dst.stat().st_mtime >= p.stat().st_mtime for p in parts):
        return dst
    listing = dst.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts),
                       encoding="utf-8")
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                        "-safe", "0", "-i", str(listing), "-c", "copy",
                        "-movflags", "+faststart", str(tmp)], check=True)
    finally:
        listing.unlink(missing_ok=True)
    tmp.replace(dst)
    return dst


def ensure_task_types(gazu) -> dict:
    return {tname: (gazu.task.get_task_type_by_name(tname, for_entity=entity)
                    or gazu.task.new_task_type(tname, color=color, for_entity=entity))
            for tname, (color, entity) in TASK_TYPES.items()}


def ensure_template(gazu) -> dict:
    """Create or update the Clipping project template (safe to rerun)."""
    pt = gazu.project_template
    tpl = pt.get_project_template_by_name(TEMPLATE["name"])
    if not tpl:
        tpl = pt.new_project_template(**TEMPLATE)
    tpl = pt.update_project_template({"id": tpl["id"], **TEMPLATE, **TEMPLATE_SETTINGS})
    have = {t["id"] for t in pt.all_task_types_for_project_template(tpl)}
    for i, tt in enumerate(ensure_task_types(gazu).values(), 1):
        if tt["id"] not in have:
            pt.add_task_type_to_project_template(tpl, tt, priority=i)
    have = {s["id"] for s in pt.all_task_statuses_for_project_template(tpl)}
    for i, short in enumerate(DECISIONS, 1):
        st = gazu.task.get_task_status_by_short_name(short)
        if st and st["id"] not in have:
            pt.add_task_status_to_project_template(tpl, st, priority=i)
    pt.set_project_template_metadata_descriptors(tpl, [
        {"entity_type": e, "name": n, "data_type": t, "choices": c or []}
        for e, n, t, c in COLUMNS])
    return pt.get_project_template(tpl["id"])


def column_fields(gazu, proj: dict) -> dict[tuple[str, str], str]:
    """(entity type, column name) -> key in the entity's data dict."""
    return {(d["entity_type"], d["name"]): d["field_name"]
            for d in gazu.project.all_metadata_descriptors(proj)}


# A review item is a Kitsu Shot (picks, Shorts) or Sequence (segments, compilations):
# ENTITY[kind]. These read and write its custom columns, keyed by field id (column_fields).

def entity_data(gazu, etype: str, eid: str) -> dict:
    """The item's custom columns as {field id: value}."""
    get = gazu.shot.get_sequence if etype == "Sequence" else gazu.shot.get_shot
    return get(eid).get("data") or {}


def write_data(gazu, etype: str, eid: str, data: dict) -> None:
    """Set some of the item's custom columns ({field id: value}); the others are kept."""
    update = gazu.shot.update_sequence_data if etype == "Sequence" else gazu.shot.update_shot_data
    update(eid, data)


def set_columns(gazu, fmap: dict, etype: str, eid: str, values: dict[str, str],
                strict: bool = False) -> dict:
    """Set columns by name ({"YouTube URL": url}). A column the production doesn't have is
    skipped, or with strict refused (run `clipper review publish` to add it). Returns
    what was written, by field id."""
    missing = [n for n in values if (etype, n) not in fmap]
    if missing and strict:
        raise ClipperError(f"Kitsu has no {etype} column {missing}; run clipper review publish")
    data = {fmap[(etype, n)]: v for n, v in values.items() if (etype, n) in fmap}
    if data:
        write_data(gazu, etype, eid, data)
    return data


def ensure_project(gazu, name: str) -> tuple[dict, dict]:
    """The show's production, built from the Clipping template. An existing production
    without our columns gets the template applied once (it only adds what's missing).
    Returns (project, {task type name: task type})."""
    tpl = ensure_template(gazu)
    proj = gazu.project.get_project_by_name(name)
    if not proj:
        proj = gazu.project.new_project(name, production_type=TEMPLATE["production_type"],
                                        production_style=TEMPLATE["production_style"],
                                        project_template=tpl)
    if not all(k in column_fields(gazu, proj) for k in ((e, n) for e, n, _, _ in COLUMNS)):
        gazu.project_template.apply_project_template(proj, tpl)
    have_types = {t["id"] for t in gazu.task.all_task_types_for_project(proj)}
    types = ensure_task_types(gazu)
    for i, tt in enumerate(types.values(), 1):
        if tt["id"] not in have_types:
            gazu.project.add_task_type(proj, tt, i)
    have_statuses = {s["id"] for s in gazu.task.all_task_statuses_for_project(proj)}
    for short in DECISIONS:
        st = gazu.task.get_task_status_by_short_name(short)
        if st and st["id"] not in have_statuses:
            gazu.project.add_task_status(proj, st)
    return gazu.project.get_project(proj["id"]), types


def ensure_task(gazu, entity: dict, task_type: dict) -> dict:
    found = gazu.task.all_tasks_for_entity_and_task_type(entity, task_type)
    return found[0] if found else gazu.task.new_task(entity, task_type)


def ensure_playlist(gazu, proj: dict, ep: dict, name: str, for_entity: str,
                    entries: list[tuple[dict, str]]) -> dict:
    """Playlist with exactly these (entity, preview_file_id) entries, in order of adding.
    Kitsu playlist names are unique per production (gazu's new_playlist returns any
    existing playlist of that name), so the episode is part of the name."""
    name = f"{ep['name']} · {name}"
    pl = gazu.playlist.get_playlist_by_name(proj, name)
    if not pl:
        pl = gazu.playlist.new_playlist(proj, name, episode=ep, for_entity=for_entity)
    if pl.get("episode_id") != ep["id"]:
        raise RuntimeError(f"playlist {name!r} belongs to another episode")
    pl = gazu.playlist.get_playlist(pl)  # the full record; entry updates need its "shots"
    pl["shots"] = pl.get("shots") or []  # a new playlist reports None, not []
    have = {e.get("entity_id") for e in pl["shots"]}
    for entity, preview_id in entries:
        if entity["id"] in have:
            gazu.playlist.update_entity_preview(pl, entity, preview_id)
        else:
            gazu.playlist.add_entity_to_playlist(pl, entity, preview_file=preview_id)
    return pl


def render_previews(stream_dir: Path, cfg: dict, todo: list[dict]) -> None:
    """Each item's low-res preview (it["preview"]), rendered before any upload (the local,
    slow part). A compilation's preview is its segments' previews joined (no re-encode),
    when they are all there."""
    rc = cfg["review"]
    codec = rc["preview_codec"] or cfg["cut"]["clip_codec"]
    prev_dir = stream_dir / "review"
    prev_dir.mkdir(exist_ok=True)
    seg_previews = {it["meta"]["id"]: prev_dir / f"{it['key']}.mp4"
                    for it in items(stream_dir, ["segments"], cfg)}
    for it in sorted(todo, key=lambda it: it["kind"] == "compilation"):
        t = time.time()
        dst = prev_dir / f"{it['key']}.mp4"
        parts = [seg_previews.get(s) for s in it.get("segments", [])]
        if it["kind"] == "compilation" and parts and all(p and p.exists() for p in parts):
            it["preview"] = concat_previews(parts, dst)
        else:
            it["preview"] = render_preview(it["file"], dst, rc, codec)
        if it["preview"].stat().st_size > rc["max_upload_mb"] * 1e6:
            it["preview"] = render_to_size(it["file"], it["preview"], it["duration_s"], rc, codec)
        print(f"preview {it['key']}: {it['preview'].stat().st_size / 1e6:.0f} MB "
              f"({time.time() - t:.0f}s)", flush=True)


def push_previews(gazu, stream_dir: Path, rc: dict, proj: dict, ep: dict, types: dict,
                  wfa: dict, todo: list[dict], force: bool) -> tuple[dict, list[str]]:
    """Each item whose preview changed (or all, with force) as a new revision of its
    task, with status wfa (waiting for approval); its ids in review_state.json. Returns
    (that state, the keys published)."""
    seqs: dict[str, dict] = {}

    def sequence(name: str, title: str) -> dict:
        if name not in seqs:
            seq = gazu.shot.get_sequence_by_name(proj, name, episode=ep)
            if not seq:
                seq = gazu.shot.new_sequence(proj, name, episode=ep)
                seq["description"] = title
                seq = gazu.shot.update_sequence(seq)
            seqs[name] = seq
        return seqs[name]

    state_path = stream_dir / "review_state.json"
    state = jsonstate.read(state_path, {})
    published: list[str] = []
    for it in todo:
        seq = sequence(it["seq"], it["seq_title"])
        mtime = it["preview"].stat().st_mtime
        if not force and state.get(it["key"], {}).get("preview_mtime") == mtime:
            continue
        if it["kind"] in SEQUENCE_KINDS:
            entity = seq
        else:
            entity = gazu.shot.get_shot_by_name(seq, it["shot"]) or gazu.shot.new_shot(
                proj, seq, it["shot"], nb_frames=int(it["duration_s"] * rc["preview_fps"]),
                description=it["title"], data={"file": it["file"].name})
        task = ensure_task(gazu, entity, types[it["type"]])
        t = time.time()
        _, preview = gazu.task.publish_preview(task, wfa, comment=it["text"],
                                               preview_file_path=str(it["preview"]),
                                               normalize_movie=rc["normalize"],
                                               set_thumbnail=True)
        old = state.get(it["key"], {})  # keep what we last proposed, for the columns
        state[it["key"]] = {**{k: old[k] for k in ("fields", "thumbnail_text") if k in old},
                            "kind": it["kind"], "id": it["meta"]["id"],
                            "file": str(it["file"].relative_to(stream_dir)),
                            "sequence": it["seq"], "entity_id": entity["id"],
                            "task_id": task["id"], "preview_id": preview["id"],
                            "revision": preview.get("revision"), "preview_mtime": mtime,
                            **links(rc, proj["id"], ep["id"], it["kind"], task["id"])}
        published.append(it["key"])
        state = jsonstate.merge(state_path, state, [it["key"]])  # a retake may write it too
        print(f"published {it['seq']}/{it.get('shot', '')} rev {preview.get('revision')} "
              f"({time.time() - t:.0f}s)", flush=True)
    return state, published


def columns_to_fill(values: dict, before: dict, current: dict, tf: str | None,
                    last_thumb: str | None) -> dict:
    """The columns (field id -> value) to write: the empty ones, and those still showing
    what we last proposed (before) when the proposal changed; a value edited in Kitsu or
    Discord is never overwritten. Thumbnail text (field tf) also follows a re-picked
    thumbnail while the column still shows what clipper last wrote there (last_thumb)."""
    missing = {k: v for k, v in values.items()
               if current.get(k) in (None, "") or current.get(k) == before.get(k) != v}
    if tf in values and current.get(tf) == last_thumb != values[tf]:
        missing[tf] = values[tf]
    return missing


def fill_columns(gazu, proj: dict, todo: list[dict], state: dict, state_path: Path) -> dict:
    """Custom columns of the published items (columns_to_fill); what was proposed is kept
    in review_state.json (st["fields"]). Returns the state."""
    fmap = column_fields(gazu, proj)
    filled = 0
    for it in todo:
        st = state.get(it["key"])
        if not st:
            continue
        etype = ENTITY[it["kind"]]
        current = entity_data(gazu, etype, st["entity_id"])
        values = {fmap[(etype, n)]: str(v) for n, v in it["fields"].items() if (etype, n) in fmap}
        before = {fmap[(etype, n)]: str(v) for n, v in st.get("fields", {}).items()
                  if (etype, n) in fmap}
        tf = fmap.get((etype, THUMB_COL))
        missing = columns_to_fill(values, before, current, tf, st.get("thumbnail_text"))
        if tf in missing:
            st["thumbnail_text"] = missing[tf]
        if missing:
            write_data(gazu, etype, st["entity_id"], missing)
            filled += 1
        st["fields"] = {n: str(v) for n, v in it["fields"].items()}  # what we proposed
    state = jsonstate.merge(state_path, state, [it["key"] for it in todo if it["key"] in state])
    print(f"custom columns filled on {filled} items")
    return state


def make_playlists(gazu, stream_dir: Path, proj: dict, ep: dict, state: dict) -> None:
    """Playlists: every clip, every Short, and each compilation as its segments in
    stream order."""
    clips = [(gazu.shot.get_shot(st["entity_id"]), st["preview_id"])
             for key, st in sorted(state.items()) if st["kind"] == "pick"]
    if clips:
        ensure_playlist(gazu, proj, ep, "Clips", "shot", clips)
    shorts = [(gazu.shot.get_shot(st["entity_id"]), st["preview_id"])
              for key, st in sorted(state.items()) if st["kind"] == "short"]
    if shorts:
        ensure_playlist(gazu, proj, ep, "Shorts", "shot", shorts)
    seg_data = jsonstate.load(stream_dir / "segments.json")
    by_id = {st["id"]: st for st in state.values() if st["kind"] == "segment"}
    for comp in seg_data.get("compilations", []):
        if all(s in by_id for s in comp["segments"]):
            entries = [(gazu.shot.get_sequence(by_id[s]["entity_id"]), by_id[s]["preview_id"])
                       for s in comp["segments"]]
            ensure_playlist(gazu, proj, ep, f"Compilation: {comp['topic']}", "sequence", entries)
            print(f"playlist Compilation: {comp['topic']} ({len(entries)} segments)")


def publish(stream_dir: Path, cfg: dict, force: bool = False, notify: bool = True,
            only: list[str] | None = None) -> None:
    """only: publish just these kinds of [review] include (e.g. re-cut Shorts without
    sending re-cut segments back for review too)."""
    cfg = for_stream(cfg, stream_dir)
    rc = cfg["review"]
    todo = items(stream_dir, [k for k in rc["include"] if not only or k in only], cfg)
    render_previews(stream_dir, cfg, todo)

    gazu = connect(rc)
    proj, types = ensure_project(gazu, project_name(cfg))
    wfa = gazu.task.get_task_status_by_short_name("wfa")
    ep_name = episode_name(stream_dir, rc, cfg["schedule"]["timezone"])
    ep = gazu.shot.get_episode_by_name(proj, ep_name) or gazu.shot.new_episode(proj, ep_name)
    state_path = stream_dir / "review_state.json"
    state, published = push_previews(gazu, stream_dir, rc, proj, ep, types, wfa, todo, force)
    state = fill_columns(gazu, proj, todo, state, state_path)
    make_playlists(gazu, stream_dir, proj, ep, state)
    print(f"{len(todo)} items in Kitsu: {rc['host'].removesuffix('/api')} "
          f"({project_name(cfg)} / {ep_name})")
    if cfg["thumbnail"]["kitsu"]:
        attach_thumbnails(stream_dir, cfg, gazu, keys=[it["key"] for it in todo] if only else None)
    if notify and published and cfg["discord"]["enabled"]:
        from . import notify as discord_notify
        discord_notify.post(stream_dir, cfg, keys=published)


def attach_thumbnails(stream_dir: Path, cfg: dict, gazu=None, keys: list[str] | None = None) -> int:
    """Add each item's .jpg thumbnail to its latest published revision (beside the preview
    movie, so the player shows both) and make it the item's Kitsu thumbnail. Skips items
    whose thumbnail and revision haven't changed; replaces our own earlier upload."""
    cfg = for_stream(cfg, stream_dir)
    state_path = stream_dir / "review_state.json"
    state = jsonstate.load(state_path)
    todo = []
    for key, st in state.items():
        if keys is not None and key not in keys:
            continue
        jpg = (stream_dir / st["file"].replace("\\", "/")).with_suffix(".jpg")
        th = st.get("thumbnail") or {}
        if jpg.exists() and (th.get("movie_preview_id") != st["preview_id"]
                             or th.get("mtime") != jpg.stat().st_mtime):
            todo.append((key, st, jpg, th))
    if not todo:
        return 0
    gazu = gazu or connect(cfg["review"])
    done = {}
    for key, st, jpg, th in todo:
        comment = next((c for c in gazu.task.all_comments_for_task(st["task_id"])
                        if st["preview_id"] in [p if isinstance(p, str) else p.get("id")
                                                for p in c.get("previews") or []]), None)
        if comment is None:
            print(f"  thumbnail {key}: published revision not found in Kitsu; republish it")
            continue
        if th.get("preview_id") and th.get("movie_preview_id") == st["preview_id"]:
            gazu.task.remove_preview_from_comment(st["task_id"], comment, th["preview_id"])
        pf = gazu.task.add_preview(st["task_id"], comment, str(jpg), normalize_movie=False,
                                   revision=st.get("revision"), no_job=True)
        gazu.task.set_main_preview(pf)
        done[key] = {"preview_id": pf["id"], "movie_preview_id": st["preview_id"],
                     "mtime": jpg.stat().st_mtime}
    with jsonstate.update(state_path, {}) as state:  # a publish may be writing it too
        for key, th in done.items():
            if key in state:
                state[key]["thumbnail"] = th
    print(f"thumbnails attached in Kitsu: {len(done)}")
    return len(done)


def web_url(rc: dict) -> str:
    return rc["host"].rstrip("/").removesuffix("/api")


def links(rc: dict, project_id: str, episode_id: str, kind: str, task_id: str) -> dict:
    """Kitsu web links for an item: its task (preview player + comments) and its episode."""
    base = f"{web_url(rc)}/productions/{project_id}/episodes/{episode_id}"
    kind_path = "sequences" if kind in SEQUENCE_KINDS else "shots"
    return {"kitsu_url": f"{base}/{kind_path}/tasks/{task_id}",
            "episode_url": f"{base}/sequences"}


def ensure_links(stream_dir: Path, cfg: dict, gazu=None) -> dict:
    """review_state.json with kitsu_url/episode_url on every item (older publishes lack them)."""
    cfg = for_stream(cfg, stream_dir)
    state_path = stream_dir / "review_state.json"
    state = jsonstate.load(state_path)
    missing = [st for st in state.values() if "kitsu_url" not in st]
    if missing:
        gazu = gazu or connect(cfg["review"])
        for st in missing:
            task = gazu.task.get_task(st["task_id"])
            if st["kind"] in SEQUENCE_KINDS:
                ep_id = gazu.shot.get_sequence(st["entity_id"])["parent_id"]
            else:
                ep_id = gazu.shot.get_shot(st["entity_id"])["episode_id"]
            st.update(links(cfg["review"], task["project_id"], ep_id, st["kind"], task["id"]))
        # Re-read before writing: a publish may be updating this file at the same time.
        found = {k: {f: st[f] for f in ("kitsu_url", "episode_url")}
                 for k, st in state.items() if "kitsu_url" in st}
        with jsonstate.update(state_path, {}) as state:
            for k, st in state.items():
                if "kitsu_url" not in st and k in found:
                    st.update(found[k])
    return state


def decision_comment(gazu, task: dict) -> str:
    """The latest comment made with the task's current status (the reason for a reject
    or retake), skipping clipper's own notes about column changes."""
    return decision_comment_entry(gazu, task)[1]


def decision_comment_entry(gazu, task: dict) -> tuple[str, str]:
    """(comment id, text) of decision_comment; ("", "") if there is none."""
    comments = sorted(gazu.task.all_comments_for_task(task),
                      key=lambda c: c.get("created_at") or "", reverse=True)
    for c in comments:
        text = (c.get("text") or "").strip()
        if c.get("task_status_id") == task["task_status_id"] and text \
                and not text.startswith(NOTE_PREFIX):
            return c["id"], text
    return "", ""


def apply_thumbnail_text(stream_dir: Path, cfg: dict, gazu, fmap: dict,
                         edits: dict[str, tuple[dict, dict, dict]]) -> None:
    """Thumbnail text changed in Kitsu (by hand, or by actions.thumbtext): write it to
    thumbnails.json and re-render the .jpg, which also re-attaches it in Kitsu. edits:
    {key: (state entry, task, fields read from Kitsu)}. Text that can't be printed is put
    back to what the thumbnail shows, with a note on the task; cleaned-up text (extra
    symbols, case) is written back as printed."""
    cfg = for_stream(cfg, stream_dir)
    from . import thumbs

    tpath = stream_dir / "thumbnails.json"
    tstate = jsonstate.read(tpath, {})
    texts, applied = {}, {}
    for key, (st, task, fields) in edits.items():
        raw = fields[THUMB_COL]
        tkey = thumbs.video_key(stream_dir / st["file"].replace("\\", "/"))
        if tkey not in tstate:
            print(f"  {key}: no thumbnail to put {raw!r} on yet; run clipper thumbs")
            continue
        try:
            text = thumbs.check_text(cfg, raw)
        except ClipperError as e:
            text = tstate[tkey]["text"]
            note = f"{NOTE_PREFIX} Thumbnail text not used: {e}. Kept {text!r}"
            gazu.task.add_comment(task, gazu.task.get_task_status(task["task_status_id"]), note)
            print(f"  {key}: {note}")
        else:
            texts[tkey] = text
        if text != raw:
            etype = ENTITY[st["kind"]]
            write_data(gazu, etype, st["entity_id"], {fmap[(etype, THUMB_COL)]: text})
            fields[THUMB_COL] = text
        applied[key] = text
    for tkey, text in thumbs.set_texts(stream_dir, cfg, texts).items():
        print(f"  thumbnail {tkey}: {text}")
    # Re-read before writing: the re-render (attach_thumbnails) just wrote this file.
    with jsonstate.update(stream_dir / "review_state.json", {}) as state:
        for key, text in applied.items():
            if key in state:
                state[key]["thumbnail_text"] = text


def sync(stream_dir: Path, cfg: dict, keys: list[str] | None = None,
         quiet: bool = False) -> dict:
    """Read decisions and columns from Kitsu into review.json. keys: refresh only these
    items and keep the rest of review.json as it was."""
    cfg = for_stream(cfg, stream_dir)
    rc = cfg["review"]
    state = jsonstate.load(stream_dir / "review_state.json")
    review_path = stream_dir / "review.json"
    gazu = connect(rc)
    fmap = column_fields(gazu, gazu.project.get_project_by_name(project_name(cfg)))
    statuses = {s["id"]: s["short_name"] for s in gazu.task.all_task_statuses()}
    out = {}
    thumb_edits = {}
    for key, st in state.items():
        if keys is not None and key not in keys:
            continue
        task = gazu.task.get_task(st["task_id"])
        status = statuses[task["task_status_id"]]
        etype = ENTITY[st["kind"]]
        data = entity_data(gazu, etype, st["entity_id"])
        fields = {n: data.get(f) for (e, n), f in fmap.items() if e == etype}
        if fields.get(THUMB_COL) and fields[THUMB_COL] != st.get("thumbnail_text"):
            thumb_edits[key] = (st, task, fields)
        proposed = st.get("fields", {})
        cid, comment = (decision_comment_entry(gazu, task) if status in ("retake", "rejected")
                        else ("", ""))
        out[key] = {"kind": st["kind"], "id": st["id"], "file": st["file"],
                    "status": status, "decision": str(DECISIONS.get(status, Decision.PENDING)),
                    "comment": comment, "comment_id": cid,
                    "fields": fields,
                    "edited": sorted(n for n, v in fields.items()
                                     if n in proposed and (v or "") != proposed[n]),
                    "updated_at": task.get("updated_at")}
    if thumb_edits:
        apply_thumbnail_text(stream_dir, cfg, gazu, fmap, thumb_edits)
    # Merged under the lock: the bot and the war room both sync single items at once.
    with jsonstate.locked(review_path):
        if keys is not None:
            out = {**jsonstate.read(review_path, {}), **out}
        out = {k: out[k] for k in state if k in out}  # state order; drops items no longer published
        jsonstate.write(review_path, out)
    if quiet:
        return out
    counts: dict[str, int] = {}
    for d in out.values():
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    print(f"review: {counts}")
    for key, d in out.items():
        if d["decision"] in ("changes_requested", "rejected"):
            print(f"  {key}: {d['comment'][:120]}")
        if d["edited"]:
            print(f"  {key} edited: {', '.join(d['edited'])}")
    return out
