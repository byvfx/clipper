"""`clipper compare`'s report logic (#72): no model calls."""
from __future__ import annotations

from clipper import compare


def kept(i: str, score: int, a: float, b: float) -> dict:
    return {"id": i, "status": "kept", "score": score, "in_s": a, "out_s": b}


def test_decisions_take_the_strongest_of_pick_and_short():
    review = {
        "c01_t20": {"kind": "pick", "id": "t20", "decision": "rejected"},
        "v01_t20": {"kind": "short", "id": "t20", "decision": "approved"},
        "v02_t42": {"kind": "short", "id": "t42", "decision": "changes_requested"},
        "s02_x": {"kind": "segment", "id": "s02", "decision": "approved"},
    }
    assert compare.decisions(review) == {"t20": "approved", "t42": "changes_requested"}


def test_pass_a_agreement_counts_verdicts_and_overlap():
    base = [kept("w01", 8, 0, 10), kept("w02", 6, 100, 120), {"id": "w03", "status": "no_clip"},
            {"id": "h01", "status": "kept", "source": "hand", "score": 9, "in_s": 0, "out_s": 1}]
    cand = [kept("w01", 6, 5, 15), {"id": "w02", "status": "rejected"}, {"id": "w03", "status": "no_clip"}]
    ag = compare.pass_a_agreement(base, cand)
    assert ag["windows"] == 3 and ag["same_status"] == 2 and ag["both_kept"] == 1
    assert ag["table"]["kept"]["rejected"] == 1 and ag["table"]["no_clip"]["no_clip"] == 1
    assert ag["mean_score_diff"] == 2 and ag["mean_clip_overlap"] == 0.33


def test_pick_rows_put_the_baseline_order_first_and_mark_review():
    base = [{"id": "t20", "title": "A"}, {"id": "t42", "title": "B"}]
    cand = [{"id": "t42", "title": "B2"}, {"id": "w11", "title": "C"}]
    rows = compare.pick_rows(base, cand, {"t20": "approved", "w11": "rejected"},
                             {"t20": {"in_s": 1, "out_s": 2}}, {"w11": {"in_s": 5, "out_s": 9}})
    assert [r["id"] for r in rows] == ["t20", "t42", "w11"]
    assert rows[0]["cand_rank"] is None and rows[0]["decision"] == "approved"
    assert rows[1]["base_rank"] == 2 and rows[1]["cand_rank"] == 1 and rows[1]["cand_title"] == "B2"
    assert rows[2]["in_s"] == 5 and rows[2]["base_title"] == ""


def test_against_review_and_the_report():
    decided = {"t20": "approved", "t42": "approved", "w11": "rejected"}
    base_picks, cand_picks = [{"id": "t20"}, {"id": "w11"}], [{"id": "t20"}, {"id": "t42"}]
    rev_b, rev_c = compare.against_review(base_picks, decided), compare.against_review(cand_picks, decided)
    assert rev_b["approved"] == {"picked": 1, "of": 2} and rev_b["rejected"] == {"picked": 1, "of": 1}
    assert rev_c["approved"] == {"picked": 2, "of": 2} and rev_c["rejected"]["picked"] == 0
    res = {"stream": "kick-1", "which": "score", "pass": "a", "prompt_version": "v5", "estimate_usd": 0.05,
           "notes": ["a note"], "pass_a_agreement": None,
           "baseline": {"label": "claude-haiku-5-5", "cost_usd": 0.2, "review": rev_b},
           "candidate": {"label": "openai:gpt-5-mini", "cost_usd": 0.04, "seconds": 12, "review": rev_c},
           "picks": compare.pick_rows([{"id": "t20", "title": "A"}], [{"id": "t20", "title": "A2"}], decided,
                                      {"t20": {"in_s": 1.0, "out_s": 2.0}}, {})}
    md = compare.report_md(res)
    assert "claude-haiku-5-5 vs openai:gpt-5-mini" in md and "- a note" in md
    assert "| t20 | 1-2 s | 1. A | 1. A2 | approved |" in md
    assert "approved: baseline picked 1 of 2, candidate 2" in md


def test_baseline_models_come_from_the_results():
    base = {"pass_a": [{"model": "claude-haiku-4-5"}, {"model": "claude-haiku-4-5"},
                       {"model": "x", "source": "hand"}], "pass_b": {"model": "claude-sonnet-4-5"}}
    cfg = {"scoring": {"model_a": "claude-haiku-5-5", "model_b": "claude-sonnet-5-5"}}
    assert compare.baseline_models(base, cfg) == ("claude-haiku-4-5", "claude-sonnet-4-5")
    assert compare.baseline_models({}, cfg) == ("claude-haiku-5-5", "claude-sonnet-5-5")
    assert compare.slug("openrouter:openai/gpt-5-mini") == "openrouter-openai-gpt-5-mini"


def test_report_names_keep_the_dots_in_model_names():
    names = {compare.report_stem(m, w) for m in ("openai:gpt-4.1", "openai:gpt-4.1-mini") for w in ("a", "b")}
    assert len(names) == 4 and "score_openai-gpt-4.1_a" in names


def test_hand_picks_stay_out_of_both_sides():
    base = {"pass_a": [{"id": "w01", "status": "kept"}, {"id": "h01", "status": "kept", "source": "hand"}],
            "pass_b": {"picks": [{"id": "h01"}, {"id": "w01"}]}}
    hand, pass_a, picks = compare.scored_only(base)
    assert hand == {"h01"} and [r["id"] for r in pass_a] == ["w01"] and picks == [{"id": "w01"}]
