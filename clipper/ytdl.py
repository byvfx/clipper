"""yt-dlp, and the one signed-in retry each caller allows.

Signed-in requests are kept rare so the account is seen as little as possible: `archive` and
`receipts` retry only age-restricted videos; pre-shows retry only YouTube's bot check (user,
2026-10-26: a few videos a week); `story` retries both ([story] youtube_bot_retry, user
11-03: a handful of videos a story). Not "Sign in to confirm" alone: the bot check ("...you're
not a bot") says that too, and retrying those signed in would put a whole archive run on the account
and hide the block."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import ClipperError

log = logging.getLogger("clipper.ytdl")

AGE = ("confirm your age", "age-restricted")
BOT = ("not a bot",)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-progress", *args],
                          check=False, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def run_retrying(cfg: dict, *args: str, retry_on: tuple[str, ...]) -> tuple[subprocess.CompletedProcess, bool]:
    """yt-dlp, then once more signed in (`signed_in_args`) when it fails with one of
    `retry_on` (AGE or BOT) in its error. Returns the result and whether it went signed in."""
    r = run(*args)
    err = r.stderr.lower()
    if r.returncode and any(s in err for s in retry_on):
        with tempfile.TemporaryDirectory(prefix="clipper-cookies-") as scratch:
            if signed := signed_in_args(cfg, Path(scratch)):
                log.info("YouTube: %s, retrying signed in (%s)",
                         "age-restricted" if any(s in err for s in AGE) else "bot check",
                         browser(cfg) or cookies_file(cfg).name)
                return run(*signed, *args), True
    return r, False


def browser(cfg: dict) -> str:
    """[youtube] cookies_from_browser: a yt-dlp browser spec ("firefox:clipper") whose cookies
    are read live on each signed-in run, so a session the browser rotates is never stale
    (11-04: four exports went stale in eight days). "" = the exported cookies file."""
    return cfg.get("youtube", {}).get("cookies_from_browser", "")


def signed_in_args(cfg: dict, scratch: Path) -> list[str]:
    """yt-dlp's options for a signed-in run: the browser profile if one is set, else a private
    Netscape copy of the cookies file in `scratch` (yt-dlp saves its jar back to its --cookies
    file on exit, non-atomically: on the shared file that would undo a fresher export written
    meanwhile, 11-09); [] when neither."""
    if spec := browser(cfg):
        return ["--cookies-from-browser", spec]
    cookies = cookies_file(cfg)
    if not cookies.is_file():
        return []
    private = scratch / "cookies.txt"
    if netscape_cookies(cookies, private) != private:  # already Netscape: copied as it is
        shutil.copyfile(cookies, private)
    return ["--cookies", str(private)]


# yt-dlp's own --cookies-from-browser syntax: BROWSER[+KEYRING][:PROFILE][::CONTAINER]
SPEC = re.compile(r"(?P<name>[^+:]+)(?:\s*\+\s*(?P<keyring>[^:]+))?"
                  r"(?:\s*:\s*(?!:)(?P<profile>.+?))?(?:\s*::\s*(?P<container>.+))?")
SESSION = ("SID", "__Secure-1PSID", "__Secure-3PSID")  # present only while signed in


class _Quiet:
    def debug(self, *a, **k): ...
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...
    def progress_bar(self): ...


def _browser_cookies(spec: str) -> list:
    """The youtube.com cookies the browser profile `spec` holds now, read from its cookie
    database on disk (no network)."""
    m = SPEC.fullmatch(spec)
    if not m:
        raise ClipperError(f"[youtube] cookies_from_browser {spec!r}: not BROWSER[:PROFILE]")
    from yt_dlp.cookies import extract_cookies_from_browser
    try:
        jar = extract_cookies_from_browser(m["name"].lower(), m["profile"], _Quiet(),
                                           keyring=m["keyring"] and m["keyring"].upper(),
                                           container=m["container"])
    except Exception as e:  # a missing profile, a locked or unreadable database
        raise ClipperError(f"[youtube] cookies_from_browser {spec!r}: {e}") from e
    return [c for c in jar if c.domain == "youtube.com" or c.domain.endswith(".youtube.com")]


def live(cookies: list, now: float | None = None) -> list:
    """The cookies that haven't expired (no expiry = a session cookie, kept)."""
    now = time.time() if now is None else now
    return [c for c in cookies if c.expires is None or c.expires > now]


