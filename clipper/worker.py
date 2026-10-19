"""Hands-off processing of finished recordings.

`clipper run <stream>` chains the pipeline stages for one stream: signals, transcribe,
score, segments, cut, meta, thumbs, review publish. Each stage runs as its own
`clipper <stage>` process (so the GPU memory WhisperX holds is freed after transcribe),
logging to <stream>/run_<stage>.log. Progress goes to <stream>/run.json, so a failed or
interrupted run resumes at the stage that didn't finish. A stage that finished before
run.json existed (a stream processed by hand) counts as done when its output is there.

`clipper worker` (the GPU machine, deploy/worker) polls paths.streams for recordings the
NAS recorder finished (live/state.json status "done") and runs them. paths.streams is
the NAS share, so nothing is copied. <stream>/.run.lock (with a heartbeat in run.json)
keeps two workers off the same stream.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import show_of_channel

log = logging.getLogger("clipper.worker")

STAGES = ["signals", "transcribe", "score", "segments", "cut", "meta", "thumbs", "shorts",
          "review"]
AFTER_CUT = ("meta", "thumbs", "shorts", "review")

# Output that shows a stage already ran (for streams processed before run.json).
DONE_MARKERS = {
    "signals": ["windows.json"],
    "transcribe": ["words.jsonl"],
    "score": ["candidates.json"],
    "segments": ["segments.json"],
    "cut": ["segments", "clips"],
    "meta": ["metadata"],
    "thumbs": ["thumbnails.json"],
    "shorts": ["shorts.json"],
    "review": ["review_state.json"],
}


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")


def cut_videos(stream_dir: Path) -> list[Path]:
    return [v for sub in ("segments", "clips", "compilations")
            for v in (stream_dir / sub).glob("*.mp4")]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def age_s(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return time.time() - datetime.fromisoformat(iso).timestamp()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def stage_args(stage: str, stream: str, wc: dict) -> list[str]:
    if stage == "review":
        return ["review", "publish", stream] + ([] if wc["notify"] else ["--no-notify"])
    return [stage, stream]


def keep_awake(on: bool) -> None:
    """Stop Windows from sleeping while a run is going (no-op elsewhere)."""
    if sys.platform == "win32":
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


class Busy(Exception):
    """Another worker holds the stream."""


class Run:
    """One stream's run.json plus its lock and heartbeat."""

    def __init__(self, stream_dir: Path, wc: dict):
        self.d = stream_dir
        self.wc = wc
        self.path = stream_dir / "run.json"
        self.lock = stream_dir / ".run.lock"
        self.data = read_json(self.path)
        self.data.setdefault("stream", stream_dir.name)
        self.data.setdefault("stages", {})
        self._stop = threading.Event()
        self._mu = threading.Lock()
        self._beat: threading.Thread | None = None

    def save(self) -> None:
        with self._mu:
            self.data["updated"] = now_iso()
            write_json(self.path, self.data)

    def acquire(self) -> None:
        me = f"{socket.gethostname()}:{os.getpid()}"
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Taken over only when its holder stopped writing heartbeats.
            held = read_json(self.path)
            stale = age_s(held.get("heartbeat")) > self.wc["stale_minutes"] * 60
            if not stale:
                raise Busy(f"{self.d.name}: running on {held.get('worker', '?')}")
            log.warning("%s: taking over a stale lock from %s", self.d.name, held.get("worker"))
            self.lock.unlink(missing_ok=True)
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, me.encode())
        os.close(fd)
        self.data = read_json(self.path) or self.data
        self.data.setdefault("stages", {})
        self.data.update(worker=me, status="running", heartbeat=now_iso(), error="")
        self.data["attempts"] = self.data.get("attempts", 0) + 1
        self.save()
        self._beat = threading.Thread(target=self._heartbeat, daemon=True)
        self._beat.start()

    def _heartbeat(self) -> None:
        while not self._stop.wait(60):
            self.data["heartbeat"] = now_iso()
            try:
                self.save()
            except OSError as e:  # NAS blip; the next beat retries
                log.warning("%s: heartbeat not written: %s", self.d.name, e)

    def release(self) -> None:
        self._stop.set()
        if self._beat:
            self._beat.join(timeout=5)
        self.data.pop("worker", None)
        self.save()
        self.lock.unlink(missing_ok=True)

    def done(self, stage: str) -> bool:
        if self.data["stages"].get(stage, {}).get("status") in ("done", "skipped"):
            return True
        return any((self.d / m).exists() for m in DONE_MARKERS[stage])


