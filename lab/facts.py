"""Jev lab: could Jev screen an archive's transcript before Haiku reads it for facts?

Cuts every item `investigate facts` read (archive/jer) into SIZE-second pieces, asks Jev one
noul per piece (does it hold a checkable claim), and compares with where Haiku's verified facts
fell (fact "t"). Report: at each threshold, the share of pieces sent on and of facts kept.

    python lab/facts.py [--size 60] [--subject jer] [--model M]
"""
from __future__ import annotations

import argparse
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from decider import ask, slug
from sklearn.metrics import roc_auc_score

from clipper import config
from clipper.llm import LLM

DATA = Path(__file__).parent / "data"
MODEL = "typesafe:jev-1.14.0"

QUESTIONS = {
    "claim": {"type": "noul", "instructions":
              "In `transcript`, the speaker says something checkable about himself or a named "
              "person or company: a claim of fact, money or a number, a plan, a promise, a legal, "
              "business, health or relationship statement, or a tease of something coming."},
    "about_self": {"type": "noul", "instructions":
                   "In `transcript`, the speaker states a fact about his own life, money, business, "
                   "plans, health or relationships."},
    "filler": {"type": "noul", "instructions":
               "`transcript` is only filler: greetings, sponsor reads, asking for likes or "
               "subscriptions, reading a clip or article aloud with no comment of his own."},
}


def pieces(idir: Path, dur: float, size: int) -> list[str]:
    p = idir / "words.jsonl"
    words = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
    n = max(1, math.ceil(dur / size))
    out = [[] for _ in range(n)]
    for w in words:
        k = int(w["start"] // size)
        if k < n:
            out[k].append((w.get("word") or "").strip())
    return [" ".join(x) for x in out]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=60)
    ap.add_argument("--subject", default="jer")
    ap.add_argument("--model", default=MODEL, help="any clipper model string; default Jev (pinned)")
    args = ap.parse_args()
    cfg = config.load()
    root = Path(cfg["paths"]["archive"]) / args.subject
    out = DATA / (f"facts_{args.subject}_{args.size}.jsonl" if args.model == MODEL
                  else f"facts_{args.subject}_{args.size}__{slug(args.model)}.jsonl")
    done = {(j["item"], j["k"]) for j in map(json.loads, open(out, encoding="utf-8"))} if out.exists() else set()
    todo, truth = [], {}
    for p in sorted((root / "investigate" / "facts").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        ps = pieces(root / d["item"], d["duration_s"] or 0, args.size)
        hits = {int(f["t"] // args.size) for f in d["facts"]}
        for k, text in enumerate(ps):
            truth[(d["item"], k)] = (k in hits, len(text))
            if text.strip() and (d["item"], k) not in done:
                todo.append((d, k, text))
    llm, lock = LLM(cfg), threading.Lock()

    def one(t) -> None:
        d, k, text = t
        try:
            ans, meta = ask(llm, args.model, {"video_title": d["title"], "date": d["date"], "transcript": text},
                                   QUESTIONS)
        except Exception as e:  # noqa: BLE001
            print(d["item"], k, e)
            return
        rec = {"item": d["item"], "k": k, **ans,
               "tokens": meta.get("input_tokens")}
        with lock, open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(one, todo))

    rows = [json.loads(x) for x in open(out, encoding="utf-8")]
    y = np.array([truth[(r["item"], r["k"])][0] for r in rows])
    toks = sum(r["tokens"] or 0 for r in rows)
    print(f"{len(rows)} pieces of {args.size} s, {y.mean():.0%} hold a fact; {toks:,} input tokens")
    words = np.array([truth[(r["item"], r["k"])][1] for r in rows])
    for name, s in [("claim", [r["claim"] for r in rows]), ("about_self", [r["about_self"] for r in rows]),
                    ("1-filler", [1 - r["filler"] for r in rows]),
                    ("max(claim,self)", [max(r["claim"], r["about_self"]) for r in rows]),
                    ("length (free)", words)]:
        s = np.array(s, float)
        print(f"\n{name}: AUC {roc_auc_score(y, s):.3f}")
        for keep in (0.95, 0.9, 0.8):
            t = np.quantile(s[y], 1 - keep)  # the cutoff that keeps this share of fact pieces
            sent = (s >= t).mean()
            print(f"  keep {keep:.0%} of fact pieces -> send {sent:.0%} of pieces "
                  f"({(words * (s >= t)).sum() / words.sum():.0%} of the words)")


if __name__ == "__main__":
    main()
