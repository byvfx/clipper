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

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import jsonstate
from .config import _root, for_show
from .errors import ClipperError

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


def collect(check, *args, **kwargs) -> list[tuple[str, str, str, str]]:
    """Run one check and return its result lines, leaving RESULTS as it was (`clipper setup`)."""
    start = len(RESULTS)
    check(*args, **kwargs)
    out = RESULTS[start:]
    del RESULTS[start:]
    return out


def report(results: list[tuple[str, str, str, str]]) -> dict[str, int]:
    """Print result lines with their hints; the count of each status."""
    width = max((len(a) for _, a, _, _ in results), default=0)
    for status, area, what, hint in results:
        print(f"{status:<4}  {area:<{width}}  {what}")
        if hint and status != "OK":
            print(f"{'':<4}  {'':<{width}}  -> {hint}")
    return {s: sum(r[0] == s for r in results) for s in ("OK", "WARN", "FAIL")}


def encoder_works(codec: str) -> tuple[bool, str]:
    """Test-encode a fraction of a second of black with this ffmpeg encoder."""
    vf = ["-vf", "format=nv12"] if codec == "h264_qsv" else []
    r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=black:s=320x240:d=0.2", *vf, "-c:v", codec, "-f", "null", "-"])
    return r.returncode == 0, r.stderr.strip()


# ---------- checks ----------

def check_python() -> None:
    v = sys.version_info
    if (v.major, v.minor) == (3, 12):
        ok("python", f"{platform.python_version()} ({sys.executable})")
    else:
        fail("python", f"{platform.python_version()}: clipper needs 3.12",
             "uv python install 3.12, then uv sync")
    if sys.platform == "win32" and len(sys.prefix) > 86:  # the folder + \.venv over ~80 characters
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
                long_paths = winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
        except OSError:
            long_paths = False
        if not long_paths:
            warn("python", f"the venv's path is {len(sys.prefix)} characters and Windows long paths are off",
                 "some packages (anthropic) fail to load past 260 characters: move clipper to a short "
                 "folder such as C:\\clipper, or turn long paths on")


def check_config(cfg: dict) -> None:
    root = _root(cfg)
    if root.get("_file"):
        ok("config", f"{root['_file']}")
    else:
        fail("config", "no clipper.toml found", "clipper setup (or copy clipper.example.toml to clipper.toml)")
    from .config import problems
    for msg in problems(cfg):
        fail("config", msg, "fix the name (clipper.example.toml lists every setting) or remove it")
    if root.get("_local_file"):
        ok("config", f"this machine: {root['_local_file'].name} sets " + ", ".join(
            f"{s}.{k}" for s, v in root["_local"].items() for k in v))
    shows = root["shows"]
    if shows:
        ok("config", f"{len(shows)} shows: " + ", ".join(
            f"{k} ({', '.join(s['channels'])})" for k, s in shows.items()))
    else:
        warn("config", "no [shows.<key>] tables; every stream uses the base settings",
             "add a [shows.<key>] table per show (see clipper.example.toml)")
    from .config import for_show
    from .shorts import layouts_of, pickable, with_examples
    for key in shows:  # Shorts scene detection reads its layouts off reference screenshots
        sc = for_show(cfg, key)  # (pick = false layouts are chosen by hand: no screenshot)
        lays = {n: lay for n, lay in with_examples(pickable(layouts_of(sc["shorts"])), sc).items()
                if n != "center"}
        missing = [n for n, lay in lays.items() if not lay.get("example")]
        if len(lays) > 1 and missing:
            warn("shorts", f"{key}: no reference screenshot for layout(s) {', '.join(missing)}; "
                 "Short scene detection is less reliable without them",
                 f"clipper shorts <a {key} stream> --save-example <layout> --at <seconds>")
        elif len(lays) > 1:
            ok("shorts", f"{key}: reference screenshots for {', '.join(lays)}")
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
        works, err = encoder_works(codec)
        if works:
            ok("ffmpeg", f"encoder {codec} works")
        else:
            hint = {"h264_qsv": "needs an Intel GPU; clipper setup --step machine picks one that works here",
                    "h264_nvenc": "needs an NVIDIA driver new enough for this ffmpeg (610+ for 8.1)",
                    }.get(codec, "pick another [cut] clip_codec (clipper setup --step machine)")
            fail("ffmpeg", f"encoder {codec} failed: {err[:120]}", hint)


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
    wc = cfg["weekly"]
    missing = [k for k in ("headline", "condensed", "serif_italic", "serif_bold_italic", "black", "bold")
               if not any(Path(f).exists() for f in wc[f"{k}_fonts"])]
    if missing:
        warn("fonts", f"weekly issue fonts missing: {', '.join(missing)}",
             "install Impact, Bahnschrift (or Roboto Condensed), Georgia and Arial, or set [weekly] *_fonts")
    else:
        ok("fonts", "weekly issue fonts (Impact, condensed, Georgia italic, Arial)")
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


