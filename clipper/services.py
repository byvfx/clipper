"""clipper's long-running pieces in one place: status, stop and restart.

    clipper services [status]                 the bot, the GPU worker, the recorder, NAS cuts
    clipper services stop|restart bot|worker|all
    clipper services stop|restart recorder [--force]
    clipper record status                     the recorder alone (exit 3 while recording)

The bot and the worker are Windows scheduled tasks; this runs their deploy scripts
(deploy/discord/bot-task.ps1, deploy/worker/worker-task.ps1), which also stop the
supervisor and the stage processes. The recorder is a container on the NAS: its state is
the heartbeat it writes to the streams share every 2 s (streams/.record/status.json), and
stop/restart go over ssh like deploy/record/deploy.sh (RECORD_SSH_HOST, RECORD_DIR,
RECORD_SUDO). A restart starts a new recording part, so it's refused while a show is being
recorded unless --force; `all` never includes the recorder. NAS cuts are the
clipper-cut-<stream> containers `[cut] backend = "nas"` runs; one left after its cut ended
can be removed with `ssh <nas> docker rm -f <name>` (status shows the command).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .errors import ClipperError

ROOT = Path(__file__).resolve().parent.parent
TASKS = {"bot": ROOT / "deploy" / "discord" / "bot-task.ps1",
         "worker": ROOT / "deploy" / "worker" / "worker-task.ps1"}
ACTION_FLAG = {"status": "-Status", "stop": "-Stop", "restart": "-Restart"}
HEARTBEAT_S = 60   # the recorder writes every 2 s; a minute without one means it's down


def recorder(cfg: dict, now: datetime | None = None) -> dict:
    """The recorder's heartbeat: {up, age_s, at, channels: {slug: state}, live: [slug ...]}.
    A channel's state is "<stream id> recording|paused|finalizing" or "offline"."""
    path = cfg["paths"]["streams"] / ".record" / "status.json"
    hb = jsonstate.read(path, {})
    at = hb.get("at") or ""
    try:
        age = ((now or datetime.now(UTC)) - datetime.fromisoformat(at)).total_seconds()
    except ValueError:
        age = None
    up = age is not None and age < HEARTBEAT_S
    channels = hb.get("channels") or {}
    live = [slug for slug, state in channels.items() if up and state != "offline"]
    return {"up": up, "age_s": age, "at": at, "channels": channels, "live": live, "path": str(path)}


def recorder_lines(rec: dict) -> list[str]:
    if rec["age_s"] is None:
        return [f"recorder: no heartbeat at {rec['path']} (not deployed, or the share isn't reachable)"]
    if not rec["up"]:
        return [f"recorder: DOWN, last heartbeat {rec['age_s'] / 60:.0f} min ago ({rec['at']})"]
    out = [f"recorder: up ({rec['age_s']:.0f} s ago); "
           + (f"RECORDING {', '.join(rec['live'])}" if rec["live"] else "nothing live")]
    out += [f"  {slug}: {state}" for slug, state in sorted(rec["channels"].items())]
    return out


def record_status(cfg: dict, now: datetime | None = None) -> None:
    """`clipper record status`: exit 3 while a show is being recorded (deploy.sh checks it)."""
    rec = recorder(cfg, now)
    print("\n".join(recorder_lines(rec)))
    if rec["live"]:
        raise ClipperError("", status=3)


def run_task(name: str, action: str) -> int:
    """The bot's or worker's task script (Windows); its output goes straight through."""
    if sys.platform != "win32":
        print(f"{name}: not a Windows scheduled task on this machine ({TASKS[name].name} is Windows only)")
        return 0
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(TASKS[name]), ACTION_FLAG[action]], check=False).returncode


def ssh(host: str, command: str, timeout: float = 20) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, command],
                              capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess([host], 255, "", str(e))


def nas_cuts(cfg: dict) -> list[str]:
    """The clipper-cut-* containers on the NAS, one "name  status" line each."""
    host = cfg["cut"]["nas_host"]
    if not host:
        return []
    r = ssh(host, "docker ps --filter name=clipper-cut- --format '{{.Names}}  {{.Status}}'")
    if r.returncode:
        return [f"(couldn't list them on {host}: {(r.stderr or r.stdout).strip()[:120]})"]
    return [f"{line}   (remove: ssh {host} docker rm -f {line.split()[0]})"
            for line in r.stdout.splitlines() if line.strip()]


def recorder_compose(action: str) -> int:
    host = os.environ.get("RECORD_SSH_HOST") or "nas"
    folder = os.environ.get("RECORD_DIR") or "clipper-record"
    sudo = os.environ.get("RECORD_SUDO", "")
    cmd = f"cd {shlex.quote(folder)} && {sudo} docker compose {action} && {sudo} docker compose ps"
    print(f"recorder: docker compose {action} on {host}", flush=True)
    r = ssh(host, cmd, timeout=120)
    print((r.stdout + r.stderr).strip())
    return r.returncode


def run(cfg: dict, action: str = "status", which: str = "all", force: bool = False) -> int:
    if action == "status":
        for name in (["bot", "worker"] if which == "all" else [which] if which in TASKS else []):
            print(f"===== {name}", flush=True)
            run_task(name, "status")
        if which in ("all", "recorder"):
            print("===== recorder")
            print("\n".join(recorder_lines(recorder(cfg))))
            cuts = nas_cuts(cfg)
            print("NAS cuts: " + ("none running" if not cuts else "\n  " + "\n  ".join(cuts)))
        return 0
    if which == "recorder":
        live = recorder(cfg)["live"]
        if live and not force:
            raise ClipperError(f"recording {', '.join(live)} now: a {action} would start a new recording part. "
                               "Wait until the show ends, or pass --force")
        return recorder_compose(action)
    worst = 0
    for name in (["worker", "bot"] if which == "all" else [which]):
        print(f"===== {name}", flush=True)
        worst = max(worst, run_task(name, action))
    return worst
