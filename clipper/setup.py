"""`clipper setup`: a guided first run on a new machine (#78).

Usage:
    clipper setup                 # the steps not done yet, in order
    clipper setup --step keys     # one step again
    clipper setup --check         # each step's checks, no questions
    clipper setup --answers a.toml   # no questions: values from the file, defaults for the rest

Steps: mode (join an existing install, or a new one), roles (what this machine runs, and
where it transcribes), machine (streams folder, encoder), keys (API keys and tokens, checked
as they are entered), check (`clipper doctor` for this machine's roles). Progress is kept in
setup_state.json beside clipper.toml. A step is done when its checks pass, and this
machine's own values go in clipper.local.toml, so clipper.toml stays shared.

Keys are never written to the config, the answers file or the log: Windows user environment
variables, the shell profile on macOS/Linux, or secrets/ (the Discord token).

When the roles need packages that aren't installed, setup exits with EXIT_SYNC after saving
its state; deploy/bootstrap.ps1 (or .sh) then runs `uv sync $(clipper setup --sync-args)` and
starts setup again. (On Windows uv can't replace clipper.exe while it runs.)
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import sys
import time
import tomllib
from pathlib import Path

from . import config, doctor, jsonstate, wiki
from .config import _root
from .errors import ClipperError

STEPS = ("mode", "roles", "machine", "keys", "check")
TITLES = {"mode": "Join or start fresh", "roles": "What this machine runs",
          "machine": "This machine's folders and encoder", "keys": "Keys and tokens",
          "check": "Check everything"}
STATE = "setup_state.json"
EXIT_SYNC = 3                     # packages missing: the bootstrap script syncs and reruns setup
ROLES = {"worker": "worker: transcribe, score, cut, Shorts, covers (`clipper worker`)",
         "serve": "serve: the war room, hourly uploads, pre-shows, retakes, daily jobs (`clipper serve`, one machine only)",
         "bot": "bot: Discord review cards, buttons and DMs (`clipper bot`, one machine only)",
         "recorder": "recorder: records the Kick shows live (Docker on the NAS)",
         "control": "control: a dev/control station; runs nothing unattended (`clipper services`, deploys, "
                    "review, one-off commands)"}
BACKENDS = {"whisperx": "WhisperX on this machine's NVIDIA GPU",
            "mlx": "mlx-whisper on this Mac's Apple GPU",
            "modal": "WhisperX on a Modal cloud GPU (any machine; ~$0.34 per 8 h stream)"}
EXTRA_MODULE = {"gpu": "whisperx", "mac": "mlx_whisper", "modal": "modal", "discord": "discord", "faces": "cv2"}


# ---------- questions ----------

def _expand(v: str) -> Path:
    try:
        return Path(v).expanduser()
    except RuntimeError:          # ~name for a user that doesn't exist: left as typed
        return Path(v)


def user_path(raw: str) -> Path:
    """A path as people type or paste it: `~` expanded, and the shell quoting that dragging a
    file into the terminal (macOS/Linux) or the quotes that "Copy as path" (Windows) adds.
    Of the readings that differ, the first that exists wins, so a literal apostrophe works too."""
    v = raw.strip()
    readings = []
    if os.name != "nt" and any(c in v for c in "'\"\\"):
        try:
            words = shlex.split(v)
        except ValueError:        # an unclosed quote: a literal apostrophe, most likely
            words = []
        if len(words) == 1:
            readings.append(words[0])
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        readings.append(v[1:-1])
    readings.append(v)
    paths = [_expand(r) for r in readings]
    return next((p for p in paths if p.exists()), paths[0])


def config_file(raw: str) -> Path:
    """The main machine's clipper.toml, given as the file or the folder holding it."""
    p = user_path(raw)
    return p / "clipper.toml" if p.is_dir() else p


def config_file_ok(raw: str) -> str | None:
    p = config_file(raw)
    if p.is_file():
        return None
    if str(p).startswith("~"):
        return f"can't find the home folder in {p} (a path from your home folder starts with ~/)"
    if not p.is_absolute():
        return f"no file at {p.resolve()} (a path from your home folder starts with ~/)"
    return f"no file at {p}"

