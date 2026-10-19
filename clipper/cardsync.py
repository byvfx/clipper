"""Keep posted Discord review cards current with Kitsu (`clipper bot`, every [discord]
refresh_minutes).

Decisions made in Kitsu or the CLI don't go through the bot, so its cards would go stale.
For each stream with a <stream>/discord.json (posted cards) whose stream started within
[discord] refresh_days, refresh() syncs review.json from Kitsu, rebuilds each card with
notify.card() and edits the messages whose card changed (status, hold, pin, title,
scheduled time, YouTube link, thumbnail text...); a card posted with the thumbnail .jpg
also gets the new picture when the .jpg changed. It doesn't rerun `clipper schedule`: a card shows
"Scheduled" once something has scheduled it.

What each message last showed lives in <stream>/discord_cards.json (message id ->
signature), kept apart from discord.json so `clipper review notify` running at the same
time can't lose posted message ids.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import actions, notify, review
from .config import for_stream

log = logging.getLogger("clipper.cardsync")
STATE = "discord_cards.json"


def signature(embed: dict, components: list[dict]) -> str:
    raw = json.dumps([embed, components], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def stream_dirs(cfg: dict) -> list[Path]:
    """Streams with posted cards, started within refresh_days (0 = all)."""
    days = cfg["discord"]["refresh_days"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    out = []
    for d in actions.review_dirs(cfg):
        if not (d / "discord.json").exists():
            continue
        started = (actions.load_json(d / "meta.json", {}) or {}).get("started_at", "")
        if cutoff and started:
            try:
                when = datetime.fromisoformat(started.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when < cutoff:
                    continue
            except ValueError:
                pass
        out.append(d)
    return out


def remember(sdir: Path, message_id: str, sig: str | float) -> None:
    path = sdir / STATE
    seen = actions.load_json(path, {}) or {}
    seen[str(message_id)] = sig
    path.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def remember_row(row: dict, message_id: int | str, thumb: bool = False) -> None:
    """After the bot edited a card itself (a button), so the next pass skips it.
    thumb: the bot also swapped in the current thumbnail .jpg."""
    embed, comps = notify.card(row)
    sdir = row["preview"].parent.parent  # <stream>/review/<key>.mp4
    remember(sdir, str(message_id), signature(embed, comps))
    if thumb:
        remember(sdir, f"{message_id}/thumb", row["thumb_mtime"])


def refresh_stream(cfg: dict, sdir: Path, api: notify.Rest | None = None,
                   dry_run: bool = False) -> int:
    """Sync one stream from Kitsu and edit its cards that changed; returns edits made."""
    cfg = for_stream(cfg, sdir)
    dstate = actions.load_json(sdir / "discord.json", {}) or {}
    thread_id, messages = dstate.get("thread_id"), dstate.get("messages") or {}
    if not thread_id or not messages:
        return 0
    review.sync(sdir, cfg, quiet=True)
    rows = actions.describe(cfg, sdir)
    path = sdir / STATE
    seen = actions.load_json(path, {}) or {}
    edited = 0
    for key, m in messages.items():
        row = rows.get(key)
        if not row:
            continue
        embed, comps = notify.card(row)
        sig = signature(embed, comps)
        mid = str(m["id"])
        # Cards posted with the .jpg ("thumb" = its mtime then) get a re-rendered one.
        new_thumb = bool(m.get("thumb") and row["thumbnail"]
                         and seen.get(f"{mid}/thumb", m["thumb"]) != row["thumb_mtime"])
        if seen.get(mid) == sig and not new_thumb:
            continue
        if dry_run:
            log.info("would refresh %s (message %s)", row["short"], mid)
            edited += 1
            continue
        api = api or notify.Rest(cfg)
        try:
            api("PATCH", f"/channels/{thread_id}/messages/{mid}",
                {"embeds": [embed], "components": comps})
            if new_thumb:
                notify.replace_thumb(api, thread_id, mid, row)
                seen[f"{mid}/thumb"] = row["thumb_mtime"]
        except SystemExit as e:  # deleted message, archived/locked thread...
            log.warning("could not refresh %s card: %s", row["short"], e)
            if "Unknown Message" in str(e) or " 404 " in str(e):
                seen[mid] = sig  # gone for good; stop trying
            continue
        seen[mid] = sig
        edited += 1
        log.info("refreshed %s card: %s", row["short"], actions.decision_text(row))
    if not dry_run:
        path.write_text(json.dumps(seen, indent=2), encoding="utf-8")
    return edited