def session_names(cookies: list, now: float | None = None) -> list[str]:
    """The sign-in cookies (names only) among `cookies` that haven't expired. [] = signed out."""
    return sorted({c.name for c in live(cookies, now) if c.name in SESSION})


def browser_session(spec: str) -> list[str]:
    """The YouTube sign-in cookies (names only) the browser profile `spec` holds now, read
    from its cookie database on disk (no network). [] = that profile isn't signed in."""
    return session_names(_browser_cookies(spec))


@dataclass
class Row:
    """One cookies.txt line: what yt-dlp needs of a cookie (expires None = a session cookie)."""
    domain: str
    path: str
    secure: bool
    expires: int | None
    name: str
    value: str


def netscape_text(cookies: list) -> str:
    """Cookies (http.cookiejar's or Rows) as a Netscape cookies.txt (expiry 0 = a session cookie)."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        lines.append("\t".join([c.domain, "TRUE" if c.domain.startswith(".") else "FALSE", c.path or "/",
                                "TRUE" if c.secure else "FALSE", str(int(c.expires or 0)),
                                c.name, c.value or ""]))
    return "\n".join(lines) + "\n"


def read_netscape(text: str) -> list[Row]:
    """A Netscape cookies.txt's cookies (#HttpOnly_ lines included, other comments skipped)."""
    rows = []
    for line in text.splitlines():
        line = line.removeprefix("#HttpOnly_")
        f = line.split("\t")
        if line.startswith("#") or len(f) != 7:
            continue
        rows.append(Row(f[0], f[2], f[3] == "TRUE", int(f[4]) if f[4].isdigit() and int(f[4]) else None,
                        f[5], f[6]))
    return rows


def read_cookies(path: Path) -> list[Row]:
    """A cookies file's cookies, Netscape or a browser extension's JSON export."""
    text = path.read_text(encoding="utf-8-sig").lstrip()
    if not text.startswith(("[", "{")):
        return read_netscape(text)
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("cookies") or []
    return [Row(c["domain"], c.get("path") or "/", bool(c.get("secure")),
                int(c["expirationDate"]) if c.get("expirationDate") else None, c["name"], c["value"])
            for c in data]


def export_browser_cookies(spec: str, out: Path) -> int:
    """Write the browser profile `spec`'s youtube.com cookies to `out` (Netscape, via a temp
    file and a replace), for machines with no browser of their own: the NAS hub's containers
    read [paths] youtube_cookies, and a laptop task refreshes it (11-09). Refuses when the
    profile isn't signed in, so a signed-out profile never overwrites a working file.
    Returns the number of cookies written."""
    cookies = live(_browser_cookies(spec))
    if not session_names(cookies):
        raise ClipperError(f"the browser profile {spec} isn't signed in to YouTube; {out.name} left as it is")
    tmp = out.with_name(f"{out.name}.tmp")
    try:
        tmp.write_text(netscape_text(cookies), encoding="utf-8")
        tmp.replace(out)
    except OSError as e:  # the share unreachable (laptop away from home), a refused replace
        tmp.unlink(missing_ok=True)
        raise ClipperError(f"couldn't write {out}: {e}") from e
    return len(cookies)


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
    if not path.read_text(encoding="utf-8-sig").lstrip().startswith(("[", "{")):
        return path
    out = out or path.with_name(f"{path.stem}.netscape.txt")
    out.write_text(netscape_text(read_cookies(path)), encoding="utf-8")
    return out