class Ask:
    """Questions on the terminal, or answered from an answers file (no prompts) instead."""

    def __init__(self, answers: dict | None):
        self.answers = answers or {}
        self.interactive = answers is None and sys.stdin.isatty()

    def _given(self, key: str):
        return self.answers.get(key) if key in self.answers else None

    def text(self, key: str, prompt: str, default: str = "", check=None) -> str:
        given = self._given(key)
        if given is not None or not self.interactive:
            value = str(given if given is not None else default)
            err = check(value) if check else None
            if err:
                raise ClipperError(f"{key} = {value!r}: {err}")
            return value
        while True:
            raw = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip() or default
            err = check(raw) if check else None
            if not err:
                return raw
            print(f"    {err}")

    def choice(self, key: str, prompt: str, options: dict[str, str], default: str) -> str:
        given = self._given(key)
        if given is not None or not self.interactive:
            value = str(given if given is not None else default)
            if value not in options:
                raise ClipperError(f"{key} = {value!r}: one of {', '.join(options)}")
            return value
        print(f"  {prompt}")
        keys = list(options)
        for i, k in enumerate(keys, 1):
            print(f"    {i}. {options[k]}" + ("   (default)" if k == default else ""))
        while True:
            raw = input("  > ").strip()
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(keys):
                return keys[int(raw) - 1]
            if raw in options:
                return raw
            print(f"    a number from 1 to {len(keys)}")

    def several(self, key: str, prompt: str, options: dict[str, str], default: list[str]) -> list[str]:
        given = self._given(key)
        if given is not None or not self.interactive:
            value = list(given if given is not None else default)
            bad = [v for v in value if v not in options]
            if bad or not value:
                raise ClipperError(f"{key} = {value!r}: some of {', '.join(options)}")
            return value
        print(f"  {prompt}")
        keys = list(options)
        for i, k in enumerate(keys, 1):
            print(f"    {i}. {options[k]}")
        hint = ",".join(str(keys.index(d) + 1) for d in default)
        while True:
            raw = input(f"  numbers, comma separated [{hint}]: ").strip() or hint
            picks = [p.strip() for p in raw.split(",") if p.strip()]
            if picks and all(p.isdigit() and 1 <= int(p) <= len(keys) for p in picks):
                return [keys[int(p) - 1] for p in dict.fromkeys(picks)]
            print(f"    numbers from 1 to {len(keys)}, e.g. 1,2")

    def yes(self, key: str, prompt: str, default: bool = True) -> bool:
        given = self._given(key)
        if given is not None or not self.interactive:
            return bool(given) if given is not None else default
        raw = input(f"  {prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not raw else raw.startswith("y")

    def secret(self, prompt: str) -> str:
        """Typed without echo. Never from the answers file: keys come from the environment."""
        return getpass.getpass(f"  {prompt} (hidden, Enter to skip): ").strip() if self.interactive else ""


def say(text: str = "") -> None:
    print(f"  {text}" if text else "")


# ---------- state ----------

def state_path(cfg: dict) -> Path:
    """Beside this machine's clipper.local.toml: never on the share a joined config lives on."""
    return config.local_path(_root(cfg)["_path"]).parent / STATE


def load_state(cfg: dict) -> dict:
    try:
        st = jsonstate.load(state_path(cfg))
    except (OSError, ValueError):
        st = {}
    st.setdefault("done", {})
    return st


def save_state(cfg: dict, st: dict) -> None:
    state_path(cfg).write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")


def doctor_role(roles: list[str]) -> str:
    """`clipper doctor --role` for these setup roles."""
    mapped = {"worker": "gpu", "serve": "serve", "bot": "bot", "recorder": "nas", "control": "control"}
    picked = {mapped[r] for r in roles if r != "recorder"}  # the recorder isn't installed here
    if len(picked) > 1:
        picked.discard("control")  # the service roles check more than control does
    return picked.pop() if len(picked) == 1 else "all" if picked else "nas"


def control_only(roles: list[str]) -> bool:
    """Nothing runs here unattended: control, with the recorder (on the NAS) at most."""
    return "control" in roles and set(roles) <= {"control", "recorder"}


# ---------- TOML, edited in place (comments kept) ----------

def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)  # a TOML basic string


def _comment(line: str) -> str:
    """The trailing `   # ...` of a `key = value` line, quotes respected."""
    quote, i = None, line.find("=") + 1
    while 0 < i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[len(line[:i].rstrip()):]
        i += 1
    return ""


