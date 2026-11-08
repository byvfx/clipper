from conftest import ROOT

from clipper import config, doctor


def prompts_check(tmp_path, toml: str) -> list[tuple[str, str, str, str]]:
    path = tmp_path / "clipper.toml"
    path.write_text(toml, encoding="utf-8")
    return doctor.collect(doctor.check_prompts, config.load(path, use_local=False))


def test_every_default_prompt_is_in_the_repo(tmp_path):
    (status, _, what, _), = prompts_check(tmp_path, "")
    assert status == "OK", what


def test_a_prompts_folder_of_your_own_is_checked(tmp_path):
    """[paths] prompts naming a folder that isn't there is a warning: the packaged files are read."""
    mine = '''[paths]
prompts = "mine"
'''
    (status, _, what, fix), = prompts_check(tmp_path, mine)
    assert status == "WARN" and "not a folder" in what and "drop the key" in fix
    (tmp_path / "mine").mkdir()
    (status, _, what, _), = prompts_check(tmp_path, mine)
    assert status == "OK" and "read first" in what


def test_the_example_shows_find_their_prompts():
    cfg = config.load(ROOT / "clipper.example.toml", use_local=False)
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
    for words in ([], ["", ""], ["   ", "PLANET"]):
        defaults["weekly"]["masthead"] = words
        rows = doctor.collect(doctor.check_fonts, defaults)
        assert any(s == "WARN" and "[weekly] masthead" in what for s, _, what, _ in rows), words
    defaults["weekly"]["masthead"] = ["DAILY", "PLANET"]
    assert not any("masthead" in what for _, _, what, _ in doctor.collect(doctor.check_fonts, defaults))


def test_role_settings_warn_on_uploads_by_hand_and_a_bot_with_discord_off(defaults):
    """The defaults never upload by themselves and post no cards: a serve machine hears about the
    first, a bot machine about the second (audit item 17)."""
    def rows(role):
        return doctor.collect(doctor.check_role_settings, defaults, role)
    assert any(s == "WARN" and "auto_upload_minutes 0" in what for s, _, what, _ in rows("serve"))
    defaults["youtube"]["auto_upload_minutes"] = 60
    assert any(s == "OK" and "auto_upload_minutes 60" in what for s, _, what, _ in rows("serve"))
    assert any(s == "WARN" and "[discord] enabled = false" in what for s, _, what, _ in rows("bot"))
    assert not any("[discord] enabled" in what for _, _, what, _ in rows("all"))
    defaults["discord"]["enabled"] = True
    assert not any("[discord] enabled" in what for _, _, what, _ in rows("bot"))
