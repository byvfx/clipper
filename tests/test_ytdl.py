import json
import subprocess

from clipper import ytdl


def fake_run(monkeypatch, stderr):
    calls = []

    def run(*args):
        calls.append(args)
        signed_in = args[:1] == ("--cookies",)
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
