import re
from pathlib import Path

from clipper import prompts

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def test_cast_names():
    name = lambda cast: prompts.cast_names({"meta": {"cast": cast}})
    assert name(["Andy", "PPP"]) == "Andy or PPP"
    assert name(["A", "B", "C"]) == "A, B or C"
    assert name(["Don"]) == "Don" and name([]) == "" and prompts.cast_names({}) == ""


def test_cast_note_is_an_aside_or_nothing(defaults):
    cfg = {**defaults, "meta": {**defaults["meta"], "cast": ["Don"]}}
    assert prompts.Prompts(cfg).vars["cast_note"] == " (Don)"
    assert prompts.Prompts(defaults).vars["cast_note"] == ""


def latest() -> dict[str, Path]:
    out: dict[str, tuple[int, Path]] = {}
    for f in PROMPTS.glob("*_v*.md"):
        m = re.fullmatch(r"(.+)_v(\d+)", f.stem)
        if m and int(m[2]) > out.get(m[1], (0, f))[0]:
            out[m[1]] = (int(m[2]), f)
    return {k: f for k, (_, f) in out.items()}


def test_newest_prompts_say_host_only_to_ban_it():
    """The model copies "the hosts" from its own prompt into titles and covers; the house
    style bans the word. Name the people ({cast_note}) instead."""
    for f in latest().values():
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bhosts?\b", line, re.IGNORECASE):
                assert re.search(r"\bnever\b", line, re.IGNORECASE), f"{f.name}:{n}: {line.strip()}"


def test_the_gambling_rule_is_a_show_setting_from_v7(defaults):
    cfg = {**defaults, "scoring": {**defaults["scoring"], "prompt_version": "v7"}}
    assert "is about gambling play" in prompts.Prompts(cfg).get("score_a")
    cfg["scoring"]["gambling"] = "commentary"
    p = prompts.Prompts(cfg)
    assert "would promote gambling" in p.get("score_a") and "promotes gambling" in p.get("score_b")
    assert "promote gambling" in p.get("scan")
    v6 = {**defaults, "scoring": {**defaults["scoring"], "prompt_version": "v6", "gambling": "commentary"}}
    assert "is about gambling play" in prompts.Prompts(v6).get("score_a")  # v6 keeps its own wording


def test_a_scan_made_with_other_settings_is_scanned_again(defaults, tmp_path):
    from clipper import jsonstate, scoring

    class Fake:
        calls = 0

        def json_call(self, *a, **k):
            Fake.calls += 1
            return {"moments": []}, {}

    words = [{"start": 1.0, "end": 1.5, "word": "hi", "seg": 0}]

    def run(cfg):
        return scoring.transcript_scan(tmp_path, words, 10, cfg, Fake(), prompts.Prompts(cfg))

    v7 = {**defaults, "scoring": {**defaults["scoring"], "prompt_version": "v7", "gambling": "reject"}}
    run(v7)
    run(v7)
    assert Fake.calls == 1  # cached
    run({**v7, "scoring": {**v7["scoring"], "gambling": "commentary"}})
    assert Fake.calls == 2  # the show's rule changed the prompt
    old = [{"start_s": 1, "end_s": 30, "summary": "x"}]
    jsonstate.write(tmp_path / "scan_v7.json", old)  # cached before the digest: still current
    assert scoring.cached_scan(tmp_path / "scan_v7.json", "anything") == old
