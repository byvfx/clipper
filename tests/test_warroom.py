import http.client
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
