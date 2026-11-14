from pathlib import Path

import pytest

from clipper import clipart
from clipper.errors import ClipperError

REAL_DOWNLOAD = clipart.download  # before the fixture stubs it


@pytest.fixture
def cfg(defaults, tmp_path, monkeypatch):
    defaults["paths"]["clipart"] = tmp_path / "clipart"
    defaults["paths"]["pixabay_key"] = tmp_path / "no_pixabay_key.txt"
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)

    def download(url, stem, max_mb, timeout):
        if url in too_big:
            raise clipart.TooBig(f"{url}: too big")
        dst = stem.with_suffix(Path(url).suffix or ".png")
        dst.write_bytes(b"img")
        downloaded.append(url)
        return dst

    downloaded.clear()
    too_big.clear()
    monkeypatch.setattr(clipart, "download", download)
    return defaults


downloaded: list = []
too_big: set = set()


def cand(key="https://c/1", source="commons", **kw):
    return {"key": key, "source": source, "title": "Cow", "page": key, "url": key + ".png", "alt": "",
            "credit": "Harrison Weir, via Wikimedia Commons", "licence": "Public domain", "description": "",
            "restrictions": "", **kw}


def test_licence_codes():
    code = clipart.licence_code
    assert code("Public domain") == "pd" and code("CC0") == "cc0" and code("CC0 (The Met Open Access)") == "cc0"
    assert code("MIT (MIT)") == "permissive" and code("Apache 2.0 (Apache-2.0)") == "permissive"
    assert code("ISC") == "permissive" and code("Open Font License (OFL-1.1)") == "permissive"
    assert code("Pixabay Content License") == "pixabay"
    assert code("CC BY 4.0 (CC-BY-4.0)") == "cc-by" and code("CC BY-SA 4.0 (CC-BY-SA-4.0)") == "cc-by-sa"
    assert code("GPL 2.0 (GPL-2.0-only)") == "other"
    assert code("CC BY-NC 4.0 (CC-BY-NC-4.0)") == "other"
    assert code("MIT, non-commercial use only") == "other"
    assert code("") == "other" and code("All rights reserved") == "other"


def test_refused(cfg):
    assert clipart.refused(cand(), cfg) == ""
    assert "licence" in clipart.refused(cand(licence="CC BY 4.0"), cfg)
    assert "not stated" in clipart.refused(cand(licence=""), cfg)
    assert "trademarked" in clipart.refused(cand(restrictions="trademarked"), cfg)
    assert "©" in clipart.refused(cand(description="© 2019 Some Studio"), cfg)
    cfg["clipart"]["licences"] = ["cc-by"]
    assert clipart.refused(cand(licence="CC BY 4.0"), cfg) == ""


def test_find_adds_skips_and_tags(cfg, monkeypatch):
    found = [cand("https://c/1"), cand("https://c/2", licence="CC BY-NC 4.0"), cand("https://c/3")]
    monkeypatch.setitem(clipart.FINDERS, "commons", lambda q, n, cfg: [dict(c) for c in found])
    said = []
    assert clipart.find(cfg, "cow", ["commons"], say=said.append) == 2
    by_key = {e["key"]: e for e in clipart.load(cfg)["art"].values()}
    assert set(by_key) == {"https://c/1", "https://c/3"} and any("skip" in s for s in said)
    e = by_key["https://c/1"]
    assert e["status"] == "new" and e["tags"] == ["cow"] and (cfg["paths"]["clipart"] / e["file"]).exists()
    assert e["licence"] == "Public domain" and e["credit"].startswith("Harrison Weir")
    # again: nothing new, the pictures pick up the new search as a tag
    assert clipart.find(cfg, "cattle", ["commons"], say=said.append) == 0
    assert clipart.load(cfg)["art"][e["id"]]["tags"] == ["cattle", "cow"]
    assert len(downloaded) == 2


