"""YouTube upload: schedule.json entries -> private videos with publishAt.

  clipper upload --sign-in   one-time OAuth in your browser; token saved beside the
                             client secret and refreshed automatically after that
  clipper upload             upload every scheduled entry due within upload_ahead_hours

Per video: title, description, hashtags, tags and playlist from Kitsu's columns (read
fresh from Kitsu just before uploading; your edits, or what publish put there), else the
metadata draft; the title gets the show name ([meta] title_format). Captions from the
.srt beside the video, the thumbnail from the .jpg beside it (`clipper thumbs`); the
playlist is created on first use.
Custom thumbnails need a phone-verified channel (youtube.com/verify); until then the
video uploads without one, and `clipper upload --thumbnails` sets them afterwards. Each upload is private with publishAt = its slot, so
YouTube releases it on time; the entry is then marked uploaded with its video id, and
the link goes into the Kitsu "YouTube URL" column. A pre-show placed "asap" by
`clipper schedule` is always due and goes up public at once (no publishAt).
With [youtube] manual_publish = true every upload stays private (no publishAt) and you
make it public in YouTube Studio; the slot is then only the suggested time.

Source links ([youtube] source_links): a clip's description links its segment at the clip's
offset, a segment's its compilation, once that longer video is public or will be by the time
this one is. The line is added here, not in Kitsu, since the longer video's link doesn't exist
at review time; each run then adds, fixes or drops it on videos already up (`--links` alone).

Uploaded by hand in Studio instead (e.g. past the channel's daily upload limit, which the
API hits first): `clipper upload --next 5` lists what's next with its file; upload the
file in Studio (private, no details), then `clipper upload --attach <item> <link>` sets
the title, description and tags, adds thumbnail, captions, playlist and Kitsu link, and
records it as uploaded so it never goes up twice.

A Short's "related video" (the link under it to the longer video) can only be set in Studio:
`clipper upload --related` lists uploaded Shorts whose segment is out, with the Studio link
and the segment's id to search for; `--related ITEM ...` records them as done (the daily
digest lists the same).

Publishing through the API (public, or private with publishAt) worked on this Cloud project
before any API audit (2026-10-20). [youtube] max_per_24h keeps every run together under the
channel's daily upload cap; with auto_upload_minutes the bot replans and uploads on a timer.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonstate
from .bleep import Bleeper
from .config import for_stream
from .errors import ClipperError
from .review import draft_fields, full_title, hashtag_line

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]  # captions, playlists
RETRY_STATUS = {500, 502, 503, 504}


# ---------- auth ----------

def credentials(paths: dict, sign_in: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path: Path = paths["youtube_token"]
    creds = None
    if token_path.exists() and not sign_in:
        # Keep scopes granted beyond ours (analytics --sign-in adds yt-analytics): loading with
        # SCOPES alone would refresh without them and rewrite the file without them.
        saved = jsonstate.load(token_path).get("scopes") or []
        creds = Credentials.from_authorized_user_file(
            str(token_path), [*SCOPES, *(s for s in saved if s not in SCOPES)])
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:  # noqa: BLE001  # revoked, or a 7-day Testing-mode token that expired
            raise ClipperError(f"YouTube token refresh failed ({e}); run: clipper upload --sign-in")
    if not creds or not creds.valid:
        if not sign_in:
            raise ClipperError("not signed in to YouTube; run: clipper upload --sign-in")
        flow = InstalledAppFlow.from_client_secrets_file(str(paths["youtube_client_secret"]), SCOPES)
        # select_account: always show the account chooser, which is also where a Brand
        # Account channel is picked; plain "consent" silently reuses the browser's account.
        creds = flow.run_local_server(port=0, prompt="select_account consent",
                                      authorization_prompt_message="Opening your browser to sign in to YouTube: {url}")
    jsonstate.write_text(token_path, creds.to_json())  # rewritten on every refresh
    return creds


def service(paths: dict, sign_in: bool = False):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=credentials(paths, sign_in), cache_discovery=False)


# ---------- metadata ----------

def clean(text: str, limit: int) -> str:
    """YouTube rejects < and > in titles and descriptions."""
    text = (text or "").replace("<", "‹").replace(">", "›").strip()
    return text if len(text.encode("utf-8")) <= limit else text.encode("utf-8")[:limit].decode("utf-8", "ignore").rstrip()


def metadata(entry: dict, cfg: dict) -> dict:
    video = Path(entry["file"])
    sdir = cfg["paths"]["streams"] / entry["stream"]
    cfg = for_stream(cfg, sdir)  # title_format and show name are per show
    draft_path = sdir / "metadata" / video.parent.name / f"{video.stem}.json"
    draft = jsonstate.read(draft_path, {})
    fields = {}
    review_path = sdir / "review.json"
    key = entry["key"].split("/", 1)[1]
    if review_path.exists():
        fields = jsonstate.load(review_path).get(key, {}).get("fields", {})
    # Kitsu's columns (your edits, or what publish put there) first, then the draft.
    proposed = draft_fields(draft) if draft else {}
    value = lambda name: fields.get(name) or proposed.get(name) or ""
    title = full_title(cfg, value("YouTube title") or entry["title"])
    description = value("YouTube description")
    hashtags = hashtag_line(value("Hashtags"))
    if hashtags and not description.rstrip().endswith(hashtags):
        description = f"{description.rstrip()}\n\n{hashtags}".strip()
    tags, total = [], 0
    raw_tags = fields["Tags"].split(",") if fields.get("Tags") else draft.get("tags", [])
    for t in raw_tags:  # YouTube caps tags at ~500 characters in total
        t = clean(t, 100)
        if t and total + len(t) + 1 <= 480:
            tags.append(t)
            total += len(t) + 1
    # Last safety net: Kitsu or an old draft may predate a [bleep] change, and banned
    # words are always masked in text, as on thumbnails.
    mask = Bleeper({**cfg["bleep"], "mode": "bleep"}).mask
    masked = [n for n, v in (("title", title), ("description", description), ("tags", ",".join(tags)))
              if mask(v) != v]
    title, description, tags = mask(title), mask(description), [mask(t) for t in tags]
    srt, jpg = video.with_suffix(".srt"), video.with_suffix(".jpg")
    playlist = value("Playlist").strip()
    show_list = (cfg["youtube"]["show_playlist"].format(show=cfg["meta"]["show_name"])
                 if cfg["youtube"]["show_playlist"] and cfg["meta"]["show_name"] else "")
    return {"title": clean(title, 100), "description": clean(description, 5000), "tags": tags,
            "playlist": playlist, "playlists": list(dict.fromkeys(p for p in (playlist, show_list) if p)),
            "srt": srt if srt.exists() else None,
            "thumb": jpg if jpg.exists() and cfg["youtube"]["thumbnails"]
            and (entry["kind"] != "short" or cfg["youtube"]["short_thumbnails"]) else None,
            "review_key": key, "stream_dir": sdir, "masked": masked}


def refresh_review(cfg: dict, entries: list[dict]) -> None:
    """Re-read these items from Kitsu into review.json, so the upload uses the columns as
    they are now. If Kitsu can't be reached, the last sync's values are used."""
    from . import review

    by_stream: dict[str, list[str]] = {}
    for e in entries:
        by_stream.setdefault(e["stream"], []).append(e["key"].split("/", 1)[1])
    for stream, keys in by_stream.items():
        sdir = cfg["paths"]["streams"] / stream
        if not (sdir / "review_state.json").exists():
            continue
        try:
            review.sync(sdir, cfg, keys=keys, quiet=True)
        except Exception as e:  # noqa: BLE001
            print(f"  Kitsu not read for {stream} ({e}); using review.json as last synced")


# ---------- links to the longer source ----------

def stream_sources(sdir: Path, cache: dict) -> tuple[list[dict], dict[str, list[str]]]:
    """A stream's segment sidecars in stream order, and each compilation's segment ids."""
    if sdir not in cache:
        load = lambda p: jsonstate.load(p)
        segs = sorted((load(p) for p in (sdir / "segments").glob("*.json")),
                      key=lambda s: s["start_s"])
        comps = {f"comp-{p.stem}": load(p).get("segments", [])
                 for p in sorted((sdir / "compilations").glob("*.json"))}
        cache[sdir] = segs, comps
    return cache[sdir]


def source_parents(cfg: dict, e: dict, entries: list[dict], cache: dict) -> list[tuple[str, dict, int]]:
    """(template key, entry, start second) for each longer video e comes from: a clip's
    segment at the clip's offset, a segment's compilations, with part_links its neighbours."""
    sdir = cfg["paths"]["streams"] / e["stream"]
    yc = for_stream(cfg, sdir)["youtube"]
    if not yc["source_links"] or e["kind"] not in ("clip", "segment"):
        return []
    mine = {(x["kind"], x["id"]): x for x in entries if x["stream"] == e["stream"]}
    segs, comps = stream_sources(sdir, cache)
    out = []
    if e["kind"] == "clip":
        side = sdir / "clips" / f"{Path(e['file']).stem}.json"
        if side.exists():
            c = jsonstate.load(side)
            for s in segs:
                a = s.get("cut_start_s", s["start_s"])
                if a <= c["in_s"] < s["end_s"] and (p := mine.get(("segment", s["id"]))):
                    out.append(("clip_source", p, int(c["in_s"] - a)))
                    break
        return out
    # A compilation that's a Kitsu item has id comp-<stem>; one scheduled from its draft
    # alone has its topic as id, so match those on the key too (#77 M3).
    by_key = {x["key"].split("/", 1)[1]: x for x in entries
              if x["stream"] == e["stream"] and x["kind"] == "compilation"}
    out += [("segment_source", p, 0) for cid, ids in comps.items()
            if e["id"] in ids and (p := mine.get(("compilation", cid)) or by_key.get(cid))]
    if yc["part_links"]:  # the nearest scheduled segment either side (intros etc. have none)
        listed = [s["id"] for s in segs if ("segment", s["id"]) in mine]
        i = listed.index(e["id"]) if e["id"] in listed else -1
        if i > 0:
            out.append(("previous_part", mine[("segment", listed[i - 1])], 0))
        if 0 <= i < len(listed) - 1:
            out.append(("next_part", mine[("segment", listed[i + 1])], 0))
    return out


def short_segment(cfg: dict, e: dict, entries: list[dict], cache: dict) -> dict | None:
    """The scheduled segment a Short's moment falls in (its sidecar's in_s), if any."""
    sdir = cfg["paths"]["streams"] / e["stream"]
    side = sdir / "shorts" / f"{Path(e['file']).stem}.json"
    if not side.exists():
        return None
    in_s = jsonstate.load(side)["in_s"]
    segs, _ = stream_sources(sdir, cache)
    s = next((s for s in segs if s.get("cut_start_s", s["start_s"]) <= in_s < s["end_s"]), None)
    return s and next((x for x in entries if x["stream"] == e["stream"]
                       and x["kind"] == "segment" and x["id"] == s["id"]), None)


def related_todo(cfg: dict, sched: dict, now: datetime | None = None) -> list[tuple[dict, dict]]:
    """(Short, segment) for each uploaded Short with no related video recorded whose segment
    is up and past its release time. Reads files only, no quota."""
    now, cache, out = now or datetime.now(UTC), {}, []
    for e in sched["entries"]:
        if e["kind"] != "short" or e.get("status") != "uploaded" or e.get("related_video"):
            continue
        p = short_segment(cfg, e, sched["entries"], cache)
        if p and p.get("status") == "uploaded" and p.get("video_id") and (
                not p.get("publish_at") or datetime.fromisoformat(p["publish_at"]) <= now):
            out.append((e, p))
    return out


def related(cfg: dict, refs: list[str]) -> None:
    """No refs: print the Shorts waiting for a related video. With refs (schedule keys or
    the Shorts' links): record their segment as set, after you set it in Studio."""
    sched_path: Path = cfg["paths"]["schedule"]
    sched = jsonstate.load(sched_path)
    todo = related_todo(cfg, sched)
    if not refs:
        print(f"{len(todo)} Shorts need their related video set in Studio")
        for e, p in todo:
            print(f"  {e['key']}  https://studio.youtube.com/video/{e['video_id']}/edit\n"
                  f"    related video: search {p['video_id']}  ({p['title'][:70]})")
        return
    cache, today = {}, datetime.now(UTC).date().isoformat()
    for ref in refs:
        e = next((x for x in sched["entries"] if x["key"] == ref), None)
        if not e:
            vid = video_id(ref)
            e = next((x for x in sched["entries"] if x.get("video_id") == vid), None)
        if not e or e["kind"] != "short":
            raise ClipperError(f"{ref}: no uploaded Short with that key or link")
        p = short_segment(cfg, e, sched["entries"], cache)
        if not p or not p.get("video_id"):
            raise ClipperError(f"{e['key']}: its segment isn't on YouTube, nothing to record")
        mark(sched_path, e["key"], related_video=p["video_id"], related_set_at=today)
        print(f"{e['key']} -> {p['video_id']} ({p['title'][:70]})")


def privacy(yt, ids) -> dict[str, dict]:
    """Video id -> its status (privacyStatus, publishAt); deleted videos are left out.
    1 quota unit per 50 videos."""
    ids, out = list(dict.fromkeys(i for i in ids if i)), {}
    for i in range(0, len(ids), 50):
        res = execute_with_retry(yt.videos().list(part="status", id=",".join(ids[i:i + 50])))
        out |= {v["id"]: v["status"] for v in res.get("items", [])}
    return out


def source_links(cfg: dict, e: dict, parents: list[tuple[str, dict, int]], status: dict) -> str:
    """The description line(s) for e: each longer video that is public by the time e is."""
    if not parents:
        return ""
    yc = for_stream(cfg, cfg["paths"]["streams"] / e["stream"])["youtube"]
    now = datetime.now(UTC)
    release = max(now, datetime.fromisoformat(e["publish_at"])) if e.get("publish_at") else now
    lines = []
    for key, p, t in parents:
        st = status.get(p.get("video_id")) or {}
        at = st.get("publishAt")
        if st.get("privacyStatus") == "public" or (
                st.get("privacyStatus") == "private" and at
                and datetime.fromisoformat(at) <= release):
            url = f"https://youtu.be/{p['video_id']}" + (f"?t={t}" if t else "")
            lines.append(yc[key].format(url=url, title=p["title"]))
    return "\n".join(dict.fromkeys(lines))


def with_links(description: str, old: str, new: str) -> str:
    """The description with the links clipper added last time (`old`) swapped for `new`,
    which goes above a closing hashtag line."""
    import re

    text = description.strip()
    if old and old in text:
        text = re.sub(r"\n{3,}", "\n\n", text.replace(old, "")).strip()
    if not new:
        return text
    paras = text.split("\n\n")
    if len(paras) > 1 and paras[-1].lstrip().startswith("#"):
        return "\n\n".join([*paras[:-1], new, paras[-1]])
    return f"{text}\n\n{new}".strip()


def mark(sched_path: Path, key: str, **fields) -> None:
    """Set fields on one entry of schedule.json, re-read under its lock (the bot may have
    replanned since this run read it)."""
    with jsonstate.update(sched_path) as sched:
        for e in sched["entries"]:
            if e["key"] == key:
                e.update(fields)


def note_links(sched_path: Path, key: str, links: str) -> None:
    mark(sched_path, key, source_links=links)


def backfill_links(cfg: dict, sched: dict, sched_path: Path, dry_run: bool,
                   limit: int | None, yt=None) -> None:
    """Add, fix or drop the source links on uploaded videos whose longer video went public,
    was re-cut or was deleted since. Checking costs 1 quota unit per 50 videos; each
    description update 51 (read + write), at most [youtube] link_updates_per_run a run."""
    cache: dict = {}
    up = [(e, ps) for e in sched["entries"] if e.get("status") == "uploaded" and e.get("video_id")
          for ps in [source_parents(cfg, e, sched["entries"], cache)] if ps or e.get("source_links")]
    if not up:
        return
    yt = yt or service(cfg["paths"])
    status = privacy(yt, [p.get("video_id") for _, ps in up for _, p, _ in ps])
    todo = [(e, want) for e, ps in up for want in [source_links(cfg, e, ps, status)]
            if want != e.get("source_links", "")]
    cap = limit or cfg["youtube"]["link_updates_per_run"]
    print(f"{len(todo)} videos need their source links set" + (" (dry run)" if dry_run else "")
          + (f"; {cap} this run" if len(todo) > cap else ""))
    for e, want in todo[:cap]:
        print(f"  {e['url']} {e['title'][:60]}\n    " + (want.replace("\n", "\n    ") or "(links removed)"))
        if dry_run:
            continue
        found = yt.videos().list(part="snippet", id=e["video_id"]).execute().get("items", [])
        if not found:
            print("    not on YouTube any more; skipped")
            continue
        sn = found[0]["snippet"]  # an update replaces the whole snippet: send back what's there
        body = {k: sn[k] for k in ("title", "categoryId", "tags", "defaultLanguage",
                                   "defaultAudioLanguage") if k in sn}
        body["description"] = clean(with_links(sn.get("description", ""),
                                               e.get("source_links", ""), want), 5000)
        execute_with_retry(yt.videos().update(part="snippet",
                                              body={"id": e["video_id"], "snippet": body}))
        note_links(sched_path, e["key"], want)


# ---------- upload ----------

def upload_video(yt, path: Path, body: dict, notify: bool) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=16 * 1024 * 1024,
                            resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media,
                             notifySubscribers=notify)
    response, errors, last = None, 0, -1
    while response is None:
        try:
            status, response = req.next_chunk()
            if status and int(status.progress() * 10) != last:
                last = int(status.progress() * 10)
                print(f"    {status.progress():.0%}", flush=True)
        except HttpError as e:
            if e.resp.status not in RETRY_STATUS or errors >= 5:
                raise
            errors += 1
            time.sleep(2 ** errors)
    return response["id"]


