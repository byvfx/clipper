"""`clipper serve`: the always-on process. It serves the war room (clipper/warroom.py) and
runs the background jobs that used to ride inside the Discord bot, so the bot is only
Discord and this can move to the NAS on its own (#12, #64).

    clipper serve [--port 8765]

Jobs (`jobs()`; each runs `clipper ...` as a child process; an interval of 0 turns one off):
    upload     every [youtube] auto_upload_minutes: `clipper schedule`, then `clipper upload`
    preshow    every [preshow] poll_minutes: `clipper preshow check --run`, for shows with a
               pre-show channel
    retake     every 10 min with [retake] auto, when Kitsu has retake notes not acted on; and
               at once when the war room or the bot asks (POST /api/retake)
    tiktok     every [tiktok] auto_send_minutes: `clipper tiktok --auto` (when signed in)
    x          every [x] auto_post_minutes: `clipper x --auto` (when signed in)
    analytics  every [analytics] auto_hours, once something is uploaded
    cast       every [cast] auto_hours: `clipper cast auto` (needs OpenCV and [thumbnail] photos)
    news       every [news] check_minutes: `clipper news check` (the bot posts what it finds)
A job that exits non-zero is queued as a DM (clipper/alerts.py; the bot sends it).

Every minute it writes <streams>/.serve/status.json (clipper/daemon.py): what ran last,
what is running, what is due next. `clipper services status`, the war room's Health panel
and `clipper doctor` read it from any machine. One serve per streams folder (.serve.lock).
It logs to [paths] serve_log; deploy/serve/serve-task.ps1 runs it at logon.

The bot reaches it at [serve] url ("" = http://127.0.0.1:<[warroom] port>/) with the war
room token (the same [warroom] token or secrets file on both machines when they differ):
`replan` after a decision, `start_retake` after "Changes...", `health` for the tailnet link.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

import requests

from . import actions, alerts, daemon, news, preshow, retake, warroom
from .config import _root
from .errors import ClipperError

log = logging.getLogger("clipper.serve")

RETAKE_EVERY_S = 600   # with [retake] auto: look for new Kitsu notes this often
FIRST_PASS_S = 30      # settle (the war room warms up) before the first job


def has_opencv() -> bool:
    """`cast auto` checks faces with OpenCV (the faces extra)."""
    import importlib.util

    return importlib.util.find_spec("cv2") is not None


def due(jobs: list, last: dict[str, float], now: float) -> list[str]:
    """Names of the jobs whose interval has passed since they last started (an interval of
    0 or less: never)."""
    return [name for name, every, *_ in jobs
            if every > 0 and now - last.get(name, float("-inf")) >= every]


class Serve:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.last: dict[str, float] = {}   # job -> monotonic time it last started
        self.state: dict[str, dict] = {}   # job -> running, started, last_at, last_exit, last_line
        self.retake_again = False
        self.started_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.loop: asyncio.AbstractEventLoop | None = None

    def running(self, name: str) -> bool:
        proc = self.procs.get(name)
        return bool(proc and proc.returncode is None)

    def jobs(self) -> list[tuple[str, float, Callable[[], bool], Callable[[], object]]]:
        """(name, every s, ready() -> bool, start()) for the background jobs."""
        cfg, paths = self.cfg, self.cfg["paths"]
        has_upload = lambda: paths["schedule"].exists() \
            and '"uploaded"' in paths["schedule"].read_text(encoding="utf-8")
        task = lambda coro: self.loop.create_task(coro)
        return [
            ("upload", cfg["youtube"]["auto_upload_minutes"] * 60,
             lambda: not self.running("upload"), lambda: task(self.auto_upload())),
            ("preshow", cfg["preshow"]["poll_minutes"] * 60,
             lambda: not self.running("preshow") and bool(preshow.watched(cfg)),
             lambda: task(self.run_clipper("preshow", ["preshow", "check", "--run"]))),
            # retake notes made in Kitsu (review.json is synced by the bot's card refresh and the replan)
            ("retake", RETAKE_EVERY_S if cfg["retake"]["auto"] else 0,
             lambda: bool(retake.pending(cfg)), self.start_retake),
            ("tiktok", cfg["tiktok"]["auto_send_minutes"] * 60,
             lambda: paths["tiktok_token"].exists() and not self.running("tiktok"),
             lambda: task(self.run_clipper("tiktok", ["tiktok", "--auto"]))),
            ("x", cfg["x"]["auto_post_minutes"] * 60,
             lambda: paths["x_token"].exists() and not self.running("x"),
             lambda: task(self.run_clipper("x", ["x", "--auto"]))),
            ("analytics", cfg["analytics"]["auto_hours"] * 3600,
             lambda: paths["youtube_token"].exists() and has_upload() and not self.running("analytics"),
             lambda: task(self.run_clipper("analytics", ["analytics"]))),
            ("cast", cfg["cast"]["auto_hours"] * 3600,  # #41: photos for anyone short of them
             lambda: bool(cfg["thumbnail"]["photos"]) and has_opencv() and not self.running("cast"),
             lambda: task(self.run_clipper("cast", ["cast", "auto"]))),
            ("news", cfg["news"]["check_minutes"] * 60,  # #110: headlines about the watched terms
             lambda: not self.running("news") and bool(news.watchlist(cfg)),  # no terms: nothing to do
             lambda: task(self.run_clipper("news", ["news", "check"]))),
        ]

    async def run_clipper(self, name: str, args: list[str]) -> int | None:
        """Run `clipper <args>` as a child process, its output into the serve log."""
        cmd = actions.clipper_cmd(self.cfg)
        log.info(f"{name}: starting")
        st = self.state.setdefault(name, {})
        st.update(running=True, started=datetime.now(UTC).isoformat(timespec="seconds"), args=args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
            self.procs[name] = proc
            out, _ = await proc.communicate()
            lines = [x for x in out.decode("utf-8", "replace").splitlines()
                     if x.strip() and not x.startswith("[")]  # skip ffmpeg decoder noise
            for line in lines:
                log.info(f"{name}: {line}")
            log.info(f"{name}: done (exit {proc.returncode})")
            st.update(running=False, last_at=datetime.now(UTC).isoformat(timespec="seconds"),
                      last_exit=proc.returncode, last_line=(lines[-1] if lines else "")[:200])
            if proc.returncode:  # #22: a background job failing is worth a DM (once per cooldown)
                last = lines[-1].strip() if lines else ""
                alerts.send(self.cfg, "upload" if name in ("upload", "tiktok", "x") else "failure",
                            f"`clipper {' '.join(args)}` failed (exit {proc.returncode}): {last[:400]}",
                            key=f"bg:{' '.join(args)}")
            return proc.returncode
        except Exception as e:
            log.exception(f"{name} run failed")
            st.update(running=False, last_at=datetime.now(UTC).isoformat(timespec="seconds"),
                      last_exit=None, last_line=f"{type(e).__name__}: {e}"[:200])
            return None

    async def auto_upload(self) -> None:
        """Replan first (slots that passed get new ones), then upload what's due. A failed
        replan (Kitsu down) is DMed on its own and the upload still runs on the last plan,
        so one outage doesn't miss every slot that hour."""
        await self.run_clipper("upload", ["schedule"])
        await self.run_clipper("upload", ["upload"])

    def start_retake(self) -> None:
        """`clipper retake` in the background: act on retake notes, republish for review.
        A request while one runs makes it run once more when it ends."""
        if not self.cfg["retake"]["auto"]:
            return
        if self.running("retake"):
            self.retake_again = True
            return
        self.last["retake"] = time.monotonic()
        self.loop.create_task(self._retake())

    async def _retake(self) -> None:
        await self.run_clipper("retake", ["retake"])
        if self.retake_again:
            self.retake_again = False
            self.start_retake()

    async def jobs_loop(self) -> None:
        """Start each job when it's due and ready, once a minute."""
        await asyncio.sleep(FIRST_PASS_S)
        while True:
            jobs = self.jobs()
            for name, _every, ready, start in jobs:
                if name not in due(jobs, self.last, time.monotonic()):
                    continue
                try:
                    if not await asyncio.to_thread(ready):
                        continue
                    self.last[name] = time.monotonic()
                    start()
                except (Exception, SystemExit):  # SystemExit would stop the event loop
                    log.exception(f"{name} check failed")
            await asyncio.sleep(60)

    def status(self) -> dict:
        """Each job's state for the heartbeat: running, last run, next due."""
        now, out = time.monotonic(), {}
        for name, every, *_ in self.jobs():
            st = dict(self.state.get(name, {}))
            st["every_s"] = every
            if every <= 0:
                st["next_in_s"] = None
            elif name in self.last:
                st["next_in_s"] = max(0.0, every - (now - self.last[name]))
            else:
                st["next_in_s"] = 0.0
            out[name] = st
        return out

    async def beat_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(daemon.beat, self.cfg, "serve", self.started_at, jobs=self.status(),
                                        warroom=warroom.url(self.cfg, with_key=False),
                                        tailnet=warroom.TAILNET["served"])
            except Exception:
                log.exception("heartbeat failed")
            await asyncio.sleep(daemon.BEAT_S)

    async def main(self) -> None:
        self.loop = asyncio.get_running_loop()
        # The war room's "changes" decision and POST /api/retake start a retake here
        # (one runner, so a second request while one runs queues one more).
        warroom.HOOKS["retake"] = lambda: self.loop.call_soon_threadsafe(self.start_retake)
        await asyncio.gather(self.jobs_loop(), self.beat_loop())


