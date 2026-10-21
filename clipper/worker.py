"""Hands-off processing of finished recordings.

`clipper run <stream>` chains the pipeline stages for one stream: signals, transcribe,
score, segments, cut, meta, thumbs, review publish. Each stage runs as its own
`clipper <stage>` process (so the GPU memory WhisperX holds is freed after transcribe),
logging to <stream>/run_<stage>.log. Progress goes to <stream>/run.json, so a failed or
interrupted run resumes at the stage that didn't finish. A stage that finished before
run.json existed (a stream processed by hand) counts as done when its output is there.

Clips first ([worker] fast_lane): cut, meta, thumbs and review each run twice. The fast
lane cuts only the picks (stage "clips"), drafts and covers them, renders their Shorts and
publishes those for review; the slow lane then cuts the long segments and compilations
("cut"), which take hours to cut and preview. The second meta and thumbs pass only fill
in what the first left out (--missing), so the clips under review keep their drafts.

`clipper worker` (the GPU machine, deploy/worker) polls paths.streams for recordings the
NAS recorder finished (live/state.json status "done") and runs them: every waiting
stream's fast lane first, then the slow lanes oldest first, and a slow lane steps aside
between stages when a newer stream's clips are waiting. paths.streams is the NAS share,
so nothing is copied. It also runs pre-shows the bot grabbed from YouTube
(streams/yt-<id>, clipper/preshow.py) with [preshow] stages instead.
<stream>/.run.lock (with a heartbeat in run.json) keeps two workers off the same stream.
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
from datetime import UTC, datetime
from pathlib import Path

from . import preshow
from .config import for_stream, show_of_channel

log = logging.getLogger("clipper.worker")

STAGES = ["signals", "transcribe", "score", "segments",
          "clips", "clips_meta", "clips_thumbs", "shorts", "clips_review",
          "cut", "meta", "thumbs", "review"]
FAST_LANE = STAGES[:STAGES.index("clips_review") + 1]
# The fast-lane twin of each slow stage ([worker] stages lists the slow names).
FAST_TWIN = {"cut": "clips", "meta": "clips_meta", "thumbs": "clips_thumbs",
             "review": "clips_review"}
# Stages that only have work when the cut before them made videos, and which cut that is
# (without the fast lane, "cut" cuts everything and the clips stages don't run).
AFTER_CUT = {"clips_meta": "clips", "clips_thumbs": "clips", "clips_review": "clips",
             "meta": "cut", "thumbs": "cut", "shorts": "cut", "review": "cut"}

# Output that shows a stage already ran (for streams processed before run.json): any of
# these paths, globs allowed.
DONE_MARKERS = {
    "signals": ["windows.json"],
    "transcribe": ["words.jsonl"],
    "score": ["candidates.json"],
    "segments": ["segments.json"],
    "clips": ["clips"],
    "clips_meta": ["metadata/clips"],
    "clips_thumbs": ["clips/*.jpg"],
    "shorts": ["shorts.json"],
    "clips_review": ["review_state.json"],
    "cut": ["segments"],
    "meta": ["metadata/segments"],
    "thumbs": ["segments/*.jpg"],
    "review": ["review/s[0-9]*.mp4"],
}


def output_found(stream_dir: Path, stage: str) -> bool:
    return any(next(stream_dir.glob(m), None) is not None for m in DONE_MARKERS[stage])


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")


def cut_videos(stream_dir: Path, subs=("segments", "clips", "compilations")) -> list[Path]:
    return [v for sub in subs for v in (stream_dir / sub).glob("*.mp4")]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a reader (war room, bot) has it open on the NAS share
            if attempt == 9:
                raise
            time.sleep(0.2 * (attempt + 1))


def stage_args(stage: str, stream: str, wc: dict) -> list[str]:
    if stage in ("review", "clips_review"):
        return ["review", "publish", stream] + ([] if wc["notify"] else ["--no-notify"])
    if stage == "clips":
        return ["cut", stream, "picks"]
    if wc["fast_lane"]:
        if stage == "cut":
            return ["cut", stream, "segments", "compilations"]
        if stage in ("meta", "thumbs", "clips_meta", "clips_thumbs"):
            # Only what has no draft/cover yet: the clips in review keep theirs.
            return [stage.removeprefix("clips_"), stream, "--missing"]
    return [stage.removeprefix("clips_"), stream]


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
        status = self.data["stages"].get(stage, {}).get("status")
        if status in ("done", "skipped"):
            return True
        if status in ("running", "failed"):
            return False  # interrupted or failed: its outputs may be partial
        return output_found(self.d, stage)


def default_stages(cfg: dict, stream_dir: Path) -> list[str]:
    """A pre-show (one whole video, no chat) skips signals, scoring and Shorts. With the
    fast lane, a scored stream also gets the clips twins of cut, meta, thumbs, review."""
    if preshow.is_preshow(stream_dir):
        return for_stream(cfg, stream_dir)["preshow"]["stages"]
    wanted = list(cfg["worker"]["stages"])
    if cfg["worker"]["fast_lane"] and "score" in wanted:
        wanted += [FAST_TWIN[s] for s in wanted if s in FAST_TWIN]
    return wanted


def plan(cfg: dict, stream_dir: Path, stages: list[str] | None = None,
         lane: str | None = None) -> list[str]:
    """The stages to run, in order: `stages` as given, or the stream's defaults; lane
    "fast" or "slow" keeps one side of FAST_LANE."""
    order = [s for s in STAGES if s in (stages or default_stages(cfg, stream_dir))]
    if lane:
        order = [s for s in order if (s in FAST_LANE) == (lane == "fast")]
    return order


def cut_before(stage: str, wc: dict) -> tuple[str, tuple[str, ...]] | None:
    """The cut stage `stage` waits on, and the folders that cut fills."""
    cut = "clips" if stage == "shorts" and wc["fast_lane"] else AFTER_CUT.get(stage)
    if not cut:
        return None
    if cut == "clips":
        return cut, ("clips",)
    return cut, ("segments", "compilations") + (() if wc["fast_lane"] else ("clips",))


def waiting(cfg: dict, stream_dir: Path, lane: str | None = None) -> bool:
    """Has this stream stages left to run (in that lane)?"""
    run = Run(stream_dir, cfg["worker"])
    return any(not run.done(s) for s in plan(cfg, stream_dir, lane=lane))


def run_stream(cfg: dict, stream_dir: Path, config_path: str | None = None,
               stages: list[str] | None = None, force: bool = False, lane: str | None = None,
               step_aside=None) -> bool:
    """Run the stages that aren't done yet. False when a stage failed (run.json has why).
    step_aside(): checked before each slow-lane stage; when it returns a reason, stop
    there (run.json "partial") so other work can go first."""
    wc = cfg["worker"]
    wanted = default_stages(cfg, stream_dir)
    order = plan(cfg, stream_dir, stages, lane)
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
            before = cut_before(stage, wc)
            if before and run.done(before[0]) and not cut_videos(stream_dir, before[1]):
                # No picks, or no segment long enough: nothing to describe or review.
                run.data["stages"][stage] = {"status": "skipped", "note": "nothing was cut"}
                continue
            reason = step_aside() if step_aside and stage not in FAST_LANE else None
            if reason:
                log.info("%s: pausing before %s: %s", name, stage, reason)
                run.data["paused"] = f"before {stage}: {reason}"
                break
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
        else:
            run.data.pop("paused", None)
        complete = all(run.done(s) for s in STAGES if s in wanted)
        # A lane that ran to its end clears the count of failed attempts (each lane takes one).
        run.data.update(status="done" if complete else "partial", error="", attempts=0)
        if "clips_review" in wanted and all(run.done(s) for s in FAST_LANE if s in wanted):
            run.data.setdefault("clips_ready", now_iso())  # when the clips went to review
        if complete:
            run.data["finished"] = now_iso()
            run.data["nothing_cut"] = not cut_videos(stream_dir)
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
    ready = []
    for state_path in sorted(root.glob("*/live/state.json")):
        d = state_path.parent.parent
        state = read_json(state_path)
        if state.get("status") != "done":
            continue
        if wc["channels"] and state.get("channel") not in wc["channels"]:
            continue
        if cfg["shows"] and not show_of_channel(cfg, state.get("channel", "")):
            continue  # recorded only: add a [shows.<key>] profile for the channel to clip it
        ready.append(d)
    # Grabbed pre-shows: meta.json is written once the download is complete.
    ready += [m.parent for m in sorted(root.glob(f"{preshow.PREFIX}*/meta.json"))]
    out = []
    for d in ready:
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


def clips_waiting(cfg: dict) -> str | None:
    """Why a slow lane should step aside: a stream whose fast lane hasn't run."""
    try:
        d = next((d for d in pending(cfg) if waiting(cfg, d, "fast")), None)
    except OSError:
        return None
    return f"{d.name}'s clips go first" if d else None


