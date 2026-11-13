"""#206: the end of the serve or bot log for the page, so an error that says "details in the
serve log" can be read from the phone without ssh. Read-only; behind the page's token like
every other route, so whoever holds the page's key reads what serve logs (fail() keeps details
out of the API's answers; this is the place they are meant to be read). Secrets are blanked on
the way out: secret env vars, the keys and tokens under secrets/, and key=... in URLs."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..config import base
from .state import token

LOGS = {"serve": "serve_log", "bot": "bot_log"}   # ?which= -> its [paths] key
TAIL_BYTES = 1_000_000   # read at most this much from the end of the file
MAX_LINES = 2000
BAD = re.compile(r"\b(ERROR|WARNING|CRITICAL|Traceback|exit [1-9]\d*)\b|failed", re.I)
SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$", re.I)
IN_URL = re.compile(r"([?&](?:api_?key|key|access_token|token|secret|client_secret|password)=)[^&\s\"']+", re.I)
SECRET_FILE_MAX = 64_000   # cookie jars and the like are skipped
MIN_SECRET = 4   # shorter values (a "1" or "on" in a *_KEY flag) would blank ordinary words everywhere


def _file_secrets(path: Path) -> list[str]:
    """The secret-looking values in one file under secrets/: a JSON file's values under keys
    like *token / *secret / *key, a text file's words (a key file holds one or two)."""
    try:
        if path.stat().st_size > SECRET_FILE_MAX:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        out: list[str] = []
        def walk(x, key=""):
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, k)
            elif isinstance(x, list):
                for v in x:
                    walk(v, key)
            elif isinstance(x, str) and SECRETISH.search(key):
                out.append(x)
        walk(data)
        return out
    if path.suffix == ".txt" and "cookie" not in path.name:
        return text.split()
    return []


def _secrets(cfg: dict) -> list[str]:
    vals = [v for k, v in os.environ.items() if SECRETISH.search(k)]
    vals.append(token(cfg))
    dirs = {Path(v).parent for v in base(cfg)["paths"].values()
            if isinstance(v, (str, Path)) and str(v) and "secrets" in Path(v).parts}
    for d in dirs:
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file():
                    vals += _file_secrets(f)
    return sorted({v for v in vals if len(v) >= MIN_SECRET}, key=len, reverse=True)   # longest first: one may contain another


def scrub(line: str, secrets: list[str]) -> str:
    for s in secrets:
        line = line.replace(s, "•••")
    return IN_URL.sub(r"\1•••", line)


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
    bad = str(bad).lower() in ("1", "true", "yes", "on")
    got = tail(path, MAX_LINES * 10 if words or bad else n)
    # blank first: a filter on the raw text would answer "is this the key's prefix?" (Copilot on #244)
    secrets = _secrets(cfg)
    got = scrub("\n".join(got), secrets).split("\n") if got else []   # one pass over the text, not one per line
    if bad:
        got = [x for x in got if BAD.search(x)]
    if words:
        got = [x for x in got if all(w in x.lower() for w in words)]
    out.update(lines=got[-n:], size=path.stat().st_size, name=path.name)
    return out
