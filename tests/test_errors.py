"""Library code raises ClipperError; only cli.main turns it into an exit."""
import re
import sys

import pytest
from conftest import ROOT

from clipper import cli


def test_cli_turns_clipper_error_into_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["clipper", "--config", str(tmp_path / "clipper.toml"),
                                      "--show", "nope", "shows"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "no [shows.nope]" in str(e.value.code)   # a str code: printed to stderr, status 1


def test_library_raises_no_system_exit():
    """`raise SystemExit` outside cli.py stops the bot or the war room instead of failing one
    action. cli.py may still exit with a status (`raise SystemExit(1)`)."""
    found = []
    for path in sorted((ROOT / "clipper").glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"raise SystemExit\b|sys\.exit\(", line) and not (
                    path.name == "cli.py" and re.search(r"SystemExit\((\d|str\(e\))", line)):
                found.append(f"{path.name}:{n}")
    assert not found, "raise ClipperError instead: " + ", ".join(found)
