"""Discord review cards: a thread (or forum post) per stream in [discord]
review_channel_id, and one message per item with a small preview, its title, hook and
Kitsu link, plus buttons that `clipper bot` answers (Approve, Reject, Changes, Hold,
Reopen, Pin, Retitle, Thumbnail text). The YouTube thumbnail (the .jpg beside the video)
is attached next to the preview when there is one. Every button writes to Kitsu (clipper/actions.py), so Kitsu stays
the record.

Posting uses Discord's REST API with the bot token, so `clipper review publish` posts
without the bot running; the buttons only respond while `clipper bot` is up.

Previews are re-rendered for Discord's upload limit (max_upload_mb, 10 MB unless the
server is boosted): 480p, bitrate sized to fit. Items too long to fit at min_video_kbps
(most segments) get their first excerpt_s seconds, and the card says the full preview is
in Kitsu. Posted message ids live in <stream>/discord.json, so reruns only post items
with a new Kitsu revision.

Token: $DISCORD_BOT_TOKEN ([discord] token_env), else the file at paths.discord_token.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from . import jsonstate, media
from .config import for_stream
from .errors import ClipperError
from .timefmt import length

API = "https://discord.com/api/v10"
COLORS = {"pending": 0x5865F2, "changes_requested": 0xFEE75C, "approved": 0x57F287,
          "rejected": 0xED4245, "held": 0x99AAB5}
KIND_LABEL = {"segment": "Segment", "pick": "Clip", "compilation": "Compilation",
              "short": "Short"}
# (action, label, style): 1 primary, 2 secondary, 3 success, 4 danger. "…" opens a form.
BUTTONS = [[("approve", "Approve", 3), ("reject", "Reject…", 4), ("changes", "Changes…", 2),
            ("hold", "Hold / unhold", 2), ("reopen", "Reopen", 2)],
           [("pin", "Pin time…", 2), ("retitle", "Retitle…", 2),
            ("thumbtext", "Thumbnail text…", 2)]]
MEDIA = {".mp4": "video/mp4", ".jpg": "image/jpeg"}
MAX_CONTENT = 2000  # characters in a Discord message
ISSUE_CLAIM_S = 30 * 60  # a `weekly --discord` run that died holding its issue lets go after this


def token(cfg: dict) -> str:
    dc = cfg["discord"]
    tok = os.environ.get(dc["token_env"], "").strip()
    path: Path = cfg["paths"]["discord_token"]
    if not tok and path.exists():
        tok = path.read_text(encoding="utf-8-sig").strip()
    if not tok:
        raise ClipperError(f"no Discord bot token: set ${dc['token_env']} or put it in {path}")
    if tok.count(".") != 2:
        raise ClipperError("that isn't a bot token (Developer Portal > your app > Bot > Reset "
                         "Token; the OAuth2 client secret won't work)")
    return tok


def user_ids(dc: dict) -> set[int]:
    ids = dc["allowed_user_ids"]
    if isinstance(ids, str):  # from CLIPPER_DISCORD_ALLOWED_USER_IDS="1,2"
        ids = ids.replace(",", " ").split()
    return {int(i) for i in ids}


class Missing(ClipperError):
    """Discord says the channel, thread or message isn't there (404): deleted, safe to remake.
    Any other failure may be passing, so the ids are kept."""


class Rest:
    def __init__(self, cfg: dict):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bot {token(cfg)}",
                               "User-Agent": "DiscordBot (clipper, 0.1)"})

    def __call__(self, method: str, path: str, payload: dict | None = None,
                 files: list[Path] | None = None) -> dict:
        """files: uploaded as files[0..n], matching payload attachments ids 0..n."""
        for _ in range(5):
            if files:
                with contextlib.ExitStack() as stack:
                    parts = {f"files[{i}]": (p.name, stack.enter_context(open(p, "rb")),
                                             MEDIA.get(p.suffix.lower(), "application/octet-stream"))
                             for i, p in enumerate(files)}
                    r = self.s.request(method, API + path,
                                       data={"payload_json": json.dumps(payload)},
                                       files=parts, timeout=300)
            else:
                r = self.s.request(method, API + path, json=payload, timeout=60)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 1)) + 0.2)
                continue
            if r.status_code == 404:
                raise Missing(f"Discord {method} {path}: 404 {r.text[:300]}")
            if r.status_code >= 400:
                raise ClipperError(f"Discord {method} {path}: {r.status_code} {r.text[:300]}")
            return r.json() if r.content else {}
        raise ClipperError(f"Discord {method} {path}: still rate limited")


# ---------- cards ----------

def card(r: dict) -> tuple[dict, list[dict]]:
    """Embed + button rows for one item (actions.describe row). Used by notify and bot."""
    from .actions import decision_text

    bits = [KIND_LABEL.get(r["kind"], r["kind"]), length(r["duration_s"])]
    if r["kind"] in ("pick", "short"):
        bits += [f"score {r['score']}" if r["score"] else "", r["category"],
                 f"found by {r['found_by']}" if r["found_by"] else ""]
    else:
        bits.append(r["topic"])
    desc = []
    if r["hook"]:
        desc.append(f"**Hook:** {r['hook']}")
    desc.append(" · ".join(b for b in bits if b))
    fields = [{"name": "Status", "value": decision_text(r), "inline": True}]
    if r["scheduled"]:
        when = r["scheduled"][:16].replace("T", " ")
        fields.append({"name": "Uploaded" if r["uploaded"] else "Scheduled",
                       "value": when, "inline": True})
    if r.get("thumb_text"):
        fields.append({"name": "Thumbnail", "value": r["thumb_text"], "inline": True})
    if r.get("other_voices"):  # held for it unless released ([shorts] hold_source_audio)
        fields.append({"name": "Held: other voices" if r["hold"] else "Other voices",
                       "value": r["other_voices"] + (". Release the hold if the show carries it."
                                                     if r["hold"] else ""), "inline": False})
    if r["comment"]:
        fields.append({"name": "Note", "value": r["comment"][:1000], "inline": False})
    color = COLORS["held"] if r["hold"] else COLORS.get(r["decision"], COLORS["pending"])
    embed = {"title": r["title"][:256], "url": r["kitsu_url"], "description": "\n".join(desc),
             "color": color, "fields": fields,
             "footer": {"text": f"{r['short']} · {r['date']} · rev {r['revision']}"}}
    rows = [{"type": 1, "components": [
        {"type": 2, "style": style, "label": label, "custom_id": f"clipper:{action}:{r['short']}"}
        for action, label, style in row]} for row in BUTTONS]
    links = [{"type": 2, "style": 5, "label": "Open in Kitsu", "url": r["kitsu_url"]}]
    if r["youtube_url"]:
        links.append({"type": 2, "style": 5, "label": "YouTube", "url": r["youtube_url"]})
    rows[1]["components"] += links
    return embed, rows


# ---------- previews ----------

def discord_preview(r: dict, dc: dict) -> tuple[Path, bool]:
    """A preview under the upload limit; True when it's only the opening excerpt."""
    src = r["preview"] if r["preview"].exists() else r["file"]
    dst = r["preview"].parent / "discord" / f"{r['key']}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = r["duration_s"] or media.duration(src)
    limit = dc["max_upload_mb"] * 1e6 * 0.97
    budget_kbit = limit * 0.93 * 8 / 1000  # leave room for container overhead
    audio = 64
    span = duration
    video = budget_kbit / span - audio
    if video < dc["min_video_kbps"]:
        span = min(duration, dc["excerpt_s"])
        video = budget_kbit / span - audio
    video = min(video, dc["max_video_kbps"])
    excerpt = span < duration - 1
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime and dst.stat().st_size <= limit:
        return dst, excerpt
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    for _ in range(3):
        v = int(video)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                        "-t", f"{span:.2f}", "-vf", f"scale=-2:{dc['preview_height']}", "-r", "30",
                        "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{v}k",
                        "-maxrate", f"{v}k", "-bufsize", f"{2 * v}k", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", f"{audio}k", "-ac", "2",
                        "-movflags", "+faststart", str(tmp)], check=True)
        if tmp.stat().st_size <= limit:
            tmp.replace(dst)
            return dst, excerpt
        video *= 0.8
    raise ClipperError(f"{r['key']}: preview still over {dc['max_upload_mb']} MB")


