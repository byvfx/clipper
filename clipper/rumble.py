"""Rumble release: schedule.json entries -> ready-to-upload folders, tracked per platform.

Rumble has no upload API (Oct 2026) and its terms forbid automating the site, so this
prepares everything for the web form instead (rumble.com/upload.php):

  clipper rumble <stream>            one folder per scheduled item under paths.rumble:
                                     the video (hard link), thumbnail.jpg if `clipper
                                     thumbs` made one, upload.txt to copy from
  clipper rumble <stream> --open     ...then open the folder and the upload page, so the
                                     upload is a drag and drop plus pasting upload.txt
  clipper rumble --done s02 --url https://rumble.com/v...
                                     mark an item uploaded (schedule.json + Kitsu)
  clipper rumble --status [stream]   what's ready, uploaded, or not prepared yet

Title and description come from the same place as YouTube's (Kitsu's columns, else the
metadata draft), with the credit line guaranteed. Each schedule.json entry records the
platform under "platforms": {"rumble": {"status": "ready" | "uploaded", ...}}; `clipper
schedule` keeps it across replans. In Kitsu the "Rumble" column shows ready/uploaded and
"Rumble URL" the link; setting either there and running `clipper review sync` (or
`clipper schedule`) counts as marking it done.

Licensing: keep [rumble] license = "Rumble Only". It is non-exclusive and removable; the
Video Management options are exclusive (and option A Content-ID-claims the YouTube copy).
"""

from __future__ import annotations

import json
import os
import shutil
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonstate, youtube
from .errors import ClipperError
from .states import split_key

LICENSE_NOTES = {
    "Rumble Only": "non-exclusive, monetized on Rumble only; the YouTube copy is unaffected",
    "Personal Use": "no ads, no earnings",
    "Video Management": "EXCLUSIVE for 50 years; Rumble Content-ID-claims the YouTube copy",
    "Video Management Excluding YouTube": "EXCLUSIVE; you may not monetize it elsewhere",
}


# ---------- schedule.json ----------

def load_schedule(cfg: dict) -> dict:
    path: Path = cfg["paths"]["schedule"]
    if not path.exists():
        raise ClipperError("no schedule.json; run: clipper schedule")
    return jsonstate.load(path)


def save_platform(cfg: dict, updates: dict[str, dict | None], platform: str = "rumble") -> None:
    """Write entries' records for a platform (rumble, tiktok) into schedule.json, re-read just
    before writing so a concurrent `clipper schedule` or `clipper upload` isn't overwritten.
    None clears."""
    path: Path = cfg["paths"]["schedule"]
    with jsonstate.update(path) as sched:
        for e in sched["entries"] + sched.get("unscheduled", []):
            if e["key"] in updates:
                plats = e.setdefault("platforms", {})
                if updates[e["key"]] is None:
                    plats.pop(platform, None)
                else:
                    plats[platform] = updates[e["key"]]
                if not plats:
                    e.pop("platforms")


def rumble_of(e: dict) -> dict:
    return (e.get("platforms") or {}).get("rumble") or {}


def short_key(e: dict) -> str:
    return split_key(e["key"])[1]


def resolve(sched: dict, ref: str) -> dict:
    """An entry by key, or by a unique piece of it: s02, s02_the-quartering, comp-..., with
    or without the stream id in front."""
    stream = None
    if "/" in ref:
        stream, ref = ref.rsplit("/", 1)
    pool = [e for e in sched["entries"] if not stream or e["stream"] == stream]
    hits = ([e for e in pool if short_key(e) == ref]
            or [e for e in pool if short_key(e).split("_")[0] == ref]
            or [e for e in pool if short_key(e).startswith(ref)])
    if not hits:
        raise ClipperError(f"no schedule.json entry {ref!r}; only scheduled items can be sent")
    if len(hits) > 1:
        raise ClipperError(f"{ref!r} matches {', '.join(e['key'] for e in hits[:8])}; give more of it")
    return hits[0]


# ---------- metadata ----------

def text_for(e: dict, cfg: dict) -> dict:
    rc = cfg["rumble"]
    m = youtube.metadata(e, cfg)
    video = Path(e["file"])
    draft_path = m["stream_dir"] / "metadata" / video.parent.name / f"{video.stem}.json"
    draft = jsonstate.read(draft_path, {})
    desc = m["description"]
    credit = draft.get("credit", "")
    if credit and credit not in desc:  # a Kitsu edit dropped it
        desc = f"{desc}\n\n{credit}".strip()
    if rc["footer"] and ("{youtube_url}" not in rc["footer"] or e.get("url")):
        desc = f"{desc}\n\n{rc['footer'].format(youtube_url=e.get('url', ''))}"
    tags = [youtube.clean(t, 100) for t in draft.get("tags", [])]
    tags = [t for t in dict.fromkeys(tags) if t][:rc["max_tags"] or None]
    return {"title": youtube.clean(m["title"], rc["max_title_chars"]),
            "description": youtube.clean(desc, rc["max_description_chars"]),
            "tags": tags, "review_key": m["review_key"], "stream_dir": m["stream_dir"]}


