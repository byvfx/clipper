import json
import subprocess
import time
from http.cookiejar import Cookie, CookieJar

import pytest

from clipper import ytdl
from clipper.errors import ClipperError


def fake_run(monkeypatch, stderr):
    calls = []

    def run(*args):
        calls.append(args)
        signed_in = args[:1] in (("--cookies",), ("--cookies-from-browser",))
        return subprocess.CompletedProcess(args, 0 if signed_in else 1, "", "" if signed_in else stderr)
    monkeypatch.setattr(ytdl, "run", run)
    return calls


def cfg_with_cookies(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return {"paths": {"youtube_cookies": cookies}}


def test_retries_signed_in_only_for_its_reason(monkeypatch, tmp_path):
    cfg = cfg_with_cookies(tmp_path)
    calls = fake_run(monkeypatch, "ERROR: Sign in to confirm your age")
    r, signed_in = ytdl.run_retrying(cfg, "URL", retry_on=ytdl.AGE)
    assert (r.returncode, signed_in, len(calls)) == (0, True, 2)

    # The bot check also says "Sign in to confirm": an archive run must not go signed in.
    calls = fake_run(monkeypatch, "ERROR: Sign in to confirm you're not a bot")
    r, signed_in = ytdl.run_retrying(cfg, "URL", retry_on=ytdl.AGE)
    assert (r.returncode, signed_in, len(calls)) == (1, False, 1)
    _, signed_in = ytdl.run_retrying(cfg, "URL", retry_on=ytdl.BOT)
    assert signed_in


def test_no_retry_without_a_cookies_file(monkeypatch, tmp_path):
    calls = fake_run(monkeypatch, "ERROR: age-restricted")
    r, signed_in = ytdl.run_retrying({"paths": {"youtube_cookies": tmp_path / "none.txt"}}, "URL",
                                     retry_on=ytdl.AGE)
    assert (r.returncode, signed_in, len(calls)) == (1, False, 1)
    assert ytdl.last_error(r) == "ERROR: age-restricted"


def test_json_cookie_export_becomes_netscape(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps({"cookies": [{"domain": ".youtube.com", "path": "/", "secure": True,
                                          "expirationDate": 1.9e9, "name": "SID", "value": "x"}]}),
                 encoding="utf-8")
    out = ytdl.netscape_cookies(p)
    assert out.read_text(encoding="utf-8").splitlines()[1] == \
        ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tx"
    txt = tmp_path / "plain.txt"
    txt.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert ytdl.netscape_cookies(txt) == txt


def test_newest_browser_export_beside_the_cookies_file_wins(tmp_path):
    import os

    p = tmp_path / "youtube_cookies.txt"
    cfg = {"paths": {"youtube_cookies": p}}
    assert ytdl.cookies_file(cfg) == p  # nothing there yet
    p.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    old = tmp_path / "www.youtube.com_28-10-2026.json"
    new = tmp_path / "www.youtube.com_31-10-2026.json"
    old.write_text("[]", encoding="utf-8")
    new.write_text("[]", encoding="utf-8")
    for f, t in ((old, 1_000), (p, 2_000), (new, 3_000)):
        os.utime(f, (t, t))
    assert ytdl.cookies_file(cfg) == new
    os.utime(p, (4_000, 4_000))  # copied over youtube_cookies.txt after that
    assert ytdl.cookies_file(cfg) == p


def test_signed_in_retry_converts_an_export_to_one_fixed_file(monkeypatch, tmp_path):
    calls = fake_run(monkeypatch, "ERROR: Sign in to confirm you're not a bot")
    (tmp_path / "www.youtube.com_31-10-2026.json").write_text("[]", encoding="utf-8")
    cfg = {"paths": {"youtube_cookies": tmp_path / "youtube_cookies.txt"}}
    _, signed_in = ytdl.run_retrying(cfg, "URL", retry_on=ytdl.BOT)
    assert signed_in and calls[1][1] == str(tmp_path / "youtube_cookies.netscape.txt")


def test_a_browser_profile_is_read_live_instead_of_the_cookies_file(monkeypatch, tmp_path):
    calls = fake_run(monkeypatch, "ERROR: Sign in to confirm you're not a bot")
    cfg = {"paths": {"youtube_cookies": tmp_path / "none.txt"}, "youtube": {"cookies_from_browser": "firefox:clipper"}}
    _, signed_in = ytdl.run_retrying(cfg, "URL", retry_on=ytdl.BOT)
    assert signed_in and calls[1] == ("--cookies-from-browser", "firefox:clipper", "URL")
    assert ytdl.signed_in_args({"paths": {"youtube_cookies": tmp_path / "none.txt"}}) == []


def cookie(name, domain=".youtube.com", expires=None):
    return Cookie(0, name, "v", None, False, domain, True, True, "/", True, True, expires, False,
                  None, None, {})


def test_browser_session_names_only_live_youtube_sign_in_cookies(monkeypatch):
    seen = []

    def extract(name, profile, logger, keyring=None, container=None):
        seen.append((name, profile, keyring, container))
        jar = CookieJar()
        for c in (cookie("SID"), cookie("__Secure-3PSID", expires=int(time.time()) + 60),
                  cookie("__Secure-1PSID", expires=1), cookie("SID", domain=".google.com"), cookie("PREF")):
            jar.set_cookie(c)
        return jar
    import yt_dlp.cookies
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", extract)
    assert ytdl.browser_session("Firefox:clipper") == ["SID", "__Secure-3PSID"]
    assert seen == [("firefox", "clipper", None, None)]


def test_browser_session_errors_name_the_setting(monkeypatch):
    def extract(*a, **k):
        raise FileNotFoundError("could not find firefox cookies database")
    import yt_dlp.cookies
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", extract)
    with pytest.raises(ClipperError, match="cookies_from_browser 'firefox:nope': could not find"):
        ytdl.browser_session("firefox:nope")
    with pytest.raises(ClipperError, match="not BROWSER"):
        ytdl.browser_session(":x")


def fake_profile(monkeypatch, *cookies):
    def extract(*a, **k):
        jar = CookieJar()
        for c in cookies:
            jar.set_cookie(c)
        return jar
    import yt_dlp.cookies
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", extract)


def test_export_writes_only_youtube_cookies_as_netscape(monkeypatch, tmp_path):
    fake_profile(monkeypatch, cookie("SID"), cookie("PREF", expires=2000000000),
                 cookie("SID", domain=".google.com"))
    out = tmp_path / "youtube_cookies.txt"
    assert ytdl.export_browser_cookies("firefox:clipper", out) == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert sorted(lines[1:]) == [".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tv",
                                 ".youtube.com\tTRUE\t/\tTRUE\t2000000000\tPREF\tv"]
    assert not (tmp_path / "youtube_cookies.txt.tmp").exists()


def test_export_keeps_the_old_file_when_the_profile_is_signed_out(monkeypatch, tmp_path):
    fake_profile(monkeypatch, cookie("PREF"), cookie("SID", expires=1))
    out = tmp_path / "youtube_cookies.txt"
    out.write_text("old", encoding="utf-8")
    with pytest.raises(ClipperError, match="isn't signed in"):
        ytdl.export_browser_cookies("firefox:clipper", out)
    assert out.read_text(encoding="utf-8") == "old"