def toml_set(path: Path, section: str, key: str, value) -> None:
    """Set [section] key = value in a TOML file, keeping its comments and layout."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    new = f"{key} = {toml_value(value)}"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"[{section}]"), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", new]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        hit = next((i for i in range(start + 1, end)
                    if re.match(rf"\s*{re.escape(key)}\s*=", lines[i])), None)
        if hit is None:
            at = end
            while at > start + 1 and not lines[at - 1].strip():
                at -= 1
            lines.insert(at, new)
        else:
            lines[hit] = re.match(r"\s*", lines[hit]).group() + new + _comment(lines[hit])
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)  # never leave a file clipper can't read
    path.write_text(text, encoding="utf-8")


def set_local(cfg: dict, section: str, key: str, value, shared) -> None:
    """This machine's value in clipper.local.toml, only where it differs from clipper.toml's."""
    path = config.local_path(_root(cfg)["_file"])
    if value == shared:
        if path.exists() and key in tomllib.loads(path.read_text(encoding="utf-8")).get(section, {}):
            toml_set(path, section, key, value)  # keep the file honest if it already had one
        return
    if not path.exists():
        path.write_text("# This machine's own settings (clipper setup writes it). They win over\n"
                        "# clipper.toml and its shows; CLIPPER_<SECTION>_<KEY> env vars win over these.\n",
                        encoding="utf-8")
    toml_set(path, section, key, value)
    say(f"-> {path.name}: [{section}] {key} = {toml_value(value)}")


def shared_cfg(cfg: dict) -> dict:
    """The config as clipper.toml and the defaults give it, without this machine's file."""
    return config.load(_root(cfg)["_file"], use_local=False)


# ---------- keys in the user's environment ----------

def user_env(name: str) -> str:
    """The variable from this process, else (Windows) the user's saved environment, which
    shells opened before it was set don't have."""
    if os.environ.get(name):
        return os.environ[name]
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                value = str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return ""
        if value:
            os.environ[name] = value
        return value
    return ""


def load_user_env(cfg: dict) -> None:
    """The keys clipper reads, from the user's saved environment into this process, so the
    checks see what a new terminal (and the scheduled tasks) will."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID", "OPENAI_API_KEY", "GEMINI_API_KEY",
                 "OPENROUTER_API_KEY", "LOCAL_LLM_API_KEY", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "HF_TOKEN",
                 "ELEVENLABS_API_KEY", "BRAVE_API_KEY", cfg["review"]["token_env"], cfg["discord"]["token_env"]):
        user_env(name)


def profile_file() -> Path:
    shell = os.environ.get("SHELL", "")
    return Path.home() / (".zshrc" if sys.platform == "darwin" or shell.endswith("zsh") else ".bashrc")


def set_user_env(name: str, value: str) -> str:
    """Save the variable for this user (new terminals and scheduled tasks get it) and set it
    here. Returns where it went. The value never goes on a command line."""
    os.environ[name] = value
    if sys.platform == "win32":
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        # tell Explorer, so terminals opened from now on see it (HWND_BROADCAST, WM_SETTINGCHANGE)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
        return "a Windows user environment variable"
    path = profile_file()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"export {name}=")]
    lines.append(f"export {name}={shlex.quote(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"{path} (open a new terminal)"


# ---------- this machine ----------

def hardware() -> dict:
    hw = {"nvidia": "", "vram_gb": 0.0,
          "apple": sys.platform == "darwin" and platform.machine() == "arm64"}
    if shutil.which("nvidia-smi"):
        r = doctor.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        first = (r.stdout.strip().splitlines() or [""])[0]
        if r.returncode == 0 and "," in first:
            name, mem = first.rsplit(",", 1)
            hw["nvidia"], hw["vram_gb"] = name.strip(), float(mem) / 1024
    return hw


def backends_here(hw: dict) -> list[str]:
    return [b for b in BACKENDS if b == "modal" or (b == "whisperx" and hw["nvidia"])
            or (b == "mlx" and hw["apple"])]


def extras_for(roles: list[str], backend: str) -> list[str]:
    out = []
    if "worker" in roles:
        out.append({"whisperx": "gpu", "mlx": "mac", "modal": "modal"}[backend])
        out.append("faces")  # the default [worker] stages run `clipper faces` (OpenCV, CPU)
    if "serve" in roles:
        if "faces" not in out:
            out.append("faces")  # the war room's Cast window and the daily `cast auto` check faces (OpenCV)
        extra = {"whisperx": "gpu", "mlx": "mac", "modal": "modal"}[backend]
        if extra not in out:
            out.append(extra)  # pre-shows run on serve's machine, transcribe included (and the Modal panel)
    if "bot" in roles:
        out.append("discord")
    return out


def installed(extra: str) -> bool:
    return importlib.util.find_spec(EXTRA_MODULE[extra]) is not None


def encoders_here(hw: dict) -> list[str]:
    """H.264 encoders to try, fastest first: the GPU's own, then the CPU's."""
    order = (["h264_nvenc"] if hw["nvidia"] else []) + ["h264_qsv"] \
        + (["h264_videotoolbox"] if sys.platform == "darwin" else []) + ["libx264"]
    return order


# ---------- steps ----------

# A new install's clipper.toml: only what it chooses differently from the built-in defaults.
# Copying clipper.example.toml instead pinned ~300 defaults (later default changes never
# reached the install) and brought this install's own show and recorded channel with it.
NEW_CONFIG = """# clipper.toml: this install's settings. Only what differs from the built-in defaults goes
# here; clipper.example.toml lists every setting with its default and what it does, and
# `clipper config get <section>.<key>` shows a value and which file set it.
# `clipper setup` adds what its steps decide.