def release_time(e: dict, cfg: dict) -> str:
    rc, tz = cfg["rumble"], ZoneInfo(cfg["schedule"]["timezone"])
    if rc["schedule"] != "youtube":
        return "publish now"
    when = datetime.fromisoformat(e["publish_at"]).astimezone(tz) + timedelta(
        minutes=rc["offset_minutes"])
    if when <= datetime.now(tz) + timedelta(minutes=15):
        return "publish now (its slot has passed)"
    return f"Scheduled: {when:%Y-%m-%d %H:%M %Z} ({when:%a})"


# ---------- folders ----------

def place_file(src: Path, dst: Path, mode: str) -> None:
    """Hard link (no extra disk; falls back to a copy across drives), or a copy."""
    if dst.exists():
        same = (os.path.samefile(src, dst) if mode == "hardlink" else
                (dst.stat().st_size, dst.stat().st_mtime) == (src.stat().st_size, src.stat().st_mtime))
        if same:
            return
        dst.unlink()
    if mode == "hardlink":
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def upload_txt(e: dict, t: dict, cfg: dict, thumb: bool, video_name: str) -> str:
    rc = cfg["rumble"]
    license_note = LICENSE_NOTES.get(rc["license"], "")
    sections = [
        f"RUMBLE UPLOAD  {e['key']}  ({e['kind']})",
        f"1. Open {rc['upload_url']} and drag {video_name} onto it.",
        "2. Paste each field below, then submit.",
        f"3. When it's live: clipper rumble --done {e['key']} --url <the video's Rumble link>",
        "=" * 60,
        f"TITLE\n{t['title']}",
        f"DESCRIPTION\n{t['description']}",
        f"TAGS\n{', '.join(t['tags'])}",
        f"CATEGORY\n{rc['category']}",
        "THUMBNAIL\n" + ("thumbnail.jpg (1280x720)" if thumb
                          else "none made; pick a frame on the form (or run clipper thumbs)"),
        f"LICENSING\n{rc['license']}" + (f"  ({license_note})" if license_note else ""),
        f"VISIBILITY\n{rc['visibility']}; {release_time(e, cfg)}",
    ]
    return "\n\n".join(sections) + "\n"


def prepare(cfg: dict, e: dict, kitsu: list) -> dict:
    rc = cfg["rumble"]
    video = Path(e["file"])
    if not video.exists():
        raise FileNotFoundError(video)
    folder = cfg["paths"]["rumble"] / e["stream"] / short_key(e)
    folder.mkdir(parents=True, exist_ok=True)
    place_file(video, folder / video.name, rc["link"])
    jpg = video.with_suffix(".jpg")
    if jpg.exists():
        place_file(jpg, folder / "thumbnail.jpg", rc["link"])
    elif (folder / "thumbnail.jpg").exists():
        (folder / "thumbnail.jpg").unlink()
    t = text_for(e, cfg)
    (folder / "upload.txt").write_text(upload_txt(e, t, cfg, jpg.exists(), video.name),
                                       encoding="utf-8")
    fields = {k: t[k] for k in ("title", "description", "tags")}
    (folder / "upload.json").write_text(json.dumps(
        {"key": e["key"], "video": video.name, **fields, "category": rc["category"],
         "license": rc["license"], "visibility": rc["visibility"],
         "release": release_time(e, cfg), "thumbnail": "thumbnail.jpg" if jpg.exists() else None},
        indent=2, ensure_ascii=False), encoding="utf-8")
    kitsu.append((e, {"Rumble": "ready"}))
    return {"status": "ready", "folder": str(folder),
            "prepared_at": datetime.now(UTC).isoformat(timespec="seconds")}


# ---------- Kitsu ----------

def push_kitsu(cfg: dict, updates: list[tuple[dict, dict]]) -> None:
    """Set Rumble / Rumble URL columns; adds the columns to the production if missing."""
    if not cfg["rumble"]["kitsu"] or not updates:
        return
    from . import review
    from .config import for_stream

    try:
        gazu = review.connect(cfg["review"])
        fmaps: dict[str, dict] = {}  # per Kitsu production, i.e. per show
        states: dict[Path, dict] = {}
        for e, values in updates:
            sdir = cfg["paths"]["streams"] / e["stream"]
            name = review.project_name(for_stream(cfg, sdir))
            if name not in fmaps:
                fmap = review.column_fields(gazu, gazu.project.get_project_by_name(name))
                if ("Sequence", "Rumble") not in fmap:
                    proj, _ = review.ensure_project(gazu, name)
                    fmap = review.column_fields(gazu, proj)
                fmaps[name] = fmap
            fmap = fmaps[name]
            if sdir not in states:
                p = sdir / "review_state.json"
                states[sdir] = jsonstate.read(p, {})
            st = states[sdir].get(short_key(e))
            if not st:
                continue  # compilations have no Kitsu entity
            review.set_columns(gazu, fmap, review.ENTITY[st["kind"]], st["entity_id"], values)
    except Exception as ex:  # noqa: BLE001  # schedule.json is the record; Kitsu is a view
        print(f"  warning: Kitsu not updated: {ex}")


