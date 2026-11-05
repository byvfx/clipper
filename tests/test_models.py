"""`clipper models test` (#72): the fixtures, the checks and the report, with a fake model."""
from __future__ import annotations

import itertools
import re

from clipper import models


class FakeModel:
    """Answers Pass A by what the window says: keeps the two keepers around their payoff,
    flags the video and the slots, passes on the rest."""

    cost = 0.0

    def json_call(self, model, system, user, schema, effort, fallback=False, stream=False):
        body = user.split("<window>")[1] if "<window>" in user else ""
        out = {"has_clip": False, "third_party_audio": False, "casino_gameplay": False, "in_s": 0, "out_s": 0,
               "short_in_s": 0, "short_out_s": 0, "category": "joke", "hook": "", "title": "", "score": 0,
               "why": "setup", "source_audio_s": 0, "needs_context": False}
        if "landlord" in body:
            out.update(has_clip=True, in_s=121, out_s=192, short_in_s=141, short_out_s=172, score=7,
                       hook="He thought the chirp was jazz", title="The landlord heard a trumpet")
        elif "Self checkout" in body:
            # a Short that ends before the payoff, and a long hook
            out.update(has_clip=True, in_s=121, out_s=190, short_in_s=121, short_out_s=145, score=6,
                       hook="Self checkout is a job you do for free, hosts", title="Unpaid staff")
        elif "Toast Master" in body:
            out.update(third_party_audio=True, source_audio_s=60)
        elif "Sweet Fortune" in body:
            out.update(has_clip=True, casino_gameplay=True, in_s=121, out_s=190, short_in_s=140, short_out_s=170,
                       score=8)
        return out, {"retried": "Gary" in body}


def test_fixture_words_carry_speakers_and_times():
    words = models.words_of(models.FIXTURES[2]["lines"])
    assert words[0]["start"] == 60 and words[0]["who"] == "Andy"
    played = [w for w in words if w["speaker"] == models.PLAYED]
    assert played and all(w["who"] == "?" for w in played)
    assert all(a["start"] <= b["start"] for a, b in itertools.pairwise(words))


def test_signals_spike_at_the_peaks():
    sig = models.signals_of([150])
    assert sig["chat_msgs"][150] > sig["chat_msgs"][100] and sig["heat"][150] == 3.0


def test_pass_a_check_counts_verdicts_cuts_and_retries(defaults):
    cfg = models.fixture_cfg(defaults, "openrouter:x/y", "none")
    assert cfg["scoring"]["model_a"] == "openrouter:x/y" and cfg["scoring"]["effort_a"] == "none"
    assert defaults["scoring"]["model_a"] != "openrouter:x/y"  # a copy
    res = models.check_pass_a(cfg, FakeModel(), rounds=2)
    assert res["right"] == 12 and res["of"] == 12 and res["rounds_agree"] == 6
    assert res["hard_rule_kept"] == []  # the slots were flagged, so the code rejected them
    assert any("misses the payoff at 150" in p for p in res["cut_problems"])
    assert any('"hosts"' in p for p in res["cut_problems"])
    assert any("hook 10 words" in n for n in res["cut_notes"])
    assert len(res["cut_problems"]) == len(set(res["cut_problems"]))  # once, not once a round
    assert res["retried"] == 2 and not res["ok"]


def test_long_transcript_hides_the_needle_once():
    text, at = models.long_transcript(tokens=5000)
    assert text.count(models.NEEDLE) == 1
    assert 4500 * models.CHARS_PER_TOKEN < len(text) < 5500 * models.CHARS_PER_TOKEN
    line = next(x for x in text.splitlines() if models.NEEDLE in x)
    assert float(re.match(r"\[([\d.]+)\]", line).group(1)) == at


def test_fitness_names_what_each_stage_lacks(defaults):
    checks = {"json": {"ok": True}, "pass_a": {"ok": True}, "vision": {"ok": False}, "text": {"ok": True}}
    rows = {r["stage"]: r for r in models.fitness(defaults, checks)}
    assert rows["scoring.model_a"]["ok"]
    assert rows["shorts.layout_model"]["failed"] == ["vision"]
    assert rows["segments.model"]["untested"] == ["long"]  # --no-long


def test_report_reads_a_saved_result(defaults):
    res = {"model": "m", "effort": "none", "prompt_version": "v5", "cost_usd": 0.01,
           "checks": {"pass_a": models.check_pass_a(models.fixture_cfg(defaults, "m", ""), FakeModel(), 1)
                      | {"seconds": 1.0},
                      "json": {"ok": True, "retried": 1, "errors": []},
                      "vision": {"ok": False, "error": "no image input", "seconds": 0.1}},
           "fit": [{"stage": "scoring.model_a", "ok": False, "failed": ["pass_a"], "untested": []}]}
    text = models.report(res)
    assert "6/6 verdicts right" in text and "no image input" in text
    assert "could take 0 of 1 stages; not: scoring.model_a (pass_a)" in text
