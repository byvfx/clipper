"""heinetwork.tv as an archive source, and what came with it: `archive add --match`, keeping
videos, and the posts sheet. Offline: the site, yt-dlp and Google are stand-ins."""
import json

import pytest

from clipper import archive, hei
from clipper.errors import ClipperError

M3U8 = "https://stream.mux.com/AbCdEfGhIjKlMnOpQrStUv012.m3u8"


def item_page(series: str, title: str, air: str, src: str = M3U8) -> str:
    return f"""<html><head><meta property="og:title" content="{title} - HEI Network"></head><body>
    <mux-video slot="media" src="{src}" poster="x.png"></mux-video>
    <div class="player-info" data-player-info> <div class="text-xs"> {series} </div>
    <div class="font-display"> {title} </div> <div class="text-xs"> Air Date: {air} </div> </div>
    <div>Playlist</div><a href="https://www.heinetwork.tv/on-cinema-at-the-cinema/season-16/other/">other</a>
    </body></html>"""


def index_page(*hrefs: str) -> str:
    return "<html><body>" + "".join(f'<a href="{h}">x</a>' for h in hrefs) + \
        '<a href="https://www.heinetwork.tv/my-account/lost-password/">lost</a></body></html>'


SITE = {
    "/on-cinema-at-the-cinema/": index_page("https://www.heinetwork.tv/on-cinema-at-the-cinema/season-16/"),
    "/on-cinema-at-the-cinema/season-16/": index_page(
        "https://www.heinetwork.tv/on-cinema-at-the-cinema/season-16/hamnet-and-ella-mccay/",
        "/on-cinema-at-the-cinema/season-16/wicked-for-good/"),
    "/on-cinema-at-the-cinema/season-16/hamnet-and-ella-mccay/": item_page(
        "On Cinema at the Cinema / Season 16", "1610 &#8216;Hamnet&#8217; and &#8216;Ella McCay&#8217;",
        "12/03/2025"),
    "/on-cinema-at-the-cinema/season-16/wicked-for-good/": item_page(
        "On Cinema at the Cinema / Season 16", "1608 &#8216;Wicked: For Good&#8217;", "11/19/2025",
        "https://stream.mux.com/ZzZzZzZzZzZzZzZzZzZzZz012.m3u8"),
    "/more/": index_page("https://www.heinetwork.tv/more/vfa-acquisition-announcement/",
                         "https://www.heinetwork.tv/more/introducing-hei-points/"),
    "/more/vfa-acquisition-announcement/": item_page("", "VFA Acquisition Announcement", "04/30/2021"),
    "/more/introducing-hei-points/": "<html><body>An article, no video</body></html>",
}


def test_item_ids_are_short_and_folder_safe():
    assert hei.item_id("/on-cinema-at-the-cinema/season-16/hamnet-and-ella-mccay/") == "hei-oc-s16-hamnet-and-ella-mccay"
    assert hei.item_id("/oscar-specials/13th-annual-on-cinema-oscar-special/") == \
        "hei-oscar-13th-annual-on-cinema-oscar-special"
    assert hei.item_id("/decker/decker-classified/decker-classified-episode-1/") == \
        "hei-decker-decker-classified-decker-classified-episode-1"


def test_parse_item_reads_the_player_info():
    it = hei.parse_item(SITE["/on-cinema-at-the-cinema/season-16/hamnet-and-ella-mccay/"],
                        "https://www.heinetwork.tv/on-cinema-at-the-cinema/season-16/hamnet-and-ella-mccay/")
    assert it["title"] == "1610 ‘Hamnet’ and ‘Ella McCay’"
    assert it["series"] == "On Cinema at the Cinema / Season 16"
    assert it["date"] == "2025-12-03"
    assert it["m3u8"] == M3U8
    assert it["kind"] == "on-cinema-at-the-cinema" and it["source"] == "hei"
    assert hei.parse_item(SITE["/more/"], "https://www.heinetwork.tv/more/") is None


def test_list_items_walks_the_sections_newest_first():
    got = hei.list_items(None, sections=("/on-cinema-at-the-cinema/", "/more/"), get=SITE.__getitem__)
    assert [it["id"] for it in got] == ["hei-oc-s16-hamnet-and-ella-mccay", "hei-oc-s16-wicked-for-good",
                                        "hei-more-vfa-acquisition-announcement"]
    # the playlist's sibling link (season-16/other/) is not below the item page: never fetched


def test_a_signed_out_page_stops_the_listing():
    site = dict(SITE)
    site["/more/vfa-acquisition-announcement/"] = "<p>You need to be a HEINetwork Member to access this content.</p>"
    with pytest.raises(ClipperError, match="members only"):
        hei.list_items(None, sections=("/more/",), get=site.__getitem__)


def test_cookies_without_a_sign_in_are_refused(tmp_path):
    f = tmp_path / "hei_cookies.txt"
    f.write_text("# Netscape HTTP Cookie File\n.heinetwork.tv\tTRUE\t/\tTRUE\t0\twoocommerce_items\t1\n",
                 encoding="utf-8")
    with pytest.raises(ClipperError, match="no live heinetwork.tv sign-in"):
        hei.session(f)
    f.write_text("# Netscape HTTP Cookie File\n.heinetwork.tv\tTRUE\t/\tTRUE\t0\twordpress_logged_in_abc\tv\n",
                 encoding="utf-8")
    assert "wordpress_logged_in_abc" in hei.session(f).cookies.keys()
    with pytest.raises(ClipperError, match="no HEI cookies"):
        hei.session(tmp_path / "missing.txt")


