"""Jev lab T9: a pre-upload check on Shorts.

Asks atomic questions about a Short as it would stand before upload (its cut's speaker-tagged
transcript, a few seconds of lead-in, its title and hook) and checks them two ways:

1. labelled: Haiku's Shorts that Pass B dropped for a safety reason (its written reason says
   context, misrepresentation, someone else's audio or gambling) are the positives, Pass B's
   picks the negatives. Same cut and text source for both (Haiku's short_in/out, title, hook).
2. live: every approved Short in review.json with its final cut (shorts.json) and final YouTube
   title and description, ranked by the check, top flags printed to read by hand.

    python lab/guard.py [--model M] [--show 12]
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from decider import ask, slug
from sklearn.metrics import roc_auc_score

from clipper import config
from clipper.config import for_stream
from clipper.llm import LLM
from clipper.prompts import Prompts
from clipper.transcript import load_words, transcript_lines

DATA = Path(__file__).parent / "data"
MODEL = "typesafe:jev-1.14.0"
LEAD_S = 8

REASONS = {  # Pass B's reasons that a pre-upload check should catch
    "third_party": r"source (video|audio|clip)|video's audio|clip's audio|played (video|clip)|outside voice|"
                   r"carried by (the )?(clip|video)|third.party|someone else's|rather than (don|andy|ppp)",
    "context": r"context|earlier|lost without|inside joke|outside the window",
    "misrep": r"misrepresent|unverified|allegation|defam|look worse|misread",
    "gambling": r"casino|gambl|slots?\b|\bbets?\b",
}


def questions(people: str) -> dict:
    def noul(t: str) -> dict:
        return {"type": "noul", "instructions": t}

    return {
        "casino": noul("The Short in `transcript` is about gambling play (slots, roulette, blackjack, poker "
                       "hands, sports bets, spins, wins or losses on a casino or betting site) or promotes "
                       "a gambling site."),
        "outside_audio": noul(f"Most of what makes the Short in `transcript` work is said by someone other "
                              f"than {people}: a video, clip or stream being played (lines tagged [?] or "
                              f"with another name)."),
        "needs_context": noul("A fan of the show who did not watch this stream would be lost in `transcript` "
                              "without something said earlier in the stream."),
        "misrepresents": noul(f"Seen alone, the Short in `transcript` could make {people} or someone they "
                              "name look worse than they are, or states an unproven accusation as fact."),
        "title_mismatch": noul("`title` or `hook` claims something that does not happen or is not said in "
                               "`transcript`."),
        "misattributed": noul("`title` or `hook` credits words or an action to a different person than the "
                              "one who says or does it in `transcript` (speaker tags show who talks)."),
        "starts_mid": noul("The first line of `transcript` starts in the middle of a thought, so the "
                           "setup the Short needs is missing."),
        "ends_early": noul("`transcript` ends before its payoff lands or in the middle of a sentence."),
    }


def state(words, a: float, b: float, title: str, hook: str, about: str, people: str, desc: str = "") -> dict:
    st = {"show": about, "show_people": people, "title": title, "hook": hook,
          "lead_in": "\n".join(transcript_lines(words, max(0, a - LEAD_S), a)),
          "transcript": "\n".join(transcript_lines(words, a, b))}
    if desc:
        st["description"] = desc
    return st


def collect(cfg: dict) -> list[dict]:
    """The labelled set and the live set, with their states."""
    out = []
    for d in sorted(Path(cfg["paths"]["streams"]).iterdir()):
        cpath = d / "candidates.json"
        if not cpath.exists():
            continue
        c = json.loads(cpath.read_text(encoding="utf-8"))
        pb = c.get("pass_b") or {}
        if not pb:
            continue
        v = Prompts(for_stream(cfg, d)).vars
        about, people = v["stream_context"].split(". ")[0] + ".", v["cast"]
        words = None
        by_id = {a["id"]: a for a in c["pass_a"]}
        rows = [(p["id"], 0, "") for p in pb.get("picks", [])]
        for r in pb.get("rejected") or []:
            cats = [k for k, rx in REASONS.items() if re.search(rx, r.get("reason", ""), re.I)]
            if cats:
                rows.append((r["id"], 1, ",".join(cats) + ": " + r.get("reason", "")))
        for pid, y, why in rows:
            a = by_id.get(pid)
            if not a or not a.get("short_out_s"):
                continue
            words = words or load_words(d)
            out.append({"set": "labelled", "stream": d.name, "id": pid, "y": y, "why": why,
                        "score": a.get("score") or 0,
                        "state": state(words, a["short_in_s"], a["short_out_s"], a.get("title", ""),
                                       a.get("hook", ""), about, people)})
        rv_path, sh_path = d / "review.json", d / "shorts.json"
        if rv_path.exists() and sh_path.exists():
            rv, sh = (json.loads(p.read_text(encoding="utf-8")) for p in (rv_path, sh_path))
            for key, r in rv.items():
                if r.get("kind") != "short" or r.get("status") != "approved" or key not in sh:
                    continue
                f = r.get("fields") or {}
                words = words or load_words(d)
                out.append({"set": "live", "stream": d.name, "id": key, "url": f.get("YouTube URL"),
                            "state": state(words, sh[key]["in_s"], sh[key]["out_s"], f.get("YouTube title") or "",
                                           f.get("Hook") or "", about, people, f.get("YouTube description") or "")})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--show", type=int, default=12, help="top live flags to print")
    args = ap.parse_args()
    cfg = config.load()
    out = DATA / ("guard.jsonl" if args.model == MODEL else f"guard__{slug(args.model)}.jsonl")
    done = {(j["set"], j["stream"], j["id"]): j for j in map(json.loads, open(out, encoding="utf-8"))} \
        if out.exists() else {}
    items = collect(cfg)
    llm, lock = LLM(cfg), threading.Lock()

    def one(it: dict) -> None:
        if (it["set"], it["stream"], it["id"]) in done:
            return
        try:
            ans, meta = ask(llm, args.model, it["state"], questions(it["state"]["show_people"]))
        except Exception as e:  # noqa: BLE001
            print(it["stream"], it["id"], e)
            return
        rec = {k: v for k, v in it.items() if k != "state"} | {"a": ans, "tokens": meta.get("input_tokens")}
        with lock:
            done[(it["set"], it["stream"], it["id"])] = rec
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(one, items))
    recs = list(done.values())
    lab = [r for r in recs if r["set"] == "labelled"]
    live = [r for r in recs if r["set"] == "live"]
    toks = sum(r["tokens"] or 0 for r in recs)
    y = np.array([r["y"] for r in lab])
    print(f"labelled: {len(lab)} ({y.sum()} dropped by Pass B for a safety reason, {len(lab) - y.sum()} picks); "
          f"live: {len(live)} approved Shorts; {toks:,} input tokens")
    qs = list(questions("x"))
    risk = lambda r: max(r["a"][q] for q in ("outside_audio", "needs_context", "misrepresents", "casino"))  # noqa: E731
    print("\nAUC, dropped-for-safety vs picked:")
    print(f"  Haiku score (low = risky)  {roc_auc_score(y, [-r['score'] for r in lab]):.3f}")
    for q in qs:
        print(f"  {q:16s}           {roc_auc_score(y, [r['a'][q] for r in lab]):.3f}")
    print(f"  max(4 risk nouls)          {roc_auc_score(y, [risk(r) for r in lab]):.3f}")
    for cat in REASONS:
        yc = np.array([r["y"] == 1 and cat in r["why"].split(":")[0] for r in lab])
        m = (y == 0) | yc
        if yc.sum() >= 3:
            q = {"third_party": "outside_audio", "context": "needs_context", "misrep": "misrepresents",
                 "gambling": "casino"}[cat]
            print(f"  {cat} ({yc.sum()}) by {q}: {roc_auc_score(yc[m], np.array([r['a'][q] for r in lab])[m]):.3f}")
    print("\nlive Shorts: share flagged at p >= 0.5 / 0.7")
    for q in qs:
        p = np.array([r["a"][q] for r in live])
        print(f"  {q:16s} {np.mean(p >= 0.5):4.0%} {np.mean(p >= 0.7):4.0%}  (median {np.median(p):.2f})")
    print(f"\ntop {args.show} live Shorts by risk:")
    for r in sorted(live, key=lambda r: -risk(r))[:args.show]:
        top = sorted(((r["a"][q], q) for q in qs), reverse=True)[:3]
        print(f"  {r['stream']}/{r['id']} {r.get('url')}: " + ", ".join(f"{q} {p:.2f}" for p, q in top))


if __name__ == "__main__":
    main()
