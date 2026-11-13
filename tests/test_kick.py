"""Kick: a VOD download's missing segments are counted, and a web page where the API's JSON
should be is a clear error. Offline: yt-dlp and Kick are stand-ins."""
import argparse
import io
import json
import subprocess

import pytest

from clipper import cli, kick
from clipper.errors import ClipperError


class FakeYtdlp:
    """subprocess.Popen as yt-dlp: these output lines (stdout and stderr together), then this
    exit code."""

    def __init__(self, lines, code=0):
        self.lines, self.code = lines, code

    def __call__(self, args, **kw):
        self.args, self.kw = args, kw
        self.stdout = io.StringIO("".join(self.lines))
        self.returncode = self.code
        return self

    def wait(self):
        return self.code

    def poll(self):
        return None if getattr(self, "killed", None) is False else self.code

    def kill(self):
        self.killed = True


def test_fetch_video_counts_skipped_segments(defaults, tmp_path, monkeypatch, capsys):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    # yt-dlp's to_screen: on stdout, so the two streams are read together
    fake = FakeYtdlp(["[download] fragment not found; Skipping fragment 12 ...\n",
                      "[download] fragment not found; Skipping fragment 13 ...\n",
                      "WARNING: something else\n"])
    monkeypatch.setattr(kick.subprocess, "Popen", fake)
    kick.fetch_video(tmp_path, {"source": "https://example.invalid/master.m3u8"}, defaults)
    assert fake.kw["stderr"] == subprocess.STDOUT
    assert "--logtostderr" not in fake.args  # the Python API's only: the command line refuses it
    assert "2 video segments were missing" in capsys.readouterr().out
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"stream_id": "kick-1", "skipped_fragments": 2}


def test_fetch_video_without_gaps_or_meta_writes_none(defaults, tmp_path, monkeypatch):
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "meta.json").exists()


def test_a_clean_download_clears_an_old_gap_count(defaults, tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1", "skipped_fragments": 3}),
                                        encoding="utf-8")
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8")) == {"stream_id": "kick-1"}


def test_fetch_video_failure_still_raises(defaults, tmp_path, monkeypatch):
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["ERROR: 403\n"], code=1))
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)


def test_a_resumed_download_keeps_the_failed_runs_count(defaults, tmp_path, monkeypatch, capsys):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    (tmp_path / "source.ts").write_bytes(b"ts")
    (tmp_path / "source.ts.ytdl").write_text("{}", encoding="utf-8")  # yt-dlp's resume point
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(
        ["Skipping fragment 12\n", "Skipping fragment 13\n", "ERROR: connection reset\n"], code=1))
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert (tmp_path / "source.ts.skipped").read_text(encoding="utf-8") == "2"
    # the retry resumes after them, so doesn't print them again
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["Skipping fragment 40\n", "[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["skipped_fragments"] == 3
    assert not (tmp_path / "source.ts.skipped").exists()


def test_a_fresh_download_ignores_an_old_tally(defaults, tmp_path, monkeypatch):
    (tmp_path / "source.ts.skipped").write_text("5", encoding="utf-8")  # no .ytdl: not a resume
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "source.ts.skipped").exists()
    assert not (tmp_path / "meta.json").exists()


def test_a_failed_fresh_download_drops_an_old_tally_before_the_retry(defaults, tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    (tmp_path / "source.ts.skipped").write_text("5", encoding="utf-8")  # an earlier attempt's
    fail = FakeYtdlp(["ERROR: connection reset\n"], code=1)

    def starts_then_fails(args, **kw):  # yt-dlp creates its resume point, then drops, no skips
        (tmp_path / "source.ts.ytdl").write_text("{}", encoding="utf-8")
        return fail(args, **kw)

    monkeypatch.setattr(kick.subprocess, "Popen", starts_then_fails)
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "source.ts.skipped").exists()
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["Skipping fragment 9\n", "[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)  # resumes from .ytdl: its own skip only
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["skipped_fragments"] == 1


def test_yt_dlp_is_killed_when_passing_its_output_through_fails(defaults, tmp_path, monkeypatch):
    fake = FakeYtdlp(["Skipping fragment 1\n", "[download] 5%\n"])
    fake.killed = False  # still running until killed
    monkeypatch.setattr(kick.subprocess, "Popen", fake)

    def broken(line):
        raise BrokenPipeError

    monkeypatch.setattr(kick.sys.stdout, "write", broken)
    with pytest.raises(BrokenPipeError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert fake.killed is True
    assert (tmp_path / "source.ts.skipped").read_text(encoding="utf-8") == "1"


def test_a_web_page_instead_of_json_is_a_clear_error(monkeypatch):
    class Page(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(kick.urllib.request, "urlopen",
                        lambda req, timeout: Page(b"<!DOCTYPE html><title>Just a moment...</title>"))
    with pytest.raises(ClipperError, match="web page, not data"):
        kick._get("https://kick.com/api/v2/channels/x", "ua")


def test_a_second_fetch_keeps_the_gap_count_while_the_video_stays(defaults, tmp_path, monkeypatch):
    defaults["paths"]["streams"] = tmp_path
    d = tmp_path / "kick-1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"stream_id": "kick-1", "skipped_fragments": 3}), encoding="utf-8")
    (d / "source.ts").write_bytes(b"ts")
    monkeypatch.setattr(kick, "resolve", lambda url, cfg: {"stream_id": "kick-1", "title": "t", "duration_s": 3600})
    monkeypatch.setattr(kick, "fetch_chat", lambda *a: None)
    monkeypatch.setattr(kick, "fetch_video", lambda *a: None)  # source.ts is there: not fetched again
    args = argparse.Namespace(url="u", no_chat=False, no_video=False)
    cli.cmd_fetch(defaults, args)
    assert json.loads((d / "meta.json").read_text(encoding="utf-8"))["skipped_fragments"] == 3
    (d / "source.ts").unlink()  # gone: downloaded again and counted afresh
    cli.cmd_fetch(defaults, args)
    assert "skipped_fragments" not in json.loads((d / "meta.json").read_text(encoding="utf-8"))
