import http.client
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

from clipper import actions, warroom
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
    assert "/api/overview" in warroom.GET_API and "/api/action" in warroom.POST_API
    assert set(warroom.POST_API) >= {"/api/undo", "/api/bulk", "/api/replan", "/api/retake"}


def test_a_refused_post_still_gets_its_answer(serve, monkeypatch):
    """The body is read before any refusal (an unread body resets the connection on
    Windows), and one past MAX_POST isn't read at all."""
    port = serve("")
    for _ in range(20):
        assert status(port, "/api/nope", "evil.example", method="POST") == 403
    monkeypatch.setattr(warroom.httpd, "MAX_POST", 1)
    assert status(port, "/api/nope", "localhost", method="POST") == 413


def test_saved_layouts(tmp_path):
    cfg = {"paths": {"warroom_layouts": tmp_path / "logs" / "layouts.json"}}
    assert warroom.layouts(cfg) == {"layouts": {}}
    lay = {"zones": {"top": ["schedule"], "left": ["pipeline"], "middle": ["queue"]}, "widths": {"left": 300}}
    assert warroom.save_layout(cfg, {"name": "Mine", "layout": lay})["layouts"]["Mine"] == lay
    assert warroom.layouts(cfg)["layouts"]["Mine"] == lay
    for bad in ({"zones": {"sideways": []}}, {"zones": {"top": "schedule"}}, {"zones": {"top": ['"]']}},
                {"zones": {"top": []}, "widths": {"left": 5000}}, {"zones": {"top": []}, "widths": [300]},
                ["top"]):   # widths and the layout itself must be dicts (Copilot on #176)
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


def test_the_page_has_no_mis_encoded_text():
    """UTF-8 read as cp1252 and saved again: a middle dot became "Â·" in the Health panel (#206)."""
    broken = re.compile("[ÂÃ][\x80-\xbf]|â[€œš]")
    files = [warroom.PAGE, warroom.WEB / "warroom.css", *(warroom.WEB / "js").glob("*.js")]
    assert not [(p.name, m.group()) for p in files for m in broken.finditer(p.read_text(encoding="utf-8"))]


def test_faces_window(tmp_path, defaults, monkeypatch):
    import numpy as np

    from clipper import faces, jsonstate

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "faces": str(tmp_path / "known")}}
    monkeypatch.setattr(warroom.faces, "for_stream", lambda c, sdir: cfg)
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
    from clipper import faces

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    sdir = tmp_path / "kick-1"
    (sdir / "review").mkdir(parents=True)
    (sdir / "review" / "comp-x.mp4").write_bytes(b"")
    (sdir / "review" / "c01_t20.mp4").write_bytes(b"")
    states = {"c01_t20": {"kind": "pick", "file": r"clips\01_t20.mp4"},
              "comp-x": {"kind": "compilation", "file": r"compilations\x.mp4"}}
    monkeypatch.setattr(actions, "resolve", lambda c, ref: actions.Item(sdir, ref.split("/")[1], states[ref.split("/")[1]]))
    grabbed = []
    monkeypatch.setattr(faces, "grab", lambda video, t, dst: grabbed.append(video) or dst)
    monkeypatch.setattr(faces, "pick", lambda img, box, c: {"box": box, "px": 90, "embedding": [], "closest": "", "match": 0.0, "tile": b"jpg"})
    monkeypatch.setattr(warroom.faces, "for_stream", lambda c, d: cfg)
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
    gone = warroom.investigations_get(defaults, "jer", "20200101000000")
    assert gone["missing_run"] and gone["markdown"] == ""
    assert warroom.investigations_get(defaults, "jer", "20261103000000")["run"] == "20261103000000"