def diarizing(cfg: dict) -> list[str]:
    """Shows (or "base") with [transcribe] diarize on."""
    return [k or "base" for k in ["", *_root(cfg)["shows"]] if for_show(cfg, k)["transcribe"]["diarize"]]


def check_gpu(cfg: dict) -> None:
    tc = cfg["transcribe"]
    if diarizing(cfg) and tc["backend"] == "whisperx":
        if os.environ.get("HF_TOKEN"):
            ok("gpu", "HF_TOKEN set for diarization")
        else:
            fail("gpu", f"[transcribe] diarize is on ({', '.join(diarizing(cfg))}) but HF_TOKEN isn't set",
                 "see SETUP.md (Speaker labels): a Hugging Face read token with the pyannote terms accepted")
    if tc["backend"] == "mlx":
        try:
            import mlx_whisper  # noqa: F401
            ok("gpu", f"mlx-whisper installed ({tc['mlx_model']})")
        except ImportError:
            fail("gpu", "[transcribe] backend = mlx but mlx-whisper isn't installed",
                 "on an Apple Silicon Mac: uv sync --extra mac")
        return
    if tc["backend"] == "modal":
        try:
            import modal
            ok("gpu", f"modal {modal.__version__} installed (transcribe on a cloud {tc['modal_gpu'] or 'GPU'})")
        except ImportError:
            fail("gpu", "[transcribe] backend = modal but modal isn't installed", "uv sync --extra modal")
            return
        if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
            ok("gpu", "Modal token in MODAL_TOKEN_ID / MODAL_TOKEN_SECRET")
        elif (Path.home() / ".modal.toml").exists():
            ok("gpu", "Modal token in ~/.modal.toml")
        else:
            fail("gpu", "no Modal token",
                 "modal.com -> Settings -> API Tokens, then clipper setup --step keys "
                 "(it saves MODAL_TOKEN_ID and MODAL_TOKEN_SECRET)")
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


def llm_providers(cfg: dict) -> dict[str, list[str]]:
    """Non-Claude providers the config's models use: {provider: ["section.key = model", ...]}."""
    from . import llm

    out: dict[str, list[str]] = {}
    for where, model in llm.configured(cfg).items():
        prov = llm.split(model)[0]
        if prov != "anthropic":
            out.setdefault(prov, []).append(f"{where} = {model}")
    return out


def check_llm_keys(cfg: dict) -> None:
    from . import llm

    for prov, uses in llm_providers(cfg).items():
        env = llm.PROVIDERS[prov][1]
        what = f"{prov} models ({'; '.join(uses[:3])}{'...' if len(uses) > 3 else ''})"
        if prov == "local":
            if cfg["llm"]["local_base_url"]:
                ok("keys", f"{what}: [llm] local_base_url = {cfg['llm']['local_base_url']}")
            else:
                fail("keys", f"{what}: [llm] local_base_url not set",
                     "set it in clipper.toml to the server's OpenAI-compatible /v1 URL")
        elif os.environ.get(env):
            ok("keys", f"{what}: {env} set")
        else:
            fail("keys", f"{what}: {env} not set", "set it as a user environment variable")