[bleep]
mode = "bleep"                      # cover slurs in the audio and mask them in captions

[meta]
title_format = "{SHOW} | {title}"   # titles start with the show's name

# One table per show: its Kick channel(s) and what it does differently, for example
# [shows.myshow]
# channels = ["kick-channel-slug"]
# [shows.myshow.meta]
# show_name = "My Show"
"""

def step_mode(cfg: dict, st: dict, ask: Ask) -> None:
    root = _root(cfg)
    have = root["_file"] is not None
    say("join: add this machine to an install that already runs (same NAS, Kitsu, Discord, YouTube).")
    say("new:  start a new install of your own (its own config, services and channel).")
    st["mode"] = ask.choice("mode", "Which is this?", {"join": "join an existing install",
                                                       "new": "start a new install"},
                            st.get("mode") or ("join" if have else "new"))
    target = root["_path"]
    if have:
        say(f"Using {root['_file']}.")
    elif st["mode"] == "new":
        target.write_text(NEW_CONFIG, encoding="utf-8")
        say(f"Wrote a starter {target.name} (only what differs from the defaults; clipper.example.toml "
            "lists every setting). The next steps fill in this machine's part.")
    else:
        say("Copy the main machine's clipper.toml here (it is gitignored, so it isn't in the code).")
        src = config_file(ask.text("from", "Path to the main machine's clipper.toml (a share or a copy; "
                                   "you can drag the file in)", check=config_file_ok)).resolve()
        on_share = not src.is_relative_to(Path.home().resolve())  # /Volumes/..., \\nas\..., a mapped drive
        if ask.yes("in_place", "Use it where it is, so this machine shares that one config? (No copies it "
                   "here; a copy doesn't see later changes, and its relative paths point into this folder)",
                   on_share):
            local = target.parent / config.LOCAL_CONFIG
            say(f"CLIPPER_CONFIG = {src}: saved in {set_user_env('CLIPPER_CONFIG', str(src))}")
            say(f"CLIPPER_LOCAL_CONFIG = {local}: saved in {set_user_env('CLIPPER_LOCAL_CONFIG', str(local))}")
            say("This machine's own settings stay in that local file, not on the share.")
            if target.exists():
                say(f"{target} is no longer read; delete it when you like.")
            return
        shutil.copyfile(src, target)
        say(f"Copied {src} -> {target}")
        secrets = src.parent / "secrets"
        if secrets.is_dir() and ask.yes("copy_secrets", f"Copy the files in {secrets} that aren't here yet?"):
            dest = target.parent / "secrets"
            dest.mkdir(exist_ok=True)
            new = [f for f in secrets.iterdir() if f.is_file() and not (dest / f.name).exists()]
            for f in new:
                shutil.copy2(f, dest / f.name)
            say(f"Copied {len(new)} file(s) into secrets/.")


def step_roles(cfg: dict, st: dict, ask: Ask) -> None:
    hw = hardware()
    say("This machine: " + ", ".join(
        [f"NVIDIA {hw['nvidia']} ({hw['vram_gb']:.0f} GB)" if hw["nvidia"] else "no NVIDIA GPU"]
        + (["Apple Silicon"] if hw["apple"] else []) + [f"{platform.system()} {platform.machine()}"]))
    roles = ask.several("roles", "What will it run?", ROLES, st.get("roles") or ["worker"])
    st["roles"] = roles
    if "recorder" in roles:
        say("The recorder runs in Docker on the NAS, not here: when no show is live, run")
        say("`bash deploy/record/deploy.sh` from a machine with ssh to the NAS: " + wiki.page("NAS-recorder"))
    shared = shared_cfg(cfg)["transcribe"]
    backend = cfg["transcribe"]["backend"]
    if "worker" in roles:
        here = backends_here(hw)
        default = backend if backend in here else here[0]
        backend = ask.choice("transcribe", "Where should it transcribe?",
                             {b: BACKENDS[b] for b in here}, default)
        set_local(cfg, "transcribe", "backend", backend, shared["backend"])
        if backend == "whisperx":
            small = hw["vram_gb"] < 7.5
            set_local(cfg, "transcribe", "compute_type", "int8_float16" if small else shared["compute_type"],
                      shared["compute_type"])
            set_local(cfg, "transcribe", "batch_size", 4 if small else shared["batch_size"], shared["batch_size"])
    st["extras"] = extras_for(roles, backend)
    missing = [e for e in st["extras"] if not installed(e)]
    if missing:
        st["done"]["roles"] = time.time()  # the choices stand; the sync is the bootstrap's part
        save_state(cfg, st)
        args = sync_args(st)
        say()
        say(f"These roles need packages that aren't installed: {', '.join(missing)}.")
        if os.environ.get("CLIPPER_BOOTSTRAP"):
            say("Installing them, then setup carries on.")
        else:
            say(f"Run `uv sync {args}`, then `clipper setup` again to carry on.")
        raise ClipperError(status=EXIT_SYNC)


def step_machine(cfg: dict, st: dict, ask: Ask) -> None:
    shared = shared_cfg(cfg)
    roles = st.get("roles") or ["worker"]

    def streams_ok(v: str) -> str | None:
        if v == "-" and control_only(roles):
            return None
        p = user_path(v)
        if str(p).startswith("~"):
            return f"can't find the home folder in {p} (a path from your home folder starts with ~/)"
        p = p if p.is_absolute() else _root(cfg)["_dir"] / p
        if not p.exists():
            if not p.parent.exists():
                return f"{p} doesn't exist (mount the share first?)"
            p.mkdir()
        try:
            (p / ".setup_write_test").write_text("x")
            (p / ".setup_write_test").unlink()
        except OSError as e:
            return f"can't write to {p}: {e}"
        return None

    if control_only(roles):
        say("A control station only needs the streams folder (the NAS share) for commands on a "
            "stream; - skips it for now.")
    elif st.get("mode") == "join":
        say("Every machine must see the same streams folder (the NAS share, or a mapped drive).")
    else:
        say("Where recordings and everything made from them go (~20-30 GB per stream).")
    current = str(cfg["paths"]["streams"])
    if control_only(roles) and not user_path(current).exists():
        current = "-"
    streams = ask.text("streams", "Streams folder", current, check=streams_ok)
    if streams == "-":
        say("No streams folder here; `clipper setup --step machine` adds one later.")
    else:
        p = user_path(streams)
        p = p if p.is_absolute() else _root(cfg)["_dir"] / p
        set_local(cfg, "paths", "streams", str(p), str(shared["paths"]["streams"]))

    if st.get("mode") == "new":
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        def tz_ok(v: str) -> str | None:
            try:
                ZoneInfo(v)
                return None
            except (ZoneInfoNotFoundError, ValueError):
                return 'an IANA time zone, e.g. "America/Los_Angeles"'
        tz = ask.text("timezone", "Time zone uploads are scheduled in", cfg["schedule"]["timezone"], tz_ok)
        if tz != cfg["schedule"]["timezone"]:
            toml_set(_root(cfg)["_file"], "schedule", "timezone", tz)
            say(f"-> clipper.toml: [schedule] timezone = {toml_value(tz)}")

    if not shutil.which("ffmpeg"):
        raise ClipperError("ffmpeg isn't on PATH: run deploy/bootstrap (or winget install Gyan.FFmpeg / "
                         "brew install ffmpeg), open a new terminal, then clipper setup --step machine")
    if control_only(roles) and cfg["serve"]["hub"] and not (cfg["serve"]["url"] or "").strip():
        say("serve runs on the hub, so the war room is at its tailnet address (" + wiki.page("Phone-access") + ").")

        def serve_ok(v: str) -> str | None:
            return None if not v or re.match(r"https?://", v) else "a URL, e.g. https://clipper.<tailnet>.ts.net/"
        u = ask.text("serve_url", "War room address (Enter skips)", "", serve_ok).strip()
        if u:
            set_local(cfg, "serve", "url", u.rstrip("/") + "/", shared["serve"]["url"])
    if roles == ["recorder"] or control_only(roles):
        return  # nothing encodes here
    hw = hardware()
    codec = cfg["cut"]["clip_codec"]
    if doctor.encoder_works(codec)[0]:
        say(f"Encoder {codec} (from clipper.toml) works here.")
        chosen = codec
    else:
        say(f"Encoder {codec} (from clipper.toml) doesn't work here; trying the others.")
        works = [c for c in encoders_here(hw) if doctor.encoder_works(c)[0]]
        if not works:
            raise ClipperError("no H.264 encoder works in this ffmpeg; install a full build "
                             "(gyan.dev 'full', or BtbN's gpl build) and run clipper setup --step machine")
        chosen = ask.choice("clip_codec", "Encoder for cuts, Shorts and previews",
                            {c: c + (" (CPU, slowest)" if c == "libx264" else "") for c in works}, works[0])
    set_local(cfg, "cut", "clip_codec", chosen, shared["cut"]["clip_codec"])
    for section, key in (("shorts", "codec"), ("shorts", "cut_codec"), ("review", "preview_codec")):
        own = shared[section][key]
        if own and own != "copy" and not doctor.encoder_works(own)[0]:
            set_local(cfg, section, key, "", own)  # empty: falls back to [cut] clip_codec


def check_key(name: str, cfg: dict) -> tuple[bool, str]:
    """Ask the service whether the key works. (True, who) or (False, why)."""
    import requests
    try:
        if name == "ANTHROPIC_API_KEY":
            from .llm import Claude, claude_model
            model = claude_model(cfg) or "claude-haiku-5-5"
            Claude(cfg).client.models.retrieve(model)
            return True, f"Anthropic answers ({model} available)"
        if name in PROVIDER_KEYS:
            from .llm import PROVIDERS
            prov = next(p for p, (_, env) in PROVIDERS.items() if env == name)
            # OpenRouter lists its models to anyone; /key answers only to a good key.
            url = PROVIDERS[prov][0] + ("/key" if prov == "openrouter" else "/models")
            r = requests.get(url, headers={"Authorization": f"Bearer {os.environ[name]}"}, timeout=15)
            return (True, f"{prov} answers") if r.ok else (False, f"{prov} refused it (HTTP {r.status_code})")
        if name == cfg["review"]["token_env"]:
            from . import review
            gazu = review.connect(cfg["review"])
            return True, f"Kitsu signed in as {gazu.client.get_current_user().get('full_name', '?')}"
        if name == "discord":
            from .notify import token
            r = requests.get("https://discord.com/api/v10/users/@me",
                             headers={"Authorization": f"Bot {token(cfg)}"}, timeout=15)
            return (True, f"Discord bot {r.json().get('username', '?')}") if r.ok \
                else (False, f"Discord refused it (HTTP {r.status_code})")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:160]}"
    return True, "set"


# Keys for the other model providers (#72), asked for only when a stage's model uses one:
# env var -> where to get one.
PROVIDER_KEYS = {
    "OPENAI_API_KEY": "platform.openai.com -> API keys",
    "GEMINI_API_KEY": "aistudio.google.com -> Get API key",
    "OPENROUTER_API_KEY": "openrouter.ai -> Keys",
}


def ensure_env_key(cfg: dict, ask: Ask, name: str, what: str, where: str, check: bool = True) -> None:
    if user_env(name):
        good, msg = check_key(name, cfg) if check else (True, "set")
        say(f"{name}: {msg}" if good else f"{name} is set, but: {msg}")
        if good or not ask.yes(f"replace_{name}", f"Enter a new {name}?"):
            return
    else:
        say(f"{name}: {what}. Get one at {where}.")
    while True:
        value = ask.secret(name)
        if not value:
            say(f"skipped {name}; `clipper setup --step keys` asks again")
            return
        os.environ[name] = value
        good, msg = check_key(name, cfg) if check else (True, "set")
        if good:
            say(f"{msg}. Saved as {set_user_env(name, value)}.")
            return
        say(f"that didn't work: {msg}")
        os.environ.pop(name, None)


def step_keys(cfg: dict, st: dict, ask: Ask) -> None:
    roles = st.get("roles") or ["worker"]
    if roles == ["recorder"]:
        say("The recorder needs no keys here.")
        return
    from .doctor import llm_providers
    from .llm import PROVIDERS, claude_model, provider_key

    if control_only(roles):
        say("A control station runs no stages and uses Kitsu in the browser, so these are optional:")
        say("Enter skips each. Add them later (`clipper setup --step keys`) to run a stage here.")
    if claude_model(cfg):
        ensure_env_key(cfg, ask, "ANTHROPIC_API_KEY", "the model calls (score, segments, titles, covers)",
                       "console.anthropic.com -> API keys")
    else:
        say("ANTHROPIC_API_KEY: not needed, no stage uses Claude")

    for prov, uses in llm_providers(cfg).items():
        env = PROVIDERS[prov][1]
        if env not in PROVIDER_KEYS:
            continue
        if not user_env(env) and provider_key(prov, cfg["paths"]):
            say(f"{env}: key in {cfg['paths'][f'{prov}_key']}")
        else:
            ensure_env_key(cfg, ask, env, f"{prov} models ({uses[0]})", PROVIDER_KEYS[env])
    rc = cfg["review"]
    if not rc["host"] or "<" in rc["host"]:
        def url_ok(v: str) -> str | None:
            return None if not v or re.match(r"https?://", v) else "a URL, e.g. http://192.168.0.155:8090"
        host = ask.text("kitsu", "Kitsu address (review happens there)", "", url_ok).rstrip("/")
        if host:
            host = host if host.endswith("/api") else host + "/api"
            toml_set(_root(cfg)["_file"], "review", "host", host)
            say(f"-> clipper.toml: [review] host = {toml_value(host)}")
            cfg = config.load(_root(cfg)["_file"])
    have_host = cfg["review"]["host"] and "<" not in cfg["review"]["host"]
    ensure_env_key(cfg, ask, cfg["review"]["token_env"], "a Kitsu bot token",
                   "Kitsu -> Bots -> New bot (Supervisor) -> copy its token", check=bool(have_host))
    if cfg["transcribe"]["backend"] == "modal" and not control_only(roles):
        if (Path.home() / ".modal.toml").exists() and not user_env("MODAL_TOKEN_ID"):
            say("Modal: token in ~/.modal.toml")
        else:
            for name in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
                ensure_env_key(cfg, ask, name, "transcription on Modal",
                               "modal.com -> Settings -> API Tokens -> New token", check=False)
            say("The Modal app is deployed with `clipper transcribe --deploy` (several minutes, once).")
    if "bot" in roles or (cfg["discord"]["enabled"] and not control_only(roles)):  # the worker posts cards too
        path: Path = cfg["paths"]["discord_token"]
        good, msg = check_key("discord", cfg) if (user_env(cfg["discord"]["token_env"]) or path.exists()) \
            else (False, "")
        if good:
            say(msg)
        else:
            say(msg or "Discord: the bot's token. Developer Portal -> your app -> Bot -> Reset Token.")
            value = ask.secret("Discord bot token")
            if value:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value + "\n", encoding="utf-8")
                good, msg = check_key("discord", cfg)
                say(f"{msg}. Saved in {path}." if good else f"saved in {path}, but: {msg}")


def step_check(cfg: dict, st: dict, ask: Ask, offline: bool = False) -> int:
    role = doctor_role(st.get("roles") or ["worker"])
    say(f"clipper doctor --role {role}{' --offline' if offline else ''}")
    say()
    code = doctor.main(cfg, offline=offline, role=role)
    say()
    if code:
        say("Fix the FAIL lines (each says how), then `clipper setup --check` or the step again.")
    else:
        say("Ready. Next:")
        roles = st.get("roles") or []
        win = sys.platform == "win32"
        if "worker" in roles:
            say("  worker, unattended: " + (r"deploy\worker\worker-task.ps1 -Install" if win
                                            else "clipper worker (in a terminal or launchd for now)"))
        if "serve" in roles:
            say("  serve, unattended:  " + (r"deploy\serve\serve-task.ps1 -Install" if win
                                            else "clipper serve (in a terminal or launchd for now)"))
        if "bot" in roles:
            say("  bot, unattended:    " + (r"deploy\discord\bot-task.ps1 -Install" if win
                                            else "clipper bot (in a terminal or launchd for now)"))
        if control_only(roles):
            say("  control: clipper services status (the hub's containers over ssh), "
                "bash deploy/hub/deploy.sh to ship main, the war room in a browser")
        say("  What setup doesn't do yet (Discord ids, YouTube sign-in, adding a show): " + wiki.page("Install"))
    return code


# ---------- command ----------

def sync_args(st: dict) -> str:
    return " ".join(f"--extra {e}" for e in st.get("extras", []))


def checks_for(cfg: dict, st: dict, step: str) -> list:
    roles = st.get("roles") or ["worker"]
    role = doctor_role(roles)
    root = _root(cfg)
    if step == "mode":
        return [("OK", "config", str(root["_file"]), "") if root["_file"] else
                ("FAIL", "config", f"no {root['_path'].name}", "clipper setup --step mode")]
    if step == "roles":
        return [("OK" if installed(e) else "FAIL", "packages", f"extra {e}"
                 + (" installed" if installed(e) else " not installed"),
                 "" if installed(e) else f"uv sync {sync_args(st)}") for e in st.get("extras", [])]
    if step == "machine":
        return doctor.collect(doctor.check_config, cfg, role) + doctor.collect(doctor.check_ffmpeg, cfg, role)
    if step == "keys":  # check_gpu: CUDA, or the Modal token
        return (doctor.collect(doctor.check_gpu, cfg) if "worker" in roles else []) \
            + doctor.collect(doctor.check_keys, cfg, role)
    return []


def main(cfg_path: str | None, step: str | None = None, answers: str | None = None,
         check_only: bool = False, print_sync: bool = False, offline: bool = False) -> int:
    cfg = config.load(cfg_path)
    st = load_state(cfg)
    load_user_env(cfg)
    if print_sync:
        print(sync_args(st))
        return 0
    if check_only:
        fails = 0
        for s in STEPS[:-1]:
            print(f"\n== {s}: {TITLES[s]}" + ("" if s in st["done"] else "  (not done yet)"))
            n = doctor.report(checks_for(cfg, st, s))
            fails += n["FAIL"]
        return 1 if fails else 0
    ask = Ask(tomllib.loads(Path(answers).read_text(encoding="utf-8")) if answers else None)
    todo = [step] if step else [s for s in STEPS if s not in st["done"] or s == "check"]
    if not step and len(todo) == 1:
        print("Setup is done on this machine; checking it. (`clipper setup --step <name>` redoes one: "
              + ", ".join(STEPS) + ")")
    if not ask.interactive and not answers:
        print("(not a terminal: using defaults and the environment, no questions)")
    for s in todo:
        print(f"\n== {STEPS.index(s) + 1}/{len(STEPS)}  {TITLES[s]}")
        cfg = config.load(cfg_path)
        if s == "check":
            return step_check(cfg, st, ask, offline)
        {"mode": step_mode, "roles": step_roles, "machine": step_machine, "keys": step_keys}[s](cfg, st, ask)
        cfg = config.load(cfg_path)
        bad = [r for r in checks_for(cfg, st, s) if r[0] == "FAIL"]
        if bad:
            doctor.report(bad)
            st["done"].pop(s, None)
            save_state(cfg, st)
            print(f"\nStep {s} isn't done: fix the above, then `clipper setup` carries on from here.")
            return 1
        st["done"][s] = time.time()
        save_state(cfg, st)
    return 0
