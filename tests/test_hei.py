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
    "/more/": index_page("https://www.heinetwork.tv/more/feed/", "https://www.heinetwork.tv/more/vfa-acquisition-announcement/",
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
    f.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\twordpress_logged_in_abc\tv\n",
                 encoding="utf-8")
    with pytest.raises(ClipperError, match="no live heinetwork.tv sign-in"):  # another WordPress site's
        hei.session(f)
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


def test_a_refresh_puts_new_items_first(defaults, tmp_path, monkeypatch):
    defaults["paths"]["archive"] = tmp_path / "archive"
    listed = [[{"id": "yt-b", "source": "youtube", "title": "b", "duration_s": 600.0},
               {"id": "yt-a", "source": "youtube", "title": "a", "duration_s": 600.0}]]
    monkeypatch.setattr(archive, "list_youtube", lambda url: listed[-1])
    archive.add(defaults, "oc", ["https://www.youtube.com/@x"])
    listed.append([{"id": "yt-c", "source": "youtube", "title": "c", "duration_s": 600.0}] + listed[0])
    archive.add(defaults, "oc", ["https://www.youtube.com/@x"])
    d = tmp_path / "archive" / "oc"
    assert list(archive.load_catalog(d)) == ["yt-c", "yt-b", "yt-a"]
    kept = []
    monkeypatch.setattr(archive, "keep_video", lambda cfg, idir, it: kept.append(it["id"]) or 1 / 0)
    archive.run_videos(defaults, "oc", None, 1, False, 1, False)
    archive.run_videos(defaults, "oc", None, 1, True, 1, False)
    assert kept == ["yt-c", "yt-a"]


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
    assert "--abort-on-unavailable-fragments" in calls[0] and "--remux-video" in calls[0]

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
        headers = {"content-type": "text/csv"}

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


def test_a_page_instead_of_csv_keeps_the_saved_posts(defaults, tmp_path, monkeypatch):
    defaults["paths"]["archive"] = tmp_path / "archive"

    class Resp:
        content = b"<!DOCTYPE html><html>Sign in</html>"
        headers = {"content-type": "text/html; charset=utf-8"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(archive.requests, "get", lambda url, timeout: Resp())
    with pytest.raises(ClipperError, match="not CSV"):
        archive.save_posts(defaults, "oncinema", "https://docs.google.com/spreadsheets/d/x/edit")
    assert not (tmp_path / "archive" / "oncinema" / "posts.json").exists()


def test_relabel_names_items_from_the_subjects_own_voices(defaults, tmp_path, capsys):
    defaults["paths"]["archive"] = tmp_path / "archive"
    defaults["paths"]["voices"] = tmp_path / "voices"
    d = tmp_path / "archive" / "oc"
    for item, emb in [("hei-a", [1.0, 0.0]), ("hei-b", [0.99, 0.05])]:
        (d / item).mkdir(parents=True)
        (d / item / "diarization.json").write_text(json.dumps(
            {"model": "m", "turns": [[0.0, 120.0, "SPEAKER_00"]], "embeddings": {"SPEAKER_00": emb}}),
            encoding="utf-8")
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "oc.json").write_text(json.dumps({"model": "m", "voices": {"Gregg": [[1.0, 0.0]]}}),
                                                  encoding="utf-8")
    (tmp_path / "voices" / "kino.json").write_text(json.dumps({"model": "m", "voices": {"Andy": [[1.0, 0.0]]}}),
                                                    encoding="utf-8")
    archive.relabel(defaults, "oc")
    assert "Gregg" in capsys.readouterr().out
    assert json.loads((d / "hei-b" / "speakers.json").read_text(encoding="utf-8"))["speakers"]["SPEAKER_00"]["name"] == "Gregg"
    assert archive.is_item(defaults, d / "hei-a") and not archive.is_item(defaults, tmp_path / "streams" / "x")
    assert archive.item_cfg(defaults, d / "hei-a")["show"]["key"] == "oc"


def test_add_orders_several_sources_by_date(defaults, tmp_path, monkeypatch):
    defaults["paths"]["archive"] = tmp_path / "archive"
    hei_items = [{"id": "hei-new", "source": "hei", "title": "a", "duration_s": 600.0, "date": "2025-12-03"},
                 {"id": "hei-old", "source": "hei", "title": "b", "duration_s": 600.0, "date": "2013-05-01"}]
    yt = [{"id": "yt-mid", "source": "youtube", "title": "c", "duration_s": 600.0, "date": "2024-01-01"}]
    monkeypatch.setattr(archive, "list_source", lambda cfg, url: hei_items if "hei" in url else yt)
    archive.add(defaults, "oc", ["https://www.heinetwork.tv", "https://www.youtube.com/@x"])
    assert list(archive.load_catalog(tmp_path / "archive" / "oc")) == ["hei-new", "yt-mid", "hei-old"]


def test_add_fills_a_missing_date_then_sorts(defaults, tmp_path, monkeypatch):
    defaults["paths"]["archive"] = tmp_path / "archive"
    listed = [{"id": "yt-a", "source": "youtube", "title": "a", "duration_s": 600.0, "date": None},
              {"id": "hei-b", "source": "hei", "title": "b", "duration_s": 600.0, "date": "2020-01-01"}]
    monkeypatch.setattr(archive, "list_source", lambda cfg, url: listed)
    archive.add(defaults, "oc", ["u"])
    d = tmp_path / "archive" / "oc"
    assert list(archive.load_catalog(d)) == ["yt-a", "hei-b"]  # one undated: the order as listed
    listed[0] = dict(listed[0], date="2010-01-01", title="renamed")
    archive.add(defaults, "oc", ["u"])
    cat = archive.load_catalog(d)
    assert list(cat) == ["hei-b", "yt-a"] and cat["yt-a"]["title"] == "a"


