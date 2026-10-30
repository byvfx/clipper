from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clipper import schedule

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)        # a Monday
START = date(2026, 11, 3)                            # slots from Tuesday
LONG_AGO = NOW - timedelta(days=2)


@pytest.fixture
def sc(defaults):
    return {**defaults["schedule"], "horizon_days": 3}


def item(key, kind="segment", show="kino", started=LONG_AGO, **kw):
    return {"key": key, "stream": key.split("/")[0], "show": show, "kind": kind, "id": key,
            "file": f"{key}.mp4", "title": key, "score": 0, "stream_start": started,
            "earliest": started, "override_text": "", "asap": False, "priority": False, **kw}


def plan(items, sc, old=None, platforms=None):
    placed, skipped = schedule.plan(items, [], old or {}, platforms or {}, sc, TZ, NOW, START,
                                    reshuffle=False)
    when = {k: datetime.fromisoformat(e["publish_at"]).strftime("%a %H:%M")
            for k, e in placed.items()}
    return placed, {s["key"]: s["reason"] for s in skipped}, when


def test_shows_take_turns(sc):
    shorts = [item("k1/a", "short", started=LONG_AGO), item("k1/b", "short", started=LONG_AGO),
              item("k1/c", "short", started=LONG_AGO),
              item("c1/a", "short", show="c404", started=LONG_AGO + timedelta(hours=1))]
    _, skipped, when = plan(shorts, sc)
    assert when == {"k1/a": "Tue 10:00", "c1/a": "Wed 10:00", "k1/b": "Thu 10:00"}
    assert skipped == {"k1/c": "no free short slot in 3 days"}

    _, skipped, when = plan(shorts, {**sc, "mix_shows": False})
    assert when == {"k1/a": "Tue 10:00", "k1/b": "Wed 10:00", "k1/c": "Thu 10:00"}
    assert list(skipped) == ["c1/a"]


def test_pins(sc):
    items = [item("s/at", override_text="2026-11-04 13:00"),
             item("s/day", override_text="2026-11-05"),       # that day's first segment slot
             item("s/past", override_text="2026-11-01 12:00"),
             item("s/bad", override_text="next tuesday")]
    placed, skipped, when = plan(items, sc)
    assert when["s/at"] == "Wed 13:00" and placed["s/at"]["source"] == "pinned"
    assert when["s/day"] == "Thu 16:00"
    assert when["s/past"] == "Tue 16:00" and placed["s/past"]["source"] == "auto"
    assert skipped == {"s/bad": "bad Publish at 'next tuesday'"}


def test_asap_goes_out_now(sc):
    placed, _, _ = plan([item("yt/pre", asap=True)], sc)
    assert placed["yt/pre"]["source"] == "asap"
    assert placed["yt/pre"]["publish_at"] == NOW.isoformat()


def test_daily_cap_and_earliest(sc):
    # Same stream, so the segments queue by key: a_seg, then b_late.
    items = [item("s/short", "short"), item("s/a_seg"),
             item("s/b_late", earliest=datetime(2026, 11, 4, 0, 0, tzinfo=TZ))]
    _, _, when = plan(items, {**sc, "max_per_day": 1})
    assert when == {"s/short": "Tue 10:00", "s/a_seg": "Wed 16:00", "s/b_late": "Thu 16:00"}
    _, _, when = plan(items, sc)
    assert when == {"s/short": "Tue 10:00", "s/a_seg": "Tue 16:00", "s/b_late": "Wed 16:00"}


def test_compilation_after_its_segments(sc):
    sc = {**sc, "horizon_days": 5}   # through Saturday, the compilation day
    items = [item("s/a"), item("s/b"),
             item("s/comp", "compilation", segment_keys=["s/a", "s/b"]),
             item("s/comp2", "compilation", segment_keys=["s/a", "s/gone"])]
    _, skipped, when = plan(items, sc)
    assert when["s/comp"] == "Sat 14:00"
    assert skipped == {"s/comp2": "a segment has no slot yet"}


