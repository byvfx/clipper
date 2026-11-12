import json

import pytest

from clipper import broll, jsonstate
from clipper.errors import ClipperError


@pytest.fixture
def cfg(defaults, tmp_path, monkeypatch):
    defaults["paths"]["broll"] = tmp_path / "broll"

    def encode(cfg, src, dst, start=None, end=None):
        dst.write_bytes(b"mp4")
        encoded.append((src, start, end))

    encoded.clear()
    monkeypatch.setattr(broll, "encode", encode)
    monkeypatch.setattr("clipper.media.try_duration", lambda p, video=False: None)
    return defaults


encoded: list = []


def cand(key="https://images.nasa.gov/details/a", source="commons", **kw):
    return {"key": key, "source": source, "title": "Storm", "page": key, "url": key + ".mp4", "date": "2023-03-17",
            "credit": "NASA", "licence": "Public domain", "description": "", "duration": 30.0, **kw}


def test_licence_codes():
    assert broll.licence_code("Public domain") == "pd"
    assert broll.licence_code("PD-USGov-NASA") == "pd"
    assert broll.licence_code("http://creativecommons.org/licenses/publicdomain/") == "pd"
    assert broll.licence_code("http://creativecommons.org/publicdomain/zero/1.0/") == "cc0"
    assert broll.licence_code("CC0") == "cc0"
    assert broll.licence_code("CC BY 4.0") == "cc-by"
    assert broll.licence_code("http://creativecommons.org/licenses/by/3.0/") == "cc-by"
    assert broll.licence_code("CC BY-SA 3.0") == "cc-by-sa"
    assert broll.licence_code("") == "other"
    assert broll.licence_code("http://creativecommons.org/licenses/by-nc/3.0/") == "other"


def test_refused(cfg):
    assert broll.refused(cand(), cfg) == ""
    assert "licence" in broll.refused(cand(licence="CC BY 4.0"), cfg)
    assert "not stated" in broll.refused(cand(licence=""), cfg)
    cfg["broll"]["licences"] = ["pd", "cc0", "cc-by"]
    assert broll.refused(cand(licence="CC BY 4.0"), cfg) == ""
    assert "©" in broll.refused(cand(description="Footage © 2019 Some Studio"), cfg)
    assert "copyright" in broll.refused(cand(credit="Copyright ESA"), cfg).lower()
    assert broll.refused(cand(description="This video is not subject to copyright."), cfg) == ""


def test_music_only_matters_with_sound(cfg):
    c = cand(source="nasa", description="Music: Production Music")
    assert broll.refused(c, cfg) == ""
    cfg["broll"]["audio"] = True
    assert "music" in broll.refused(c, cfg)


def test_seconds():
    assert broll.seconds("0:01:55") == 115
    assert broll.seconds("1:55") == 115
    assert broll.seconds("115.2 s") == 115.2
    assert broll.seconds("1345.85") == 1345.85
    assert broll.seconds(82.4) == 82.4
    assert broll.seconds("") is None and broll.seconds("soon") is None


def test_clip_id_is_stable_and_per_range():
    a = broll.clip_id("nasa", "k")
    assert a == broll.clip_id("nasa", "k") and a.startswith("nasa-")
    assert broll.clip_id("nasa", "k", 10, 20) not in (a, broll.clip_id("nasa", "k", 10, 30))


