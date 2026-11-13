"""#206: the end of the serve or bot log for the page, so an error that says "details in the
serve log" can be read from the phone without ssh. Read-only; behind the page's token like
every other route. Values of secret env vars and the page's token are blanked on the way out."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import base
from .state import token

LOGS = {"serve": "serve_log", "bot": "bot_log"}   # ?which= -> its [paths] key
TAIL_BYTES = 1_000_000   # read at most this much from the end of the file
MAX_LINES = 2000
BAD = re.compile(r"\b(ERROR|WARNING|CRITICAL|Traceback|exit [1-9]\d*)\b|failed", re.I)
SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$")


def _secrets(cfg: dict) -> list[str]:
    vals = [v for k, v in os.environ.items() if SECRETISH.search(k) and len(v) >= 8]
    if (t := token(cfg)) and len(t) >= 8:
        vals.append(t)
    return sorted(set(vals), key=len, reverse=True)   # a longer secret before one it contains


def scrub(line: str, secrets: list[str]) -> str:
    for s in secrets:
        line = line.replace(s, "•••")
    return line


def tail(path: Path, n: int) -> list[str]:
    """The last n lines of path, reading no more than TAIL_BYTES from its end."""
    with path.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(0, size - TAIL_BYTES)
        f.seek(start)
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    if start and lines:
        lines = lines[1:]   # began mid-line
    return lines[-n:] if n else []


def log_tail(cfg: dict, which: str = "serve", lines: str = "400", grep: str = "", bad: str = "") -> dict:
    """{which, path, lines, size, note}: the log's last lines, filtered by `grep` (words,
    case-insensitive) and, with bad=1, to errors, warnings and failed jobs."""
    which = which if which in LOGS else "serve"
    raw = base(cfg)["paths"].get(LOGS[which])
    try:
        n = max(1, min(MAX_LINES, int(lines or 400)))
    except ValueError:
        n = 400
    out = {"which": which, "logs": list(LOGS), "lines": [], "size": 0, "note": ""}
    path = Path(raw) if raw else None
    if not path or path.is_dir():
        out["note"] = f"[paths] {LOGS[which]} is empty: it logs to the console only (docker compose logs {which})."
        return out
    if not path.is_file():
        out["note"] = f"No {which} log yet at [paths] {LOGS[which]}."
        return out
    words = grep.lower().split()
    # filtered: look further back, so a filter still finds n matches in a busy log
    got = tail(path, MAX_LINES * 10 if words or bad else n)
    if bad:
        got = [x for x in got if BAD.search(x)]
    if words:
        got = [x for x in got if all(w in x.lower() for w in words)]
    secrets = _secrets(cfg)
    out.update(lines=[scrub(x, secrets) for x in got[-n:]], size=path.stat().st_size, name=path.name)
    return out