def test_next_slot_for_every_segment_leaves_out_a_full_budget(tmp_path, defaults, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from clipper import jsonstate, schedule

    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path, "schedule": tmp_path / "schedule.json"}}
    jsonstate.write(cfg["paths"]["schedule"], {"entries": []})
    row = lambda ref, **kw: {"ref": ref, "stream": ref.split("/")[0], "kind": "segment", "title": ref,
                             "decision": "approved", "hold": False, "scheduled": "", "uploaded": False, **kw}
    monkeypatch.setattr(warroom.review, "all_rows", lambda c: [row("a/x"), row("b/y"), row("b/held", hold=True),
                                                        row("b/no", decision="pending")])
    monkeypatch.setattr(schedule, "release_window", lambda c, sdir, meta: (datetime.now(UTC) - timedelta(days=1), None))
    auto = {"key": "a/auto", "title": "a/auto", "publish_at": "2026-11-09T16:00:00-08:00"}

    def with_pins(c, pins):  # stream a's budget is full: pinning a/x bumps a/auto
        if "a/x" in pins:
            return {}, [{"key": "a/auto", "reason": "over the stream's segment budget (1)"}]
        return {"a/auto": auto}, []
    monkeypatch.setattr(schedule, "with_pins", with_pins)

    out = warroom.slot_plan(cfg, kind="segment")
    assert [p["ref"] for p in out["plan"]] == ["b/y"] and out["bumped"] == []
    assert [(r["ref"], r["why"][:27]) for r in out["refused"]] == [("a/x", "its stream's budget is full")]

    out = warroom.slot_plan(cfg, ["a/x", "b/held", "b/no"])  # one you pick may swap
    assert [p["ref"] for p in out["plan"]] == ["a/x"]
    assert out["bumped"] == [{"ref": "a/auto", "title": "a/auto", "at": auto["publish_at"],
                              "why": "over the stream's segment budget (1)"}]
    assert {r["ref"]: r["why"] for r in out["refused"]} == {"b/held": "held", "b/no": "not approved"}

    shown = warroom.do_slot(cfg, {"kind": "segment", "dry_run": True})
    assert shown["plan"][0]["let_go"] == "" and shown["print"]
    moved = warroom.do_slot(cfg, {"kind": "segment", "print": "an older preview"})  # Apply pins nothing
    assert moved["changed"] and moved["done"] == 0 and moved["print"] == shown["print"]


def test_hidden_shows_leave_the_page(tmp_path):
    from clipper import config
    path = tmp_path / "clipper.toml"
    path.write_text('[warroom]\nhide_shows = ["trish"]\n[shows.kino]\nchannels = ["k"]\n'
                    '[shows.trish]\nchannels = ["t"]\n', encoding="utf-8")
    assert list(warroom.shown_shows(config.load(path))) == ["kino"]


def test_bad_headers_and_queries_are_bad_requests_not_crashes(serve, defaults, tmp_path):
    """A malformed Content-Length, Range or ?t= answers with a status, never a 500 (Copilot on #176)."""
    defaults["paths"]["streams"] = tmp_path / "streams"   # before serve(): the handler reads it then
    port = serve("")
    root = defaults["paths"]["streams"]
    (root / "kick-1").mkdir(parents=True)
    (root / "kick-1" / "c.mp4").write_bytes(b"0123456789")

    def get(path: str, **headers) -> tuple[int, bytes, dict]:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers={"Host": "localhost", **headers})
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())

    assert get("/media/kick-1/c.mp4", Range="bytes=2-4")[:2] == (206, b"234")
    assert get("/media/kick-1/c.mp4", Range="bytes=2-4")[2]["Content-Range"] == "bytes 2-4/10"
    assert get("/media/kick-1/c.mp4", Range="bytes=abc-def")[:2] == (200, b"0123456789")   # ignored
    code, body, hdrs = get("/media/kick-1/c.mp4", Range="bytes=100-")
    assert (code, body, hdrs["Content-Range"]) == (416, b"", "bytes */10")
    assert get("/watch?src=/media/kick-1/c.mp4&t=abc")[0] == 200   # from the start

    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("POST", "/api/nope")
    c.putheader("Host", "localhost")
    c.putheader("Content-Length", "abc")
    c.endheaders()
    assert c.getresponse().status == 400


def test_tailscale_json_that_isnt_is_an_empty_status():
    from types import SimpleNamespace

    j = warroom.tailscale._json
    assert j(SimpleNamespace(stdout='{"BackendState": "Running"}')) == {"BackendState": "Running"}
    assert j(SimpleNamespace(stdout="Log in at https://login.tailscale.com/...")) == {}
    assert j(SimpleNamespace(stdout="")) == {} and j(SimpleNamespace(stdout="[1, 2]")) == {}