def test_find_adds_skips_and_lists_long(cfg, monkeypatch):
    found = [cand("https://c/1"), cand("https://c/2", licence="CC BY-NC"), cand("https://c/3", duration=900.0)]
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [dict(c) for c in found])
    said = []
    assert broll.find(cfg, "hurricane", ["commons"], say=said.append) == 2
    lib = broll.load(cfg)
    by_key = {e["key"]: e for e in lib["clips"].values()}
    assert by_key["https://c/1"]["status"] == "new" and by_key["https://c/1"]["tags"] == ["hurricane"]
    assert (cfg["paths"]["broll"] / by_key["https://c/1"]["file"]).exists()
    assert by_key["https://c/3"]["status"] == "long" and by_key["https://c/3"]["file"] == ""
    assert "https://c/2" not in by_key and any("skip" in s for s in said)
    assert len(encoded) == 1
    # again: nothing new, the old clip picks up the new search as a tag
    assert broll.find(cfg, "storm", ["commons"], say=said.append) == 0
    assert broll.load(cfg)["clips"][by_key["https://c/1"]["id"]]["tags"] == ["hurricane", "storm"]


def test_a_failing_source_doesnt_stop_the_others(cfg, monkeypatch):
    def down(q, n, cfg=None):
        raise ClipperError("archive.org: HTTP 503")

    monkeypatch.setitem(broll.FINDERS, "prelinger", down)
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [cand()])
    said = []
    assert broll.find(cfg, "x", ["prelinger", "commons"], say=said.append) == 1
    assert "prelinger: archive.org: HTTP 503" in said


def test_drop_remembers_the_source(cfg, monkeypatch):
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [cand()])
    broll.find(cfg, "x", ["commons"], say=lambda s: None)
    (cid,) = broll.load(cfg)["clips"]
    with pytest.raises(ClipperError, match="no clip"):
        broll.set_status(cfg, [cid, "nope"], "drop")
    assert (cfg["paths"]["broll"] / f"{cid}.mp4").exists()  # a bad id deletes nothing
    broll.set_status(cfg, [cid], "drop")
    assert not (cfg["paths"]["broll"] / f"{cid}.mp4").exists()
    assert broll.find(cfg, "x", ["commons"], say=lambda s: None) == 0


def test_keep_and_long(cfg, monkeypatch):
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [cand(), cand("https://c/long", duration=600.0)])
    broll.find(cfg, "x", ["commons"], say=lambda s: None)
    lib = broll.load(cfg)["clips"]
    short = next(i for i, e in lib.items() if e["status"] == "new")
    long_ = next(i for i, e in lib.items() if e["status"] == "long")
    broll.set_status(cfg, [short], "keep")
    assert broll.load(cfg)["clips"][short]["status"] == "kept"
    with pytest.raises(ClipperError, match="long"):
        broll.set_status(cfg, [long_], "keep")


def test_add_a_part_of_a_long_clip(cfg, monkeypatch):
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [cand("https://c/long", duration=600.0)])
    broll.find(cfg, "parade", ["commons"], say=lambda s: None)
    (lid,) = broll.load(cfg)["clips"]
    e = broll.add(cfg, lid, 80.0, 105.0, ["march"], say=lambda s: None)
    assert e["id"] != lid and e["start"] == 80 and e["end"] == 105 and e["duration"] == 25
    assert e["tags"] == ["march", "parade"]
    assert encoded[-1] == ("https://c/long.mp4", 80.0, 105.0)
    part = broll.add(cfg, e["id"], 5.0, 10.0, say=lambda s: None)  # a part of the part: the source's times
    assert (part["start"], part["end"]) == (85.0, 90.0)
    with pytest.raises(ClipperError, match="after"):
        broll.add(cfg, lid, 50.0, 40.0, say=lambda s: None)
    with pytest.raises(ClipperError, match="already"):
        broll.add(cfg, e["id"], say=lambda s: None)
    broll.set_status(cfg, [e["id"]], "drop")  # dropping a part leaves the film findable
    assert broll.load(cfg)["dropped"] == []


def test_add_a_file_needs_a_credit(cfg, tmp_path):
    f = tmp_path / "dvids.mp4"
    f.write_bytes(b"x")
    with pytest.raises(ClipperError, match="--credit"):
        broll.add(cfg, str(f), say=lambda s: None)
    e = broll.add(cfg, str(f), credit="U.S. Navy video by MC2 Example", say=lambda s: None)
    assert e["source"] == "file" and e["licence"] == "public domain"
    with pytest.raises(ClipperError, match="licence"):
        broll.add(cfg, str(f), credit="someone", licence="all rights reserved", say=lambda s: None)


