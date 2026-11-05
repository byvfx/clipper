"""Jev lab: ask Jev one variant's questions about every window in data/windows.jsonl.

    python lab/run.py VARIANT [--model M] [--limit N] [--streams a,b]

Answers are cached in data/<variant>.jsonl for Jev (data/<variant>__<model>.jsonl for any other
--model), one row per stream/id; a rerun asks only the windows not there yet. A variant is a
state builder plus a question set (below); lab/decider.py asks them of any model.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from decider import ask, slug

from clipper import config
from clipper.config import for_stream
from clipper.llm import LLM
from clipper.prompts import Prompts

DATA = Path(__file__).parent / "data"
MODEL = "typesafe:jev-1.14.0"  # pinned: thresholds tuned on one version

# ---------- per-show context, short ----------

_ctx: dict[str, dict] = {}


def show_ctx(cfg: dict, stream: str) -> dict:
    if stream not in _ctx:
        p = Prompts(for_stream(cfg, Path(cfg["paths"]["streams"]) / stream))
        v = p.vars
        _ctx[stream] = {"cast": v["cast"], "about": v["stream_context"].split(". ")[0] + ".",
                        "regulars": v["known_figures"], "brief": p.get("score_a")}
    return _ctx[stream]


def section(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n?</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


# ---------- states ----------

def state_lean(row: dict, ctx: dict) -> dict:
    return {"show": ctx["about"], "show_people": ctx["cast"],
            "lead_in": section(row["text"], "lead_in"), "window": section(row["text"], "window"),
            "chat_at_peak": section(row["text"], "chat_at_peak")}


_brief: dict[str, str] = {}


def state_brief(row: dict, ctx: dict) -> str:
    """The 11-03 state: the rendered Pass A brief, then exactly what Haiku read."""
    return f"<brief>\n{ctx['brief']}\n</brief>\n\n{row['text']}"


def state_body(row: dict, ctx: dict) -> dict:
    return {"show_people": ctx["cast"], "window": row["body"]}


# ---------- questions ----------

def noul(text: str) -> dict:
    return {"type": "noul", "instructions": text}


def q_gate(ctx: dict) -> dict:
    """Pass A's decisions, self-contained (no brief), situational score levels."""
    people = ctx["cast"]
    viewer = (f"a fan who knows the show's people ({people}) and the regulars it covers "
              f"({ctx['regulars']}) but did not watch this stream")
    return {
        "has_clip": noul(
            f"`window` holds a moment that {viewer} would enjoy as a 15-60 second Short on its "
            "own: a joke or roast that lands, a fail, a reveal, a clean strong opinion, or a "
            f"genuine reaction from {people}. Setup with no payoff does not count."),
        "third_party_audio": noul(
            f"The best moment in `window` is carried by audio from a video, clip or stream being "
            f"played (lines tagged [?] or with a name other than {people}), not by {people}'s own "
            "words."),
        "casino_gameplay": noul(
            "The best moment in `window` is about gambling play (slots, roulette, blackjack, "
            "poker hands, bets, spins, wins or losses on a casino or betting site)."),
        "needs_context": noul(
            f"{viewer[0].upper() + viewer[1:]} would be lost in `window` without something set "
            "up earlier in this same stream."),
        "short_score": {"type": "score", "instructions":
            f"How good the best moment in `window` would be as a Short for {viewer}.",
            "criteria": [
                "Nothing happens: housekeeping, reading donations or chat, tech problems, dead air.",
                "Talk with no point or payoff, or setup that goes nowhere.",
                "Mildly interesting talk, or a small joke only a regular would smile at.",
                "A clear funny or interesting moment with a payoff, but it needs setup or drags.",
                "A self-contained moment with a strong payoff: a big laugh, a sharp roast, a "
                "reveal or a clean hot take.",
                "The stream's standout: a moment fans would share and replay.",
            ]},
    }


