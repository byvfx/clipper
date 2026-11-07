import os

import pytest

from clipper import setup


def test_join_config_path_as_people_type_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    folder = tmp_path / "My Downloads"
    folder.mkdir()
    toml = folder / "clipper.toml"
    toml.write_text("", encoding="utf-8")
    for typed in ("~/My Downloads/clipper.toml", f"'{toml}'", f'"{toml}"', f"  {toml}  ", str(folder)):
        assert setup.config_file_ok(typed) is None, typed
        assert setup.config_file(typed) == toml, typed


@pytest.mark.skipif(os.name == "nt", reason="backslash is the separator on Windows")
def test_dragged_in_path_with_escaped_spaces(tmp_path):
    toml = tmp_path / "My Downloads" / "clipper.toml"
    toml.parent.mkdir()
    toml.write_text("", encoding="utf-8")
    dragged = str(toml).replace(" ", "\\ ") + " "
    assert setup.config_file(dragged) == toml


def test_missing_config_names_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert setup.config_file_ok("/nope/clipper.toml") == f"no file at {os.path.normpath('/nope/clipper.toml')}"
    err = setup.config_file_ok("Downloads/clipper.toml")
    assert str(tmp_path.resolve()) in err and "~/" in err
