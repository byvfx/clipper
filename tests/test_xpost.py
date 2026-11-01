from datetime import UTC, datetime, timedelta

from clipper import cli, xpost

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


def test_only_posts_in_the_last_24h_count():
    sched = {"entries": [
        entry("v01_t01", x={"status": "posted", "posted_at": (NOW - timedelta(hours=2)).isoformat()}),
        entry("v02_t02", x={"status": "posted", "posted_at": (NOW - timedelta(hours=30)).isoformat()}),
        entry("v03_t03", x={"status": "failed", "failed_at": NOW.isoformat()})]}
    assert xpost.posted_last_24h(sched, NOW) == 1


def test_auto_waits_for_youtube_and_skips_tried_items():
    xc = {"kinds": ["short"], "after_youtube": True, "max_age_hours": 48}
    sched = {"entries": [
        entry("v05_t05", at="2026-11-01T11:00:00-08:00"),                 # 19:00 UTC, live
        entry("v04_t04", at="2026-11-01T10:00:00-08:00"),                 # live, earlier
        entry("v06_t06", at="2026-11-01T13:00:00-08:00"),                 # 21:00 UTC, not yet
        entry("v07_t07", status="scheduled"),                             # not on YouTube yet
        entry("v08", kind="segment"),
        entry("v09_t09", x={"status": "failed"}),                         # --items retries it
        entry("v10_t10", at="2026-10-29T10:00:00-08:00")]}                # too old for --auto
    assert [e["key"] for e in xpost.due(sched, xc, NOW)] == ["kick-1/v04_t04", "kick-1/v05_t05"]
    xc["after_youtube"] = False
    assert len(xpost.due(sched, xc, NOW)) == 4
    xc["max_age_hours"] = 0
    assert len(xpost.due(sched, xc, NOW)) == 5


def test_a_refresh_keeps_the_refresh_token_if_none_comes_back():
    tok = xpost.save_token({"access_token": "a2", "expires_in": 7200}, {"refresh_token": "r1"})
    assert tok["refresh_token"] == "r1" and tok["expires_at"] > 0
    tok = xpost.save_token({"access_token": "a3", "expires_in": 7200, "refresh_token": "r2"}, tok)
    assert tok["refresh_token"] == "r2"


def test_command_line():
    a = cli.build_parser().parse_args(["x", "--items", "t20", "kick-1"])
    assert a.items == ["t20"] and a.stream == "kick-1" and a.fn is cli.cmd_x
