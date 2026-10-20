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

Uploaded by hand in Studio instead (e.g. past the channel's daily upload limit, which the
API hits first): `clipper upload --next 5` lists what's next with its file; upload the
file in Studio (private, no details), then `clipper upload --attach <item> <link>` sets
the title, description and tags, adds thumbnail, captions, playlist and Kitsu link, and
records it as uploaded so it never goes up twice.

Until the Cloud project passes the YouTube API audit, YouTube keeps API uploads private
regardless of publishAt; publish those by hand in YouTube Studio meanwhile.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import for_stream
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
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:  # noqa: BLE001  # revoked, or a 7-day Testing-mode token that expired
            raise SystemExit(f"YouTube token refresh failed ({e}); run: clipper upload --sign-in")
    if not creds or not creds.valid:
        if not sign_in:
            raise SystemExit("not signed in to YouTube; run: clipper upload --sign-in")
        flow = InstalledAppFlow.from_client_secrets_file(str(paths["youtube_client_secret"]), SCOPES)
        # select_account: always show the account chooser, which is also where a Brand
        # Account channel is picked; plain "consent" silently reuses the browser's account.
        creds = flow.run_local_server(port=0, prompt="select_account consent",
                                      authorization_prompt_message="Opening your browser to sign in to YouTube: {url}")
    token_path.write_text(creds.to_json(), encoding="utf-8")
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
    draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else {}
    fields = {}
    review_path = sdir / "review.json"
    key = entry["key"].split("/", 1)[1]
    if review_path.exists():
        fields = json.loads(review_path.read_text(encoding="utf-8")).get(key, {}).get("fields", {})
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
    srt, jpg = video.with_suffix(".srt"), video.with_suffix(".jpg")
    return {"title": clean(title, 100), "description": clean(description, 5000), "tags": tags,
            "playlist": value("Playlist").strip(), "srt": srt if srt.exists() else None,
            "thumb": jpg if jpg.exists() and cfg["youtube"]["thumbnails"] else None,
            "review_key": key, "stream_dir": sdir}


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
        except (Exception, SystemExit) as e:  # noqa: BLE001
            print(f"  Kitsu not read for {stream} ({e}); using review.json as last synced")


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
            raise SystemExit(str(ex)) from None
        e["thumbnail_set"] = datetime.now(UTC).isoformat(timespec="seconds")
        sched_path.write_text(json.dumps(sched, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {e['url']} thumbnail set")


def finish(yt, cfg: dict, e: dict, m: dict, vid: str, playlists: dict, cache_path: Path,
           sched: dict, sched_path: Path) -> None:
    """Everything after the video itself: thumbnail, captions, playlist, Kitsu link. None
    of it counts against the channel's daily upload limit."""
    yc = cfg["youtube"]
    extras = []
    if m["thumb"]:  # its own try: a refusal here shouldn't skip captions and playlist
        try:
            set_thumbnail(yt, vid, m["thumb"])
            e["thumbnail_set"] = datetime.now(UTC).isoformat(timespec="seconds")
            sched_path.write_text(json.dumps(sched, indent=2, ensure_ascii=False), encoding="utf-8")
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
        if yc["add_to_playlist"] and m["playlist"]:
            pid = ensure_playlist(yt, m["playlist"], playlists, yc["playlist_privacy"])
            cache_path.write_text(json.dumps(playlists, indent=2))
            execute_with_retry(yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}}))
            extras.append(f"playlist {m['playlist']}")
        kitsu_url(cfg, m, f"https://youtu.be/{vid}")
        extras.append("Kitsu link")
    except Exception as ex:  # noqa: BLE001  # the video itself is up; report and move on
        print(f"  warning: after upload: {type(ex).__name__}: {ex}")
    if extras:
        print(f"  + {', '.join(extras)}")


