"""clipper/actions.py: the row every card, list and panel is built from (describe), from the
local files alone, and the small formatters it uses."""
from clipper import actions, jsonstate, review


def test_other_voices_only_speaks_over_the_limit():
    assert actions.other_voices(22, 5) == "22 s of other voices (limit 5 s)"
    assert actions.other_voices(3, 5) == ""
    assert actions.other_voices(None, 5) == "" and actions.other_voices("n/a", 5) == ""


def test_describe_builds_a_row_from_the_local_files(defaults, tmp_path, monkeypatch):
    sdir = tmp_path / "kick-1"
    (sdir / "clips").mkdir(parents=True)
    state = {"c01_t20": {"kind": "pick", "id": "t20", "file": "clips/01_t20.mp4", "task_id": "T", "entity_id": "E",
                         "kitsu_url": "http://k/t", "episode_url": "http://k/e", "revision": 2,
                         "fields": {"YouTube title": "Proposed title", "Hook": "a hook", "Score": "8"}}}
    jsonstate.write(sdir / "review_state.json", state)
    jsonstate.write(sdir / "review.json", {"c01_t20": {
        "decision": "approved", "status": "approved", "comment": "", "updated_at": "2026-11-08T10:00:00",
        "fields": {"YouTube title": "Edited title", "Hold": "true", "Publish at": "2026-11-09 16:00"}}})
    jsonstate.write(sdir / "meta.json", {"started_at": "2026-11-07T20:00:00-08:00"})
    monkeypatch.setattr(actions, "for_stream", lambda cfg, d: cfg)
    monkeypatch.setattr(review, "ensure_links", lambda d, cfg, gazu=None: state)
    monkeypatch.setattr(review, "items", lambda d, include, cfg: [{"key": "c01_t20", "duration_s": 42.0}])
    monkeypatch.setattr(actions, "schedule_entries", lambda cfg: {
        "kick-1/c01_t20": {"publish_at": "2026-11-09T16:00:00-08:00", "status": "uploaded"}})
    monkeypatch.setattr(actions, "schedule_dropped", lambda cfg: {})

    row = actions.describe(defaults, sdir)["c01_t20"]
    assert row["title"] == "Edited title" and row["hook"] == "a hook"   # Kitsu's edit wins over the proposal
    assert row["decision"] == "approved" and row["hold"] is True and row["publish_at"] == "2026-11-09 16:00"
    assert row["scheduled"] == "2026-11-09T16:00:00-08:00" and row["uploaded"] is True and row["dropped"] == ""
    assert row["duration_s"] == 42.0 and row["date"] == "2026-11-07" and row["thumbnail"] is None
    assert row["ref"] == "kick-1/c01_t20" and row["short"] == "kick-1/c01" and row["revision"] == 2
    assert row["kitsu_url"] == "http://k/t" and row["synced_at"] == "2026-11-08T10:00:00"


def test_describe_defaults_for_an_item_nobody_decided_on(defaults, tmp_path, monkeypatch):
    sdir = tmp_path / "kick-2"
    sdir.mkdir()
    state = {"s02_topic": {"kind": "segment", "id": "s02", "file": "segments/s02_topic.mp4", "task_id": "T",
                           "entity_id": "E", "kitsu_url": "", "episode_url": "", "fields": {}}}
    jsonstate.write(sdir / "review_state.json", state)
    monkeypatch.setattr(actions, "for_stream", lambda cfg, d: cfg)
    monkeypatch.setattr(review, "ensure_links", lambda d, cfg, gazu=None: state)
    monkeypatch.setattr(review, "items", lambda d, include, cfg: [])
    monkeypatch.setattr(actions, "schedule_entries", lambda cfg: {})
    monkeypatch.setattr(actions, "schedule_dropped", lambda cfg: {"kick-2/s02_topic": "stale"})
    row = actions.describe(defaults, sdir)["s02_topic"]
    assert (row["title"], row["decision"], row["status"], row["hold"]) == ("s02_topic", "pending", "wfa", False)
    assert row["scheduled"] == "" and row["uploaded"] is False and row["dropped"] == "stale"
    assert row["duration_s"] is None and row["date"] == ""