def execute_with_retry(request, tries: int = 6):
    """YouTube answers 409 SERVICE_UNAVAILABLE (and 5xx) for a while after creating
    something, e.g. adding to a just-made playlist; those succeed on retry."""
    from googleapiclient.errors import HttpError

    for attempt in range(tries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status not in RETRY_STATUS | {409} or attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def ensure_playlist(yt, name: str, cache: dict, privacy: str) -> str:
    if name in cache:
        return cache[name]
    page = None
    while True:
        res = yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=page).execute()
        for p in res.get("items", []):
            cache.setdefault(p["snippet"]["title"], p["id"])
        page = res.get("nextPageToken")
        if not page or name in cache:
            break
    if name not in cache:
        res = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": name}, "status": {"privacyStatus": privacy}}).execute()
        cache[name] = res["id"]
    return cache[name]


def add_to_playlist(yt, name: str, vid: str, cache: dict, cache_path: Path, privacy: str) -> None:
    from googleapiclient.errors import HttpError

    for attempt in range(2):
        pid = ensure_playlist(yt, name, cache, privacy)
        jsonstate.write(cache_path, cache)
        try:
            execute_with_retry(yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}}))
            return
        except HttpError as e:
            if e.resp.status != 404 or attempt:
                raise
            # Deleted in Studio since it was cached: look the name up again (or create it).
            print(f"  playlist {name!r} ({pid}) is gone; finding or making it again")
            cache.pop(name, None)


