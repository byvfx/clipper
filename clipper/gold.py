"""`clipper models gold`: Pass A against windows a person has labelled (#72).

  clipper models gold propose [--streams 12]   windows to label, into [paths] model_gold
  clipper models gold confirm labels.json      labels from elsewhere (the label page), by key
  clipper models gold run <model> [--effort none] [--prompt-version v6]

The built-in checks (`clipper models test`) show a model can follow the format; whether it
makes the calls this channel's editor would make needs real windows with the right answer
known. `propose` gathers them from reviewed streams:
  keep           a Short or pick approved in review (confirmed already: you said so)
  third_party, needs_context, casino, no_clip
                 windows the stream's own model rejected or passed on, with its reason as the
                 proposed label (two runs agreeing, where a `compare --repeat` exists)
  ?              windows it kept that were never approved
Each needs a person to confirm or change the label ("confirmed": true in the file, or the
label page, whose rows `confirm` takes: {"<stream>_<id>": {"label": "keep", "note": ""}}).
A proposal never overwrites a label that is confirmed.

`run` scores only the confirmed windows, each stream with its own show's rules and the
candidate model, and reports how often its verdict matches the label, per label (recall: of
the windows that are X, how many it called X; precision: of the windows it called X, how many
are X), next to what the stream's own model said when it scored the stream.
"""
from __future__ import annotations

import copy
import random
from collections import Counter
from pathlib import Path

from . import jsonstate, llm
from .compare import decisions, out_tokens, score_windows, scored_only, slug
from .config import EFFORTS, for_stream
from .errors import ClipperError
from .llm import Claude
from .prompts import Prompts
from .states import Decision

LABELS = ("keep", "third_party", "needs_context", "casino", "no_clip")
# How many of each to propose (keeps come confirmed and are all taken, up to their cap).
QUOTA = {"keep": 24, "third_party": 10, "needs_context": 10, "casino": 6, "no_clip": 6, "?": 10}


def verdict(rec: dict, max_source_s: float) -> str:
    """A Pass A result as one of LABELS ("error" for an error or refusal)."""
    if rec["status"] == "kept":
        return "keep"
    if rec["status"] in ("error", "refused"):
        return "error"
    if rec.get("casino_gameplay"):
        return "casino"
    if rec.get("third_party_audio") or (rec.get("third_party_mode") == "allow_reactions"
                                        and rec.get("source_audio_s", 0) > max_source_s):
        return "third_party"
    return "needs_context" if rec.get("needs_context") else "no_clip"


def excerpt(words: list[dict], rec: dict) -> list[str]:
    from .transcript import transcript_lines

    a, b = rec["window"]
    return transcript_lines(words, a, b)


def stream_proposals(sdir: Path, cfg: dict) -> list[dict]:
    """Every labelable window in one reviewed stream, with a proposed label and why."""
    base = jsonstate.read(sdir / "candidates.json")
    review = jsonstate.read(sdir / "review.json")
    if not base or not review:
        return []
    cfg = for_stream(cfg, sdir)
    max_src = cfg["scoring"]["max_source_audio_s"]
    _, base_a, _ = scored_only(base)
    repeat = jsonstate.read(sdir / "compare" / "score_claude-haiku-5-5-repeat_a.json") or {}
    again = {r["id"]: verdict(r, max_src) for r in (repeat.get("candidate") or {}).get("pass_a", [])}
    decided = decisions(review)
    out = []
    for r in base_a:
        v = verdict(r, max_src)
        if v == "error":
            continue
        item = {"stream": sdir.name, "channel": (jsonstate.read(sdir / "meta.json") or {}).get("channel", ""),
                "id": r["id"], "window": r["window"], "model": r.get("model", ""),
                "model_said": v, "why": r.get("why", ""), "score": r.get("score", 0)}
        if r["status"] == "kept":
            item["short"] = [r.get("short_in_s"), r.get("short_out_s")]
            item["hook"] = r.get("hook", "")
        if decided.get(r["id"]) == Decision.APPROVED:
            out.append({**item, "proposed": "keep", "label": "keep", "confirmed": True,
                        "source": "approved in review"})
        elif v == "keep":
            out.append({**item, "proposed": "?", "label": "", "confirmed": False,
                        "source": "kept by the model, never approved"})
        elif again.get(r["id"], v) == v:
            out.append({**item, "proposed": v, "label": v, "confirmed": False,
                        "source": "the model, twice" if r["id"] in again else "the model"})
    return out


