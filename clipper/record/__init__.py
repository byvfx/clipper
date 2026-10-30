"""Live recorder: watch Kick channels, record each live stream with real-time chat.

`clipper record watch` is the long-lived service (Docker on the NAS): it polls each
channel in [record] channels; when one goes live it records the HLS stream and the chat
into streams/kick-<livestream id>/, and when the stream ends (after end_grace_s with no
restart) it finalizes the directory into what `clipper fetch` produces (source, chat.jsonl,
meta.json, audio.wav).

    watch.py       the watcher: live status per channel, start / pause / finish, heartbeat
    recording.py   one recording's threads; the video one writes live/partNNN.ts
    hls.py         variant pick, media playlist parsing, where a segment goes (gap, reset)
    chat.py        the chat thread: Kick's realtime service into live/chat.jsonl
    finalize.py    join the parts, place chat in source time, meta.json, audio, on_finish
    state.py       live/state.json (State, Part)
    client.py      Kick over HTTP

Chat: Kick's realtime service (Centrifugo since 2026; the old public Pusher key is gone).
Kick's created_at (whole seconds) is kept as ts; our receive time goes in recv. Periods the
socket was down are backfilled from the chat-history API at finalize.

Alerts (Discord and the bot's outbox) are clipper.alerts.Alerts; the name is kept here too.
"""

from ..alerts import Alerts
from .chat import DEFAULT_CHAT_WS, chat_row
from .client import CHANNEL_API, REALTIME_API, Kick
from .finalize import finalize, on_finish_args, source_map, source_time
from .hls import Segment, parse_media, pick_variant
from .recording import Recording
from .state import LIVE_CHAT, STATE, Part, State
from .watch import Watcher, channel_list, setup_logging

__all__ = ["CHANNEL_API", "DEFAULT_CHAT_WS", "LIVE_CHAT", "REALTIME_API", "STATE", "Alerts",
           "Kick", "Part", "Recording", "Segment", "State", "Watcher", "channel_list",
           "chat_row", "finalize", "on_finish_args", "parse_media", "pick_variant",
           "setup_logging", "source_map", "source_time"]