def playlist_videos(yt, pid: str) -> set[str]:
    """Videos in a playlist; empty if it no longer exists (add_to_playlist then remakes it)."""
    from googleapiclient.errors import HttpError

    out, page = set(), None
    while True:
        try:
            res = yt.playlistItems().list(part="contentDetails", playlistId=pid, maxResults=50,
                                          pageToken=page).execute()
        except HttpError as e:
            if e.resp.status == 404:
                return out
            raise
        out |= {i["contentDetails"]["videoId"] for i in res.get("items", [])}
        page = res.get("nextPageToken")
        if not page:
            return out


def backfill_playlists(cfg: dict, sched: dict, sched_path: Path, dry_run: bool,
                       limit: int | None) -> None:
    """Put uploaded videos in the playlists they belong in but aren't in yet (e.g. the show
    playlist, added 2026-10-22). Each add costs 50 quota units, so --max spreads it out."""
    paths, yc = cfg["paths"], cfg["youtube"]
    cache_path = paths["youtube_token"].with_name("youtube_playlists.json")
    cache = jsonstate.read(cache_path, {})
    up = [e for e in sched["entries"] if e.get("status") == "uploaded" and e.get("video_id")]
    yt = service(paths)
    members: dict[str, set[str]] = {}
    todo = []
    for e in up:
        for name in metadata(e, cfg)["playlists"]:
            if name in e.get("playlists", []):
                continue
            if name not in members:  # existing playlists only; a new one is empty
                members[name] = playlist_videos(yt, cache[name]) if name in cache else set()
            if e["video_id"] in members[name]:
                if not dry_run:  # already there; just remember it
                    note_playlist(sched_path, e["key"], name)
            else:
                todo.append((e, name))
    todo = todo[:limit] if limit else todo
    print(f"{len(todo)} playlist adds" + (" (dry run)" if dry_run else "")
          + f", {len(todo) * 50} quota units")
    for e, name in todo:
        print(f"  {name}: {e['url']} {e['title'][:60]}")
        if not dry_run:
            add_to_playlist(yt, name, e["video_id"], cache, cache_path, yc["playlist_privacy"])
            note_playlist(sched_path, e["key"], name)


