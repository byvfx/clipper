import os
import shlex

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


@pytest.mark.skipif(os.name == "nt", reason="shell quoting is POSIX only")
def test_shell_quoted_path_with_apostrophe(tmp_path):
    toml = tmp_path / "O'Brien Downloads" / "clipper.toml"
    toml.parent.mkdir()
    toml.write_text("", encoding="utf-8")
    for typed in (shlex.quote(str(toml)), str(toml).replace("'", "\\'").replace(" ", "\\ "), str(toml)):
        assert setup.config_file(typed) == toml, typed


def test_missing_config_names_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "nope" / "clipper.toml"
    assert setup.config_file_ok(str(missing)) == f"no file at {missing}"
    err = setup.config_file_ok("Downloads/clipper.toml")
    assert str(tmp_path.resolve()) in err and "~/" in err


def test_unknown_user_in_home_path_is_a_message(tmp_path):
    err = setup.config_file_ok("~no_such_user_clipper/clipper.toml")
    assert err and "~/" in err


def test_join_uses_a_shared_config_in_place(tmp_path, monkeypatch):
    from clipper import config

    home, share, checkout = tmp_path / "home", tmp_path / "share", tmp_path / "home" / "clipper"
    for d in (share, checkout):
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (share / "clipper.toml").write_text("", encoding="utf-8")
    saved = {}
    monkeypatch.setattr(setup, "set_user_env", lambda k, v: (saved.__setitem__(k, v), "the profile")[1])
    cfg = config.load(checkout / "clipper.toml", use_local=False)
    st = {"done": {}}
    setup.step_mode(cfg, st, setup.Ask({"mode": "join", "from": str(share)}))  # in_place defaults on
    assert saved == {"CLIPPER_CONFIG": str((share / "clipper.toml").resolve()),
                     "CLIPPER_LOCAL_CONFIG": str(checkout / "clipper.local.toml")}
    assert not (checkout / "clipper.toml").exists()

    monkeypatch.setenv("CLIPPER_LOCAL_CONFIG", saved["CLIPPER_LOCAL_CONFIG"])
    monkeypatch.setenv("CLIPPER_CONFIG", saved["CLIPPER_CONFIG"])
    assert setup.state_path(config.load()) == checkout / setup.STATE  # setup's state stays off the share


def test_join_copies_a_downloaded_config(tmp_path, monkeypatch):
    from clipper import config

    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "clipper").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "Downloads" / "clipper.toml").write_text("# shared\n", encoding="utf-8")
    monkeypatch.setattr(setup, "set_user_env", lambda k, v: pytest.fail("no env for a copy"))
    cfg = config.load(home / "clipper" / "clipper.toml", use_local=False)
    setup.step_mode(cfg, {"done": {}}, setup.Ask({"mode": "join", "from": "~/Downloads/clipper.toml"}))
    assert (home / "clipper" / "clipper.toml").read_text(encoding="utf-8") == "# shared\n"
