import json
import tomllib

import pytest
from conftest import ROOT

from clipper import config
from clipper.errors import ClipperError

TOML = """
[meta]
srt_max_s = 7

[shows.kino]
channels = ["KinoCasino"]

[shows.kino.meta]
srt_max_s = 8
srt_line_chars = 30
"""


@pytest.fixture
def cfg_path(tmp_path):
    path = tmp_path / "clipper.toml"
    path.write_text(TOML, encoding="utf-8")
    (tmp_path / config.LOCAL_CONFIG).write_text("[meta]\nsrt_line_chars = 35\n", encoding="utf-8")
    return path


def test_layers(cfg_path, monkeypatch):
    """defaults < clipper.toml < show < clipper.local.toml < env < (stream config.toml)"""
    monkeypatch.setenv("CLIPPER_META_EFFORT", "high")
    cfg = config.load(cfg_path)
    assert cfg["meta"]["srt_max_s"] == 7
    assert cfg["meta"]["srt_line_chars"] == 35
    assert cfg["meta"]["effort"] == "high"
    assert cfg["meta"]["model"] == config.DEFAULTS["meta"]["model"]

    kino = config.for_show(cfg, "kino")
    assert kino["meta"]["srt_max_s"] == 8          # the show's own value
    assert kino["meta"]["srt_line_chars"] == 35    # this machine's value still wins
    assert kino["meta"]["effort"] == "high"
    assert kino["show"] == {"key": "kino", "channel": "kinocasino"}

    sdir = cfg_path.parent / "streams" / "kick-1"
    sdir.mkdir(parents=True)
    (sdir / "meta.json").write_text(json.dumps({"channel": "kinocasino"}), encoding="utf-8")
    (sdir / config.STREAM_CONFIG).write_text("[meta]\nsrt_max_s = 9\n", encoding="utf-8")
    one = config.for_stream(cfg, sdir)
    assert one["show"]["key"] == "kino"
    assert one["meta"]["srt_max_s"] == 9
    assert one["meta"]["srt_line_chars"] == 35


def test_env_value_must_parse(cfg_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_META_SRT_MAX_S", "six")
    with pytest.raises(ClipperError, match="CLIPPER_META_SRT_MAX_S"):
        config.load(cfg_path)


def test_env_list(cfg_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_WORKER_CHANNELS", "a, b")
    assert config.load(cfg_path)["worker"]["channels"] == ["a", "b"]


def test_unknown_keys_are_refused(cfg_path, tmp_path):
    (tmp_path / config.LOCAL_CONFIG).write_text("[meta]\nsrt_max = 1\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="unknown setting"):
        config.load(cfg_path)

    cfg = config.load(cfg_path, use_local=False)
    sdir = tmp_path / "kick-2"
    sdir.mkdir()
    (sdir / config.STREAM_CONFIG).write_text("[meta]\nsrt_max = 1\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="unknown setting"):
        config.for_stream(cfg, sdir)


def test_unknown_show_is_refused(cfg_path):
    with pytest.raises(ClipperError, match="no \\[shows.nope\\]"):
        config.for_show(config.load(cfg_path), "nope")


def test_example_documents_every_setting():
    """Every DEFAULTS key appears in clipper.example.toml, and nothing else does."""
    text = (ROOT / "clipper.example.toml").read_text(encoding="utf-8")
    example = tomllib.loads(text)
    # TOML has no null: a key whose default is None is documented as a commented line.
    missing = [f"[{s}] {k}" for s, v in config.DEFAULTS.items() for k, d in v.items()
               if k not in example.get(s, {}) and not (d is None and f"# {k} =" in text)]
    assert not missing, "add to clipper.example.toml: " + ", ".join(missing)
    unknown = [f"[{s}] {k}" for s, v in example.items() if s != "shows"
               for k in v if k not in config.DEFAULTS.get(s, {})]
    assert not unknown, "clipper.example.toml has settings config.py doesn't know: " + ", ".join(unknown)


def test_example_loads(tmp_path):
    cfg = config.load(ROOT / "clipper.example.toml", use_local=False)
    for key in cfg["shows"]:
        config.for_show(cfg, key)
