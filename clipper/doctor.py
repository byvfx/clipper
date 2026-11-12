"""`clipper doctor`: check that this machine can run clipper, and say what to fix.

Usage:
    clipper doctor [--offline] [--role all|gpu|serve|bot|nas|claude|control]

Checks Python, the config and prompt files, ffmpeg (filters and the configured encoders),
fonts, the GPU stack (torch + WhisperX, only needed where transcribe runs), API keys and
tokens, and, unless --offline, that Anthropic, Kitsu, Discord and Kick answer. Each line is
OK, WARN (works, but something is missing or slow) or FAIL (a stage will break); the exit
code is 1 when anything fails. --role skips what that machine doesn't run: gpu (a worker:
everything but serve and the bot), serve (`clipper serve`: the war room, uploads, pre-shows,
retakes, the daily jobs), bot (the Discord bot, which needs serve to answer), nas (the
recorder only), claude (Claude Code on the NAS: what a worker needs, plus its sign-in and clone),
control (a dev/control station: the keys, the war room, and ssh to the hub for `clipper services`).
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

from . import jsonstate, prompts, wiki
from .config import base, for_show
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


def check_command() -> None:
    """`clipper` typed bare: the bootstrap puts a command in ~/.local/bin that runs this venv's."""
    found = shutil.which("clipper")
    if found:
        ok("command", f"clipper -> {found}")
    else:
        warn("command", "`clipper` isn't on PATH (only the venv's own clipper works)",
             "run the bootstrap again (deploy\\bootstrap.ps1 or deploy/bootstrap.sh): it adds a "
             "clipper command to ~/.local/bin, then open a new terminal")