def done_in_kitsu(cfg: dict, e: dict) -> str | None:
    """A Rumble URL (or "uploaded") set in Kitsu and synced into review.json."""
    p = cfg["paths"]["streams"] / e["stream"] / "review.json"
    if not p.exists():
        return None
    f = jsonstate.load(p).get(short_key(e), {}).get("fields", {})
    url = (f.get("Rumble URL") or "").strip()
    return url or ("" if f.get("Rumble") == "uploaded" else None)


# ---------- commands ----------

def mark_done(cfg: dict, ref: str, url: str = "", undo: bool = False) -> None:
    e = resolve(load_schedule(cfg), ref)
    prev = rumble_of(e)
    if undo:
        rec = None
        if prev.get("folder"):  # back to prepared
            rec = {k: v for k, v in prev.items() if k not in ("url", "uploaded_at")}
            rec["status"] = "ready"
        save_platform(cfg, {e["key"]: rec})
        push_kitsu(cfg, [(e, {"Rumble": "ready" if rec else "", "Rumble URL": ""})])
        print(f"{e['key']}: Rumble mark cleared")
        return
    rec = {**prev, "status": "uploaded", "url": url or prev.get("url", ""),
           "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds")}
    save_platform(cfg, {e["key"]: rec})
    push_kitsu(cfg, [(e, {"Rumble": "uploaded", "Rumble URL": rec["url"]})])
    print(f"{e['key']}: on Rumble{' at ' + rec['url'] if rec['url'] else ''}")


def run(cfg: dict, stream: str | None = None, items: list[str] | None = None,
        open_after: bool = False, dry_run: bool = False) -> None:
    rc = cfg["rumble"]
    sched = load_schedule(cfg)
    if items:
        todo = [resolve(sched, r if "/" in r or not stream else f"{stream}/{r}") for r in items]
    else:
        todo = [e for e in sched["entries"] if (not stream or e["stream"] == stream)
                and e["kind"] in rc["kinds"]]
    if not todo:
        raise ClipperError(f"nothing scheduled{' for ' + stream if stream else ''}; "
                         "approve items in Kitsu and run: clipper schedule")
    updates, kitsu, made = {}, [], []
    for e in todo:
        r = rumble_of(e)
        from_kitsu = done_in_kitsu(cfg, e) if r.get("status") != "uploaded" else None
        if from_kitsu is not None:
            updates[e["key"]] = {**r, "status": "uploaded", "url": from_kitsu, "uploaded_at":
                                 datetime.now(UTC).isoformat(timespec="seconds")}
            print(f"  {e['key']}: marked uploaded in Kitsu {from_kitsu}")
            continue
        if r.get("status") == "uploaded":
            print(f"  {e['key']}: already on Rumble {r.get('url', '')}")
            continue
        if dry_run:
            t = text_for(e, cfg)
            print(f"  {e['key']}: {t['title']}  ({len(t['tags'])} tags, "
                  f"thumbnail {'yes' if Path(e['file']).with_suffix('.jpg').exists() else 'no'}, "
                  f"{release_time(e, cfg)})")
            continue
        try:
            updates[e["key"]] = {**r, **prepare(cfg, e, kitsu)}
        except FileNotFoundError as ex:
            print(f"  {e['key']}: video missing ({ex}); rerun clipper cut")
            continue
        made.append(Path(updates[e["key"]]["folder"]))
        print(f"  ready {made[-1]}")
    if dry_run:
        return
    if updates:
        save_platform(cfg, updates)
    push_kitsu(cfg, kitsu)
    print(f"{len(made)} ready for Rumble -> {cfg['paths']['rumble']}")
    if open_after and made:
        target = made[0] if len(made) == 1 else made[0].parent
        if hasattr(os, "startfile"):
            os.startfile(target)  # Explorer window to drag from
        else:
            print(f"  folder: {target}")
        webbrowser.open(rc["upload_url"])


def status(cfg: dict, stream: str | None = None) -> None:
    sched = load_schedule(cfg)
    tz = ZoneInfo(sched["timezone"])
    rows = [e for e in sched["entries"] if not stream or e["stream"] == stream]
    counts: dict[str, int] = {}
    for e in rows:
        r = rumble_of(e)
        st = r.get("status") or ("-" if e["kind"] in cfg["rumble"]["kinds"] else "skipped")
        counts[st] = counts.get(st, 0) + 1
        t = datetime.fromisoformat(e["publish_at"]).astimezone(tz)
        print(f"  {t:%a %m-%d %H:%M}  {e['kind']:<11} rumble {st:<8} {e['title'][:60]}"
              + (f"  {r['url']}" if r.get("url") else ""))
    print(f"{len(rows)} scheduled: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