def run_stream(cfg: dict, stream_dir: Path, config_path: str | None = None,
               stages: list[str] | None = None, force: bool = False) -> bool:
    """Run the stages that aren't done yet. False when a stage failed (run.json has why)."""
    wc = cfg["worker"]
    order = [s for s in STAGES if s in (stages or wc["stages"])]
    run = Run(stream_dir, wc)
    run.acquire()
    keep_awake(True)
    name = stream_dir.name
    ok = False
    try:
        for stage in order:
            if not force and run.done(stage):
                run.data["stages"].setdefault(stage, {"status": "done", "note": "output found"})
                continue
            if stage in AFTER_CUT and run.done("cut") and not cut_videos(stream_dir):
                # No picks and no segment long enough: nothing to describe or review.
                run.data["stages"][stage] = {"status": "skipped", "note": "nothing was cut"}
                run.data["nothing_cut"] = True
                continue
            log_path = stream_dir / f"run_{stage}.log"
            cmd = [sys.executable, "-m", "clipper.cli"]
            if config_path:
                cmd += ["--config", str(config_path)]
            cmd += stage_args(stage, str(stream_dir), wc)
            st = run.data["stages"][stage] = {"status": "running", "started": now_iso(),
                                              "log": log_path.name}
            run.save()
            log.info("%s: %s", name, stage)
            t = time.time()
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            with open(log_path, "a", encoding="utf-8") as out:
                out.write(f"\n=== {now_iso()} {' '.join(cmd[3:])}\n")
                out.flush()
                code = subprocess.call(cmd, stdout=out, stderr=subprocess.STDOUT, env=env,
                                       cwd=Path(config_path).resolve().parent if config_path else None)
            st.update(finished=now_iso(), seconds=round(time.time() - t), exit=code)
            if code:
                st["status"] = "failed"
                tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
                run.data.update(status="failed", error=f"{stage} exited {code}: " + " | ".join(tail))
                run.save()
                log.error("%s: %s failed (exit %s), see %s", name, stage, code, log_path)
                return False
            st["status"] = "done"
            log.info("%s: %s done in %ds", name, stage, st["seconds"])
            run.save()
        complete = all(run.done(s) for s in STAGES if s in wc["stages"])
        run.data.update(status="done" if complete else "partial", error="")
        if complete:
            run.data["finished"] = now_iso()
        ok = True
        return True
    except BaseException as e:  # Ctrl+C or a crash: leave a resumable record
        run.data.update(status="failed", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        keep_awake(False)
        run.release()
        if ok:
            log.info("%s: run %s", name, run.data["status"])


def pending(cfg: dict) -> list[Path]:
    """Finished recordings whose run isn't done, oldest first, minus ones to wait on."""
    wc = cfg["worker"]
    root: Path = cfg["paths"]["streams"]
    out = []
    for state_path in sorted(root.glob("*/live/state.json")):
        d = state_path.parent.parent
        state = read_json(state_path)
        if state.get("status") != "done":
            continue
        if wc["channels"] and state.get("channel") not in wc["channels"]:
            continue
        if cfg["shows"] and not show_of_channel(cfg, state.get("channel", "")):
            continue  # recorded only: add a [shows.<key>] profile for the channel to clip it
        run = read_json(d / "run.json")
        if run.get("status") in ("done", "skip"):
            continue
        if run.get("status") == "running" and age_s(run.get("heartbeat")) < wc["stale_minutes"] * 60:
            continue
        if run.get("status") == "failed":
            if run.get("attempts", 0) >= wc["max_attempts"]:
                continue
            if age_s(run.get("updated")) < wc["retry_minutes"] * 60:
                continue
        out.append(d)
    return out


def watch(cfg: dict, config_path: str | None = None, once: bool = False) -> None:
    from .record import Alerts

    wc = cfg["worker"]
    alert = Alerts(cfg)
    root: Path = cfg["paths"]["streams"]
    log.info("worker on %s: watching %s every %ss", socket.gethostname(), root, wc["poll_s"])
    reported_offline = False
    while True:
        try:
            todo = pending(cfg)
            reported_offline = False
        except OSError as e:  # share not mounted / NAS asleep
            if not reported_offline:
                alert(f"clipper worker ({socket.gethostname()}): can't read {root}: {e}")
                reported_offline = True
            todo = []
        for d in todo:
            try:
                t = time.time()
                run_stream(cfg, d, config_path)
                # (> 60 s: not just a stream processed earlier being marked done)
                run = read_json(d / "run.json")
                if run.get("status") == "done" and time.time() - t > 60:
                    alert(f"clipper worker: {d.name} " + (
                        "had no picks or segments to cut; nothing to review"
                        if run.get("nothing_cut") else "is in review")
                        + f" ({(time.time() - t) / 60:.0f} min)", level=logging.INFO)
            except Busy as e:
                log.info("%s", e)
            except Exception as e:
                log.exception("%s: run crashed", d.name)
                alert(f"clipper worker: {d.name} crashed: {e}")
            run = read_json(d / "run.json")
            if run.get("status") == "failed":
                left = wc["max_attempts"] - run.get("attempts", 0)
                alert(f"clipper worker: {d.name} failed: {run.get('error', '')[:600]}"
                      + (f" (retry in {wc['retry_minutes']} min, {left} left)" if left > 0
                         else " (no retries left: fix it, then `clipper run " + d.name + "`)"))
        if once:
            return
        time.sleep(wc["poll_s"])


def status(cfg: dict) -> None:
    root: Path = cfg["paths"]["streams"]
    for d in sorted(p for p in root.iterdir() if (p / "run.json").exists() or (p / "live").is_dir()):
        run = read_json(d / "run.json")
        rec = read_json(d / "live" / "state.json").get("status", "-")
        stages = run.get("stages", {})
        last = next((s for s in reversed(STAGES) if stages.get(s, {}).get("status")), "")
        print(f"{d.name:<18} rec={rec:<10} run={run.get('status', 'new'):<8} "
              f"last={last}:{stages.get(last, {}).get('status', '')} {run.get('error', '')[:80]}")
