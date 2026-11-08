import json
import re
import tomllib
from pathlib import Path

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


def test_big_story_preset_only_has_known_settings():
    """docs/big-story.toml is copied to a stream's config.toml, which refuses unknown keys."""
    preset = tomllib.loads((ROOT / "docs" / "big-story.toml").read_text(encoding="utf-8"))
    unknown = [f"[{s}] {k}" for s, v in preset.items() for k in v if k not in config.DEFAULTS.get(s, {})]
    assert not unknown, "docs/big-story.toml: " + ", ".join(unknown)


def test_example_loads(tmp_path):
    cfg = config.load(ROOT / "clipper.example.toml", use_local=False)
    for key in cfg["shows"]:
        config.for_show(cfg, key)


def test_bad_choices_are_refused(tmp_path, monkeypatch):
    """B7: [bleep] mode = "Bleep" used to mean no bleeping at all."""
    path = tmp_path / "clipper.toml"
    path.write_text('[bleep]\nmode = "Bleep"\n', encoding="utf-8")
    with pytest.raises(ClipperError, match=r"\[bleep\] mode = 'Bleep' \(one of: off, bleep, mute\)"):
        config.load(path)
    path.write_text('[shows.kino]\nchannels = ["k"]\n[shows.kino.shorts]\nwindow = "long"\n', encoding="utf-8")
    cfg = config.load(path)
    with pytest.raises(ClipperError, match=r"\[shows.kino\]: .*\[shorts\] window"):
        config.for_show(cfg, "kino")
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLIPPER_CUT_BACKEND", "cloud")
    with pytest.raises(ClipperError, match=r"\[cut\] backend = 'cloud'"):
        config.load(path)


def test_every_choice_default_is_valid():
    for (section, key), choices in config.CHOICES.items():
        assert config.DEFAULTS[section][key] in choices, (section, key)


def test_unknown_keys_and_env_warn(tmp_path, monkeypatch, capsys):
    path = tmp_path / "clipper.toml"
    path.write_text('[meta]\nsrt_max = 1\n[shows.kino]\nchannels = ["k"]\n[shows.kino.metaa]\nx = 1\n',
                    encoding="utf-8")
    monkeypatch.setenv("CLIPPER_META_EFORT", "high")
    cfg = config.load(path)   # warns, doesn't refuse: a typo mustn't stop the bot
    err = capsys.readouterr().err
    assert "unknown setting [meta] srt_max" in err and "[shows.kino]: unknown section [metaa]" in err
    assert "CLIPPER_META_EFORT matches no setting" in err
    assert len(config.problems(cfg)) == 3


def test_env_for_an_unset_default():
    assert config._coerce("false", None) is False and config._coerce("TRUE", None) is True
    assert config._coerce("anything", None) == "anything"


def test_show_flag_sticks_for_a_stream(tmp_path):
    """B4: `clipper --show c404 meta <a kino stream>` used c404's settings nowhere."""
    path = tmp_path / "clipper.toml"
    path.write_text('[shows.kino]\nchannels = ["kinocasino"]\n[shows.kino.meta]\nsrt_max_s = 4\n'
                    '[shows.c404]\nchannels = ["donflurgundy"]\n[shows.c404.meta]\nsrt_max_s = 8\n',
                    encoding="utf-8")
    sdir = tmp_path / "kick-9"
    sdir.mkdir()
    (sdir / "meta.json").write_text(json.dumps({"channel": "kinocasino"}), encoding="utf-8")
    cfg = config.load(path)
    assert config.for_stream(cfg, sdir)["meta"]["srt_max_s"] == 4      # the channel's show
    pinned = config.for_stream(config.pin_show(cfg, "c404"), sdir)
    assert pinned["meta"]["srt_max_s"] == 8 and pinned["show"]["key"] == "c404"
    assert config.for_stream(pinned, sdir)["meta"]["srt_max_s"] == 8   # stages re-resolve
    (sdir / config.STREAM_CONFIG).write_text("[meta]\nsrt_line_chars = 30\n", encoding="utf-8")
    both = config.for_stream(config.pin_show(cfg, "c404"), sdir)
    assert (both["meta"]["srt_max_s"], both["meta"]["srt_line_chars"]) == (8, 30)
    assert config.for_stream(cfg, sdir)["meta"]["srt_max_s"] == 4      # no leak into the cache


