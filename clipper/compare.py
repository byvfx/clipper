"""`clipper compare`: another model on a stage that already ran, side by side (#72).

  clipper compare score <stream> --model openrouter:openai/gpt-5-mini [--pass a|b|ab]

Scores the stream again with the candidate model on the same windows and the prompt version
it was scored with, never touching candidates.json, and reports how the two agree and how
each did against the review decisions (review.json, synced from Kitsu). Written to
<stream>/compare/score_<model>_<pass>.json and .md.

  --pass a   the candidate judges every window (Pass A); Pass B stays on the stream's model,
             so the picks show what the Pass A difference does downstream (the default)
  --pass b   the stream's own Pass A results; the candidate ranks them (Pass B, one call)
  --pass ab  the candidate does both

The summary and transcript scan stay as the stream cached them: what is compared is the
judgments, not the windows. --dry-run prints the estimate; a run whose estimate is over
--budget is refused.
"""
from __future__ import annotations

import copy
import math
import re
import time
from collections import Counter
from pathlib import Path

from . import jsonstate, llm
from .config import for_stream
from .errors import ClipperError
from .llm import Claude, preflight
from .prompts import Prompts
from .scoring import file_sha
from .states import Decision

STATUSES = ("kept", "no_clip", "rejected", "refused", "error")
# Pick and Short of the same moment share a candidate id; the stronger decision speaks for it.
DECISION_RANK = [Decision.APPROVED, Decision.CHANGES, Decision.REJECTED, Decision.PENDING]
A_OUT_TOKENS, B_OUT_TOKENS, B_TOKENS_PER_CANDIDATE = 600, 3000, 400


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", model.lower()).strip("-")


def baseline_models(base: dict, cfg: dict) -> tuple[str, str]:
    """The models that made candidates.json (recorded per result), else the config's."""
    a = Counter(r.get("model") for r in base.get("pass_a", []) if r.get("source") != "hand" and r.get("model"))
    sc = cfg["scoring"]
    return (a.most_common(1)[0][0] if a else sc["model_a"]), (base.get("pass_b") or {}).get("model") or sc["model_b"]


def decisions(review: dict) -> dict[str, str]:
    """{candidate id: decision} for the picks and Shorts in review.json."""
    out: dict[str, str] = {}
    for r in review.values():
        if r.get("kind") not in ("pick", "short") or not r.get("id"):
            continue
        d = r.get("decision", Decision.PENDING)
        old = out.get(r["id"])
        if old is None or DECISION_RANK.index(d) < DECISION_RANK.index(old):
            out[r["id"]] = d
    return out


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def pass_a_agreement(base_a: list[dict], cand_a: list[dict]) -> dict:
    """How two Pass A runs over the same windows agree: a status table (baseline -> candidate),
    and for windows both kept, the score difference and how much the clips overlap."""
    by_id = {r["id"]: r for r in base_a}
    table: dict[str, dict[str, int]] = {s: dict.fromkeys(STATUSES, 0) for s in STATUSES}
    diffs, overlaps = [], []
    for c in cand_a:
        b = by_id.get(c["id"])
        if not b or b["status"] not in table or c["status"] not in table:
            continue
        table[b["status"]][c["status"]] += 1
        if b["status"] == c["status"] == "kept":
            diffs.append(abs(b.get("score", 0) - c.get("score", 0)))
            overlaps.append(iou((b["in_s"], b["out_s"]), (c["in_s"], c["out_s"])))
    n = sum(sum(row.values()) for row in table.values())
    same = sum(table[s][s] for s in STATUSES)
    return {"windows": n, "same_status": same, "table": table, "both_kept": len(diffs),
            "mean_score_diff": round(sum(diffs) / len(diffs), 2) if diffs else None,
            "mean_clip_overlap": round(sum(overlaps) / len(overlaps), 2) if overlaps else None}


def pick_rows(base_picks: list[dict], cand_picks: list[dict], decided: dict[str, str],
              base_results: dict[str, dict], cand_results: dict[str, dict]) -> list[dict]:
    """One row per moment either side picked: the baseline's order first, then the
    candidate's own; each side's rank and title, the clip's span (the baseline's where it
    picked the moment), and the review decision if there is one."""
    b = {p["id"]: (i, p) for i, p in enumerate(base_picks, 1)}
    c = {p["id"]: (i, p) for i, p in enumerate(cand_picks, 1)}
    order = [p["id"] for p in base_picks] + [p["id"] for p in cand_picks if p["id"] not in b]
    rows = []
    for pid in order:
        r = (base_results if pid in b else cand_results).get(pid, {})
        rows.append({"id": pid, "in_s": r.get("in_s"), "out_s": r.get("out_s"),
                     "base_rank": b[pid][0] if pid in b else None,
                     "base_title": b[pid][1].get("title", "") if pid in b else "",
                     "cand_rank": c[pid][0] if pid in c else None,
                     "cand_title": c[pid][1].get("title", "") if pid in c else "",
                     "decision": decided.get(pid, "")})
    return rows


