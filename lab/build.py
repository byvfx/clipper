"""Jev lab: one dataset of every Pass A window of every scored stream, with labels.

Writes lab/data/windows.jsonl: stream, show, id, source, prompt_version, the Pass A user text
Haiku read (text), the bare transcript (body), Haiku's status/score/category, Pass B pick,
pick rank, the best 7-day views of an upload cut from it, and Haiku's cost for the window.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from clipper import config, jsonstate
from clipper.config import for_stream
from clipper.gate import views_by_pick, windows
from clipper.llm import price_of
from clipper.prompts import Prompts
from clipper.scoring import load_signals, pass_a_user, transcript_lines
from clipper.transcript import load_words

OUT = Path(__file__).parent / "data"


def main() -> None:
    cfg = config.load()
    root = cfg["paths"]["streams"]
    views = views_by_pick(cfg)
    OUT.mkdir(exist_ok=True)
    n = 0
    with open(OUT / "windows.jsonl", "w", encoding="utf-8") as f:
        for d in sorted(Path(root).iterdir()):
            cands = jsonstate.read(d / "candidates.json")
            if not cands or not cands.get("pass_a"):
                continue
            scfg = copy.deepcopy(for_stream(cfg, d))
            scfg["scoring"]["prompt_version"] = cands["prompt_version"]
            prompts = Prompts(scfg)
            try:
                by_id = windows(d, scfg, prompts)
            except Exception as e:  # noqa: BLE001
                print(d.name, "skipped:", e, file=sys.stderr)
                continue
            words, sig = load_words(d), load_signals(d)
            picks = [p["id"] for p in (cands.get("pass_b") or {}).get("picks", [])]
            meta = jsonstate.read(d / "meta.json") or {}
            kept = 0
            for rec in cands["pass_a"]:
                if rec.get("source") == "hand" or rec["status"] == "error":
                    continue
                w = by_id.get(rec["id"])
                if w is None or [w["start_s"], w["end_s"]] != rec.get("window"):
                    continue
                api = rec.get("api") or {}
                pin, pout = price_of(cfg["llm"]["prices"], rec.get("model") or "") or (0, 0)
                row = {
                    "stream": d.name, "show": scfg.get("show_key") or meta.get("channel"),
                    "id": rec["id"], "source": rec.get("source", "chat"),
                    "pv": cands["prompt_version"], "start_s": w["start_s"], "end_s": w["end_s"],
                    "text": pass_a_user(w, words, sig, scfg["scoring"]["lead_in_s"]),
                    "body": "\n".join(transcript_lines(words, w["start_s"], w["end_s"])),
                    "status": rec["status"], "score": rec.get("score") or 0,
                    "category": rec.get("category"), "has_clip": rec.get("has_clip"),
                    "third_party_audio": rec.get("third_party_audio"),
                    "casino_gameplay": rec.get("casino_gameplay"),
                    "needs_context": rec.get("needs_context"),
                    "a_title": rec.get("title"), "a_hook": rec.get("hook"),
                    "pick": rec["id"] in picks,
                    "pick_rank": picks.index(rec["id"]) if rec["id"] in picks else None,
                    "views": views.get((d.name, rec["id"])),
                    "a_usd": (api.get("input_tokens", 0) * pin + api.get("output_tokens", 0) * pout) / 1e6,
                }
                kept += row["status"] == "kept"
                f.write(json.dumps(row) + "\n")
                n += 1
            print(f"{d.name} {cands['prompt_version']} {meta.get('channel')}: "
                  f"{len(cands['pass_a'])} pass A, {kept} kept, {len(picks)} picks")
    print(n, "windows")


if __name__ == "__main__":
    main()
