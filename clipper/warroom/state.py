"""What the war room's modules share: the one Kitsu-write lock, the sync and tailnet state,
the hook `clipper serve` installs, the page's token (the server and the tailnet check both
read it), and who the page signs its Kitsu notes as."""
from __future__ import annotations

import threading
from pathlib import Path

from ..config import _root

LOCK = threading.Lock()  # one Kitsu write / replan at a time

SYNC = {"running": False, "last": None, "error": ""}
SYNC_LOCK = threading.Lock()   # the check-and-set in sync_all: one sync thread at a time

TAILNET: dict = {"served": ""}   # the https URL tailscale serve gives the war room, found at start

HOOKS: dict = {"retake": None}   # set by clipper serve: start a retake on its job runner

WHO = "war room"

def token(cfg: dict) -> str:
    """[warroom] token, else the one `clipper warroom --tailnet on` keeps in [paths] warroom_token."""
    wc, f = _root(cfg)["warroom"], Path(cfg["paths"]["warroom_token"])
    return wc["token"] or (f.read_text(encoding="utf-8").strip() if f.is_file() else "")
