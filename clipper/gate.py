"""`clipper gate`: ask a decision model the Pass A questions, and compare it with Pass A.

A decision model (TypeSafe's Jev, `typesafe:jev-latest`) answers typed questions with
probabilities and writes no text, for a small fraction of Haiku's price. It can't write
Pass A's cut points, hook or title, but it could stand in front of Pass A as a gate: ask it
whether each window has a clip, and send only the likely ones on to Haiku.

This is the experiment. For streams already scored, every Pass A window is asked the
questions in prompts/gate_<version>.md (has_clip, the hard rejections, needs_context, a
1-10 score, the category), with the same brief and window text Haiku read. The answers are
cached in streams/<id>/gate_<model>_<version>.json. The report compares them with what
Haiku decided, with Pass B's picks, and with the uploaded Shorts' 7-day YouTube views
(analytics.json): at each has_clip threshold, how many windows the gate lets through
(Haiku calls kept) and how many of Haiku's keeps, picks and best Shorts it would have lost.

    clipper gate                     the last 10 reviewed streams
    clipper gate kick-134467434 ...  these streams
    clipper gate --force             ask again instead of reading the cache

Nothing here changes scoring; candidates.json is only read.
"""
from __future__ import annotations

import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import jsonstate
from .config import for_stream
from .errors import ClipperError
from .llm import LLM, preflight, split
from .prompts import Prompts, read_sections
from .states import split_key

NOULS = ("has_clip", "third_party_audio", "casino_gameplay", "needs_context")
REJECTS = ("third_party_audio", "casino_gameplay", "needs_context")
THRESHOLDS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)
BULLET = re.compile(r"^-\s+(.*)$")


def questions(sections: dict[str, str]) -> dict:
    """prompts/gate_<v>.md's "## name" sections as the request's questions: the NOULS are
    yes/no, "score" lists its levels (lowest first) as "- " lines, "category" its options
    as "- name: description" lines; other lines are the instructions."""
    out = {}
    for name, text in sections.items():
        lines = text.splitlines()
        bullets = [m.group(1).strip() for m in map(BULLET.match, lines) if m]
        instructions = " ".join(x.strip() for x in lines if x.strip() and not BULLET.match(x))
        if name in NOULS:
            out[name] = {"type": "noul", "instructions": instructions}
        elif name == "score":
            out[name] = {"type": "score", "instructions": instructions, "criteria": bullets}
        elif name == "category":
            opts = dict(b.split(":", 1) for b in bullets)
            out[name] = {"type": "choice", "instructions": instructions,
                         "criteria": {k.strip(): v.strip() for k, v in opts.items()}}
        else:
            raise ClipperError(f"gate prompt: unknown question ## {name}")
    missing = set(NOULS) - set(out)
    if missing:
        raise ClipperError(f"gate prompt: missing ## {', '.join(sorted(missing))}")
    return out


def flatten(answers: dict) -> dict:
    """The answers as one row: p(yes) per noul, score on Pass A's 1-10 scale, top category."""
    row = {n: answers[n]["noul"] for n in NOULS if n in answers}
    if "score" in answers:
        row["score"] = round(answers["score"]["score"] + 1, 2)  # level 0 is Pass A's 1
    if "category" in answers:
        row["category"] = answers["category"]["choice"]
    return row


def passes(row: dict, t: float) -> bool:
    """The gate at threshold t: likely a clip, and no hard rejection more likely than not."""
    j = row.get("gate") or {}
    return j.get("has_clip", 0.0) >= t and all(j.get(r, 0.0) < 0.5 for r in REJECTS)


# ---------- inputs ----------

def windows(stream_dir: Path, cfg: dict, prompts: Prompts) -> dict[str, dict]:
    """Pass A's windows by id: the chat windows (windows.json) and the transcript-scan ones,
    rebuilt from the cached scan the same way `clipper score` built them."""
    from . import scoring

    sc = cfg["scoring"]
    chat = jsonstate.load(stream_dir / "windows.json")["windows"]
    out = {w.get("id") or f"w{w['rank']:02d}": w for w in chat}
    scan = jsonstate.read(stream_dir / f"scan_{prompts.version}.json")
    if scan:
        meta = jsonstate.load(stream_dir / "meta.json")
        sig = scoring.load_signals(stream_dir)
        extra = scoring.scan_windows(scan, chat, sig, scoring.load_chat(stream_dir, meta), sc,
                                     cfg["signals"]["chat_lag_s"])
        out.update({w["id"]: w for w in extra})
    return out


def views_by_pick(cfg: dict) -> dict[tuple[str, str], int]:
    """(stream, Pass A id) -> the best 7-day YouTube views of an upload cut from it (total
    views while it is younger than 7 days)."""
    from . import analytics

    out: dict[tuple[str, str], int] = {}
    for r in analytics.load(cfg).values():
        if r.get("missing") or r.get("kind") not in ("short", "clip", "pick"):
            continue
        stream, item = split_key(r.get("key", ""))
        pid = item.split("_", 1)[1] if "_" in item else ""
        if not pid:
            continue
        v = (r.get("d7") or {}).get("views", r.get("views", 0)) or 0
        out[(stream, pid)] = max(out.get((stream, pid), 0), int(v))
    return out


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", split(model)[1].lower()).strip("-")


