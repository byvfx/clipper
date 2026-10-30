"""The chat thread: Kick's realtime service (Centrifugo) into live/chat.jsonl.

A guest token from web.kick.com/api/v1/realtime/auth/connection, refreshed before it expires,
and a subscription to chatrooms.<chatroom id>.v2. Each connected span goes into the state's
chat coverage, so finalize backfills the rest from the chat-history API."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

import requests

from .. import kick
from .state import LIVE_CHAT, _now

if TYPE_CHECKING:
    from .recording import Recording

log = logging.getLogger("clipper.record")

DEFAULT_CHAT_WS = "wss://realtime.us-east-1.platform.kick.com/connection/websocket"


def listen(rec: Recording) -> None:
    """Until rec.stop_evt: connect, subscribe, append rows; reconnect with backoff."""
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    st, http = rec.st, rec.chat_http
    backoff = 1
    rows = open(rec.d / LIVE_CHAT, "a", encoding="utf-8")  # noqa: SIM115 (closed in finally)
    try:
        while not rec.stop_evt.is_set():
            span = None
            try:
                ws_url = DEFAULT_CHAT_WS
                try:
                    conns = http.post(f"/channels/{st.channel_id}/chat/connection",
                                      {"client": {"id": str(uuid.uuid4()), "type": "web"},
                                       "capabilities": {"accepted_providers":
                                                        [{"provider": "centrifugo"}]}})
                    for c in conns["data"]["connections"]:
                        if c["provider"] == "centrifugo":
                            ws_url = c["credentials"]["url"]
                except (requests.RequestException, KeyError, ValueError) as e:
                    log.warning("%s: chat connection lookup failed (%s); using default",
                                st.stream_id, e)
                client_id = str(uuid.uuid4())
                token = http.post("/auth/connection", {"client_id": client_id})["data"]["token"]
                with connect(ws_url, open_timeout=20, close_timeout=5,
                             additional_headers={"Origin": "https://kick.com",
                                                 "User-Agent": rec.cfg["kick"]["user_agent"]}) as ws:
                    ws.send(json.dumps({"id": 1, "connect": {"token": token, "name": "js"}}))
                    reply = json.loads(ws.recv(timeout=20).split("\n")[0])
                    if "connect" not in reply:
                        raise ConnectionError(f"chat connect refused: {reply}")
                    ttl = reply["connect"].get("ttl") or 0
                    refresh_at = time.monotonic() + max(60, ttl - 300) if ttl else None
                    channel = f"chatrooms.{st.chatroom_id}.v2"
                    ws.send(json.dumps({"id": 2, "subscribe": {"channel": channel}}))
                    cmd_id = 3
                    log.info("%s: chat connected (%s)", st.stream_id, channel)
                    while not rec.stop_evt.is_set():
                        if span:
                            span[1] = _now()
                        if refresh_at and time.monotonic() > refresh_at:
                            token = http.post("/auth/connection",
                                              {"client_id": client_id})["data"]["token"]
                            ws.send(json.dumps({"id": cmd_id, "refresh": {"token": token}}))
                            cmd_id += 1
                            refresh_at = time.monotonic() + max(60, ttl - 300)
                        try:
                            frame = ws.recv(timeout=5)
                        except TimeoutError:
                            continue
                        for raw in frame.split("\n"):
                            if raw == "{}":
                                ws.send("{}")  # Centrifugo ping
                                continue
                            if not raw:
                                continue
                            msg = json.loads(raw)
                            if msg.get("id") == 2:
                                if "error" in msg:
                                    raise ConnectionError(f"subscribe refused: {msg['error']}")
                                span = [_now(), _now()]
                                with rec.lock:
                                    st.chat.append(span)
                                backoff = 1
                            elif "error" in msg:
                                raise ConnectionError(f"chat error: {msg['error']}")
                            row = chat_row(msg)
                            if row:
                                rows.write(json.dumps(row, ensure_ascii=False) + "\n")
                                rows.flush()
            except (WebSocketException, OSError, ConnectionError, requests.RequestException,
                    ValueError, KeyError) as e:
                if not rec.stop_evt.is_set():
                    log.warning("%s: chat dropped: %r; reconnecting in %d s",
                                st.stream_id, e, backoff)
                    if backoff >= 60:
                        rec.alert(f"{st.stream_id}: chat keeps dropping ({e!r}); "
                                  "the gaps are backfilled at finalize")
                rec.stop_evt.wait(backoff)
                backoff = min(backoff * 2, 120)
            finally:
                if span:
                    span[1] = _now()
                    rec.save()
    except Exception as e:
        log.exception("%s: chat thread crashed", st.stream_id)
        rec.alert(f"{st.stream_id}: chat thread crashed: {e!r}", logging.ERROR)
    finally:
        rows.close()


def chat_row(msg: dict) -> dict | None:
    """A chat.jsonl row from a Centrifugo push, or None if it isn't a chat message."""
    pub = (msg.get("push") or {}).get("pub") or {}
    ev = pub.get("data") or {}
    if not isinstance(ev, dict) or ev.get("event") != "App\\Events\\ChatMessageEvent":
        return None
    m = ev["data"]
    if isinstance(m, str):
        m = json.loads(m)
    return {"id": m["id"],
            "ts": kick._parse_ts(m["created_at"]).timestamp(),
            "user": (m.get("sender") or {}).get("username"),
            "type": m.get("type"),
            "content": m.get("content", ""),
            "recv": round(_now(), 3)}