def test_add_refuses_an_unknown_address(cfg):
    with pytest.raises(ClipperError, match="images.nasa.gov"):
        broll.add(cfg, "https://example.com/video", say=lambda s: None)


def test_list_filters_and_json(cfg, monkeypatch, capsys):
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [cand()])
    broll.find(cfg, "hurricane", ["commons"], say=lambda s: None)
    capsys.readouterr()
    broll.show_list(cfg, as_json=True)
    (e,) = json.loads(capsys.readouterr().out)
    assert e["path"].endswith(e["file"]) and e["credit"] == "NASA"
    assert broll.select(broll.load(cfg), tag="hurr") and not broll.select(broll.load(cfg), tag="parade")
    assert not broll.select(broll.load(cfg), status="kept")


def test_sources_parse_their_apis(monkeypatch):
    nasa_js = {"collection": {"items": [{"href": "http://x/collection.json", "data": [
        {"nasa_id": "A1", "media_type": "video", "title": "Freddy", "date_created": "2023-03-17T00:00:00Z",
         "center": "GSFC", "description": "Cyclone"}]}, {"href": "h", "data": [{"nasa_id": "B", "media_type": "image"}]}]}}
    commons_js = {"query": {"pages": {"1": {"index": 1, "title": "File:Freddy.webm", "imageinfo": [{
        "mime": "video/webm", "url": "https://upload.wikimedia.org/f.webm?utm_source=x", "duration": 82.4,
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Freddy.webm",
        "extmetadata": {"LicenseShortName": {"value": "Public domain"}, "Artist": {"value": "<a>NASA</a>"}}}]},
        "2": {"index": 2, "imageinfo": [{"mime": "image/png", "url": "u"}]}}}}

    def get(url, params=None, timeout=20):
        if "nasa" in url:
            return nasa_js
        if "advancedsearch" in url:
            return {"response": {"docs": [{"identifier": "ToEnrichMank"}]}}
        if "archive.org/metadata" in url:
            return {"metadata": {"title": "To Enrich Mankind", "date": "1952", "licenseurl":
                                 "http://creativecommons.org/licenses/publicdomain/"},
                    "files": [{"name": "a.ogv"}, {"name": "a.mp4", "height": "480", "length": "1345.85"},
                              {"name": "a_512kb.mp4", "height": "240"}]}
        return commons_js

    monkeypatch.setattr(broll, "_get", get)
    (n,) = broll.nasa("cyclone", 2)
    assert n["page"] == "https://images.nasa.gov/details/A1" and n["credit"] == "NASA GSFC" and n["date"] == "2023-03-17"
    (p,) = broll.prelinger("office", 2)
    assert p["url"].endswith("/ToEnrichMank/a.mp4") and p["duration"] == 1345.85
    assert broll.licence_code(p["licence"]) == "pd"
    (c,) = broll.commons("cyclone", 2)
    assert c["url"] == "https://upload.wikimedia.org/f.webm" and c["credit"] == "NASA" and c["duration"] == 82.4


def test_index_is_jsonstate(cfg, monkeypatch):
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [cand()])
    broll.find(cfg, "x", ["commons"], say=lambda s: None)
    assert jsonstate.load(cfg["paths"]["broll"] / broll.INDEX)["clips"]


def test_dvids_key_env_then_first_line(tmp_path, monkeypatch):
    monkeypatch.delenv("DVIDS_API_KEY", raising=False)
    f = tmp_path / "dvids_secret.txt"
    cfg = {"paths": {"dvids_key": f}}
    assert broll.dvids_key(cfg) == ""
    f.write_text("key-public\r\nprivate\n", encoding="utf-8")
    assert broll.dvids_key(cfg) == "key-public"
    monkeypatch.setenv("DVIDS_API_KEY", "key-env")
    assert broll.dvids_key(cfg) == "key-env"


