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