def check_llm_online(cfg: dict) -> None:
    """Each non-Claude provider in use lists its models, and has the configured ones."""
    import requests

    from . import llm

    for prov, uses in llm_providers(cfg).items():
        base, env = llm.PROVIDERS[prov]
        base = (cfg["llm"]["local_base_url"] if prov == "local" else base).rstrip("/")
        key = os.environ.get(env, "")
        if not base or (prov != "local" and not key):
            continue  # already a failure under keys
        try:
            r = requests.get(f"{base}/models", timeout=15,
                             headers={"Authorization": f"Bearer {key}"} if key else {})
        except requests.RequestException as e:
            fail("online", f"{prov}: {base} unreachable ({type(e).__name__})")
            continue
        if not r.ok:
            fail("online", f"{prov}: HTTP {r.status_code} from {base}/models", f"check {env}")
            continue
        have = {m.get("id", "").removeprefix("models/") for m in r.json().get("data", [])}
        for use in uses:
            name = llm.split(use.split(" = ", 1)[1])[1]
            (ok if name in have else warn)("online", f"{prov} model {name}"
                                           + ("" if name in have else " not listed by the server"))


def check_keys(cfg: dict, role: str) -> None:
    if role != "nas":
        if os.environ.get("ANTHROPIC_API_KEY"):
            ok("keys", "ANTHROPIC_API_KEY set")
        else:
            fail("keys", "ANTHROPIC_API_KEY not set", "clipper setup --step keys (or set it as a user env var)")
        check_llm_keys(cfg)
        rc = cfg["review"]
        if rc["host"] and "<" not in rc["host"]:
            ok("keys", f"[review] host = {rc['host']}")
        else:
            fail("keys", "[review] host not set", "clipper setup --step keys, or set it to your Kitsu URL + /api in clipper.toml")
        (ok if os.environ.get(rc["token_env"]) else fail)(
            "keys", f"${rc['token_env']} (Kitsu bot token) " + ("set" if os.environ.get(rc["token_env"]) else "not set"),
            "" if os.environ.get(rc["token_env"]) else "Kitsu > Bots > new token; clipper setup --step keys")
    if cfg["discord"]["enabled"] or role == "bot":
        from .notify import token
        try:
            token(cfg)
            ok("keys", "Discord bot token found")
        except ClipperError as e:
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
        check_tiktok(cfg)


def check_tiktok(cfg: dict) -> None:
    """Optional: only `clipper tiktok` needs it."""
    p = cfg["paths"]
    if not p["tiktok_client"].exists():
        warn("keys", "no TikTok client key: `clipper tiktok` won't work here (optional)",
             f"see SETUP.md (TikTok); save the app's key and secret in {p['tiktok_client']}")
        return
    try:
        c = json.loads(p["tiktok_client"].read_text(encoding="utf-8-sig"))
        assert c.get("client_key") and c.get("client_secret")
    except (ValueError, AssertionError, AttributeError):
        fail("keys", f"{p['tiktok_client'].name} is not JSON with client_key and client_secret",
             '{"client_key": "...", "client_secret": "..."}')
        return
    if not p["tiktok_token"].exists():
        warn("keys", "TikTok client key present but not signed in", "clipper tiktok --sign-in")
        return
    tok = jsonstate.load(p["tiktok_token"])
    days = (tok.get("refresh_expires_at", 0) - time.time()) / 86400
    if days <= 0:
        fail("keys", "TikTok sign-in expired", "clipper tiktok --sign-in")
    elif "video.upload" not in tok.get("scope", ""):
        fail("keys", f"TikTok token lacks video.upload (has {tok.get('scope')!r})",
             "add the scope to the app, then clipper tiktok --sign-in")
    else:
        (warn if days < 14 else ok)("keys", f"TikTok sign-in token {p['tiktok_token'].name} "
                                    f"(renew within {days:.0f} days)", "clipper tiktok --sign-in")


def check_narrate(cfg: dict) -> None:
    """Optional: only `clipper narrate` needs it."""
    from .narrate import find_key, settings

    nc = settings(cfg)
    where = f"${nc['key_env']} or {nc['key_file']}"
    if not find_key(nc):
        if nc["voice_id"]:
            fail("keys", f"[narrate] voice_id is set but there's no ElevenLabs key ({where})",
                 "SETUP.md, Narration")
        else:
            warn("keys", f"no ElevenLabs key ({where}): `clipper narrate` won't work here (optional)",
                 "SETUP.md, Narration")
        return
    if not nc["voice_id"]:
        warn("keys", "ElevenLabs key found but no [narrate] voice_id", "clipper narrate voices")
    else:
        ok("keys", f"ElevenLabs key found, voice {nc['voice_id']}")