def test_n_limits_each_source(cfg, monkeypatch):
    monkeypatch.setitem(clipart.FINDERS, "met", lambda q, n, cfg: [cand(f"https://m/{i}", "met") for i in range(9)])
    assert clipart.find(cfg, "x", ["met"], n=3, say=lambda s: None) == 3
    with pytest.raises(ClipperError, match="at least 1"):
        clipart.find(cfg, "x", ["met"], n=0)
    with pytest.raises(ClipperError, match="a search"):
        clipart.find(cfg, "  ", ["met"])


def test_a_failing_source_doesnt_stop_the_others(cfg, monkeypatch):
    def down(q, n, cfg):
        raise ClipperError("pixabay.com: HTTP 503")

    monkeypatch.setitem(clipart.FINDERS, "pixabay", down)
    monkeypatch.setitem(clipart.FINDERS, "commons", lambda q, n, cfg: [cand()])
    said = []
    assert clipart.find(cfg, "x", ["pixabay", "commons"], say=said.append) == 1
    assert "pixabay: pixabay.com: HTTP 503" in said


def test_too_big_falls_back_to_the_smaller_copy(cfg, monkeypatch):
    too_big.add("https://c/1.png")
    monkeypatch.setitem(clipart.FINDERS, "met", lambda q, n, cfg: [cand(source="met", alt="https://c/1-web.jpg"),
                                                                   cand("https://c/2", "met")])
    too_big.add("https://c/2.png")  # no smaller copy: not taken, and said
    said = []
    assert clipart.find(cfg, "x", ["met"], say=said.append) == 1
    assert downloaded == ["https://c/1-web.jpg"]
    assert any("too big" in s for s in said)


def test_drop_remembers_and_keep(cfg, monkeypatch):
    monkeypatch.setitem(clipart.FINDERS, "commons", lambda q, n, cfg: [cand(), cand("https://c/2")])
    clipart.find(cfg, "x", ["commons"], say=lambda s: None)
    a, b = clipart.load(cfg)["art"]
    file_a = cfg["paths"]["clipart"] / clipart.load(cfg)["art"][a]["file"]
    with pytest.raises(ClipperError, match="no art"):
        clipart.set_status(cfg, [a, "nope"], "drop")
    assert file_a.exists()  # a bad id deletes nothing
    clipart.set_status(cfg, [a], "drop")
    clipart.set_status(cfg, [b], "keep")
    assert not file_a.exists()
    assert clipart.load(cfg)["art"][b]["status"] == "kept"
    assert clipart.find(cfg, "x", ["commons"], say=lambda s: None) == 0


def test_add_a_folder_of_pictures(cfg, tmp_path):
    pack = tmp_path / "kenney_emotes"
    (pack / "PNG").mkdir(parents=True)
    for name in ("emote_heart.png", "emote_alert.png"):
        (pack / "PNG" / name).write_bytes(b"png")
    (pack / "License.txt").write_text("CC0")
    with pytest.raises(ClipperError, match="--credit"):
        clipart.add(cfg, str(pack))
    out = clipart.add(cfg, str(pack), ["emote"], "Kenney (kenney.nl)", "CC0", say=lambda s: None)
    assert len(out) == 2 and {e["source"] for e in out} == {"file"} and all(e["licence"] == "CC0" for e in out)
    assert all((cfg["paths"]["clipart"] / e["file"]).read_bytes() == b"png" for e in out)
    assert len(clipart.add(cfg, str(pack), credit="Kenney", licence="CC0", say=lambda s: None)) == 2  # no copies
    assert len(clipart.load(cfg)["art"]) == 2
    with pytest.raises(ClipperError, match="licence"):
        clipart.add(cfg, str(pack / "PNG" / "emote_heart.png"), credit="someone", licence="CC BY-NC")


