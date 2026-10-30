"""A recording's live/state.json: its parts, chat coverage, gaps and where the video got to."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .. import jsonstate

STATE = "live/state.json"
LIVE_CHAT = "live/chat.jsonl"


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


@dataclass
class Part:
    file: str
    wall_start: float
    duration_s: float = 0.0
    segments: int = 0
    bytes: int = 0


@dataclass
class State:
    stream_id: str
    channel: str
    channel_id: int
    user_id: int
    chatroom_id: int
    sessions: list[int]
    title: str
    stream_started_at: str
    max_height: int
    status: str = "recording"          # recording | paused | finalizing | done | failed
    parts: list[Part] = field(default_factory=list)
    chat: list[list[float]] = field(default_factory=list)   # [from, to] socket coverage
    gaps: list[dict] = field(default_factory=list)
    titles: dict[str, int] = field(default_factory=dict)    # EXTINF titles seen
    last_seen: float = 0.0             # last time a new segment arrived
    last_end: float = 0.0              # program date-time where the last segment ended
    last_seq: int = -1                 # media sequence of the last segment written
    variant: str = ""
    joined: list[str] = field(default_factory=list)  # part files finalize put in the source

    @classmethod
    def load(cls, path: Path) -> State:
        d = jsonstate.load(path)
        d["parts"] = [Part(**p) for p in d["parts"]]
        return cls(**d)

    def dump(self) -> dict:
        d = dict(self.__dict__)
        d["parts"] = [dict(p.__dict__) for p in self.parts]
        return d

    def save(self, d: Path) -> None:
        jsonstate.write(d / STATE, self.dump())