def run(cfg: dict, port: int | None = None) -> None:
    daemon.setup_logging(cfg["paths"]["serve_log"])
    try:
        lock = daemon.single_instance(cfg, "serve")
    except ClipperError as e:
        log.error(str(e))
        raise
    log.info(f"clipper serve starting (pid {os.getpid()})")
    try:
        srv = warroom.server(cfg, port)
    except OSError as e:
        raise ClipperError(f"war room: port {port or _root(cfg)['warroom']['port']} is busy ({e}); is "
                           "`clipper warroom`, or a `clipper bot` from before the split, still serving it?") from None
    threading.Thread(target=srv.serve_forever, daemon=True, name="warroom").start()
    log.info(f"war room on {warroom.url(cfg, port, with_key=False)}")  # no token in the log
    try:
        asyncio.run(Serve(cfg).main())
    except KeyboardInterrupt:
        pass
    except BaseException:
        log.exception("clipper serve stopped")
        raise
    finally:
        srv.shutdown()
        lock.close()
    log.info("clipper serve stopped")


# ---------- the client: the bot (and doctor) talking to a running serve ----------

def url(cfg: dict) -> str:
    """[serve] url, else the war room on this machine. Always ends in /."""
    root = _root(cfg)
    u = (root["serve"]["url"] or "").strip() or f"http://127.0.0.1:{root['warroom']['port']}/"
    return u.rstrip("/") + "/"


