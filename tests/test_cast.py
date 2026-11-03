import io

import numpy as np
import pytest
from PIL import Image

from clipper import cast, jsonstate, warroom
from clipper.errors import ClipperError
from clipper.thumbs import photos


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


RNG = np.random.default_rng(4)
JER, DON = unit(RNG.normal(size=128)), unit(RNG.normal(size=128))


def near(v, scale=0.25, seed=0):
    return unit(v + np.random.default_rng(seed).normal(scale=scale / np.sqrt(128), size=128))  # |noise| ~ scale


@pytest.fixture
def cfg(defaults, tmp_path):
    defaults["thumbnail"]["photos"] = str(tmp_path / "photos")
    defaults["paths"]["faces"] = tmp_path / "faces"
    (tmp_path / "photos").mkdir()
    return defaults


def known(cfg, **people):
    cfg["paths"]["faces"].mkdir(exist_ok=True)
    jsonstate.write(cfg["paths"]["faces"] / "known.json",
                    {"model": "yunet-2023mar+sface-2021dec", "people": {n: [v.tolist()] for n, v in people.items()}})


def jpeg(w=800, h=600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 90, 90)).save(buf, "JPEG")
    return buf.getvalue()


def face(emb, box=(0.4, 0.3, 0.15, 0.2), px=120):
    return ({"box": list(box), "px": px, "sharp": 50.0, "frontal": 0.9}, emb)


def test_head_crop_keeps_head_and_shoulders_inside():
    l, t, r, b = cast.head_crop((1920, 1080), [900, 300, 120, 150])
    assert 0 <= l < 900 and r > 1020 and t < 300 - 60 and b > 450 + 150 and b <= 1080
    assert 0.7 < (r - l) / (b - t) < 0.9  # about 4:5
    l, t, r, b = cast.head_crop((1920, 1080), [10, 5, 200, 250])  # a face at the corner
    assert (l, t) == (0, 0) and r <= 1920 and b <= 1080


def test_choose_face_by_the_known_face():
    people = {"Jer": [JER.tolist()], "Don": [DON.tolist()]}
    f, score, why = cast.choose_face([face(DON, px=300), face(near(JER), px=100)], "jer", people, 0.5, 0.7)
    assert f["px"] == 100 and score > 0.5 and why == ""                # Jer's face, not the bigger one
    f, score, why = cast.choose_face([face(DON)], "Jer", people, 0.5, 0.7)
    assert f is None and why.startswith("no face like Jer's")
    stranger = unit(RNG.normal(size=128))
    f, _, _ = cast.choose_face([face(stranger, px=90), face(unit(RNG.normal(size=128)), px=200)], "Hasan", people, 0.5, 0.7)
    assert f["px"] == 200                                                # not enrolled: the biggest
    f, _, why = cast.choose_face([face(near(DON), px=200)], "Hasan", people, 0.5, 0.7)
    assert f is None and "looks like don" in why
    assert cast.choose_face([], "Jer", people, 0.5, 0.7)[2] == "no face big enough"


def test_add_crops_records_and_skips_repeats(cfg, monkeypatch):
    known(cfg, Jer=JER)
    found = [[face(near(JER, seed=1))], [face(near(JER, scale=0.01, seed=2))], [face(near(JER, scale=3, seed=3))]]
    monkeypatch.setattr(cast, "faces_in", lambda img, cfg: found.pop(0))
    said = []
    pid = cast.add(cfg, "Jer", jpeg(), {"key": "u1", "kind": "web", "url": "https://x/1.jpg"}, say=said.append)
    d = cast.person_dir(cfg, "Jer")
    ph = jsonstate.load(d / "cast.json")["photos"][pid]
    assert ph["status"] == "new" and ph["source"] == "web" and len(ph["emb"]) == 128
    assert 0 < ph["face"]["x"] < 1 and 0 < ph["face"]["height"] < 1 and (d / ph["file"]).exists()
    w, h = Image.open(d / ph["file"]).size
    assert 0.7 < w / h < 0.9
    assert cast.add(cfg, "Jer", jpeg(), {"key": "u1", "kind": "web"}) is None  # the same source
    assert cast.add(cfg, "Jer", jpeg(), {"key": "u2", "kind": "web"}, say=said.append) is None
    assert said[-1].endswith(f"the same photo as {pid}")                         # a copy of it
    assert cast.add(cfg, "Jer", jpeg(), {"key": "u3", "kind": "web"}, say=said.append) is None
    assert "no face like Jer's" in said[-1]
    assert "emb" not in cast.overview(cfg, "Jer")["person"]["photos"][0]


