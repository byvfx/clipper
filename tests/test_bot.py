"""The Discord bot (clipper/bot.py), the one process holding the token. Its handlers are thin:
resolve the item, decide through clipper/actions.py, replan through serve; the allowlist
refuses everyone else. Needs discord.py (the `discord` extra; CI installs it)."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("discord")

from clipper import actions, bot, serve


def test_chunks_keeps_each_message_under_the_limit():
    out = bot.chunks(["x" * 1000] * 5, size=2500)
    assert len(out) == 3 and all(len(c) <= 2501 for c in out)   # each line ends with a newline
    assert bot.chunks([]) == []


def make_bot(defaults) -> bot.Bot:
    cfg = {**defaults, "discord": {**defaults["discord"], "allowed_user_ids": [7], "guild_id": "42"}}
    return bot.Bot(cfg)


def test_do_decides_through_actions_and_replans_through_serve(defaults, monkeypatch):
    b = make_bot(defaults)
    item = actions.Item("s", "c01", {"kind": "pick"})
    calls = []
    monkeypatch.setattr(actions, "resolve", lambda cfg, ref: calls.append(("resolve", ref)) or item)
    monkeypatch.setattr(actions, "decide", lambda cfg, it, action, who, text="", on=None, values=None:
                        calls.append(("decide", action, text, values)) or {"short": "s/c01"})
    monkeypatch.setattr(actions, "replan", lambda cfg, it, action, row, via=None:
                        calls.append(("replan", action, via is serve.replan)) or (row, "scheduled Fri 16:00"))
    row, note = b.do("approve", "s/c01", "", "Discord @me")
    assert row == {"short": "s/c01"} and note == "; scheduled Fri 16:00"
    assert calls == [("resolve", "s/c01"), ("decide", "approve", "", None), ("replan", "approve", True)]
    calls.clear()
    b.do("details", "s/c01", {"Tags": "a, b"}, "Discord @me")   # the Retitle form: columns as one edit
    assert calls[1] == ("decide", "edit", "", {"Tags": "a, b"})
    monkeypatch.setattr(actions, "replan", lambda cfg, it, action, row, via=None: (row, ""))
    assert b.do("hold", "s/c01", "", "Discord @me") == ({"short": "s/c01"}, "")   # no note when nothing moved


class FakeResponse:
    def __init__(self, done: bool):
        self.done, self.sent = done, []

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, msg: str, ephemeral: bool = False) -> None:
        self.sent.append(msg)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, msg: str, ephemeral: bool = False) -> None:
        self.sent.append(msg)


def interaction(user_id: int, guild_id: int = 42, done: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=user_id, name="who"), guild_id=guild_id,
                           response=FakeResponse(done), followup=FakeFollowup())


def test_check_refuses_anyone_off_the_allowlist_or_in_another_guild(defaults):
    b = make_bot(defaults)
    ok = interaction(7)
    assert asyncio.run(b.check(ok)) is True and ok.response.sent == []
    stranger = interaction(8)
    assert asyncio.run(b.check(stranger)) is False
    assert stranger.response.sent == ["You're not on this bot's allowlist."]
    elsewhere = interaction(7, guild_id=1, done=True)   # after a defer the answer goes by followup
    assert asyncio.run(b.check(elsewhere)) is False and elsewhere.followup.sent == ["You're not on this bot's allowlist."]
