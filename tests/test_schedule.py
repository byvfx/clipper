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
    # Back to what the last sync read: review.json says so, but the push changed it since.
    assert schedule.needs_push("", "", last, "e1")
    # A sync read something else there after that push (typed in Kitsu): put it right.
    assert schedule.needs_push("2026-11-03 16:00", "2026-11-09 10:00", last, "e1")
    assert not schedule.needs_push("2026-11-03 16:00", "2026-11-03 16:00", last, "e1")


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
    del placed["s/a"]   # unscheduled again before any sync: review.json still shows ""
    schedule.push_to_kitsu(cfg, items, placed, TZ)
    assert calls == [("get", "e1"), ("put", "e1", {"f_sched": ""})]
    calls.clear()
    schedule.push_to_kitsu(cfg, items, placed, TZ, every=True)
    assert calls == [("get", "e1"), ("get", "e2"), ("get", "e3")]


def test_budget_scales_with_length(defaults):
    ssc = {**defaults["schedule"], "stream_budget": {"segment": 10, "short": 8}, "budget_hours": 8}
    assert schedule.budget_for(ssc, 8.2) == {"segment": 10, "short": 8}
    assert schedule.budget_for(ssc, 5.5) == {"segment": 7, "short": 6}
    assert schedule.budget_for(ssc, 0.3) == {"segment": 1, "short": 1}
    assert schedule.budget_for(defaults["schedule"], 8) == {}
    ssc["stream_budget"] = {"segment": 6, "compilation": 0}
    assert schedule.budget_for(ssc, 5.5) == {"segment": 4, "compilation": 0}  # 0 = none, not 1


def test_budget_keeps_the_best(sc):
    sc = {**sc, "short_times": ["10:00", "15:00", "20:00"]}
    old = {"s/up": {"key": "s/up", "stream": "s", "kind": "short", "id": "s/up", "title": "",
                    "file": "", "publish_at": datetime(2026, 11, 3, 10, 0, tzinfo=TZ).isoformat(),
                    "source": "auto", "status": "uploaded"}}
    shorts = [item("s/up", "short", score=1, budget=3), item("s/low", "short", score=2, budget=3),
              item("s/mid", "short", score=5, budget=3), item("s/top", "short", score=9, budget=3)]
    placed, skipped, _ = plan(shorts, sc, old)
    assert set(placed) == {"s/up", "s/top", "s/mid"}       # the uploaded one counts first
    assert skipped == {"s/low": "over the stream's short budget (3)"}


def test_stale_items_drop_out(sc):
    soon = NOW + timedelta(days=1, hours=8)                  # Tue 17:00
    old = {"s/far": {"key": "s/far", "stream": "s", "kind": "segment", "id": "s/far", "title": "",
                     "file": "", "publish_at": datetime(2026, 11, 5, 16, 0, tzinfo=TZ).isoformat(),
                     "source": "auto", "status": "scheduled"}}
    items = [item("s/far", expires=soon), item("s/next", expires=soon),
             item("s/gone", expires=NOW - timedelta(days=1))]
    _, skipped, when = plan(items, sc, old)
    assert when == {"s/far": "Tue 16:00"}                  # its Thursday slot was past expiry
    assert skipped["s/next"].startswith("stale: no free segment slot by 11-03 17:00")
    assert skipped["s/gone"].startswith("stale:")


def test_compilation_skips_segments_that_wont_go(sc):
    sc = {**sc, "horizon_days": 5, "compilation_days": ["sat"]}
    items = [item("s/a", score=5, budget=1), item("s/b", score=1, budget=1),
             item("s/comp", "compilation", segment_keys=["s/a", "s/b"])]
    _, skipped, when = plan(items, sc)
    assert when["s/comp"] == "Sat 14:00"
    assert skipped == {"s/b": "over the stream's segment budget (1)"}


def test_a_budget_of_0_lets_only_a_pinned_one_go(sc):
    sc = {**sc, "horizon_days": 5, "compilation_days": ["sat"]}
    items = [item("s/a"), item("s/comp", "compilation", segment_keys=["s/a"], budget=0),
             item("t/comp", "compilation", segment_keys=[], budget=0, override_text="2026-11-07 14:00")]
    _, skipped, when = plan(items, sc)
    assert skipped == {"s/comp": "over the stream's compilation budget (0)"}
    assert when["t/comp"] == "Sat 14:00"