def propose(cfg: dict, streams: int = 12, seed: int = 72) -> dict:
    """Pick windows from the newest reviewed streams into [paths] model_gold, keeping every
    item already there (and its label)."""
    from .transcript import load_words

    path = Path(cfg["paths"]["model_gold"])
    have = jsonstate.read(path) or {"items": []}
    known = {(i["stream"], i["id"]) for i in have["items"]}
    root = Path(cfg["paths"]["streams"])
    dirs = sorted((d for d in root.iterdir() if (d / "review.json").exists() and (d / "candidates.json").exists()),
                  key=lambda d: d.name, reverse=True)[:streams]
    pool: dict[str, list[dict]] = {}
    for d in dirs:
        for p in stream_proposals(d, cfg):
            if (p["stream"], p["id"]) not in known:
                pool.setdefault(p["proposed"], []).append(p)
    rnd = random.Random(seed)
    have_n = Counter(i["proposed"] for i in have["items"])
    added = []
    for label, quota in QUOTA.items():
        cands = pool.get(label, [])
        rnd.shuffle(cands)
        added += cands[:max(0, quota - have_n[label])]
    words: dict[str, list[dict]] = {}
    for p in added:
        if p["stream"] not in words:
            words[p["stream"]] = load_words(root / p["stream"])
        p["transcript"] = excerpt(words[p["stream"]], p)
    have["items"] += sorted(added, key=lambda p: (p["stream"], p["window"][0]))
    jsonstate.write(path, have)
    n = Counter(i["proposed"] for i in have["items"])
    print(f"gold: {len(added)} new proposal(s) from {len(dirs)} stream(s); {len(have['items'])} in {path} ("
          + ", ".join(f"{k} {v}" for k, v in n.most_common()) + f"); "
          f"{sum(not i['confirmed'] for i in have['items'])} wait for a label")
    return have


def confirm(cfg: dict, labels: dict[str, dict]) -> int:
    """Set and confirm labels by "<stream>_<id>"; returns how many changed."""
    path = Path(cfg["paths"]["model_gold"])
    unknown = [k for k, v in labels.items() if v.get("label") not in LABELS]
    if unknown:
        raise ClipperError(f"labels must be one of {', '.join(LABELS)}: {', '.join(unknown[:5])}")
    n = 0
    with jsonstate.update(path, {"items": []}) as data:
        for item in data["items"]:
            new = labels.get(f"{item['stream']}_{item['id']}")
            if new and (item.get("label"), item.get("confirmed"), item.get("note", "")) != (
                    new["label"], True, new.get("note", "")):
                item.update(label=new["label"], confirmed=True, note=new.get("note", ""))
                n += 1
    print(f"gold: {n} label(s) set in {path}")
    return n


def score(gold: list[dict], said: dict[tuple[str, str], str]) -> dict:
    """How the verdicts in `said` ({(stream, id): label}) match the gold labels."""
    rows = [(g["label"], said.get((g["stream"], g["id"]), "error")) for g in gold]
    per = {}
    for lab in LABELS:
        tp = sum(t == lab and p == lab for t, p in rows)
        n_true, n_said = sum(t == lab for t, _ in rows), sum(p == lab for _, p in rows)
        per[lab] = {"n": n_true, "recall": round(tp / n_true, 2) if n_true else None,
                    "precision": round(tp / n_said, 2) if n_said else None}
    hard = sum(t in ("third_party", "casino") and p == "keep" for t, p in rows)
    confusion = Counter(f"{t}>{p}" for t, p in rows if t != p)
    return {"n": len(rows), "right": sum(t == p for t, p in rows),
            "keep_right": sum((t == "keep") == (p == "keep") for t, p in rows), "per_label": per,
            "kept_hard_rule": hard, "confusion": dict(confusion.most_common())}