def test_explain_lists_each_layer(cfg_path, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_META_SRT_LINE_CHARS", "40")
    cfg = config.load(cfg_path)
    e = config.explain(config.pin_show(cfg, "kino"), "meta.srt_max_s")
    assert e["value"] == 8 and [n for n, _ in e["layers"]] == ["default", "clipper.toml", "[shows.kino]"]
    e = config.explain(cfg, "meta.srt_line_chars")
    assert e["value"] == 40 and [n for n, _ in e["layers"]][-2:] == ["clipper.local.toml",
                                                                     "$CLIPPER_META_SRT_LINE_CHARS"]
    with pytest.raises(ClipperError, match="did you mean meta.srt_max_s"):
        config.explain(cfg, "meta.srt_max")


def test_setup_starter_config_is_clean(tmp_path):
    from clipper import setup
    path = tmp_path / "clipper.toml"
    path.write_text(setup.NEW_CONFIG, encoding="utf-8")
    cfg = config.load(path, use_local=False)
    assert config.problems(cfg) == [] and cfg["shows"] == {}
    assert cfg["bleep"]["mode"] == "bleep" and cfg["record"]["channels"] == []


def test_show_pin_carries_to_children(cfg_path):
    cfg = config.load(cfg_path)
    assert config.show_args(cfg) == []
    assert config.show_args(config.pin_show(cfg, "kino")) == ["--show", "kino"]


def test_show_key_typos_warn(tmp_path, capsys, monkeypatch):
    path = tmp_path / "clipper.toml"
    path.write_text('[shows.kino]\nchannnels = ["kinocasino"]\n', encoding="utf-8")
    config.load(path)
    assert "[shows.kino]: unknown setting channnels" in capsys.readouterr().err
    monkeypatch.setenv("CLIPPER_DATA", "/volume1/docker/clipper")   # the NAS recorder's own variable
    path.write_text('[shows.kino]\nchannels = ["kinocasino"]\n', encoding="utf-8")
    assert config.problems(config.load(path)) == []

def test_find_falls_back_to_the_user_config(tmp_path, monkeypatch):
    # The laptop's config is on the NAS share (2026-11-05): shells that didn't inherit the
    # user-scope CLIPPER_CONFIG still find it, but ./clipper.toml and the env var come first.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLIPPER_CONFIG", raising=False)
    monkeypatch.setattr(config, "user_env", lambda name: "//nas/clipper/clipper.toml")
    assert config.find() == Path("//nas/clipper/clipper.toml")
    assert config.find("other.toml") == Path("other.toml")
    (tmp_path / "clipper.toml").write_text("")
    assert config.find() == Path("clipper.toml")
    monkeypatch.setenv("CLIPPER_CONFIG", "env.toml")
    assert config.find() == Path("env.toml")


def test_prompts_fall_back_to_the_checkout(tmp_path):
    path = tmp_path / "clipper.toml"
    path.write_text("")
    assert config.load(path, use_local=False)["paths"]["prompts"] == config.CHECKOUT_PROMPTS
    (tmp_path / "prompts").mkdir()
    assert config.load(path, use_local=False)["paths"]["prompts"] == tmp_path / "prompts"


def test_photos_is_beside_the_config_not_the_cwd(tmp_path, monkeypatch):
    """The claude container runs in /app, the hub in /data: photos = "photos" is /data/photos."""
    conf = tmp_path / "conf"
    conf.mkdir()
    path = conf / "clipper.toml"
    path.write_text('[thumbnail]\nphotos = "photos"\n[shows.kino]\nchannels = ["k"]\n'
                    '[shows.kino.thumbnail]\nphotos = "kino_photos"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = config.load(path)
    assert cfg["thumbnail"]["photos"] == str(conf.resolve() / "photos")
    assert config.for_show(cfg, "kino")["thumbnail"]["photos"] == str(conf.resolve() / "kino_photos")
    sdir = tmp_path / "streams" / "kick-1"
    sdir.mkdir(parents=True)
    (sdir / "meta.json").write_text('{"channel": "k"}', encoding="utf-8")
    (sdir / config.STREAM_CONFIG).write_text('[thumbnail]\nphotos = "night_photos"\n', encoding="utf-8")
    assert config.for_stream(cfg, sdir)["thumbnail"]["photos"] == str(conf.resolve() / "night_photos")
    path.write_text('[thumbnail]\nphotos = ""\n', encoding="utf-8")
    assert config.load(path)["thumbnail"]["photos"] == ""


def test_container_variables_and_hidden_shows(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_REPO", "https://github.com/x/clipper")   # deploy/claude's clone URL
    path = tmp_path / "clipper.toml"
    path.write_text('[warroom]\nhide_shows = ["kino"]\n[shows.kino]\nchannels = ["k"]\n', encoding="utf-8")
    assert config.problems(config.load(path)) == []
    path.write_text('[warroom]\nhide_shows = ["knio"]\n[shows.kino]\nchannels = ["k"]\n', encoding="utf-8")
    assert config.problems(config.load(path)) == ["[warroom] hide_shows: no [shows.knio]"]


# ---------- what load and problems refuse (the 11-03 audit's input edges) ----------

def test_notify_times_and_budget_are_checked_at_load(defaults):
    """A typo in [notify] used to kill the bot's DM loop every minute with no DM to say so."""
    cfg = {**defaults, "notify": {**defaults["notify"], "quiet_hours": "9:00pm-07:00", "digest_time": "09:00"}}
    assert [p for p in config.problems(cfg) if "quiet_hours" in p] == ["[notify] quiet_hours = '9:00pm-07:00': HH:MM-HH:MM (or empty)"]
    cfg["notify"]["quiet_hours"], cfg["notify"]["digest_time"] = "23:00-07:00", "9:5"
    assert [p for p in config.problems(cfg) if "notify" in p] == ["[notify] digest_time = '9:5': HH:MM (or empty)"]
    cfg["notify"]["digest_time"] = ""
    assert not [p for p in config.problems(cfg) if "notify" in p]
    cfg["schedule"] = {**defaults["schedule"], "budget_hours": 0}
    assert [p for p in config.problems(cfg) if "budget_hours" in p]


def test_budget_hours_is_checked_per_show(defaults):
    cfg = {**defaults, "shows": {"c404": {"channels": ["channel404"], "overrides": {"schedule": {"budget_hours": 0}}}}}
    assert [p for p in config.problems(cfg) if "budget_hours" in p] == ["[shows.c404.schedule] budget_hours = 0 must be a positive number (it divides in every replan)"]
    cfg["schedule"] = {**defaults["schedule"], "budget_hours": -1}
    assert len([p for p in config.problems(cfg) if "budget_hours" in p]) == 2


def test_a_missing_stream_is_one_error(defaults, tmp_path):
    cfg = {**defaults, "paths": {**defaults["paths"], "streams": tmp_path}}
    with pytest.raises(ClipperError, match="no stream 'kick-nope'"):
        config.stream_dir(cfg, "kick-nope")
    (tmp_path / "kick-1").mkdir()
    assert config.stream_dir(cfg, "kick-1") == tmp_path / "kick-1"


def test_stream_dir_still_accepts_a_directory(tmp_path):
    d = tmp_path / "anywhere"
    d.mkdir()
    assert config.stream_dir({"paths": {"streams": Path("/nope")}}, str(d)) == d


def test_effort_values_are_checked(defaults):
    cfg = {**defaults, "meta": {**defaults["meta"], "effort": "med"}}
    with pytest.raises(ClipperError, match=r"\[meta\] effort = 'med'"):
        config.check_choices(cfg)


def test_effort_values_of_any_type_are_checked(defaults):
    for bad in (False, 3, ["high"]):
        cfg = {**defaults, "meta": {**defaults["meta"], "effort": bad}}
        with pytest.raises(ClipperError, match=r"\[meta\] effort"):
            config.check_choices(cfg)


def test_a_loose_key_in_the_toml_is_a_warning_not_a_crash(tmp_path):
    path = tmp_path / "clipper.toml"
    path.write_text('stale = 1\n[meta]\nshow_name = "x"\n', encoding="utf-8")
    cfg = config.load(path, use_local=False)
    assert any("outside any [section]" in p for p in cfg["_unknown"])
    path.write_text("[shows]\nfoo = 1\n", encoding="utf-8")
    with pytest.raises(ClipperError, match=r"\[shows\] holds"):
        config.load(path, use_local=False)


# This install's own names (its shows, people, channels and addresses), which the product's
# defaults, example and setup must not carry: a new install's come from its own clipper.toml.
# Audit of 2026-11-08, item 13; extend the pattern when a new one slips in.
INSTALL = re.compile(r"(?<![a-z])(kino|lolcow|andy|ppp|jer|jeremy|hambly|quartering|dspgaming|donflurgundy"
                     r"|byvfx|UCt26)|192\.168\.", re.I)


def test_defaults_example_and_setup_name_no_install():
    hits = []
    for f in ("clipper/config.py", "clipper.example.toml", "clipper/setup.py"):
        for i, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
            m = INSTALL.search(line)
            if m:
                hits.append(f"{f}:{i} {m.group(0)!r}")
    assert not hits, "this install's names in the product's files (they belong in clipper.toml): " + ", ".join(hits)
