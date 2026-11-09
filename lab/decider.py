"""Jev lab: one way to ask typed questions of any model, so every test can be rerun on others.

A decision model (typesafe:...) answers through LLM.decide(). Any other model string (Claude,
openai:, gemini:, local:, a future openrouter: provider) gets the same state and questions as
one structured-output call and answers in the same shape: a probability per yes/no question,
a level per score, an option per choice. Rows record which model answered, so results from
different models sit side by side in the same data files (one file per variant and model).
"""
from __future__ import annotations

import json
import re

from clipper.llm import LLM, split

try:
    from clipper.llm import DECIDERS
except ImportError:  # main's llm.py, which has no Jev provider: other models only
    DECIDERS = ()

SYSTEM = """You answer typed questions about the STATE below, each on its own.
- yes/no questions: the probability (0 to 1) that the answer is yes.
- score questions: the index (0 = first level) of the level that fits best.
- choice questions: the name of the option that fits best.
Answer every question. No other text."""


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", model.lower()).strip("-")


def _schema(questions: dict) -> dict:
    props = {}
    for name, q in questions.items():
        if q["type"] == "noul":
            props[name] = {"type": "number"}
        elif q["type"] == "score":
            props[name] = {"type": "integer"}
        elif q["type"] == "choice":
            props[name] = {"type": "string", "enum": list(q["criteria"])}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _prompt(state, questions: dict) -> str:
    qs = []
    for name, q in questions.items():
        line = f"- {name} ({'yes/no' if q['type'] == 'noul' else q['type']}): {q.get('instructions') or ''}"
        if q["type"] == "score":
            line += "\n  levels: " + " | ".join(f"{i}: {c}" for i, c in enumerate(q["criteria"]))
        elif q["type"] == "choice":
            line += "\n  options: " + " | ".join(f"{k}: {v}" for k, v in q["criteria"].items())
        qs.append(line)
    st = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, indent=1)
    return f"<state>\n{st}\n</state>\n\nQuestions:\n" + "\n".join(qs)


def ask(llm: LLM, model: str, state, questions: dict, effort: str = "low") -> tuple[dict, dict]:
    """({name: number or choice}, meta). Scores come back as the level number (Jev's is an
    expected level, fractional; an LLM's a whole one)."""
    if split(model)[0] in DECIDERS:
        answers, meta = llm.decide(model, state, questions)
        out = {}
        for k, a in answers.items():
            out[k] = a.get("noul", a.get("score", a.get("choice")))
        return out, meta
    out, meta = llm.json_call(model, SYSTEM, _prompt(state, questions), _schema(questions), effort)
    if out is None:
        raise RuntimeError(f"{model} refused")
    return out, meta
