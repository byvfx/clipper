"""yt-dlp, and the one signed-in retry each caller allows.

Signed-in requests are kept rare so the account is seen as little as possible: `archive` and
`receipts` retry only age-restricted videos; pre-shows retry only YouTube's bot check (user,
2026-10-26: a few videos a week); `story` retries the bot check too only when [story]
youtube_bot_retry is on. Not "Sign in to confirm" alone: the bot check ("...you're not a bot")
says that too, and retrying those signed in would put a whole archive run on the account
and hide the block."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("clipper.ytdl")

AGE = ("confirm your age", "age-restricted")
BOT = ("not a bot",)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-progress", *args],
                          check=False, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def run_retrying(cfg: dict, *args: str, retry_on: tuple[str, ...]) -> tuple[subprocess.CompletedProcess, bool]:
    """yt-dlp, then once more with [paths] youtube_cookies when it fails with one of
    `retry_on` (AGE or BOT) in its error. Returns the result and whether it went signed in."""
    r = run(*args)
    cookies = cookies_file(cfg)
    err = r.stderr.lower()
    if r.returncode and any(s in err for s in retry_on) and cookies.is_file():
        log.info("YouTube: %s, retrying signed in (%s)",
                 "age-restricted" if any(s in err for s in AGE) else "bot check", cookies.name)
        out = cfg["paths"]["youtube_cookies"]
        return run("--cookies", str(netscape_cookies(cookies, out.with_name(f"{out.stem}.netscape.txt"))),
                   *args), True
    return r, False


EXPORT_GLOB = "www.youtube.com_*.json"  # Cookie-Editor's own name for a JSON export


def cookies_file(cfg: dict) -> Path:
    """[paths] youtube_cookies, or a newer browser export saved beside it under the extension's
    own name (www.youtube.com_<date>.json), so a fresh export works without a rename (10-31)."""
    p = cfg["paths"]["youtube_cookies"]
    found = [f for f in p.parent.glob(EXPORT_GLOB) if f.is_file()] + ([p] if p.is_file() else [])
    return max(found, key=lambda f: f.stat().st_mtime) if found else p


def last_error(r: subprocess.CompletedProcess) -> str:
    """The last line yt-dlp wrote to stderr (its error)."""
    lines = r.stderr.strip().splitlines()
    return lines[-1] if lines else f"exit {r.returncode}"


def netscape_cookies(path: Path, out: Path | None = None) -> Path:
    """yt-dlp reads only Netscape cookies.txt; a browser extension's JSON export (a list of
    {domain, path, secure, expirationDate, name, value}, or an object holding that list
    under "cookies") is converted to `out` (default: beside it)."""
    text = path.read_text(encoding="utf-8-sig").lstrip()
    if not text.startswith(("[", "{")):
        return path
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("cookies") or []
    lines = ["# Netscape HTTP Cookie File"]
    for c in data:
        dom = c["domain"]
        lines.append("\t".join([dom, "TRUE" if dom.startswith(".") else "FALSE", c.get("path") or "/",
                                "TRUE" if c.get("secure") else "FALSE",
                                str(int(c.get("expirationDate") or 0)), c["name"], c["value"]]))
    out = out or path.with_name(f"{path.stem}.netscape.txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