def note_playlist(sched_path: Path, key: str, name: str) -> None:
    """Record that an uploaded entry is in a playlist, re-reading schedule.json first (the
    bot may have replanned since)."""
    with jsonstate.update(sched_path) as sched:
        for e in sched["entries"]:
            if e["key"] == key and name not in e.get("playlists", []):
                e.setdefault("playlists", []).append(name)


class ThumbnailsNotAllowed(Exception):
    pass


def set_thumbnail(yt, video_id: str, path: Path) -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    try:
        execute_with_retry(yt.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(str(path), mimetype="image/jpeg")))
    except HttpError as e:
        if e.resp.status == 403:
            raise ThumbnailsNotAllowed(
                "YouTube refused the custom thumbnail: the channel must be phone-verified "
                "(https://www.youtube.com/verify). After verifying, run: clipper upload "
                f"--thumbnails ({e.reason or e})") from None
        raise


def backfill_thumbnails(cfg: dict, sched: dict, sched_path: Path, dry_run: bool) -> None:
    """Set thumbnails on videos already uploaded without one (e.g. before verification)."""
    todo = [(e, m) for e in sched["entries"] if e.get("status") == "uploaded"
            and e.get("video_id") and not e.get("thumbnail_set")
            for m in [metadata(e, cfg)] if m["thumb"]]
    print(f"{len(todo)} uploaded videos without a thumbnail" + (" (dry run)" if dry_run else ""))
    if dry_run or not todo:
        for e, m in todo:
            print(f"  {e['url']} {m['thumb'].name}")
        return
    yt = service(cfg["paths"])
    for e, m in todo:
        try:
            set_thumbnail(yt, e["video_id"], m["thumb"])
        except ThumbnailsNotAllowed as ex:
            raise ClipperError(str(ex)) from None
        e["thumbnail_set"] = datetime.now(UTC).isoformat(timespec="seconds")
        mark(sched_path, e["key"], thumbnail_set=e["thumbnail_set"])
        print(f"  {e['url']} thumbnail set")


