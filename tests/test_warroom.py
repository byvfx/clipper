import http.client
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

from clipper import warroom
from clipper.errors import ClipperError


@pytest.fixture
def serve(defaults):
    servers = []

    def start(token: str) -> int:
        cfg = {**defaults, "warroom": {**defaults["warroom"], "token": token}}
        srv = ThreadingHTTPServer(("127.0.0.1", 0), warroom.make_handler(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]
    yield start
    for srv in servers:
        srv.shutdown()


def status(port: int, path: str, host: str, key: str = "", method: str = "GET") -> int:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
    if key:
        headers["X-Key"] = key
    c.request(method, path, body="{}" if method == "POST" else None, headers=headers)
    return c.getresponse().status


def test_without_a_token_only_local_hosts(serve):
    """B13: a DNS-rebinding page has Host: evil.example though it reaches 127.0.0.1."""
    port = serve("")
    assert status(port, "/api/nope", "evil.example") == 403
    assert status(port, "/api/nope", "evil.example", method="POST") == 403
    assert status(port, "/api/nope", f"localhost:{port}") == 404   # reached the router
    assert status(port, "/api/nope", "127.0.0.1") == 404


def test_with_a_token_any_host_with_the_key(serve):
    """The tailnet name (phone via tailscale serve) works once the key is right."""
    port = serve("t0ken")
    assert status(port, "/api/nope", "laptop.tail1234.ts.net", key="t0ken") == 404
    assert status(port, "/api/nope", "laptop.tail1234.ts.net") == 401


def test_routes_are_tables():
    assert "/api/items" in warroom.GET_API and "/api/action" in warroom.POST_API
    assert set(warroom.POST_API) >= {"/api/undo", "/api/bulk", "/api/replan", "/api/retake"}


def test_a_refused_post_still_gets_its_answer(serve, monkeypatch):
    """The body is read before any refusal (an unread body resets the connection on
    Windows), and one past MAX_POST isn't read at all."""
    port = serve("")
    for _ in range(20):
        assert status(port, "/api/nope", "evil.example", method="POST") == 403
    monkeypatch.setattr(warroom, "MAX_POST", 1)
    assert status(port, "/api/nope", "localhost", method="POST") == 413


def test_saved_layouts(tmp_path):
    cfg = {"paths": {"warroom_layouts": tmp_path / "logs" / "layouts.json"}}
    assert warroom.layouts(cfg) == {"layouts": {}}
    lay = {"zones": {"top": ["schedule"], "left": ["pipeline"], "middle": ["queue"]}, "widths": {"left": 300}}
    assert warroom.save_layout(cfg, {"name": "Mine", "layout": lay})["layouts"]["Mine"] == lay
    assert warroom.layouts(cfg)["layouts"]["Mine"] == lay
    for bad in ({"zones": {"sideways": []}}, {"zones": {"top": "schedule"}}, {"zones": {"top": ['"]']}},
                {"zones": {"top": []}, "widths": {"left": 5000}}):
        with pytest.raises(ClipperError, match="not a layout"):
            warroom.save_layout(cfg, {"name": "Bad", "layout": bad})
    with pytest.raises(ClipperError, match="name"):
        warroom.save_layout(cfg, {"name": " ", "layout": lay})
    assert warroom.save_layout(cfg, {"name": "Mine", "delete": True}) == {"layouts": {}}


def test_page_code_is_served_without_the_key(serve):
    """The stylesheet and scripts hold no secrets; they're fetched fresh so a restart's
    page never runs old code. Nothing outside web/ and no other file type is served."""
    port = serve("t0ken")
    for path, ctype in (("/warroom.css", "text/css"), ("/js/app.js", "text/javascript")):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith(ctype)
        assert r.getheader("Cache-Control") == "no-cache"
    for path in ("/js/../../warroom.py", "/../config.py", "/js/nope.js", "/warroom.html"):
        assert status(port, path, "localhost") == 401   # not a page file: the key is still asked


def test_the_page_loads_every_script_app_last():
    html = warroom.PAGE.read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="js/([\w-]+\.js)"></script>', html)
    assert sorted(scripts) == sorted(p.name for p in (warroom.WEB / "js").glob("*.js"))
    assert scripts[-1] == "app.js" and '<link rel="stylesheet" href="warroom.css">' in html
    assert "<script>" not in html and "<style>" not in html


def test_faces_window(tmp_path, defaults, monkeypatch):
    import numpy as np

    from clipper import faces, jsonstate

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "faces": str(tmp_path / "known")}}
    monkeypatch.setattr(warroom, "for_stream", lambda c, sdir: cfg)
    sdir = tmp_path / "kick-1"
    (sdir / "faces").mkdir(parents=True)
    jsonstate.write(sdir / "meta.json", {"channel": "x"})
    with pytest.raises(ClipperError):
        warroom.faces_get(cfg, "kick-2")
    with pytest.raises(ClipperError):
        warroom.faces_get(cfg, "kick-1/c01")
    assert warroom.faces_get(cfg, "kick-1")["scanned"] is False
    rng = np.random.default_rng(0)
    emb = np.array([faces.unit(c + rng.normal(scale=0.2, size=128)) for c in rng.normal(size=(2, 128)) for _ in range(5)])
    rows = [{"t": float(k), "box": [0.4, 0.3, 0.1, 0.2], "px": 90, "sharp": 50.0, "frontal": 0.9} for k in range(10)]
    groups, fs = faces.build_groups(rows, emb, {"cluster": 0.4, "min_faces": 3})
    jsonstate.write(sdir / "faces.json", {"model": faces.MODEL, "frames": 10, "groups": groups, "faces": fs})
    (sdir / "faces" / "F01.jpg").write_bytes(b"jpg")
    got = warroom.faces_get(cfg, "kick-1")
    assert [g["key"] for g in got["groups"]] == ["F01"] and got["more"] == 1
    assert got["groups"][0]["sheet_url"].startswith("/media/kick-1/faces/F01.jpg?v=")
    with pytest.raises(ClipperError):
        warroom.faces_name(cfg, {"stream": "kick-1", "names": {"../x": "Jer"}})
    got = warroom.faces_name(cfg, {"stream": "kick-1", "names": {"F02": "Jer"}, "enroll": True})
    assert got["known"] == {"Jer": 1} and [g["key"] for g in got["groups"]] == ["F01", "F02"]
    assert got["groups"][1]["name"] == "Jer"
    got = warroom.faces_forget(cfg, {"stream": "kick-1", "name": "Jer"})
    assert got["known"] == {} and got["groups"][1]["by"] == "hand"  # a hand name stays
    for bad in ({}, {"x": 0, "y": 0, "width": 0, "height": 0.5}, {"x": 1.2, "y": 0, "width": 0.1, "height": 0.1}):
        with pytest.raises(ClipperError):
            warroom.checked_box(bad)
    assert warroom.checked_box({"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}) == [0.1, 0.2, 0.3, 0.4]


def test_face_pick_grabs_the_cut_or_a_compilations_preview(tmp_path, defaults, monkeypatch):
    from clipper import actions, faces

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    sdir = tmp_path / "kick-1"
    (sdir / "review").mkdir(parents=True)
    (sdir / "review" / "comp-x.mp4").write_bytes(b"")
    (sdir / "review" / "c01_t20.mp4").write_bytes(b"")
    states = {"c01_t20": {"kind": "pick", "file": r"clips\01_t20.mp4"},
              "comp-x": {"kind": "compilation", "file": r"compilations\x.mp4"}}
    monkeypatch.setattr(warroom.actions, "resolve", lambda c, ref: actions.Item(sdir, ref.split("/")[1], states[ref.split("/")[1]]))
    grabbed = []
    monkeypatch.setattr(faces, "grab", lambda video, t, dst: grabbed.append(video) or dst)
    monkeypatch.setattr(faces, "pick", lambda img, box, c: {"box": box, "px": 90, "embedding": [], "closest": "", "match": 0.0, "tile": b"jpg"})
    monkeypatch.setattr(warroom, "for_stream", lambda c, d: cfg)
    box = {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
    got = warroom.faces_pick(cfg, {"ref": "kick-1/c01_t20", "t": 3.0, "box": box})
    assert got["tile"].startswith("data:image/jpeg;base64,") and got["stream"] == "kick-1"
    warroom.faces_pick(cfg, {"ref": "kick-1/comp-x", "t": 3.0, "box": box})
    assert grabbed == [sdir / "clips/01_t20.mp4", sdir / "review" / "comp-x.mp4"]
    with pytest.raises(ClipperError):
        warroom.faces_pick(cfg, {"ref": "kick-1/c01_t20", "t": "soon", "box": box})


def test_investigations_list_newest_first_and_render_the_one_asked_for(defaults, tmp_path):
    from clipper import jsonstate

    defaults["paths"]["archive"] = tmp_path
    for subject, made in (("jer", "2026-11-03T00:00:00+00:00"), ("dsp", "2026-11-04T00:00:00+00:00")):
        (tmp_path / subject / "investigate").mkdir(parents=True)
        jsonstate.write(tmp_path / subject / "investigate" / "leads.json",
                        {"subject": subject, "about": "", "made_at": made, "model": "m", "candidates": 0, "leads": []})
    got = warroom.investigations_get(defaults)
    assert [s["subject"] for s in got["subjects"]] == ["dsp", "jer"] and got["subject"] == "dsp"
    assert warroom.investigations_get(defaults, "jer")["markdown"].startswith("# Leads: jer")
    assert warroom.investigations_get(defaults, "../etc")["subject"] == "dsp"  # only subjects on file