def against_review(picks: list[dict], decided: dict[str, str]) -> dict:
    """Of the moments review decided on, how many this side picked, by decision."""
    ids = {p["id"] for p in picks}
    out = {}
    for d in (Decision.APPROVED, Decision.CHANGES, Decision.REJECTED):
        of = [i for i, x in decided.items() if x == d]
        out[str(d)] = {"picked": sum(i in ids for i in of), "of": len(of)}
    return out


def report_md(res: dict) -> str:
    """The comparison for a person to read."""
    base, cand, ag = res["baseline"], res["candidate"], res.get("pass_a_agreement")
    lines = [f"# {res['stream']}: {res['which']} — {base['label']} vs {cand['label']}", "",
             (f"Prompt {res['prompt_version']}, pass {res['pass']}. Candidate ${cand['cost_usd']:.3f} "
             f"(estimated ${res['estimate_usd']:.3f}) in {cand['seconds']:.0f} s; "
             f"baseline scoring cost ${base['cost_usd']:.3f} in all."), ""]
    for note in res.get("notes", []):
        lines.append(f"- {note}")
    if res.get("notes"):
        lines.append("")
    if ag:
        lines += ["## Pass A", "",
                  (f"{ag['same_status']} of {ag['windows']} windows got the same verdict. "
                  f"Both kept {ag['both_kept']}: mean score difference {ag['mean_score_diff']}, "
                  f"mean clip overlap {ag['mean_clip_overlap']}."), "",
                  "| baseline \\ candidate | " + " | ".join(STATUSES) + " |",
                  "|---|" + "---|" * len(STATUSES)]
        for s in STATUSES:
            lines.append(f"| {s} | " + " | ".join(str(ag["table"][s][t]) for t in STATUSES) + " |")
        lines.append("")
    lines += ["## Picks", "", "| id | time | baseline | candidate | review |", "|---|---|---|---|---|"]
    for r in res["picks"]:
        t = f"{r['in_s']:.0f}-{r['out_s']:.0f} s" if r["in_s"] is not None else ""
        side = [f"{r[k + '_rank']}. {r[k + '_title']}" if r[k + "_rank"] else "" for k in ("base", "cand")]
        lines.append(f"| {r['id']} | {t} | {side[0]} | {side[1]} | {r['decision'].replace('_', ' ')} |")
    lines += ["", "## Against review", ""]
    for d, label in ((Decision.APPROVED, "approved"), (Decision.CHANGES, "sent back for changes"),
                     (Decision.REJECTED, "rejected")):
        b, c = base["review"][str(d)], cand["review"][str(d)]
        if b["of"]:
            lines.append(f"- {label}: baseline picked {b['picked']} of {b['of']}, candidate {c['picked']}")
    if not any(base["review"][str(d)]["of"] for d in DECISION_RANK[:3]):
        lines.append("- no review decisions on this stream's picks yet")
    return "\n".join(lines) + "\n"


def score_windows(sdir: Path, cfg: dict, prompts: Prompts, base_a: list[dict]) -> tuple[list[dict], list[str]]:
    """The windows Pass A saw: windows.json plus the transcript-scan windows rebuilt from the
    cached scan. Returns (windows in candidates.json's order, ids that couldn't be rebuilt)."""
    from .scoring import load_chat, load_signals, scan_windows

    windows = jsonstate.load(sdir / "windows.json")["windows"]
    scan = jsonstate.read(sdir / f"scan_{prompts.version}.json")
    if scan:
        meta = jsonstate.load(sdir / "meta.json")
        windows = windows + scan_windows(scan, windows, load_signals(sdir), load_chat(sdir, meta),
                                         cfg["scoring"], cfg["signals"]["chat_lag_s"])
    by_id = {w.get("id") or f"w{w['rank']:02d}": w for w in windows}
    out, missing = [], []
    for r in base_a:
        if r.get("source") == "hand":
            continue
        w = by_id.get(r["id"])
        if w and [w["start_s"], w["end_s"]] == list(r["window"]):
            out.append(w)
        else:
            missing.append(r["id"])
    return out, missing


def estimate(cfg: dict, prompts: Prompts, which: str, windows: list[dict], n_kept: int,
             model_a: str, model_b: str, words: list[dict], sig: dict) -> float:
    from .scoring import pass_a_user

    usd = 0.0
    if "a" in which:
        system = prompts.get("score_a")
        tokens_in = sum(len(system) + len(pass_a_user(w, words, sig, cfg["scoring"]["lead_in_s"]))
                        for w in windows) / 4
        usd += llm.estimate(cfg, model_a, tokens_in, A_OUT_TOKENS * len(windows))
    tokens_b = len(prompts.get("score_b")) / 4 + B_TOKENS_PER_CANDIDATE * n_kept + 3000
    return usd + llm.estimate(cfg, model_b, tokens_b, B_OUT_TOKENS)


