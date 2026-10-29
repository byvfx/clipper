"""Discord review bot (`clipper bot`): answers the buttons on review cards (clipper/
notify.py) and a few slash commands, for [discord] allowed_user_ids only.

    buttons   Approve, Reject…, Changes…, Hold / unhold, Reopen, Pin time…, Retitle…,
              Thumbnail text… ("…" opens a form). Each writes to Kitsu through
              clipper/actions.py, then (with [discord] replan) reruns `clipper schedule`,
              and updates the card; Thumbnail text also swaps in the re-rendered .jpg.
              Retitle… edits the YouTube title, description, hashtags, tags and
              playlist in one form.
    /status   decision counts per stream and what's scheduled
    /pending  items waiting on a decision (all=True: every item), with Kitsu links
    /item     show one item's card with its buttons (preview=True attaches the preview)
    /schedule upcoming releases from schedule.json (replan=True reruns the schedule first)
    /post     post a stream's review cards (what `clipper review notify` does)

Every [discord] refresh_minutes the bot also re-reads Kitsu for streams with posted cards
and edits cards whose status, hold, pin, title or schedule changed outside Discord
(clipper/cardsync.py). It logs to paths.bot_log as well as the console, and only one bot
runs per streams folder (a lock file). deploy/discord/bot-task.ps1 runs it at logon.
Every [preshow] poll_minutes it runs `clipper preshow check --run`: new pre-shows on the
shows' YouTube channels are grabbed and put through the pipeline (clipper/preshow.py).

Background jobs (`jobs_loop`, their own loop, independent of the card refresh): every
[youtube] auto_upload_minutes `clipper schedule` then `clipper upload`; every [tiktok]
auto_send_minutes `clipper tiktok --auto`; every [analytics] auto_hours `clipper analytics`;
with [retake] auto, `clipper retake` when Kitsu has new retake notes. Each runs as a child
process and a non-zero exit is DMed.

There is no bulk approve: every release needs its own Approve. Slash commands are
registered on [discord] guild_id only. The bot needs no privileged intents.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from . import actions, alerts, cardsync, notify, preshow, retake, schedule
from .config import _root
from .errors import ClipperError
from .timefmt import length

log = logging.getLogger("clipper.bot")

FORMS = {  # action: (modal title, field label, required, max length, multiline)
    "reject": ("Reject", "Why? (becomes a scoring example)", True, 1000, True),
    "changes": ("Request changes", "What should change?", True, 1000, True),
    "pin": ("Pin release time", "YYYY-MM-DD HH:MM, or a date; empty unpins", False, 16, False),
    "thumbtext": ("Thumbnail text", "2-4 words on the thumbnail", True, 40, False),
}
DONE = {"approve": "Approved", "reject": "Rejected", "changes": "Changes requested for",
        "reopen": "Reopened", "hold": "Hold toggled on", "pin": "Pinned",
        "thumbtext": "New thumbnail text for"}


class UserError(Exception):
    pass


def chunks(lines: list[str], size: int = 3900) -> list[str]:
    out, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    return out + ([cur] if cur else [])


class CardButton(discord.ui.DynamicItem[discord.ui.Button],
                 template=r"clipper:(?P<action>[a-z]+):(?P<ref>\S+)"):
    """Every card button, also on cards posted before the bot (re)started."""

    def __init__(self, action: str, ref: str, label: str | None = None,
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary, row: int | None = None):
        super().__init__(discord.ui.Button(label=label or action, style=style, row=row,
                                           custom_id=f"clipper:{action}:{ref}"))
        self.action, self.ref = action, ref

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["ref"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await interaction.client.check(interaction)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.on_button(interaction, self.action, self.ref)


# The Retitle… button's form: (Kitsu column, actions.describe key, label, max length,
# multiline). Only fields you change are written. A value longer than Discord allows
# (4000) is left out of the form; edit it in Kitsu or with `clipper review`.
DETAILS = [("YouTube title", "title", "YouTube title", 100, False),
           ("YouTube description", "description", "Description (hashtags go below it)", 4000, True),
           ("Hashtags", "hashtags", "Hashtags (closing the description)", 500, False),
           ("Tags", "tags", "Tags, comma-separated (about 500 chars)", 4000, True),
           ("Playlist", "playlist", "Playlist", 150, False)]


class Details(discord.ui.Modal):
    def __init__(self, bot: Bot, ref: str, row: dict):
        super().__init__(title="YouTube title and details", timeout=900)
        self.bot, self.ref, self.fields = bot, ref, []
        for col, key, label, max_len, multiline in DETAILS:
            shown = row.get(key) or ""
            if len(shown) > max_len:
                continue
            field = discord.ui.TextInput(
                label=label, required=False, max_length=max_len, default=shown or None,
                style=discord.TextStyle.paragraph if multiline else discord.TextStyle.short)
            self.add_item(field)
            self.fields.append((col, field, shown))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        same = lambda a, b: a.split() == b.split()
        changes = {col: f.value for col, f, shown in self.fields if not same(f.value, shown)}
        await self.bot.apply(interaction, "details", self.ref, changes)


class Form(discord.ui.Modal):
    def __init__(self, bot: Bot, action: str, ref: str, default: str = ""):
        title, label, required, max_len, multiline = FORMS[action]
        super().__init__(title=title, timeout=900)
        self.bot, self.action, self.ref = bot, action, ref
        self.field = discord.ui.TextInput(
            label=label, required=required, max_length=max_len, default=default[:max_len] or None,
            style=discord.TextStyle.paragraph if multiline else discord.TextStyle.short)
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.bot.apply(interaction, self.action, self.ref, self.field.value)


def view_for(components: list[dict]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for i, row in enumerate(components):
        for c in row["components"]:
            if c["style"] == 5:
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=c["label"],
                                                url=c["url"], row=i))
            else:
                _, action, ref = c["custom_id"].split(":", 2)
                view.add_item(CardButton(action, ref, c["label"], discord.ButtonStyle(c["style"]), i))
    return view


class Bot(discord.Client):
    def __init__(self, cfg: dict):
        super().__init__(intents=discord.Intents(guilds=True))
        self.cfg = cfg
        dc = cfg["discord"]
        self.allowed = notify.user_ids(dc)
        self.guild_id = int(dc["guild_id"]) if dc["guild_id"] else None
        self.tree = app_commands.CommandTree(self)
        self.lock = asyncio.Lock()  # one Kitsu/schedule job at a time
        self.retake_proc: asyncio.subprocess.Process | None = None
        self.retake_again = False
        self.upload_proc: asyncio.subprocess.Process | None = None
        self.analytics_proc: asyncio.subprocess.Process | None = None
        self.preshow_proc: asyncio.subprocess.Process | None = None
        self.tiktok_proc: asyncio.subprocess.Process | None = None
        register_commands(self)

    async def setup_hook(self) -> None:
        self.add_dynamic_items(CardButton)
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        if self.cfg["discord"]["refresh_minutes"] > 0:
            self.loop.create_task(self.refresh_cards())
        self.loop.create_task(self.jobs_loop())
        if preshow.watched(self.cfg) and self.cfg["preshow"]["poll_minutes"] > 0:
            self.loop.create_task(self.preshow_loop())
        if self.cfg["notify"]["enabled"]:
            self.loop.create_task(self.notify_loop())

    async def on_ready(self) -> None:
        log.info(f"clipper bot ready as {self.user} in guild {self.guild_id}; "
                 f"{len(self.allowed)} allowed user(s)")

    async def refresh_cards(self) -> None:
        """Edit cards whose Kitsu state changed outside Discord, every refresh_minutes."""
        await self.wait_until_ready()
        await asyncio.sleep(30)
        every = self.cfg["discord"]["refresh_minutes"] * 60
        while not self.is_closed():
            edited = 0
            try:
                for d in await asyncio.to_thread(cardsync.stream_dirs, self.cfg):
                    try:  # one stream at a time, so buttons don't wait on the whole pass
                        edited += await self.blocking(cardsync.refresh_stream, self.cfg, d)
                    except UserError as e:
                        log.warning(f"card refresh {d.name}: {e}")
            except (Exception, SystemExit):
                log.exception("card refresh failed")
            if edited:
                log.info(f"card refresh: {edited} card(s) updated")
            await asyncio.sleep(every)

    def running(self, name: str) -> bool:
        proc = getattr(self, f"{name}_proc")
        return bool(proc and proc.returncode is None)

    def jobs(self) -> list[tuple[str, float, object, object]]:
        """(name, every s, ready() -> bool, start()) for the background jobs; every <= 0 is off."""
        cfg, paths = self.cfg, self.cfg["paths"]
        has_upload = lambda: paths["schedule"].exists() \
            and '"uploaded"' in paths["schedule"].read_text(encoding="utf-8")
        return [
            ("upload", cfg["youtube"]["auto_upload_minutes"] * 60,
             lambda: not self.running("upload"),
             lambda: self.loop.create_task(self._auto_upload())),
            ("tiktok", cfg["tiktok"]["auto_send_minutes"] * 60,
             lambda: paths["tiktok_token"].exists() and not self.running("tiktok"),
             lambda: self.loop.create_task(self._clipper_bg("tiktok", ["tiktok", "--auto"]))),
            ("analytics", cfg["analytics"]["auto_hours"] * 3600,
             lambda: paths["youtube_token"].exists() and has_upload(),
             lambda: self.loop.create_task(self._clipper_bg("analytics", ["analytics"]))),
            # retake notes made in Kitsu (review.json is synced by the card refresh and the replan)
            ("retake", (cfg["discord"]["refresh_minutes"] or 10) * 60 if cfg["retake"]["auto"] else 0,
             lambda: retake.pending(cfg), self.start_retake),
        ]

    async def jobs_loop(self) -> None:
        """Start each background job when it's due. Its own loop, not the card refresh's,
        so turning card refresh off ([discord] refresh_minutes = 0) leaves uploads running."""
        await self.wait_until_ready()
        await asyncio.sleep(30)
        last: dict[str, float] = {}
        while not self.is_closed():
            for name, every, ready, start in self.jobs():
                if every <= 0 or time.monotonic() - last.get(name, float("-inf")) < every:
                    continue
                try:
                    if not await asyncio.to_thread(ready):
                        continue
                    last[name] = time.monotonic()
                    start()
                except (Exception, SystemExit):  # SystemExit would stop the event loop, and the bot
                    log.exception(f"{name} check failed")
            await asyncio.sleep(60)

    async def preshow_loop(self) -> None:
        """Grab new pre-shows from the shows' YouTube feeds and run them, every poll_minutes."""
        await self.wait_until_ready()
        every = self.cfg["preshow"]["poll_minutes"] * 60
        while not self.is_closed():
            if not (self.preshow_proc and self.preshow_proc.returncode is None):
                self.loop.create_task(self._clipper_bg("preshow", ["preshow", "check", "--run"]))
            await asyncio.sleep(every)

    async def notify_loop(self) -> None:
        """DM the queued notifications (#22, clipper/alerts.py) every minute."""
        await self.wait_until_ready()
        nc = self.cfg["notify"]
        uid = int(nc["user_id"] or min(self.allowed, default=0))
        if not uid:
            log.warning("notify: no [notify] user_id or [discord] allowed_user_ids; no DMs")
            return
        link = ""
        if self.cfg["warroom"]["tailnet"]:
            from . import warroom

            link = (await asyncio.to_thread(warroom.tailnet_status, self.cfg))["served"]
        share_down = 0.0
        while not self.is_closed():
            try:
                msgs, files, st = await asyncio.to_thread(alerts.pending, self.cfg, link)
                if msgs:
                    user = self.get_user(uid) or await self.fetch_user(uid)
                    for m in msgs:
                        await user.send(m)
                    log.info(f"notify: sent {len(files)} notification(s) in {len(msgs)} DM(s)")
                if files:  # sent, or skipped (kind off, cooldown): either way handled
                    await asyncio.to_thread(alerts.done, self.cfg, files, st)
                share_down = 0.0
            except OSError as e:  # the outbox lives on the streams share: say so directly
                log.warning(f"notify: {e}")
                if time.monotonic() - share_down > nc["cooldown_hours"] * 3600:
                    share_down = time.monotonic()
                    try:
                        user = self.get_user(uid) or await self.fetch_user(uid)
                        await user.send(f"❌ clipper bot can't read the streams share: {e}")
                    except Exception:
                        log.exception("notify: DM failed")
            except (Exception, SystemExit):  # SystemExit would stop the event loop, and the bot
                log.exception("notify pass failed")
            await asyncio.sleep(60)

    def start_retake(self) -> None:
        """`clipper retake` in the background: act on retake notes, republish for review.
        A request while one runs makes it run once more when it ends."""
        if not self.cfg["retake"]["auto"]:
            return
        if self.retake_proc and self.retake_proc.returncode is None:
            self.retake_again = True
            return
        self.loop.create_task(self._retake())

    async def _clipper_bg(self, name: str, args: list[str]) -> int | None:
        """Run `clipper <args>` as a child process, its output into the bot log."""
        cmd = [sys.executable, "-m", "clipper.cli"]
        if _root(self.cfg).get("_file"):
            cmd += ["--config", str(_root(self.cfg)["_file"])]
        log.info(f"{name}: starting")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
            setattr(self, f"{name}_proc", proc)
            out, _ = await proc.communicate()
            for line in out.decode("utf-8", "replace").splitlines():
                if line.strip() and not line.startswith("["):  # skip ffmpeg decoder noise
                    log.info(f"{name}: {line}")
            log.info(f"{name}: done (exit {proc.returncode})")
            if proc.returncode:  # #22: a background job failing is worth a DM (once per cooldown)
                last = next((x.strip() for x in reversed(out.decode("utf-8", "replace").splitlines())
                             if x.strip() and not x.startswith("[")), "")
                alerts.send(self.cfg, "upload" if name in ("upload", "tiktok") else "failure",
                            f"`clipper {' '.join(args)}` failed (exit {proc.returncode}): {last[:400]}",
                            key=f"bg:{' '.join(args)}")
            return proc.returncode
        except Exception:
            log.exception(f"{name} run failed")
            return None

    async def _auto_upload(self) -> None:
        """Replan first (slots that passed get new ones), then upload what's due. A failed
        replan (Kitsu down) is DMed on its own and the upload still runs on the last plan,
        so one outage doesn't miss every slot that hour."""
        await self._clipper_bg("upload", ["schedule"])
        await self._clipper_bg("upload", ["upload"])

    async def _retake(self) -> None:
        await self._clipper_bg("retake", ["retake"])
        if self.retake_again:
            self.retake_again = False
            self.start_retake()

    async def check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.allowed and interaction.guild_id == self.guild_id:
            return True
        msg = "You're not on this bot's allowlist."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        log.warning(f"refused {interaction.user} ({interaction.user.id})")
        return False

    async def blocking(self, fn, *args, **kwargs):
        """Run clipper code (sync, may print or raise ClipperError) off the event loop."""
        def run():
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    return fn(*args, **kwargs)
            except (ClipperError, SystemExit) as e:
                raise UserError(str(e)) from None
            finally:
                if out.getvalue().strip():
                    log.info(out.getvalue().rstrip())
        async with self.lock:
            return await asyncio.to_thread(run)

    def who(self, interaction: discord.Interaction) -> str:
        return f"Discord @{interaction.user.name}"

    # ---------- buttons ----------

    async def on_button(self, interaction: discord.Interaction, action: str, ref: str) -> None:
        if action == "retitle":
            try:
                item = actions.resolve(self.cfg, ref)
                row = actions.describe(self.cfg, item.stream_dir)[item.key]
            except (ClipperError, KeyError) as e:
                await interaction.response.send_message(f"Can't edit {ref}: {e}", ephemeral=True)
                return
            await interaction.response.send_modal(Details(self, ref, row))
            return
        if action in FORMS:
            default = ""
            if action in ("pin", "thumbtext"):
                try:
                    item = actions.resolve(self.cfg, ref)
                    row = actions.describe(self.cfg, item.stream_dir)[item.key]
                    default = row[{"pin": "publish_at", "thumbtext": "thumb_text"}[action]]
                except (ClipperError, KeyError):
                    pass
            await interaction.response.send_modal(Form(self, action, ref, default[:100]))
            return
        await interaction.response.defer()
        await self.apply(interaction, action, ref, "")

    def do(self, action: str, ref: str, text, who: str) -> tuple[dict, str]:
        """text: what the form said; for "details", {Kitsu column: new value}."""
        cfg = self.cfg
        item = actions.resolve(cfg, ref)
        if action == "hold":
            on = not actions.refresh(cfg, item)["hold"]
            row = actions.hold(cfg, item, on, who)
        else:
            fn = {"approve": lambda: actions.approve(cfg, item, "", who),
                  "reject": lambda: actions.reject(cfg, item, text, who),
                  "changes": lambda: actions.changes(cfg, item, text, who),
                  "reopen": lambda: actions.reopen(cfg, item, who),
                  "pin": lambda: actions.pin(cfg, item, text, who),
                  "details": lambda: actions.edit_meta(cfg, item, text, who),
                  "thumbtext": lambda: actions.thumbtext(cfg, item, text, who)}.get(action)
            if not fn:
                raise ClipperError(f"unknown action {action!r}")
            row = fn()
        note = ""
        if cfg["discord"]["replan"]:
            schedule.run(cfg, sync_first=False)
            row = actions.describe(cfg, item.stream_dir)[item.key]
        if row["scheduled"]:
            note = f"; scheduled {row['scheduled'][:16].replace('T', ' ')}"
        elif row["decision"] == "approved" and cfg["discord"]["replan"]:
            note = "; no free slot yet" if not row["hold"] else "; held, not scheduled"
        return row, note

    async def apply(self, interaction: discord.Interaction, action: str, ref: str, text) -> None:
        who = self.who(interaction)
        log.info(f"{who}: {action} {ref} {text!r}")
        try:
            row, note = await self.blocking(self.do, action, ref, text, who)
        except UserError as e:
            await interaction.followup.send(f"Nothing changed: {e}", ephemeral=True)
            return
        embed, _ = notify.card(row)
        if interaction.message:
            kwargs = {}
            thumb = action == "thumbtext" and row["thumbnail"] is not None
            if thumb:  # the re-rendered .jpg in place of the old one, preview kept
                kwargs["attachments"] = [
                    *(a for a in interaction.message.attachments
                      if not a.filename.lower().endswith(".jpg")),
                    discord.File(row["thumbnail"])]
            await interaction.edit_original_response(embed=discord.Embed.from_dict(embed), **kwargs)
            with contextlib.suppress(Exception):  # so the next refresh pass skips this card
                cardsync.remember_row(row, interaction.message.id, thumb)
        verb = DONE.get(action, "")
        if action == "details":
            verb = "Changed " + ", ".join(c.replace("YouTube ", "") for c in text).lower() + " of"
        if action == "hold":
            verb = "Held" if row["hold"] else "Released hold on"
        if action == "pin" and not row["publish_at"]:
            verb = "Unpinned"
        if action == "changes" and self.cfg["retake"]["auto"]:
            note += "; clipper is working on it and will post the new version here"
            self.start_retake()
        await interaction.followup.send(f"{verb} `{row['short']}` in Kitsu{note}.", ephemeral=True)


