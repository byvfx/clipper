import pytest

from clipper import jsonstate


def test_load_and_read(tmp_path):
    p = tmp_path / "x.json"
    with pytest.raises(FileNotFoundError):
        jsonstate.load(p)
    default = {"a": []}
    got = jsonstate.read(p, default)
    assert got == default and got is not default   # a copy, safe to change
    assert jsonstate.read(p) is None

    jsonstate.write(p, {"k": 1})
    assert jsonstate.load(p) == jsonstate.read(p, {}) == {"k": 1}


def test_update_rereads_under_the_lock(tmp_path):
    p = tmp_path / "s.json"
    jsonstate.write(p, {"a": 1})
    with jsonstate.update(p, {}) as data:
        data["b"] = 2
    assert jsonstate.load(p) == {"a": 1, "b": 2}
    assert not list(tmp_path.glob("*.lock")) and not list(tmp_path.glob("*.tmp"))


def test_lock_being_deleted_is_waited_for(tmp_path, monkeypatch):
    # On the NAS share a lock its holder is deleting answers stat with "Access is denied"
    # until it is gone; that is a held lock, not an error.
    p = tmp_path / "review.json"
    lock = tmp_path / "review.json.lock"
    lock.write_text("other pid\n")
    real_stat = type(lock).stat
    calls = []

    def stat(self, *a, **kw):
        if self == lock and not calls:
            calls.append(1)
            lock.unlink()
            raise PermissionError(13, "Access is denied")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(type(lock), "stat", stat)
    with jsonstate.locked(p):
        assert lock.exists()
    assert calls and not lock.exists()


def test_release_retries_a_busy_lock(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    lock = tmp_path / "s.json.lock"
    real_unlink = type(lock).unlink
    fails = [PermissionError(13, "Access is denied")]

    def unlink(self, *a, **kw):
        if self == lock and fails:
            raise fails.pop()
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(type(lock), "unlink", unlink)
    with jsonstate.locked(p):
        pass
    assert not fails and not lock.exists()


def test_release_keeps_trying_in_the_background(tmp_path, monkeypatch):
    # Left behind, a fresh lock would make the next writer time out (60 s) before it counts
    # as stale (120 s), so the releaser keeps at it until it can be deleted.
    import threading
    p = tmp_path / "s.json"
    lock = tmp_path / "s.json.lock"
    real_unlink = type(lock).unlink
    fails = [PermissionError(13, "Access is denied")] * 15

    def unlink(self, *a, **kw):
        if self == lock and fails:
            raise fails.pop()
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(type(lock), "unlink", unlink)
    monkeypatch.setattr(jsonstate.time, "sleep", lambda s: None)
    with jsonstate.locked(p):
        pass
    for t in threading.enumerate():
        if t.name == "release s.json.lock":
            t.join(5)
    assert not fails and not lock.exists()


def test_release_leaves_a_successors_lock(tmp_path):
    # Held past STALE_S, another process may have reclaimed the lock and made its own:
    # releasing must not delete that one.
    p = tmp_path / "s.json"
    lock = tmp_path / "s.json.lock"
    with jsonstate.locked(p):
        lock.unlink()
        lock.write_text("other host pid 1 2026-11-04 00:00:00 abc\n")
    assert lock.read_text() == "other host pid 1 2026-11-04 00:00:00 abc\n"


def test_release_past_the_cutoff_leaves_the_lock(tmp_path, monkeypatch):
    # Held until a waiter may be taking it over as stale, the holder doesn't touch it at all:
    # read-then-delete could otherwise hit the waiter's new lock in between.
    monkeypatch.setattr(jsonstate, "STALE_S", 10)   # cutoff = 10 s before stale = at once
    p = tmp_path / "s.json"
    with jsonstate.locked(p):
        pass
    assert (tmp_path / "s.json.lock").exists()


def test_unwritable_folder_fails_instead_of_spinning(tmp_path, monkeypatch):
    def refuse(*a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(jsonstate.os, "open", refuse)
    with pytest.raises(PermissionError), jsonstate.locked(tmp_path / "s.json", wait_s=0.2):
        pass
