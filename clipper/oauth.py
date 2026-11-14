"""Browser sign-in for desktop OAuth (TikTok, X): open the authorize page and catch the
redirect on a local port."""

from __future__ import annotations

import http.server
import select
import socket
import time
import urllib.parse
import webbrowser

from .errors import ClipperError


def browser_code(url: str, port: int, state: str, service: str, again: str) -> str:
    """Open `url` and wait up to 10 min for the redirect to http://localhost:<port>/...;
    the authorization code it carries. `again` is the command that retries the sign-in."""
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if "code" in q or "error" in q:
                got.update(q)
            ok = q.get("state") == state and "code" in q
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write((f"{service} approved; clipper is finishing the sign-in. You can close this tab." if ok else
                              f"{service} sign-in failed: {q.get('error_description') or q.get('error') or 'no code'}"
                              ).encode())

        def log_message(self, *a):
            pass

    class Server6(http.server.HTTPServer):
        address_family = socket.AF_INET6

    # The browser may resolve localhost to ::1 or to 127.0.0.1; listen on both.
    servers = [http.server.HTTPServer(("127.0.0.1", port), Handler)]
    try:
        servers.append(Server6(("::1", port), Handler))
    except OSError:
        pass
    print(f"Opening your browser to sign in to {service}: {url}")
    webbrowser.open(url)
    deadline = time.time() + 600
    while not got and time.time() < deadline:
        ready, _, _ = select.select(servers, [], [], 5)
        for s in ready:
            s.handle_request()
    for s in servers:
        s.server_close()
    if not got:
        raise ClipperError(f"{service} sign-in timed out (10 min); run: {again}")
    if got.get("state") != state or "code" not in got:
        raise ClipperError(f"{service} sign-in failed: "
                           f"{got.get('error_description') or got.get('error') or 'state did not match'}")
    return got["code"]
