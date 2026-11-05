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