def check_online(cfg: dict, role: str) -> None:
    import requests

    from .narrate import find_key, settings

    nc = settings(cfg)
    if el_key := find_key(nc):
        r = requests.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": el_key}, timeout=15)
        if not r.ok:
            fail("online", f"ElevenLabs refused the key (HTTP {r.status_code})",
                 f"check ${nc['key_env']} / {nc['key_file']}")
        else:
            tier = (r.json().get("subscription") or {}).get("tier", "?")
            ok("online", f"ElevenLabs answers (plan {tier})")
            if nc["voice_id"]:
                v = requests.get(f"https://api.elevenlabs.io/v1/voices/{nc['voice_id']}",
                                 headers={"xi-api-key": el_key}, timeout=15)
                (ok if v.ok else fail)("online", f"ElevenLabs voice {nc['voice_id']}: "
                                       + (v.json().get("name", "?") if v.ok else f"HTTP {v.status_code}"),
                                       "" if v.ok else "clipper narrate voices --add OWNER:VOICE_ID")

    if role in ("all", "bot") and cfg["paths"]["youtube_token"].exists():
        try:  # the file can be there long after the sign-in stopped working
            from . import youtube
            youtube.credentials(cfg["paths"], force_refresh=True)
            ok("online", "YouTube sign-in works (token refreshed)")
        except ClipperError as e:
            fail("online", str(e).split("; run:")[0], "clipper upload --sign-in; see SETUP.md (A new YouTube channel)")
        except ImportError as e:
            warn("online", f"YouTube sign-in not checked: {e}", "uv sync")
    if role != "nas":
        check_llm_online(cfg)
    if role != "nas" and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from .llm import Claude
            Claude(cfg).client.models.retrieve(cfg["scoring"]["model_a"])
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
        except Exception as e:  # noqa: BLE001
            fail("online", f"Kitsu: {e}", "is the NAS up, and this machine on its network?")
    if cfg["discord"]["enabled"] or role == "bot":
        try:
            from .notify import token
            r = requests.get("https://discord.com/api/v10/users/@me",
                             headers={"Authorization": f"Bot {token(cfg)}"}, timeout=15)
            (ok if r.ok else fail)("online", f"Discord bot {r.json().get('username', '?')}" if r.ok
                                   else f"Discord refused the token ({r.status_code})",
                                   "" if r.ok else "reset the bot token in the Developer Portal")
        except Exception as e:  # noqa: BLE001
            fail("online", f"Discord: {e}")
    if role in ("all", "gpu") and cfg["transcribe"]["backend"] == "modal":
        try:
            import modal
            modal.Cls.from_name(cfg["transcribe"]["modal_app"], "WhisperX").hydrate()
            ok("online", f"Modal app {cfg['transcribe']['modal_app']!r} deployed")
        except ImportError:
            pass  # check_gpu already failed it
        except Exception as e:  # noqa: BLE001
            fail("online", f"Modal app {cfg['transcribe']['modal_app']!r}: {type(e).__name__}: {str(e)[:120]}",
                 "clipper transcribe --deploy (after setting the Modal token)")
        if diarizing(cfg):
            try:
                import modal

                from .modal_app import HF_SECRET
                modal.Secret.from_name(HF_SECRET).hydrate()
                ok("online", f"Modal secret {HF_SECRET!r} for diarization")
            except ImportError:
                pass
            except Exception as e:  # noqa: BLE001
                fail("online", f"diarize is on but the Modal secret {HF_SECRET!r}: {type(e).__name__}",
                     "see SETUP.md (Speaker labels): modal secret create huggingface HF_TOKEN=..., "
                     "then clipper transcribe --deploy")
    cc = cfg["cut"]
    if role in ("all", "gpu") and cc["backend"] == "nas":
        missing = [k for k in ("nas_host", "nas_data") if not cc[k]]
        if missing:
            fail("online", f"[cut] backend = \"nas\" without [cut] {', '.join(missing)}",
                 "set them in clipper.toml (the ssh alias, and the recorder's CLIPPER_DATA)")
        else:
            probe = f"docker image inspect --format '{{{{.Id}}}}' {cc['nas_image']} && test -d {cc['nas_data']}/streams"
            r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", cc["nas_host"], probe])
            if r.returncode == 0:
                ok("online", f"cut on the NAS: {cc['nas_host']} has {cc['nas_image']} and {cc['nas_data']}/streams")
            else:
                fail("online", f"cut on the NAS: {(r.stderr or r.stdout).strip()[-160:]}",
                     "key login to the NAS (ssh -o BatchMode=yes <host>), and deploy the recorder once "
                     "(bash deploy/record/deploy.sh): its image does the cutting")
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


