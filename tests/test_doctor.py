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


def test_v7_prompts_need_the_show_gambling_rule(defaults):
    defaults["scoring"].update(prompt_version="v7", gambling="commentary")
    assert "gambling_commentary_v7.md" in doctor.prompt_files(defaults)
    defaults["scoring"]["prompt_version"] = "v6"
    assert not any("gambling" in n for n in doctor.prompt_files(defaults))


def test_repo_state_reads_branch_ahead_and_changes():
    assert doctor.repo_state("## main...origin/main\n") == ("main", 0, 0)
    assert doctor.repo_state("## nas-claude...origin/nas-claude [ahead 2, behind 1]\n M clipper/x.py\n?? y\n") \
        == ("nas-claude", 2, 2)
    assert doctor.repo_state("## local-only\n") == ("local-only", 0, 0)
    assert doctor.repo_state("## HEAD (no branch)\n") == ("", 0, 0)


def test_claude_auth_reads_the_status():
    assert doctor.claude_auth('{"loggedIn": true, "authMethod": "claude.ai"}', 0) == (True, "claude.ai")
    assert doctor.claude_auth('{"authMethod": "api_key"}', 0) == (True, "api_key")
    assert doctor.claude_auth("Not logged in", 1) == (False, "")


def test_a_missing_clipper_command_points_at_the_bootstrap(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    (status, _, _, hint), = doctor.collect(doctor.check_command)
    assert status == "WARN" and "bootstrap" in hint
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/home/me/.local/bin/clipper")
    (status, _, what, _), = doctor.collect(doctor.check_command)
    assert status == "OK" and what.endswith(".local/bin/clipper")


def test_font_hints_name_the_linux_packages_only_on_linux(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    assert doctor.LINUX_FONTS in doctor.font_hint("install Impact")
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    assert doctor.font_hint("install Impact") == "install Impact"


def test_doctor_prices_the_defaults_like_the_client(defaults):
    """A [llm.prices] table replaces the nested default in load; the client merges the
    defaults back, and so does the check."""
    cfg = {**defaults, "llm": {**defaults["llm"], "prices": {"openai:gpt-5-mini": [1.0, 2.0]}}}
    (status, _, what, _), = doctor.collect(doctor.check_llm_prices, cfg)
    assert status == "OK", what


def test_an_unset_masthead_is_a_warning(defaults):
    """The default [weekly] masthead is empty: doctor warns, and stops once two words are set."""
    rows = doctor.collect(doctor.check_fonts, defaults)
    assert any(s == "WARN" and "[weekly] masthead" in what for s, _, what, _ in rows)
    defaults["weekly"]["masthead"] = ["DAILY", "PLANET"]
    assert not any("masthead" in what for _, _, what, _ in doctor.collect(doctor.check_fonts, defaults))
