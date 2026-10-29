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
    channel = api("GET", f"/channels/{channel_id}")
    if channel["type"] == 15:  # forum: the header is the post's first message
        thread = api("POST", f"/channels/{channel_id}/threads",
                     {"name": name, "auto_archive_duration": 10080, "message": header})
    else:
        msg = api("POST", f"/channels/{channel_id}/messages", header)
        thread = api("POST", f"/channels/{channel_id}/messages/{msg['id']}/threads",
                     {"name": name, "auto_archive_duration": 10080})
    dstate.update({"channel_id": channel_id, "thread_id": thread["id"], "messages": {}})
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
