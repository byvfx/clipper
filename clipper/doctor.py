"""`clipper doctor`: check that this machine can run clipper, and say what to fix.

Usage:
    clipper doctor [--offline] [--role all|gpu|bot|nas]

Checks Python, the config and prompt files, ffmpeg (filters and the configured encoders),
fonts, the GPU stack (torch + WhisperX, only needed where transcribe runs), API keys and
tokens, and, unless --offline, that Anthropic, Kitsu, Discord and Kick answer. Each line is
OK, WARN (works, but something is missing or slow) or FAIL (a stage will break); the exit
code is 1 when anything fails. --role skips what that machine doesn't run: gpu (a worker:
everything but the bot), bot (the Discord bot + review), nas (the recorder only).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import _root

RESULTS: list[tuple[str, str, str, str]] = []  # (status, area, what, hint)


def ok(area: str, what: str, hint: str = "") -> None:
    RESULTS.append(("OK", area, what, ""))


def warn(area: str, what: str, hint: str = "") -> None:
    RESULTS.append(("WARN", area, what, hint))


def fail(area: str, what: str, hint: str = "") -> None:
    RESULTS.append(("FAIL", area, what, hint))


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False)


# ---------- checks ----------

def check_python() -> None:
    v = sys.version_info
    if (v.major, v.minor) == (3, 12):
        ok("python", f"{platform.python_version()} ({sys.executable})")
    else:
        fail("python", f"{platform.python_version()}: clipper needs 3.12",
             "uv python install 3.12, then uv sync")


def check_config(cfg: dict) -> None:
    root = _root(cfg)
    if root.get("_file"):
        ok("config", f"{root['_file']}")
    else:
        fail("config", "no clipper.toml found", "copy clipper.example.toml to clipper.toml, or pass --config")
    shows = root["shows"]
    if shows:
        ok("config", f"{len(shows)} shows: " + ", ".join(
            f"{k} ({', '.join(s['channels'])})" for k, s in shows.items()))
    else:
        warn("config", "no [shows.<key>] tables; every stream uses the base settings",
             "add a [shows.<key>] table per show (see clipper.example.toml)")
    streams: Path = cfg["paths"]["streams"]
    if not streams.exists():
        fail("paths", f"streams folder {streams} doesn't exist",
             "create it, or point [paths] streams at the NAS share")
    else:
        probe = streams / ".doctor_write_test"
        try:
            probe.write_text("x")
            probe.unlink()
            free = shutil.disk_usage(streams).free / 1e9
            n = sum(1 for _ in streams.glob("*/meta.json"))
            (ok if free > 100 else warn)(
                "paths", f"streams {streams}: writable, {n} streams, {free:.0f} GB free",
                "" if free > 100 else "a stream needs ~20-30 GB for source + cuts")
        except OSError as e:
            fail("paths", f"can't write to {streams}: {e}", "check the share login / permissions")


def prompt_files(cfg: dict) -> list[str]:
    sc, v = cfg["scoring"], cfg["scoring"]["prompt_version"]
    names = [f"score_a_{v}", f"score_b_{v}", f"summary_{v}", f"scan_{v}",
             f"third_party_{sc['third_party_audio']}_{v}", f"audience_{sc['audience_knowledge']}_{v}",
             f"segments_{cfg['segments']['prompt_version']}",
             f"compilation_{cfg['segments']['prompt_version']}",
             f"meta_{cfg['meta']['prompt_version']}", f"chapters_{cfg['meta']['prompt_version']}",
             f"retake_{cfg['retake']['prompt_version']}",
             f"preshow_{cfg['preshow']['prompt_version']}"]
    tc = cfg["thumbnail"]
    names.append(f"thumbnail_enquirer_{tc['enquirer_prompt_version']}" if tc["style"] == "enquirer"
                 else f"thumbnail_{tc['prompt_version']}")
    return names


def check_prompts(cfg: dict) -> None:
    d: Path = cfg["paths"]["prompts"]
    missing = [n for n in prompt_files(cfg) if not (d / f"{n}.md").exists()]
    # v3/v4 rule files are optional per version; only the core ones must exist.
    missing = [n for n in missing if not n.startswith(("third_party_", "audience_", "scan_"))
               or int(cfg["scoring"]["prompt_version"].lstrip("v") or 0) >= 4]
    if missing:
        fail("prompts", f"missing in {d}: " + ", ".join(f"{m}.md" for m in missing),
             "copy the prompts/ folder from the main machine")
    else:
        ok("prompts", f"{len(prompt_files(cfg))} prompt files for the configured versions")


def check_ffmpeg(cfg: dict, role: str) -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            fail("ffmpeg", f"{tool} not on PATH",
                 "Windows: winget install Gyan.FFmpeg; macOS: brew install ffmpeg; Linux: apt install ffmpeg")
            return
    ver = run(["ffmpeg", "-hide_banner", "-version"]).stdout.splitlines()[0]
    ok("ffmpeg", ver[:80])
    filters = run(["ffmpeg", "-hide_banner", "-filters"]).stdout
    need = {"loudnorm": "Shorts loudness", "asendcmd": "bleeps", "subtitles": "Shorts captions (libass)"}
    for f, why in need.items():
        if f" {f} " in filters:
            ok("ffmpeg", f"filter {f} ({why})")
        else:
            fail("ffmpeg", f"no {f} filter: {why} won't work", "install a full ffmpeg build (gyan.dev 'full')")
    if role == "nas":
        return
    codecs = {cfg["cut"]["clip_codec"], cfg["shorts"]["codec"] or cfg["cut"]["clip_codec"],
              cfg["review"]["preview_codec"] or cfg["cut"]["clip_codec"]}
    for codec in sorted(c for c in codecs if c and c != "copy"):
        vf = ["-vf", "format=nv12"] if codec == "h264_qsv" else []
        r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=320x240:d=0.2", *vf, "-c:v", codec, "-f", "null", "-"])
        if r.returncode == 0:
            ok("ffmpeg", f"encoder {codec} works")
        else:
            hint = {"h264_qsv": "needs an Intel GPU; set [cut] clip_codec = \"libx264\" (or h264_nvenc)",
                    "h264_nvenc": "needs an NVIDIA driver new enough for this ffmpeg (610+ for 8.1)",
                    }.get(codec, "pick another [cut] clip_codec")
            fail("ffmpeg", f"encoder {codec} failed: {r.stderr.strip()[:120]}", hint)


def font_dirs() -> list[Path]:
    if sys.platform == "win32":
        return [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    if sys.platform == "darwin":
        return [Path("/Library/Fonts"), Path("/System/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]


def check_fonts(cfg: dict) -> None:
    tc = cfg["thumbnail"]
    found = next((f for f in ([tc["font"]] if tc["font"] else []) + tc["fonts"] if Path(f).exists()), None)
    if found:
        ok("fonts", f"thumbnail font {found}")
    else:
        fail("fonts", "none of [thumbnail] fonts exist", "install Impact (ttf-mscorefonts) or set [thumbnail] font")
    if tc["style"] == "enquirer":
        cond = next((f for f in tc["condensed_fonts"] if Path(f).exists()), None)
        (ok if cond else warn)("fonts", f"condensed font {cond}" if cond else "no condensed font for enquirer covers",
                               "" if cond else "install Bahnschrift / Roboto Condensed or set [thumbnail] condensed_fonts")
    sc = cfg["shorts"]
    name = sc["caption_font"].lower().replace(" ", "")
    dirs = [Path(sc["caption_fontsdir"])] if sc["caption_fontsdir"] else font_dirs()
    hit = next((p for d in dirs if d.exists() for p in d.rglob("*")
                if p.suffix.lower() in (".ttf", ".otf") and name in p.stem.lower().replace(" ", "")), None)
    if hit:
        ok("fonts", f"caption font {sc['caption_font']!r}: {hit}")
    else:
        warn("fonts", f"caption font {sc['caption_font']!r} not found; libass falls back to another font",
             "install it, or set [shorts] caption_fontsdir to a folder with the .ttf")


def check_gpu(cfg: dict) -> None:
    tc = cfg["transcribe"]
    if tc["backend"] == "mlx":
        try:
            import mlx_whisper  # noqa: F401
            ok("gpu", f"mlx-whisper installed ({tc['mlx_model']})")
        except ImportError:
            fail("gpu", "[transcribe] backend = mlx but mlx-whisper isn't installed",
                 "on an Apple Silicon Mac: uv sync --extra mac")
        return
    try:
        import torch
    except ImportError:
        warn("gpu", "torch not installed: this machine can't transcribe",
             "only the GPU worker needs it: uv sync --extra gpu")
        return
    if tc["device"] == "cuda":
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            ok("gpu", f"CUDA {torch.version.cuda}: {p.name}, {p.total_memory / 2**30:.0f} GB "
                      f"(torch {torch.__version__})")
            if p.total_memory / 2**30 < 7:
                warn("gpu", "under 8 GB VRAM", "set [transcribe] compute_type = \"int8_float16\", batch_size = 4")
        else:
            fail("gpu", f"[transcribe] device = cuda but torch {torch.__version__} sees no CUDA GPU",
                 "CPU-only torch: reinstall torch/torchaudio/torchvision from the cu128 index (uv sync --extra gpu), "
                 "or update the NVIDIA driver")
    else:
        ok("gpu", f"transcribe device {tc['device']} (torch {torch.__version__})")
    try:
        import whisperx  # noqa: F401
        ok("gpu", "whisperx importable")
    except Exception as e:  # noqa: BLE001
        fail("gpu", f"whisperx import failed: {e}", "uv sync --extra gpu")


def check_keys(cfg: dict, role: str) -> None:
    if role != "nas":
        if os.environ.get("ANTHROPIC_API_KEY"):
            ok("keys", "ANTHROPIC_API_KEY set")
        else:
            fail("keys", "ANTHROPIC_API_KEY not set", "set it as a user environment variable")
        rc = cfg["review"]
        if rc["host"] and "<" not in rc["host"]:
            ok("keys", f"[review] host = {rc['host']}")
        else:
            fail("keys", "[review] host not set", "set it to your Kitsu URL + /api in clipper.toml")
        (ok if os.environ.get(rc["token_env"]) else fail)(
            "keys", f"${rc['token_env']} (Kitsu bot token) " + ("set" if os.environ.get(rc["token_env"]) else "not set"),
            "" if os.environ.get(rc["token_env"]) else "Kitsu > Bots > new token; set it as a user env var")
    if cfg["discord"]["enabled"] or role == "bot":
        from .notify import token
        try:
            token(cfg)
            ok("keys", "Discord bot token found")
        except SystemExit as e:
            fail("keys", f"Discord: {e}", f"put the bot token in {cfg['paths']['discord_token']}")
        try:
            import discord  # noqa: F401
            ok("keys", "discord.py installed")
        except ImportError:
            (fail if role == "bot" else warn)("keys", "discord.py not installed (needed for `clipper bot`)",
                                              "uv sync --extra discord")
    if role in ("all", "bot"):
        p = cfg["paths"]
        if p["youtube_token"].exists():
            ok("keys", f"YouTube sign-in token {p['youtube_token'].name}")
        elif p["youtube_client_secret"].exists():
            warn("keys", "YouTube client secret present but not signed in", "clipper upload --sign-in")
        else:
            warn("keys", "no YouTube client secret: `clipper upload` won't work here",
                 f"copy {p['youtube_client_secret'].name} and {p['youtube_token'].name} into secrets/")


def check_online(cfg: dict, role: str) -> None:
    import requests

    if role != "nas" and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from .scoring import Claude
            Claude(cfg["scoring"]["workspace_id"]).client.models.retrieve(cfg["scoring"]["model_a"])
            ok("online", f"Anthropic API answers ({cfg['scoring']['model_a']} available)")
        except Exception as e:  # noqa: BLE001
            fail("online", f"Anthropic API: {type(e).__name__}: {str(e)[:120]}",
                 "check the key, or set [scoring] workspace_id / $ANTHROPIC_WORKSPACE_ID")
    if role != "nas" and os.environ.get(cfg["review"]["token_env"]) and cfg["review"]["host"]             and "<" not in cfg["review"]["host"]:
        try:
            from . import review
            from .config import for_show
            gazu = review.connect(cfg["review"])
            ok("online", f"Kitsu answers at {cfg['review']['host']}")
            for key in _root(cfg)["shows"] or [""]:
                name = review.project_name(for_show(cfg, key))
                if gazu.project.get_project_by_name(name):
                    ok("online", f"Kitsu production {name!r}")
                else:
                    warn("online", f"Kitsu production {name!r} doesn't exist yet",
                         "created by the first `clipper review publish` of that show")
        except (Exception, SystemExit) as e:  # noqa: BLE001
            fail("online", f"Kitsu: {e}", "is the NAS up, and this machine on its network?")
    if cfg["discord"]["enabled"] or role == "bot":
        try:
            from .notify import token
            r = requests.get("https://discord.com/api/v10/users/@me",
                             headers={"Authorization": f"Bot {token(cfg)}"}, timeout=15)
            (ok if r.ok else fail)("online", f"Discord bot {r.json().get('username', '?')}" if r.ok
                                   else f"Discord refused the token ({r.status_code})",
                                   "" if r.ok else "reset the bot token in the Developer Portal")
        except (Exception, SystemExit) as e:  # noqa: BLE001
            fail("online", f"Discord: {e}")
    if role in ("all", "gpu"):  # the worker grabs pre-shows
        from . import preshow
        for key, sc in preshow.watched(_root(cfg)):
            ch = sc["preshow"]["youtube_channel"]
            try:
                hits = [e for e in preshow.feed(ch, sc["kick"]["user_agent"])
                        if re.search(sc["preshow"]["title_pattern"], e["title"])]
                ok("online", f"pre-show feed for {key} ({ch}): {len(hits)} recent pre-shows")
            except Exception as e:  # noqa: BLE001
                fail("online", f"pre-show feed for {key} ({ch}): {e}",
                     "check [shows.<key>.preshow] youtube_channel (a UC... channel id)")
    chans = [c for s in _root(cfg)["shows"].values() for c in s["channels"]] or [cfg["kick"]["channel"]]
    chans = [c for c in chans if c]
    if chans:
        try:
            r = requests.get(f"https://kick.com/api/v2/channels/{chans[0]}",
                             headers={"User-Agent": cfg["kick"]["user_agent"]}, timeout=15)
            (ok if r.ok else warn)("online", f"Kick API ({chans[0]}): HTTP {r.status_code}",
                                   "" if r.ok else "Kick may be blocking this IP or user agent")
        except Exception as e:  # noqa: BLE001
            warn("online", f"Kick API: {e}")


# ---------- command ----------

def check_photos(cfg: dict) -> None:
    tc = cfg["thumbnail"]
    if not tc["photos"]:
        return
    d = Path(tc["photos"])
    if not d.is_dir():
        fail("photos", f"[thumbnail] photos folder {d} not found",
             "create it (or mount the share), or set [thumbnail] photos = \"\"")
        return
    from .thumbs import photos
    names = list(photos(tc))
    ok("photos", f"{len(names)} in {d}: {', '.join(names)}" if names else f"none yet in {d}")


def main(cfg: dict, offline: bool = False, role: str = "all") -> int:
    RESULTS.clear()
    check_python()
    check_config(cfg)
    if role != "nas":
        check_prompts(cfg)
    check_ffmpeg(cfg, role)
    if role in ("all", "gpu"):
        check_fonts(cfg)
        check_photos(cfg)
        check_gpu(cfg)
    check_keys(cfg, role)
    if not offline:
        check_online(cfg, role)
    width = max(len(a) for _, a, _, _ in RESULTS)
    for status, area, what, hint in RESULTS:
        print(f"{status:<4}  {area:<{width}}  {what}")
        if hint and status != "OK":
            print(f"{'':<4}  {'':<{width}}  -> {hint}")
    n = {s: sum(r[0] == s for r in RESULTS) for s in ("OK", "WARN", "FAIL")}
    print(f"\n{n['OK']} ok, {n['WARN']} warnings, {n['FAIL']} failures")
    return 1 if n["FAIL"] else 0
