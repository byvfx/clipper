"""`clipper thumbs`: candidate frames -> the model's pick -> a cover per cut video, and a
9:16 one per Short; the reviewers' text and photo changes, re-rendered without model calls."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import jsonstate
from ..config import for_stream
from ..errors import ClipperError
from ..llm import Claude, preflight
from ..meta import duration, load_assets
from ..prompts import Prompts
from ..scoring import load_signals
from .classic import compose
from .enquirer import compose_enquirer, compose_vertical, cover_context, enquirer_setup
from .frames import (
    asset_key,
    face_frames,
    frames,
    grab,
    photo_labels,
    photo_name,
    photos,
)
from .pick import clean_text, pick, unsafe


def printable(e: dict, bleeper) -> dict:
    """The entry with its text made safe: a hand edit with a listed word falls back to
    the original headline (enquirer) or no text (classic)."""
    if not unsafe(e["text"], bleeper):
        return e
    if e.get("style") == "enquirer":
        print(f"  {e['id']}: text has a banned word; using the original headline")
        return {**e, "text": " ".join(e["headline"])}
    print(f"  {e['id']}: text {e['text']!r} has a banned word; printing none")
    return {**e, "text": ""}


def short_covers(stream_dir: Path, state: dict, tc_enq: dict, date: str, own_band: str, bleeper,
                 clip_ids: set[str] | None = None, short_keys: set[str] | None = None) -> list[dict]:
    """A vertical cover beside each Short (shorts/<name>.jpg) whose clip has an enquirer
    choice in thumbnails.json; clip_ids / short_keys limit which. Returns what was written."""
    shorts_path = stream_dir / "shorts.json"
    if not shorts_path.exists():
        return []
    by_clip = {e["id"]: (k, e) for k, e in state.items()
               if e.get("kind") == "clip" and e.get("style") == "enquirer"}
    out = []
    for skey, s in jsonstate.load(shorts_path).items():
        cid = skey.split("_", 1)[1] if "_" in skey else skey  # v01_t20 -> t20
        video = stream_dir / s["file"]
        if (clip_ids is not None and cid not in clip_ids) or (short_keys is not None and skey not in short_keys):
            continue
        if cid not in by_clip or not video.exists():
            continue
        ckey, e = by_clip[cid]
        e = printable(e, bleeper)
        dst = video.with_suffix(".jpg")
        size = compose_vertical(stream_dir, ckey, e, cover_context(ckey, state, stream_dir, tc_enq, own_band),
                                tc_enq, date, stream_dir / f"{ckey}.mp4", dst)
        out.append({**e, "key": f"shorts/{video.stem}", "short": skey, "jpg": dst, "bytes": size})
    return out


def shorts_only(stream_dir: Path, cfg: dict, short_keys: set[str] | None = None,
                kitsu: bool = False) -> int:
    """Vertical covers for these Shorts (all if None), no model calls: `clipper shorts`
    right after it renders them, `clipper thumbs --shorts` for a stream's existing ones
    (kitsu: attach just those). 0 if the stream has no enquirer thumbnails yet."""
    cfg = for_stream(cfg, stream_dir)
    from ..bleep import Bleeper

    tc = {**cfg["thumbnail"]}
    state_path = stream_dir / "thumbnails.json"
    if tc["style"] != "enquirer" or not state_path.exists():
        return 0
    tc["_photos"] = photos(tc)
    tc_enq, date, own_band = enquirer_setup(stream_dir, cfg, tc)
    state = jsonstate.load(state_path)
    done = short_covers(stream_dir, state, tc_enq, date, own_band,
                        Bleeper({**cfg["bleep"], "mode": "bleep"}), short_keys=short_keys)
    for r in done:
        print(f"  cover {r['jpg'].relative_to(stream_dir)}  {r['bytes'] / 1e3:.0f} KB  {r['text']}")
    if done and kitsu and tc["kitsu"] and (stream_dir / "review_state.json").exists():
        from .. import review as kitsu_review
        kitsu_review.attach_thumbnails(stream_dir, cfg, keys=[r["short"] for r in done])
    return len(done)


def asset_info(stream_dir: Path, x: dict, review: dict) -> dict:
    """Title as it will upload (Kitsu edit > metadata draft > cut title), hook, about."""
    draft = stream_dir / "metadata" / x["video"].parent.name / f"{x['video'].stem}.json"
    d = jsonstate.read(draft, {})
    rel = x["video"].relative_to(stream_dir).as_posix()
    fields = next((r.get("fields") or {} for r in review.values()
                   if r.get("file", "").replace("\\", "/") == rel), {})
    return {"title": fields.get("YouTube title") or d.get("title") or x["title"],
            "hook": x.get("hook", ""), "playlist": d.get("playlist") or x.get("topic") or "",
            "about": (d.get("lead") or x.get("summary") or x.get("body") or "")[:600]}


def video_key(video: Path) -> str:
    """thumbnails.json key for a cut video (asset_key without loading the asset)."""
    return f"{video.parent.name}/{video.stem}"


def check_text(cfg: dict, text: str) -> str:
    """Reviewer text for a thumbnail, cleaned like the model's; ClipperError if it's empty
    or has a word the channel never prints."""
    from ..bleep import Bleeper

    tc = cfg["thumbnail"]
    out = clean_text(text, tc["max_words"])
    if not out:
        raise ClipperError("thumbnail text is empty")
    if len(text.split()) > tc["max_words"]:
        raise ClipperError(f"thumbnail text is at most {tc['max_words']} words")
    bleeper = Bleeper({**cfg["bleep"], "mode": "bleep"})
    if unsafe(out, bleeper):
        raise ClipperError(f"thumbnail text {bleeper.mask(out)!r} has a word the channel never prints")
    return out


def set_texts(stream_dir: Path, cfg: dict, texts: dict[str, str]) -> dict[str, str]:
    """New text for thumbnails ({thumbnails.json key: text}, already check_text'ed), then
    re-render them (and re-attach them in Kitsu) without model calls. The highlight is
    kept only while its word is still in the text. Returns the keys changed."""
    state_path = stream_dir / "thumbnails.json"
    state = jsonstate.read(state_path, {})
    missing = [k for k in texts if k not in state]
    if missing:
        raise ClipperError(f"no thumbnail yet for {missing}; run clipper thumbs first")
    norm = lambda w: re.sub(r"\W", "", w.upper())  # as compose() matches the highlight
    changed = {}
    for key, text in texts.items():
        e = state[key]
        if e["text"] == text:
            continue
        e["text"] = text
        if e.get("highlight") and norm(e["highlight"]) not in {norm(w) for w in text.split()}:
            e["highlight"] = ""
        changed[key] = text
    if changed:  # only these keys: the worker may be writing other picks right now
        state = jsonstate.merge(state_path, state, list(changed))
        run(stream_dir, cfg, ids=[state[k]["id"] for k in changed], compose_only=True)
    return changed


def set_photos(stream_dir: Path, cfg: dict, ids: list[str], photo: str | None = None,
               teaser_photo: str | None = None, kitsu: bool = True) -> list[str]:
    """Put a [thumbnail] photos portrait on enquirer covers by hand ("" clears): `photo`
    as each one's main face, `teaser_photo` beside its teaser on the other covers. Then
    re-render the whole stream (teasers show on every cover) without model calls, and
    re-attach in Kitsu. Returns the keys changed."""
    tc = {**for_stream(cfg, stream_dir)["thumbnail"]}
    tc["_photos"] = photos(tc)
    state_path = stream_dir / "thumbnails.json"
    state = jsonstate.read(state_path, {})
    keys = [k for k, e in state.items() if e["id"] in ids]
    if missing := sorted(set(ids) - {state[k]["id"] for k in keys}):
        raise ClipperError(f"no thumbnail yet for {missing}; run clipper thumbs first")
    changed = []
    for field, name in (("photo", photo), ("teaser_photo", teaser_photo)):
        if name is None:
            continue
        on_file = photo_name(tc, name)
        if name and not on_file:
            raise ClipperError(f"no photo of {name!r} in [thumbnail] photos "
                             f"({tc['photos'] or 'unset'}): {', '.join(tc['_photos']) or 'none'}")
        for k in keys:
            if state[k].get("style") != "enquirer":
                raise ClipperError(f"{state[k]['id']}: photos are for enquirer covers only")
            if state[k].get(field, "") != on_file:
                if on_file:
                    state[k][field] = on_file
                else:
                    state[k].pop(field, None)
                state[k].pop(f"{field}_label", None)  # the model's label was for the last person
                changed.append(k)
    if changed:
        jsonstate.merge(state_path, state, sorted(set(changed)))
        run(stream_dir, cfg, compose_only=True, kitsu=kitsu)
    return sorted(set(changed))


def run(stream_dir: Path, cfg: dict, ids: list[str] | None = None, force: bool = False,  # noqa: C901
        compose_only: bool = False, kitsu: bool = True,
        feedback: dict[str, str] | None = None, missing: bool = False) -> list[dict]:
    """feedback: {asset id: what was wrong with its thumbnail}, for a re-pick (retakes).
    missing: only videos with no thumbnail choice yet; the others' covers (and their
    Shorts' covers) are not re-rendered."""
    cfg = for_stream(cfg, stream_dir)
    from ..bleep import Bleeper

    tc = {**cfg["thumbnail"]}
    tc["_photos"] = photos(tc) if tc["style"] == "enquirer" else {}
    tc["_photo_labels"] = photo_labels(tc)
    assets = [x for x in load_assets(stream_dir) if not ids or x["id"] in ids]
    if not assets:
        raise ClipperError("no cut assets" + (f" matching {ids}" if ids else "; run `clipper cut` first"))
    state_path = stream_dir / "thumbnails.json"
    state = jsonstate.read(state_path, {})
    if missing:
        kept = len(assets)
        assets = [x for x in assets if asset_key(x) not in state]
        if not assets:
            print(f"all {kept} videos have a thumbnail")
            return []
        print(f"{len(assets)} videos without a thumbnail ({kept - len(assets)} kept)")
    review_path = stream_dir / "review.json"
    review = jsonstate.read(review_path, {})
    # Banned words are always enforced on thumbnail text, whatever [bleep] mode is.
    bleeper = Bleeper({**cfg["bleep"], "mode": "bleep"})

    style = tc["style"]
    todo = [x for x in assets if force or asset_key(x) not in state
            or cut_changed(state[asset_key(x)], x["video"])
            or state[asset_key(x)].get("style", "classic") != style]
    if compose_only:
        missing = [asset_key(x) for x in todo if asset_key(x) not in state]
        if missing:
            raise ClipperError(f"--compose-only: no choice yet for {missing}; run without it first")
        todo = []
    claude = None
    if todo:
        claude = Claude(cfg)
        preflight(claude, tc["model"])
        prompts = Prompts(cfg)
        prompts.vars.update(show_name=cfg["meta"]["show_name"] or "the show",
                            max_words=tc["max_words"], max_words_min=min(2, tc["max_words"]),
                            min_crop=tc["min_crop"])
        system = prompts.get("thumbnail_enquirer" if style == "enquirer" else "thumbnail")
        retry = Prompts(cfg)  # the retry and reviewer-feedback wording
        sig = load_signals(stream_dir) if (stream_dir / "signals.parquet").exists() else None
        faces = jsonstate.read(stream_dir / "faces.json", None) if style == "enquirer" else None

        def one(x: dict) -> tuple[str, dict]:
            t = time.time()
            note = (feedback or {}).get(x["id"], "")
            # A reviewer turned the last pick down: look through more of the video.
            ftc = {**tc, "even_frames": tc["even_frames"] * 3, "heat_frames": tc["heat_frames"] * 2,
                   "clip_frames": tc["clip_frames"] * 2} if note else tc
            fr = frames(x, sig, ftc, stream_dir / "thumbs" / asset_key(x))
            if faces:
                fr += face_frames(x, faces, tc, stream_dir / "thumbs" / asset_key(x), len(fr))
            info = asset_info(stream_dir, x, review)
            choice = pick(x, fr, info, tc, claude, system, bleeper, retry, style, note)
            print(f"{x['id']:<22} {len(fr)} frames -> #{choice['frame']} at {choice['t']:.1f}s: "
                  f"{choice['text']} ({time.time() - t:.0f}s)", flush=True)
            return asset_key(x), {
                "kind": x["kind"], "id": x["id"], "video": x["video"].name,
                "video_mtime": x["video"].stat().st_mtime, "cut_key": cut_key(x["video"]),
                "title": info["title"],
                "frames": [{**f, "path": f["path"].name} for f in fr], **choice,
                "style": style, "topic": info["playlist"], "duration_s": round(duration(x), 1),
                "model": tc["model"],
                "prompt_version": prompts.version_of("thumbnail_enquirer" if style == "enquirer" else "thumbnail"),
                **({"feedback": note} if note else {})}

        def safe(x: dict) -> tuple[str, dict | None]:
            try:
                return one(x)
            except Exception as e:  # noqa: BLE001  # #75: one bad grab can't cost the others' picks
                print(f"{x['id']:<22} failed: {type(e).__name__}: {e}", flush=True)
                return asset_key(x), None

        failed = []
        with ThreadPoolExecutor(tc["concurrency"]) as pool:
            for key, entry in pool.map(safe, todo):
                if entry is None:
                    failed.append(key)
                    continue
                state[key] = entry  # each paid pick is kept as soon as it's made
                state = jsonstate.merge(state_path, state, [key])
        if failed:
            raise ClipperError(f"thumbnails failed for {', '.join(failed)}; the other picks are saved, "
                             "rerun clipper thumbs for these")

    tc_enq, date, own_band = enquirer_setup(stream_dir, cfg, tc)
    results = []
    for x in assets:
        e = printable(state[asset_key(x)], bleeper)
        frame = stream_dir / "thumbs" / asset_key(x) / e["frames"][e["frame"] - 1]["path"]
        if not frame.exists():  # edited "frame"/"t" by hand, or candidates cleaned up
            frame.parent.mkdir(parents=True, exist_ok=True)
            grab(x["video"], e["t"], frame)
        if e.get("style") == "enquirer":
            size = compose_enquirer(stream_dir, asset_key(x), e,
                                    cover_context(asset_key(x), state, stream_dir, tc, own_band),
                                    tc_enq, date, x["video"], x["video"].with_suffix(".jpg"))
        else:
            size = compose(frame, e, tc, x["video"].with_suffix(".jpg"))
        results.append({"key": asset_key(x), "jpg": x["video"].with_suffix(".jpg"), "bytes": size, **e})
    # Shorts get a vertical cover from their clip's choice (no model calls).
    results += short_covers(stream_dir, state, tc_enq, date, own_band, bleeper,
                            clip_ids={x["id"] for x in assets if x["kind"] == "clip"})
    print(f"{len(results)} thumbnails ({len(todo)} picked"
          + (f", cost ${claude.cost:.3f}" if claude else "") + ")")
    for r in results:
        print(f"  {r['jpg'].relative_to(stream_dir)}  {r['bytes'] / 1e3:.0f} KB  {r['text']}")

    if kitsu and tc["kitsu"] and (stream_dir / "review_state.json").exists():
        from .. import review as kitsu_review
        kitsu_review.attach_thumbnails(stream_dir, cfg)
    return results


def cut_key(video: Path) -> str:
    """What makes a cover stale: the cut's bounds from its sidecar (picks: in_s/out_s;
    segments: cut_start_s or start_s, and end_s), not the file's mtime (a re-encode of the
    same cut used to re-pay every cover and re-attach it in Kitsu). A sidecar with neither
    (compilations) falls back to the mtime."""
    try:
        s = jsonstate.load(video.with_suffix(".json"))
        if "in_s" in s and "out_s" in s:
            return f"{s['in_s']}-{s['out_s']}"
        if "end_s" in s and ("cut_start_s" in s or "start_s" in s):
            return f"{s.get('cut_start_s', s.get('start_s'))}-{s['end_s']}"
    except (OSError, ValueError):
        pass
    return f"mtime:{video.stat().st_mtime}"


def cut_changed(entry: dict, video: Path) -> bool:
    """Against a cover made before cut_key existed, the mtime still decides."""
    if "cut_key" in entry:
        return entry["cut_key"] != cut_key(video)
    return entry.get("video_mtime") != video.stat().st_mtime