def kitsu_url(cfg: dict, meta: dict, url: str) -> None:
    from . import review

    state = json.loads((meta["stream_dir"] / "review_state.json").read_text(encoding="utf-8"))
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
        thumbnails_only: bool = False) -> None:
    yc, paths = cfg["youtube"], cfg["paths"]
    if sign_in:
        service(paths, sign_in=True)
        print(f"signed in; token saved to {paths['youtube_token']}")
        return
    sched_path: Path = paths["schedule"]
    if not sched_path.exists():
        raise SystemExit("no schedule.json; run: clipper schedule")
    sched = json.loads(sched_path.read_text(encoding="utf-8"))
    if thumbnails_only:
        backfill_thumbnails(cfg, sched, sched_path, dry_run)
        return
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
    due = due[:limit or yc["max_per_run"]]
    refresh_review(cfg, due)
    for e in missed:
        print(f"  missed slot {e['publish_at']}: {e['title'][:60]} (rerun clipper schedule)")
    print(f"{len(due)} due within {yc['upload_ahead_hours']} h" + (" (dry run)" if dry_run else "")
          + ("; uploads stay private ([youtube] manual_publish)" if yc["manual_publish"] else ""))
    if not due:
        return
    if dry_run:
        for e in due:
            m = metadata(e, cfg)
            print(f"  {datetime.fromisoformat(e['publish_at']).astimezone(tz):%a %m-%d %H:%M} "
                  f"{e['kind']:<11} {m['title']}\n      tags {len(m['tags'])}, "
                  f"captions {'yes' if m['srt'] else 'no'}, "
                  f"thumbnail {m['thumb'].name if m['thumb'] else 'no'}, "
                  f"playlist {m['playlist'] or '-'}")
        return

    yt = service(paths)
    cache_path = paths["youtube_token"].with_name("youtube_playlists.json")
    playlists = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    for e in due:
        m = metadata(e, cfg)
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
                "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds")}
        e.update(done)
        # Re-read first: `clipper rumble` may have written other platforms' marks meanwhile.
        fresh = json.loads(sched_path.read_text(encoding="utf-8"))
        mine = next((x for x in fresh["entries"] if x["key"] == e["key"]), None)
        if mine:
            mine.update(done)
        else:  # replanned away meanwhile; the upload must still be on record
            fresh["entries"].append(e)
        sched_path.write_text(json.dumps(fresh, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {url} ({time.time() - t:.0f}s)", flush=True)
        finish(yt, cfg, e, m, vid, playlists, cache_path, sched, sched_path)


# ---------- videos uploaded by hand ----------

def video_id(url: str) -> str:
    """The id from a youtu.be / watch?v= / shorts / Studio link, or a bare id."""
    import re

    m = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|/video/|/live/)([\w-]{11})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url.strip()):
        return url.strip()
    raise SystemExit(f"no YouTube video id in {url!r}")


def queue(cfg: dict) -> list[dict]:
    """Approved items not on YouTube yet, in the order they would go out: scheduled ones by
    slot, then the unslotted ones as `clipper schedule` would place them next."""
    sched = json.loads(cfg["paths"]["schedule"].read_text(encoding="utf-8"))
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
    sched = json.loads(sched_path.read_text(encoding="utf-8"))
    prev = next((e for e in sched["entries"] if e["key"] == key), None)
    if prev and prev.get("status") == "uploaded":
        raise SystemExit(f"{key} is already on YouTube: {prev.get('url')}")
    if other := next((e for e in sched["entries"] if e.get("video_id") == vid), None):
        raise SystemExit(f"{vid} is already recorded as {other['key']}")
    e = prev or next((x for x in queue(cfg) if x["key"] == key), None)
    if not e:
        raise SystemExit(f"{key} isn't approved (or is on hold): approve it in Kitsu first")
    e = {k: v for k, v in e.items() if k in ("key", "stream", "kind", "id", "title", "file",
                                             "publish_at", "source", "platforms")}
    refresh_review(cfg, [e])
    m = metadata(e, cfg)
    yt = service(paths)
    found = yt.videos().list(part="snippet,status", id=vid).execute().get("items", [])
    if not found:
        raise SystemExit(f"YouTube has no video {vid} on this channel (still uploading?)")
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
    execute_with_retry(yt.videos().update(part="snippet", body={"id": vid, "snippet": {
        "title": m["title"], "description": m["description"], "tags": m["tags"],
        "categoryId": yc["category_id"], "defaultLanguage": yc["language"],
        "defaultAudioLanguage": yc["language"]}}))
    now = datetime.now(UTC)
    # Recorded as out now, so any slot it had goes to the next item at the next replan.
    e.update({"publish_at": now.astimezone(ZoneInfo(sched["timezone"])).isoformat(timespec="seconds"),
              "source": "studio", "status": "uploaded", "video_id": vid,
              "url": f"https://youtu.be/{vid}", "uploaded_at": now.isoformat(timespec="seconds")})
    fresh = json.loads(sched_path.read_text(encoding="utf-8"))  # clipper rumble may have written
    fresh["entries"] = [x for x in fresh["entries"] if x["key"] != key] + [e]
    fresh["unscheduled"] = [x for x in fresh.get("unscheduled", []) if x["key"] != key]
    sched_path.write_text(json.dumps(fresh, indent=2, ensure_ascii=False), encoding="utf-8")
    print("  details set; recorded as uploaded")
    cache_path = paths["youtube_token"].with_name("youtube_playlists.json")
    playlists = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    finish(yt, cfg, e, m, vid, playlists, cache_path, fresh, sched_path)
