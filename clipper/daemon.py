"""What clipper's long-running commands share (`clipper serve`, `clipper bot`): a rotated
log file, one instance per streams folder, and a heartbeat on the streams share.

A heartbeat is <streams>/.<name>/status.json, like the recorder's (clipper/record):
{"at": <iso>, "host", "pid", "started_at", ...whatever the service adds}. The service writes
it every BEAT_S; a reader (`clipper services status`, the war room's Health panel,
`clipper doctor`) calls it down after HEARTBEAT_S without one, from any machine that sees
the share. A lock file alone can't say that: it stays behind when a process is killed.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .errors import ClipperError

log = logging.getLogger("clipper.daemon")

BEAT_S = 60          # how often a service writes its heartbeat
HEARTBEAT_S = 180    # without one for this long, it's down


def setup_logging(path) -> None:
    """Console plus `path` (rotated at 5 MB, 5 kept); an empty path = console only."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if path and not Path(path).is_dir():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)


def single_instance(cfg: dict, name: str):
    """Hold a lock on <streams>/.<name>.lock for the process's life; refuse a second copy
    (two bots would race to answer every button, two serves would upload twice)."""
    path = cfg["paths"]["streams"] / f".{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")  # noqa: SIM115 (held for the process's life)
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise ClipperError(f"another clipper {name} is already running (lock {path})") from None
    return f


# ---------- heartbeat ----------

def heartbeat_path(cfg: dict, name: str) -> Path:
    return cfg["paths"]["streams"] / f".{name}" / "status.json"


def beat(cfg: dict, name: str, started_at: str, **extra) -> None:
    """Write the heartbeat. Never raises: a share hiccup mustn't stop the service."""
    path = heartbeat_path(cfg, name)
    data = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "host": socket.gethostname(),
            "pid": os.getpid(), "started_at": started_at, **extra}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        jsonstate.write(path, data)
    except OSError as e:
        log.warning(f"heartbeat not written ({e})")


def heartbeat(cfg: dict, name: str, now: datetime | None = None) -> dict:
    """{up, age_s (None = no heartbeat), at, host, data: the whole file, path}."""
    path = heartbeat_path(cfg, name)
    try:
        data = jsonstate.read(path, {})
    except (OSError, ValueError):
        data = {}
    at = data.get("at") or ""
    try:
        age = ((now or datetime.now(UTC)) - datetime.fromisoformat(at)).total_seconds()
    except ValueError:
        age = None
    return {"up": age is not None and age < HEARTBEAT_S, "age_s": age, "at": at,
            "host": data.get("host", ""), "data": data, "path": str(path)}


def state(hb: dict) -> tuple[str, str]:
    """(ok|mid|bad, short text) for a Health line."""
    if hb["age_s"] is None:
        return "bad", "not running (no heartbeat)"
    if not hb["up"]:
        return "bad", f"down, last seen {hb['age_s'] / 60:.0f} min ago"
    return "ok", f"running on {hb['host']}" if hb["host"] else "running"


def local(iso: str, fmt: str = "%m-%d %H:%M") -> str:
    """A heartbeat's UTC timestamp in this machine's time, for the status lines."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime(fmt)
    except (TypeError, ValueError):
        return iso or "?"


def heartbeat_lines(name: str, hb: dict) -> list[str]:
    """`clipper services status` lines for a service's heartbeat."""
    if hb["age_s"] is None:
        return [f"{name}: no heartbeat at {hb['path']} (not running, or the share isn't reachable)"]
    if not hb["up"]:
        return [f"{name}: DOWN, last heartbeat {hb['age_s'] / 60:.0f} min ago ({local(hb['at'])}) on {hb['host']}"]
    since = local(hb["data"].get("started_at", ""))
    out = [f"{name}: up on {hb['host']} (pid {hb['data'].get('pid')}, {hb['age_s']:.0f} s ago, since {since})"]
    for job, j in sorted((hb["data"].get("jobs") or {}).items()):
        if j.get("running"):
            out.append(f"  {job}: running since {local(j.get('started', ''), '%H:%M')}")
        elif j.get("last_at"):
            out.append(f"  {job}: last {local(j['last_at'])} exit {j.get('last_exit')}"
                       + (f", next in {j['next_in_s'] / 60:.0f} min" if j.get("next_in_s") is not None else ""))
    return out