def finish(yt, cfg: dict, e: dict, m: dict, vid: str, playlists: dict, cache_path: Path,
           sched_path: Path) -> None:
    """Everything after the video itself: thumbnail, captions, playlist, Kitsu link. None
    of it counts against the channel's daily upload limit."""
    yc = cfg["youtube"]
    extras = []
    if m["thumb"]:  # its own try: a refusal here shouldn't skip captions and playlist
        try:
            set_thumbnail(yt, vid, m["thumb"])
            e["thumbnail_set"] = datetime.now(UTC).isoformat(timespec="seconds")
            mark(sched_path, e["key"], thumbnail_set=e["thumbnail_set"])
            extras.append("thumbnail")
        except ThumbnailsNotAllowed as ex:
            print(f"  warning: {ex}")
        except Exception as ex:  # noqa: BLE001  # the video is up, the thumbnail is optional
            print(f"  warning: thumbnail: {type(ex).__name__}: {ex}")
    try:
        if yc["captions"] and m["srt"]:
            from googleapiclient.http import MediaFileUpload
            yt.captions().insert(part="snippet", body={"snippet": {
                "videoId": vid, "language": yc["language"], "name": "", "isDraft": False}},
                media_body=MediaFileUpload(str(m["srt"]), mimetype="application/octet-stream")).execute()
            extras.append("captions")
        for name in m["playlists"] if yc["add_to_playlist"] else []:
            add_to_playlist(yt, name, vid, playlists, cache_path, yc["playlist_privacy"])
            e.setdefault("playlists", []).append(name)
            note_playlist(sched_path, e["key"], name)
            extras.append(f"playlist {name}")
        kitsu_url(cfg, m, f"https://youtu.be/{vid}")
        extras.append("Kitsu link")
    except Exception as ex:  # noqa: BLE001  # the video itself is up; report and move on
        print(f"  warning: after upload: {type(ex).__name__}: {ex}")
    if extras:
        print(f"  + {', '.join(extras)}")