def check_tailnet(cfg: dict, offline: bool) -> None:
    """[warroom] tailnet: the phone reaches the war room through `tailscale serve` here."""
    if not cfg["warroom"]["tailnet"]:
        return
    from .warroom import tailnet_status, token

    hint = "SETUP.md, Phone access (Tailscale)"
    st = tailnet_status(cfg)
    if not st["installed"]:
        return fail("warroom", "[warroom] tailnet is on but Tailscale isn't installed", hint)
    if st["state"] != "Running":
        return fail("warroom", f"Tailscale is {st['state'] or 'not running'}", "sign in from its tray icon")
    if not st["served"]:
        return fail("warroom", f"Tailscale is up as {st['name']}, but the war room isn't served",
                    "clipper warroom --tailnet on")
    if not token(cfg):
        warn("warroom", "no war room token: any device on the tailnet can open it",
             "clipper warroom --tailnet on makes one in secrets/")
    if offline:
        return ok("warroom", f"on the tailnet at {st['served']}")
    import requests
    try:  # through the tailnet proxy to the running war room (the bot's)
        r = requests.get(st["served"].rstrip("/") + "/manifest.webmanifest", timeout=10)
        r.raise_for_status()
        ok("warroom", f"on the tailnet at {st['served']} (answers)")
    except Exception as e:  # noqa: BLE001
        fail("warroom", f"{st['served']} doesn't answer ({type(e).__name__})",
             "is the bot (or clipper warroom) running here?")


def check_role_settings(cfg: dict, role: str) -> None:
    """Settings whose absence makes a role exit at start (#77 L12)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(cfg["schedule"]["timezone"])
        ok("config", f"[schedule] timezone {cfg['schedule']['timezone']}")
    except (ZoneInfoNotFoundError, ValueError):
        fail("config", f"[schedule] timezone {cfg['schedule']['timezone']!r} isn't a time zone",
             'an IANA name, e.g. "America/Los_Angeles"')
    dc = cfg["discord"]
    # --role bot always (the bot needs them even with card posting off); "all" when Discord is on
    if role == "bot" or (role == "all" and dc["enabled"]):
        for k, why in (("guild_id", "the bot refuses to start without it"),
                       ("allowed_user_ids", "the bot refuses to start without it"),
                       ("review_channel_id", "review cards have nowhere to go")):
            (ok if dc[k] else fail)("config", f"[discord] {k}" + (" set" if dc[k] else " not set"),
                                    "" if dc[k] else f"{why}; see SETUP")
    if role in ("all", "nas"):
        chans = cfg["record"].get("channels")
        (ok if chans else warn)("config", "[record] channels " + ("set" if chans else "empty: the recorder has nothing to watch"),
                                "" if chans else "add the Kick channel slugs to record")


def main(cfg: dict, offline: bool = False, role: str = "all") -> int:
    RESULTS.clear()
    check_python()
    check_config(cfg)
    check_role_settings(cfg, role)
    if role != "nas":
        check_prompts(cfg)
    check_ffmpeg(cfg, role)
    if role in ("all", "gpu"):
        check_fonts(cfg)
        check_photos(cfg)
        check_gpu(cfg)
    check_keys(cfg, role)
    if role in ("all", "gpu"):
        check_narrate(cfg)
    if role in ("all", "bot"):
        check_tailnet(cfg, offline)
    if not offline:
        check_online(cfg, role)
    n = report(RESULTS)
    print(f"\n{n['OK']} ok, {n['WARN']} warnings, {n['FAIL']} failures")
    return 1 if n["FAIL"] else 0
