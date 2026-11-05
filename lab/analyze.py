"""Jev lab: how each variant's answers line up with Haiku's keeps, Pass B's picks and views.

    python lab/analyze.py lean body ...

Baselines: chat heat (free; the rank that already picks windows), Haiku's own score, and the
11-03 run (brief + window, gate_jev-latest_v5.json) where it exists. "composite" is a logistic
regression over a variant's answers, fitted leave-one-stream-out, so every stream is scored by
weights that never saw it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

DATA = Path(__file__).parent / "data"
STREAMS = Path("//192.168.0.155/docker/clipper/streams")
PRICE = 0.042 / 1e6


def load_rows() -> list[dict]:
    rows = [json.loads(x) for x in open(DATA / "windows.jsonl", encoding="utf-8")]
    heat, old = {}, {}
    for s in {r["stream"] for r in rows}:
        c = json.loads((STREAMS / s / "candidates.json").read_text(encoding="utf-8"))
        for a in c["pass_a"]:
            heat[(s, a["id"])] = a.get("heat") or 0.0
        g = STREAMS / s / "gate_jev-latest_v5.json"
        if g.exists():
            for r in json.loads(g.read_text(encoding="utf-8"))["rows"]:
                if "gate" in r:
                    old[(s, r["id"])] = r["gate"]
    for r in rows:
        r["heat"] = heat.get((r["stream"], r["id"]), 0.0)
        r["old"] = old.get((r["stream"], r["id"]))
        r["kept"] = r["status"] == "kept"
    return rows


def auc(y, x) -> float | None:
    y = np.asarray(y, bool)
    if y.all() or not y.any():
        return None
    return round(float(roc_auc_score(y, x)), 3)


def rho(x, y) -> float | None:
    if len(x) < 5:
        return None
    return round(float(spearmanr(x, y).statistic), 2)


def features(jev: dict) -> list[str]:
    return sorted(k for k, v in jev.items() if isinstance(v, (int, float))
                  and k not in ("tokens", "latency_s"))


def loso(rows: list[dict], feats: list[str], target: str, subset=None) -> np.ndarray:
    """Out-of-stream composite scores for `rows` (logistic regression on feats)."""
    X = np.array([[r["jev"][f] for f in feats] for r in rows])
    y = np.array([r[target] for r in rows], bool)
    fit_mask = np.ones(len(rows), bool) if subset is None else np.array([subset(r) for r in rows])
    streams = np.array([r["stream"] for r in rows])
    out = np.zeros(len(rows))
    for s in set(streams):
        tr = (streams != s) & fit_mask
        te = streams == s
        if y[tr].all() or not y[tr].any():
            continue
        m = LogisticRegression(C=0.5, max_iter=2000).fit(X[tr], y[tr])
        out[te] = m.predict_proba(X[te])[:, 1]
    return out


def per_stream_recall(rows, score, label, share) -> float:
    """Send the top `share` of each stream's windows on: share of `label` rows that get through."""
    hit = tot = 0
    by = {}
    for r, s in zip(rows, score):
        by.setdefault(r["stream"], []).append((s, r[label] if isinstance(label, str) else label(r)))
    for xs in by.values():
        xs.sort(key=lambda t: -t[0])
        k = int(np.ceil(share * len(xs)))
        hit += sum(1 for _, y in xs[:k] if y)
        tot += sum(1 for _, y in xs if y)
    return round(hit / tot, 2) if tot else float("nan")


def topk_overlap(rows, score) -> float:
    """Pass B stand-in: among Haiku's keeps, the top n of each stream (n = its pick count) vs picks."""
    hit = tot = 0
    by = {}
    for r, s in zip(rows, score):
        if r["kept"]:
            by.setdefault(r["stream"], []).append((s, r["pick"]))
    for xs in by.values():
        n = sum(p for _, p in xs)
        xs.sort(key=lambda t: -t[0])
        hit += sum(p for _, p in xs[:n])
        tot += n
    return round(hit / tot, 2) if tot else float("nan")