def kitsu_url(cfg: dict, meta: dict, url: str) -> None:
    from . import review

    state = jsonstate.load(meta["stream_dir"] / "review_state.json")
    st = state.get(meta["review_key"])
    if not st:
        return  # a compilation not published as its own Kitsu item
    cfg = for_stream(cfg, meta["stream_dir"])  # the show's own Kitsu production
    gazu = review.connect(cfg["review"])
    etype = review.ENTITY[st["kind"]]
    field = review.column_fields(gazu, gazu.project.get_project_by_name(review.project_name(cfg))).get(
        (etype, "YouTube URL"))
    if field:
        update = gazu.shot.update_sequence_data if etype == "Sequence" else gazu.shot.update_shot_data
        update(st["entity_id"], {field: url})


def run(cfg: dict, sign_in: bool = False, dry_run: bool = False, limit: int | None = None,
        thumbnails_only: bool = False, playlists_only: bool = False,
        links_only: bool = False) -> None:
    paths = cfg["paths"]
    if sign_in:
        service(paths, sign_in=True)
        print(f"signed in; token saved to {paths['youtube_token']}")
        return
    sched_path: Path = paths["schedule"]
    if not sched_path.exists():
        raise ClipperError("no schedule.json; run: clipper schedule")
    sched = jsonstate.load(sched_path)
    if thumbnails_only:
        backfill_thumbnails(cfg, sched, sched_path, dry_run)
        return
    if playlists_only:
        backfill_playlists(cfg, sched, sched_path, dry_run, limit)
        return
    if links_only:
        backfill_links(cfg, sched, sched_path, dry_run, limit)
        return
    yt, failed = upload_due(cfg, sched, sched_path, dry_run, limit)
    try:  # links on videos already up whose longer video went public since
        backfill_links(cfg, jsonstate.read(sched_path), sched_path, dry_run, None, yt)
    except Exception as ex:  # noqa: BLE001  # the uploads are done either way
        print(f"  warning: source links: {type(ex).__name__}: {ex}")
    if failed:  # a non-zero exit is what makes the bot DM it
        raise ClipperError(f"{len(failed)} upload(s) failed: {'; '.join(failed)}"[:900])