def test_a_pin_that_has_passed_still_goes_at_budget_0(sc):
    sc = {**sc, "horizon_days": 5, "compilation_days": ["sat"]}
    items = [item("s/comp", "compilation", segment_keys=[], budget=0, override_text="2026-11-01 14:00")]
    placed, skipped, when = plan(items, sc)
    assert skipped == {}
    assert when["s/comp"] == "Sat 14:00" and placed["s/comp"]["source"] == "auto"


def test_entry_files_are_stream_relative(tmp_path):
    # Entries from before 2026-11-05 hold the laptop's UNC path; every machine maps them onto
    # its own streams root by the part from the stream's folder on (#9).
    cfg = {"paths": {"streams": tmp_path}}
    old = {"stream": "kick-1", "file": r"\\192.168.0.155\docker\clipper\streams\kick-1\shorts\v01_x.mp4"}
    assert schedule.stream_file(old) == "kick-1/shorts/v01_x.mp4"
    assert schedule.stream_file({"stream": "kick-1", "file": "/data/streams/kick-1/clips/01.mp4"}) == "kick-1/clips/01.mp4"
    new = {"stream": "kick-1", "file": "kick-1/segments/s02_x.mp4"}
    assert schedule.stream_file(new) == new["file"]
    assert schedule.video(cfg, old) == tmp_path / "kick-1" / "shorts" / "v01_x.mp4"
    assert schedule.video(cfg, new) == tmp_path / "kick-1" / "segments" / "s02_x.mp4"
    # a path with no stream folder in it is left as it is
    assert schedule.video(cfg, {"stream": "kick-2", "file": "/elsewhere/a.mp4"}).as_posix() == "/elsewhere/a.mp4"


def test_previous_rewrites_old_paths(tmp_path):
    from clipper import jsonstate
    path = tmp_path / "schedule.json"
    jsonstate.write(path, {"entries": [{"key": "kick-1/v01", "stream": "kick-1", "status": "uploaded",
                                        "file": "//nas/streams/kick-1/shorts/v01.mp4"}]})
    old, _ = schedule.previous(path)
    assert old["kick-1/v01"]["file"] == "kick-1/shorts/v01.mp4"


def test_next_free_keeps_the_rules(sc):
    sc = {**sc, "segment_times": ["10:00", "16:00"], "min_gap_minutes": 90}
    at = lambda d, h, m=0: datetime(2026, 11, d, h, m, tzinfo=TZ)
    nxt = lambda taken, after=NOW: schedule.next_free("segment", taken, sc, TZ, after)
    assert nxt([]) == at(2, 10)                          # Monday's 10:00, an hour from NOW
    assert nxt([], at(2, 10)) == at(2, 16)               # strictly after `after`
    assert nxt([at(2, 10)]) == at(2, 16)                 # taken
    assert nxt([at(2, 9, 0)]) == at(2, 16)               # 10:00 is within 90 min of 9:00
    assert nxt([at(2, 10), at(2, 16)]) == at(3, 10)
    assert nxt([at(2, 12)], at(2, 9)) == at(2, 10)       # 2 h apart is fine
    assert schedule.next_free("segment", [at(2, 12)], {**sc, "max_per_day": 1}, TZ, NOW) == at(3, 10)
    assert schedule.next_free("short", [], {**sc, "short_days": []}, TZ, NOW) is None
    full = [at(d, h) for d in (2, 3, 4) for h in (10, 16)]
    assert nxt(full) is None                             # nothing in horizon_days (3)


def test_release_window(defaults, tmp_path):
    meta = {"started_at": "2026-11-01T18:00:00-08:00", "duration_s": 7200}
    sc = defaults["schedule"]
    end = datetime.fromisoformat("2026-11-01T20:00:00-08:00")
    earliest = end + timedelta(hours=sc["kick_first_hours"])
    assert schedule.release_window(defaults, tmp_path, meta) == (
        earliest, earliest + timedelta(days=sc["stale_days"]) if sc["stale_days"] else None)
    started = datetime.fromisoformat(meta["started_at"])
    defaults["preshow"]["publish"] = "asap"
    assert schedule.release_window(defaults, tmp_path, {**meta, "kind": "preshow"}) == (started, None)
    defaults["schedule"]["stale_days"] = 2
    assert schedule.release_window(defaults, tmp_path, meta)[1] == earliest + timedelta(days=2)
