from datetime import UTC, datetime, timedelta

import pytest

from clipper import cli, jsonstate, xpost
from clipper.errors import ClipperError

NOW = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)


def entry(key: str, kind: str = "short", status: str = "uploaded", at: str = "2026-11-01T09:00:00-08:00",
          x: dict | None = None) -> dict:
    e = {"key": f"kick-1/{key}", "stream": "kick-1", "kind": kind, "status": status, "publish_at": at,
         "file": f"{key}.mp4"}
    if x:
        e["platforms"] = {"x": x}
    return e


def test_post_text_drops_tags_before_cutting_the_title():
    assert xpost.post_text("Don on DSP", ["#a", "#b", "#c", "#a"], 280, 2) == "Don on DSP\n\n#a #b"
    assert xpost.post_text("x" * 20, ["#tag"], 22, 2) == "x" * 20
    cut = xpost.post_text("word " * 80, [], 280, 2)
    assert len(cut) == 280 and cut.endswith("…")


def test_post_text_takes_links_out():
    """X bills a post with a URL at $0.20, not $0.015."""
    assert xpost.post_text("DSP reacts to https://example.com/x?y=1 live", [], 280, 2) == "DSP reacts to live"
    assert xpost.post_text("see www.example.com", ["#a"], 280, 2) == "see\n\n#a"


def test_posts_and_live_claims_count_against_the_24h_allowance():
    sched = {"entries": [
        entry("v01_t01", x={"status": "posted", "posted_at": (NOW - timedelta(hours=2)).isoformat()}),
        entry("v02_t02", x={"status": "posted", "posted_at": (NOW - timedelta(hours=30)).isoformat()}),
        entry("v03_t03", x={"status": "failed", "failed_at": NOW.isoformat()}),
        entry("v04_t04", x={"status": "posting", "claimed_at": (NOW - timedelta(minutes=5)).isoformat()}),
        entry("v05_t05", x={"status": "posting", "claimed_at": (NOW - timedelta(hours=2)).isoformat()})]}  # died
    assert xpost.posted_last_24h(sched, NOW) == 2


def test_auto_waits_for_youtube_and_retries_failures():
    xc = {"kinds": ["short"], "after_youtube": True, "max_age_hours": 48}
    sched = {"entries": [
        entry("v05_t05", at="2026-11-01T11:00:00-08:00"),                 # 19:00 UTC, live
        entry("v04_t04", at="2026-11-01T10:00:00-08:00"),                 # live, earlier
        entry("v06_t06", at="2026-11-01T13:00:00-08:00"),                 # 21:00 UTC, not yet
        entry("v07_t07", status="scheduled"),                             # not on YouTube yet
        entry("v08", kind="segment"),
        entry("v09_t09", x={"status": "failed", "tries": 1}),             # tried again
        entry("v11_t11", x={"status": "failed", "tries": 3}),             # given up
        entry("v10_t10", at="2026-10-29T10:00:00-08:00")]}                # too old for --auto
    assert [e["key"] for e in xpost.due(sched, lambda e: xc, NOW)] == [
        "kick-1/v09_t09", "kick-1/v04_t04", "kick-1/v05_t05"]
    xc["after_youtube"] = False
    assert len(xpost.due(sched, lambda e: xc, NOW)) == 5
    xc["max_age_hours"] = 0
    assert len(xpost.due(sched, lambda e: xc, NOW)) == 6
    assert xpost.due(sched, lambda e: {**xc, "kinds": []}, NOW) == []   # a show that posts nothing


def test_by_default_x_takes_the_newest_stream_first_without_waiting_for_youtube():
    xc = {"kinds": ["short"], "after_youtube": False, "max_age_hours": 48}
    aired = {"kick-1": NOW - timedelta(hours=60), "kick-2": NOW - timedelta(hours=20),
             "kick-3": NOW - timedelta(hours=5)}
    sched = {"entries": [
        entry("v01_t01", status="scheduled", at="2026-11-02T09:00:00-08:00"),
        {**entry("v02_t02", status="scheduled", at="2026-11-03T09:00:00-08:00"), "stream": "kick-3"},
        {**entry("v03_t03", status="scheduled", at="2026-11-02T10:00:00-08:00"), "stream": "kick-2"},
        {**entry("v04_t04", status="scheduled", at="2026-11-02T11:00:00-08:00"), "stream": "kick-3"}]}
    got = xpost.due(sched, lambda e: xc, NOW, aired=lambda e: aired[e["stream"]])
    assert [e["key"].split("/")[1] for e in got] == ["v04_t04", "v02_t02", "v03_t03"]   # kick-1 aired 60 h ago