# ---------- asking ----------

def ask(stream_dir: Path, cfg: dict, model: str, llm: LLM, force: bool = False) -> dict:
    """The gate's answers for every Pass A window of one stream (cached)."""
    from .scoring import load_signals, pass_a_user
    from .transcript import load_words

    cfg = for_stream(cfg, stream_dir)
    sc = cfg["scoring"]
    prompts = Prompts(cfg)
    path = stream_dir / f"gate_{slug(model)}_{prompts.version}.json"
    cached = None if force else jsonstate.read(path)
    if cached:
        return cached
    cands = jsonstate.read(stream_dir / "candidates.json")
    if not cands:
        raise ClipperError(f"{stream_dir.name}: no candidates.json (run clipper score first)")
    if cands.get("prompt_version") != prompts.version:  # Haiku read another brief
        raise ClipperError(f"{stream_dir.name}: scored with prompts {cands.get('prompt_version')}, "
                           f"not {prompts.version}")
    sections = read_sections(prompts.dir / f"gate_{prompts.version}.md", "q", prompts.vars)
    qs = questions({k.removeprefix("q_"): v for k, v in sections.items()})
    brief = prompts.get("score_a")
    by_id = windows(stream_dir, cfg, prompts)
    words, sig = load_words(stream_dir), load_signals(stream_dir)
    picks = {p["id"] for p in (cands.get("pass_b") or {}).get("picks", [])}

    def usd(api: dict, m: str) -> float:
        """A call's cost from its recorded tokens and [llm] prices."""
        pin, pout = llm.prices.get(m) or llm.prices.get(split(m)[1]) or (0.0, 0.0)
        return round((api.get("input_tokens", 0) * pin + api.get("output_tokens", 0) * pout) / 1e6, 6)

    def a_usd(rec: dict) -> float:
        return usd(rec.get("api") or {}, rec.get("model") or "")

    def one(rec: dict) -> dict:
        row = {"id": rec["id"], "source": rec.get("source", "chat"), "status": rec["status"],
               "score": rec.get("score") or 0, "category": rec.get("category"),
               "pick": rec["id"] in picks, "a_model": rec.get("model"), "a_usd": a_usd(rec)}
        w = by_id.get(rec["id"])
        if w is None or [w["start_s"], w["end_s"]] != rec.get("window"):
            return {**row, "skipped": "window not rebuilt"}
        state = f"<brief>\n{brief}\n</brief>\n\n{pass_a_user(w, words, sig, sc['lead_in_s'])}"
        try:
            answers, meta = llm.decide(model, state, qs)
        except ClipperError:
            raise
        except Exception as e:  # noqa: BLE001  # one bad window shouldn't sink the stream
            return {**row, "error": f"{type(e).__name__}: {e}"}
        return {**row, "gate": flatten(answers), "api": {**meta, "usd": usd(meta, model)}}

    recs = [r for r in cands["pass_a"] if r.get("source") != "hand" and r["status"] != "error"]
    with ThreadPoolExecutor(sc["concurrency"]) as pool:
        rows = list(pool.map(one, recs))
    out = {"stream_id": stream_dir.name, "model": model, "prompt_version": prompts.version,
           "a_prompt_version": cands.get("prompt_version"), "rows": rows}
    if not any("error" in r for r in rows):  # an errored window is asked again next time
        jsonstate.write(path, out)
    return out


# ---------- the comparison ----------

def _rank(xs: list[float]) -> np.ndarray:
    order = np.argsort(xs, kind="stable")
    r = np.empty(len(xs))
    r[order] = np.arange(len(xs))
    for v in set(xs):  # ties share their mean rank
        idx = [i for i, x in enumerate(xs) if x == v]
        r[idx] = r[idx].mean()
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    return round(float(np.corrcoef(_rank(a), _rank(b))[0, 1]), 2)