def since_end(stream_dir: Path, t: float) -> str:
    ended = read_json(stream_dir / "meta.json").get("ended_at")
    if ended:
        return f"{age_s(ended) / 60:.0f} min after the stream ended"
    return f"{(time.time() - t) / 60:.0f} min"


def watch(cfg: dict, config_path: str | None = None, once: bool = False) -> None:
    from .record import Alerts

    wc = cfg["worker"]
    alert = Alerts(cfg)
    root: Path = cfg["paths"]["streams"]
    log.info("worker on %s: watching %s every %ss", socket.gethostname(), root, wc["poll_s"])

    def one(d: Path, lane: str | None, step_aside=None) -> None:
        try:
            t = time.time()
            run_stream(cfg, d, config_path, lane=lane, step_aside=step_aside)
            # (> 60 s: not just a stream processed earlier being marked done)
            run = read_json(d / "run.json")
            if time.time() - t <= 60:
                pass
            elif run.get("status") == "done":
                alert(f"clipper worker: {d.name} " + (
                    "had no picks or segments to cut; nothing to review"
                    if run.get("nothing_cut") else "is in review")
                    + f" ({since_end(d, t)})", level=logging.INFO)
            elif lane == "fast" and run.get("clips_ready") and cut_videos(d, ("clips",)):
                alert(f"clipper worker: {d.name} clips and Shorts are in review ({since_end(d, t)});"
                      " segments next", level=logging.INFO)
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

    reported_offline = False
    while True:
        stepped_aside = False
        try:
            todo = pending(cfg)
            reported_offline = False
        except OSError as e:  # share not mounted / NAS asleep
            if not reported_offline:
                alert(f"clipper worker ({socket.gethostname()}): can't read {root}: {e}")
                reported_offline = True
            todo = []
        if wc["fast_lane"]:
            # Every waiting stream's clips and Shorts first, then the long segments, oldest
            # stream first; those step aside between stages when a newer stream's clips wait.
            for d in [d for d in todo if waiting(cfg, d, "fast")]:
                one(d, "fast")
            for d in todo:
                if not waiting(cfg, d, "fast"):  # (else its fast lane failed or is busy)
                    one(d, "slow", step_aside=lambda: clips_waiting(cfg))
                if clips_waiting(cfg):
                    stepped_aside = True
                    break
        else:
            for d in todo:
                one(d, None)
        if once:
            return
        if not stepped_aside:
            time.sleep(wc["poll_s"])


def status(cfg: dict) -> None:
    root: Path = cfg["paths"]["streams"]
    for d in sorted(p for p in root.iterdir() if (p / "run.json").exists() or (p / "live").is_dir()):
        run = read_json(d / "run.json")
        rec = read_json(d / "live" / "state.json").get("status", "-")
        stages = run.get("stages", {})
        last = next((s for s in reversed(STAGES) if stages.get(s, {}).get("status")), "")
        print(f"{d.name:<18} rec={rec:<10} run={run.get('status', 'new'):<8} "
              f"last={last}:{stages.get(last, {}).get('status', '')} "
              + (f"clips in review {run['clips_ready'][5:16]} " if run.get("clips_ready") else "")
              + f"{run.get('error') or run.get('paused', '')}"[:80])