def check_config(cfg: dict, role: str = "all") -> None:
    root = base(cfg)
    if root.get("_file"):
        ok("config", f"{root['_file']}")
    else:
        fail("config", "no clipper.toml found", "clipper setup (or copy clipper.example.toml to clipper.toml)")
    from .config import problems
    for msg in problems(cfg):
        fail("config", msg, "fix the name (clipper/defaults.toml lists every setting) or remove it")
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
        (warn if role == "control" else fail)("paths", f"streams folder {streams} doesn't exist",
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
    """Every prompt file this (show's) config reads: each prompt in prompts.VERSIONS at
    the version its config key sets, the [scoring] rule files for that version, and the weekly
    article example."""
    sc, v = cfg["scoring"], cfg["scoring"]["prompt_version"]
    enquirer = cfg["thumbnail"]["style"] == "enquirer"
    # Prompts a config may never read: the other thumbnail style's, and the cast and news
    # labellers' when those features are off (Copilot on #174).
    unread = {"thumbnail": enquirer, "thumbnail_retry": enquirer,
              "thumbnail_enquirer": not enquirer, "thumbnail_enquirer_retry": not enquirer,
              "cast_label": not cfg["cast"]["label_model"], "news_judge": not cfg["news"]["judge_model"]}
    names = [f"{name}_{prompts.version_of(cfg, name)}" for name in prompts.VERSIONS if not unread.get(name)]
    # The scan and rule files came in v3/v4; before v4 they are read only if present.
    if int(v.lstrip("v") or 0) < 4:
        names.remove(f"scan_{v}")
    else:
        names += [f"third_party_{sc['third_party_audio']}_{v}", f"audience_{sc['audience_knowledge']}_{v}"]
    if int(v.lstrip("v") or 0) >= 7:
        names.append(f"gambling_{sc['gambling']}_{v}")
    return [f"{n}.md" for n in names] + [cfg["weekly"]["article_example"]]


def check_prompts(cfg: dict) -> None:
    """Each show can set its own prompt versions, so every show's files are checked. The files
    ship in the package (clipper/prompts/); [paths] prompts, when set, is read first."""
    shows = list(base(cfg)["shows"])
    users: dict[Path, list[str]] = {}   # file -> the configs that read it
    extras: dict[Path, list[str]] = {}  # [paths] prompts folder -> the configs that name it (a show has its own)
    for key in ["", *shows]:
        c = for_show(cfg, key)
        if (d := prompts.extra_dir(c)) is not None:
            extras.setdefault(d, []).append(key or "base")
        for name in prompt_files(c):
            users.setdefault(prompts.find(c, name), []).append(key or "base")
    missing = [(p, who) for p, who in users.items() if not p.exists()]
    absent = [(d, who) for d, who in extras.items() if not d.is_dir()]
    if missing:
        fail("prompts", f"missing in {prompts.PACKAGED}" + "".join(f" and {d}" for d in extras) + ": " + ", ".join(
            p.name if len(who) == len(shows) + 1 else f"{p.name} ({', '.join(who)})"
            for p, who in missing),
             "fix the prompt version keys, or put the file in the folder [paths] prompts names")
    elif absent:
        warn("prompts", "[paths] prompts names a folder that isn't there: "
             + ", ".join(f"{d} ({', '.join(who)})" for d, who in absent)
             + "; the prompts that ship with the code are read instead",
             "drop the key, or create the folder for prompt files of your own")
    else:
        ok("prompts", f"{len(users)} prompt files for the configured versions"
           + (f" (base and {len(shows)} shows)" if shows else "")
           + (f", {', '.join(str(d) for d in extras)} read first" if extras else ""))


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
            (warn if role == "control" else fail)(
                "ffmpeg", f"no {f} filter: {why} won't work",
                "install a full ffmpeg build (Windows: gyan.dev 'full'; macOS: brew install ffmpeg-full, "
                "then put \"$(brew --prefix ffmpeg-full)/bin\" first on PATH: it's keg-only)")
    if role in ("nas", "control"):
        return  # nothing encodes there
    codecs = {cfg["cut"]["clip_codec"], cfg["shorts"]["codec"] or cfg["cut"]["clip_codec"],
              cfg["review"]["preview_codec"] or cfg["cut"]["clip_codec"],
              cfg["broll"]["codec"] or cfg["cut"]["clip_codec"]}
    for codec in sorted(c for c in codecs if c and c != "copy"):
        works, err = encoder_works(codec)
        if works:
            ok("ffmpeg", f"encoder {codec} works")
        else:
            hint = {"h264_qsv": "needs an Intel GPU; clipper setup --step machine picks one that works here",
                    "h264_nvenc": "needs an NVIDIA driver new enough for this ffmpeg (610+ for 8.1)",
                    }.get(codec, "pick another [cut] clip_codec (clipper setup --step machine)")
            fail("ffmpeg", f"encoder {codec} failed: {err[:120]}", hint)


# Linux has none of the fonts Windows and macOS come with; deploy/bootstrap.sh installs these.
# A declined (or unanswered) license leaves the package installed without the fonts, where
# apt-get install does nothing: dpkg-reconfigure asks again.
LINUX_FONTS = ("sudo apt-get install ttf-mscorefonts-installer fonts-roboto-unhinted; already installed: "
               "sudo dpkg-reconfigure ttf-mscorefonts-installer")


def font_hint(hint: str) -> str:
    return f"{hint} (Linux: {LINUX_FONTS})" if sys.platform.startswith("linux") else hint


def check_fonts(cfg: dict) -> None:
    from .draw import first_font, font_dirs

    tc = cfg["thumbnail"]
    found = first_font([tc["font"], *tc["fonts"]])
    if found:
        ok("fonts", f"thumbnail font {found}")
    else:
        fail("fonts", "none of [thumbnail] fonts exist", font_hint("install Impact or set [thumbnail] font"))
    if tc["style"] == "enquirer":
        cond = first_font(tc["condensed_fonts"])
        (ok if cond else warn)("fonts", f"condensed font {cond}" if cond else "no condensed font for enquirer covers",
                               "" if cond else font_hint("install Bahnschrift / Roboto Condensed or set "
                                                         "[thumbnail] condensed_fonts"))
    wc = cfg["weekly"]
    from .weekly import style as weekly_style
    if weekly_style.masthead(wc) is None:
        warn("config", "[weekly] masthead is not set: `clipper weekly` refuses until it has the cover's two words",
             'masthead = ["DAILY", "PLANET"] (red word, chrome word; "WEEKLY" follows)')
    missing = [k for k in ("headline", "condensed", "serif_italic", "serif_bold_italic", "black", "bold")
               if not first_font(wc[f"{k}_fonts"])]
    if missing:
        warn("fonts", f"weekly issue fonts missing: {', '.join(missing)}",
             font_hint("install Impact, Bahnschrift (or Roboto Condensed), Georgia and Arial, or set [weekly] *_fonts"))
    else:
        ok("fonts", "weekly issue fonts (Impact, condensed, Georgia italic, Arial)")
    look = Path(cfg["paths"]["weekly"]) / "style.toml"
    if look.exists():
        try:
            weekly_style.load(cfg)
            ok("fonts", f"weekly look: {look}")
        except ClipperError as e:
            fail("fonts", str(e), "fix or remove it: its tables and keys are clipper/weekly/style.py's; "
                 + wiki.page("Weekly-issue", "style"))
    sc = cfg["shorts"]
    name = sc["caption_font"].lower().replace(" ", "")
    dirs = [Path(sc["caption_fontsdir"])] if sc["caption_fontsdir"] else font_dirs()
    hit = next((p for d in dirs if d.exists() for p in d.rglob("*")
                if p.suffix.lower() in (".ttf", ".otf") and name in p.stem.lower().replace(" ", "")), None)
    if hit:
        ok("fonts", f"caption font {sc['caption_font']!r}: {hit}")
    else:
        warn("fonts", f"caption font {sc['caption_font']!r} not found; libass falls back to another font",
             font_hint("install it, or set [shorts] caption_fontsdir to a folder with the .ttf"))


def diarizing(cfg: dict) -> list[str]:
    """Shows (or "base") with [transcribe] diarize on."""
    return [k or "base" for k in ["", *base(cfg)["shows"]] if for_show(cfg, k)["transcribe"]["diarize"]]


def check_gpu(cfg: dict) -> None:
    tc = cfg["transcribe"]
    if diarizing(cfg) and tc["backend"] == "whisperx":
        if os.environ.get("HF_TOKEN"):
            ok("gpu", "HF_TOKEN set for diarization")
        else:
            fail("gpu", f"[transcribe] diarize is on ({', '.join(diarizing(cfg))}) but HF_TOKEN isn't set",
                 "a Hugging Face read token with the pyannote terms accepted; " + wiki.page("Speakers"))
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


def check_llm_keys(cfg: dict, need=None) -> None:
    from . import llm

    need = need or fail

    for prov, uses in llm_providers(cfg).items():
        env = llm.PROVIDERS[prov][1]
        what = f"{prov} models ({'; '.join(uses[:3])}{'...' if len(uses) > 3 else ''})"
        if prov == "local":
            if cfg["llm"]["local_base_url"]:
                ok("keys", f"{what}: [llm] local_base_url = {cfg['llm']['local_base_url']}")
            else:
                need("keys", f"{what}: [llm] local_base_url not set",
                     "set it in clipper.toml to the server's OpenAI-compatible /v1 URL")
        elif os.environ.get(env):
            ok("keys", f"{what}: {env} set")
        elif llm.provider_key(prov, cfg["paths"]):
            ok("keys", f"{what}: key in {cfg['paths'][f'{prov}_key']}")
        else:
            need("keys", f"{what}: {env} not set",
                 f"set it as a user environment variable, or put the key in {cfg['paths'].get(f'{prov}_key')}")


def check_llm_prices(cfg: dict) -> None:
    """Every configured model has an [llm] prices entry, else `clipper costs` counts it as $0."""
    from . import llm

    table = llm.prices(cfg)  # the defaults plus [llm.prices], as the client accounts
    # OpenRouter reports each call's cost, so its models need no entry.
    unpriced = sorted({m for m in llm.configured(cfg).values()
                       if llm.price_of(table, m) is None and llm.split(m)[0] != "openrouter"})
    if unpriced:
        warn("keys", f"no [llm] prices entry for {', '.join(unpriced)}: costs count them as $0",
             'add [llm.prices] "<model>" = [<$ per 1M in>, <$ per 1M out>] to clipper.toml')
    else:
        ok("keys", "every configured model has a price")


def check_llm_online(cfg: dict) -> None:
    """Each non-Claude provider in use lists its models, and has the configured ones."""
    import requests

    from . import llm

    for prov, uses in llm_providers(cfg).items():
        base, env = llm.PROVIDERS[prov]
        base = (cfg["llm"]["local_base_url"] if prov == "local" else base).rstrip("/")
        key = llm.provider_key(prov, cfg["paths"]) if prov != "local" else os.environ.get(env, "")
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
        if prov == "openrouter":  # its /models answers anyone; /key only a good key
            try:
                k = requests.get(f"{base}/key", timeout=15, headers={"Authorization": f"Bearer {key}"})
            except requests.RequestException as e:
                fail("online", f"openrouter: {base}/key unreachable ({type(e).__name__})")
                continue
            if not k.ok:
                fail("online", f"openrouter refused the key (HTTP {k.status_code})",
                     f"check {env} or {cfg['paths'].get('openrouter_key')}")
                continue
            ok("online", "openrouter key accepted")
        have = {m.get("id", "").removeprefix("models/") for m in r.json().get("data", [])}
        for use in uses:
            name = llm.split(use.split(" = ", 1)[1])[1]
            (ok if name in have else warn)("online", f"{prov} model {name}"
                                           + ("" if name in have else " not listed by the server"))


def check_keys(cfg: dict, role: str) -> None:
    # a control station runs no stage and uses Kitsu in the browser: missing keys only warn there
    need = warn if role == "control" else fail
    if role != "nas":
        from .llm import claude_model, provider_key

        if os.environ.get("ANTHROPIC_API_KEY"):
            ok("keys", "ANTHROPIC_API_KEY set")
        elif provider_key("anthropic", cfg["paths"]):
            ok("keys", f"Anthropic key in {cfg['paths']['anthropic_key']}")
        elif not claude_model(cfg):
            ok("keys", "ANTHROPIC_API_KEY not set; no stage uses Claude")
        else:
            need("keys", "ANTHROPIC_API_KEY not set", "clipper setup --step keys (or set it as a user env var)")
        check_llm_keys(cfg, need)
        check_llm_prices(cfg)
        rc = cfg["review"]
        if rc["host"] and "<" not in rc["host"]:
            ok("keys", f"[review] host = {rc['host']}")
        else:
            need("keys", "[review] host not set", "clipper setup --step keys, or set it to your Kitsu URL + /api in clipper.toml")
        (ok if os.environ.get(rc["token_env"]) else need)(
            "keys", f"${rc['token_env']} (Kitsu bot token) " + ("set" if os.environ.get(rc["token_env"]) else "not set"),
            "" if os.environ.get(rc["token_env"]) else "Kitsu > Bots > new token; clipper setup --step keys")
    if (cfg["discord"]["enabled"] and role != "control") or role == "bot":
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
    if role in ("all", "serve"):
        p = cfg["paths"]
        if p["youtube_token"].exists():
            ok("keys", f"YouTube sign-in token {p['youtube_token'].name}")
        elif p["youtube_client_secret"].exists():
            warn("keys", "YouTube client secret present but not signed in", "clipper upload --sign-in")
        else:
            warn("keys", "no YouTube client secret: `clipper upload` won't work here",
                 f"copy {p['youtube_client_secret'].name} and {p['youtube_token'].name} into secrets/")
        check_tiktok(cfg)
        check_x(cfg)
        from .usage import ADMIN_ENV, admin_key

        if admin_key(cfg):
            ok("keys", "Anthropic Admin key found (the war room's API usage panel reads Claude's bill)")
        else:
            warn("keys", "no Anthropic Admin key: the API usage panel shows our log for Claude, not its bill",
                 f"Console -> Settings -> Admin keys (organizations only); set {ADMIN_ENV} "
                 f"or put it in {cfg['paths'].get('anthropic_admin_key')}")


COOKIES_STALE_HOURS = 24  # the laptop's clipper-cookies task exports every 4 h


def check_youtube_cookies_file(cfg: dict, now: float | None = None) -> None:
    """No browser here (the NAS hub): the cookies file the signed-in retries copy, when there is
    one, still holds a YouTube sign-in, and the laptop's export task has written it lately. Only
    the export writes it: yt-dlp gets a private copy (11-09)."""
    from . import ytdl

    f = ytdl.cookies_file(cfg)
    if not f.is_file():
        return  # optional: no signed-in retries
    hint = wiki.page("Archives", "keeping-the-nas-hubs-cookies-fresh")
    try:
        names = ytdl.session_names(ytdl.read_cookies(f), now)
    except (OSError, ValueError, KeyError) as e:
        fail("keys", f"YouTube cookies {f.name} unreadable: {e}", hint)
        return
    if not names:
        fail("keys", f"YouTube cookies {f.name} hold no live sign-in",
             "sign in again in the laptop's firefox:clipper profile, close it, run "
             "deploy\\cookies\\cookies-task.ps1 -Run; " + hint)
        return
    hours = ((time.time() if now is None else now) - f.stat().st_mtime) / 3600
    if hours > COOKIES_STALE_HOURS:
        warn("keys", f"YouTube cookies {f.name} last exported {hours:.0f} h ago",
             "is the laptop on and its clipper-cookies task running? "
             "deploy\\cookies\\cookies-task.ps1 -Status; " + hint)
    else:
        ok("keys", f"YouTube cookies {f.name} signed in, exported {hours:.0f} h ago")


def check_youtube_cookies(cfg: dict) -> None:
    """[youtube] cookies_from_browser, when set: the profile is there and still signed in to
    YouTube (its cookie database on disk, no network). Unset: the cookies file instead."""
    from . import ytdl

    if not (spec := ytdl.browser(cfg)):
        check_youtube_cookies_file(cfg)
        return
    hint = wiki.page("Archives", "signed-in-through-a-browser-profile")
    try:
        names = ytdl.browser_session(spec)
    except ClipperError as e:
        fail("keys", str(e), hint)
        return
    except ImportError:
        warn("keys", "yt-dlp not installed: YouTube browser cookies not checked", "uv sync")
        return
    if names:
        ok("keys", f"YouTube signed in through the browser ({spec})")
    else:
        fail("keys", f"the browser profile {spec} isn't signed in to YouTube",
             "open that profile, sign in to YouTube, close it; " + hint)


def check_tiktok(cfg: dict) -> None:
    """Optional: only `clipper tiktok` needs it (parked since 2026-11-01, so a stale sign-in warns)."""
    p = cfg["paths"]
    if not p["tiktok_client"].exists():
        warn("keys", "no TikTok client key: `clipper tiktok` won't work here (optional)",
             f"save the app's key and secret in {p['tiktok_client']}; {wiki.page('TikTok')}")
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
        warn("keys", "TikTok sign-in expired (optional)", "clipper tiktok --sign-in")
    elif "video.upload" not in tok.get("scope", ""):
        warn("keys", f"TikTok token lacks video.upload (has {tok.get('scope')!r})",
             "add the scope to the app, then clipper tiktok --sign-in")
    else:
        (warn if days < 14 else ok)("keys", f"TikTok sign-in token {p['tiktok_token'].name} "
                                    f"(renew within {days:.0f} days)", "clipper tiktok --sign-in")


def check_x(cfg: dict) -> None:
    """Optional: only `clipper x` needs it."""
    from .xpost import SCOPES

    p = cfg["paths"]
    if not p["x_client"].exists():
        warn("keys", "no X client id: `clipper x` won't work here (optional)",
             f"save the app's client id in {p['x_client']}; {wiki.page('X-posts')}")
        return
    try:
        c = json.loads(p["x_client"].read_text(encoding="utf-8-sig"))
        assert c.get("client_id")
    except (ValueError, AssertionError, AttributeError):
        fail("keys", f"{p['x_client'].name} is not JSON with client_id", '{"client_id": "..."}')
        return
    if not p["x_token"].exists():
        warn("keys", "X client id present but not signed in", "clipper x --sign-in")
        return
    tok = jsonstate.load(p["x_token"])
    missing = set(SCOPES.split()) - set(tok.get("scope", "").split())
    if missing:
        fail("keys", f"X token lacks {' '.join(sorted(missing))}", "clipper x --sign-in")
    elif not tok.get("refresh_token"):
        warn("keys", "X token has no refresh token: it lapses in 2 h", "clipper x --sign-in")
    else:
        ok("keys", f"X sign-in token {p['x_token'].name}")


def check_narrate(cfg: dict) -> None:
    """Optional: only `clipper narrate`, receipts' voice-over and story narration need it."""
    from .narrate import find_key, settings

    nc = settings(cfg)
    where = f"${nc['key_env']} or {nc['key_file']}"
    # a show can set its own narrator (and key), so each show's story settings are checked
    unread = []   # each with where its own key would be
    for key in ["", *base(cfg)["shows"]]:
        c = for_show(cfg, key)
        sn = settings(c)
        if c["story"]["narrate"] and c["story"]["voice_id"] and not find_key(sn):
            unread.append(f"{key or 'base'}: ${sn['key_env']} or {sn['key_file']}")
    if unread:
        fail("keys", f"[story] voice_id is set but there's no ElevenLabs key ({'; '.join(unread)}): "
             "story narration can't be read", wiki.page("Story-dossiers", "narration"))
    if not find_key(nc):
        if nc["voice_id"]:
            fail("keys", f"[narrate] voice_id is set but there's no ElevenLabs key ({where})",
                 wiki.page("Character-pieces", "narration-voice-elevenlabs"))
        else:
            warn("keys", f"no ElevenLabs key ({where}): `clipper narrate` won't work here (optional)",
                 wiki.page("Character-pieces", "narration-voice-elevenlabs"))
        return
    if not nc["voice_id"]:
        warn("keys", "ElevenLabs key found but no [narrate] voice_id", "clipper narrate voices")
    else:
        ok("keys", f"ElevenLabs key found, voice {nc['voice_id']}")


def check_online(cfg: dict, role: str) -> None:
    """That the services this role uses answer, in this order."""
    online_elevenlabs(cfg)
    if role in ("all", "serve"):
        online_youtube(cfg)
    if role != "nas":
        check_llm_online(cfg)
        online_anthropic(cfg)
        online_kitsu(cfg)
    if (cfg["discord"]["enabled"] and role != "control") or role == "bot":
        online_discord(cfg)
    if role in ("all", "bot", "control"):
        online_serve(cfg, role)
    if role == "control":
        online_hub(cfg)
    if role in ("all", "gpu", "claude"):
        online_modal(cfg)
        online_nas_cut(cfg)
        online_preshow_feeds(cfg)  # the worker grabs pre-shows
    online_kick(cfg)


def online_elevenlabs(cfg: dict) -> None:
    import requests

    from .narrate import find_key, settings

    nc = settings(cfg)
    if not (el_key := find_key(nc)):
        return
    try:
        r = requests.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": el_key}, timeout=15)
        if not r.ok:
            fail("online", f"ElevenLabs refused the key (HTTP {r.status_code})",
                 f"check ${nc['key_env']} / {nc['key_file']}")
            return
        tier = (r.json().get("subscription") or {}).get("tier", "?")
        ok("online", f"ElevenLabs answers (plan {tier})")
        if nc["voice_id"]:
            v = requests.get(f"https://api.elevenlabs.io/v1/voices/{nc['voice_id']}",
                             headers={"xi-api-key": el_key}, timeout=15)
            (ok if v.ok else fail)("online", f"ElevenLabs voice {nc['voice_id']}: "
                                   + (v.json().get("name", "?") if v.ok else f"HTTP {v.status_code}"),
                                   "" if v.ok else "clipper narrate voices --add OWNER:VOICE_ID")
    except requests.RequestException as e:  # unreachable is a finding, not a doctor crash
        fail("online", f"ElevenLabs didn't answer: {type(e).__name__}", "check the network")


def online_youtube(cfg: dict) -> None:
    """The YouTube sign-in still refreshes (the token file can be there long after the
    sign-in stopped working)."""
    if not cfg["paths"]["youtube_token"].exists():
        return
    try:
        from . import youtube
        youtube.credentials(cfg["paths"], force_refresh=True)
        ok("online", "YouTube sign-in works (token refreshed)")
    except ClipperError as e:
        fail("online", str(e).split("; run:")[0], "clipper upload --sign-in; " + wiki.page("YouTube-channel"))
    except ImportError as e:
        warn("online", f"YouTube sign-in not checked: {e}", "uv sync")


def online_anthropic(cfg: dict) -> None:
    from .llm import Claude, claude_model, provider_key

    model = claude_model(cfg)  # [scoring] model_a may be another provider's
    if not provider_key("anthropic", cfg["paths"]) or not model:
        return
    try:
        Claude(cfg).client.models.retrieve(model)
        ok("online", f"Anthropic API answers ({model} available)")
    except Exception as e:  # noqa: BLE001
        fail("online", f"Anthropic API: {type(e).__name__}: {str(e)[:120]}",
             "check the key, or set [scoring] workspace_id / $ANTHROPIC_WORKSPACE_ID")


def online_kitsu(cfg: dict) -> None:
    """Kitsu, and each show's production in it."""
    rc = cfg["review"]
    if not (os.environ.get(rc["token_env"]) and rc["host"] and "<" not in rc["host"]):
        return
    try:
        from . import review
        gazu = review.connect(rc)
        ok("online", f"Kitsu answers at {rc['host']}")
        for key in base(cfg)["shows"] or [""]:
            name = review.project_name(for_show(cfg, key))
            if gazu.project.get_project_by_name(name):
                ok("online", f"Kitsu production {name!r}")
            else:
                warn("online", f"Kitsu production {name!r} doesn't exist yet",
                     "created by the first `clipper review publish` of that show")
    except Exception as e:  # noqa: BLE001
        fail("online", f"Kitsu: {e}", "is the NAS up, and this machine on its network?")


def online_discord(cfg: dict) -> None:
    import requests

    try:
        from .notify import token
        r = requests.get("https://discord.com/api/v10/users/@me",
                         headers={"Authorization": f"Bot {token(cfg)}"}, timeout=15)
        (ok if r.ok else fail)("online", f"Discord bot {r.json().get('username', '?')}" if r.ok
                               else f"Discord refused the token ({r.status_code})",
                               "" if r.ok else "reset the bot token in the Developer Portal")
    except Exception as e:  # noqa: BLE001
        fail("online", f"Discord: {e}")


def online_serve(cfg: dict, role: str) -> None:
    """That `clipper serve` answers at [serve] url: the bot asks it to replan after a decision
    and to start a retake (a decision still lands in Kitsu without it)."""
    from . import serve

    try:
        serve.health(cfg)
        ok("online", f"clipper serve answers at {serve.url(cfg)}")
    except Exception as e:  # noqa: BLE001
        hint = ("start it: clipper serve, or its container (deploy/hub); set [serve] url when it "
                "runs on another machine")
        if role == "control":
            hint = ("set [serve] url to the hub's tailnet address: clipper setup --step machine ("
                    + wiki.page("Phone-access") + ")")
        (fail if role == "bot" else warn)("online", f"{e}", hint)


def online_hub(cfg: dict) -> None:
    """With [serve] hub: ssh to the NAS works and finds the compose project, which
    `clipper services` and deploy/hub/deploy.sh need."""
    import shlex

    from .services import recorder_ssh, ssh

    folder = cfg["serve"]["hub"]
    if not folder:
        warn("online", "[serve] hub not set: `clipper services` runs Windows tasks, not the NAS containers",
             "set [serve] hub in clipper.toml (the compose folder on the NAS): " + wiki.page("NAS-hub"))
        return
    host, _, sudo = recorder_ssh(cfg)
    r = ssh(host, f"cd {shlex.quote(folder)} && {sudo} docker compose ps --services".strip())
    if r.returncode == 0:
        ok("online", f"hub on {host}:{folder}: " + (", ".join(r.stdout.split()) or "no containers up"))
    else:
        fail("online", f"hub on {host}:{folder}: {(r.stderr or r.stdout).strip()[-160:] or 'ssh failed'}",
             f"key login to the NAS (ssh -o BatchMode=yes {host}; ~/.ssh/config Host {host}): "
             + wiki.page("NAS-hub"))


def online_modal(cfg: dict) -> None:
    """The deployed transcription app (and the diarization secret), with backend = "modal"."""
    if cfg["transcribe"]["backend"] != "modal":
        return
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
        secret = "?"  # (named before the try: modal_app itself can fail to import)
        try:
            import modal

            from .modal_app import HF_SECRET as secret
            modal.Secret.from_name(secret).hydrate()
            ok("online", f"Modal secret {secret!r} for diarization")
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            fail("online", f"diarize is on but the Modal secret {secret!r}: {type(e).__name__}",
                 "modal secret create huggingface HF_TOKEN=..., then clipper transcribe --deploy; "
                 + wiki.page("Speakers"))


def online_nas_cut(cfg: dict) -> None:
    """With [cut] backend = "nas": ssh to the NAS works, and it has the image and the streams."""
    cc = cfg["cut"]
    if cc["backend"] != "nas":
        return
    missing = [k for k in ("nas_host", "nas_data") if not cc[k]]
    if missing:
        fail("online", f"[cut] backend = \"nas\" without [cut] {', '.join(missing)}",
             "set them in clipper.toml (the ssh alias, and the recorder's CLIPPER_DATA)")
        return
    probe = f"docker image inspect --format '{{{{.Id}}}}' {cc['nas_image']} && test -d {cc['nas_data']}/streams"
    r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", cc["nas_host"], probe])
    if r.returncode == 0:
        ok("online", f"cut on the NAS: {cc['nas_host']} has {cc['nas_image']} and {cc['nas_data']}/streams")
    else:
        fail("online", f"cut on the NAS: {(r.stderr or r.stdout).strip()[-160:]}",
             "key login to the NAS (ssh -o BatchMode=yes <host>), and deploy the recorder once "
             "(bash deploy/record/deploy.sh): its image does the cutting")


def online_preshow_feeds(cfg: dict) -> None:
    from . import preshow
    for key, sc in preshow.watched(base(cfg)):
        ch = sc["preshow"]["youtube_channel"]
        try:
            hits = [e for e in preshow.feed(ch, sc["kick"]["user_agent"])
                    if re.search(sc["preshow"]["title_pattern"], e["title"])]
            ok("online", f"pre-show feed for {key} ({ch}): {len(hits)} recent pre-shows")
        except Exception as e:  # noqa: BLE001
            fail("online", f"pre-show feed for {key} ({ch}): {e}",
                 "check [shows.<key>.preshow] youtube_channel (a UC... channel id)")


def online_kick(cfg: dict) -> None:
    import requests

    chans = [c for s in base(cfg)["shows"].values() for c in s["channels"]] or [cfg["kick"]["channel"]]
    chans = [c for c in chans if c]
    if not chans:
        return
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
    check_cast(cfg)


def check_cast(cfg: dict) -> None:
    """#41: the cast library: anyone with a folder or an enrolled face and fewer than [cast]
    min_photos kept photos; the prompt and image search key it uses."""
    from . import cast

    cc = cfg["cast"]
    people = cast.overview(cfg)["people"]
    few = [f"{p['name']} {p['kept']}" for p in people if p["kept"] < cc["min_photos"]]
    if few:
        warn("cast", f"fewer than {cc['min_photos']} photos kept: " + ", ".join(few),
             "war room > Cast (or clipper cast find <Person>, clipper cast grab <stream>)")
    elif people:
        ok("cast", f"{len(people)} people, {cc['min_photos']}+ photos each")
    if not cast.search_key(cfg):
        warn("cast", f"no $BRAVE_API_KEY or {cfg['paths']['brave_key']}: cast image search is off "
             "(Commons, channels and streams still work)",
             "optional: a Brave Search API key (api-dashboard.search.brave.com) in that file or $BRAVE_API_KEY")


def check_people(cfg: dict) -> None:
    """#138: people.json readable, and no name two records answer to (a lookup by it fails)."""
    from . import people

    try:
        everyone = people.load(cfg)
    except Exception as e:  # noqa: BLE001  # a broken people.json: say so, don't stop doctor
        fail("people", f"{people.path(cfg)}: {e}", "fix the JSON, or move it aside and run clipper people import")
        return
    clash = people.conflicts(everyone)
    if clash:
        warn("people", "a name of two people: " + "; ".join(f"{n} ({', '.join(ids)})" for n, ids in clash.items()),
             "clipper people set <id> --unalias NAME, or fix the name in streamers.json / the cast library")
    else:
        own = len(people.stored(cfg))
        ok("people", f"{len(everyone)} people, {own} in {people.path(cfg).name}"
           + ("" if own else " (clipper people import writes them there)"))


def check_news(cfg: dict, offline: bool) -> None:
    """#110: where the bot posts news, the judge's prompt, and (online) whether the feeds answer."""
    nc = cfg["news"]
    if nc["check_minutes"] <= 0:
        return
    from . import news

    if not news.watchlist(cfg):
        ok("news", "nothing to watch ([news] watch is empty): no feeds are read")
        return
    if not (nc["channel"] or nc["channel_id"] or cfg["discord"]["review_channel_id"]):
        fail("news", "[news] check_minutes is on but there is no channel to post to",
             "set [news] channel (a name the bot makes) or channel_id, or [news] check_minutes = 0")
    if offline:
        return
    bad = []
    for f in nc["feeds"][:4]:
        try:
            body, _, _ = news.fetch(f["url"])
            news.parse_feed(body or b"<rss/>")
        except Exception as e:  # noqa: BLE001 - any failure is the finding
            bad.append(f"{f.get('name') or f['url']}: {type(e).__name__}")
    if bad:
        warn("news", "feeds not answering: " + "; ".join(bad), "check the address in [news] feeds")
    else:
        ok("news", f"every {nc['check_minutes']} min, {len(nc['feeds'])} feeds, {len(nc['reddit'])} subreddits"
           + (", Google News" if nc["google_news"] else ""))


def check_broll(cfg: dict) -> None:
    """#229: the b-roll library's settings (its sources need no key)."""
    from . import broll

    bc = cfg["broll"]
    bad = [s for s in bc["sources"] if s not in broll.SOURCES]
    if bad:
        fail("broll", f"[broll] sources: unknown {', '.join(bad)}", f"use some of {', '.join(broll.SOURCES)}")
    bad = [c for c in bc["licences"] if c not in broll.LICENCES]
    if bad:
        fail("broll", f"[broll] licences: unknown {', '.join(bad)}", f"use some of {', '.join(broll.LICENCES)}")
    if not cfg["paths"]["broll"]:
        return
    lib = broll.load(cfg)
    n = {s: sum(e["status"] == s for e in lib["clips"].values()) for s in broll.STATUSES}
    ok("broll", f"{cfg['paths']['broll']}: {n['kept']} kept, {n['new']} new, {n['long']} long")


def check_cast_serve(cfg: dict) -> None:
    """serve's daily `cast auto` and the war room's Cast window check faces with OpenCV."""
    import importlib.util

    if cfg["thumbnail"]["photos"] and cfg["cast"]["auto_hours"] > 0 and not importlib.util.find_spec("cv2"):
        warn("cast", "OpenCV not installed: serve's daily cast find and the Cast window can't add photos",
             "uv sync --extra faces, or [cast] auto_hours = 0")


def check_faces(cfg: dict) -> None:
    from . import faces

    try:
        import cv2
    except ImportError:
        if "faces" in cfg["worker"]["stages"]:
            fail("faces", "OpenCV not installed, and [worker] stages runs faces on every stream",
                 "uv sync --extra faces, or take \"faces\" out of [worker] stages")
        else:
            warn("faces", "OpenCV not installed: `clipper faces` can't run here", "uv sync --extra faces")
        return
    missing = [n for n in faces.MODELS if not (faces.model_dir(cfg) / n).exists()]
    ok("faces", f"OpenCV {cv2.__version__}" + (f"; models download on the first scan into {faces.model_dir(cfg)}"
                                               if missing else ", face models in place"))
    known = faces.load_known(cfg)
    if known["people"]:
        ok("faces", f"{len(known['people'])} known: " + ", ".join(sorted(known["people"])))
    else:
        warn("faces", f"no known faces in {faces.known_path(cfg)}",
             "clipper faces <stream>, then --name F01=Name --enroll (or --enroll-photos)")


def repo_state(porcelain: str) -> tuple[str, int, int]:
    """`git status --porcelain --branch` → (branch, commits ahead of upstream, changed files);
    branch "" when HEAD is detached."""
    head, *changed = porcelain.splitlines() or [""]
    m = re.match(r"## (?:No commits yet on )?(\S+?)(?:\.\.\.\S+)?(?: \[(.*)\])?$", head)
    branch = m.group(1) if m and m.group(1) != "HEAD" else ""
    ahead = re.search(r"ahead (\d+)", m.group(2) or "") if m else None
    return branch, int(ahead.group(1)) if ahead else 0, len([c for c in changed if c.strip()])


def claude_auth(stdout: str, returncode: int) -> tuple[bool, str]:
    """`claude auth status` (JSON, exit 0 when signed in) → (signed in, its authMethod)."""
    try:
        data = json.loads(stdout)
    except ValueError:
        data = {}
    method = data.get("authMethod", "") if isinstance(data, dict) else ""
    return returncode == 0, str(method)


def check_claude(cfg: dict) -> None:
    """--role claude: Claude Code on the NAS (deploy/claude) can sign in, work in its own
    clone of the repo and run every stage against the share."""
    hint = wiki.page("Claude-on-the-NAS")
    if not shutil.which("claude"):
        fail("claude", "Claude Code (`claude`) not on PATH", "deploy/claude installs it; " + hint)
    else:
        ok("claude", "Claude Code " + run(["claude", "--version"]).stdout.strip())
        r = run(["claude", "auth", "status"])
        signed_in, method = claude_auth(r.stdout, r.returncode)
        if not signed_in:
            fail("claude", "Claude Code isn't signed in",
                 "docker compose exec claude claude auth login; " + wiki.page("Claude-on-the-NAS", "sign-in"))
        else:
            (ok if method in ("", "claude.ai") else warn)(
                "claude", f"Claude Code signed in ({method or 'method not given'})",
                "" if method in ("", "claude.ai") else "Remote Control needs a claude.ai login: claude auth login")
    for env in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        if os.environ.get(env):
            fail("claude", f"{env} is set: Remote Control refuses to start with it",
                 "deploy/claude/deploy.sh passes the Anthropic key as a file ([paths] anthropic_key); "
                 f"take {env} out of the container's environment")
    root = Path(__file__).resolve().parent.parent
    r = run(["git", "-C", str(root), "status", "--porcelain", "--branch"]) if shutil.which("git") else None
    if r is None or r.returncode:
        fail("claude", f"{root} isn't a git clone", "deploy/claude clones the repo into /app on first start")
    else:
        branch, ahead, changed = repo_state(r.stdout)
        (ok if branch else fail)("claude", f"repo on {branch}" if branch else "repo HEAD detached",
                                 "" if branch else "git switch main (or the branch you were on)")
        if changed or ahead:
            warn("claude", f"repo has {changed} changed file(s), {ahead} unpushed commit(s)",
                 "commit and push, or git stash, before leaving it")
    streams = Path(cfg["paths"]["streams"])
    probe = streams / f".doctor-{os.getpid()}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        ok("claude", f"streams writable at {streams}")
    except OSError as e:
        fail("claude", f"can't write to {streams} ({type(e).__name__})",
             "the container mounts CLIPPER_DATA at /data as PUID:PGID, the owner of the NAS folder")


def check_tailnet(cfg: dict, offline: bool) -> None:
    """[warroom] tailnet: the phone reaches the war room through `tailscale serve` here."""
    if not cfg["warroom"]["tailnet"]:
        return
    from .warroom import tailnet_status, token

    hint = wiki.page("Phone-access")
    st = tailnet_status(cfg)
    if not st["installed"]:
        return fail("warroom", "[warroom] tailnet is on but Tailscale isn't installed", hint)
    if st["state"] != "Running":
        return fail("warroom", f"Tailscale is {st['state'] or 'not running'}",
                    "sign in from its tray icon" if sys.platform in ("win32", "darwin") else
                    "the tailscale container's sign-in link: docker compose logs tailscale (deploy/hub); "
                    + wiki.page("Phone-access", "on-the-nas"))
    if not st["served"]:
        return fail("warroom", f"Tailscale is up as {st['name']}, but the war room isn't served",
                    "clipper warroom --tailnet on" if sys.platform in ("win32", "darwin") else
                    "the tailscale container serves it from deploy/hub/tailscale-serve.json (TS_SERVE_CONFIG): "
                    "redeploy, then docker compose logs tailscale")
    if not token(cfg):
        warn("warroom", "no war room token: any device on the tailnet can open it",
             "clipper warroom --tailnet on makes one in secrets/")
    if offline:
        return ok("warroom", f"on the tailnet at {st['served']}")
    import requests
    # Through the tailnet proxy to the running war room. Userspace Tailscale (the NAS hub)
    # can't route this machine's own requests onto the tailnet: try the proxy's target.
    url = (f"http://127.0.0.1:{cfg['warroom']['port']}/" if st.get("userspace") else st["served"].rstrip("/") + "/")
    try:
        r = requests.get(url + "manifest.webmanifest", timeout=10)
        r.raise_for_status()
        ok("warroom", f"on the tailnet at {st['served']} (answers"
           + (f"; checked at {url}, the address tailscale serve proxies to)" if st.get("userspace") else ")"))
    except Exception as e:  # noqa: BLE001
        fail("warroom", f"{url} doesn't answer ({type(e).__name__})", "is clipper serve running here?")


def serve_elsewhere(cfg: dict) -> bool:
    """[serve] url names another machine (not this one by a local name)."""
    from urllib.parse import urlparse

    host = urlparse(cfg["serve"]["url"] or "").hostname or ""
    return bool(host) and host not in ("localhost", "127.0.0.1", "::1")


def check_shared_with_serve(cfg: dict) -> None:
    """With serve on another machine, the bot still reads what serve writes: schedule.json
    (cards, /schedule) and the news database (what to post). Both must be on the share."""
    if not serve_elsewhere(cfg):
        return
    streams = Path(cfg["paths"]["streams"]).resolve()
    for key in ("schedule", "news"):
        p = Path(cfg["paths"][key]).resolve()
        shared = streams == p or streams in p.parents
        where = "on the streams share" if shared else "is local to this checkout"
        (ok if shared else fail)(
            "config", f"[paths] {key} {where}: {p}",
            "" if shared else f"serve on another machine writes it: put [paths] {key} under [paths] streams "
                              "on both machines (the same path)")


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
                                    "" if dc[k] else f"{why}; see {wiki.page('Install')}")
    ic = cfg["investigate"]
    if ic["discord"] and not (dc["guild_id"] and str(ic["discord_channel"]).strip()):
        fail("config", "[investigate] discord is on but [discord] guild_id or [investigate] discord_channel "
             "is empty", "set both, or [investigate] discord = false")
    if role in ("all", "serve"):
        wc = cfg["warroom"]
        local = wc["host"] in ("127.0.0.1", "localhost", "::1")
        from .warroom import token
        (ok if local or token(cfg) else fail)(
            "config", f"[warroom] host {wc['host']}" + ("" if local or token(cfg) else " with no token"),
            "" if local or token(cfg) else "set [warroom] token (serve refuses to start without it off localhost)")
    if role in ("all", "serve"):
        m = cfg["youtube"]["auto_upload_minutes"]
        (ok if m else warn)("config", f"[youtube] auto_upload_minutes {m}" + ("" if m else ": nothing uploads by itself"),
                            "" if m else "set it (60 is usual): `clipper serve` then replans and uploads approved items on "
                                         "that timer; 0 leaves it to `clipper upload` by hand")
    if role == "bot" and not dc["enabled"]:
        warn("config", "[discord] enabled = false on a bot machine: `review publish` posts no cards, so the bot "
                       "has none to show", "set [discord] enabled = true (with the ids above), or don't run `clipper bot` here")
    if role in ("all", "bot"):
        u = cfg["serve"]["url"]
        (ok if not u or u.startswith(("http://", "https://")) else fail)(
            "config", f"[serve] url {u or '(this machine)'}", "" if not u or u.startswith("http") else "an http(s) URL")
        check_shared_with_serve(cfg)
    if role in ("all", "nas"):
        chans = cfg["record"].get("channels")
        (ok if chans else warn)("config", "[record] channels " + ("set" if chans else "empty: the recorder has nothing to watch"),
                                "" if chans else "add the Kick channel slugs to record")


def main(cfg: dict, offline: bool = False, role: str = "all") -> int:
    RESULTS.clear()
    check_python()
    check_command()
    check_config(cfg, role)
    check_role_settings(cfg, role)
    if role != "nas":
        check_prompts(cfg)
    check_ffmpeg(cfg, role)
    if role in ("all", "gpu", "claude"):
        check_fonts(cfg)
        check_photos(cfg)
        check_faces(cfg)
        check_gpu(cfg)
    check_keys(cfg, role)
    if role not in ("nas", "control"):
        check_youtube_cookies(cfg)  # archive fetches; a control station doesn't run them
    if role in ("all", "gpu", "claude"):
        check_narrate(cfg)
        check_broll(cfg)
    if role in ("all", "serve"):
        check_tailnet(cfg, offline)
    if role == "serve":
        check_cast_serve(cfg)
    if role in ("all", "serve"):
        check_people(cfg)
    if role in ("all", "serve", "bot"):
        check_news(cfg, offline)
    if role == "claude":
        check_claude(cfg)
    if not offline:
        check_online(cfg, role)
    n = report(RESULTS)
    print(f"\n{n['OK']} ok, {n['WARN']} warnings, {n['FAIL']} failures")
    return 1 if n["FAIL"] else 0
