"""`clipper backup` (#204): one dated zip of the taste layer, the files months of editorial
judgement live in and the code can't rebuild (HANDOFF "Back up the taste layer").

    clipper backup [--to DIR] [--with-secrets] [--dry-run]

What goes in, each relative to the config file's folder (the NAS share's /data):
    clipper.toml and clipper.local.toml, backups/ (the hand-edit copies; not this folder),
    schedule.json, people.json, logs/costs.jsonl, faces/ (not the downloaded models),
    voices/, layouts/, [thumbnail] photos, [paths] prompts if set, and each stream's
    config.toml, meta.json, review.json, review_state.json, thumbnails.json, retakes.json
secrets/ only with --with-secrets ([backup] with_secrets for serve's run).

Not in it: the recordings and cuts (prune keeps their JSON; the videos are on YouTube), the
transcripts (Modal can redo them), the Kitsu database (its Docker volumes on the NAS: wiki
Backups), and deploy/*/env.local on the machine you deploy from. The zip lands in [backup]
folder as taste-<date>-<time>.zip; the newest [backup] keep stay. Copy them off the NAS now and
then: a backup on the same disk doesn't survive the disk. serve runs it every [backup]
every_days (0 = only by hand); `clipper audit` and doctor say when the newest is old."""
from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path

from .errors import ClipperError

STREAM_FILES = ("config.toml", "meta.json", "review.json", "review_state.json", "thumbnails.json",
                "retakes.json")
PREFIX = "taste-"
SKIP_DIRS = {"models", "__pycache__"}   # faces/models: downloaded again by `clipper faces`


def folder(cfg: dict, to: str | Path | None = None) -> Path:
    """Where backups land: --to, else [backup] folder, relative to the config file's folder."""
    name = to or cfg["backup"]["folder"]
    if not name:
        raise ClipperError("no backup folder: set [backup] folder or pass --to")
    p = Path(name).expanduser()
    return p if p.is_absolute() else Path(cfg["_dir"]) / p


def _walk(top: Path):
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS]
        for n in filenames:
            yield Path(dirpath) / n


def plan(cfg: dict, with_secrets: bool = False, dest: Path | None = None) -> list[tuple[str, Path]]:
    """(name in the zip, file) for everything a backup takes."""
    root = Path(cfg["_dir"])
    paths = cfg["paths"]
    seen: set[Path] = set()
    out: list[tuple[str, Path]] = []
    skip = dest.resolve() if dest else None

    def add(p: Path | str | None) -> None:
        if not p:
            return
        p = Path(p)
        if not p.exists():
            return
        for f in _walk(p) if p.is_dir() else [p]:
            f = f.resolve()
            if f in seen or not f.is_file() or (skip and skip in f.parents):   # never the zips themselves
                continue
            seen.add(f)
            try:
                name = f.relative_to(root.resolve()).as_posix()
            except ValueError:   # outside the config folder: a photos share of its own, say
                name = f"outside/{f.parent.name}/{f.name}" if p.is_file() else \
                    f"outside/{p.name}/{f.relative_to(p.resolve()).as_posix()}"
            out.append((name, f))

    add(cfg.get("_file"))
    add(cfg.get("_local_file"))
    add(root / "backups")
    for key in ("schedule", "people", "costs", "faces", "voices", "layouts"):
        add(paths[key])
    add(paths["prompts"] or None)
    add(cfg["thumbnail"].get("photos") or None)
    streams: Path = paths["streams"]
    if streams.is_dir():
        for d in sorted(streams.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                for name in STREAM_FILES:
                    add(d / name)
    if with_secrets:
        add(root / "secrets")
    return out


def run(cfg: dict, to: str | Path | None = None, with_secrets: bool | None = None,
        dry_run: bool = False) -> list[str]:
    """Write the zip (or, with dry_run, say what would go in it) and drop the old ones."""
    dest = folder(cfg, to)
    secrets = cfg["backup"]["with_secrets"] if with_secrets is None else with_secrets
    files = plan(cfg, secrets, dest)
    if not files:
        raise ClipperError(f"nothing to back up under {cfg['_dir']}")
    size = sum(f.stat().st_size for _, f in files)
    groups: dict[str, list[int]] = {}
    for name, f in files:
        top = name.split("/", 1)[0] if "/" in name else name
        g = groups.setdefault(top, [0, 0])
        g[0] += 1
        g[1] += f.stat().st_size
    lines = [f"  {top:<22} {n:>5} files {_mb(b):>9}" for top, (n, b) in sorted(groups.items())]
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    target = dest / f"{PREFIX}{stamp}.zip"
    if dry_run:
        return [f"would write {target} ({len(files)} files, {_mb(size)} before compression"
                + (", secrets included" if secrets else ", no secrets") + "):", *lines]
    dest.mkdir(parents=True, exist_ok=True)
    for left in dest.glob(f"{PREFIX}*.zip.part"):   # a run killed mid-write
        left.unlink(missing_ok=True)
    tmp = target.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, f in files:
            z.write(f, name)
    tmp.replace(target)
    lines = [f"wrote {target} ({len(files)} files, {_mb(target.stat().st_size)}"
             + (", secrets included" if secrets else ", no secrets") + ")"]
    keep = cfg["backup"]["keep"]
    old = sorted(dest.glob(f"{PREFIX}*.zip"))[:-keep] if keep > 0 else []
    for p in old:
        p.unlink()
    if old:
        lines.append(f"removed {len(old)} older backup{'s' if len(old) != 1 else ''} ([backup] keep = {keep})")
    return lines


def newest(cfg: dict) -> Path | None:
    """The newest backup zip in [backup] folder, if any."""
    try:
        dest = folder(cfg)
    except ClipperError:
        return None
    zips = sorted(dest.glob(f"{PREFIX}*.zip")) if dest.is_dir() else []
    return zips[-1] if zips else None


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"
