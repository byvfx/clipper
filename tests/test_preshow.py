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