def upload_due(cfg: dict, sched: dict, sched_path: Path, dry_run: bool, limit: int | None):
    """Upload what's due; returns the YouTube client if it made one, and "key: error" for
    each upload that failed."""
    yc, paths = cfg["youtube"], cfg["paths"]
    tz = ZoneInfo(sched["timezone"])
    now = datetime.now(UTC)
    horizon = now + timedelta(hours=yc["upload_ahead_hours"])
    due, missed = [], []
    for e in sched["entries"]:
        if e.get("status") != "scheduled":
            continue
        when = datetime.fromisoformat(e["publish_at"])
        if e.get("source") == "asap":  # a pre-show: published as soon as it's uploaded
            due.append(e)
        elif when <= now + timedelta(minutes=yc["min_lead_minutes"]):
            missed.append(e)
        elif when <= horizon:
            due.append(e)
    # Pre-shows first, then by slot; at most max_per_run, and never past max_per_24h
    # counting everything uploaded in the last 24 h (YouTube's daily cap is per channel).
    due.sort(key=lambda e: (e.get("source") != "asap", e["publish_at"]))
    recent = sum(1 for e in sched["entries"] if e.get("uploaded_at")
                 and now - datetime.fromisoformat(e["uploaded_at"]) < timedelta(hours=24))
    room = max(0, yc["max_per_24h"] - recent)
    if room < len(due[:limit or yc["max_per_run"]]):
        print(f"  {recent} uploaded in the last 24 h ([youtube] max_per_24h = {yc['max_per_24h']}): "
              f"{room} more now")
    due = due[:min(limit or yc["max_per_run"], room)]
    if not dry_run:  # a sync writes review.json (and can post Kitsu comments)
        refresh_review(cfg, due)
    for e in missed:
        print(f"  missed slot {e['publish_at']}: {e['title'][:60]} (rerun clipper schedule)")
    print(f"{len(due)} due within {yc['upload_ahead_hours']} h" + (" (dry run)" if dry_run else "")
          + ("; uploads stay private ([youtube] manual_publish)" if yc["manual_publish"] else ""))
    if not due:
        return None, []
    sources: dict = {}
    if dry_run:
        for e in due:
            m = metadata(e, cfg)
            parents = source_parents(cfg, e, sched["entries"], sources)
            print(f"  {datetime.fromisoformat(e['publish_at']).astimezone(tz):%a %m-%d %H:%M} "
                  f"{e['kind']:<11} {m['title']}\n      tags {len(m['tags'])}, "
                  f"captions {'yes' if m['srt'] else 'no'}, "
                  f"thumbnail {m['thumb'].name if m['thumb'] else 'no'}, "
                  f"playlist {m['playlist'] or '-'}, "
                  f"links to {', '.join(p['id'] for _, p, _ in parents) or '-'} (if public by then)")
            if m["masked"]:
                print(f"      masked banned words in {', '.join(m['masked'])}: fix the Kitsu columns too")
        return None, []

    yt = service(paths)
    cache_path = paths["youtube_token"].with_name("youtube_playlists.json")
    playlists = jsonstate.read(cache_path, {})
    failed: list[str] = []
    for e in due:
        # #77 M5: one bad entry (a missing file, a refused upload) no longer blocks every
        # later one each hour; it's noted on the entry and the run moves on. A quota or daily
        # limit refusal stops the run, since everything after would fail the same way.
        try:
            upload_one(cfg, yt, e, sched, sched_path, sources, playlists, cache_path, tz)
        except Exception as ex:  # noqa: BLE001  # ClipperError: a missing file etc.
            reason = getattr(ex, "reason", "") or ""
            text = f"{type(ex).__name__}: {ex}"[:300]
            print(f"  FAILED {e['key']}: {text}", flush=True)
            mark(sched_path, e["key"], last_error=text,
                 last_error_at=datetime.now(UTC).isoformat(timespec="seconds"))
            failed.append(f"{e['key']}: {text[:150]}")
            if any(s in f"{reason} {ex}" for s in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded")):
                print("  YouTube's quota or upload limit: stopping this run", flush=True)
                break
    if failed:
        print(f"{len(failed)} upload(s) failed; the error is on their schedule.json entries")
    return yt, failed


def upload_one(cfg: dict, yt, e: dict, sched: dict, sched_path: Path, sources: dict,
               playlists: dict, cache_path: Path, tz) -> None:
    # #77 M6: a show's [shows.<key>.youtube] (manual_publish, notify_subscribers, captions,
    # made_for_kids, playlist_privacy...) applies to its uploads.
    cfg = for_stream(cfg, cfg["paths"]["streams"] / e["stream"])
    yc = cfg["youtube"]
    if not Path(e["file"]).exists():
        raise FileNotFoundError(f"no video file {e['file']}")
    m = metadata(e, cfg)
    parents = source_parents(cfg, e, sched["entries"], sources)
    links = source_links(cfg, e, parents, privacy(yt, [p.get("video_id") for _, p, _ in parents])
                         if parents else {})
    m["description"] = clean(with_links(m["description"], "", links), 5000)
    publish_utc = datetime.fromisoformat(e["publish_at"]).astimezone(UTC)
    at_once = publish_utc <= datetime.now(UTC) + timedelta(minutes=yc["min_lead_minutes"])
    slot = f"{publish_utc.astimezone(tz):%a %m-%d %H:%M}"
    if yc["manual_publish"]:  # stays private; you make it public in YouTube Studio
        status, when = {"privacyStatus": "private"}, f"private, publish in Studio {'now' if at_once else slot}"
    elif at_once:
        status, when = {"privacyStatus": "public"}, "public now"
    else:
        status, when = {"privacyStatus": "private",
                        "publishAt": publish_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}, slot
    body = {
        "snippet": {"title": m["title"], "description": m["description"], "tags": m["tags"],
                    "categoryId": yc["category_id"], "defaultLanguage": yc["language"],
                    "defaultAudioLanguage": yc["language"]},
        "status": {**status, "selfDeclaredMadeForKids": yc["made_for_kids"]},
    }
    print(f"uploading {e['key']} -> {when}: {m['title']}",
          flush=True)
    t = time.time()
    vid = upload_video(yt, Path(e["file"]), body, yc["notify_subscribers"])
    url = f"https://youtu.be/{vid}"
    # Record the upload before the optional extras, so a failure there can't
    # make the next run upload the same video twice.
    done = {"status": "uploaded", "video_id": vid, "url": url,
            "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source_links": links}
    e.update(done)
    # Re-read under the lock: a replan or `clipper rumble` may have written meanwhile.
    with jsonstate.update(sched_path) as fresh:
        mine = next((x for x in fresh["entries"] if x["key"] == e["key"]), None)
        if mine:
            mine.update(done)
        else:  # replanned away meanwhile; the upload must still be on record
            fresh["entries"].append(e)
        for x in fresh["entries"]:  # a retry that worked clears the last failure
            if x["key"] == e["key"]:
                x.pop("last_error", None)
                x.pop("last_error_at", None)
    print(f"  {url} ({time.time() - t:.0f}s)" + (f", links {links!r}" if links else ""), flush=True)
    finish(yt, cfg, e, m, vid, playlists, cache_path, sched_path)


# ---------- videos uploaded by hand ----------

def video_id(url: str) -> str:
    """The id from a youtu.be / watch?v= / shorts / Studio link, or a bare id."""
    import re

    m = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|/video/|/live/)([\w-]{11})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url.strip()):
        return url.strip()
    raise ClipperError(f"no YouTube video id in {url!r}")


