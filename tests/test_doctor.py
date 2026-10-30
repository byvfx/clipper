from conftest import ROOT

from clipper import config, doctor


def prompts_check(tmp_path, toml: str) -> list[tuple[str, str, str, str]]:
    path = tmp_path / "clipper.toml"
    path.write_text(f'[paths]\nprompts = "{(ROOT / "prompts").as_posix()}"\n' + toml, encoding="utf-8")
    return doctor.collect(doctor.check_prompts, config.load(path, use_local=False))


def test_every_default_prompt_is_in_the_repo(tmp_path):
    (status, _, what, _), = prompts_check(tmp_path, "")
    assert status == "OK", what


def test_the_example_shows_find_their_prompts():
    cfg = config.load(ROOT / "clipper.example.toml", use_local=False)
    cfg["paths"]["prompts"] = ROOT / "prompts"
    (status, _, what, _), = doctor.collect(doctor.check_prompts, cfg)
    assert status == "OK", what
    assert f"{len(cfg['shows'])} shows" in what


def test_a_show_with_a_missing_prompt_is_named(tmp_path):
    toml = ('[shows.kino]\nchannels = ["KinoCasino"]\n[shows.kino.pick]\nprompt_version = "v99"\n'
            '[shows.c404]\nchannels = ["Channel404"]\n')
    (status, _, what, _), = prompts_check(tmp_path, toml)
    assert status == "FAIL"
    assert "pick_v99.md (kino)" in what and "pick_locate_v99.md (kino)" in what


def test_the_labeller_prompt_is_checked(defaults):
    assert "shorts_layout_v1.md" in doctor.prompt_files(defaults)