def compare_score(sdir: Path, cfg: dict, model: str, which: str = "a", budget: float = 2.0,
                  dry_run: bool = False) -> dict:
    """Score `sdir` again with `model` in the passes `which` names; see the module doc."""
    from .scoring import load_signals, run_pass_a, run_pass_b, stream_summary
    from .transcript import load_words

    if which not in ("a", "b", "ab"):
        raise ClipperError(f"--pass {which}: a, b or ab")
    cfg = copy.deepcopy(for_stream(cfg, sdir))
    base = jsonstate.read(sdir / "candidates.json")
    if not base:
        raise ClipperError(f"{sdir.name} isn't scored yet (no candidates.json): nothing to compare with")
    sc = cfg["scoring"]
    sc["prompt_version"] = base.get("prompt_version") or sc["prompt_version"]
    prompts = Prompts(cfg)
    base_a_model, base_b_model = baseline_models(base, cfg)
    model_a = model if "a" in which else base_a_model
    model_b = model if "b" in which else base_b_model
    if model_a == base_a_model and model_b == base_b_model:
        raise ClipperError(f"{model} is what scored {sdir.name} already ({which}): pick another model")
    sc["model_a"], sc["model_b"] = model_a, model_b

    base_a = base.get("pass_a", [])
    base_b = base.get("pass_b") or {}
    notes = []
    if base.get("windows_sha") not in (None, file_sha(sdir / "windows.json")):
        notes.append("windows.json changed since the stream was scored: only windows that still match are compared")
    words, sig = load_words(sdir), load_signals(sdir)
    windows, missing = score_windows(sdir, cfg, prompts, base_a) if "a" in which else ([], [])
    if missing:
        notes.append(f"{len(missing)} window(s) not rebuilt, left out: {', '.join(missing[:8])}")
    base_kept = [r for r in base_a if r["status"] == "kept"]
    est = estimate(cfg, prompts, which, windows, len(base_kept), model_a, model_b, words, sig)
    # OpenRouter charges each call at its reported cost, but the estimate needs a price.
    unpriced = sorted(m for m in {model_a, model_b} if llm.price_of(llm.prices(cfg), m) is None)
    print(f"compare {sdir.name}: pass {which}, Pass A {model_a}, Pass B {model_b}, prompt {prompts.version}; "
          f"{len(windows)} windows" + (f" ({len(missing)} not rebuilt)" if missing else "")
          + f"; estimate ${est:.3f}"
          + (f" (no [llm] prices for {', '.join(unpriced)}: counted as $0)" if unpriced else ""))
    if dry_run:
        return {}
    if est > budget:
        raise ClipperError(f"estimate ${est:.2f} is over --budget ${budget:.2f}; raise it to go ahead")

    claude = Claude(cfg)
    preflight(claude, model)
    t0 = time.time()
    out_dir = sdir / "compare"
    out_dir.mkdir(exist_ok=True)
    if "a" in which:
        cand_a = run_pass_a(windows, words, sig, cfg, claude, prompts)
        kept = [r for r in cand_a if r["status"] == "kept"]
    else:
        cand_a, kept = [], base_kept
    cost_a = claude.cost
    # The stream's cached summary; made with the stream's own Pass A model if it's missing.
    cached = sdir / f"summary_{prompts.version}.json"
    if cached.exists():
        summary = jsonstate.load(cached)
    else:
        sum_cfg = copy.deepcopy(cfg)
        sum_cfg["scoring"]["model_a"] = base_a_model
        summary = stream_summary(out_dir, words, math.ceil(jsonstate.load(sdir / "meta.json")["duration_s"]),
                                 sum_cfg, claude, prompts)
    cand_b = run_pass_b(kept, summary, words, cfg, claude, prompts) if kept else {"picks": []}
    base_results = {r["id"]: r for r in base_a}

    decided = decisions(jsonstate.read(sdir / "review.json", {}))
    res = {
        "stream": sdir.name, "which": "score", "pass": which, "prompt_version": prompts.version,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "estimate_usd": round(est, 4), "notes": notes,
        "baseline": {"label": base_a_model if which == "a" else base_b_model if which == "b"
                     else f"{base_a_model} + {base_b_model}",
                     "model_a": base_a_model, "model_b": base_b_model,
                     "cost_usd": base.get("cost_usd", 0.0), "picks": base_b.get("picks", []),
                     "review": against_review(base_b.get("picks", []), decided)},
        "candidate": {"label": model, "model_a": model_a, "model_b": model_b,
                      "cost_usd": round(claude.cost, 4), "cost_pass_a_usd": round(cost_a, 4),
                      "seconds": round(time.time() - t0, 1), "pass_a": cand_a, "pass_b": cand_b,
                      "review": against_review(cand_b.get("picks", []), decided)},
        "pass_a_agreement": pass_a_agreement(base_a, cand_a) if cand_a else None,
        "picks": pick_rows(base_b.get("picks", []), cand_b.get("picks", []), decided, base_results,
                           {r["id"]: r for r in cand_a} or base_results),
    }
    stem = out_dir / f"score_{slug(model)}_{which}"
    jsonstate.write(stem.with_suffix(".json"), res)
    md = report_md(res)
    stem.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {stem}.json, .md")
    return res