DVIDS_FILES = [{"src": "https://cf/v.mp4", "type": "video/mp4", "height": 2160, "size": 45},
               {"src": "https://cf/v-1080-9000k.mp4", "type": "video/mp4", "height": 1080, "size": 34},
               {"src": "https://cf/v-1080-6000k.mp4", "type": "video/mp4", "height": 1080, "size": 22},
               {"src": "https://cf/v-720.mp4", "type": "video/mp4", "height": 720, "size": 11}]


def test_dvids_search_asset_and_file_pick(cfg, monkeypatch):
    monkeypatch.setenv("DVIDS_API_KEY", "key-secret")
    calls = []

    def get(url, params=None, timeout=20):
        calls.append((url, dict(params or {})))
        if url.endswith("/search"):
            return {"results": [{"id": "video:1029172", "type": "video", "title": "Preflight checks",
                                 "credit": "SPC Leonardo A Martinez Tovar", "unit_name": "25th Infantry Division",
                                 "branch": "Army", "duration": 38, "date": "2026-11-08T06:00:00Z",
                                 "url": "https://www.dvidshub.net/video/1029172/preflight",
                                 "hls_url": "https://api.dvidshub.net/hls/video/1029172.m3u8?api_key=key-secret"},
                                {"id": "image:1", "type": "image"}]}
        return {"results": {"id": "video:1029172", "type": "video", "title": "Preflight checks", "files": DVIDS_FILES,
                            "credit": [{"name": "Leonardo A Martinez Tovar", "rank": "Spc."}], "branch": "Army",
                            "url": "https://www.dvidshub.net/video/1029172/preflight", "duration": 38}}

    monkeypatch.setattr(broll, "_get", get)
    (c,) = broll.dvids("helicopter", 2, cfg)
    assert calls[0][1]["category"] == "B-Roll" and c["duration"] == 38
    assert c["credit"] == "SPC Leonardo A Martinez Tovar, 25th Infantry Division (Army), via DVIDS"
    broll.media(c, cfg)
    assert c["url"] == "https://cf/v-1080-6000k.mp4"  # the smallest that's 1080 tall
    assert calls[-1][1]["id"] == "video:1029172"
    cfg["broll"]["max_height"] = 4320
    c2 = broll.resolve("https://www.dvidshub.net/video/1029172/preflight", cfg)
    assert c2["credit"] == "Spc. Leonardo A Martinez Tovar (Army), via DVIDS"
    broll.media(c2, cfg)
    assert c2["url"] == "https://cf/v.mp4"  # none that tall: the tallest
    cfg["broll"]["max_height"] = 1080
    monkeypatch.setattr(broll, "dvids", lambda q, n, cfg=None, asset="": [
        {**c, "url": ""}] if not asset else [{"files": DVIDS_FILES}])
    monkeypatch.setitem(broll.FINDERS, "dvids", broll.dvids)
    broll.find(cfg, "helicopter", ["dvids"], say=lambda s: None)
    text = (cfg["paths"]["broll"] / broll.INDEX).read_text(encoding="utf-8")
    assert "key-secret" not in text and "api_key" not in text


def test_dvids_without_a_key_is_skipped(cfg, monkeypatch):
    monkeypatch.delenv("DVIDS_API_KEY", raising=False)
    cfg["paths"]["dvids_key"] = cfg["paths"]["broll"] / "missing.txt"
    monkeypatch.setitem(broll.FINDERS, "commons", lambda q, n, cfg=None: [cand()])
    said = []
    assert broll.find(cfg, "x", ["dvids", "commons"], say=said.append) == 1
    assert any(s.startswith("dvids: no $DVIDS_API_KEY") for s in said)
