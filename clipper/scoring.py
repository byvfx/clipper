"""Stage 4: Claude highlight scoring -> candidates.json.

  summary  Pass A model condenses the transcript into a 15-minute-block timeline (for Pass B).
  Pass A   one call per window: is there a self-contained clip? Short + horizontal cuts,
           hook, title, score; hard-rejects other creators' video audio and casino play.
  Pass B   one call over every kept candidate: dedupe, spread, rank the final picks.

Prompts live in paths.prompts as <name>_<version>.md with {placeholders} filled from
[scoring]. Every candidate records model and prompt_version so prompt changes can be
compared. --dry-run writes the Pass A requests to scoring_requests/ without calling the API.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import jsonstate
from .config import for_stream
from .llm import LLM

CATEGORIES = ["fail", "win", "joke", "insight", "reaction", "hot_take"]

PASS_A_SCHEMA = {
    "type": "object",
    "properties": {
        "has_clip": {"type": "boolean"},
        "third_party_audio": {"type": "boolean"},
        "casino_gameplay": {"type": "boolean"},
        "in_s": {"type": "number"},
        "out_s": {"type": "number"},
        "short_in_s": {"type": "number"},
        "short_out_s": {"type": "number"},
        "category": {"type": "string", "enum": CATEGORIES},
        "hook": {"type": "string"},
        "title": {"type": "string"},
        "score": {"type": "integer"},
        "why": {"type": "string"},
        "source_audio_s": {"type": "number"},
        "needs_context": {"type": "boolean"},
    },
    "required": ["has_clip", "third_party_audio", "casino_gameplay", "in_s", "out_s",
                 "short_in_s", "short_out_s", "category", "hook", "title", "score", "why",
                 "source_audio_s", "needs_context"],
    "additionalProperties": False,
}

PASS_B_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "title", "hook", "reason"],
                "additionalProperties": False,
            },
        },
        "rejected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["picks", "rejected"],
    "additionalProperties": False,
}

SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "summary": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["start_s", "end_s", "summary", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["moments"],
    "additionalProperties": False,
}

SUMMARY_BLOCK_S = 900


# ---------- inputs ----------

UNNAMED = re.compile(r"SPEAKER_\d+")


def load_words(stream_dir: Path) -> list[dict]:
    """words.jsonl. When `clipper speakers` named at least one voice (other than music),
    each word also gets `who`: the name, "?" for a voice nobody matched, None for no turn."""
    lines = (stream_dir / "words.jsonl").read_text(encoding="utf-8").splitlines()
    words = [json.loads(line) for line in lines]
    names = {w.get("speaker") for w in words} - {None, "music"}
    if any(not UNNAMED.fullmatch(n) for n in names):
        for w in words:
            spk = w.get("speaker")
            w["who"] = "?" if spk and UNNAMED.fullmatch(spk) else spk
    return words


TURN_MIN_WORDS = 3  # a shorter change of voice inside a line is a turn edge a word off


def transcript_lines(words: list[dict], a: float, b: float, speakers: bool = True) -> list[str]:
    """One line per WhisperX segment overlapping [a, b): "[start_s] text". With speaker names
    (load_words `who`), a line also splits where the voice changes, and the first line of each
    turn is tagged: "[start_s] [PPP] text", "[?]" for an unnamed voice, "[music]"."""
    tags = speakers and bool(words) and "who" in words[0]
    out, cur, seg = [], [], None
    for w in words:
        if w["end"] < a or w["start"] >= b:
            continue
        if w["seg"] != seg and cur:
            out.append(cur)
            cur = []
        seg = w["seg"]
        cur.append(w)
    if cur:
        out.append(cur)
    if not tags:
        return [_line(line) for line in out]
    lines, said = [], None
    for line in out:
        for spk, run in turns(line):
            tag = ("?" if UNNAMED.fullmatch(spk) else spk) if spk and spk != said else ""
            lines.append(_line(run, tag))
            said = spk or said
    return lines


def turns(line: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """A line's words split by speaker. Words with no turn stay with the run they are in;
    runs under TURN_MIN_WORDS words join their longer neighbour (pyannote's turns and
    WhisperX's word times disagree by a word or so at each change)."""
    runs: list[list] = []
    for w in line:
        s = w.get("speaker")
        if runs and (s is None or s == runs[-1][0] or runs[-1][0] is None):
            runs[-1][0] = runs[-1][0] or s
            runs[-1][1].append(w)
        else:
            runs.append([s, [w]])
    while len(runs) > 1:
        i = min(range(len(runs)), key=lambda k: len(runs[k][1]))
        if len(runs[i][1]) >= TURN_MIN_WORDS:
            break
        j = i - 1 if i == len(runs) - 1 or (i and len(runs[i - 1][1]) >= len(runs[i + 1][1])) else i + 1
        lo = min(i, j)
        runs[lo:lo + 2] = [[runs[j][0], runs[lo][1] + runs[lo + 1][1]]]
        if lo and runs[lo - 1][0] == runs[lo][0]:  # the merge can make two neighbours one voice
            runs[lo - 1:lo + 1] = [[runs[lo][0], runs[lo - 1][1] + runs[lo][1]]]
        if lo + 1 < len(runs) and runs[lo + 1][0] == runs[lo][0]:
            runs[lo:lo + 2] = [[runs[lo][0], runs[lo][1] + runs[lo + 1][1]]]
    return [(s, ws) for s, ws in runs]


def _line(line: list[dict], tag: str = "") -> str:
    return f"[{line[0]['start']:.1f}] " + (f"[{tag}] " if tag else "") + " ".join(x["word"] for x in line)


def signal_table(sig: dict, a: int, b: int, bin_s: int = 10) -> str:
    rows = ["t_start  chat/min  react  clip  loud_z  heat"]
    for s in range(a, b, bin_s):
        e = min(b, s + bin_s)
        chat = sig["chat_msgs"][s:e].sum() * 60 / (e - s)
        loud = np.nanmax(sig["loud_z"][s:e])
        heat = np.nanmax(sig["heat"][s:e])
        rows.append(f"{s:>7}  {chat:8.0f}  {int(sig['react'][s:e].sum()):5d}  "
                    f"{int(sig['clip'][s:e].sum()):4d}  {loud:6.1f}  {heat:4.1f}")
    return "\n".join(rows)


def load_signals(stream_dir: Path) -> dict:
    import pyarrow.parquet as pq

    t = pq.read_table(stream_dir / "signals.parquet")
    return {c: t[c].to_numpy(zero_copy_only=False) for c in t.column_names}


def load_chat(stream_dir: Path, meta: dict) -> list[dict]:
    from .signals import load_chat as _load

    msgs, _ = _load(stream_dir, meta, int(np.ceil(meta["duration_s"])))
    return msgs


def snap(words: list[dict], t: float, key: str) -> float:
    i = min(range(len(words)), key=lambda k: abs(words[k][key] - t))
    return words[i][key]


# ---------- prompts ----------

def read_sections(path: Path, prefix: str, fmt: dict) -> dict[str, str]:
    """Split a rule file on "## name" lines into {prefix_name: text}."""
    out, name, buf = {}, None, []
    for line in path.read_text(encoding="utf-8").format(**fmt).splitlines():
        if line.startswith("## "):
            if name:
                out[f"{prefix}_{name}"] = "\n".join(buf).strip()
            name, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if name:
        out[f"{prefix}_{name}"] = "\n".join(buf).strip()
    return out


# Prompts that write text viewers see; [scoring] house_style rules are appended to them.
VIEWER_FACING = {"score_a", "score_b", "meta", "chapters", "segments", "compilation",
                 "thumbnail", "thumbnail_enquirer", "preshow", "oneoff", "pick", "retake",
                 "receipts_stories"}


class Prompts:
    def __init__(self, cfg: dict):
        sc = cfg["scoring"]
        self.version = sc["prompt_version"]
        self.dir: Path = cfg["paths"]["prompts"]
        self.house_style: list[str] = list(sc.get("house_style") or [])
        self.vars = {"channel_name": sc["channel_name"], "audience": sc["audience"],
                     "tone": sc["tone"], "max_picks": sc["max_picks"],
                     "stream_context": sc["stream_context"],
                     "max_source_audio_s": sc["max_source_audio_s"],
                     "scan_max_per_block": sc["scan_max_per_block"],
                     "known_figures": sc["known_figures"]}
        # Swappable rule files, <kind>_<mode>_<version>.md, split into "## <section>" parts
        # that fill {<kind>_<section>} placeholders. v3+: third_party; v4+: audience.
        for kind, key, modes in (("third_party", "third_party_audio", ("reject", "allow_reactions")),
                                 ("audience", "audience_knowledge", ("strangers", "fans"))):
            if sc[key] not in modes:
                raise SystemExit(f"scoring.{key} must be one of {modes}, not {sc[key]!r}")
            rule = self.dir / f"{kind}_{sc[key]}_{self.version}.md"
            if rule.exists():
                self.vars.update(read_sections(rule, kind, self.vars))
        # v3 prompts name the third-party sections {third_party_rule} / {third_party_rule_b}.
        if "third_party_pass_a" in self.vars:
            self.vars["third_party_rule"] = self.vars["third_party_pass_a"]
            self.vars["third_party_rule_b"] = self.vars["third_party_pass_b"]

    def get(self, name: str) -> str:
        text = (self.dir / f"{name}_{self.version}.md").read_text(encoding="utf-8")
        text = text.format(**self.vars)
        if name in VIEWER_FACING and self.house_style:
            text = (text.rstrip() + "\n\nHouse style for anything viewers see (titles, "
                    "descriptions, hooks, chapter and thumbnail text):\n"
                    + "\n".join(f"- {rule}" for rule in self.house_style) + "\n")
        return text


def pass_a_user(w: dict, words: list[dict], sig: dict, lead_in_s: int) -> str:
    lead = transcript_lines(words, max(0, w["start_s"] - lead_in_s), w["start_s"])
    body = transcript_lines(words, w["start_s"], w["end_s"])
    peaks = ", ".join(str(p) for p in sorted(w["peaks"]))
    chat = "\n".join(f"- {m}" for m in w.get("chat_sample", []))
    origin = (f"Found by a transcript scan, not by chat: {w['scan_summary']}\n"
              if w.get("scan_summary") else "")
    return (
        f"Window {w['start_s']}-{w['end_s']} s (chat peaks at {peaks} s).\n{origin}\n"
        f"<lead_in>\n" + "\n".join(lead) + "\n</lead_in>\n\n"
        "<window>\n" + "\n".join(body) + "\n</window>\n\n"
        f"<signals>\n{signal_table(sig, w['start_s'], w['end_s'])}\n</signals>\n\n"
        f"<chat_at_peak>\n{chat}\n</chat_at_peak>"
    )


# ---------- API ----------

# Every stage's model calls go through clipper/llm.py, which picks the provider from the
# model string; `Claude` stays the name the stages import.
Claude = LLM


# ---------- passes ----------

def stream_summary(stream_dir: Path, words: list[dict], n: int, cfg: dict,
                   claude: Claude, prompts: Prompts) -> list[dict]:
    path = stream_dir / f"summary_{prompts.version}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    sc = cfg["scoring"]
    system = prompts.get("summary")

    def one(a: int) -> dict:
        lines = transcript_lines(words, a, a + SUMMARY_BLOCK_S)
        if not lines:
            return {"start_s": a, "summary": "(no speech)"}
        try:
            text, _ = claude.text_call(sc["model_a"], system, "\n".join(lines), sc["effort_a"])
        except Exception as e:  # noqa: BLE001  # one bad block shouldn't sink the run
            print(f"summary block {a}: {type(e).__name__}: {e}")
            return {"start_s": a, "summary": "(unavailable)", "failed": True}
        return {"start_s": a, "summary": text} if text else {"start_s": a, "summary": "(refused)", "failed": True}

    with ThreadPoolExecutor(sc["concurrency"]) as pool:
        blocks = list(pool.map(one, range(0, n, SUMMARY_BLOCK_S)))
    failed = sum(1 for b in blocks if b.pop("failed", False))
    if failed:  # #75: not cached, so the next run asks for those blocks again
        print(f"summary: {failed} block(s) failed; not cached")
    else:
        jsonstate.write(path, blocks)
    return blocks


def transcript_scan(stream_dir: Path, words: list[dict], n: int, cfg: dict,
                    claude: Claude, prompts: Prompts) -> list[dict]:
    """Find own-voice moments block by block, independent of chat heat."""
    path = stream_dir / f"scan_{prompts.version}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    sc = cfg["scoring"]
    system = prompts.get("scan")

    def one(a: int) -> list[dict]:
        lines = transcript_lines(words, a, a + SUMMARY_BLOCK_S)
        if not lines:
            return []
        try:
            out, _ = claude.json_call(sc["model_a"], system, "\n".join(lines), SCAN_SCHEMA,
                                      sc["effort_a"])
        except Exception as e:  # noqa: BLE001  # one bad block shouldn't sink the scan
            print(f"scan block {a}: {type(e).__name__}: {e}")
            return None
        if out is None:
            return None
        moments = out.get("moments", [])[:sc["scan_max_per_block"]]
        return [m for m in moments if a - 60 <= m["start_s"] < m["end_s"] <= a + SUMMARY_BLOCK_S + 60]

    with ThreadPoolExecutor(sc["concurrency"]) as pool:
        blocks = list(pool.map(one, range(0, n, SUMMARY_BLOCK_S)))
    moments = [m for block in blocks if block for m in block]
    failed = sum(1 for b in blocks if b is None)
    if failed:  # #75: not cached, so the next run scans those blocks again
        print(f"scan: {failed} block(s) failed or refused; not cached")
    else:
        jsonstate.write(path, moments)
    return moments


def scan_windows(moments: list[dict], windows: list[dict], sig: dict, msgs: list[dict],
                 sc: dict, lag: int) -> list[dict]:
    """Turn scanned moments into Pass A windows, skipping ones a chat window already covers."""
    from .signals import EMOTE

    n = len(sig["heat"])
    out = []
    mt = np.array([m["t"] for m in msgs])
    for m in sorted(moments, key=lambda m: m["start_s"]):
        a = max(0, int(m["start_s"]) - sc["scan_pad_s"])
        b = min(n, int(m["end_s"]) + sc["scan_pad_s"])
        overlap = max((max(0, min(b, w["end_s"]) - max(a, w["start_s"])) for w in windows + out),
                      default=0)
        if b <= a or overlap / (b - a) > sc["scan_max_overlap"]:
            continue
        heat = np.nan_to_num(sig["heat"][a:b], nan=0.0)
        p = a + int(np.argmax(heat))
        lo, hi = np.searchsorted(mt, [p + lag - 5, p + lag + 15])
        out.append({
            "id": f"t{len(out) + 1:02d}", "source": "transcript", "rank": None,
            "start_s": a, "end_s": b, "peak_s": p, "peaks": [p], "heat": float(heat.max()),
            "scan_summary": m["summary"],
            "chat_sample": [" ".join(EMOTE.sub(r" \1 ", x["content"]).split())
                            for x in msgs[lo:hi]][:10],
        })
    return out


def run_pass_a(windows: list[dict], words: list[dict], sig: dict, cfg: dict,
               claude: Claude, prompts: Prompts) -> list[dict]:
    sc = cfg["scoring"]
    system = prompts.get("score_a")

    def one(w: dict) -> dict:
        rec = {"id": w.get("id") or f"w{w['rank']:02d}", "source": w.get("source", "chat"),
               "window_rank": w["rank"], "window": [w["start_s"], w["end_s"]],
               "heat": w["heat"], "pass": "A", "model": sc["model_a"],
               "prompt_version": prompts.version}
        user = pass_a_user(w, words, sig, sc["lead_in_s"])
        try:
            out, meta = claude.json_call(sc["model_a"], system, user, PASS_A_SCHEMA, sc["effort_a"])
        except Exception as e:  # noqa: BLE001  # one bad window shouldn't sink the stream
            return {**rec, "status": "error", "error": f"{type(e).__name__}: {e}"}
        rec["api"] = meta
        if out is None:
            return {**rec, "status": "refused"}
        rec.update(out)
        rec["third_party_mode"] = sc["third_party_audio"]
        too_much_source = (sc["third_party_audio"] == "allow_reactions"
                           and out["source_audio_s"] > sc["max_source_audio_s"])
        if out["third_party_audio"] or out["casino_gameplay"] or too_much_source:
            rec["status"] = "rejected"
            rec["has_clip"] = False
        elif not out["has_clip"]:
            rec["status"] = "no_clip"
        elif out["needs_context"]:
            rec["status"] = "rejected"
        else:
            rec["status"] = "kept"
        if rec["status"] == "kept":
            lo = max(0, w["start_s"] - sc["lead_in_s"])
            span = [x for x in words if lo <= x["start"] < w["end_s"] + 30]
            if not span:  # #75: music or a break with chat heat; nothing to snap to
                return {**rec, "status": "error", "error": "no transcript words in the window"}
            for a, b in (("in_s", "out_s"), ("short_in_s", "short_out_s")):
                rec[a], rec[b] = snap(span, out[a], "start"), snap(span, out[b], "end")
            if rec["out_s"] <= rec["in_s"]:
                return {**rec, "status": "error",
                        "error": f"clip ends before it starts ({out['in_s']}-{out['out_s']})"}
            if rec["short_out_s"] <= rec["short_in_s"]:  # the Short falls back to the clip
                rec["short_in_s"], rec["short_out_s"] = rec["in_s"], rec["out_s"]
            rec["duration_s"] = round(rec["out_s"] - rec["in_s"], 2)
            rec["short_duration_s"] = round(rec["short_out_s"] - rec["short_in_s"], 2)
        return rec

    with ThreadPoolExecutor(sc["concurrency"]) as pool:
        return list(pool.map(one, windows))


def run_pass_b(kept: list[dict], summary: list[dict], words: list[dict], cfg: dict,
               claude: Claude, prompts: Prompts) -> dict:
    sc = cfg["scoring"]
    timeline = "\n".join(f"[{b['start_s'] // 60:>3} min] {b['summary']}" for b in summary)
    cands = []
    for c in kept:
        text = " ".join(transcript_lines(words, c["in_s"], c["out_s"]))
        cands.append(
            f"<candidate id=\"{c['id']}\">\n"
            f"time {c['in_s']:.1f}-{c['out_s']:.1f} s (short {c['short_in_s']:.1f}-"
            f"{c['short_out_s']:.1f}), category {c['category']}, score {c['score']}, "
            f"heat {c['heat']:.1f}\nhook: {c['hook']}\ntitle: {c['title']}\nwhy: {c['why']}\n"
            f"transcript: {text}\n</candidate>")
    from .analytics import feedback_block

    user = (feedback_block(cfg, cfg["show"]["key"])
            + f"<stream_timeline>\n{timeline}\n</stream_timeline>\n\n" + "\n\n".join(cands))
    out, meta = claude.json_call(sc["model_b"], prompts.get("score_b"), user, PASS_B_SCHEMA,
                                 sc["effort_b"], fallback=True)
    result = {"pass": "B", "model": sc["model_b"], "prompt_version": prompts.version, "api": meta}
    if out is None:
        return {**result, "status": "refused"}
    ids = {c["id"] for c in kept}
    seen = [p["id"] for p in out["picks"]] + [r["id"] for r in out["rejected"]]
    result["unaccounted"] = sorted(ids - set(seen))
    result["unknown_ids"] = sorted(set(seen) - ids)
    return {**result, "status": "ok", **out}


def preflight(claude: Claude, model: str) -> None:
    """One tiny call so account problems stop the run with one clear line."""
    claude.preflight(model)


def run(stream_dir: Path, cfg: dict, dry_run: bool = False) -> dict:
    cfg = for_stream(cfg, stream_dir)
    sc = cfg["scoring"]
    prompts = Prompts(cfg)
    meta = json.loads((stream_dir / "meta.json").read_text(encoding="utf-8"))
    windows = json.loads((stream_dir / "windows.json").read_text(encoding="utf-8"))["windows"]
    words = load_words(stream_dir)
    sig = load_signals(stream_dir)

    if dry_run:
        d = stream_dir / "scoring_requests"
        d.mkdir(exist_ok=True)
        system = prompts.get("score_a")
        for w in windows:
            user = pass_a_user(w, words, sig, sc["lead_in_s"])
            (d / f"pass_a_w{w['rank']:02d}.md").write_text(
                f"# system\n\n{system}\n\n# user\n\n{user}\n", encoding="utf-8")
        print(f"wrote {len(windows)} Pass A requests to {d}")
        return {}

    claude = Claude(cfg)
    preflight(claude, sc["model_a"])
    t0 = time.time()
    n = int(np.ceil(meta["duration_s"]))
    # #75: Pass A is saved here before Pass B, so a Pass B failure (or a crash) doesn't mean
    # paying for every window again; the next run re-asks only windows that errored.
    ckpt = stream_dir / f"pass_a_{prompts.version}.json"
    saved = jsonstate.read(ckpt)
    if saved:
        summary = stream_summary(stream_dir, words, n, cfg, claude, prompts)
        redo = [r for r in saved["pass_a"] if r["status"] == "error"]
        by_id = {w.get("id") or f"w{w['rank']:02d}": w for w in windows + saved.get("scan_windows", [])}
        fresh = {r["id"]: r for r in run_pass_a([by_id[r["id"]] for r in redo if r["id"] in by_id],
                                                words, sig, cfg, claude, prompts)}
        results = [fresh.get(r["id"], r) for r in saved["pass_a"]]
        print(f"Pass A from {ckpt.name} (saved before a failed run); {len(redo)} errored window(s) asked again")
        return _finish(stream_dir, cfg, meta, prompts, claude, summary, words, results, ckpt, t0)
    extra: list[dict] = []
    with ThreadPoolExecutor(3) as pool:
        f_sum = pool.submit(stream_summary, stream_dir, words, n, cfg, claude, prompts)
        f_scan = pool.submit(transcript_scan, stream_dir, words, n, cfg, claude, prompts) \
            if sc["transcript_scan"] else None
        f_a = pool.submit(run_pass_a, windows, words, sig, cfg, claude, prompts)
        summary, results = f_sum.result(), f_a.result()
        if f_scan:
            moments = f_scan.result()
            extra = scan_windows(moments, windows, sig, load_chat(stream_dir, meta), sc,
                                 cfg["signals"]["chat_lag_s"])
            print(f"transcript scan: {len(moments)} moments, {len(extra)} new windows")
            results += run_pass_a(extra, words, sig, cfg, claude, prompts)
    jsonstate.write(ckpt, {"prompt_version": prompts.version, "pass_a": results, "scan_windows": extra,
                           "cost_usd": round(claude.cost, 4)})
    return _finish(stream_dir, cfg, meta, prompts, claude, summary, words, results, ckpt, t0)


def _finish(stream_dir: Path, cfg: dict, meta: dict, prompts: Prompts, claude: Claude,
            summary: list[dict], words: list[dict], results: list[dict], ckpt: Path, t0: float) -> dict:
    """Pass B over the kept Pass A results, then candidates.json (the stage's done marker)."""
    kept = [r for r in results if r["status"] == "kept"]
    counts = {s: sum(r["status"] == s for r in results)
              for s in ("kept", "no_clip", "rejected", "refused", "error")}
    print(f"Pass A: {counts} in {time.time() - t0:.0f}s")

    pass_b = run_pass_b(kept, summary, words, cfg, claude, prompts) if kept else {
        "pass": "B", "status": "skipped", "picks": [], "rejected": []}
    out = {"stream_id": stream_dir.name, "title": meta.get("title"),
           "prompt_version": prompts.version, "cost_usd": round(claude.cost, 4),
           "pass_a": results, "pass_b": pass_b}
    jsonstate.write(stream_dir / "candidates.json", out)
    ckpt.unlink(missing_ok=True)

    by_id = {r["id"]: r for r in results}
    print(f"Pass B: {len(pass_b.get('picks', []))} picks; cost ${claude.cost:.3f}")
    for i, p in enumerate(pass_b.get("picks", []), 1):
        c = by_id.get(p["id"], {})
        print(f"{i:>2}. {p['id']} {c.get('in_s', 0):8.1f}-{c.get('out_s', 0):8.1f}s "
              f"[{c.get('category')}, A={c.get('score')}] {p['title']}")
    return out
