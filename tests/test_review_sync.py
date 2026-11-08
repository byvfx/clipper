"""review.sync: Kitsu decisions and columns into review.json (a fake gazu; offline)."""
from clipper import jsonstate, review

STATUSES = [{"id": "st-done", "short_name": "done"}, {"id": "st-retake", "short_name": "retake"},
            {"id": "st-todo", "short_name": "todo"}]
TASKS = {"t1": {"task_status_id": "st-done", "updated_at": "2026-11-03T10:00:00"},
         "t2": {"task_status_id": "st-retake", "updated_at": "2026-11-03T11:00:00"}}
DATA = {"e1": {"title": "New title"}, "e2": {"title": "", "thumb": "NEW WORDS"}}
STATE = {"s01": {"kind": "segment", "id": "s01", "file": "a.mp4", "task_id": "t1", "entity_id": "e1",
                 "fields": {"YouTube title": "Old title"}},
         "v02": {"kind": "short", "id": "v02", "file": "b.mp4", "task_id": "t2", "entity_id": "e2",
                 "fields": {}}}


class FakeTask:
    @staticmethod
    def all_task_statuses():
        return STATUSES

    @staticmethod
    def get_task(tid):
        return TASKS[tid]


class FakeProject:
    @staticmethod
    def get_project_by_name(name):
        return {"id": "p", "name": name}


class FakeGazu:
    task = FakeTask
    project = FakeProject


def fake_kitsu(monkeypatch, thumb_edits: list):
    monkeypatch.setattr(review, "for_stream", lambda cfg, sdir: cfg)
    monkeypatch.setattr(review, "connect", lambda rc: FakeGazu)
    monkeypatch.setattr(review, "column_fields", lambda g, p: {
        ("Sequence", "YouTube title"): "title", ("Shot", "YouTube title"): "title",
        ("Shot", review.THUMB_COL): "thumb"})
    monkeypatch.setattr(review, "entity_data", lambda g, et, eid: DATA[eid])
    monkeypatch.setattr(review, "decision_comment_entry",
                        lambda g, task: ("c9", "trim the start") if task["task_status_id"] == "st-retake" else ("", ""))
    monkeypatch.setattr(review, "apply_thumbnail_text",
                        lambda sdir, cfg, g, fmap, edits: thumb_edits.append(sorted(edits)))


def stream(tmp_path, state=STATE):
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    jsonstate.write(sdir / "review_state.json", state)
    return sdir


def test_sync_writes_decisions_comments_and_edited_columns(defaults, tmp_path, monkeypatch):
    thumbs: list = []
    fake_kitsu(monkeypatch, thumbs)
    out = review.sync(stream(tmp_path), defaults, quiet=True)
    assert out["s01"]["decision"] == "approved" and out["s01"]["edited"] == ["YouTube title"]
    assert out["v02"]["decision"] == "changes_requested"
    assert out["v02"]["comment"] == "trim the start" and out["v02"]["comment_id"] == "c9"
    assert jsonstate.read(tmp_path / "kick-1" / "review.json") == out
    assert thumbs == [["v02"]]  # thumbnail words typed in Kitsu get re-rendered


def test_sync_of_some_keys_keeps_the_rest_as_they_were(defaults, tmp_path, monkeypatch):
    """The bot and the war room sync single items; the others mustn't be rewritten from a
    half-read of Kitsu."""
    fake_kitsu(monkeypatch, [])
    sdir = stream(tmp_path)
    jsonstate.write(sdir / "review.json", {"s01": {"decision": "rejected", "kind": "segment"}})
    out = review.sync(sdir, defaults, keys=["v02"], quiet=True)
    assert out["s01"] == {"decision": "rejected", "kind": "segment"}
    assert out["v02"]["decision"] == "changes_requested"
    assert list(out) == ["s01", "v02"]  # review_state order


def test_an_item_no_longer_published_drops_out(defaults, tmp_path, monkeypatch):
    fake_kitsu(monkeypatch, [])
    sdir = stream(tmp_path, {"v02": STATE["v02"]})
    jsonstate.write(sdir / "review.json", {"s01": {"decision": "approved"}, "v02": {}})
    assert list(review.sync(sdir, defaults, quiet=True)) == ["v02"]


def test_sync_reads_kitsu_into_review_json_and_keeps_a_newer_local_decision(defaults, tmp_path, monkeypatch):
    """The record comes back as decisions, comments and edited columns; an item a button decided
    while the whole-stream sync was reading Kitsu keeps its newer local entry."""
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    jsonstate.write(sdir / "review_state.json", {
        "c01": {"kind": "pick", "id": "t01", "file": "clips/01.mp4", "task_id": "T1", "entity_id": "E1",
                "fields": {"YouTube title": "Old"}},
        "s02": {"kind": "segment", "id": "s02", "file": "segments/s02.mp4", "task_id": "T2", "entity_id": "E2",
                "fields": {}}})
    local = {"s02": {"kind": "segment", "decision": "approved", "status": "approved",
                     "updated_at": "2026-11-08T12:00:00", "fields": {}}}

    class Gazu:
        class task:
            @staticmethod
            def all_task_statuses():
                return [{"id": "S1", "short_name": "retake"}, {"id": "S2", "short_name": "wfa"}]

            @staticmethod
            def get_task(tid):
                if tid == "T1":   # a button decides s02 while this sync is still reading Kitsu
                    jsonstate.write(sdir / "review.json", local)
                return {"T1": {"task_status_id": "S1", "updated_at": "2026-11-08T10:00:00"},
                        "T2": {"task_status_id": "S2", "updated_at": "2026-11-08T09:00:00"}}[tid]

        class project:
            @staticmethod
            def get_project_by_name(name):
                return {"id": "P"}

    monkeypatch.setattr(review, "for_stream", lambda cfg, d: cfg)
    monkeypatch.setattr(review, "connect", lambda rc: Gazu)
    monkeypatch.setattr(review, "column_fields", lambda gazu, proj: {("Shot", "YouTube title"): "data_title",
                                                                      ("Sequence", "Hold"): "data_hold"})
    monkeypatch.setattr(review, "entity_data", lambda gazu, etype, eid: {"E1": {"data_title": "New"},
                                                                          "E2": {"data_hold": "true"}}[eid])
    monkeypatch.setattr(review, "decision_comment_entry", lambda gazu, task: ("C9", "trim the end"))
    monkeypatch.setattr(review, "apply_thumbnail_text", lambda *a: None)
    out = review.sync(sdir, defaults, quiet=True)
    c01 = out["c01"]
    assert (c01["decision"], c01["status"], c01["comment"], c01["comment_id"]) == ("changes_requested", "retake", "trim the end", "C9")
    assert c01["fields"] == {"YouTube title": "New"} and c01["edited"] == ["YouTube title"]
    assert out["s02"]["updated_at"] == "2026-11-08T12:00:00" and out["s02"]["decision"] == "approved"
    assert list(out) == ["c01", "s02"] and jsonstate.read(sdir / "review.json", {}) == out