def test_add_match_keeps_only_matching_titles(defaults, tmp_path, monkeypatch, capsys):
    defaults["paths"]["archive"] = tmp_path / "archive"
    found = [{"id": f"yt-{i}", "source": "youtube", "url": f"u{i}", "title": t, "duration_s": 600.0}
             for i, t in enumerate(["'Pompeii' | On Cinema Season 4, Ep. 7", "Rick and Morty clip",
                                    "DECKER: Episode 2 | Decker"])]
    monkeypatch.setattr(archive, "list_youtube", lambda url: found)
    archive.add(defaults, "oncinema", ["https://www.youtube.com/@adultswim"], match="on cinema|decker")
    assert sorted(archive.load_catalog(tmp_path / "archive" / "oncinema")) == ["yt-0", "yt-2"]
    assert "2 found matching" in capsys.readouterr().out


def test_videos_todo_skips_kept_dead_and_unkeepable(tmp_path):
    cat = {i: {"id": i, "source": s} for i, s in
           [("hei-a", "hei"), ("hei-b", "hei"), ("yt-c", "youtube"), ("od-d", "odysee"), ("yt-e", "youtube")]}
    (tmp_path / "hei-b").mkdir()
    (tmp_path / "hei-b" / "source.mp4").write_bytes(b"x")
    (tmp_path / "links.json").write_text(json.dumps({"yt-e": {"status": "dead"}}), encoding="utf-8")
    assert [it["id"] for it in archive.videos_todo(tmp_path, cat)] == ["hei-a", "yt-c"]
    assert [it["id"] for it in archive.videos_todo(tmp_path, cat, ["yt-c"])] == ["yt-c"]


def test_keep_video_renames_only_a_complete_download(defaults, tmp_path, monkeypatch):
    calls = []

    class Done:
        returncode, stderr = 0, ""

    def run(*args):
        calls.append(args)
        out = args[args.index("-o") + 1].replace("%(ext)s", "mp4")
        open(out, "wb").write(b"video")
        return Done()

    monkeypatch.setattr(archive.ytdl, "run", run)
    idir = tmp_path / "hei-a"
    got = archive.keep_video(defaults, idir, {"id": "hei-a", "source": "hei", "m3u8": M3U8})
    assert got == idir / "source.mp4" and got.read_bytes() == b"video"
    assert not list(idir.glob("download.*"))
    assert calls[0][-1] == M3U8 and "--referer" in calls[0]

    class Failed:
        returncode, stderr = 1, "ERROR: boom"

    monkeypatch.setattr(archive.ytdl, "run", lambda *a: Failed())
    with pytest.raises(RuntimeError, match="boom"):
        archive.keep_video(defaults, tmp_path / "hei-b", {"id": "hei-b", "source": "hei", "m3u8": M3U8})
    assert archive.kept_video(tmp_path / "hei-b") is None


def test_sheet_csv_url():
    base = "https://docs.google.com/spreadsheets/d/1AbC-d_9/"
    assert archive.sheet_csv_url(base + "edit?usp=sharing") == base + "export?format=csv"
    assert archive.sheet_csv_url(base + "edit#gid=42") == base + "export?format=csv&gid=42"
    with pytest.raises(ClipperError):
        archive.sheet_csv_url("https://example.com/sheet")


def test_save_posts_writes_csv_and_json(defaults, tmp_path, monkeypatch, capsys):
    defaults["paths"]["archive"] = tmp_path / "archive"
    csv_text = ('﻿"Account Name","Post","Date"\n"@timheidecker","hey everyone!","Oct 5, 2011"\n'
                '"@greggturkington","Pop Quiz","Oct 6, 2011"\n"@timheidecker","more","Oct 7, 2011"\n')

    class Resp:
        content = csv_text.encode("utf-8")

        def raise_for_status(self):
            pass

    monkeypatch.setattr(archive.requests, "get", lambda url, timeout: Resp())
    archive.save_posts(defaults, "oncinema", "https://docs.google.com/spreadsheets/d/x/edit")
    d = tmp_path / "archive" / "oncinema"
    data = json.loads((d / "posts.json").read_text(encoding="utf-8"))
    assert [p["Account Name"] for p in data["posts"]] == ["@timheidecker", "@greggturkington", "@timheidecker"]
    assert (d / "posts.csv").is_file()
    assert "@timheidecker 2" in capsys.readouterr().out


def jwt(exp: float) -> str:
    import base64

    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


def test_signed_streams_get_a_fresh_token_only_when_theirs_runs_out(monkeypatch):
    public = {"id": "hei-a", "url": "https://www.heinetwork.tv/more/a/", "m3u8": M3U8}
    assert hei.token_expiry(M3U8) is None
    assert hei.fresh_m3u8(None, public) == M3U8  # no token: never refetched
    signed = dict(public, m3u8=f"{M3U8}?token={jwt(2_000_000)}")
    assert hei.token_expiry(signed["m3u8"]) == 2_000_000
    assert hei.fresh_m3u8(None, signed, now=1_000_000) == signed["m3u8"]  # still good

    new = f"{M3U8}?token={jwt(3_000_000)}"

    class Session:
        def get(self, url, timeout):
            class R:
                text = item_page("More", "A", "01/02/2020", new.replace("&", "&amp;"))

                def raise_for_status(self):
                    pass
            return R()

    monkeypatch.setattr(hei, "session", lambda cookies: Session())
    assert hei.fresh_m3u8(None, signed, now=1_999_000) == new  # within the margin: refetched
    assert hei.scrub(f"ERROR: 403 on {new}") == f"ERROR: 403 on {M3U8}?token=..."
