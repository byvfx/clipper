import argparse
import tomllib

import pytest
from conftest import ROOT

from clipper import __version__, cli, stages
from clipper.errors import ClipperError


def commands() -> dict[str, argparse.ArgumentParser]:
    sub = argparse.ArgumentParser().add_subparsers()
    for add in (cli.add_pipeline, cli.add_review, cli.add_services, cli.add_extras, cli.add_setup):
        add(sub)
    return sub.choices


def test_every_command_is_in_one_group():
    """`clipper -h` lists commands by COMMAND_GROUPS: a new command goes in one."""
    grouped = [n for _, names in cli.COMMAND_GROUPS for n in names]
    assert sorted(grouped) == sorted(commands())
    assert len(grouped) == len(set(grouped))


def test_every_stage_runs_a_real_command():
    cmds = commands()
    wc = {"fast_lane": True, "notify": False}
    for st in stages.STAGES:
        argv = stages.argv(st.name, "kick-1", wc)
        parser = cmds[argv[0]]
        parser.parse_args(argv[1:])   # the worker's argv parses (exits on an unknown flag)


def parse(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(argv)


def test_one_items_flag_old_spellings_still_work():
    assert parse("cut", "kick-1", "--items", "s03,s07").items == ["s03", "s07"]
    assert parse("cut", "kick-1", "--ids", "s03", "--items", "s07").items == ["s03", "s07"]
    assert parse("review", "notify", "kick-1", "--keys", "c01").items == ["c01"]
    assert parse("retake", "--item", "c03").items == ["c03"]
    assert parse("receipts", "find", "kick-1", "--segments", "s02,s05").items == ["s02", "s05"]
    assert parse("archive", "check", "jer", "--ids", "yt-a", "yt-b,yt-c").items == ["yt-a", "yt-b", "yt-c"]
    a = parse("tiktok", "--items", "t20", "kick-1")   # one value: the stream positional survives
    assert a.items == ["t20"] and a.stream == "kick-1"
    assert parse("thumbs", "kick-1").items is None


def test_flags_mean_the_same_everywhere():
    assert parse("shorts", "kick-1", "--save-example", "cams", "--at", "1:02:03").at == 3723
    assert parse("shorts", "kick-1", "--save-example", "cams", "--at", "90.5").at == 90.5
    assert parse("upload", "--max", "3").limit == 3 == parse("upload", "--limit", "3").limit
    assert parse("review", "status").sync is False   # a status never writes (a sync can post to Kitsu)


@pytest.mark.parametrize("argv", [
    ("upload", "--links", "--next", "2"), ("upload", "--sign-in", "--thumbnails"),
    ("rumble", "--status", "--done", "s02"), ("tiktok", "--auto", "--items", "t20"),
    ("x", "--auto", "--items", "t20"),
    ("schedule", "--show"), ("analytics", "--show"),   # clashed with the global --show KEY
    ("shorts", "kick-1", "--at", "soon")])
def test_refused_on_the_command_line(argv, capsys):
    with pytest.raises(SystemExit):
        parse(*argv)


def test_mode_flags_that_dont_combine():
    a = parse("thumbs", "kick-1", "--shorts", "--items", "s02")
    with pytest.raises(ClipperError, match="--shorts doesn't combine with --items"):
        cli.refuse(a, "--shorts", "--items", "--compose-only", "--missing", "--force")
    cli.refuse(parse("thumbs", "kick-1", "--shorts"), "--shorts", "--items", "--missing")
    a = parse("thumbs", "kick-1", "--photo", "", "--items", "s02", "--force")
    with pytest.raises(ClipperError, match="--force"):
        cli.refuse(a, "--photo", "--force")


def test_version_is_one_number(capsys):
    """`clipper --version` prints the package version; __init__.py and pyproject.toml agree."""
    with pytest.raises(SystemExit) as e:
        parse("--version")
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"clipper {__version__}"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__