def run(cfg: dict, model: str, effort: str = "", prompt_version: str = "", budget: float = 2.0,
        dry_run: bool = False) -> dict:
    from .scoring import load_signals, pass_a_user, run_pass_a
    from .transcript import load_words

    if effort and effort not in EFFORTS:
        raise ClipperError(f"--effort {effort}: one of {', '.join(EFFORTS)}")
    path = Path(cfg["paths"]["model_gold"])
    gold = [i for i in (jsonstate.read(path) or {}).get("items", []) if i.get("confirmed") and i.get("label") in LABELS]
    if not gold:
        raise ClipperError(f"no confirmed labels in {path}: clipper models gold propose, then label them")
    root = Path(cfg["paths"]["streams"])
    by_stream: dict[str, list[dict]] = {}
    for g in gold:
        by_stream.setdefault(g["stream"], []).append(g)
    jobs, est = [], 0.0
    for name, items in sorted(by_stream.items()):
        sdir = root / name
        scfg = copy.deepcopy(for_stream(cfg, sdir))
        sc = scfg["scoring"]
        base = jsonstate.load(sdir / "candidates.json")
        base_version = base.get("prompt_version") or sc["prompt_version"]
        sc.update(model_a=model, prompt_version=prompt_version or sc["prompt_version"])
        if effort:
            sc["effort_a"] = effort
        _, base_a, _ = scored_only(base)
        want = {g["id"] for g in items}
        windows, _ = score_windows(sdir, scfg, base_version, [r for r in base_a if r["id"] in want])
        words, sig = load_words(sdir), load_signals(sdir)
        prompts = Prompts(scfg)
        system = prompts.get("score_a")
        est += llm.estimate(cfg, model, sum(len(system) + len(pass_a_user(w, words, sig, sc["lead_in_s"]))
                                            for w in windows) / 4,
                            out_tokens(model, sc["effort_a"], "a") * len(windows))
        jobs.append((sdir, scfg, prompts, windows, words, sig, len(want) - len(windows)))
    missing = sum(j[-1] for j in jobs)
    version = prompt_version or cfg["scoring"]["prompt_version"]
    print(f"gold {model}" + (f" @{effort}" if effort else "") + f" (prompt {version}): {len(gold) - missing} "
          f"labelled windows in {len(jobs)} streams" + (f" ({missing} not rebuilt)" if missing else "")
          + f"; estimate ${est:.3f}")
    if dry_run:
        return {}
    if llm.known_price(cfg, model) is None and llm.split(model)[0] != "local":
        raise ClipperError(f"no price for {model}: add it to [llm.prices] in clipper.toml")
    if est > budget:
        raise ClipperError(f"estimate ${est:.2f} is over --budget ${budget:.2f}; raise it to go ahead")
    claude = Claude(cfg)
    llm.preflight(claude, model)
    said, theirs, results = {}, {}, []
    for sdir, scfg, prompts, windows, words, sig, _ in jobs:
        max_src = scfg["scoring"]["max_source_audio_s"]
        for rec in run_pass_a(windows, words, sig, scfg, claude, prompts):
            said[(sdir.name, rec["id"])] = verdict(rec, max_src)
            results.append({"stream": sdir.name, "id": rec["id"], "said": said[(sdir.name, rec["id"])],
                            "why": rec.get("why") or rec.get("error", "")})
    for g in gold:
        theirs[(g["stream"], g["id"])] = g["model_said"]
    res = {"model": model, "effort": effort, "prompt_version": version, "cost_usd": round(claude.cost, 4),
           "candidate": score(gold, said), "original": score(gold, theirs),
           "original_models": sorted({g["model"] for g in gold}), "windows": results}
    out = path.parent / f"gold_{slug(f'{model} {effort} {version}')}.json"
    jsonstate.write(out, res)
    print(report(res))
    print(f"saved {out}")
    return res


def report(res: dict) -> str:
    c, o = res["candidate"], res["original"]
    lines = [f"{res['model']}" + (f" @{res['effort']}" if res["effort"] else "")
             + f" (prompt {res['prompt_version']}): {c['right']}/{c['n']} labels right, keep-or-not "
             f"{c['keep_right']}/{c['n']}, kept {c['kept_hard_rule']} against a hard rule; ${res['cost_usd']:.3f}",
             (f"  the stream's own model ({', '.join(res['original_models'])}) when it scored them: "
             f"{o['right']}/{o['n']}, keep-or-not {o['keep_right']}/{o['n']}, kept {o['kept_hard_rule']} "
             "against a hard rule (flattering: only what it kept could be approved, and its own reasons "
             "seeded the proposals)"),
             "  label          n   recall  precision   (own model: recall precision)"]

    def pct(x):
        return "  -  " if x is None else f"{x:5.0%}"

    for lab in LABELS:
        p, q = c["per_label"][lab], o["per_label"][lab]
        if p["n"] or p["precision"] is not None:
            lines.append(f"  {lab:<13} {p['n']:>2}   {pct(p['recall'])}   {pct(p['precision'])}      "
                         f"({pct(q['recall'])} {pct(q['precision'])})")
    if c["confusion"]:
        lines.append("  mistakes (label>said): " + ", ".join(f"{k} {v}" for k, v in c["confusion"].items()))
    return "\n".join(lines)