def block(name: str, rows: list[dict], score) -> dict:
    score = np.asarray(score, float)
    kept = [r for r in rows if r["kept"]]
    ks = np.array([s for r, s in zip(rows, score) if r["kept"]])
    viewed = [(s, r["views"]) for r, s in zip(rows, score) if r["views"] is not None]
    vs = sorted(r["views"] for r in rows if r["views"] is not None)
    top_cut = vs[int(len(vs) * 0.75)] if vs else 0
    return {
        "name": name,
        "auc_kept": auc([r["kept"] for r in rows], score),
        "auc_pick": auc([r["pick"] for r in rows], score),
        "auc_pick_in_kept": auc([r["pick"] for r in kept], ks),
        "passB_overlap": topk_overlap(rows, score),
        "views_rho": rho([s for s, _ in viewed], [v for _, v in viewed]),
        **{f"top{int(sh * 100)}%_picks": per_stream_recall(rows, score, "pick", sh) for sh in (0.3, 0.5)},
        **{f"top{int(sh * 100)}%_topviews": per_stream_recall(
            rows, score, lambda r: r["views"] is not None and r["views"] >= top_cut, sh) for sh in (0.3, 0.5)},
    }


def table(blocks: list[dict]) -> str:
    cols = list(blocks[0])
    w = max(len(b["name"]) for b in blocks) + 2
    head = "".ljust(w) + "  ".join(c[:16].rjust(16) for c in cols[1:])
    lines = [head]
    for b in blocks:
        lines.append(b["name"].ljust(w) + "  ".join(
            ("-" if b[c] is None else str(b[c])).rjust(16) for c in cols[1:]))
    return "\n".join(lines)


def main(variants: list[str]) -> None:
    rows = load_rows()
    print(f"{len(rows)} windows, {len({r['stream'] for r in rows})} streams, "
          f"{sum(r['kept'] for r in rows)} kept, {sum(r['pick'] for r in rows)} picks, "
          f"{sum(r['views'] is not None for r in rows)} with views\n")
    for v in variants:
        jev = {(j["stream"], j["id"]): j for j in map(json.loads, open(DATA / f"{v}.jsonl", encoding="utf-8"))}
        rs = [dict(r, jev=jev[(r["stream"], r["id"])]) for r in rows if (r["stream"], r["id"]) in jev]
        feats = features(rs[0]["jev"])
        toks = sum(r["jev"]["tokens"] or 0 for r in rs)
        print(f"=== {v}: {len(rs)} windows, ${toks * PRICE:.3f} "
              f"({toks / len(rs):.0f} tokens a window) vs Haiku Pass A ${sum(r['a_usd'] for r in rs):.3f}")
        blocks = [block("chat heat (free)", rs, [r["heat"] for r in rs]),
                  block("Haiku score", rs, [r["score"] for r in rs])]
        comp_k = loso(rs, feats, "kept")
        comp_p = loso(rs, feats, "pick")
        blocks += [block("composite->kept", rs, comp_k), block("composite->pick", rs, comp_p),
                   block("composite->pick +heat", rs, loso(
                       [dict(r, jev={**r["jev"], "heat": np.log1p(r["heat"])}) for r in rs],
                       feats + ["heat"], "pick"))]
        for f in feats:
            blocks.append(block(f, rs, [r["jev"][f] for r in rs]))
        print(table(blocks))
        old = [r for r in rs if r["old"]]
        if old:
            print(f"\n  same windows as the 11-03 run ({len(old)}):")
            print(table([block("11-03 has_clip", old, [r["old"]["has_clip"] for r in old]),
                         block("11-03 score", old, [r["old"].get("score", 0) for r in old]),
                         block(f"{v} has_clip", old, [r["jev"]["has_clip"] for r in old]),
                         block(f"{v} short_score", old, [r["jev"].get("short_score", r["jev"].get("score", 0)) for r in old]),
                         block(f"{v} composite->kept", old,
                               [s for r, s in zip(rs, comp_k) if r["old"]]),
                         block("Haiku score", old, [r["score"] for r in old])]))
        print()


if __name__ == "__main__":
    main(sys.argv[1:] or ["lean"])
