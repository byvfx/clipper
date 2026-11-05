"""Shared JSON state files (schedule.json, review.json, review_state.json, thumbnails.json,
discord.json, ...): written whole or not at all, and changed under a lock that other
processes respect.

The bot, its hourly `clipper upload` child, the war room, the worker and a CLI run all read
and write these files, some over the NAS share. `write` goes through a temp file and
os.replace, so a reader sees the old file or the new one, never half of one. `update` holds
`<file>.lock` (an O_EXCL file, so it works across processes and machines) around a re-read,
your change and the write, so two writers can't drop each other's changes.

    with jsonstate.update(path, {}) as data:
        data["key"] = value

Keep the locked part short (no Kitsu or YouTube calls inside): other writers wait for it."""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

STALE_S = 120  # a lock older than this was left by a killed process
WAIT_S = 60

_held: dict[str, list] = {}  # lock path -> [RLock, depth] for this process
_held_guard = threading.Lock()


def read(path: Path, default: Any = None) -> Any:
    """The file's JSON, or `default` if it doesn't exist (a copy, if it's a dict or list).
    A file that exists but isn't JSON still raises: that's worth knowing about."""
    try:
        return load(path)
    except FileNotFoundError:
        return json.loads(json.dumps(default)) if default is not None else None


def load(path: Path) -> Any:
    """The file's JSON; FileNotFoundError if it doesn't exist. Use this (or `read`) for every
    JSON file rather than json.loads(path.read_text()): a file another process is replacing
    is retried instead of failing."""
    for attempt in range(10):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except PermissionError:  # a writer is replacing it (Windows, the NAS share)
            if attempt == 9:
                raise
            time.sleep(0.2 * (attempt + 1))
    return json.loads(text)


def write(path: Path, data: Any, indent: int | None = 2) -> None:
    """Write JSON via a temp file beside it and os.replace."""
    write_text(path, json.dumps(data, indent=indent, ensure_ascii=False))


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a reader has it open (Windows, the NAS share)
            if attempt == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2 * (attempt + 1))


def _local(lock_path: Path) -> list:
    with _held_guard:
        return _held.setdefault(str(lock_path.resolve()), [threading.RLock(), 0])


@contextmanager
def locked(path: Path, wait_s: float = WAIT_S) -> Iterator[None]:
    """Hold `<path>.lock`. Re-entrant within a thread; other threads and processes wait."""
    lock_path = path.with_name(path.name + ".lock")
    local = _local(lock_path)
    local[0].acquire()
    try:
        if local[1] == 0:
            _acquire_file(lock_path, wait_s)
        local[1] += 1
        try:
            yield
        finally:
            local[1] -= 1
            if local[1] == 0:
                _release_file(lock_path)
    finally:
        local[0].release()


def _release_file(lock_path: Path) -> None:
    if not _unlink(lock_path, 10):
        # Still busy: keep trying from a thread (exit waits for it, this caller doesn't), so a
        # waiter gets the lock as soon as it can be deleted. ~60 s, under STALE_S, so nobody
        # can have reclaimed it and made a new one by then.
        threading.Thread(target=_unlink, args=(lock_path, 120), name=f"release {lock_path.name}",
                         daemon=False).start()


def _unlink(lock_path: Path, tries: int) -> bool:
    for attempt in range(tries):
        try:
            lock_path.unlink(missing_ok=True)
            return True
        except PermissionError:  # another process is looking at it (Windows, the NAS share)
            time.sleep(min(0.1 * (attempt + 1), 0.5))
    return False


def _acquire_file(lock_path: Path, wait_s: float) -> None:
    deadline = time.monotonic() + wait_s
    delay = 0.05
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError) as e:
            # PermissionError: on Windows and the NAS share a lock that is being deleted
            # (its holder just let go) refuses opens and stats until it is gone. Wait for it.
            denied = e if isinstance(e, PermissionError) else None
            try:
                if time.time() - lock_path.stat().st_mtime > STALE_S:
                    lock_path.unlink(missing_ok=True)  # its owner died holding it
                    continue
            except FileNotFoundError:
                if not denied:
                    continue
                # no lock, yet creating one is refused: maybe it just went, maybe the folder
                # isn't writable. Back off, and past the deadline say which.
                if time.monotonic() > deadline:
                    raise denied
            except PermissionError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock_path} is held by another process for over {wait_s:.0f} s "
                                   f"(delete it if nothing is running)") from None
            time.sleep(delay)
            delay = min(delay * 2, 0.5)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{socket.gethostname()} pid {os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        return


def merge(path: Path, data: dict, keys, indent: int | None = 2) -> dict:
    """Write only `keys` of `data` into a dict-shaped file, re-read under its lock, so keys
    another writer changed meanwhile stay as it left them (a key missing from `data` is
    removed). Returns what was written."""
    with locked(path):
        fresh = read(path, {})
        for k in keys:
            if k in data:
                fresh[k] = data[k]
            else:
                fresh.pop(k, None)
        write(path, fresh, indent)
        return fresh


@contextmanager
def update(path: Path, default: Any = None, indent: int | None = 2) -> Iterator[Any]:
    """Re-read the file under its lock, yield the data to change in place, write it back.
    Nothing is written if the block raises."""
    with locked(path):
        data = read(path, default)
        yield data
        write(path, data, indent)