def test_hook_first_text_and_mentions():
    from clipper.review import bare_title

    cfg = {"meta": {"show_name": "Kino Casino", "title_format": "{SHOW} | {title}"}}
    assert bare_title(cfg, "KINO CASINO | Joe turned down $1,000") == "Joe turned down $1,000"
    assert bare_title(cfg, "Joe turned down $1,000") == "Joe turned down $1,000"
    assert bare_title({"meta": {}}, "KINO CASINO | x") == "KINO CASINO | x"
    assert bare_title(cfg, "KINO CASINO | -$1,000 in donations") == "-$1,000 in donations"   # hook kept whole
    assert xpost.post_text("Hook", ["#Kino", "#B"], 280, 1, ["Andywarski", "@PoweroftheTruth"]) == \
        "Hook\n\n#Kino @Andywarski @PoweroftheTruth"


def test_a_long_title_is_shortened_around_the_mentions():
    ats = ["@Andywarski", "@PoweroftheTruth"]
    text = xpost.post_text("x" * 80, ["#Kino"], 100, 1, ats)
    assert len(text) <= 100 and text.endswith("\n\n@Andywarski @PoweroftheTruth") and "…" in text
    assert xpost.post_text("x" * 80, [], 30, 1, ats) == "x" * 29 + "…"   # mentions alone would fill it


def test_auto_skips_uploads_no_longer_approved():
    xc = {"kinds": ["short"], "after_youtube": False, "max_age_hours": 0}
    sched = {"entries": [entry("v01_t01"), entry("v02_t02", status="scheduled")]}
    got = xpost.due(sched, lambda e: xc, NOW, approved=lambda e: e["key"].endswith("t02"))
    assert [e["key"] for e in got] == ["kick-1/v02_t02"]


def test_approval_reads_the_kitsu_decision_and_hold(tmp_path):
    sdir = tmp_path / "kick-1"
    sdir.mkdir()
    jsonstate.write(sdir / "review.json", {
        "v01_t01": {"decision": "approved", "fields": {}},
        "v02_t02": {"decision": "approved", "fields": {"Hold": "true"}},
        "v03_t03": {"decision": "rejected", "fields": {}}})
    ok = xpost.approved_of({"paths": {"streams": tmp_path}})
    assert [ok(entry(k)) for k in ("v01_t01", "v02_t02", "v03_t03", "v04_t04")] == [True, False, False, False]


def test_a_claim_stops_a_second_run_and_the_allowance(tmp_path):
    path = tmp_path / "schedule.json"
    jsonstate.write(path, {"entries": [entry("v01_t01"), entry("v02_t02"),
                                       entry("v03_t03", x={"status": "posted", "url": "u",
                                                           "posted_at": datetime.now(UTC).isoformat()})]})
    cfg = {"paths": {"schedule": path}, "x": {"max_per_24h": 2}}
    assert xpost.claim(cfg, "kick-1/v01_t01") == ""
    assert xpost.claim(cfg, "kick-1/v01_t01") == "being posted by another run"
    assert xpost.claim(cfg, "kick-1/v03_t03") == "already on X (u)"
    assert "max_per_24h" in xpost.claim(cfg, "kick-1/v02_t02")    # v03 posted + v01 claimed = 2


def test_a_refresh_keeps_the_refresh_token_if_none_comes_back():
    tok = xpost.save_token({"access_token": "a2", "expires_in": 7200}, {"refresh_token": "r1"})
    assert tok["refresh_token"] == "r1" and tok["expires_at"] > 0
    tok = xpost.save_token({"access_token": "a3", "expires_in": 7200, "refresh_token": "r2"}, tok)
    assert tok["refresh_token"] == "r2"


