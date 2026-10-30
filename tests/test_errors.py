"""Library code raises ClipperError; only cli.main turns it into an exit."""
import re
import sys

import pytest
from conftest import ROOT

from clipper import cli, setup
from clipper.errors import ClipperError


def run_cli(monkeypatch, tmp_path, *argv):
    monkeypatch.setattr(sys, "argv", ["clipper", "--config", str(tmp_path / "clipper.toml"), *argv])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


def test_clipper_error_exits_1_with_its_message(tmp_path, monkeypatch, capsys):
    assert run_cli(monkeypatch, tmp_path, "--show", "nope", "shows") == 1
    assert "no [shows.nope]" in capsys.readouterr().err


def test_exit_status_passes_through(tmp_path, monkeypatch, capsys):
    """setup's EXIT_SYNC (3) tells deploy/bootstrap to install packages and rerun setup;
    doctor and setup return 0 on success."""
    def needs_sync(*a, **k):
        raise ClipperError(status=setup.EXIT_SYNC)
    monkeypatch.setattr(setup, "main", needs_sync)
    assert run_cli(monkeypatch, tmp_path, "setup") == 3
    assert capsys.readouterr().err == ""

    monkeypatch.setattr(setup, "main", lambda *a, **k: 0)
    assert run_cli(monkeypatch, tmp_path, "setup") == 0


def test_library_raises_no_system_exit():
    """`raise SystemExit` outside cli.py stops the bot or the war room instead of failing one
    action. cli.py is the boundary: it exits with a status."""
    found = []
    for path in sorted((ROOT / "clipper").rglob("*.py")):
        if path.name == "cli.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"raise SystemExit\b|sys\.exit\(", line):
                found.append(f"{path.name}:{n}")
    assert not found, "raise ClipperError instead: " + ", ".join(found)


def test_no_status_wrapped_as_a_message():
    """ClipperError's first argument is a message; an exit status goes in status=."""
    found = []
    for path in sorted((ROOT / "clipper").rglob("*.py")):
        if path.name == "errors.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"ClipperError\(([^)]*)", line)
            if m and m.group(1) and not re.match(r"""\s*(f?["']|\(|str\(|status=|$)""", m.group(1)):
                found.append(f"{path.name}:{n}")
    assert not found, ", ".join(found)
