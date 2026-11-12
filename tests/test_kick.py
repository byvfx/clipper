"""Kick: a VOD download's missing segments are counted, and a web page where the API's JSON
should be is a clear error. Offline: yt-dlp and Kick are stand-ins."""
import io
import json
import subprocess

import pytest

from clipper import kick
from clipper.errors import ClipperError


class FakeYtdlp:
    """subprocess.Popen as yt-dlp: these stderr lines, then this exit code."""

    def __init__(self, lines, code=0):
        self.lines, self.code = lines, code

    def __call__(self, args, **kw):
        self.args = args
        self.stderr = io.StringIO("".join(self.lines))
        self.returncode = self.code
        return self

    def wait(self):
        return self.code


def test_fetch_video_counts_skipped_segments(defaults, tmp_path, monkeypatch, capsys):
    (tmp_path / "meta.json").write_text(json.dumps({"stream_id": "kick-1"}), encoding="utf-8")
    fake = FakeYtdlp(["WARNING: [download] fragment not found; Skipping fragment 12 ...\n",
                      "WARNING: [download] fragment not found; Skipping fragment 13 ...\n",
                      "WARNING: something else\n"])
    monkeypatch.setattr(kick.subprocess, "Popen", fake)
    kick.fetch_video(tmp_path, {"source": "https://example.invalid/master.m3u8"}, defaults)
    assert "2 video segments were missing" in capsys.readouterr().out
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"stream_id": "kick-1", "skipped_fragments": 2}


def test_fetch_video_without_gaps_leaves_meta_alone(defaults, tmp_path, monkeypatch):
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["[download] 100%\n"]))
    kick.fetch_video(tmp_path, {"source": "u"}, defaults)
    assert not (tmp_path / "meta.json").exists()


def test_fetch_video_failure_still_raises(defaults, tmp_path, monkeypatch):
    monkeypatch.setattr(kick.subprocess, "Popen", FakeYtdlp(["ERROR: 403\n"], code=1))
    with pytest.raises(subprocess.CalledProcessError):
        kick.fetch_video(tmp_path, {"source": "u"}, defaults)


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
