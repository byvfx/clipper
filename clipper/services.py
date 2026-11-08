"""clipper's long-running pieces in one place: status, stop and restart.

    clipper services [status]                 serve, the bot, the GPU worker, the recorder, NAS cuts
    clipper services stop|restart serve|bot|worker|all
    clipper services stop|restart recorder [--force]
    clipper record status                     the recorder alone (exit 3 while recording)

serve, the bot and the worker are containers on a Docker host when [serve] hub is set
(deploy/hub; `docker compose` in that folder over ssh). Without it, the worker on Windows is a
scheduled task (deploy/worker/worker-task.ps1, which also stops the supervisor and the stage
processes) and serve and the bot are whatever you started by hand (`clipper serve`, `clipper
bot`): status shows their heartbeats, stop and restart say so. serve and the bot write a
heartbeat to the streams share every minute (streams/.serve/status.json, .bot/status.json,
clipper/daemon.py), shown whatever machine they run on. The recorder is a container on
the NAS: its state is the heartbeat it writes every 2 s (streams/.record/status.json), and
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

from . import daemon
from .errors import ClipperError

ROOT = Path(__file__).resolve().parent.parent
TASKS = {"worker": ROOT / "deploy" / "worker" / "worker-task.ps1"}  # serve and the bot: deploy/hub, or by hand
SERVICES = ("serve", "bot", "worker")
HEARTBEATS = ("serve", "bot")  # write one (clipper/daemon.py); the recorder has its own
ACTION_FLAG = {"status": "-Status", "stop": "-Stop", "restart": "-Restart"}
HEARTBEAT_S = 60   # the recorder writes every 2 s; a minute without one means it's down


def recorder(cfg: dict, now: datetime | None = None) -> dict:
    """The recorder's heartbeat: {up, age_s, at, channels: {slug: state}, live: [slug ...]}.
    A channel's state is "<stream id> recording|paused|finalizing" or "offline"."""
    path = cfg["paths"]["streams"] / ".record" / "status.json"
    hb = daemon.read_heartbeat(path)
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


HUB_ACTION = {"status": "ps", "stop": "stop", "restart": "restart"}


def hub_compose(cfg: dict, name: str, action: str) -> int:
    """serve, bot or worker as a container on the NAS ([serve] hub): docker compose over ssh."""
    host, _, sudo = recorder_ssh(cfg)
    folder = cfg["serve"]["hub"]
    cmd = f"cd {shlex.quote(folder)} && {sudo} docker compose {HUB_ACTION[action]} {name}"
    if action != "status":
        cmd += f" && {sudo} docker compose ps {name}"
    r = ssh(host, cmd, timeout=120)
    print((r.stdout + r.stderr).strip() or f"{name}: no output from {host}")
    return r.returncode


def run_task(name: str, action: str, cfg: dict | None = None) -> int:
    """The hub container ([serve] hub), or the worker's task script (Windows); its output goes
    straight through. serve and the bot have no task: compose (deploy/hub) or by hand."""
    if cfg and cfg["serve"]["hub"]:
        return hub_compose(cfg, name, action)
    if name not in TASKS:
        if action == "status":
            print(f"{name}: no [serve] hub here; its heartbeat above is all this machine knows")
            return 0
        print(f"{name}: runs under Docker compose (deploy/hub; set [serve] hub to that folder for {action}) "
              f"or by hand (`clipper {name}`): nothing to {action} here")
        return 1
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


def recorder_ssh(cfg: dict) -> tuple[str, str, str]:
    """(ssh host, compose dir, sudo prefix): [record] ssh_host / compose_dir / sudo, else the
    RECORD_* variables deploy/record/deploy.sh reads, else its defaults."""
    rc = cfg["record"]
    return (rc["ssh_host"] or os.environ.get("RECORD_SSH_HOST") or "nas",
            rc["compose_dir"] or os.environ.get("RECORD_DIR") or "clipper-record",
            rc["sudo"] or os.environ.get("RECORD_SUDO", ""))


def recorder_compose(cfg: dict, action: str) -> int:
    host, folder, sudo = recorder_ssh(cfg)
    cmd = f"cd {shlex.quote(folder)} && {sudo} docker compose {action} && {sudo} docker compose ps"
    print(f"recorder: docker compose {action} on {host}", flush=True)
    r = ssh(host, cmd, timeout=120)
    print((r.stdout + r.stderr).strip())
    return r.returncode


def run(cfg: dict, action: str = "status", which: str = "all", force: bool = False) -> int:
    if action == "status":
        for name in (list(SERVICES) if which == "all" else [which] if which in SERVICES else []):
            print(f"===== {name}", flush=True)
            if name in HEARTBEATS:
                print("\n".join(daemon.heartbeat_lines(name, daemon.heartbeat(cfg, name))), flush=True)
            run_task(name, "status", cfg)
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
        return recorder_compose(cfg, action)
    worst = 0
    for name in (["worker", "serve", "bot"] if which == "all" else [which]):
        print(f"===== {name}", flush=True)
        worst = max(worst, run_task(name, action, cfg))
    return worst