def test_keep_drop_label_and_the_best_photo(cfg, monkeypatch):
    monkeypatch.setattr(cast, "faces_in", lambda img, cfg: [face(unit(RNG.normal(size=128)), px=150 + len(img.tobytes()) % 7)])
    ids = [cast.add(cfg, "Hasan Piker", jpeg(), {"key": f"k{i}", "kind": "commons"}) for i in range(3)]
    d = cast.person_dir(cfg, "Hasan Piker")
    assert cast.best_photo(d) is None                                  # nothing kept yet
    cast.set_status(cfg, "Hasan Piker", ids[:2], "keep")
    cast.set_label(cfg, "Hasan Piker", ids[1], "angry")
    cast.set_label(cfg, "Hasan Piker", ids[0], "neutral")
    assert cast.best_photo(d).name == f"{ids[0]}.jpg"                  # neutral by default
    assert cast.best_photo(d, "angry").name == f"{ids[1]}.jpg"
    cast.set_status(cfg, "Hasan Piker", [ids[2]], "drop")
    idx = jsonstate.load(d / "cast.json")
    assert ids[2] not in idx["photos"] and idx["dropped"] == ["k2"] and not (d / f"{ids[2]}.jpg").exists()
    assert cast.add(cfg, "Hasan Piker", jpeg(), {"key": "k2", "kind": "commons"}) is None  # dropped stays dropped
    with pytest.raises(ClipperError, match="label 'sexy'"):
        cast.set_label(cfg, "Hasan Piker", ids[0], "sexy")
    with pytest.raises(ClipperError, match="no photo"):
        cast.set_status(cfg, "Hasan Piker", ["0000000000"], "keep")


def test_photos_takes_a_folders_kept_photo_over_a_single_file(cfg, monkeypatch):
    root = cast.root(cfg)
    Image.new("RGB", (10, 10)).save(root / "Hasan Piker.jpg")
    Image.new("RGB", (10, 10)).save(root / "Jer.jpg")
    monkeypatch.setattr(cast, "faces_in", lambda img, cfg: [face(unit(RNG.normal(size=128)))])
    pid = cast.add(cfg, "Jer", jpeg(), {"key": "a", "kind": "stream"})
    tc = cfg["thumbnail"]
    assert photos(tc)["Jer"].name == "Jer.jpg"                         # new photos don't count yet
    cast.set_status(cfg, "Jer", [pid], "keep")
    got = photos(tc)
    assert got["Jer"].name == f"{pid}.jpg" and got["Hasan Piker"].name == "Hasan Piker.jpg"
    people = {p["name"]: p for p in cast.overview(cfg)["people"]}
    assert people["Jer"]["kept"] == 2 and people["Hasan Piker"]["kept"] == 1  # the folder's and the file


def test_names_channels_and_agencies(cfg):
    for bad in ("../x", "a/b", "C:x", ".hidden"):
        with pytest.raises(ClipperError):
            cast.person_dir(cfg, bad)
    cast.set_info(cfg, "Jer", ["The Quartering"], ["https://www.youtube.com/@TheQuartering", "https://kick.com/jer"])
    assert cast.load(cast.person_dir(cfg, "Jer"))["channels"][1] == "https://kick.com/jer"
    with pytest.raises(ClipperError, match="youtube.com/@name"):
        cast.set_info(cfg, "Jer", None, ["http://evil.example/x"])
    assert cast.blocked("media.gettyimages.com", cfg) and cast.blocked("ap.org", cfg)
    assert not cast.blocked("snap.org", cfg) and not cast.blocked("upload.wikimedia.org", cfg)
    with pytest.raises(ClipperError, match="photo agency"):
        cast.fetch("https://media.gettyimages.com/id/1.jpg", cfg)
    with pytest.raises(ClipperError, match="not a web address"):
        cast.fetch("file:///etc/passwd", cfg)


