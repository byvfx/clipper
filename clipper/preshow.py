"""Pre-shows: whole videos a show posts on its own YouTube channel.

Kino Casino posts a pre-recorded pre-show (opening bumper, fan songs, skits; 7-25 min)
on its YouTube channel on show days, usually 11:45-14:45 PT, hours before the live show.
The same video also opens the Kick stream, where `segments` marks it kind "intro" and
skips it.

  clipper preshow check [--run]  read each show's uploads feed ([shows.<key>.preshow]
                               youtube_channel), grab new uploads matching title_pattern,
                               and with --run put them through the pipeline. `clipper bot`
                               runs this every [preshow] poll_minutes.
  clipper preshow fetch <url>  grab one video by hand (any age); --show <key> picks the show
  clipper preshow fetch <url> --from 1:30:00 [--to ...] --about "..."
                               a one-off: ONE long segment out of a stretch of any YouTube
                               video (a podcast episode, say). The model finds the exact
                               start and end inside that window from the transcript.
  clipper preshow list         what the feeds show and which ones are grabbed

A grabbed video becomes streams/yt-<video id>/ (source.mp4 + meta.json, kind "preshow",
channel = the show's Kick slug so `config.for_stream` finds the show). `--run` (or
`clipper worker`) puts it through [preshow] stages: transcribe, then `segments` writes ONE segment covering
the whole video (no topic split, no clips, no Shorts), cut, meta, thumbs, review. Once
approved, `clipper schedule` places it as soon as possible ([preshow] publish = "asap")
and `clipper upload` publishes it right away.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import alerts, jsonstate, media, ytdl
from .config import for_show, for_stream
from .states import PRESHOW_PREFIX as PREFIX

log = logging.getLogger("clipper.preshow")

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "topic": {"type": "string"},
        "figures": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["title", "topic", "figures", "summary", "description"],
    "additionalProperties": False,
}

# A one-off window (`fetch --from/--to/--about`): the model also returns where the
# segment really starts and ends inside the window.
SCHEMA_WINDOW = {
    **SCHEMA,
    "properties": {**SCHEMA["properties"], "start_s": {"type": "number"}, "end_s": {"type": "number"}},
    "required": SCHEMA["required"] + ["start_s", "end_s"],
}
WINDOW_PAD_S = 1.0   # keep this much room around the model's bounds


def watched(cfg: dict) -> list[tuple[str, dict]]:
    """(show key, that show's config) for every show with a pre-show channel set."""
    out = []
    for key in cfg["shows"]:
        sc = for_show(cfg, key)
        if sc["preshow"]["youtube_channel"]:
            out.append((key, sc))
    return out


def feed(channel_id: str, ua: str) -> list[dict]:
    """The channel's latest ~15 uploads, newest first. YouTube's RSS feed for a channel
    sometimes 404s for hours while the channel is fine, so on any failure fall back to
    listing the channel's videos page with yt-dlp (no dates: see `published_at`)."""
    try:
        return rss_feed(channel_id, ua)
    except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
        log.info("pre-show feed %s: RSS failed (%s), listing the channel with yt-dlp", channel_id, e)
    r = ytdl.run("--flat-playlist", "--playlist-end", "8", "--print", "%(id)s	%(title)s",
                 f"https://www.youtube.com/channel/{channel_id}/videos")
    out = []
    for line in r.stdout.splitlines():
        vid, _, title = line.partition("	")
        if vid and title:
            out.append({"video_id": vid, "title": title, "published": "",
                        "url": f"https://www.youtube.com/watch?v={vid}"})
    if not out:
        raise RuntimeError(f"pre-show feed {channel_id}: RSS and yt-dlp both failed "
                           f"({ytdl.last_error(r)})")
    return out


def rss_feed(channel_id: str, ua: str) -> list[dict]:
    req = urllib.request.Request(FEED.format(channel_id), headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", "", NS)
        out.append({"video_id": vid, "title": e.findtext("a:title", "", NS),
                    "published": e.findtext("a:published", "", NS),
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def published_at(e: dict) -> datetime:
    """When an entry went up. yt-dlp's channel listing has no dates, so look the video up
    (only done for entries that already match the title pattern and aren't grabbed)."""
    if not e["published"]:
        r = ytdl.run("--skip-download", "--no-playlist", "--print",
                     "%(timestamp|)s", e["url"])
        ts = r.stdout.strip().splitlines()[-1:] or [""]
        try:
            e["published"] = datetime.fromtimestamp(float(ts[0]), UTC).isoformat()
        except ValueError:
            e["published"] = datetime.now(UTC).isoformat()  # unknown: treat as new
    return datetime.fromisoformat(e["published"])


def stream_of(cfg: dict, video_id: str) -> Path:
    return cfg["paths"]["streams"] / f"{PREFIX}{video_id}"


def grabbed(cfg: dict, video_id: str) -> bool:
    return (stream_of(cfg, video_id) / "meta.json").exists()


def fetch(cfg: dict, url: str, key: str, window: dict | None = None) -> Path:
    """Download one video into streams/yt-<id>/; meta.json last, so a failed or partial
    download is retried (and resumed) by the next check. `window` ({start_s, end_s or
    None, about}) marks a one-off: the segment is one stretch of the video, not all of it."""
    sc = for_show(cfg, key)
    pc = sc["preshow"]
    info, _ = ytdl.run_retrying(cfg, "-J", "--no-playlist", url, retry_on=ytdl.BOT)
    if info.returncode:
        raise RuntimeError(f"yt-dlp: {ytdl.last_error(info)}")
    v = json.loads(info.stdout)
    d = stream_of(cfg, v["id"])
    d.mkdir(parents=True, exist_ok=True)
    src = d / "source.mp4"
    if not src.exists():
        r, _ = ytdl.run_retrying(cfg, "--no-playlist", "-f", pc["format"], "--merge-output-format", "mp4",
                         "-o", str(d / "source.%(ext)s"), v["webpage_url"], retry_on=ytdl.BOT)
        if r.returncode or not src.exists():
            raise RuntimeError(f"yt-dlp download: {ytdl.last_error(r)}")
    published = datetime.fromtimestamp(v.get("timestamp") or v.get("release_timestamp")
                                       or datetime.now(UTC).timestamp(), UTC)
    meta = {"stream_id": d.name, "kind": "preshow", "source": "youtube", "show": key,
            "channel": sc["show"]["channel"], "video_id": v["id"], "url": v["webpage_url"],
            "title": v.get("title", ""), "description": v.get("description") or "",
            "youtube_channel": v.get("channel_id", ""), "youtube_channel_name": v.get("channel", ""),
            "started_at": published.isoformat(), "duration_s": float(v.get("duration") or 0),
            "grabbed_at": datetime.now(UTC).isoformat(timespec="seconds")}
    if window:
        meta["window"] = window
    jsonstate.write(d / "meta.json", meta)
    log.info("%s: grabbed %r (%.0f min)", d.name, meta["title"], meta["duration_s"] / 60)
    return d


def new_entries(cfg: dict) -> list[tuple[str, dict]]:
    """(show key, feed entry) for recent matching uploads not grabbed yet. Feeds are newest
    first, so an undated entry (yt-dlp's listing) below a grabbed pre-show is older than it:
    skipped without a lookup, which hits the bot check and would read as new (11-06: the
    8/12 pre-show, under 11/6 and 11/4 on the channel Kino moved to)."""
    out = []
    now = datetime.now(UTC)
    for key, sc in watched(cfg):
        pc = sc["preshow"]
        below_grabbed = False
        for e in feed(pc["youtube_channel"], sc["kick"]["user_agent"]):
            if not re.search(pc["title_pattern"], e["title"]):
                continue
            if grabbed(cfg, e["video_id"]):
                below_grabbed = True
            elif not (below_grabbed and not e["published"]) and \
                    now - published_at(e) <= timedelta(hours=pc["max_age_hours"]):
                out.append((key, e))
    return out


def check(cfg: dict) -> list[Path]:
    """Grab every new pre-show. Failures (a premiere not out yet, a network blip) are
    logged and tried again on the next check."""
    got = []
    for key, e in new_entries(cfg):
        try:
            got.append(fetch(cfg, e["url"], key))
        except Exception as ex:  # noqa: BLE001 - one bad video must not stop the rest
            log.warning("pre-show %s (%s): not grabbed yet: %s", e["video_id"], e["title"], ex)
            if any(s in str(ex).lower() for s in ytdl.BOT):
                bot_check_alert(cfg, e)
    return got


def bot_check_alert(cfg: dict, e: dict) -> None:
    """DM that a pre-show is stuck on YouTube's bot check even signed in: the cookies have
    rotated (10-31: an hour went by with only a log line). Once per video per cooldown_hours."""
    if spec := ytdl.browser(cfg):
        alerts.send(cfg, "failure", f"Pre-show \"{e['title']}\" is stuck on YouTube's bot check even signed in "
                    f"through the browser ({spec}): open that profile, check it is still signed in to "
                    "YouTube and play any video, then close it. The next check picks it up.",
                    key=f"preshow-bot:{e['video_id']}")
        return
    p = cfg["paths"]["youtube_cookies"]
    alerts.send(cfg, "failure", f"Pre-show \"{e['title']}\" is stuck on YouTube's bot check even signed in "
                f"with {p.name}: on the laptop, open the firefox:clipper profile, check it is still signed "
                "in to YouTube and play any video, close it, then run deploy\\cookies\\cookies-task.ps1 -Run "
                "(-Status shows whether the 4-hourly export runs). Or export fresh cookies into "
                f"{p.parent} as {ytdl.EXPORT_GLOB.replace('*', '<date>')}. The next check picks them up.",
                key=f"preshow-bot:{e['video_id']}")


def run_pending(cfg: dict, config_path: str | None = None) -> None:
    """`clipper run` each grabbed pre-show whose run isn't done (retries per [worker])."""
    from . import worker

    worker.setup_logging()
    for d in worker.pending(cfg):
        if not d.name.startswith(PREFIX):
            continue  # recordings are the worker's
        try:
            worker.run_stream(cfg, d, config_path)
        except worker.Busy as e:
            log.info("%s", e)


def show_list(cfg: dict) -> None:
    if not watched(cfg):
        print("no show has [shows.<key>.preshow] youtube_channel set")
    for key, sc in watched(cfg):
        pc = sc["preshow"]
        print(f"{key}: youtube channel {pc['youtube_channel']}, titles matching {pc['title_pattern']}")
        for e in feed(pc["youtube_channel"], sc["kick"]["user_agent"]):
            if not re.search(pc["title_pattern"], e["title"]):
                continue
            when = published_at(e).astimezone(
                ZoneInfo(sc["schedule"]["timezone"]))
            state = "grabbed" if grabbed(cfg, e["video_id"]) else "-"
            print(f"  {when:%a %m-%d %H:%M}  {state:<8} {PREFIX}{e['video_id']}  {e['title']}")


# ---------- the `segments` stage for a pre-show ----------

def write_segments(stream_dir: Path, cfg: dict, force: bool = False) -> list[dict]:
    """segments.json with one segment covering the whole video: no topic split, and no
    compilations. Title and description come from one small model call over the transcript.
    With a window in meta.json (a one-off grabbed with --from/--to), the segment is the
    stretch the model marks inside that window instead."""
    from .llm import Claude, preflight
    from .media import source_file
    from .prompts import Prompts
    from .segments import slug
    from .transcript import load_words, transcript_lines

    cfg = for_stream(cfg, stream_dir)
    pc = cfg["preshow"]
    path = stream_dir / "segments.json"
    if path.exists() and not force:
        data = jsonstate.load(path)
        print(f"{data['segments'][0]['id']}: {data['segments'][0]['title']} (already written)")
        return data["segments"]
    meta = jsonstate.load(stream_dir / "meta.json")
    words = load_words(stream_dir)
    full = media.try_duration(source_file(stream_dir), video=True) or meta["duration_s"]
    window = meta.get("window") or {}
    start = float(window.get("start_s") or 0.0)
    end = min(full, float(window["end_s"])) if window.get("end_s") else full
    if end == full and full > 2 * pc["trim_end_s"]:
        end -= pc["trim_end_s"]
    prompts = Prompts(cfg)
    claude = Claude(cfg)
    preflight(claude, pc["model"])
    user = (f"Original title: {meta['title']}\n\nOriginal description:\n{meta['description'][:2000]}"
            "\n\nTranscript:\n" + "\n".join(transcript_lines(words, start, end + 1, speakers=False)))
    if window:
        prompts.vars.update(about=window.get("about") or "the stretch described below",
                            window_start=f"{start:.0f}", window_end=f"{end:.0f}")
        user = (f"Window: {start:.0f} s to {end:.0f} s of the video. What to find: "
                f"{window.get('about') or '(not said; the one story this stretch tells)'}\n\n" + user)
    out, api = claude.json_call(pc["model"], prompts.get("oneoff" if window else "preshow"), user,
                                SCHEMA_WINDOW if window else SCHEMA, pc["effort"], fallback=True)
    if out is None:
        out = {"title": meta["title"], "topic": "Pre-show", "figures": [],
               "summary": "", "description": ""}
    if window:
        # The model's bounds, kept inside the window with a little room; a bad answer
        # (end before start) falls back to the whole window.
        a = max(start, float(out.pop("start_s", start)) - WINDOW_PAD_S)
        b = min(end, float(out.pop("end_s", end)) + WINDOW_PAD_S)
        if b - a < 60:
            log.warning("%s: model bounds %.0f-%.0f s too short, using the window", stream_dir.name, a, b)
            a, b = start, end
        start, end = a, b
    seg = {"start_s": round(start, 3), "end_s": round(end, 3), **out, "kind": "intro",
           "source_share": "most", "standalone": True, "id": "s00",
           "duration_s": round(end - start, 1),
           "file": f"s00_{slug(out['topic'] or 'pre-show')}.mp4", "cut": True, "skip_reasons": [],
           "preshow": True}
    data = {"stream_id": stream_dir.name, "model": pc["model"],
            "prompt_version": prompts.version_of("oneoff" if window else "preshow"),
            "api": api, "gaps": [], "segments": [seg], "compilations": [],
            "cost_usd": round(claude.cost, 4)}
    jsonstate.write(path, data)
    what = (f"one-off: {start / 60:.1f}-{end / 60:.1f} min" if window
            else f"pre-show: one segment, {end / 60:.1f} min"
                 + (f" ({full - end:.1f} s off the end)" if end < full else ""))
    print(f"{what}: {seg['title']}; ${claude.cost:.3f}")
    return [seg]
