import json
import subprocess

import pytest

from clipper import archive, instagram, story, swarm, ytdl
from clipper.errors import ClipperError


def test_reel_and_profile_links():
    code = instagram.shortcode
    assert code("https://www.instagram.com/reel/DAbc_12-x/?igsh=abc") == "DAbc_12-x"
    assert code("https://instagram.com/some.one/reel/DAbc/") == "DAbc"
    assert code("https://www.instagram.com/p/DAbc/") == code("https://www.instagram.com/reels/DAbc/") == "DAbc"
    assert code("https://www.instagram.com/some.one/") is None
    name = instagram.username
    assert name("https://www.instagram.com/Some.One/") == name("instagram.com/some.one?hl=en") == "some.one"
    assert name("https://www.instagram.com/reel/DAbc/") is None       # a reel isn't a profile
    assert name("https://www.instagram.com/explore/") is None
    assert name("https://www.youtube.com/someone") is None
    assert instagram.post_url("DAbc") == "https://www.instagram.com/reel/DAbc/"


def node(code, media_type=2, **kw):
    return {"code": code, "media_type": media_type, "video_duration": 41.5, "taken_at": 1793000000,
            "product_type": "clips", "caption": {"text": "\n  Gas station part 2  \nmore words"},
            "user": {"username": "some.one", "full_name": "Some One"}, **kw}


def test_only_a_single_video_post_is_an_item():
    it = instagram.item(node("DAbc"))
    assert (it["id"], it["source"], it["url"], it["kind"]) == \
        ("ig-DAbc", "instagram", "https://www.instagram.com/reel/DAbc/", "reel")
    assert (it["title"], it["duration_s"], it["channel"], it["date"]) == \
        ("Gas station part 2", 41.5, "Some One", "2026-10-26")
    assert instagram.item(node("DAbc", product_type="feed"))["url"] == "https://www.instagram.com/p/DAbc/"
    assert instagram.item(node("DAbc", caption=None))["title"] == ""
    assert instagram.item(node("DAbc", media_type=1)) is None      # a photo
    assert instagram.item(node("DAbc", media_type=8)) is None      # a carousel


class FakeSession:
    """Instagram's two endpoints, as instagram.com's own pages get them."""

    def __init__(self, pages, user=None, status=200):
        self.pages, self.asked, self.status = pages, [], status
        self.user = {"id": "42", "full_name": "Some One", "is_private": False} if user is None else user

    def get(self, url, params, **kw):
        self.asked.append((url.rsplit("/api/v1/", 1)[1], dict(params)))

        class R:
            status_code = self.status

            def json(r):
                if "web_profile_info" in url:
                    return {"data": {"user": self.user}, "status": "ok"}
                return self.pages[len([a for a in self.asked if a[0].startswith("feed/")]) - 1]
        return R()


def test_a_profile_is_read_a_page_at_a_time(defaults):
    pages = [{"items": [node("A"), node("B", media_type=1)], "more_available": True, "next_max_id": "m1"},
             {"items": [node("C"), node("D", media_type=8)], "more_available": False}]
    s, slept = FakeSession(pages), []
    items, other = instagram.list_profile(defaults, "https://www.instagram.com/some.one/", s=s, sleep=slept.append)
    assert ([it["id"] for it in items], other) == (["ig-A", "ig-C"], 2)
    assert s.asked == [("users/web_profile_info/", {"username": "some.one"}),
                       ("feed/user/42/", {"count": 33}), ("feed/user/42/", {"count": 33, "max_id": "m1"})]
    assert slept == [defaults["archive"]["instagram_pace_s"]] * 2    # paced: never a burst

    # [archive] instagram_max_items stops a long profile
    defaults["archive"]["instagram_max_items"] = 2
    s = FakeSession([{"items": [node("A"), node("B")], "more_available": True, "next_max_id": "m1"}] * 5)
    items, _ = instagram.list_profile(defaults, "instagram.com/some.one", s=s, sleep=lambda _: None)
    assert len(items) == 2 and len(s.asked) == 2


def test_a_refusal_says_what_to_do(defaults):
    with pytest.raises(ClipperError, match="private"):
        instagram.list_profile(defaults, "instagram.com/some.one", sleep=lambda _: None,
                               s=FakeSession([], user={"id": "42", "is_private": True}))
    with pytest.raises(ClipperError, match="rate-limiting"):
        instagram.list_profile(defaults, "instagram.com/some.one", s=FakeSession([], status=429))
    with pytest.raises(ClipperError, match="export the cookies again"):
        instagram.list_profile(defaults, "instagram.com/some.one", s=FakeSession([], status=302))
    with pytest.raises(ClipperError, match="not an Instagram profile"):
        instagram.list_profile(defaults, "https://www.instagram.com/reel/DAbc/", s=FakeSession([]))


def test_a_profile_needs_the_cookies_file(tmp_path):
    f = tmp_path / "instagram_cookies.txt"
    cfg = {"paths": {"instagram_cookies": f}}
    with pytest.raises(ClipperError, match="no cookies file"):
        instagram.session(cfg)
    f.write_text(ytdl.netscape_text([ytdl.Row(".instagram.com", "/", True, None, "csrftoken", "t")]), encoding="utf-8")
    with pytest.raises(ClipperError, match="no live Instagram sign-in"):
        instagram.session(cfg)
    f.write_text(ytdl.netscape_text([ytdl.Row(".instagram.com", "/", True, None, "csrftoken", "t"),
                                     ytdl.Row(".instagram.com", "/", True, None, "sessionid", "s"),
                                     ytdl.Row(".youtube.com", "/", True, None, "SID", "y")]), encoding="utf-8")
    s = instagram.session(cfg)
    assert sorted(c.name for c in s.cookies) == ["csrftoken", "sessionid"]   # only Instagram's own
    assert s.headers["X-CSRFToken"] == "t"
    # a fresher export under the extension's own name is used without a rename
    newer = tmp_path / "www.instagram.com_11-11-2026.json"
    newer.write_text("[]", encoding="utf-8")
    import os
    os.utime(f, (1_000, 1_000))
    assert ytdl.instagram_cookies_file(cfg) == newer


