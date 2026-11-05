"""`clipper models gold` (#72): verdicts, proposals, labels and the score; no model calls."""
from __future__ import annotations

import pytest

from clipper import gold, jsonstate
from clipper.errors import ClipperError


def rec(status: str, **kw) -> dict:
    return {"status": status, "has_clip": status == "kept", **kw}


def test_verdict_names_the_reason():
    assert gold.verdict(rec("kept"), 5) == "keep"
    assert gold.verdict(rec("error"), 5) == "error"
    assert gold.verdict(rec("rejected", casino_gameplay=True, third_party_audio=True), 5) == "casino"
    assert gold.verdict(rec("rejected", third_party_audio=True), 5) == "third_party"
    # allow_reactions: too much source audio is third party without the flag
    assert gold.verdict(rec("rejected", third_party_mode="allow_reactions", source_audio_s=12), 5) == "third_party"
    assert gold.verdict(rec("rejected", needs_context=True), 5) == "needs_context"
    assert gold.verdict(rec("no_clip", needs_context=True), 5) == "needs_context"
    assert gold.verdict(rec("no_clip"), 5) == "no_clip"


def stream(root, name, pass_a, review, repeat=None):
    d = root / name
    (d / "compare").mkdir(parents=True)
    jsonstate.write(d / "candidates.json", {"prompt_version": "v5", "pass_a": pass_a, "pass_b": {}})
    jsonstate.write(d / "review.json", review)
    jsonstate.write(d / "meta.json", {"channel": "kinocasinogaming"})
    if repeat is not None:
        jsonstate.write(d / "compare" / "score_claude-haiku-5-5-repeat_a.json",
                        {"candidate": {"pass_a": repeat}})
    return d


def test_proposals_confirm_approvals_and_need_two_runs_to_agree(defaults, tmp_path):
    w = {"window": [100, 200], "model": "claude-haiku-5-5", "why": "x"}
    d = stream(tmp_path, "kick-1", [
        {"id": "t01", **w, **rec("kept"), "short_in_s": 110, "short_out_s": 140},
        {"id": "t02", **w, **rec("kept")},
        {"id": "w03", **w, **rec("rejected", third_party_audio=True)},
        {"id": "w04", **w, **rec("rejected", needs_context=True)},
        {"id": "w05", **w, **rec("no_clip")},
        {"id": "h01", **w, **rec("kept"), "source": "hand"},
    ], {"v01_t01": {"kind": "short", "id": "t01", "decision": "approved"},
        "v02_h01": {"kind": "short", "id": "h01", "decision": "approved"}},
        repeat=[{"id": "w03", **rec("rejected", third_party_audio=True)}, {"id": "w04", **rec("no_clip")}])
    got = {p["id"]: p for p in gold.stream_proposals(d, defaults)}
    assert set(got) == {"t01", "t02", "w03", "w05"}  # w04: the repeat disagreed; h01: a hand pick
    assert got["t01"]["confirmed"] and got["t01"]["label"] == "keep" and got["t01"]["short"] == [110, 140]
    assert got["t02"]["proposed"] == "?" and not got["t02"]["confirmed"]
    assert got["w03"]["label"] == "third_party" and got["w03"]["source"] == "the model, twice"
    assert got["w05"]["source"] == "the model"


def test_confirm_sets_labels_by_key(defaults, tmp_path):
    defaults["paths"]["model_gold"] = path = tmp_path / "gold.json"
    jsonstate.write(path, {"items": [{"stream": "kick-1", "id": "t02", "label": "", "confirmed": False},
                                     {"stream": "kick-1", "id": "w03", "label": "third_party", "confirmed": False}]})
    assert gold.confirm(defaults, {"kick-1_t02": {"label": "no_clip", "note": "flat"}}) == 1
    items = jsonstate.read(path)["items"]
    assert items[0] == {"stream": "kick-1", "id": "t02", "label": "no_clip", "confirmed": True, "note": "flat"}
    assert not items[1]["confirmed"]
    assert gold.confirm(defaults, {"kick-1_t02": {"label": "no_clip", "note": "flat"}}) == 0
    with pytest.raises(ClipperError):
        gold.confirm(defaults, {"kick-1_w03": {"label": "maybe"}})


def test_score_counts_recall_precision_and_hard_rule_keeps():
    g = [{"stream": "s", "id": i, "label": lab} for i, lab in
         [("a", "keep"), ("b", "keep"), ("c", "third_party"), ("d", "casino"), ("e", "no_clip")]]
    said = {("s", "a"): "keep", ("s", "b"): "no_clip", ("s", "c"): "keep", ("s", "d"): "casino"}
    res = gold.score(g, said)
    assert res["right"] == 2 and res["n"] == 5
    assert res["per_label"]["keep"] == {"n": 2, "recall": 0.5, "precision": 0.5}
    assert res["kept_hard_rule"] == 1  # c: a played clip kept
    assert res["keep_right"] == 3  # a, d, e right (e: "error" is not a keep); b, c wrong
    assert res["confusion"]["no_clip>error"] == 1
