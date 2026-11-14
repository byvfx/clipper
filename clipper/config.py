"""Config loading: defaults.toml < clipper.toml < clipper.local.toml < CLIPPER_<SECTION>_<KEY> env vars.

The file is found via --config, then $CLIPPER_CONFIG, then ./clipper.toml, then (Windows)
the user-scope CLIPPER_CONFIG a shell may not have inherited: the laptop's points at the one
on the NAS share since 2026-11-05. Relative paths in [paths] resolve against the config
file's directory; [paths] prompts is optional, a folder of your own read before the package's.

clipper.local.toml (beside clipper.toml, or $CLIPPER_LOCAL_CONFIG) holds this machine's own
values (streams path, codec, transcribe backend; `clipper setup` writes it), so clipper.toml
can stay identical on every machine. Like env vars, it wins over show and stream overrides.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from . import jsonstate
from .errors import ClipperError, did_you_mean

# Every default, with a comment on what it does, is defaults.toml beside this file: that file is
# the documentation of the settings, and this is it loaded. TOML has no null, so a key whose
# default is None is a commented line there and gets its value here.
NONE_DEFAULTS: dict[tuple[str, str], Any] = {
    ("preshow", "teasers"): None,   # cover's side boxes; false = one story fills it; None = as [thumbnail]
}


def _load_defaults() -> dict[str, dict[str, Any]]:
    data = tomllib.loads(Path(__file__).with_name("defaults.toml").read_text(encoding="utf-8"))
    for (section, key), value in NONE_DEFAULTS.items():
        data[section][key] = value
    return data


DEFAULTS: dict[str, dict[str, Any]] = _load_defaults()


def _coerce(raw: str, like: Any, name: str = "") -> Any:
    """An env var's text as the default's type. Lists take "a,b" or "a b" (or JSON); tables
    take JSON (#77 L9)."""
    try:
        if isinstance(like, bool):
            return raw.lower() in ("1", "true", "yes", "on")
        if isinstance(like, int):
            return int(raw)
        if isinstance(like, float):
            return float(raw)
        if isinstance(like, list):
            raw = raw.strip()
            return json.loads(raw) if raw.startswith("[") else raw.replace(",", " ").split()
        if isinstance(like, dict):
            return json.loads(raw)
        if like is None and raw.strip().lower() in ("true", "false"):  # e.g. [preshow] teasers (unset = inherit)
            return raw.strip().lower() == "true"
    except ValueError as e:
        raise ClipperError(f"{name or 'environment variable'}={raw!r}: not a {type(like).__name__} ({e})") from None
    return raw


def _stream_ids(root: Path) -> list[str]:
    return sorted(n.name for n in root.iterdir() if not n.name.startswith(".") and n.is_dir()) \
        if root.is_dir() else []


def stream_dir(cfg: dict, ref: str) -> Path:
    """A stream id under paths.streams, or a stream directory. One that doesn't exist is an
    error here, not a traceback three calls later (or a base config labelled as the stream).
    Exact only: the shortcuts are find_stream's, for what a person types."""
    p = Path(ref)
    if p.is_dir():
        return p
    root: Path = cfg["paths"]["streams"]
    d = root / ref
    if d.is_dir():
        return d
    hint = did_you_mean(ref, _stream_ids(root))
    raise ClipperError(f"no stream {ref!r} in {root}"
                       + (f"; {hint}" if hint else " (clipper worker --status lists them)"))


def find_stream(cfg: dict, ref: str) -> Path:
    """stream_dir plus the shortcuts a person types on the command line (#205): `latest` (the
    newest finished stream), `latest:<show>` (that show's), or any unique start of an id
    (`kick-1373`). Services keep to stream_dir, so an id they hold never matches another."""
    root: Path = cfg["paths"]["streams"]
    if Path(ref).is_dir() or (root / ref).is_dir():
        return stream_dir(cfg, ref)
    if ref == "latest" or ref.startswith("latest:"):
        return latest_stream(cfg, ref.partition(":")[2])
    hits = [i for i in _stream_ids(root) if i.startswith(ref)]
    if len(hits) == 1:
        return root / hits[0]
    if hits:
        more = f" and {len(hits) - 5} more" if len(hits) > 5 else ""
        raise ClipperError(f"{ref!r} starts {len(hits)} stream ids ({', '.join(hits[:5])}{more}): give more of it")
    return stream_dir(cfg, ref)  # its error, with the nearest ids


def latest_stream(cfg: dict, show: str = "") -> Path:
    """The stream with the newest started_at in its meta.json (a recording still going has
    none yet), of this show if one is named."""
    root: Path = cfg["paths"]["streams"]
    if show and show not in _root(cfg)["shows"]:
        raise ClipperError(f"latest:{show}: no [shows.{show}] (shows: {', '.join(_root(cfg)['shows']) or 'none'})")
    from datetime import UTC, datetime

    best: tuple[datetime, Path] | None = None
    for m in root.glob("*/meta.json") if root.is_dir() else []:
        try:
            meta = jsonstate.load(m)
            started = datetime.fromisoformat(meta.get("started_at") or "")
        except (OSError, ValueError, TypeError):  # unreadable, or no start (yet)
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if show and show_of_channel(cfg, meta.get("channel", "")) != show:
            continue
        if best is None or started > best[0]:
            best = (started, m.parent)
    if best is None:
        raise ClipperError(f"no finished stream{f' of {show}' if show else ''} in {root}")
    return best[1]


# Settings that take one of a fixed set of values. Anything else would quietly do the wrong
# thing (B7: [bleep] mode = "Bleep" meant no bleeping), so loading refuses it.
CHOICES = {
    ("bleep", "mode"): ("off", "bleep", "mute"),
    ("cut", "backend"): ("local", "nas"),
    ("transcribe", "backend"): ("whisperx", "mlx", "modal"),
    ("transcribe", "modal_audio"): ("flac", "opus"),
    ("thumbnail", "style"): ("classic", "enquirer"),
    ("shorts", "window"): ("short", "clip"),
    ("schedule", "clip_order"): ("score", "stream"),
    ("preshow", "publish"): ("asap", "slots"),
    ("record", "container"): ("mkv", "ts"),
    ("rumble", "visibility"): ("public", "unlisted"),
    ("rumble", "schedule"): ("youtube", "now"),
    ("rumble", "link"): ("hardlink", "copy"),
    ("perform", "lipsync_engine"): ("latentsync", "sync"),
    ("scoring", "third_party_audio"): ("reject", "allow_reactions"),
    ("scoring", "audience_knowledge"): ("strangers", "fans"),
    ("scoring", "gambling"): ("reject", "commentary"),
}
# CLIPPER_* variables that aren't settings.
SPECIAL_ENV = {"CLIPPER_CONFIG", "CLIPPER_LOCAL_CONFIG", "CLIPPER_BOOTSTRAP", "CLIPPER_ALERT_WEBHOOK",
               "CLIPPER_DATA", "CLIPPER_TOML",  # these two: the NAS recorder's deploy (deploy/record)
               "CLIPPER_REPO"}  # the claude container's clone URL (deploy/claude)
SHOW_KEYS = ("channel", "channels")  # a [shows.<key>] table's own settings; the rest are sections


# Every *effort* key (clipper/llm.py maps them per provider); "none" turns reasoning off on
# other providers and is Claude's "low".
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


def check_choices(cfg: dict, where: str = "") -> None:
    bad = [f"[{s}] {k} = {cfg[s][k]!r} (one of: {', '.join(c)})"
           for (s, k), c in CHOICES.items() if s in cfg and cfg[s].get(k) not in c]
    bad += [f"[{s}] {k} = {v!r} (one of: {', '.join(EFFORTS)})"
            for s, sec in _sections(cfg) for k, v in sec.items()
            if "effort" in k and v not in EFFORTS]  # any type: false or 3 is as wrong as "med"
    if bad:
        raise ClipperError(f"{where}not a valid choice: " + "; ".join(bad))


def problems(cfg: dict) -> list[str]:
    """What load() warns about: settings clipper.toml or [shows.*] name that clipper doesn't
    have (a typo, or a key since renamed), and CLIPPER_* variables that match no setting."""
    root = _root(cfg)
    out = list(root.get("_unknown", []))
    names = {f"CLIPPER_{s}_{k}".upper() for s, v in _sections(root) for k in v}
    out += [f"environment variable {n} matches no setting" for n in sorted(os.environ)
            if n.startswith("CLIPPER_") and n not in names and n not in SPECIAL_ENV]
    # [notify] times: a typo here used to kill the bot's DM loop quietly, every minute.
    for key, pat, want in (("quiet_hours", _HM + "-" + _HM, "HH:MM-HH:MM"), ("digest_time", _HM, "HH:MM")):
        v = str((root.get("notify") or {}).get(key, "") or "").strip()
        if v and not re.fullmatch(pat, v):
            out.append(f"[notify] {key} = {v!r}: {want} (or empty)")
    # [schedule] budget_hours divides in every replan: the base value and each show's override.
    budgets = [("[schedule]", (root.get("schedule") or {}).get("budget_hours", 1))]
    budgets += [(f"[shows.{k}.schedule]", s["overrides"]["schedule"]["budget_hours"])
                for k, s in (root.get("shows") or {}).items()
                if "budget_hours" in (s.get("overrides") or {}).get("schedule", {})]
    out += [f"{where} budget_hours = {v!r} must be a positive number (it divides in every replan)"
            for where, v in budgets if not (isinstance(v, int | float) and v > 0)]
    out += [f"[warroom] hide_shows: no [shows.{k}]" for k in (root.get("warroom") or {}).get("hide_shows", [])
            if k not in (root.get("shows") or {})]
    return out


_HM = r"(?:[01]?\d|2[0-3]):[0-5]\d"


# Keys of a loaded config that aren't [section] tables: "shows" (the [shows.<key>]
# profiles), "show" (which one this config is resolved for) and private bookkeeping.
NOT_SECTIONS = ("shows", "show")


def _sections(cfg: dict):
    return ((s, v) for s, v in cfg.items() if s not in NOT_SECTIONS and not s.startswith("_"))


def _apply_env(cfg: dict, only: set[str] | None = None, local: dict | None = None) -> None:
    """This machine's values over cfg: clipper.local.toml (`local`, as loaded), then env vars."""
    for section, values in _sections(cfg):
        if only is not None and section not in only:
            continue
        values.update(copy.deepcopy((local or {}).get(section, {})))
        for key, value in values.items():
            name = f"CLIPPER_{section}_{key}".upper()
            env = os.environ.get(name)
            if env is not None:
                values[key] = _coerce(env, value, name)


OPTIONAL_PATHS = {"prompts"}    # "" (unset) resolves to None, never to a folder


def _resolve_paths(paths: dict, base: Path, keys=None) -> None:
    for key in keys if keys is not None else list(paths):
        if key in OPTIONAL_PATHS and not paths[key]:
            paths[key] = None
            continue
        p = Path(paths[key]).expanduser()
        paths[key] = p if p.is_absolute() else base / p


def _resolve_photos(cfg: dict, base: Path) -> None:
    """[thumbnail] photos, a folder outside [paths]: relative to the config file like [paths],
    not to wherever the command runs ("" stays unset). Kept a str."""
    d = (cfg.get("thumbnail") or {}).get("photos")
    if d and not Path(d).expanduser().is_absolute():
        cfg["thumbnail"]["photos"] = str(base / Path(d).expanduser())


LOCAL_CONFIG = "clipper.local.toml"


def local_path(cfg_path: str | Path | None = None) -> Path:
    """This machine's clipper.local.toml: $CLIPPER_LOCAL_CONFIG (or the user's saved one, as for
    CLIPPER_CONFIG), else beside the config file."""
    if os.environ.get("CLIPPER_LOCAL_CONFIG") or user_env("CLIPPER_LOCAL_CONFIG"):
        return Path(os.environ.get("CLIPPER_LOCAL_CONFIG") or user_env("CLIPPER_LOCAL_CONFIG"))
    return find(cfg_path).resolve().parent / LOCAL_CONFIG


def _load_local(path: Path, cfg: dict, base: Path) -> dict:
    """clipper.local.toml as {section: {key: value}}, [paths] resolved; unknown keys refused."""
    if not path.exists():
        return {}
    local = tomllib.loads(path.read_text(encoding="utf-8"))
    for section, values in local.items():
        if section in NOT_SECTIONS or not isinstance(values, dict):
            raise ClipperError(f"{path}: only this machine's [section] settings go here, not [{section}]")
        unknown = [k for k in values if k not in cfg.get(section, {})]
        if unknown:
            raise ClipperError(f"{path}: unknown setting [{section}] {', '.join(unknown)}")
    if "paths" in local:
        _resolve_paths(local["paths"], base)
    return local


def user_env(name: str) -> str:
    """A Windows user-scope environment variable, read from the registry: agent and service
    shells started without it don't inherit it. "" elsewhere or when unset."""
    if sys.platform != "win32":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def find(path: str | Path | None = None) -> Path:
    """The config file: --config, $CLIPPER_CONFIG, ./clipper.toml, the user's CLIPPER_CONFIG."""
    if path or os.environ.get("CLIPPER_CONFIG"):
        return Path(path or os.environ["CLIPPER_CONFIG"])
    here = Path("clipper.toml")
    return here if here.exists() else Path(user_env("CLIPPER_CONFIG") or here)


def load(path: str | Path | None = None, use_local: bool = True) -> dict[str, dict[str, Any]]:
    cfg = copy.deepcopy(DEFAULTS)
    unknown: list[str] = []
    path = find(path)
    base = Path.cwd()
    shows: dict[str, dict] = {}
    if path.exists():
        base = path.resolve().parent
        with open(path, "rb") as f:
            data = tomllib.load(f)
        shows = data.pop("shows", {})
        loose = [k for k, v in data.items() if not isinstance(v, dict)]  # a key above any [section]
        unknown += [f"{path.name}: {k} = {data[k]!r} is outside any [section]" for k in loose]
        for k in loose:
            data.pop(k)
        if not isinstance(shows, dict) or any(not isinstance(s, dict) for s in shows.values()):
            raise ClipperError(f"{path.name}: [shows] holds [shows.<key>] tables only")
        unknown += _unknown(data, f"{path.name}: ")
        for key, show in shows.items():
            unknown += _unknown({s: v for s, v in show.items() if isinstance(v, dict)},
                                f"{path.name} [shows.{key}]: ")
            unknown += [f"{path.name} [shows.{key}]: unknown setting {s} (a show has {' and '.join(SHOW_KEYS)})"
                        for s, v in show.items() if not isinstance(v, dict) and s not in SHOW_KEYS]
        for section, values in data.items():
            cfg.setdefault(section, {}).update(values)
    lpath = local_path(path)
    local = _load_local(lpath, cfg, base) if use_local else {}
    _apply_env(cfg, local=local)
    _resolve_paths(cfg["paths"], base)
    _resolve_photos(cfg, base)

    cfg["shows"] = {}
    for key, show in shows.items():
        chans = show.get("channels") or ([show["channel"]] if show.get("channel") else [])
        cfg["shows"][key] = {"channels": [c.lower() for c in chans],
                             "overrides": {s: v for s, v in show.items()
                                           if isinstance(v, dict)}}
    cfg["show"] = {"key": "", "channel": cfg["kick"]["channel"]}
    cfg["_dir"] = base
    cfg["_file"] = path.resolve() if path.exists() else None
    cfg["_path"] = path.resolve()  # where it is, or would be (`clipper setup` writes it)
    cfg["_local"] = local
    cfg["_local_file"] = lpath if local else None
    cfg["_resolved"] = {}
    cfg["_unknown"] = unknown
    for msg in problems(cfg):  # a typo shouldn't stop the bot; `clipper doctor` fails on these
        print(f"warning: {msg}", file=sys.stderr)
    check_choices(cfg, f"{path.name}: ")
    return cfg


def _unknown(data: dict, where: str) -> list[str]:
    out = []
    for section, values in data.items():
        if section not in DEFAULTS:
            out.append(f"{where}unknown section [{section}]")
        elif isinstance(values, dict):
            out += [f"{where}unknown setting [{section}] {k}" for k in values if k not in DEFAULTS[section]]
    return out


# ---------- shows ----------
# One clipper.toml serves several shows. Each [shows.<key>] table names the show's Kick
# channels and holds sub-tables that override the same sections for that show's streams,
# e.g. [shows.myshow.meta] show_name = "My Show". A stream belongs to the show whose
# channels include the "channel" in its meta.json; with no match it keeps the base settings.

def show_of_channel(cfg: dict, channel: str) -> str:
    channel = (channel or "").lower()
    return next((k for k, s in _root(cfg)["shows"].items() if channel in s["channels"]), "")


_CHANNELS: dict[str, str] = {}   # a stream's channel never changes; meta.json may be on the NAS
STREAM_CONFIG = "config.toml"    # streams/<id>/config.toml: that stream's own overrides


def stream_channel(sdir: Path) -> str:
    key = str(sdir)
    if key in _CHANNELS:
        return _CHANNELS[key]
    channel = ""
    # live/state.json: a stream the NAS recorder is still on has no meta.json yet
    for p in (sdir / "meta.json", sdir / "live" / "state.json"):
        try:
            channel = jsonstate.load(p).get("channel") or ""
        except (OSError, ValueError):
            continue
        break
    if channel:
        _CHANNELS[key] = channel
    return channel


def base(cfg: dict) -> dict:
    """The config as loaded, before any show or stream override (for_show and for_stream keep
    it under "_root"): for settings that are about the whole install, [shows] included."""
    return cfg.get("_root") or cfg


_root = base   # the old name; drop it after a release


def for_show(cfg: dict, key: str, channel: str = "") -> dict:
    """The config with show `key`'s overrides applied (env vars still win). Cached and
    safe to call on an already resolved config; "" gives the base config."""
    root = _root(cfg)
    if not key:
        if not channel or channel == root["show"]["channel"]:
            return root
    elif key not in root["shows"]:
        raise ClipperError(f"no [shows.{key}] in the config; shows: {', '.join(root['shows'])}")
    cache_key = (key, channel)
    if cache_key in root["_resolved"]:
        return root["_resolved"][cache_key]
    out = {k: copy.deepcopy(v) for k, v in root.items() if not k.startswith("_")}
    overrides = root["shows"][key]["overrides"] if key else {}
    for section, values in overrides.items():
        out.setdefault(section, {}).update(copy.deepcopy(values))
    _apply_env(out, set(overrides), root["_local"])
    if "paths" in overrides:
        _resolve_paths(out["paths"], root["_dir"], list(overrides["paths"]))
    _resolve_photos(out, root["_dir"])  # a show's own, or an env var re-applied over it
    if key and not channel:
        channel = (root["shows"][key]["channels"] or [""])[0]
    out["kick"]["channel"] = channel or out["kick"]["channel"]
    out["show"] = {"key": key, "channel": out["kick"]["channel"]}
    out["_root"] = root
    check_choices(out, f"[shows.{key}]: " if key else "")
    root["_resolved"][cache_key] = out
    return out


def for_stream(cfg: dict, sdir: Path) -> dict:
    """The config for this stream's show, picked by the Kick channel in its meta.json, then
    the stream's own streams/<id>/config.toml if it has one (a one-off night: more picks, a
    big story in stream_context, schedule priority). Env vars still win. A config from
    pin_show (`clipper --show <key>`) keeps that show instead of the channel's (B4)."""
    res = _for_stream(cfg, Path(sdir))
    return {**res, "_pinned": True} if cfg.get("_pinned") else res


def explain(cfg: dict, name: str, sdir: Path | None = None) -> dict:
    """Where "section.key" comes from: every layer that sets it, in the order they apply
    (default < clipper.toml < show < stream config.toml < clipper.local.toml < env), and the
    value that wins, for this stream (or the config's show, or the base config)."""
    section, _, key = name.partition(".")
    if section not in DEFAULTS or key not in DEFAULTS[section]:
        hint = did_you_mean(name, [f"{s}.{k}" for s, v in DEFAULTS.items() for k in v], 4, 0.75)
        raise ClipperError(f"no setting {name!r}" + (f"; {hint}" if hint else ""))
    root = _root(cfg)
    resolved = for_stream(cfg, sdir) if sdir else cfg
    show = resolved["show"]["key"]
    layers = [("default", DEFAULTS[section][key])]
    if root.get("_file"):
        data = tomllib.loads(Path(root["_file"]).read_text(encoding="utf-8"))
        if key in data.get(section, {}):
            layers.append((root["_file"].name, data[section][key]))
    if show and key in root["shows"][show]["overrides"].get(section, {}):
        layers.append((f"[shows.{show}]", root["shows"][show]["overrides"][section][key]))
    if sdir and (Path(sdir) / STREAM_CONFIG).exists():
        data = tomllib.loads((Path(sdir) / STREAM_CONFIG).read_text(encoding="utf-8"))
        if key in data.get(section, {}):
            layers.append((str(Path(sdir) / STREAM_CONFIG), data[section][key]))
    if key in root["_local"].get(section, {}):
        layers.append((LOCAL_CONFIG, root["_local"][section][key]))
    env = f"CLIPPER_{section}_{key}".upper()
    if env in os.environ:
        layers.append((f"${env}", os.environ[env]))
    return {"name": name, "value": resolved[section][key], "show": show, "layers": layers}


def diff(cfg: dict, show: str = "") -> list[tuple[str, str, Any, Any]]:
    """What this install changes from defaults.toml (#205), as (layer, "section.key", default,
    value) in the order the layers apply: clipper.toml, each show's table (only `show`'s when
    one is named), clipper.local.toml, then CLIPPER_* env vars. A key set to its default isn't
    listed; nor are a show's channels or the [shows] tables themselves (`clipper shows` lists those)."""
    root = _root(cfg)
    if show and show not in root["shows"]:
        raise ClipperError(f"no [shows.{show}] (shows: {', '.join(root['shows']) or 'none'})")
    layers: list[tuple[str, dict]] = []
    if root.get("_file"):
        layers.append((Path(root["_file"]).name,
                       tomllib.loads(Path(root["_file"]).read_text(encoding="utf-8"))))
    layers += [(f"[shows.{k}]", s["overrides"]) for k, s in root["shows"].items() if not show or k == show]
    layers.append((LOCAL_CONFIG, root["_local"]))
    env: dict[str, dict] = {}
    for section, keys in DEFAULTS.items():
        for key, like in keys.items():
            raw = os.environ.get(f"CLIPPER_{section}_{key}".upper())
            if raw is not None:
                env.setdefault(section, {})[key] = _coerce(raw, like, f"CLIPPER_{section}_{key}".upper())
    layers.append(("environment", env))
    out = []
    for layer, data in layers:
        for section, keys in data.items():
            if section not in DEFAULTS or not isinstance(keys, dict):
                continue
            out += [(layer, f"{section}.{key}", DEFAULTS[section][key], value) for key, value in keys.items()
                    if key in DEFAULTS[section] and value != DEFAULTS[section][key]]
    return out


def show_args(cfg: dict) -> list[str]:
    """`--show <key>` for a clipper child process when this config is pinned to a show."""
    return ["--show", cfg["show"]["key"]] if cfg.get("_pinned") else []


def pin_show(cfg: dict, key: str) -> dict:
    """Show `key`'s config, kept by for_stream for any stream (`clipper --show <key>`)."""
    return {**for_show(cfg, key), "_pinned": True}


def _for_stream(cfg: dict, sdir: Path) -> dict:
    channel = stream_channel(sdir)
    key = cfg["show"]["key"] if cfg.get("_pinned") else show_of_channel(cfg, channel)
    out = for_show(cfg, key, channel)
    path = sdir / STREAM_CONFIG
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return out
    root = _root(cfg)
    cache_key = ("stream", str(sdir), key, channel, mtime)
    if cache_key in root["_resolved"]:
        return root["_resolved"][cache_key]
    overrides = tomllib.loads(path.read_text(encoding="utf-8"))
    res = {k: copy.deepcopy(v) for k, v in out.items() if not k.startswith("_")}
    for section, values in overrides.items():
        unknown = [k for k in values if k not in res.get(section, {})] if isinstance(values, dict) else [""]
        if unknown:
            raise ClipperError(f"{path}: unknown setting [{section}] {', '.join(unknown)}")
        res[section].update(copy.deepcopy(values))
    _apply_env(res, set(overrides), root["_local"])
    _resolve_photos(res, root["_dir"])  # the stream's own, or local/env re-applied over it
    res["_root"] = root
    check_choices(res, f"{path}: ")
    root["_resolved"][cache_key] = res
    return res