def call(cfg: dict, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    """GET (body None) or POST one of the war room's /api routes on the running serve.
    ClipperError when it isn't up or refuses."""
    u = url(cfg) + path.lstrip("/")
    headers = {"X-Key": warroom.token(cfg)}
    try:
        if body is None:
            r = requests.get(u, headers=headers, timeout=timeout)
        else:
            r = requests.post(u, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise ClipperError(f"clipper serve isn't answering at {url(cfg)} ({type(e).__name__})") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        why = data.get("error", "") if isinstance(data, dict) else ""
        raise ClipperError(f"clipper serve {path}: {why or 'refused'} (HTTP {r.status_code})")
    return data


def health(cfg: dict) -> dict:
    return call(cfg, "/api/health", timeout=15)


def tailnet_link(cfg: dict) -> str:
    """The war room's https tailnet URL, or "" (serve not up, or not on the tailnet)."""
    try:
        return str(health(cfg).get("tailnet") or "")
    except ClipperError:
        return ""


def replan(cfg: dict, sync: bool = False) -> dict:
    """`clipper schedule` on the serve process (it owns schedule.json); sync: read Kitsu first."""
    return call(cfg, "/api/replan", {"sync": sync}, timeout=600)


def start_retake(cfg: dict) -> dict:
    return call(cfg, "/api/retake", {}, timeout=15)
