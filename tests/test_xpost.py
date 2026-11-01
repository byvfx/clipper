from datetime import UTC, datetime, timedelta

from clipper import cli, jsonstate, xpost

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