def test_yt_date_from_upload_date_or_timestamp():
    assert archive.yt_date({"upload_date": "20240131"}) == "2024-01-31"
    assert archive.yt_date({"timestamp": 1704067200}) == "2024-01-01"
    assert archive.yt_date({}) is None


def test_children_skip_the_feed_but_not_a_feedback_page():
    page = index_page("https://www.heinetwork.tv/more/feed/", "https://www.heinetwork.tv/more/feedback/")
    assert hei.children(page, "/more/") == ["/more/feedback/"]


def test_a_bad_match_is_a_clear_error(defaults, tmp_path):
    defaults["paths"]["archive"] = tmp_path / "archive"
    with pytest.raises(ClipperError, match="valid regular expression"):
        archive.add(defaults, "oncinema", ["https://www.youtube.com/@adultswim"], match="[")


def test_a_refresh_fills_a_failed_duration_and_drops_a_short_one(defaults, tmp_path, monkeypatch):
    defaults["paths"]["archive"] = tmp_path / "archive"
    defaults["archive"]["min_duration_s"] = 60
    first = [{"id": "hei-ep", "source": "hei", "title": "ep", "duration_s": 0.0, "date": "2020-01-02"},
             {"id": "hei-bumper", "source": "hei", "title": "bumper", "duration_s": 0.0, "date": "2020-01-01"},
             {"id": "hei-done", "source": "hei", "title": "done", "duration_s": 0.0, "date": "2019-01-01"},
             {"id": "hei-kept", "source": "hei", "title": "kept", "duration_s": 0.0, "date": "2018-01-01"}]
    monkeypatch.setattr(archive, "list_source", lambda cfg, url: first)  # every probe failed
    archive.add(defaults, "oc", ["https://www.heinetwork.tv"])
    d = tmp_path / "archive" / "oc"
    assert len(archive.load_catalog(d)) == 4
    (d / "hei-done").mkdir()
    (d / "hei-done" / "words.jsonl").write_text("", encoding="utf-8")  # already transcribed: kept
    (d / "hei-kept").mkdir()
    (d / "hei-kept" / "source.mp4").write_bytes(b"v")  # its video already kept: kept too
    again = [dict(first[0], duration_s=3600.0), dict(first[1], duration_s=12.0), dict(first[2], duration_s=30.0),
             dict(first[3], duration_s=40.0)]
    monkeypatch.setattr(archive, "list_source", lambda cfg, url: again)
    archive.add(defaults, "oc", ["https://www.heinetwork.tv"])
    cat = archive.load_catalog(d)
    assert sorted(cat) == ["hei-done", "hei-ep", "hei-kept"]
    assert cat["hei-ep"]["duration_s"] == 3600.0 and cat["hei-done"]["duration_s"] == 30.0


def test_speakers_counts_items_not_speaker_labels(defaults, tmp_path, capsys):
    defaults["paths"]["archive"] = tmp_path / "archive"
    defaults["paths"]["voices"] = tmp_path / "voices"
    d = tmp_path / "archive" / "oc" / "hei-a"
    d.mkdir(parents=True)
    d.joinpath("diarization.json").write_text(json.dumps(  # pyannote split one voice in two
        {"model": "m", "turns": [[0.0, 60.0, "SPEAKER_00"], [60.0, 120.0, "SPEAKER_01"]],
         "embeddings": {"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.99, 0.05]}}), encoding="utf-8")
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "oc.json").write_text(json.dumps({"model": "m", "voices": {"Gregg": [[1.0, 0.0]]}}),
                                                  encoding="utf-8")
    archive.relabel(defaults, "oc")
    assert "in 1 items" in capsys.readouterr().out


def test_a_failed_hei_download_is_never_taken_as_done(defaults, tmp_path, monkeypatch):
    idir = tmp_path / "hei-ep"
    idir.mkdir()
    (idir / "source.ts").write_bytes(b"half")  # an earlier run that died on a segment
    (idir / "source.ts.ytdl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(archive.transcribe, "modal_settings", lambda tc, diarize: {})
    monkeypatch.setattr(archive.transcribe, "modal_worker", lambda tc, gpu: None)
    monkeypatch.setattr(archive.hei, "fresh_m3u8", lambda cookies, it: "https://stream.example/x.m3u8")
    seen = []

    class Failed:
        returncode, stdout, stderr = 1, "", "ERROR: fragment 7 not found"

    def run(*args):
        seen.append((idir / "source.ts").exists())
        (idir / "source.ts").write_bytes(b"part")  # --no-part: written in place, then it fails
        return Failed()

    monkeypatch.setattr(archive.ytdl, "run", run)
    it = {"id": "hei-ep", "source": "hei", "title": "ep", "duration_s": 3600.0, "m3u8": "x"}
    with pytest.raises(RuntimeError, match="yt-dlp"):
        archive.transcribe_item(defaults, idir, it, "L4")
    assert seen == [False]  # yt-dlp never saw the old partial (it would call it downloaded)
    assert not (idir / "source.ts").exists() and not (idir / "source.ts.ytdl").exists()


def test_a_transcript_fills_the_catalogs_missing_length(defaults, tmp_path, monkeypatch):
    d = tmp_path / "oc"
    archive.save_catalog(d, {"hei-ep": {"id": "hei-ep", "source": "hei", "title": "ep", "duration_s": 0.0}})
    monkeypatch.setattr(archive, "transcribe_item",
                        lambda cfg, idir, it, gpu, retry_on: {"est_usd": 0.1, "signed_in": False, "duration_s": 3600.0})
    archive._transcribe_all(defaults, d, "oc", list(archive.load_catalog(d).values()), "L4", 1)
    assert archive.load_catalog(d)["hei-ep"]["duration_s"] == 3600.0