def test_sync_all_starts_one_sync_for_two_requests(defaults, monkeypatch):
    import time

    calls = []

    def slow_sync(d, cfg, quiet=False):
        calls.append(d)
        time.sleep(0.3)
    monkeypatch.setattr(warroom.review.review, "sync", slow_sync)
    monkeypatch.setattr(warroom.review.actions, "review_dirs", lambda cfg: [defaults["paths"]["streams"] / "kick-1"])
    warroom.SYNC.update(running=False, error="")
    warroom.sync_all(defaults)
    warroom.sync_all(defaults)   # the same instant: still one thread
    time.sleep(0.6)
    assert len(calls) == 1 and warroom.SYNC["running"] is False and warroom.SYNC["last"]


def test_signed_out_page_is_a_page(serve):
    """#206: an expired cookie on the page itself is a sign-in hint, not raw JSON; the API
    still answers JSON."""
    port = serve("t0ken")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/", headers={"Host": "laptop.tail1234.ts.net"})
    r = c.getresponse()
    body = r.read().decode("utf-8")
    assert r.status == 401 and r.getheader("Content-Type").startswith("text/html")
    assert "?key=" in body and "t0ken" not in body
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/api/overview", headers={"Host": "laptop.tail1234.ts.net"})
    r = c.getresponse()
    assert r.status == 401 and r.getheader("Content-Type").startswith("application/json")


def test_retake_cost():
    """#203: the mean of recent retakes that recorded a cost, and this month's."""
    from clipper.warroom.data import retake_cost

    hs = [{"at": "2026-11-12T10:00:00+00:00", "cost_usd": 0.03}, {"at": "2026-11-01T10:00:00+00:00", "cost_usd": 0.01},
          {"at": "2026-11-01T09:00:00+00:00"}, {"at": "2026-10-30T10:00:00+00:00", "cost_usd": 0.02}]
    assert retake_cost(hs, "2026-11") == {"avg_usd": 0.02, "month_n": 3, "month_usd": 0.04}
    assert retake_cost([], "2026-11") == {"avg_usd": None, "month_n": 0, "month_usd": 0}
    # the month is the show's: 2026-12-01 03:00 UTC is still November in Los Angeles
    from zoneinfo import ZoneInfo
    late = [{"at": "2026-12-01T03:00:00+00:00", "cost_usd": 0.01}, {"at": "", "cost_usd": 0.01}]
    assert retake_cost(late, "2026-11", ZoneInfo("America/Los_Angeles"))["month_n"] == 1
    assert retake_cost(late, "2026-11")["month_n"] == 0


def test_cast_find_estimate(tmp_path, defaults):
    """#203: a Find's label cost before it runs, from past cast label calls in the costs log."""
    import json

    from clipper import costs
    from clipper.warroom.cast import find_estimate

    log = tmp_path / "costs.jsonl"
    cfg = {**defaults, "paths": {**defaults["paths"], "costs": log}}
    assert find_estimate(cfg)["label_usd"] is None   # no log yet
    log.write_text("\n".join(json.dumps(r) for r in (
        {"kind": "llm", "stage": "cast grab", "usd": 0.0003}, {"kind": "llm", "stage": "score", "usd": 0.5},
        {"kind": "llm", "stage": "cast auto", "usd": 0.0005}, {"kind": "modal", "stage": "cast", "usd": 9})) + "\n")
    assert find_estimate(cfg)["label_usd"] is None   # cached for a while: the log grows all the time
    costs._AVG.clear()
    est = find_estimate(cfg)
    assert est["per_source"] == defaults["cast"]["per_source"] and est["label_usd"] == pytest.approx(0.0004)
    cfg["cast"] = {**defaults["cast"], "label_model": ""}
    assert find_estimate(cfg)["label_usd"] == 0.0