def test_command_line():
    a = cli.build_parser().parse_args(["x", "--items", "t20", "kick-1"])
    assert a.items == ["t20"] and a.stream == "kick-1" and a.fn is cli.cmd_x


def test_issue_thread_goes_four_pages_a_post_with_the_poll_last(tmp_path):
    pages = [tmp_path / f"{n:02d}_p.jpg" for n in range(1, 11)]
    issue = {"cover": {"person": "The Quartering"},
             "post": {"part1": "Issue 03 is out. Huck needed an MRI.\n#Kick #KinoCasino",
                      "part2": "Pages 6-10: AB's cousin hands out career advice.\n#Kick #DSP"},
             "back": {"poll": "WHO HAD THE WORST WEEK?",
                      "options": [{"name": "Jer"}, {"name": "Ethan Klein"}, {"person": "AB"}]}}
    thread = xpost.issue_thread(issue, pages, 280)
    assert [len(g) for _, g in thread] == [4, 4, 2]
    assert [t for t, _ in thread] == [
        "Issue 03 is out. Huck needed an MRI.\n\n#TheQuartering",
        "AB's cousin hands out career advice.",
        "Who had the worst week? Jer, Ethan Klein or AB. Reply with your pick."]
    issue["post"]["x"] = ["one", "two"]  # a hand edit wins; a post without text goes bare
    assert [t for t, _ in xpost.issue_thread(issue, pages, 280)] == ["one", "two", ""]


def test_issue_folder_finds_the_saturday_or_the_newest_issue(tmp_path):
    for d in ("2026-10-24", "2026-10-31"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "issue.json").write_text("{}")
    cfg = {"paths": {"weekly": tmp_path}}
    assert xpost.issue_folder(cfg, None).name == "2026-10-31"
    assert xpost.issue_folder(cfg, "2026-10-19").name == "2026-10-24"   # a Monday
    assert xpost.issue_folder(cfg, "2026-10-31").name == "2026-10-31"   # the Saturday itself


def test_an_issue_needs_all_ten_pages_once(tmp_path):
    for n in range(1, 10):                       # a render that stopped after page 09
        (tmp_path / f"{n:02d}_p.jpg").write_bytes(b"")
    with pytest.raises(ClipperError, match=r"missing \['10'\]"):
        xpost.issue_pages(tmp_path)
    (tmp_path / "10_back.jpg").write_bytes(b"")
    assert len(xpost.issue_pages(tmp_path)) == 10
    (tmp_path / "10_other.jpg").write_bytes(b"")
    with pytest.raises(ClipperError, match=r"extra \['10'\]"):
        xpost.issue_pages(tmp_path)


def test_x_len_counts_emoji_double():
    assert xpost.x_len("Issue 03 — out") == 14
    assert xpost.x_len("📰 x") == 4


def test_an_issue_claim_stops_a_second_run_and_counts_against_the_allowance(tmp_path):
    sched, weekly = tmp_path / "schedule.json", tmp_path / "weekly"
    here = weekly / "2026-10-31"
    here.mkdir(parents=True)
    jsonstate.write(sched, {"entries": [entry("v01_t01"), entry(
        "v03_t03", x={"status": "posted", "url": "u", "posted_at": datetime.now(UTC).isoformat()})]})
    cfg = {"paths": {"schedule": sched, "weekly": weekly}, "x": {"max_per_24h": 4}}
    assert xpost.claim_issue(cfg, here, 3) == ""
    assert xpost.claim_issue(cfg, here, 3) == "being posted by another run"
    assert xpost.used_last_24h(cfg, jsonstate.load(sched)) == 4         # 1 Short + 3 reserved
    assert "max_per_24h" in xpost.claim(cfg, "kick-1/v01_t01")
    jsonstate.write(here / "x.json", {"posts": [{"posted_at": datetime.now(UTC).isoformat()}] * 2})
    assert "3 posts needed, 1 of 4 left" in xpost.claim_issue(cfg, here, 3)
