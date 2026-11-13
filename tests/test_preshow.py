import json
from pathlib import Path

from clipper import preshow


def run_check(monkeypatch, error, browser=""):
    sent = []
    entry = {"video_id": "HjJ-PYtBF1k", "title": "KINO CASINO Preshow", "url": "https://youtu.be/HjJ-PYtBF1k"}
    monkeypatch.setattr(preshow, "new_entries", lambda cfg: [("kino", entry)])

    def fetch(cfg, url, key):
        raise RuntimeError(error)
    monkeypatch.setattr(preshow, "fetch", fetch)
    monkeypatch.setattr(preshow.alerts, "send", lambda cfg, kind, text, key="": sent.append((kind, key, text)))
    cfg = {"paths": {"youtube_cookies": Path("secrets/youtube_cookies.txt")},
           "youtube": {"cookies_from_browser": browser}}
    assert preshow.check(cfg) == []
    return sent


def test_bot_check_on_a_preshow_sends_one_keyed_dm(monkeypatch):
    sent = run_check(monkeypatch, "yt-dlp: ERROR: [youtube] HjJ-PYtBF1k: Sign in to confirm you're not a bot.")
    assert [(k, key) for k, key, _ in sent] == [("failure", "preshow-bot:HjJ-PYtBF1k")]
    assert "www.youtube.com_<date>.json" in sent[0][2] and "youtube_cookies.txt" in sent[0][2]


def test_bot_check_with_a_browser_profile_says_to_open_it(monkeypatch):
    sent = run_check(monkeypatch, "yt-dlp: ERROR: Sign in to confirm you're not a bot.", browser="firefox:clipper")
    assert len(sent) == 1 and "(firefox:clipper): open that profile" in sent[0][2]
    assert "youtube_cookies.txt" not in sent[0][2]


def test_other_grab_failures_stay_in_the_log(monkeypatch):
    assert run_check(monkeypatch, "yt-dlp: ERROR: Premieres in 2 hours") == []


def test_undated_entries_below_a_grabbed_preshow_are_old(monkeypatch):
    def e(vid, title, published=""):
        return {"video_id": vid, "title": title, "published": published, "url": f"https://youtu.be/{vid}"}
    listing = [e("new", "Preshow 11/8"), e("ep", "Full Episode 11/6"), e("got", "Preshow 11/6"),
               e("aug", "Preshow 8/12")]
    looked_up = []

    def published_at(entry):
        looked_up.append(entry["video_id"])
        return preshow.datetime.now(preshow.UTC)
    monkeypatch.setattr(preshow, "watched", lambda cfg: [("kino", {
        "preshow": {"youtube_channel": "UC", "title_pattern": "(?i)pre.?show", "max_age_hours": 36},
        "kick": {"user_agent": "ua"}})])
    monkeypatch.setattr(preshow, "feed", lambda channel, ua: listing)
    monkeypatch.setattr(preshow, "grabbed", lambda cfg, vid: vid == "got")
    monkeypatch.setattr(preshow, "published_at", published_at)
    assert [x["video_id"] for _, x in preshow.new_entries({})] == ["new"]
    assert looked_up == ["new"]


def test_dated_entries_below_a_grabbed_preshow_still_count(monkeypatch):
    now = preshow.datetime.now(preshow.UTC).isoformat()
    listing = [{"video_id": "got", "title": "Preshow", "published": now, "url": ""},
               {"video_id": "missed", "title": "Preshow", "published": now, "url": ""}]
    monkeypatch.setattr(preshow, "watched", lambda cfg: [("kino", {
        "preshow": {"youtube_channel": "UC", "title_pattern": "(?i)pre.?show", "max_age_hours": 36},
        "kick": {"user_agent": "ua"}})])
    monkeypatch.setattr(preshow, "feed", lambda channel, ua: listing)
    monkeypatch.setattr(preshow, "grabbed", lambda cfg, vid: vid == "got")
    assert [x["video_id"] for _, x in preshow.new_entries({})] == ["missed"]


def test_fetch_asks_for_an_mp4_even_from_a_single_file_format(defaults, tmp_path, monkeypatch):
    # the format's last fallback (b) can be one webm: remuxed, it still lands as source.mp4
    from clipper import preshow

    defaults["paths"]["streams"] = tmp_path
    defaults["show"]["channel"] = "kino"
    monkeypatch.setattr(preshow, "for_show", lambda cfg, key: defaults)
    calls = []

    def run_retrying(cfg, *args, retry_on):
        calls.append(args)

        class R:
            returncode, stderr = 0, ""
            stdout = json.dumps({"id": "abcdefghijk", "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
                                 "timestamp": 1762819200, "duration": 60, "title": "t"})
        if "-o" in args:
            (tmp_path / "yt-abcdefghijk" / "source.mp4").write_bytes(b"v")
        return R(), False

    monkeypatch.setattr(preshow.ytdl, "run_retrying", run_retrying)
    preshow.fetch(defaults, "https://www.youtube.com/watch?v=abcdefghijk", "kino")
    assert "--remux-video" in calls[1] and calls[1][calls[1].index("--remux-video") + 1] == "mp4"