def test_page_scripts_parse():
    """#206: a syntax error in one script blanks the page; CI's runners have node."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    for p in sorted((warroom.WEB / "js").glob("*.js")):
        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{p.name}: {r.stderr[-500:]}"


def test_log_tail_filters_and_blanks_secrets(tmp_path, defaults, monkeypatch):
    from clipper.warroom import logview
    log = tmp_path / "serve.log"
    log.write_text("".join(f"2026-11-13 10:00:{k:02d} INFO    clipper.serve: job {k} ok\n" for k in range(50))
                   + "2026-11-13 10:01:00 WARNING clipper.warroom: kick-1: KeyError: 'x'\n"
                   + "2026-11-13 10:01:01 INFO    clipper.serve: thumbs exit 2\n"
                   + "2026-11-13 10:01:02 INFO    clipper.serve: called with sk-ant-0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-0123456789abcdef")
    cfg = {**defaults, "paths": {**defaults["paths"], "serve_log": log}}
    r = logview.log_tail(cfg, "serve", "5")
    assert len(r["lines"]) == 5 and r["lines"][-1].endswith("called with •••")
    assert not any("sk-ant" in x for x in r["lines"])
    bad = logview.log_tail(cfg, "serve", "100", bad="1")["lines"]
    assert [x.split(": ", 1)[1] for x in bad] == ["kick-1: KeyError: 'x'", "thumbs exit 2"]
    assert len(logview.log_tail(cfg, "serve", "100", grep="KeyError kick-1")["lines"]) == 1   # every word, any case
    assert logview.log_tail(cfg, "nope", "x")["which"] == "serve"   # unknown names and counts: the defaults
    none = logview.log_tail({**cfg, "paths": {**cfg["paths"], "bot_log": tmp_path}}, "bot")
    assert none["lines"] == [] and "console only" in none["note"]
    assert "No bot log" in logview.log_tail({**cfg, "paths": {**cfg["paths"], "bot_log": tmp_path / "bot.log"}}, "bot")["note"]


def test_log_tail_reads_only_the_end_of_a_big_file(tmp_path, monkeypatch):
    from clipper.warroom import logview
    monkeypatch.setattr(logview, "TAIL_BYTES", 100)
    p = tmp_path / "x.log"
    p.write_text("".join(f"line {k:04d}\n" for k in range(1000)), encoding="utf-8")
    got = logview.tail(p, 1000)
    assert got[-1] == "line 0999" and 0 < len(got) <= 10 and all(x.startswith("line ") and len(x) == 9 for x in got)


def test_log_scrub_covers_secret_files_and_url_keys(tmp_path, defaults, monkeypatch):
    from clipper.warroom import logview
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "dvids_secret.txt").write_text("pubkey-0123456789\nsecretkey-abcdefghij\n", encoding="utf-8")
    (sec / "x_token.json").write_text('{"access_token": "tok-abcdefghijkl", "scope": "tweet.write"}', encoding="utf-8")
    (sec / "youtube_cookies.txt").write_text("cookiejar-value-not-a-key\n", encoding="utf-8")
    log = tmp_path / "serve.log"
    log.write_text("used secretkey-abcdefghij and tok-abcdefghijkl, scope tweet.write\n"
                   "404 for url: https://api.example/search?q=moon&api_key=zzzzzzzz12345&page=2\n", encoding="utf-8")
    cfg = {**defaults, "paths": {**defaults["paths"], "serve_log": log, "x_token": sec / "x_token.json"}}
    a, b = logview.log_tail(cfg, "serve")["lines"]
    assert a == "used ••• and •••, scope tweet.write"
    assert b == "404 for url: https://api.example/search?q=moon&api_key=•••&page=2"
    assert "cookiejar-value-not-a-key" not in logview._secrets(cfg)
    assert logview.log_tail(cfg, "serve", bad="0")["lines"] == [a, b]   # "0" is off


def test_log_filters_see_only_scrubbed_text(tmp_path, defaults, monkeypatch):
    """Copilot on #244: a filter on the raw line would confirm a key's prefix; short keys count too."""
    from clipper.warroom import logview
    monkeypatch.setenv("SOME_API_KEY", "k3y9")
    log = tmp_path / "serve.log"
    log.write_text("ERROR called with k3y9 and sk-live-abcdef123456\n", encoding="utf-8")
    monkeypatch.setenv("OTHER_TOKEN", "sk-live-abcdef123456")
    cfg = {**defaults, "paths": {**defaults["paths"], "serve_log": log}}
    assert logview.log_tail(cfg)["lines"] == ["ERROR called with ••• and •••"]
    for probe in ("sk-live-a", "k3y", "abcdef"):
        assert logview.log_tail(cfg, grep=probe)["lines"] == []
        assert logview.log_tail(cfg, grep=probe, bad="1")["lines"] == []
    assert logview.log_tail(cfg, grep="•••")["lines"]   # what is shown can be searched