# ---------- posting ----------

def replace_thumb(api: Rest, channel_id: str, message_id: str, r: dict) -> None:
    """Swap a posted card's .jpg for the current thumbnail, keeping its preview video."""
    msg = api("GET", f"/channels/{channel_id}/messages/{message_id}")
    keep = [{"id": a["id"]} for a in msg.get("attachments", [])
            if not a["filename"].lower().endswith(".jpg")]
    api("PATCH", f"/channels/{channel_id}/messages/{message_id}",
        {"attachments": keep + [{"id": 0, "filename": r["thumbnail"].name}]},
        files=[r["thumbnail"]])


def save_state(dpath: Path, dstate: dict, key: str | None = None) -> None:
    """discord.json, re-read under its lock (a retake may post a card at the same time):
    a new thread replaces it, otherwise only card `key`'s message is written."""
    with jsonstate.update(dpath, {}) as fresh:
        if fresh.get("thread_id") != dstate.get("thread_id"):
            fresh.clear()
            fresh.update(dstate)
        elif key:
            fresh.setdefault("messages", {})[key] = dstate["messages"][key]


def ensure_thread(api: Rest, cfg: dict, sdir: Path, dstate: dict, rows: list[dict]) -> str:
    cfg = for_stream(cfg, sdir)
    dc = cfg["discord"]
    channel_id = str(dc["review_channel_id"])
    if dstate.get("thread_id") and dstate.get("channel_id") == channel_id:
        try:
            api("GET", f"/channels/{dstate['thread_id']}")
            return dstate["thread_id"]
        except ClipperError:
            pass  # deleted: make a new one
    meta = jsonstate.load(sdir / "meta.json")
    show = cfg["meta"]["show_name"] or cfg["review"]["project"] or "Stream"
    name = dc["thread_name"].format(show=show, date=meta["started_at"][:10],
                                    stream_id=sdir.name, title=meta.get("title", ""))[:100]
    n_seg = sum(r["kind"] == "segment" for r in rows)
    n_clip = len(rows) - n_seg
    header = {"content": (f"**{name}** · {meta.get('title', '')}\n"
                          f"{n_seg} segments and {n_clip} clips for review. "
                          f"Kitsu: <{rows[0]['episode_url']}>")[:2000]}
    thread_id = open_thread(api, channel_id, name, header)
    dstate.update({"channel_id": channel_id, "thread_id": thread_id, "messages": {}})
    return thread_id


