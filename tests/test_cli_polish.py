"""#205: stream shortcuts, `config diff`, `--json` on the read commands, shell completion."""
import json

import pytest

from clipper import cli, completion, config, jsonstate
from clipper.errors import ClipperError

TOML = """
[meta]
srt_max_s = 9

[shows.kino]
channels = ["kinocasino"]

[shows.kino.meta]
srt_max_s = 4
show_name = "Kino"

[shows.c404]
channels = ["donflurgundy"]
"""


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "clipper.toml"
    path.write_text(TOML + f'\n[paths]\nstreams = "{(tmp_path / "streams").as_posix()}"\n', encoding="utf-8")
    root = tmp_path / "streams"
    for sid, channel, started in [("kick-1370001", "kinocasino", "2026-11-10T20:00:00Z"),
                                  ("kick-1370002", "kinocasino", "2026-11-12T20:00:00Z"),
                                  ("kick-1380001", "donflurgundy", "2026-11-11T20:00:00Z"),
                                  ("kick-1390001", "kinocasino", "")]:  # still recording
        (root / sid).mkdir(parents=True)
        jsonstate.write(root / sid / "meta.json", {"channel": channel, "started_at": started})
    return config.load(path, use_local=False)


def test_stream_shortcuts(cfg):
    root = cfg["paths"]["streams"]
    assert config.find_stream(cfg, "latest") == root / "kick-1370002"
    assert config.find_stream(cfg, "latest:c404") == root / "kick-1380001"
    assert config.find_stream(cfg, "kick-138") == root / "kick-1380001"  # a unique start
    assert config.find_stream(cfg, "kick-1370001") == root / "kick-1370001"
    with pytest.raises(ClipperError, match="starts 2 stream ids"):
        config.find_stream(cfg, "kick-137")
    with pytest.raises(ClipperError, match="did you mean kick-1370001"):
        config.find_stream(cfg, "kick-1370011")
    with pytest.raises(ClipperError, match=r"no \[shows.nope\]"):
        config.find_stream(cfg, "latest:nope")
    # what services call stays exact: an id they hold never resolves to another stream
    with pytest.raises(ClipperError, match="no stream 'kick-138'"):
        config.stream_dir(cfg, "kick-138")


def test_latest_compares_times_not_text(cfg):
    root = cfg["paths"]["streams"]
    # 13:00-08:00 is 21:00 UTC: after kick-1370002's 20:00Z, though its text sorts first
    jsonstate.write(root / "kick-1380001" / "meta.json",
                    {"channel": "donflurgundy", "started_at": "2026-11-12T13:00:00-08:00"})
    assert config.find_stream(cfg, "latest") == root / "kick-1380001"


def test_config_diff_lists_what_differs_by_layer(cfg, monkeypatch):
    monkeypatch.setenv("CLIPPER_CUT_BACKEND", "nas")
    monkeypatch.setenv("CLIPPER_META_SRT_LINE_CHARS", str(config.DEFAULTS["meta"]["srt_line_chars"]))
    rows = config.diff(cfg)
    names = [(layer, name) for layer, name, _, _ in rows]
    assert ("clipper.toml", "meta.srt_max_s") in names and ("[shows.kino]", "meta.srt_max_s") in names
    assert ("environment", "cut.backend") in names
    assert not any(n == "meta.srt_line_chars" for _, n in names)  # set to its default: not a change
    assert not any(n.startswith("shows.") for _, n in names)
    assert [la for la, *_ in config.diff(cfg, "c404")] == ["clipper.toml", "clipper.toml", "environment"]
    with pytest.raises(ClipperError, match=r"no \[shows.nope\]"):
        config.diff(cfg, "nope")


def test_completion_knows_commands_actions_flags_and_shortcuts():
    t = completion.table(cli.build_parser(), ["kino"])
    assert "review" in t[""] and "--config" in t[""]
    assert "list" in t["review"] and "--json" in t["review list"]
    assert "latest:kino" in t["run"] and "latest" not in t["review approve"]
    bash = completion.script("bash", cli.build_parser(), ["kino"])
    assert 'complete -o default -F _clipper clipper' in bash and '"review list")' in bash
    ps = completion.script("powershell", cli.build_parser(), ["kino"])
    assert "Register-ArgumentCompleter" in ps and "'review list' = " in ps
    assert "'" not in "".join(w for words in t.values() for w in words)  # would break the PS quoting


@pytest.mark.parametrize("argv", [["review", "list", "--json"], ["schedule", "--list", "--json"],
                                  ["shows", "--json"], ["config", "diff", "--json"], ["config", "diff", "kino"],
                                  ["completion", "bash"]])
def test_the_new_flags_parse(argv):
    cli.build_parser().parse_args(argv)


def test_shows_and_config_diff_print_json(cfg, capsys):
    cli.cmd_shows(cfg, cli.build_parser().parse_args(["shows", "--json"]))
    rows = json.loads(capsys.readouterr().out)
    kino = next(r for r in rows if r["show"] == "kino")
    assert kino["show_name"] == "Kino" and len(kino["streams"]) == 3
    cli.cmd_config(cfg, cli.build_parser().parse_args(["config", "diff", "--json"]))
    assert {"layer": "[shows.kino]", "name": "meta.srt_max_s", "default": config.DEFAULTS["meta"]["srt_max_s"],
            "value": 4} in json.loads(capsys.readouterr().out)


def test_schedule_json_needs_list(cfg):
    with pytest.raises(ClipperError, match="--json goes with --list"):
        cli.cmd_schedule(cfg, cli.build_parser().parse_args(["schedule", "--json"]))


def test_unknown_stage_suggests_the_nearest():
    stages = ["score", "segments", "thumbs", "cut"]
    assert cli.near_stage({"thmubs", "segmnets"}, stages) == (
        "segmnets: did you mean segments?; thmubs: did you mean thumbs?")
    assert cli.near_stage(["zzz"], ["score", "cut"]) == "stages: score, cut"


def test_completion_bytes_keep_unix_newlines(cfg, capsysbinary):
    cli.cmd_completion(cfg, cli.build_parser().parse_args(["completion", "bash"]))
    out = capsysbinary.readouterr().out
    assert b"complete -o default" in out and b"\r" not in out


def test_shows_json_marks_streams_of_no_show(cfg, capsys):
    jsonstate.write(cfg["paths"]["streams"] / "kick-1390001" / "meta.json", {"channel": "someoneelse"})
    config._CHANNELS.clear()
    cli.cmd_shows(cfg, cli.build_parser().parse_args(["shows", "--json"]))
    rows = json.loads(capsys.readouterr().out)
    assert {"show": "", "show_name": "", "channels": ["someoneelse"], "kitsu": "",
            "streams": ["kick-1390001"]} in rows


def test_config_diff_takes_the_pinned_show(cfg, capsys):
    cli.cmd_config(config.pin_show(cfg, "c404"), cli.build_parser().parse_args(["config", "diff"]))
    assert "[shows.kino]" not in capsys.readouterr().out