def q_atomic(ctx: dict) -> dict:
    """One condition each; combined with weights in code."""
    p = ctx["cast"]
    return {
        "a_payoff": noul("`window` contains a punchline, reveal or payoff that clearly lands."),
        "a_roast": noul(f"{p} mock, roast or insult someone in `window`."),
        "a_hot_take": noul(f"{p} state a strong opinion in one or two clean sentences in `window`."),
        "a_story": noul("A story or anecdote is told from start to finish inside `window`."),
        "a_surprise": noul("Something surprising or unexpected is revealed or happens in `window`."),
        "a_conflict": noul("Two people argue or clash in `window`."),
        "a_emotion": noul(f"{p} show strong genuine emotion (anger, shock, disgust, glee) in `window`."),
        "a_quotable": noul("`window` has one line people would quote or put on a T-shirt."),
        "a_regular": noul(f"The main moment in `window` is about one of these people: {ctx['regulars']}."),
        "a_outside_voice": noul(
            f"The funniest or most important line in `window` is said by someone other than {p} "
            "(a played video or clip, a caller or a guest)."),
        "a_housekeeping": noul(
            "Most of `window` is stream housekeeping: reading donations or super chats, sponsor "
            "reads, technical problems, waiting or saying goodbye."),
        "a_reading_chat": noul(f"In `window`, {p} mostly read chat messages aloud."),
        "a_standalone": noul(
            "Someone who started watching at the start of `window` would follow what is "
            "happening without anything said earlier."),
        "a_chat_laughs": noul("`chat_at_peak` shows chat laughing (LUL, KEKW, LMAO, lol, crying-laughing)."),
        "a_chat_hype": noul("`chat_at_peak` shows chat excited or shocked (caps, W, OMG, clip it)."),
    }


def q_old(ctx: dict) -> dict:
    """The 11-03 questions, prompts/gate_v5.md."""
    from clipper.gate import questions
    from clipper.prompts import read_sections

    if "old_q" not in ctx:
        p = Prompts(config.load())
        s = read_sections(Path(__file__).parent.parent / "prompts" / "gate_v5.md", "q", p.vars)
        ctx["old_q"] = questions({k.removeprefix("q_"): v for k, v in s.items()})
    return ctx["old_q"]


VARIANTS = {
    "brief_old": (state_brief, q_old),
    "lean": (state_lean, lambda c: {**q_gate(c), **q_atomic(c)}),
    "brief": (state_brief, lambda c: {**q_gate(c), **q_atomic(c)}),
    "body": (state_body, lambda c: {**q_gate(c), **{k: v for k, v in q_atomic(c).items()
                                                     if not k.startswith("a_chat")}}),
}



def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=sorted(VARIANTS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--streams", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--model", default=MODEL, help="any clipper model string; default Jev (pinned)")
    args = ap.parse_args()
    cfg = config.load()
    rows = [json.loads(x) for x in open(DATA / "windows.jsonl", encoding="utf-8")]
    if args.streams:
        rows = [r for r in rows if r["stream"] in args.streams.split(",")]
    out = DATA / (f"{args.variant}.jsonl" if args.model == MODEL else f"{args.variant}__{slug(args.model)}.jsonl")
    done = set()
    if out.exists():
        done = {(j["stream"], j["id"]) for j in map(json.loads, open(out, encoding="utf-8"))}
    todo = [r for r in rows if (r["stream"], r["id"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    state_fn, q_fn = VARIANTS[args.variant]
    llm = LLM(cfg)
    lock = threading.Lock()
    errs = 0

    def one(r: dict) -> None:
        nonlocal errs
        ctx = show_ctx(cfg, r["stream"])
        try:
            answers, meta = ask(llm, args.model, state_fn(r, ctx), q_fn(ctx))
        except Exception as e:  # noqa: BLE001
            with lock:
                errs += 1
                print(f"{r['stream']}/{r['id']}: {e}", file=sys.stderr)
            return
        rec = {"stream": r["stream"], "id": r["id"], **answers,
               "tokens": meta.get("input_tokens"), "latency_s": meta.get("latency_s"), "model": meta.get("model", args.model)}
        with lock, open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    for r in todo:  # contexts first, single-threaded
        show_ctx(cfg, r["stream"])
    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, todo))
    print(f"{args.variant}: {len(todo) - errs} asked, {errs} errors, ${llm.cost:.4f}")


if __name__ == "__main__":
    main()