def test_commons_pages_keep_licence_and_author():
    js = {"query": {"pages": {"1": {"imageinfo": [{
        "url": "https://upload.wikimedia.org/a.jpg", "thumburl": "https://upload.wikimedia.org/a_1600.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:A.jpg", "mime": "image/jpeg",
        "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}, "Artist": {"value": '<a href="x">Gage Skidmore</a>'}}}]},
        "2": {"imageinfo": [{"url": "https://upload.wikimedia.org/b.pdf", "mime": "application/pdf"}]}}}}
    [p] = cast._commons_pages(js)
    assert p == {"key": "https://commons.wikimedia.org/wiki/File:A.jpg", "kind": "commons",
                 "url": "https://upload.wikimedia.org/a_1600.jpg", "page": "https://commons.wikimedia.org/wiki/File:A.jpg",
                 "licence": "CC BY-SA 4.0", "author": "Gage Skidmore"}


def test_war_room_cast_refuses_bad_requests(cfg):
    with pytest.raises(ClipperError, match="keep or drop"):
        warroom.cast_mark(cfg, {"person": "Jer", "ids": ["../../x"], "status": "keep"})
    with pytest.raises(ClipperError, match="web address"):
        warroom.cast_run(cfg, {"person": "Jer", "what": "add", "url": "C:/secrets/x.txt"})
    with pytest.raises(ClipperError, match="find, add or grab"):
        warroom.cast_run(cfg, {"person": "Jer", "what": "find", "sources": ["google"]})
    with pytest.raises(ClipperError, match="lists of text"):
        warroom.cast_set(cfg, {"person": "Jer", "aliases": "Jer"})
    assert not warroom.CAST_JOB["running"]


def test_a_bad_id_drops_nothing(cfg, monkeypatch):
    monkeypatch.setattr(cast, "faces_in", lambda img, cfg: [face(unit(RNG.normal(size=128)))])
    pid = cast.add(cfg, "Don", jpeg(), {"key": "k", "kind": "stream"})
    with pytest.raises(ClipperError, match="no photo 0000000000"):
        cast.set_status(cfg, "Don", [pid, "0000000000"], "drop")
    d = cast.person_dir(cfg, "Don")
    assert pid in jsonstate.load(d / "cast.json")["photos"] and (d / f"{pid}.jpg").exists()
    cast.set_status(cfg, "Don", [pid, pid], "drop")                    # twice in one call is once
    assert jsonstate.load(d / "cast.json")["dropped"] == ["k"]


def test_a_streams_named_face_is_the_one_taken(cfg, monkeypatch):
    other, mine = unit(RNG.normal(size=128)), unit(RNG.normal(size=128))
    monkeypatch.setattr(cast, "faces_in", lambda img, cfg: [face(other, box=(0.1, 0.1, 0.2, 0.3), px=300),
                                                            face(mine, box=(0.6, 0.2, 0.1, 0.15), px=110)])
    pid = cast.add(cfg, "Rich", jpeg(), {"key": "s@1", "kind": "stream"}, box=[0.59, 0.19, 0.12, 0.17])
    ph = jsonstate.load(cast.person_dir(cfg, "Rich") / "cast.json")["photos"][pid]
    assert ph["px"] == 110                                             # not the bigger face beside him
    said = []
    assert cast.add(cfg, "Rich", jpeg(), {"key": "s@2", "kind": "stream"}, say=said.append,
                    box=[0.0, 0.8, 0.1, 0.1]) is None and "no face big enough" in said[-1]


def test_files_are_keyed_by_content(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(cast, "faces_in", lambda img, cfg: [face(unit(RNG.normal(size=128)))])
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    (tmp_path / "a" / "photo.jpg").write_bytes(jpeg(800, 600))
    (tmp_path / "b" / "photo.jpg").write_bytes(jpeg(900, 600))
    one = cast.add_file(cfg, "Geezer", str(tmp_path / "a" / "photo.jpg"))
    two = cast.add_file(cfg, "Geezer", str(tmp_path / "b" / "photo.jpg"))
    assert one and two and one != two
    assert cast.add_file(cfg, "Geezer", str(tmp_path / "a" / "photo.jpg")) is None  # the same file again


class Raw(io.BytesIO):
    def read(self, n=-1, decode_content=False):  # urllib3's raw stream
        return super().read(n)


class Resp:
    def __init__(self, status=200, location="", body=b"", js=None):
        self.status_code, self.headers, self._js = status, {"Location": location} if location else {}, js
        self.is_redirect, self.ok = bool(location), status < 400
        self.raw = Raw(body)

    def json(self):
        if self._js is None:
            raise ValueError("not JSON")
        return self._js

    def close(self):
        pass


def test_a_redirect_to_an_agency_is_refused(cfg, monkeypatch):
    import requests

    hops = {"https://img.example/a.jpg": Resp(302, "https://media.gettyimages.com/x.jpg"),
            "https://img.example/b.jpg": Resp(301, "/c.jpg"), "https://img.example/c.jpg": Resp(body=b"jpeg")}
    seen = []

    def get(url, **kw):
        assert kw["allow_redirects"] is False
        seen.append(url)
        return hops[url]
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(ClipperError, match="gettyimages.com is a photo agency"):
        cast.fetch("https://img.example/a.jpg", cfg)
    assert seen == ["https://img.example/a.jpg"]                       # never asked for
    assert cast.fetch("https://img.example/b.jpg", cfg) == b"jpeg"


def test_one_failing_channel_doesnt_stop_the_rest(cfg, monkeypatch):
    import requests

    def get(url, **kw):
        if "deadchannel" in url:
            raise requests.ConnectionError("down")
        return Resp(js={"user": {"profile_pic": "https://files.kick.com/pic.jpg"}})
    monkeypatch.setattr(requests, "get", get)
    said = []
    got = cast.channel_pictures(cfg, "Don", {"channels": ["https://kick.com/deadchannel", "https://kick.com/donflurgundy"]},
                                said.append)
    assert [g["url"] for g in got] == ["https://files.kick.com/pic.jpg"] and "ConnectionError" in said[0]


def test_labelled_photos_for_covers_and_known_figures(cfg, monkeypatch):
    from clipper.prompts import figures
    from clipper.thumbs.frames import photo_file, photo_labels

    monkeypatch.setattr(cast, "faces_in", lambda img, cfg: [face(unit(RNG.normal(size=128)), px=150)])
    kinds = ("web", "stream", "commons")                               # "web": image search, a pasted URL
    ids = [cast.add(cfg, "Jer", jpeg(), {"key": f"k{i}", "kind": k}) for i, k in enumerate(kinds)]
    cast.set_status(cfg, "Jer", ids, "keep")
    for pid, label in zip(ids, ("angry", "angry", "laughing"), strict=True):
        cast.set_label(cfg, "Jer", pid, label)
    d = cast.person_dir(cfg, "Jer")
    assert cast.best_photo(d, "angry").name == f"{ids[1]}.jpg"        # a stream photo before a web one
    assert cast.labels(d) == ["angry", "laughing"]
    Image.new("RGB", (10, 10)).save(cast.root(cfg) / "Hasan Piker.jpg")
    tc = {**cfg["thumbnail"]}
    tc["_photos"] = photos(tc)
    assert photo_labels(tc) == {"Hasan Piker": [], "Jer": ["angry", "laughing"]}
    assert photo_file(tc, "jer", "laughing").name == f"{ids[2]}.jpg"
    assert photo_file(tc, "Jer", "sad") == tc["_photos"]["Jer"]          # no such label: their best
    assert photo_file(tc, "Hasan Piker", "angry").name == "Hasan Piker.jpg"
    assert photo_file(tc, "Nobody", "angry") is None
    from clipper.thumbs.editor import _clean_changes  # the war room's cover editor
    e = {"frames": [{}], "photo": "Jer", "photo_label": "angry"}
    assert _clean_changes(cfg, e, {"photo_label": "laughing"}) == {"photo_label": "laughing"}
    assert _clean_changes(cfg, e, {"photo": "hasan piker"}) == {"photo": "Hasan Piker", "photo_label": ""}
    with pytest.raises(ClipperError, match="no 'angry' photo of Hasan Piker"):
        _clean_changes(cfg, e, {"photo": "Hasan Piker", "photo_label": "angry"})

    cast.set_info(cfg, "Jer", ["The Quartering"], None)
    cast.person_dir(cfg, "Victor").mkdir()                              # no kept photo: not a figure
    assert cast.figures(cfg) == [("Hasan Piker", []), ("Jer", ["The Quartering"])]
    assert cast.known_figures("DSP, The Quartering", cast.figures(cfg)) == "DSP, The Quartering, Hasan Piker"
    assert cast.known_figures("", [("Jer", ["The Quartering"])]) == "Jer (The Quartering)"
    cfg["scoring"]["known_figures"] = "Jer (The Quartering), Blade"
    assert figures(cfg) == "Jer (The Quartering), Blade, Hasan Piker"
    cfg["cast"]["known_figures"] = False
    assert figures(cfg) == "Jer (The Quartering), Blade"
    cfg["cast"]["known_figures"], cfg["thumbnail"]["photos"] = True, ""
    assert figures(cfg) == "Jer (The Quartering), Blade" and cast.figures(cfg) == []