def open_thread(api: Rest, channel_id: str, name: str, header: dict) -> str:
    """A new thread under header in a text channel, or a post in a forum; its id."""
    channel = api("GET", f"/channels/{channel_id}")
    if channel["type"] == 15:  # forum: the header is the post's first message
        thread = api("POST", f"/channels/{channel_id}/threads",
                     {"name": name, "auto_archive_duration": 10080, "message": header})
    else:
        msg = api("POST", f"/channels/{channel_id}/messages", header)
        thread = api("POST", f"/channels/{channel_id}/messages/{msg['id']}/threads",
                     {"name": name, "auto_archive_duration": 10080})
    return thread["id"]


def post(sdir: Path, cfg: dict, keys: list[str] | None = None, force: bool = False,
         note: str = "") -> int:
    """Post (or repost, for a new Kitsu revision) review cards for a stream's items.
    keys: item keys or their short names (c01, s02)."""
    cfg = for_stream(cfg, sdir)
    from . import actions, review

    dc = cfg["discord"]
    if not dc["review_channel_id"]:
        raise ClipperError("set [discord] review_channel_id in clipper.toml")
    review.sync(sdir, cfg, quiet=True)
    rows = list(actions.describe(cfg, sdir).values())
    if not rows:
        print("nothing published to Kitsu for this stream yet")
        return 0
    want = set(keys or [])
    todo = [r for r in rows if not want or r["key"] in want or r["key"].split("_")[0] in want]
    api = Rest(cfg)
    dpath = sdir / "discord.json"
    dstate = jsonstate.read(dpath, {})
    thread_id = ensure_thread(api, cfg, sdir, dstate, rows)
    save_state(dpath, dstate)
    posted = 0
    for r in todo:
        prev = dstate["messages"].get(r["key"])
        if prev and prev.get("revision") == r["revision"] and not force:
            continue
        t = time.time()
        file, excerpt = discord_preview(r, dc)
        embed, components = card(r)
        content = (f"First {int(dc['excerpt_s'])} s of {length(r['duration_s'])}; "
                   f"full preview in Kitsu." if excerpt else None)
        if note:
            content = note + (f"\n{content}" if content else "")
        files = [file] + ([r["thumbnail"]] if r["thumbnail"] else [])
        msg = api("POST", f"/channels/{thread_id}/messages",
                  {"content": content, "embeds": [embed], "components": components,
                   "attachments": [{"id": i, "filename": f.name} for i, f in enumerate(files)]},
                  files=files)
        dstate["messages"][r["key"]] = {"id": msg["id"], "revision": r["revision"],
                                        "thumb": r["thumb_mtime"]}
        save_state(dpath, dstate, r["key"])
        posted += 1
        print(f"discord {r['short']}: {file.stat().st_size / 1e6:.1f} MB"
              f"{' excerpt' if excerpt else ''} ({time.time() - t:.0f}s)", flush=True)
    print(f"discord: {posted} cards posted to thread {thread_id}")
    return posted