def queue(cfg: dict) -> list[dict]:
    """Approved items not on YouTube yet, in the order they would go out: scheduled ones by
    slot, then the unslotted ones as `clipper schedule` would place them next."""
    sched = jsonstate.load(cfg["paths"]["schedule"])
    by_key = {e["key"]: e for e in sched["entries"]}
    out = sorted((e for e in sched["entries"] if e.get("status") == "scheduled"),
                 key=lambda e: e["publish_at"])
    from .schedule import candidates

    sc = cfg["schedule"]
    items = {it["key"]: it for it in candidates(cfg, cfg["paths"]["streams"], sc,
                                                 ZoneInfo(sc["timezone"]))[0]}
    for u in sched.get("unscheduled", []):
        if u["key"] in items and u["key"] not in by_key and u["reason"].startswith("no free"):
            out.append({**items[u["key"]], "publish_at": "", "reason": u["reason"]})
    return out


def next_up(cfg: dict, n: int) -> None:
    """Print the next n videos to upload by hand in YouTube Studio, with their files."""
    for e in queue(cfg)[:n]:
        when = e["publish_at"][:16].replace("T", " ") or "unslotted"
        print(f"{e['key']}  ({e['kind']}, {when})\n  {e['title']}\n  {e['file']}")


def attach(cfg: dict, ref: str, url: str, dry_run: bool = False) -> None:
    """A video you uploaded yourself in YouTube Studio becomes item `ref`: title,
    description and tags set, thumbnail, captions, playlist and Kitsu link added, and the
    item recorded as uploaded so `clipper upload` never sends it again. The video's
    privacy is left as you set it in Studio."""
    from . import actions

    yc, paths = cfg["youtube"], cfg["paths"]
    vid = video_id(url)
    item = actions.resolve(cfg, ref)
    key = item.ref
    sched_path: Path = paths["schedule"]
    sched = jsonstate.load(sched_path)
    prev = next((e for e in sched["entries"] if e["key"] == key), None)
    if prev and prev.get("status") == "uploaded":
        raise ClipperError(f"{key} is already on YouTube: {prev.get('url')}")
    if other := next((e for e in sched["entries"] if e.get("video_id") == vid), None):
        raise ClipperError(f"{vid} is already recorded as {other['key']}")
    e = prev or next((x for x in queue(cfg) if x["key"] == key), None)
    if not e:
        raise ClipperError(f"{key} isn't approved (or is on hold): approve it in Kitsu first")
    e = {k: v for k, v in e.items() if k in ("key", "stream", "kind", "id", "title", "file",
                                             "publish_at", "source", "platforms")}
    refresh_review(cfg, [e])
    m = metadata(e, cfg)
    yt = service(paths)
    found = yt.videos().list(part="snippet,status", id=vid).execute().get("items", [])
    if not found:
        raise ClipperError(f"YouTube has no video {vid} on this channel (still uploading?)")
    v = found[0]
    stem = Path(e["file"]).stem
    print(f"{key} <- https://youtu.be/{vid} ({v['status']['privacyStatus']}, "
          f"Studio title {v['snippet']['title']!r})")
    import re

    norm = lambda s: re.sub(r"[\W_]+", " ", s).strip().lower()  # Studio turns _ and - into spaces
    if norm(v["snippet"]["title"]) not in (norm(stem), norm(m["title"])):
        print(f"  note: the file for {key} is {stem}.mp4; check this is the same video")
    print(f"  title {m['title']}\n  tags {len(m['tags'])}, captions {'yes' if m['srt'] else 'no'}, "
          f"thumbnail {'yes' if m['thumb'] else 'no'}, playlist {m['playlist'] or '-'}")
    if dry_run:
        return
    parents = source_parents(cfg, e, sched["entries"], {})
    links = source_links(cfg, e, parents, privacy(yt, [p.get("video_id") for _, p, _ in parents]))
    m["description"] = clean(with_links(m["description"], "", links), 5000)
    execute_with_retry(yt.videos().update(part="snippet", body={"id": vid, "snippet": {
        "title": m["title"], "description": m["description"], "tags": m["tags"],
        "categoryId": yc["category_id"], "defaultLanguage": yc["language"],
        "defaultAudioLanguage": yc["language"]}}))
    now = datetime.now(UTC)
    # Recorded as out now, so any slot it had goes to the next item at the next replan.
    e.update({"publish_at": now.astimezone(ZoneInfo(sched["timezone"])).isoformat(timespec="seconds"),
              "source": "studio", "status": "uploaded", "video_id": vid,
              "url": f"https://youtu.be/{vid}", "uploaded_at": now.isoformat(timespec="seconds"),
              "source_links": links})
    with jsonstate.update(sched_path) as fresh:  # a replan or clipper rumble may have written
        fresh["entries"] = [x for x in fresh["entries"] if x["key"] != key] + [e]
        fresh["unscheduled"] = [x for x in fresh.get("unscheduled", []) if x["key"] != key]
    print("  details set; recorded as uploaded")
    cache_path = paths["youtube_token"].with_name("youtube_playlists.json")
    playlists = jsonstate.read(cache_path, {})
    finish(yt, cfg, e, m, vid, playlists, cache_path, sched_path)
