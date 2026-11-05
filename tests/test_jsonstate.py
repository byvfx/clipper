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