def test_adding_by_hand_undrops(cfg, monkeypatch):
    c = cand("https://commons.wikimedia.org/wiki/File:Cow.svg")
    monkeypatch.setattr(clipart, "resolve", lambda target, cfg: dict(c))
    (e,) = clipart.add(cfg, c["page"], say=lambda s: None)
    clipart.set_status(cfg, [e["id"]], "drop")
    assert c["key"] in clipart.load(cfg)["dropped"]
    clipart.add(cfg, c["page"], say=lambda s: None)
    assert c["key"] not in clipart.load(cfg)["dropped"] and e["id"] in clipart.load(cfg)["art"]


def test_add_refuses_what_isnt_a_target(cfg):
    with pytest.raises(ClipperError, match="not a web address"):
        clipart.add(cfg, "no-such-file.png")
    with pytest.raises(ClipperError, match="Iconify name"):
        clipart.resolve("https://example.com/cow.png", cfg)


def test_commons_parse(cfg, monkeypatch):
    seen = {}

    def get(url, params=None, timeout=20):
        seen.update(params)
        return {"query": {"pages": {
            "1": {"index": 2, "title": "File:Cow.svg", "imageinfo": [{
                "mime": "image/svg+xml", "width": 500, "url": "https://upload.wikimedia.org/c/Cow.svg",
                "thumburl": "https://upload.wikimedia.org/thumb/c/Cow.svg/2000px-Cow.svg.png?utm=1",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Cow.svg",
                "extmetadata": {"LicenseShortName": {"value": "CC0"}, "Artist": {"value": "<a href='x'>Ann</a>"},
                                "Restrictions": {"value": "trademarked"}}}]},
            "2": {"index": 1, "title": "File:Old cow.jpg", "imageinfo": [{
                "mime": "image/jpeg", "width": 1200, "url": "https://upload.wikimedia.org/o/Old_cow.jpg",
                "thumburl": "https://upload.wikimedia.org/o/Old_cow.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Old_cow.jpg",
                "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]},
            "3": {"index": 3, "title": "File:Cow.webm", "imageinfo": [{"mime": "video/webm", "url": "x"}]}}}}

    monkeypatch.setattr(clipart.broll, "_get", lambda url, params=None, timeout=20, ua="": get(url, params))
    cfg["clipart"]["commons_pictures"] = False
    old, svg = clipart.commons("cow", 4, cfg)
    assert seen["gsrsearch"] == "cow filetype:drawing" and seen["iiurlwidth"] == 2000
    assert old["url"] == "https://upload.wikimedia.org/o/Old_cow.jpg" and old["licence"] == "Public domain"
    assert svg["url"].endswith("2000px-Cow.svg.png") and svg["credit"] == "Ann, via Wikimedia Commons"
    assert svg["restrictions"] == "trademarked" and clipart.refused(svg, cfg)
    clipart.resolve("https://commons.wikimedia.org/wiki/File:Old_cow.jpg", cfg)
    assert seen["titles"] == "File:Old_cow.jpg"


def commons_page(name: str, mime: str) -> dict:
    return {"query": {"pages": {"1": {"index": 1, "title": f"File:{name}", "imageinfo": [{
        "mime": mime, "width": 800, "url": f"https://upload.wikimedia.org/{name}",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{name}",
        "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]}}}}


def test_commons_drawings_first_then_pictures_only_if_wanted(cfg, monkeypatch):
    searches = []

    def get(url, params=None, timeout=20, ua=""):
        searches.append(params["gsrsearch"])
        if params["gsrsearch"].endswith("drawing"):
            return commons_page("Popcorn.svg", "image/svg+xml")
        if fail_pictures:
            raise ClipperError("commons.wikimedia.org: HTTP 503")
        return commons_page("Popcorn.jpg", "image/jpeg")

    monkeypatch.setattr(clipart.broll, "_get", get)
    fail_pictures = False
    assert clipart.find(cfg, "popcorn", ["commons"], n=1, say=lambda s: None) == 1
    assert searches == ["popcorn filetype:drawing"]  # one drawing was enough: no photo search
    assert clipart.find(cfg, "popcorn", ["commons"], n=2, say=lambda s: None) == 1  # the drawing is had already
    assert searches[1:] == ["popcorn filetype:drawing", "popcorn filetype:bitmap"]
    assert {e["title"] for e in clipart.load(cfg)["art"].values()} == {"File:Popcorn.svg", "File:Popcorn.jpg"}
    fail_pictures = True  # the photo search failing keeps the drawings found before it
    searches.clear()
    cfg["paths"]["clipart"] = cfg["paths"]["clipart"].with_name("clipart2")
    said = []
    assert clipart.find(cfg, "popcorn", ["commons"], n=3, say=said.append) == 1
    assert "commons: 1 added" in said


def test_met_parse_keeps_only_public_domain(cfg, monkeypatch):
    objects = {
        "1": {"isPublicDomain": True, "primaryImage": "https://images.metmuseum.org/o/1.jpg",
              "primaryImageSmall": "https://images.metmuseum.org/w/1.jpg", "title": "Cow",
              "artistDisplayName": "Paulus Potter", "objectDate": "1650", "medium": "Etching",
              "objectURL": "https://www.metmuseum.org/art/collection/search/1"},
        "2": {"isPublicDomain": False, "primaryImage": "https://images.metmuseum.org/o/2.jpg", "title": "Modern cow"},
    }

    def get(url, params=None, timeout=20, ua=""):
        if url.endswith("/search"):
            assert params["hasImages"] == "true" and "v1.1" in url
            return {"total": 3, "objectIDs": [1, 2, 3]}
        oid = url.rsplit("/", 1)[1]
        if oid not in objects:
            raise ClipperError("collectionapi.metmuseum.org: HTTP 404")
        return objects[oid]

    monkeypatch.setattr(clipart.broll, "_get", get)
    (c,) = clipart.met("cow", 2, cfg)
    assert c["url"].endswith("/o/1.jpg") and c["alt"].endswith("/w/1.jpg")
    assert c["credit"] == "Paulus Potter, The Metropolitan Museum of Art" and clipart.licence_code(c["licence"]) == "cc0"


def test_cleveland_parse(cfg, monkeypatch):
    def get(url, params=None, timeout=20, ua=""):
        assert params["cc0"] == 1 and params["has_image"] == 1
        return {"data": [
            {"share_license_status": "CC0", "title": "The Cow Drinking", "url": "https://clevelandart.org/art/1922.485",
             "creators": [{"description": "Nicolaes Berchem (Dutch, 1620-1683)"}], "tombstone": "The Cow Drinking. Etching.",
             "images": {"web": {"url": "https://cdn/1922.485_web.jpg"}, "print": {"url": "https://cdn/1922.485_print.jpg"}}},
            {"share_license_status": "Copyrighted", "title": "Later cow", "images": {"web": {"url": "https://cdn/x.jpg"}}},
        ]}

    monkeypatch.setattr(clipart.broll, "_get", get)
    (c,) = clipart.cleveland("cow", 4, cfg)
    assert c["url"].endswith("_print.jpg") and c["alt"].endswith("_web.jpg")
    assert c["credit"].startswith("Nicolaes Berchem") and c["licence"] == "CC0"


COLLECTIONS = {
    "fluent-emoji": {"name": "Fluent Emoji", "author": {"name": "Microsoft Corporation"},
                     "license": {"title": "MIT", "spdx": "MIT"}, "category": "Emoji"},
    "twemoji": {"name": "Twitter Emoji", "author": {"name": "Twitter"},
                "license": {"title": "CC BY 4.0", "spdx": "CC-BY-4.0"}, "category": "Emoji"},
    "simple-icons": {"name": "Simple Icons", "license": {"title": "CC0", "spdx": "CC0-1.0"},
                     "category": "Brands / Social"},
}


def test_iconify_licences_and_logo_sets(cfg, monkeypatch):
    seen = {}

    def get(url, params=None, timeout=20, ua=""):
        if url.endswith("/collections"):
            return {p: i for p, i in COLLECTIONS.items() if not params or p in params["prefixes"].split(",")}
        seen.update(params)
        return {"icons": ["fluent-emoji:cow-face", "twemoji:cow-face", "simple-icons:cow", "unknown:cow",
                          "fluent-emoji:cow-face-flat"]}  # a style of the first: not taken again

    monkeypatch.setattr(clipart.broll, "_get", get)
    cfg["clipart"]["iconify_sets"] = []
    cands = clipart.iconify("cow", 4, cfg)
    assert [c["key"] for c in cands] == ["iconify:fluent-emoji:cow-face", "iconify:twemoji:cow-face"]  # no logo set
    fluent, tw = cands
    assert fluent["url"] == "https://api.iconify.design/fluent-emoji/cow-face.svg?height=2000"
    assert fluent["licence"] == "MIT (MIT)" and clipart.refused(fluent, cfg) == ""
    assert "licence" in clipart.refused(tw, cfg)  # CC BY isn't in the default licences
    assert "prefixes" not in seen
    cfg["clipart"]["iconify_sets"] = ["fluent-emoji", "twemoji"]
    clipart.iconify("cow", 4, cfg)
    assert seen["prefixes"] == "fluent-emoji,twemoji"
    cfg["clipart"]["iconify_sets"] = ["simple-icons"]
    with pytest.raises(ClipperError, match="logo"):
        clipart.iconify("cow", 4, cfg)
    cfg["clipart"]["iconify_sets"] = []
    assert clipart.resolve("fluent-emoji:cow-face", cfg)["key"] == "iconify:fluent-emoji:cow-face"
    assert clipart.resolve("https://icon-sets.iconify.design/fluent-emoji/cow-face/", cfg)["source"] == "iconify"
    with pytest.raises(ClipperError, match="no such icon"):
        clipart.resolve("simple-icons:cow", cfg)


def test_icon_variants():
    base = lambda name: clipart.VARIANT.sub("", name)
    assert base("cow") == base("cow-bold") == base("cow-duotone") == base("cow-fill-round") == "cow"
    assert base("cow-48-bold") == "cow" and base("cow-face") == "cow-face"


def test_met_stops_fetching_once_find_has_enough(cfg, monkeypatch):
    fetched = []

    def get(url, params=None, timeout=20, ua=""):
        if url.endswith("/search"):
            return {"objectIDs": list(range(1, 13))}
        fetched.append(url)
        oid = url.rsplit("/", 1)[1]
        return {"isPublicDomain": True, "primaryImage": f"https://images.metmuseum.org/o/{oid}.jpg", "title": oid,
                "objectURL": f"https://www.metmuseum.org/art/collection/search/{oid}"}

    monkeypatch.setattr(clipart.broll, "_get", get)
    assert clipart.find(cfg, "cow", ["met"], n=2, say=lambda s: None) == 2
    assert len(fetched) == 2


def test_a_failing_met_search_is_the_sources_error(cfg, monkeypatch):
    def down(url, params=None, timeout=20, ua=""):
        raise ClipperError("collectionapi.metmuseum.org: HTTP 503")

    monkeypatch.setattr(clipart.broll, "_get", down)
    said = []
    assert clipart.find(cfg, "cow", ["met"], say=said.append) == 0
    assert "met: collectionapi.metmuseum.org: HTTP 503" in said


def test_pixabay_needs_a_key_and_hides_it(cfg, monkeypatch, tmp_path):
    with pytest.raises(ClipperError, match="PIXABAY_API_KEY"):
        clipart.pixabay("cow", 4, cfg)
    (tmp_path / "no_pixabay_key.txt").write_text("sekrit\n")
    seen = {}

    def get(url, params=None, timeout=20, ua=""):
        seen.update(params)
        return {"hits": [{"pageURL": "https://pixabay.com/vectors/cow-animal-123/", "tags": "cow, animal",
                          "largeImageURL": "https://pixabay.com/get/big.png", "webformatURL": "https://pixabay.com/get/w.png",
                          "user": "OpenClipart-Vectors"}, {"tags": "no page"}]}

    monkeypatch.setattr(clipart.broll, "_get", get)
    (c,) = clipart.pixabay("cow", 4, cfg)
    assert seen["key"] == "sekrit" and seen["image_type"] == "vector" and "colors" not in seen
    assert c["url"].endswith("big.png") and c["alt"].endswith("w.png") and c["credit"] == "OpenClipart-Vectors on Pixabay"
    assert clipart.licence_code(c["licence"]) == "pixabay" and "sekrit" not in str(c)
    cache = cfg["paths"]["clipart"] / clipart.PIXABAY_CACHE
    assert cache.exists() and "sekrit" not in cache.read_text()
    clipart.resolve("https://pixabay.com/vectors/cow-animal-123/", cfg)
    assert seen["id"] == "123"

    def fails(url, params=None, timeout=20, ua=""):
        raise ClipperError(f"pixabay.com: HTTP 400 {params['key']}")

    monkeypatch.setattr(clipart.broll, "_get", fails)
    assert clipart.pixabay("cow", 4, cfg)[0]["key"] == c["key"]  # the same request within 24 h: from the cache
    with pytest.raises(ClipperError) as e:
        clipart.pixabay("horse", 4, cfg)
    assert "sekrit" not in str(e.value)


def test_pixabay_cache_expires(cfg, monkeypatch, tmp_path):
    (tmp_path / "no_pixabay_key.txt").write_text("k\n")
    calls = []
    monkeypatch.setattr(clipart.broll, "_get", lambda url, params=None, timeout=20, ua="": calls.append(1) or {"hits": []})
    t = [1_000_000.0]
    monkeypatch.setattr(clipart.time, "time", lambda: t[0])
    clipart.pixabay("cow", 4, cfg)
    clipart.pixabay("cow", 4, cfg)
    assert len(calls) == 1
    t[0] += 86400
    clipart.pixabay("cow", 4, cfg)
    assert len(calls) == 2


class FakeResponse:
    def __init__(self, body=b"x" * 10, mime="image/png", length=None, status=200):
        self.body, self.status_code, self.ok = body, status, status < 400
        self.headers = {"Content-Type": mime, **({"Content-Length": str(length)} if length else {})}

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download(tmp_path, monkeypatch):
    import requests

    resp = {}
    monkeypatch.setattr(requests, "get", lambda url, **kw: resp["r"])
    resp["r"] = FakeResponse(mime="image/svg+xml")
    assert REAL_DOWNLOAD("https://api.iconify.design/ph/cow", tmp_path / "a", 1, 5) == tmp_path / "a.svg"
    resp["r"] = FakeResponse(mime="image/jpeg")
    assert REAL_DOWNLOAD("https://x/y.jpeg", tmp_path / "b", 1, 5).name == "b.jpg"
    resp["r"] = FakeResponse(mime="image/webp")  # the server's type wins over the address's
    assert REAL_DOWNLOAD("https://x/y.png", tmp_path / "w", 1, 5).name == "w.webp"
    resp["r"] = FakeResponse(mime="image/x-unknown")
    assert REAL_DOWNLOAD("https://x/y.gif", tmp_path / "u", 1, 5).name == "u.gif"
    resp["r"] = FakeResponse(mime="text/html")
    with pytest.raises(ClipperError, match="not a picture"):
        REAL_DOWNLOAD("https://x/y.png", tmp_path / "c", 1, 5)
    resp["r"] = FakeResponse(length=5_000_000)
    with pytest.raises(clipart.TooBig):
        REAL_DOWNLOAD("https://x/y.png", tmp_path / "d", 1, 5)
    resp["r"] = FakeResponse(body=b"x" * 2_000_000)  # no length given: stopped while reading
    with pytest.raises(clipart.TooBig):
        REAL_DOWNLOAD("https://x/y.png", tmp_path / "e", 1, 5)
    assert not list(tmp_path.glob("e*")) and not list(tmp_path.glob("d*"))
    resp["r"] = FakeResponse(status=404)
    with pytest.raises(ClipperError, match="HTTP 404"):
        REAL_DOWNLOAD("https://x/y.png", tmp_path / "f", 1, 5)

    class Broken(FakeResponse):
        def iter_content(self, size):
            yield b"half"
            raise requests.ConnectionError("reset")

    resp["r"] = Broken()
    with pytest.raises(ClipperError, match="ConnectionError"):
        REAL_DOWNLOAD("https://x/y.png", tmp_path / "g", 1, 5)
    assert not list(tmp_path.glob("g*"))  # no .part left behind


def test_a_folder_skips_a_file_it_cant_copy(cfg, tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    pack.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        (pack / name).write_bytes(b"png")
    real = clipart.shutil.copyfile

    def copy(src, dst):
        if src.endswith("b.png"):
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    monkeypatch.setattr(clipart.shutil, "copyfile", copy)
    said = []
    out = clipart.add(cfg, str(pack), credit="me", licence="CC0", say=said.append)
    assert sorted(e["title"] for e in out) == ["a", "c"]
    assert any("Permission denied" in s for s in said) and "2 pictures added" in said[-1] and "1 failed" in said[-1]


def test_cli_and_doctor(cfg, monkeypatch, capsys):
    from clipper import cli, doctor

    args = cli.build_parser().parse_args(["clipart", "add", "pack", "--credit", "Kenney", "--licence", "CC0",
                                          "--tag", "emote"])
    got = {}
    monkeypatch.setattr(clipart, "add", lambda cfg, *a: got.setdefault("add", a))
    args.fn(cfg, args)
    assert got["add"] == ("pack", ["emote"], "Kenney", "CC0")
    args = cli.build_parser().parse_args(["clipart", "find", "cow", "--source", "met", "-n", "3"])
    monkeypatch.setattr(clipart, "find", lambda cfg, q, sources, n: got.setdefault("find", (q, sources, n)) and 0)
    args.fn(cfg, args)
    assert got["find"] == ("cow", ["met"], 3)

    doctor.RESULTS.clear()
    cfg["clipart"]["sources"] = ["met", "openclipart"]
    cfg["clipart"]["licences"] = ["cc0", "cc-nc"]
    cfg["clipart"]["pixabay_type"] = "photo"
    doctor.check_clipart(cfg)
    fails = [what for status, area, what, hint in doctor.RESULTS if status == "FAIL"]
    assert any("openclipart" in w for w in fails) and any("cc-nc" in w for w in fails)
    assert any("pixabay_type" in w for w in fails)
    doctor.RESULTS.clear()
    cfg["clipart"].update(sources=["pixabay"], licences=["cc0"], pixabay_type="vector")
    doctor.check_clipart(cfg)
    assert [s for s, area, *_ in doctor.RESULTS] == ["WARN", "OK"]
    doctor.RESULTS.clear()


def test_list(cfg, monkeypatch, capsys):
    clipart.show_list(cfg)
    assert "clipper clipart find" in capsys.readouterr().out
    monkeypatch.setitem(clipart.FINDERS, "commons", lambda q, n, cfg: [cand()])
    clipart.find(cfg, "cow", ["commons"], say=lambda s: None)
    clipart.show_list(cfg, tag="co")
    out = capsys.readouterr().out
    assert "Cow" in out and "Public domain" in out
    clipart.show_list(cfg, source="met")
    assert "like that" in capsys.readouterr().out