def report(results: list[dict], views: dict[tuple[str, str], int]) -> dict:
    """Pure: the streams' gate answers against Haiku's decisions, Pass B's picks and views."""
    rows = [{**r, "stream": res["stream_id"],
             "views": views.get((res["stream_id"], r["id"]))}
            for res in results for r in res["rows"] if "gate" in r]
    skipped = sum(1 for res in results for r in res["rows"] if "gate" not in r)
    kept = [r for r in rows if r["status"] == "kept"]
    picked = [r for r in rows if r["pick"]]
    viewed = [r for r in picked if r["views"] is not None]
    top = sorted(viewed, key=lambda r: -r["views"])[:max(1, len(viewed) // 4)] if viewed else []

    def share(sub: list[dict], t: float) -> float | None:
        return round(sum(passes(r, t) for r in sub) / len(sub), 2) if sub else None

    gate_usd = sum((r.get("api") or {}).get("usd", 0) or 0 for r in rows)
    return {
        "streams": len(results), "windows": len(rows), "skipped": skipped,
        "haiku_kept": len(kept), "picks": len(picked), "with_views": len(viewed),
        "thresholds": [{"t": t, "pass": share(rows, t), "kept": share(kept, t),
                        "picks": share(picked, t), "top_views": share(top, t)}
                       for t in THRESHOLDS],
        "agree_0.5": round(sum(passes(r, 0.5) == (r["status"] == "kept") for r in rows) / len(rows), 2)
        if rows else None,
        "score_vs_haiku": spearman([r["score"] for r in rows], [r["gate"].get("score", 0) for r in rows]),
        "haiku_score_vs_views": spearman([float(r["score"]) for r in viewed], [r["views"] for r in viewed]),
        "gate_score_vs_views": spearman([r["gate"].get("score", 0) for r in viewed], [r["views"] for r in viewed]),
        "gate_has_clip_vs_views": spearman([r["gate"]["has_clip"] for r in viewed], [r["views"] for r in viewed]),
        "category_agree": round(sum(r["gate"].get("category") == r["category"] for r in kept) / len(kept), 2)
        if kept else None,
        "gate_usd": round(gate_usd, 4),
        "haiku_usd": round(sum(r.get("a_usd", 0) for r in rows), 4),
        "latency_s": statistics.median([r["api"]["latency_s"] for r in rows]) if rows else None,
        "lost_picks_0.5": [f"{r['stream']}/{r['id']} has_clip {r['gate']['has_clip']:.2f}"
                           + (f", {r['views']} views" if r["views"] is not None else "")
                           for r in picked if not passes(r, 0.5)],
    }


def report_text(rep: dict) -> str:
    def pct(x: float | None) -> str:
        return "  -" if x is None else f"{x * 100:3.0f}%"

    lines = [(f"{rep['streams']} streams, {rep['windows']} windows ({rep['skipped']} skipped), "
             f"Haiku kept {rep['haiku_kept']}, Pass B picked {rep['picks']} "
             f"({rep['with_views']} with YouTube views)"),
             "",
             "has_clip >=   windows through   of Haiku's keeps   of picks   of top-viewed picks"]
    for t in rep["thresholds"]:
        lines.append(f"   {t['t']:.1f}           {pct(t['pass'])}             {pct(t['kept'])}"
                     f"            {pct(t['picks'])}        {pct(t['top_views'])}")
    lines += ["",
              (f"keep/no-keep agreement with Haiku at 0.5: {pct(rep['agree_0.5'])}; "
              f"category agreement on Haiku's keeps: {pct(rep['category_agree'])}"),
              f"rank correlation, gate score vs Haiku score: {rep['score_vs_haiku']}",
              (f"rank correlation with 7-day views: Haiku score {rep['haiku_score_vs_views']}, "
              f"gate score {rep['gate_score_vs_views']}, gate has_clip {rep['gate_has_clip_vs_views']}"),
              (f"cost: gate ${rep['gate_usd']:.4f} vs Haiku Pass A ${rep['haiku_usd']:.4f} "
              f"for the same windows; gate median latency {rep['latency_s']} s")]
    if rep["lost_picks_0.5"]:
        lines += ["", "picks the gate would have dropped at 0.5:"] + [f"  {x}" for x in rep["lost_picks_0.5"]]
    return "\n".join(lines)


def recent(cfg: dict, n: int) -> list[Path]:
    """The last n streams published for review that have candidates.json."""
    from .actions import review_dirs

    return [d for d in review_dirs(cfg) if (d / "candidates.json").exists()][-n:]


def run(cfg: dict, streams: list[Path], model: str, force: bool = False) -> dict:
    if split(model)[0] == "anthropic":
        raise ClipperError(f"{model}: the gate is for decision models (typesafe:jev-latest)")
    llm = LLM(cfg)
    preflight(llm, model)
    results = []
    for d in streams:
        have = (jsonstate.read(d / "candidates.json") or {}).get("prompt_version")
        want = for_stream(cfg, d)["scoring"]["prompt_version"]
        if have != want:  # Haiku read another brief, and the scan windows can't be rebuilt
            print(f"{d.name}: scored with prompts {have}, not {want}; skipped")
            continue
        res = ask(d, cfg, model, llm, force)
        errs = sum("error" in r for r in res["rows"])
        print(f"{d.name}: {len(res['rows'])} windows" + (f", {errs} errored (not cached)" if errs else ""))
        results.append(res)
    rep = report(results, views_by_pick(cfg))
    print("\n" + report_text(rep))
    if llm.cost:
        print(f"\nthis run: ${llm.cost:.4f}")
    return rep
