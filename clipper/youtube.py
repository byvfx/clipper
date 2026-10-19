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
the link goes into the Kitsu "YouTube URL" column.

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
        if when <= now + timedelta(minutes=yc["min_lead_minutes"]):
            missed.append(e)
        elif when <= horizon:
            due.append(e)
    due = due[:limit or yc["max_per_run"]]
    refresh_review(cfg, due)
    for e in missed:
        print(f"  missed slot {e['publish_at']}: {e['title'][:60]} (rerun clipper schedule)")
    print(f"{len(due)} due within {yc['upload_ahead_hours']} h" + (" (dry run)" if dry_run else ""))
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
        body = {
            "snippet": {"title": m["title"], "description": m["description"], "tags": m["tags"],
                        "categoryId": yc["category_id"], "defaultLanguage": yc["language"],
                        "defaultAudioLanguage": yc["language"]},
            "status": {"privacyStatus": "private",
                       "publishAt": publish_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "selfDeclaredMadeForKids": yc["made_for_kids"]},
        }
        print(f"uploading {e['key']} -> {publish_utc.astimezone(tz):%a %m-%d %H:%M}: {m['title']}",
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
            kitsu_url(cfg, m, url)
            extras.append("Kitsu link")
        except Exception as ex:  # noqa: BLE001  # the video itself is up; report and move on
            print(f"  warning: after upload: {type(ex).__name__}: {ex}")
        if extras:
            print(f"  + {', '.join(extras)}")
