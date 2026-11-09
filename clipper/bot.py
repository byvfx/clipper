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
(clipper/cardsync.py). Every minute it DMs the notifications other processes queued
(clipper/alerts.py), and every few minutes it posts the stories `clipper news check`
found. It logs to paths.bot_log as well as the console, only one bot runs per streams
folder (a lock file), and it writes a heartbeat (clipper/daemon.py) that the war room's
Health panel and `clipper services status` read. deploy/hub runs it as a container.

Everything that isn't Discord is `clipper serve` (clipper/serve.py): the war room, the
hourly replan and upload, pre-show grabs, retakes and the daily jobs. The bot asks it over
its API ([serve] url) to replan after a decision ([discord] replan) and to start a retake
after "Changes": when serve isn't up, the decision is still in Kitsu and serve's next
hourly run picks it up.

There is no bulk approve: every release needs its own Approve. Slash commands are
registered on [discord] guild_id only. The bot needs no privileged intents.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from . import actions, alerts, cardsync, daemon, digest, news, notify, schedule, serve
from .errors import ClipperError
from .timefmt import length

log = logging.getLogger("clipper.bot")

FORMS = {  # action: (modal title, field label, required, max length, multiline)
    "reject": ("Reject", "Why? (becomes a scoring example)", True, 1000, True),
    "changes": ("Request changes", "What should change?", True, 1000, True),
    "pin": ("Pin release time", "YYYY-MM-DD HH:MM, or a date; empty unpins", False, 16, False),
    "thumbtext": ("Thumbnail text", "2-4 words on the thumbnail", True, 40, False),
}
NEWS_POST_S = 300  # how often the bot posts what `clipper news check` (a serve job) found
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
        self.lock = asyncio.Lock()  # one Kitsu job at a time
        self.started_at = datetime.now(UTC).isoformat(timespec="seconds")
        register_commands(self)

    async def setup_hook(self) -> None:
        self.add_dynamic_items(CardButton)
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        if self.cfg["discord"]["refresh_minutes"] > 0:
            self.loop.create_task(self.refresh_cards())
        if self.cfg["news"]["check_minutes"] > 0:
            self.loop.create_task(self.news_loop())
        if self.cfg["notify"]["enabled"]:
            self.loop.create_task(self.notify_loop())
        self.loop.create_task(self.beat_loop())

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

    async def beat_loop(self) -> None:
        """The heartbeat `clipper services status` and the war room's Health panel read."""
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.to_thread(daemon.beat, self.cfg, "bot", self.started_at,
                                    user=str(self.user), guild=self.guild_id)
            await asyncio.sleep(daemon.BEAT_S)

    async def news_loop(self) -> None:
        """Post the stories `clipper news check` (a serve job) found, every few minutes."""
        await self.wait_until_ready()
        await asyncio.sleep(90)  # after serve's first check
        while not self.is_closed():
            if news.watchlist(self.cfg):
                try:
                    await self.post_news()
                except (Exception, SystemExit):
                    log.exception("news: posting failed")
            await asyncio.sleep(NEWS_POST_S)

    async def notify_loop(self) -> None:
        """DM the queued notifications (#22, clipper/alerts.py) every minute."""
        await self.wait_until_ready()
        nc = self.cfg["notify"]
        uid = int(nc["user_id"] or min(self.allowed, default=0))
        if not uid:
            log.warning("notify: no [notify] user_id or [discord] allowed_user_ids; no DMs")
            return
        link = ""  # the war room's tailnet URL for the DMs; serve knows it (and may not be up yet)
        share_down = 0.0
        while not self.is_closed():
            try:
                if not link and self.cfg["warroom"]["tailnet"]:
                    link = await asyncio.to_thread(serve.tailnet_link, self.cfg)
                msgs, files, st = await asyncio.to_thread(alerts.pending, self.cfg, link, digest.text)
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
                        await user.send(f"❌ The Clippa bot can't read the streams share: {e}")
                    except Exception:
                        log.exception("notify: DM failed")
            except (Exception, SystemExit):  # SystemExit would stop the event loop, and the bot
                log.exception("notify pass failed")
            await asyncio.sleep(60)

    async def news_channel(self):
        """[news] channel_id; else the text channel named [news] channel, made if missing; else
        the review channel. In a forum, one "News" post kept for it."""
        nc, dc = self.cfg["news"], self.cfg["discord"]
        name = str(nc["channel"]).strip().lstrip("#").lower()
        if not nc["channel_id"] and name:
            return await self.named_channel(name, "Headlines about the shows' people (clipper news): "
                                                  "Dismiss what isn't news, Tonight's context for the next stream")
        cid = int(nc["channel_id"] or dc["review_channel_id"] or 0)
        if not cid:
            raise ClipperError("set [news] channel (a name), channel_id or [discord] review_channel_id")
        channel = self.get_channel(cid) or await self.fetch_channel(cid)
        if not isinstance(channel, discord.ForumChannel):
            return channel
        path = news.home(self.cfg) / "discord.json"
        state = await asyncio.to_thread(news.jsonstate.read, path, {})
        if state.get("channel_id") == str(cid) and state.get("thread_id"):
            with contextlib.suppress(discord.NotFound):
                return self.get_channel(int(state["thread_id"])) or await self.fetch_channel(int(state["thread_id"]))
        made = await channel.create_thread(name="News", content="📰 Headlines about the shows' people "
                                           "(`clipper news`). Dismiss what isn't news; Tonight's context "
                                           "adds a story to the show's next stream.")
        await asyncio.to_thread(news.jsonstate.write, path, {"channel_id": str(cid), "thread_id": str(made.thread.id)})
        return made.thread

    async def named_channel(self, name: str, topic: str) -> discord.TextChannel:
        """The guild's text channel called name, made (with topic) if there is none."""
        guild = self.get_guild(self.guild_id) or await self.fetch_guild(self.guild_id)
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel:
            return channel
        try:
            channel = await guild.create_text_channel(name, topic=topic[:1024], reason="clipper news")
        except discord.Forbidden:
            raise ClipperError(f"couldn't make #{name}: give the bot Manage Channels, or make the channel "
                               "by hand") from None
        log.info(f"news: made #{name}")
        return channel

    async def post_news(self) -> None:
        cfg = self.cfg
        todo = await asyncio.to_thread(news.unposted, cfg)
        due = await asyncio.to_thread(news.due_suggestions, cfg) if cfg["news"]["preshow_minutes"] > 0 else []
        if not todo and not due:
            return
        channel = await self.news_channel()
        quiet = discord.AllowedMentions.none()  # headlines are outside text: never a ping
        for s in todo:
            view = discord.ui.View(timeout=None)
            view.add_item(CardButton("newsctx", str(s["id"]), "Tonight's context", discord.ButtonStyle.primary))
            view.add_item(CardButton("newsdismiss", str(s["id"]), "Dismiss"))
            msg = await channel.send(news.post_text(cfg, s), view=view, allowed_mentions=quiet)
            await asyncio.to_thread(news.mark, cfg, s["id"], posted_at=news.iso(news.now()), message_id=str(msg.id))
        for d in due:
            # The button carries the suggestion's number; its stories are kept with it.
            n = await asyncio.to_thread(news.mark_suggested, cfg, d["slot"], d["show"], d["stories"])
            view = discord.ui.View(timeout=None)
            view.add_item(CardButton("newsctx", f"s{n}", f"Context for {d['show_name']}"[:80],
                                     discord.ButtonStyle.primary))
            start = datetime.fromisoformat(d["start"])
            text = (f"🗞️ **{d['show_name']}** usually starts <t:{int(start.timestamp())}:R>. In the news today "
                    "about its people:\n" + "\n".join(f"- #{i} {h}" for i, h in zip(d["stories"], d["headlines"])))
            await channel.send(text[:1990], view=view, allowed_mentions=quiet)
        log.info(f"news: posted {len(todo)} story(ies), {len(due)} pre-show suggestion(s)")

    async def news_button(self, interaction: discord.Interaction, action: str, ref: str) -> None:
        """newsdismiss <id>; newsctx <id> (a story) or s<n> (a pre-show suggestion) shows the
        context text exactly as it would be queued, with an Add button (newsadd <show>.<id> or
        s<n>) that queues it for the show's next stream."""
        cfg, quiet = self.cfg, discord.AllowedMentions.none()
        show = ""
        try:
            if ref.startswith("s"):
                show, ids = await asyncio.to_thread(news.suggestion, cfg, int(ref[1:]))
            else:
                show, _, ids_text = ref.rpartition(".")
                ids = [int(x) for x in ids_text.split("-")]
        except ValueError:
            await interaction.response.send_message(f"Bad news button {ref}", ephemeral=True)
            return
        except ClipperError as e:
            await interaction.response.send_message(f"Nothing changed: {e}", ephemeral=True)
            return
        show = "" if show == "-" else show
        who = self.who(interaction)
        try:
            if action == "newsdismiss":
                await asyncio.to_thread(news.dismiss, cfg, ids)
                log.info(f"{who}: news dismiss {ids}")
                await interaction.response.edit_message(
                    content=f"~~{interaction.message.content[:1900]}~~\nDismissed by {interaction.user.name}.",
                    view=None, allowed_mentions=quiet)
            elif action == "newsctx":
                found = await asyncio.to_thread(news.stories, cfg, 3, ids)
                found.sort(key=lambda s: ids.index(s["id"]))
                text, used = news.context_block(found)  # what Add queues, whole (at most CONTEXT_MAX)
                show = show or (await asyncio.to_thread(news.story_show, cfg, found[0]) if found else "")
                view = discord.ui.View(timeout=None)
                add_ref = ref if ref.startswith("s") else f"{show or '-'}.{'-'.join(map(str, used))}"
                view.add_item(CardButton("newsadd", add_ref, "Add it", discord.ButtonStyle.success))
                await interaction.response.send_message(
                    f"For {news.show_names(cfg).get(show, show) or 'which show? (`clipper news context --show`)'}'s "
                    f"next stream, [scoring] stream_context gets:\n> {text}"[:2000],
                    view=view if show and used else None, ephemeral=True, allowed_mentions=quiet)
            elif action == "newsadd":
                e = await asyncio.to_thread(news.set_context, cfg, ids, show, who)
                log.info(f"{who}: news context {e['stories']} for {e['show']}")
                await interaction.response.edit_message(
                    content=f"Added to {news.show_names(cfg).get(e['show'], e['show'])}'s next stream: "
                            f"the worker puts it in that stream's config.toml before scoring.", view=None)
        except ClipperError as e:
            if interaction.response.is_done():
                await interaction.followup.send(f"Nothing changed: {e}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Nothing changed: {e}", ephemeral=True)

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
        if action.startswith("news"):
            await self.news_button(interaction, action, ref)
            return
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
        item = actions.resolve(self.cfg, ref)
        if action == "details":  # the bot's name for an edit; its form gives {column: value}
            row = actions.decide(self.cfg, item, "edit", who, values=text)
        else:  # hold flips (on=None), as the card's one Hold button does
            row = actions.decide(self.cfg, item, action, who, text=text)
        row, note = actions.replan(self.cfg, item, action, row, via=serve.replan)
        return row, f"; {note}" if note else ""

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
            try:
                await asyncio.to_thread(serve.start_retake, self.cfg)
                note += "; Clippa is working on it and will post the new version here"
            except ClipperError as e:
                note += f"; retake not started ({e}); serve's next check will"
        await interaction.followup.send(f"{verb} `{row['short']}` in Kitsu{note}.", ephemeral=True)


def register_commands(bot: Bot) -> None:  # noqa: C901
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
            title="Clippa status", description="\n".join(lines)[:4096], color=0x5865F2))

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
                await bot.blocking(serve.replan, cfg, True)
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


def run(cfg: dict) -> None:
    dc = cfg["discord"]
    if not notify.user_ids(dc):
        raise ClipperError("set [discord] allowed_user_ids (your Discord user id) in clipper.toml")
    if not dc["guild_id"]:
        raise ClipperError("set [discord] guild_id (your server's id) in clipper.toml")
    daemon.setup_logging(cfg["paths"]["bot_log"])
    try:
        lock = daemon.single_instance(cfg, "bot")
    except ClipperError as e:
        log.error(str(e))
        raise
    log.info(f"clipper bot starting (pid {os.getpid()}); card refresh every "
             f"{dc['refresh_minutes']} min; clipper serve at {serve.url(cfg)}")
    try:
        Bot(cfg).run(notify.token(cfg), log_handler=None)
    except BaseException as e:
        if not isinstance(e, KeyboardInterrupt):
            log.exception("clipper bot stopped")
        raise
    finally:
        lock.close()
    log.info("clipper bot stopped")
