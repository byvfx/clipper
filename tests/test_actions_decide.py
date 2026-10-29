"""actions.decide / replan: the one path the bot's buttons and the war room take."""
from types import SimpleNamespace

import pytest

from clipper import actions, schedule
from clipper.errors import ClipperError

ITEM = SimpleNamespace(stream_dir="s", key="k")


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for name in ("approve", "reject", "changes", "reopen", "pin", "thumbtext"):
        monkeypatch.setattr(actions, name, lambda cfg, item, *a, _n=name: seen.append((_n, a)) or {})
    monkeypatch.setattr(actions, "hold", lambda cfg, item, on, who: seen.append(("hold", (on, who))) or {})
    monkeypatch.setattr(actions, "edit_meta", lambda cfg, item, v, who: seen.append(("edit", (v, who))) or {})
    monkeypatch.setattr(actions, "refresh", lambda cfg, item: {"hold": True})
    return seen


def test_decide_dispatch(calls):
    actions.decide({}, ITEM, "approve", "me", text="nice")
    actions.decide({}, ITEM, "reopen", "me")
    actions.decide({}, ITEM, "edit", "me", values={"Tags": "a"})
    actions.decide({}, ITEM, "hold", "me")               # flips: it was held
    actions.decide({}, ITEM, "hold", "me", on=True)
    assert calls == [("approve", ("nice", "me")), ("reopen", ("me",)),
                     ("edit", ({"Tags": "a"}, "me")), ("hold", (False, "me")), ("hold", (True, "me"))]
    with pytest.raises(ClipperError, match="unknown action"):
        actions.decide({}, ITEM, "publish", "me")


def test_replan_only_for_schedule_moves(monkeypatch):
    ran = []
    monkeypatch.setattr(schedule, "run", lambda cfg, sync_first: ran.append(sync_first))
    rows = iter([{"scheduled": "2026-11-03T16:00:00-08:00", "decision": "approved", "hold": False},
                 {"scheduled": "", "decision": "approved", "hold": True}])
    monkeypatch.setattr(actions, "describe", lambda cfg, sdir: {"k": next(rows)})
    cfg = {"discord": {"replan": True}}
    assert actions.replan(cfg, ITEM, "thumbtext", {"x": 1}) == ({"x": 1}, "")   # no replan
    assert actions.replan(cfg, ITEM, "approve", {})[1] == "scheduled 2026-11-03 16:00"
    assert actions.replan(cfg, ITEM, "hold", {})[1] == "held, not scheduled"
    assert ran == [False, False]
    assert actions.replan({"discord": {"replan": False}}, ITEM, "approve", {}) == ({}, "")
