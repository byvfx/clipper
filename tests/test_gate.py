import pytest

from clipper import gate, llm
from clipper.errors import ClipperError

SECTIONS = {
    "has_clip": "There is a clip.",
    "third_party_audio": "Other creators' audio.",
    "casino_gameplay": "Gambling play.",
    "needs_context": "Needs earlier context.",
    "score": "How good.\n- 1: nothing\n- 2: something",
    "category": "Kind.\n- fail: a fail\n- joke: a joke: with a colon",
}


def test_questions():
    qs = gate.questions(SECTIONS)
    assert qs["has_clip"] == {"type": "noul", "instructions": "There is a clip."}
    assert qs["score"] == {"type": "score", "instructions": "How good.",
                           "criteria": ["1: nothing", "2: something"]}
    assert qs["category"]["criteria"] == {"fail": "a fail", "joke": "a joke: with a colon"}
    with pytest.raises(ClipperError, match="unknown question"):
        gate.questions({**SECTIONS, "mood": "x"})
    with pytest.raises(ClipperError, match="missing ## has_clip"):
        gate.questions({k: v for k, v in SECTIONS.items() if k != "has_clip"})


def test_the_shipped_prompt_parses():
    from pathlib import Path

    from clipper.prompts import read_sections

    path = Path(__file__).parent.parent / "prompts" / "gate_v5.md"
    qs = gate.questions({k.removeprefix("q_"): v for k, v in read_sections(path, "q", {}).items()})
    assert len(qs["score"]["criteria"]) == 10
    assert set(qs["category"]["criteria"]) == {"fail", "win", "joke", "insight", "reaction", "hot_take"}


def test_flatten_and_passes():
    answers = {"has_clip": {"type": "noul", "noul": 0.8},
               "third_party_audio": {"type": "noul", "noul": 0.1},
               "casino_gameplay": {"type": "noul", "noul": 0.0},
               "needs_context": {"type": "noul", "noul": 0.2},
               "score": {"type": "score", "score": 5.5, "confidence": 0.5},
               "category": {"type": "choice", "choice": "joke", "confidence": 0.9}}
    row = {"gate": gate.flatten(answers)}
    assert row["gate"]["score"] == 6.5  # level 0 is Pass A's 1
    assert row["gate"]["category"] == "joke"
    assert gate.passes(row, 0.5) and not gate.passes(row, 0.9)
    row["gate"]["casino_gameplay"] = 0.6  # a hard rejection closes the gate
    assert not gate.passes(row, 0.5)


def g(has_clip, score=3.0):
    return {"has_clip": has_clip, "third_party_audio": 0, "casino_gameplay": 0,
            "needs_context": 0, "score": score, "category": "joke"}


def test_report():
    rows = [
        {"id": "w01", "status": "kept", "score": 7, "category": "joke", "pick": True,
         "gate": g(0.9, 8), "api": {"usd": 0.0001, "latency_s": 0.5}, "a_usd": 0.001},
        {"id": "w02", "status": "kept", "score": 5, "category": "fail", "pick": True,
         "gate": g(0.3, 4), "api": {"usd": 0.0001, "latency_s": 0.7}, "a_usd": 0.001},
        {"id": "w03", "status": "no_clip", "score": 0, "category": "joke", "pick": False,
         "gate": g(0.1, 1), "api": {"usd": 0.0001, "latency_s": 0.6}, "a_usd": 0.001},
        {"id": "w04", "status": "no_clip", "score": 0, "category": "joke", "pick": False,
         "skipped": "window not rebuilt"},
    ]
    rep = gate.report([{"stream_id": "s1", "rows": rows}], {("s1", "w01"): 900, ("s1", "w02"): 10})
    assert (rep["windows"], rep["skipped"], rep["haiku_kept"], rep["picks"], rep["with_views"]) == (3, 1, 2, 2, 2)
    at = {t["t"]: t for t in rep["thresholds"]}
    assert at[0.5] == {"t": 0.5, "pass": 0.33, "kept": 0.5, "picks": 0.5, "top_views": 1.0}
    assert at[0.2]["picks"] == 1.0
    assert rep["agree_0.5"] == 0.67
    assert rep["category_agree"] == 0.5
    assert rep["lost_picks_0.5"] == ["s1/w02 has_clip 0.30, 10 views"]
    assert rep["gate_usd"] == 0.0003 and rep["haiku_usd"] == 0.003
    assert "windows through" in gate.report_text(rep)


def test_spearman():
    assert gate.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert gate.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert gate.spearman([1, 1, 1], [1, 2, 3]) is None  # no spread
    assert gate.spearman([1, 2], [1, 2]) is None        # too few


def test_decision_models_only_decide(monkeypatch):
    m = llm.LLM()
    assert llm.split("typesafe:jev-latest") == ("typesafe", "jev-latest")
    with pytest.raises(ClipperError, match="decision model"):
        m.text_call("typesafe:jev-latest", "", "hi", "low")
    with pytest.raises(ClipperError, match="not a decision model"):
        m.decide("claude-haiku-5-5", "x", {})
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ClipperError, match="TYPESAFE_API_KEY"):
        m.decide("typesafe:jev-latest", "x", {})


def test_decide_parses_the_response(monkeypatch):
    class R:
        status_code, ok, headers = 200, True, {}

        @staticmethod
        def json():
            return {"model": "jev-1.14.0", "answers": {"a": {"type": "noul", "noul": 0.9}},
                    "usage": {"input_tokens": 1000, "output_tokens": 5}}

    sent = {}

    def post(url, json, headers, timeout):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return R()

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    m = llm.LLM()
    answers, meta = m.decide("typesafe:jev-latest", "state", {"a": {"type": "noul", "instructions": "?"}})
    assert answers == {"a": {"type": "noul", "noul": 0.9}}
    assert meta["model"] == "typesafe:jev-1.14.0" and meta["input_tokens"] == 1000
    assert sent["url"] == "https://api.typesafe.ai/v1/systemone"
    assert sent["body"]["model"] == "jev-latest" and sent["auth"] == "Bearer k"
    assert m.cost == pytest.approx(1000 * 0.042 / 1e6)