# ---------- weekly issue ----------

def issue_messages(issue: dict, here: Path, pages: list[Path]) -> list[tuple[str, str, list[Path]]]:
    """(key, text, files) per message: the ten 3:4 pages for the TikTok carousel, then the
    square pages as the two YouTube Posts with their captions, when youtube/ has all ten."""
    post = issue.get("post") or {}
    head = f"**Lolcow Cinema Weekly · issue {int(issue.get('issue') or 0):02d}** · {issue.get('week') or here.name}"
    out = [("tiktok", f"{head}\nTikTok carousel: pages 01-10 (1080x1440)", pages)]
    square = [here / "youtube" / p.name for p in pages]
    if all(p.exists() for p in square):
        out += [("youtube1", f"YouTube Post part 1 (square 01-05):\n{post.get('part1', '')}", square[:5]),
                ("youtube2", f"YouTube Post part 2 (square 06-10):\n{post.get('part2', '')}", square[5:])]
    out = [(k, t.strip(), f) for k, t, f in out]
    long = [k for k, t, _ in out if len(t) > MAX_CONTENT]
    if long:
        raise ClipperError(f"over Discord's {MAX_CONTENT} characters: {', '.join(long)}; "
                           f"shorten issue.json post.part1/part2")
    return out


def weekly_channel(api: Rest, dc: dict) -> str:
    """The id of the text channel named [discord] weekly_channel in guild_id, made if missing."""
    if not str(dc["weekly_channel"]).strip() or not dc["guild_id"]:
        raise ClipperError("set [discord] guild_id and weekly_channel in clipper.toml")
    return named_channel(api, dc, dc["weekly_channel"], "Weekly issues' pages, to save on a phone")


def named_channel(api: Rest, dc: dict, name: str, topic: str, kinds: tuple[int, ...] = (0,)) -> str:
    """The id of guild_id's channel called name of one of kinds (0 text, 15 forum), or a new
    text channel with that name and topic."""
    name = str(name).strip().lstrip("#").lower()
    if not name or not dc["guild_id"]:
        raise ClipperError("set [discord] guild_id and the channel's name in clipper.toml")
    for ch in api("GET", f"/guilds/{dc['guild_id']}/channels"):
        if ch["type"] in kinds and ch["name"] == name:
            return ch["id"]
    try:
        ch = api("POST", f"/guilds/{dc['guild_id']}/channels", {"name": name, "type": 0, "topic": topic[:1024]})
    except ClipperError as e:
        raise ClipperError(f"couldn't make #{name} ({e}); give the bot Manage Channels "
                           f"or make the channel by hand") from e
    print(f"discord: made #{name}")
    return ch["id"]