def test_uploaded_entries_stay(sc):
    old = {"s/up": {"key": "s/up", "stream": "s", "kind": "segment", "id": "s/up", "title": "",
                    "file": "", "publish_at": datetime(2026, 11, 3, 16, 0, tzinfo=TZ).isoformat(),
                    "source": "auto", "status": "uploaded", "video_id": "abc"}}
    placed, _, when = plan([item("s/new")], sc, old)
    assert placed["s/up"]["video_id"] == "abc"
    assert "no longer approved" in placed["s/up"]["note"]
    assert when["s/new"] == "Wed 16:00"   # Tuesday's slot is the uploaded one's


def test_needs_push():
    last = {"entity_id": "e1", "value": "2026-11-03 16:00", "over": ""}
    assert not schedule.needs_push("2026-11-03 16:00", "2026-11-03 16:00", None, "e1")  # synced so
    assert schedule.needs_push("2026-11-03 16:00", "", None, "e1")        # new slot
    assert schedule.needs_push("", None, None, "e1")                      # never read
    # Written by the last push, and no sync has seen the column since: no call.
    assert not schedule.needs_push("2026-11-03 16:00", "", last, "e1")
    assert schedule.needs_push("2026-11-04 16:00", "", last, "e1")        # the slot moved again
    assert schedule.needs_push("2026-11-03 16:00", "", last, "e2")        # another entity
    # A sync read something else there after that push (typed in Kitsu): put it right.
    assert schedule.needs_push("2026-11-03 16:00", "2026-11-09 10:00", last, "e1")


def test_push_to_kitsu_writes_only_changed_slots(defaults, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from clipper import jsonstate, review

    data = {"e1": {"f_sched": ""}, "e2": {"f_sched": "2026-11-03 16:00"}, "e3": {"f_sched": "x"}}
    calls = []
    shot = SimpleNamespace(
        get_shot=lambda eid: calls.append(("get", eid)) or {"data": dict(data[eid])},
        update_shot_data=lambda eid, d: calls.append(("put", eid, d)) or data[eid].update(d))
    gazu = SimpleNamespace(shot=shot, project=SimpleNamespace(get_project_by_name=lambda n: {}))
    monkeypatch.setattr(review, "connect", lambda rc: gazu)
    monkeypatch.setattr(review, "column_fields", lambda g, p: {("Shot", "Scheduled"): "f_sched"})
    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path / "streams",
                                 "schedule": tmp_path / "schedule.json"}}
    sdir = tmp_path / "streams" / "s"
    sdir.mkdir(parents=True)
    jsonstate.write(sdir / "review_state.json", {
        k: {"kind": "pick", "entity_id": e} for k, e in (("a", "e1"), ("b", "e2"), ("c", "e3"))})
    jsonstate.write(sdir / "review.json", {
        "a": {"fields": {"Scheduled": None}}, "b": {"fields": {"Scheduled": "2026-11-03 16:00"}},
        "c": {"fields": {"Scheduled": ""}}})   # c was typed into Kitsu after the last sync
    items = [{"key": f"s/{k}", "entity": ("Shot", e)} for k, e in (("a", "e1"), ("b", "e2"))]
    placed = {"s/a": {"publish_at": "2026-11-04T16:00:00-08:00"},
              "s/b": {"publish_at": "2026-11-03T16:00:00-08:00"}}

    schedule.push_to_kitsu(cfg, items, placed, TZ)
    assert calls == [("get", "e1"), ("put", "e1", {"f_sched": "2026-11-04 16:00"})]
    calls.clear()
    schedule.push_to_kitsu(cfg, items, placed, TZ)   # review.json not synced since: no calls
    assert calls == []
    jsonstate.write(sdir / "review.json", {**jsonstate.load(sdir / "review.json"),
                                           "c": {"fields": {"Scheduled": "x"}}})  # a sync saw it
    schedule.push_to_kitsu(cfg, items, placed, TZ)
    assert calls == [("get", "e3"), ("put", "e3", {"f_sched": ""})]
    calls.clear()
    schedule.push_to_kitsu(cfg, items, placed, TZ, every=True)
    assert calls == [("get", "e1"), ("get", "e2"), ("get", "e3")]
