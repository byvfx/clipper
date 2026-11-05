"""Jev lab T5-T7: the gate cascade, the flags against Haiku's, and routing by confidence.

    python lab/routing.py [VARIANT]      (default brief; any data/<stem>.jsonl, e.g. brief__openrouter-x)
"""
from __future__ import annotations

import json
import sys

import analyze as A
import numpy as np
from sklearn.metrics import roc_auc_score

FLAGS = ("third_party_audio", "casino_gameplay", "needs_context")


def main(stem: str) -> None:
    rows = A.load_rows()
    ans = {(j["stream"], j["id"]): j for j in map(json.loads, open(A.DATA / f"{stem}.jsonl", encoding="utf-8"))}
    rs = [dict(r, jev=ans[(r["stream"], r["id"])]) for r in rows if (r["stream"], r["id"]) in ans]
    comp = A.loso(rs, A.features(rs[0]["jev"]), "kept")
    vs = sorted(r["views"] for r in rs if r["views"] is not None)
    top = vs[int(len(vs) * 0.75)]
    print(f"{stem}: {len(rs)} windows\n\nT5 gate: send the top X% of each stream (composite) to Haiku")
    for share in (0.9, 0.8, 0.7, 0.5):
        print(f"  {share:.0%}: picks {A.per_stream_recall(rs, comp, 'pick', share)}, "
              f"top-viewed {A.per_stream_recall(rs, comp, lambda r: r['views'] is not None and r['views'] >= top, share)}")

    print("\nT6 flags vs Haiku's own (AUC)")
    for f in FLAGS:
        if f in rs[0]["jev"]:
            y = [bool(r[f]) for r in rs]
            print(f"  {f}: {roc_auc_score(y, [r['jev'][f] for r in rs]):.3f} ({sum(y)} flagged by Haiku)")

    print("\nT7 route by confidence: the model answers when sure, Haiku otherwise")
    y_k, y_p = np.array([r["kept"] for r in rs]), np.array([r["pick"] for r in rs])
    for cov in (0.2, 0.4, 0.6):
        m = comp <= np.quantile(comp, cov)
        print(f"  keep/no: rejects its lowest {m.mean():.0%} alone: {(~y_k[m]).mean():.1%} right, "
              f"{y_p[m].sum()} picks lost")
    for f in FLAGS[:2]:
        if f not in rs[0]["jev"]:
            continue
        p, y = np.array([r["jev"][f] for r in rs], float), np.array([bool(r[f]) for r in rs])
        conf = np.abs(2 * p - 1)
        for cov in (0.2, 0.4, 0.6):
            m = conf >= np.quantile(conf, 1 - cov)
            print(f"  {f}: decides its surest {m.mean():.0%}: agrees with Haiku {((p[m] >= 0.5) == y[m]).mean():.1%}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "brief")