def register_commands(bot: Bot) -> None:
    tree, cfg = bot.tree, bot.cfg

    async def streams(interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=d.name, value=d.name)
                for d in reversed(actions.review_dirs(cfg)) if current in d.name][:25]

    async def guard(interaction: discord.Interaction) -> bool:
        return await bot.check(interaction)
    tree.interaction_check = guard

    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if not isinstance(error, app_commands.CheckFailure):  # refusals are answered in check()
            raise error
    tree.on_error = on_error

    async def fail(interaction: discord.Interaction, e: Exception) -> None:
        await interaction.followup.send(f"Error: {e}", ephemeral=True)

    @tree.command(description="Decision counts per stream, and what's scheduled")
    async def status(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            s = await bot.blocking(actions.status, cfg, True)
        except UserError as e:
            return await fail(interaction, e)
        lines = []
        for st in s["streams"]:
            c = st["counts"]
            parts = [f"{c[k]} {k.replace('_', ' ')}" for k in
                     ("pending", "changes_requested", "approved", "rejected") if c.get(k)]
            held = f", {st['held']} held" if st["held"] else ""
            lines.append(f"[{st['date']}]({st['episode_url']}) `{st['stream']}`: "
                         f"{', '.join(parts) or 'empty'}{held}")
        nxt = s["next"]
        lines.append(f"\n**Schedule:** {s['next_7_days']} releases in the next 7 days, "
                     f"{s['uploaded']} uploaded"
                     + (f"\nNext: {nxt['publish_at'][:16].replace('T', ' ')} · {nxt['title']}"
                        if nxt else ""))
        await interaction.followup.send(embed=discord.Embed(
            title="clipper status", description="\n".join(lines)[:4096], color=0x5865F2))

    @tree.command(description="Items waiting on a decision, with Kitsu links")
    @app_commands.describe(stream="only this stream", all="every item, decided or not")
    @app_commands.autocomplete(stream=streams)
    async def pending(interaction: discord.Interaction, stream: str | None = None, all: bool = False):
        await interaction.response.defer(thinking=True)
        try:
            rows = await bot.blocking(actions.list_items, cfg, stream, all, True)
        except UserError as e:
            return await fail(interaction, e)
        if not rows:
            return await interaction.followup.send("Nothing waiting for review.")
        lines = [f"`{r['short']}` {length(r['duration_s'])} "
                 f"[{r['title'][:80]}]({r['kitsu_url']}) · {actions.decision_text(r)}"
                 for r in rows]
        pages = chunks(lines)
        for i in range(0, len(pages), 10):
            await interaction.followup.send(embeds=[
                discord.Embed(title=f"{len(rows)} items" if i + j == 0 else None,
                              description=p, color=0x5865F2)
                for j, p in enumerate(pages[i:i + 10])])

    @tree.command(description="Show one item's card and buttons")
    @app_commands.describe(item="e.g. c03 or kick-131151926/s02", preview="attach the preview video")
    async def item(interaction: discord.Interaction, item: str, preview: bool = False):
        await interaction.response.defer(thinking=True)

        def load():
            it = actions.resolve(cfg, item)
            row = actions.refresh(cfg, it)
            files = [notify.discord_preview(row, cfg["discord"])[0]] if preview else []
            return row, files + ([row["thumbnail"]] if row["thumbnail"] else [])
        try:
            row, files = await bot.blocking(load)
        except UserError as e:
            return await fail(interaction, e)
        embed, comps = notify.card(row)
        await interaction.followup.send(embed=discord.Embed.from_dict(embed), view=view_for(comps),
                                        files=[discord.File(f) for f in files])

    @tree.command(name="schedule", description="Upcoming releases from schedule.json")
    @app_commands.describe(days="how far ahead", replan="rerun clipper schedule first (syncs Kitsu)")
    async def schedule_cmd(interaction: discord.Interaction, days: int = 7, replan: bool = False):
        await interaction.response.defer(thinking=True)
        try:
            if replan:
                await bot.blocking(schedule.run, cfg)
            entries = await bot.blocking(schedule.upcoming, cfg, days)
        except UserError as e:
            return await fail(interaction, e)
        tz = ZoneInfo(cfg["schedule"]["timezone"])
        lines = [f"`{datetime.fromisoformat(e['publish_at']).astimezone(tz):%a %m-%d %H:%M}` "
                 f"{e['kind']} · {e['title'][:80]}"
                 + (" · uploaded" if e.get("status") == "uploaded" else "")
                 + (" · pinned" if e.get("source") == "pinned" else "") for e in entries]
        pages = chunks(lines or ["Nothing scheduled."])
        await interaction.followup.send(embeds=[
            discord.Embed(title=f"Next {days} days ({cfg['schedule']['timezone']})" if i == 0 else None,
                          description=p, color=0x57F287) for i, p in enumerate(pages[:10])])

    @tree.command(description="Post a stream's review cards to the review channel")
    @app_commands.describe(stream="stream id", force="repost items already posted")
    @app_commands.autocomplete(stream=streams)
    async def post(interaction: discord.Interaction, stream: str, force: bool = False):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            n = await bot.blocking(notify.post, actions.stream_dir(cfg, stream), cfg, None, force)
        except UserError as e:
            return await fail(interaction, e)
        await interaction.followup.send(f"Posted {n} cards for `{stream}`.", ephemeral=True)


def setup_logging(path) -> None:
    """Console plus paths.bot_log (rotated at 5 MB, 5 kept); empty bot_log = console only."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if path and not path.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)


def single_instance(cfg: dict):
    """Hold a lock on <streams>/.bot.lock for the process's life; refuse a second bot
    (two would race to answer every button)."""
    path = cfg["paths"]["streams"] / ".bot.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")  # noqa: SIM115 (held for the process's life)
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise ClipperError(f"another clipper bot is already running (lock {path})") from None
    return f


def run(cfg: dict) -> None:
    dc = cfg["discord"]
    if not notify.user_ids(dc):
        raise ClipperError("set [discord] allowed_user_ids (your Discord user id) in clipper.toml")
    if not dc["guild_id"]:
        raise ClipperError("set [discord] guild_id (your server's id) in clipper.toml")
    setup_logging(cfg["paths"]["bot_log"])
    try:
        lock = single_instance(cfg)
    except ClipperError as e:
        log.error(str(e))
        raise
    log.info(f"clipper bot starting (pid {os.getpid()}); card refresh every "
             f"{dc['refresh_minutes']} min")
    if cfg["warroom"]["with_bot"]:  # the dashboard rides along, so it's up whenever the bot is
        import threading

        from . import warroom
        try:
            srv = warroom.server(cfg)
            threading.Thread(target=srv.serve_forever, daemon=True, name="warroom").start()
            log.info(f"war room on {warroom.url(cfg, with_key=False)}")  # no token in the log
        except OSError as e:
            log.warning(f"war room not started: {e}")
    try:
        Bot(cfg).run(notify.token(cfg), log_handler=None)
    except BaseException as e:
        if not isinstance(e, KeyboardInterrupt):
            log.exception("clipper bot stopped")
        raise
    finally:
        lock.close()
    log.info("clipper bot stopped")