def test_a_reel_goes_signed_in_only_when_instagram_asks(monkeypatch, tmp_path):
    cookies = tmp_path / "instagram_cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    cfg = {"paths": {"instagram_cookies": cookies}}
    calls = []

    def run(err):
        def fake(*args):
            calls.append(args)
            signed = args[:1] == ("--cookies",)
            return subprocess.CompletedProcess(args, 0 if signed or not err else 1, "", "" if signed else err)
        monkeypatch.setattr(ytdl, "run", fake)
        calls.clear()
    run("")
    assert ytdl.run_instagram(cfg, "URL")[1] is False and len(calls) == 1      # anonymous worked
    run("ERROR: [Instagram] DAbc: Requested content is not available, rate-limit reached or login required. "
        "Use --cookies, --cookies-from-browser to provide account credentials")
    r, signed = ytdl.run_instagram(cfg, "URL")
    assert (r.returncode, signed, len(calls)) == (0, True, 2)
    assert calls[1][1] != str(cookies)                                           # a private copy, not the file
    run("ERROR: [Instagram] DAbc: Unable to download webpage: HTTP Error 500")
    assert ytdl.run_instagram(cfg, "URL")[1] is False and len(calls) == 1      # not a login problem: no retry
    run("ERROR: login required")
    r, signed = ytdl.run_instagram({"paths": {"instagram_cookies": tmp_path / "none.txt"}}, "URL")
    assert (r.returncode, signed, len(calls)) == (1, False, 1)                   # no cookies file: as it failed


def test_archive_add_keeps_short_reels(defaults, tmp_path, monkeypatch, capsys):
    defaults["paths"]["archive"] = tmp_path / "archive"
    found = [instagram.item(node("A", video_duration=12.0)), instagram.item(node("B", video_duration=3.0))]
    monkeypatch.setattr(instagram, "list_url", lambda cfg, url: (found, 4))
    archive.add(defaults, "someone", ["https://www.instagram.com/some.one/"])
    cat = archive.load_catalog(archive.subject_dir(defaults, "someone"))
    assert list(cat) == ["ig-A"]              # 12 s is under the Shorts floor (60) and kept; 3 s is not
    assert "2 found, 4 photo or carousel posts passed over, 1 under 5 s skipped, 1 new" in capsys.readouterr().out


def test_a_reel_by_its_link(monkeypatch):
    info = {"id": "DAbc", "title": "Video by some.one", "description": "Gas station part 2\n#tags",
            "duration": 41.5, "channel": "Some One", "timestamp": 1793000000}
    monkeypatch.setattr(ytdl, "run_instagram", lambda cfg, *a: (
        subprocess.CompletedProcess(a, 0, json.dumps(info), ""), False))
    it = instagram.probe({}, "https://www.instagram.com/some.one/reel/DAbc/?igsh=x")
    assert (it["id"], it["url"], it["title"], it["duration_s"], it["date"]) == \
        ("ig-DAbc", "https://www.instagram.com/reel/DAbc/", "Gas station part 2", 41.5, "2026-10-26")
    monkeypatch.setattr(ytdl, "run_instagram", lambda cfg, *a: (
        subprocess.CompletedProcess(a, 0, json.dumps({"_type": "playlist", "entries": []}), ""), False))
    with pytest.raises(ClipperError, match="not one video"):
        instagram.probe({}, "https://www.instagram.com/p/DAbc/")
    monkeypatch.setattr(ytdl, "run_instagram", lambda cfg, *a: (
        subprocess.CompletedProcess(a, 1, "", "ERROR: login required"), False))
    with pytest.raises(ClipperError, match="login required"):
        instagram.probe({}, "https://www.instagram.com/reel/DAbc/")


def test_a_story_takes_a_reel_link(defaults):
    k = swarm.finding_key
    assert k({"url": "https://www.instagram.com/some.one/reel/DAbc/?igsh=x"}) == \
        k({"url": "https://instagram.com/p/DAbc"}) == "ig:DAbc"
    assert k({"url": "https://www.instagram.com/some.one/"}) == "instagram.com/some.one"   # a profile is a page
    it = story.given_item(defaults, "https://www.instagram.com/reel/DAbc/?igsh=x")   # no request: its facts come with the download
    assert (it["id"], it["source"], it["url"], it["key"]) == \
        ("ig-DAbc", "instagram", "https://www.instagram.com/reel/DAbc/", "ig:DAbc")
    # a reel of unknown length doesn't count as an hour against --max-hours
    assert story.length_s(it) == story.UNKNOWN_REEL_S
    assert story.length_s({"source": "youtube", "duration_s": 0}) == story.UNKNOWN_S
    keep, left = story.fit([dict(it, id=f"ig-{n}") for n in range(5)], 12, 0.1)
    assert (len(keep), len(left)) == (4, 1)