def claim_issue(dpath: Path, now: datetime | None = None) -> bool:
    """Take the issue for this run under discord.json's lock, so two runs can't post it twice;
    False while another run holds it (a claim older than ISSUE_CLAIM_S is taken over)."""
    now = now or datetime.now(UTC)
    with jsonstate.update(dpath, {}) as state:
        c = state.get("claim")
        if c and (now - datetime.fromisoformat(c)).total_seconds() < ISSUE_CLAIM_S:
            return False
        state["claim"] = now.isoformat(timespec="seconds")
    return True


def release_issue(dpath: Path) -> None:
    with jsonstate.update(dpath, {}) as state:
        state.pop("claim", None)


def publish_and_post(stream_dir: Path, cfg: dict, force: bool = False, only: list[str] | None = None,
                     post_cards: bool = True) -> list[str]:
    """`clipper review publish`, then a card for each item it published. The CLI, `pick`
    and `story cut` go through here so review.py itself never knows about Discord.
    post_cards=False (`--no-notify`, the worker without [worker] notify) publishes only.
    Returns the published keys."""
    from . import review

    cfg = for_stream(cfg, stream_dir)  # the stream's own [discord] enabled, as publish and post see it
    published = review.publish(stream_dir, cfg, force=force, only=only)
    if post_cards and published and cfg["discord"]["enabled"]:
        post(stream_dir, cfg, keys=published)
    return published


def post_issue(cfg: dict, day: str | None = None) -> int:
    """Post a weekly issue's pages to [discord] weekly_channel, to save them on a phone for
    posting by hand. <issue>/discord.json keeps each message, so a rerun posts only what's missing."""
    from .weekly import issue_folder, issue_pages

    dc = cfg["discord"]
    here = issue_folder(cfg, day)
    issue = jsonstate.load(here / "issue.json")
    messages = issue_messages(issue, here, issue_pages(here))
    limit = dc["max_upload_mb"] * 1e6
    big = [p.name for _, _, files in messages for p in files if p.stat().st_size > limit]
    if big:
        raise ClipperError(f"over [discord] max_upload_mb: {', '.join(big)}")
    api = Rest(cfg)
    channel_id = weekly_channel(api, dc)
    dpath = here / "discord.json"
    if not claim_issue(dpath):
        raise ClipperError(f"issue {here.name} is being posted to Discord by another run")
    posted = 0
    try:
        for key, text, files in messages:
            if jsonstate.read(dpath, {}).get("messages", {}).get(key, {}).get("channel_id") == channel_id:
                continue
            msg = api("POST", f"/channels/{channel_id}/messages",
                      {"content": text,
                       "attachments": [{"id": i, "filename": f.name} for i, f in enumerate(files)]},
                      files=files)
            with jsonstate.update(dpath, {}) as state:
                state.setdefault("messages", {})[key] = {"id": msg["id"], "channel_id": channel_id}
            posted += 1
            print(f"discord {key}: {len(files)} pages")
    finally:
        release_issue(dpath)
    print(f"discord: issue {here.name}, {posted} of {len(messages)} messages posted"
          f"{'' if posted == len(messages) else ' (the rest were already there)'}")
    return posted


def invite(cfg: dict, user_id: str) -> tuple[list[str], bool]:
    """`clipper review invite <discord user id>`: a reviewer onto [discord] allowed_user_ids in
    clipper.toml (the bot reads it at start, so restart it). (the ids after, whether it was new)."""
    from .config import base
    from .setup import toml_set

    uid = str(user_id).strip()
    if not uid.isdigit() or not 15 <= len(uid) <= 22:
        raise ClipperError(f"{user_id!r} isn't a Discord user id (17-20 digits: Settings > Advanced > "
                           "Developer Mode, then right-click the person > Copy User ID)")
    path = base(cfg).get("_file")
    if not path:
        raise ClipperError("no clipper.toml to write to: run clipper setup first")
    ids = [str(x) for x in cfg["discord"]["allowed_user_ids"]]
    if uid in ids:
        return ids, False
    ids.append(uid)
    toml_set(Path(path), "discord", "allowed_user_ids", ids)
    return ids, True
