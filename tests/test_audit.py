"""`clipper audit`: the weekly digest and the brief the monthly deep audit reads."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from clipper import audit, jsonstate
from clipper.errors import ClipperError

NOW = datetime(2026, 11, 15, 16, 30, tzinfo=UTC)
TZ = ZoneInfo("UTC")


def iso(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


@pytest.fixture
def cfg(defaults, tmp_path, monkeypatch):
    defaults["paths"]["streams"] = tmp_path / "streams"
    defaults["paths"]["schedule"] = tmp_path / "schedule.json"
    defaults["paths"]["costs"] = tmp_path / "costs.jsonl"
    defaults["paths"]["modal_runs"] = tmp_path / "modal.jsonl"
    defaults["schedule"]["timezone"] = "UTC"
    defaults["paths"]["streams"].mkdir()
    monkeypatch.setitem(audit.SECTIONS, "repo", lambda c, n, d: {"open": 1, "stale": [], "branches": 2})
    monkeypatch.setitem(audit.SECTIONS, "checkup", lambda c, n, d: [])
    return defaults


def stream(cfg, name, run=None, review=None):
    d = cfg["paths"]["streams"] / name
    d.mkdir()
    if run is not None:
        jsonstate.write(d / "run.json", run)
    if review is not None:
        jsonstate.write(d / "review.json", review)


def failed(stage="cut", error="cut exited 1: a | ffmpeg died at frame 123"):
    return {"status": "failed", "updated": iso(1), "error": error, "stages": {stage: {"status": "failed"}}}


def test_runs_counts_only_the_week(cfg):
    stream(cfg, "kick-1", {"status": "done", "finished": iso(2)})
    stream(cfg, "kick-2", {"status": "done", "finished": iso(30)})
    stream(cfg, "kick-3", {"status": "partial", "updated": iso(3)})
    stream(cfg, "kick-4", failed())
    got = audit.check_runs(cfg, NOW, 7)
    assert (got["done"], got["partial"]) == (1, 1)
    assert [(r["stream"], r["stage"]) for r in got["failed"]] == [("kick-4", "cut")]


def test_the_same_failure_on_two_streams_is_one_pattern(cfg):
    stream(cfg, "kick-1", failed())
    stream(cfg, "kick-2", failed(error="cut exited 1: x | ffmpeg died at frame 99999"))
    stream(cfg, "kick-3", failed(error="cut exited 1: x | no space left on device"))
    found = [i for i in audit.collect(cfg, NOW)["issues"] if i["key"].startswith("fail:")]
    assert len(found) == 3 and len({i["pattern"] for i in found}) == 2
    assert found[0]["pattern"] == "fail:*:cut:ffmpeg died at frame #"


def test_uploads_and_spend_in_the_window(cfg):
    jsonstate.write(cfg["paths"]["schedule"], {"entries": [
        {"key": "a", "kind": "short", "publish_at": iso(1), "status": "uploaded"},
        {"key": "b", "kind": "short", "publish_at": iso(2), "status": "uploaded"},
        {"key": "c", "kind": "segment", "publish_at": iso(3), "status": "scheduled"},
        {"key": "d", "kind": "short", "publish_at": iso(20), "status": "uploaded"}]})
    assert audit.check_uploads(cfg, NOW, 7) == {"uploaded": 2, "kinds": {"short": 2}, "missed": 1}
    cfg["paths"]["costs"].write_text(
        f'{{"ts": "{iso(1)}", "stage": "score", "usd": 2.5}}\n{{"ts": "{iso(2)}", "stage": "meta", "usd": 1}}\n'
        f'{{"ts": "{iso(40)}", "stage": "score", "usd": 99}}\n', encoding="utf-8")
    assert audit.check_spend(cfg, NOW, 7) == {"usd": 3.5, "stages": {"score": 2.5, "meta": 1.0}}


def test_reviews_keep_the_reasons_for_rejecting(cfg):
    stream(cfg, "kick-1", review={
        "s01": {"decision": "approved", "updated_at": iso(1)},
        "s02": {"decision": "rejected", "comment": "no payoff", "updated_at": iso(2)},
        "s03": {"decision": "pending", "updated_at": iso(1)},
        "s04": {"decision": "rejected", "comment": "old", "updated_at": iso(30)}})
    got = audit.check_reviews(cfg, NOW, 7)
    assert got["counts"] == {"approved": 1, "rejected": 1}
    assert got["rejected_because"] == [{"item": "kick-1/s02", "comment": "no payoff"}]


def test_backups_none_set_up_old_and_fresh(cfg, tmp_path):
    def todo():
        return [i["key"] for i in audit.collect(cfg, datetime.now(UTC))["issues"] if i["key"].startswith("backups")]

    cfg["backup"]["folder"] = ""
    assert todo() == ["backups:none"]
    (tmp_path / "backup").mkdir()
    cfg["backup"]["folder"] = str(tmp_path / "backup")
    assert todo() == ["backups:old"]              # an empty folder is not a backup
    (tmp_path / "backup" / "state.tar").write_text("x", encoding="utf-8")
    assert todo() == []


def test_reminders_show_from_a_week_before(cfg):
    cfg["audit"]["reminders"] = ["2026-11-16 compare v8", "2027-01-01 later", "no date here"]
    got = audit.check_reminders(cfg, NOW, 7)
    assert got[0] == "2026-11-16: compare v8" and len(got) == 2 and "no date first" in got[1]


def test_a_broken_section_is_a_thing_to_do_and_the_rest_still_run(cfg, monkeypatch):
    def boom(c, n, d):
        raise OSError("share gone")

    monkeypatch.setitem(audit.SECTIONS, "spend", boom)
    snap = audit.collect(cfg, NOW)
    assert snap["spend"] is None and snap["runs"] is not None
    assert any("spend section couldn't be read" in i["text"] for i in snap["issues"])
    assert "Spend:" not in audit.text(snap, TZ)


def test_a_failed_stream_is_listed_once(cfg, monkeypatch):
    stream(cfg, "kick-1", failed())
    monkeypatch.setitem(audit.SECTIONS, "checkup", lambda c, n, d: [
        {"key": "failed:kick-1", "level": "bad", "text": "kick-1: failed", "look": "", "stream": "kick-1"}])
    assert [i["key"][:5] for i in audit.collect(cfg, NOW)["issues"] if "kick-1" in i["key"]] == ["fail:"]


def test_a_missing_backup_outranks_sections_that_couldnt_be_read(cfg, monkeypatch):
    def boom(c, n, d):
        raise OSError("share gone")

    for name in ("repo", "best", "spend"):
        monkeypatch.setitem(audit.SECTIONS, name, boom)
    cfg["backup"]["folder"] = ""
    snap = audit.collect(cfg, NOW)
    assert snap["issues"][0]["key"] == "backups:none" and "nothing backs up" in audit.text(snap, TZ)


def test_a_section_that_couldnt_be_read_is_no_zero_in_the_brief(cfg):
    audit.folder(cfg).mkdir()
    jsonstate.write(audit.folder(cfg) / "weekly-2026-11-08.json", {
        "at": "2026-11-08T16:30:00+00:00", "days": 7, "errors": {}, "issues": [],
        "runs": None, "uploads": None, "spend": None, "reviews": None, "disk": {"free_gb": 9}})
    assert "| 2026-11-08 | ? | ? | ? | ? | ? | ? | 9 |" in audit.brief(cfg, now=NOW)


def test_the_digest_shows_three_and_says_how_many_more(cfg):
    for n in range(5):
        stream(cfg, f"kick-{n}", failed(error=f"cut exited 1: x | problem {'abcde'[n]}"))
    snap = audit.collect(cfg, NOW)
    short = audit.text(snap, TZ)
    assert "\n3. " in short and "\n4. " not in short and "more: clipper audit --all" in short
    assert len(short) <= audit.DM_MAX
    assert "\n4. " in audit.text(snap, TZ, limit=0) and "look: " in audit.text(snap, TZ, limit=0)
    assert "Repo: 1 open PRs, 2 branches" in short


def week(at: str, patterns: list[str], stream_id: str = "") -> dict:
    return {"at": at, "days": 7, "errors": {}, "runs": {"done": 1, "partial": 0, "failed": []},
            "issues": [{"key": p, "pattern": p, "level": "bad", "text": f"{p} now", "look": "", "stream": stream_id}
                       for p in patterns]}


def test_recurring_ranks_what_keeps_coming_back():
    got = audit.recurring([week("a", ["x", "y"], "kick-1"), week("b", ["x", "x"], "kick-2"), week("c", ["z"])])
    assert [(r["pattern"], r["weeks"], r["of"]) for r in got] == [("x", 2, 3), ("y", 1, 3), ("z", 1, 3)]
    assert got[0]["streams"] == ["kick-1", "kick-2"]


def test_save_and_brief_with_last_months_audit(cfg, tmp_path):
    assert audit.brief(cfg, now=NOW) == ""        # nothing saved: the script says so, no model run
    snap = audit.collect(cfg, NOW)
    path = audit.save(cfg, snap, TZ)
    assert path.name == "weekly-2026-11-15.json" and jsonstate.load(path)["at"] == snap["at"]
    for i, at in enumerate(("2026-11-01", "2026-11-08")):
        jsonstate.write(audit.folder(cfg) / f"weekly-{at}.json", week(f"{at}T16:30:00+00:00", ["x"] if i else ["y"]))
    old = tmp_path / "old.md"
    old.write_text("fix the cut retries\n", encoding="utf-8")
    assert audit.keep_deep(cfg, old, datetime(2026, 10, 1, tzinfo=UTC)).name == "deep-2026-10.md"
    text = audit.brief(cfg, weeks=2, now=NOW)
    assert "2 weekly digests" in text and "weekly-2026-11-01" not in text
    assert "1 of 2 weeks, `x`" in text and "fix the cut retries" in text


def test_this_months_own_report_isnt_the_last_one(cfg, tmp_path):
    audit.folder(cfg).mkdir()
    jsonstate.write(audit.folder(cfg) / "weekly-2026-11-08.json", week("2026-11-08T16:30:00+00:00", []))
    f = tmp_path / "now.md"
    f.write_text("this month\n", encoding="utf-8")
    audit.keep_deep(cfg, f, NOW)
    assert "this month" not in audit.brief(cfg, now=NOW)


def test_an_empty_deep_report_is_refused(cfg, tmp_path):
    f = tmp_path / "empty.md"
    f.write_text(" \n", encoding="utf-8")
    with pytest.raises(ClipperError, match="is empty"):
        audit.keep_deep(cfg, f, NOW)


def test_the_container_ships_the_audit_files():
    here = Path(__file__).parent.parent / "deploy" / "claude"
    for name in ("Dockerfile", "deploy.sh"):
        body = (here / name).read_text(encoding="utf-8")
        assert "audit.sh" in body and "audit-deep.md" in body, name
    assert "clipper-audit" in (here / "entrypoint.sh").read_text(encoding="utf-8")
