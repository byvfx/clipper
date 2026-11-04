"""`clipper investigate`: read an archive subject's own videos, connect the facts, find leads (#108).

Usage:
    clipper investigate facts <subject> [--since DATE] [--before DATE] [--limit N] [--new] [--dry-run]
    clipper investigate leads <subject> [--about "focus"] [--since ...] [--before ...] [--max 10] [--confirm]
    clipper investigate check <subject> [--lead L03] [--trail]
    clipper investigate list  <subject>
    clipper investigate show  <subject> <lead>
    clipper investigate run   <subject> [--since ...] [--about ...]   facts (--new) -> leads -> check

Where `receipts` (#48) starts from a story and finds his old clips, this starts from the person
and finds the story. It reads the archive subject's transcripts (`clipper archive`), so the
subject needs `archive transcribe` first (and `archive index` for --confirm).

1. facts: [investigate] facts_model (Haiku) reads each item in chunk_s chunks with its date and
   title and returns the checkable things said (claims, money and numbers, plans, promises,
   legal / business / health / relationship statements, teases), each with a verbatim quote.
   Every quote is looked up in the item's words.jsonl; a fact whose quote isn't there is
   dropped, so nothing later can rest on a made-up line. One file per item, facts/<item>.json,
   kept while the prompt version, model, chunk size and transcript are unchanged: a rerun
   reads only what is new or re-transcribed. --new takes only items newer than the newest
   one read. --dry-run prints the items, hours and estimated cost and stops.
2. leads: the facts go into facts.sqlite (entities merged across spellings, plus any
   alias -> name pairs in investigate/aliases.json; a name mapped to "self" is him, like his
   channels' names), and code finds candidate threads in his own claims (numbers are
   followed for anyone): a trend (a name or topic suddenly talked about far more), a contradiction (the same thing
   affirmed and denied on different dates), a shift (a number that changes), a dropped
   thread (talked about, then silence; a tease never paid off) and a convergence (offhand
   personal remarks from several videos close together). --about narrows them to a focus.
   [investigate] leads_model (Sonnet) reads each candidate, max_candidates of them, and
   writes a lead or rejects it. Every receipt in a lead is one of the verified facts, with
   its quote and a link at the moment; a lead with fewer than two is dropped. --confirm runs
   `archive ask` on each lead's "what would confirm or kill this" and has check_model weigh
   the windows it finds (quotes verified the same way) and adjust the confidence.
3. check: "is anyone else on this?" One search per source for each kept lead: YouTube
   (yt-dlp search, titles and channels only), the web and Reddit (Brave search, the key
   `clipper cast` uses; Reddit's own feed without one), our own streams' segments, and the
   subject's own video titles. check_model reads the results and labels
   the lead fresh / partly covered / covered with the links that match. The trail is
   investigate/swarm/<lead>.json (--trail prints it). The search swarm (#109) will replace the
   fixed searches with planned rounds and follow-ups.

Files, all in paths.archive/<subject>/investigate/: facts/<item>.json, facts.sqlite,
leads.json (the edit: flip `keep`, change a headline, add `notes`; `leads` again keeps the
old one as leads.<time>.json), leads.md (a readable dossier), swarm/<lead>.json, runs.json
(what each run covered and cost). A run stops before it would spend more than
[investigate] budget_usd. Leads are research for the user: nothing here is uploaded, posted
or scheduled.
"""

from __future__ import annotations

import difflib
import hashlib
import itertools
import json
import math
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import jsonstate
from .archive import load_catalog, subject_dir
from .errors import ClipperError
from .llm import Budget, OverBudget, estimate  # [investigate] budget_usd
from .receipts import tokens

TYPES = ["claim", "money", "number", "plan", "promise", "tease", "legal", "business", "health",
         "relationship", "other"]
PERSONAL = {"money", "plan", "legal", "business", "health", "relationship"}  # convergence keys
MODES = ["stated", "rumor", "joke", "hypothetical", "reading"]
FACTS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["type", "about", "entities", "statement", "mode", "polarity", "measure", "value",
                     "quote"],
        "properties": {
            "type": {"type": "string", "enum": TYPES},
            "about": {"type": "string", "enum": ["self", "other"]},
            "entities": {"type": "array", "items": {"type": "string"}},
            "statement": {"type": "string"},
            "mode": {"type": "string", "enum": MODES},
            "polarity": {"type": "string", "enum": ["affirms", "denies", "neutral"]},
            "measure": {"type": "string"}, "value": {"type": "string"},
            "quote": {"type": "string"}}}}},
}
KINDS = ["trend", "contradiction", "shift", "dropped", "convergence", "about"]
LEADS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["keep", "reject", "headline", "story", "kind", "confidence", "confirm_or_kill",
                 "people", "timeline", "search"],
    "properties": {
        "keep": {"type": "boolean"}, "reject": {"type": "string"},
        "headline": {"type": "string"}, "story": {"type": "string"},
        "kind": {"type": "string", "enum": KINDS},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "confirm_or_kill": {"type": "string"},
        "people": {"type": "array", "items": {"type": "string"}},
        "timeline": {"type": "array", "items": {"type": "string"}},
        "search": {"type": "string"}},
}
CHECK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["coverage", "note", "matches"],
    "properties": {"coverage": {"type": "string", "enum": ["fresh", "partly covered", "covered"]},
                   "note": {"type": "string"},
                   "matches": {"type": "array", "items": {"type": "integer"}}},
}
CONFIRM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "confidence", "note", "used"],
    "properties": {"verdict": {"type": "string", "enum": ["supports", "undercuts", "mixed", "nothing"]},
                   "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                   "note": {"type": "string"},
                   "used": {"type": "array", "items": {"type": "integer"}}},
}
CONFIDENCE = {"high": 0, "medium": 1, "low": 2}
MAX_THREAD_FACTS = 60   # facts one candidate shows the leads model (evenly spread over time)
COMMON_SHARE = 0.25     # a name in more of the videos than this is the background, not a thread
BASELINE_WEEKS = 26
OUT_RATIO = 0.65        # facts: output tokens per input token, for the estimate (Sept-Oct 2026 jer run)


def inv_dir(cfg: dict, subject: str) -> Path:
    return subject_dir(cfg, subject) / "investigate"


def with_budget(cfg: dict, usd: float) -> dict:
    """cfg with [investigate] budget_usd set to what a later step may still spend."""
    return {**cfg, "investigate": {**cfg["investigate"], "budget_usd": max(0.0, usd)}}


def log_run(d: Path, rec: dict) -> None:
    with jsonstate.update(d / "runs.json", []) as runs:
        runs.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), **rec})


def llm_setup(cfg: dict, model: str):
    from .archive_index import quiet_http
    from .llm import Claude, preflight
    from .prompts import Prompts

    claude = Claude(cfg)
    quiet_http()
    preflight(claude, model)
    prompts = Prompts(cfg)
    prompts.version = cfg["investigate"]["prompt_version"]
    return claude, prompts


# ---------- items ----------

def items(cfg: dict, subject: str, transcribed: bool = True) -> list[dict]:
    """The subject's items, newest first: id, date, title, channel, url, source, duration_s
    (dates from the archive index, else each item's meta.json). transcribed: only items with
    both transcript.json and words.jsonl (transcribe writes the words last)."""
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise ClipperError(f"no catalog for {subject!r}: clipper archive add {subject} <url>")
    known: dict[str, dict] = {}
    if (d / "index.sqlite").exists():
        db = sqlite3.connect(d / "index.sqlite")
        keys = ("id", "title", "date", "channel", "url", "source", "duration_s")
        known = {r[0]: dict(zip(keys, r, strict=True))
                 for r in db.execute(f"SELECT {', '.join(keys)} FROM items")}
        db.close()
    out = []
    for i, it in cat.items():
        if transcribed and not ((d / i / "transcript.json").exists() and (d / i / "words.jsonl").exists()):
            continue
        row = known.get(i) or {**it, **jsonstate.read(d / i / "meta.json", {}), "id": i}
        out.append({k: row.get(k) or ("" if k != "duration_s" else 0.0)
                    for k in ("id", "title", "date", "channel", "url", "source", "duration_s")})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def in_range(day: str, since: str | None, before: str | None) -> bool:
    return (not since or day >= since) and (not before or day < before)


# ---------- facts ----------

def signature(ic: dict, tp: Path) -> str:
    """The cache key of an item's facts: prompt, model, chunking and the transcript itself."""
    st = tp.stat()
    sig = f"{ic['prompt_version']}|{ic['facts_model']}|{ic['chunk_s']}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


class Words:
    """An item's words as tokens: each token's start time, token -> positions, and the word
    it came from (to give a receipt his own wording, not the model's)."""

    def __init__(self, words: list[dict]):
        self.words, self.toks, self.starts, self.src = words, [], [], []
        self.where: dict[str, list[int]] = defaultdict(list)
        for n, w in enumerate(words):
            for tok in tokens(w.get("word") or ""):
                self.where[tok].append(len(self.toks))
                self.toks.append(tok)
                self.starts.append(w["start"])
                self.src.append(n)

    def text(self, at: int, n: int) -> str:
        """The words he said for n tokens from token at."""
        a, b = self.src[at], self.src[min(at + n, len(self.src)) - 1]
        return " ".join((w.get("word") or "").strip() for w in self.words[a:b + 1])


NUM_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
             "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
             "thousand", "million", "billion", "half", "k"}
NEG = {"not", "no", "never", "nothing", "none", "nobody", "nowhere", "neither", "nor", "dont", "didnt",
       "doesnt", "isnt", "wasnt", "arent", "werent", "cant", "wont", "couldnt", "wouldnt", "shouldnt",
       "havent", "hasnt", "hadnt", "aint"}


def numbers(toks: list[str]) -> set[str]:
    return {t for t in toks if t.isdigit() or t in NUM_WORDS}


def negated(toks: list[str]) -> bool:
    return any(t in NEG or t.endswith("n't") for t in toks)


def locate(ws: Words, quote: str, lo: float | None = None, hi: float | None = None) -> int | None:
    """Where the quote starts among the item's tokens between lo and hi seconds, or None when
    it isn't really there: at least 3 tokens, 70% of them in place at one offset (Whisper and
    the model differ on a word here and there), and the numbers and any negation unchanged
    (a near match that says "ninety" where he said "forty" is a different claim)."""
    q = tokens(quote)
    if len(q) < 3:
        return None
    votes: Counter[int] = Counter()
    for j, tok in enumerate(q):
        for pos in set(ws.where.get(tok, ())):
            if (lo is None or ws.starts[pos] >= lo) and (hi is None or ws.starts[pos] <= hi):
                votes[pos - j] += 1
    if not votes:
        return None
    off, n = votes.most_common(1)[0]
    if n < max(3, math.ceil(0.7 * len(q))):
        return None
    at = max(0, off)
    core, near = ws.toks[at:at + len(q)], ws.toks[max(0, at - 2):at + len(q) + 2]
    if not (numbers(q) <= numbers(near) and numbers(core) <= numbers(q)) or negated(q) != negated(core):
        return None
    return at


def read_words(idir: Path) -> list[dict]:
    p = idir / "words.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def item_facts(cfg: dict, subject: str, it: dict, claude, system: str, budget: Budget) -> dict:
    from .archive_index import windows
    from .timefmt import clock

    ic = cfg["investigate"]
    idir = subject_dir(cfg, subject) / it["id"]
    tp = idir / "transcript.json"
    sig = signature(ic, tp)
    segs = jsonstate.load(tp).get("segments") or []
    ws = Words(read_words(idir))
    chunks = windows(segs, ic["chunk_s"])
    kept, dropped, refused = [], 0, 0
    for k, (start, text) in enumerate(chunks):
        end = chunks[k + 1][0] if k + 1 < len(chunks) else it["duration_s"] or start
        user = (f"Video: {it['title']}\nChannel: {it['channel']}\nDate: {it['date'] or 'unknown'}\n"
                f"Part {k + 1} of {len(chunks)} ({clock(start)}-{clock(end)})\n\n<transcript>\n{text}\n</transcript>")
        est = estimate(cfg, ic["facts_model"], (len(system) + len(user)) / 4, len(user) / 4 * OUT_RATIO)
        budget.reserve(est)
        try:
            out, _ = claude.json_call(ic["facts_model"], system, user, FACTS_SCHEMA, ic["facts_effort"])
        finally:
            budget.release(est)
        if out is None:
            refused += 1
            continue
        for f in out.get("facts", []):
            at = locate(ws, f["quote"], start - 1, end + 1)  # in this chunk, not an earlier repeat
            if at is None:
                dropped += 1
                continue
            kept.append({**f, "id": f"{it['id']}:{len(kept)}", "t": round(ws.starts[at], 2),
                         "quote": ws.text(at, len(tokens(f["quote"])))})
    return {"item": it["id"], "key": sig, "model": ic["facts_model"], "prompt_version": ic["prompt_version"],
            "read_at": datetime.now(UTC).isoformat(timespec="seconds"),
            **{k: it[k] for k in ("date", "title", "channel", "url", "source", "duration_s")},
            "chunks": len(chunks), "dropped": dropped, "refused": refused, "facts": kept}


def facts_todo(cfg: dict, subject: str, since: str | None, before: str | None, limit: int | None,
               new: bool) -> tuple[list[dict], int]:
    """(items whose facts are missing or stale, items already current in the range)."""
    ic = cfg["investigate"]
    d = inv_dir(cfg, subject) / "facts"
    if not since and ic["since_days"]:
        since = (date.today() - timedelta(days=ic["since_days"])).isoformat()
    every = items(cfg, subject)
    if new:  # from the newest item read on (that day again: the cache skips what's done)
        done = [it["date"] for it in every if it["date"] and (d / f"{it['id']}.json").exists()]
        since = max([since or "", *done]) or None
    picked = [it for it in every if in_range(it["date"], since, before)]
    todo, current = [], 0
    root = subject_dir(cfg, subject)
    for it in picked:
        rec = jsonstate.read(d / f"{it['id']}.json", {})
        if rec.get("key") == signature(ic, root / it["id"] / "transcript.json"):
            current += 1
        else:
            todo.append(it)
    return (todo[:limit] if limit else todo), current


def run_facts(cfg: dict, subject: str, since: str | None = None, before: str | None = None,
              limit: int | None = None, new: bool = False, dry_run: bool = False) -> float:
    """Read the items not read yet; returns what it spent."""
    ic = cfg["investigate"]
    d = inv_dir(cfg, subject)
    todo, current = facts_todo(cfg, subject, since, before, limit, new)
    hours = sum(it["duration_s"] for it in todo) / 3600
    chars_per_s = sample_rate(cfg, subject, todo)
    tok_in = hours * 3600 * chars_per_s / 4
    est = estimate(cfg, ic["facts_model"], tok_in * 1.08, tok_in * OUT_RATIO)  # + prompt per chunk
    span = f"{todo[-1]['date'] or '?'} to {todo[0]['date'] or '?'}" if todo else ""
    print(f"{subject}: {len(todo)} items to read ({hours:.1f} h, {span}), {current} already read; "
          f"~{tok_in / 1e6:.1f}M tokens in, ~${est:.2f} on {ic['facts_model']}")
    if dry_run or not todo:
        return 0.0
    claude, prompts = llm_setup(cfg, ic["facts_model"])
    prompts.vars.update(subject=subject, channels=", ".join(sorted({it["channel"] for it in todo if it["channel"]})))
    system = prompts.get("investigate_facts")
    budget = Budget(ic["budget_usd"], claude)
    (d / "facts").mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    t0, done, n_facts, n_dropped, failed, why = time.time(), 0, 0, 0, 0, ""

    def one(it: dict) -> dict | None:
        if stop.is_set():
            return None
        return item_facts(cfg, subject, it, claude, system, budget)

    with ThreadPoolExecutor(ic["parallel"]) as pool:
        futs = {pool.submit(one, it): it for it in todo}
        for f in as_completed(futs):
            it = futs[f]
            try:
                rec = f.result()
            except OverBudget as e:
                if not stop.is_set():
                    why = str(e)
                    print(f"stopping: {e}", flush=True)
                stop.set()
                continue
            except Exception as e:  # noqa: BLE001  # one bad item doesn't end the run
                failed += 1
                print(f"  {it['id']} failed: {e}", flush=True)
                continue
            if rec is None:
                continue
            jsonstate.write(d / "facts" / f"{it['id']}.json", rec, indent=1)
            done += 1
            n_facts += len(rec["facts"])
            n_dropped += rec["dropped"]
            if done % 25 == 0 or done == len(todo):
                print(f"[{done}/{len(todo)}] {n_facts} facts, ~${claude.cost:.2f}, {time.time() - t0:.0f} s",
                      flush=True)
    left = len(todo) - done - failed
    print(f"{subject}: {done} items read, {n_facts} facts kept, {n_dropped} dropped (quote not in the "
          f"words), {failed} failed; ~${claude.cost:.2f}" + (f"; {left} left over budget" if why else ""))
    log_run(d, {"step": "facts", "since": since, "before": before, "items": done, "facts": n_facts,
                "dropped": n_dropped, "failed": failed, "usd": round(claude.cost, 4), "stopped": why})
    return claude.cost


def sample_rate(cfg: dict, subject: str, todo: list[dict], n: int = 40) -> float:
    """Transcript characters per second of video, from up to n of the items."""
    root = subject_dir(cfg, subject)
    chars = secs = 0.0
    for it in todo[:: max(1, len(todo) // n)][:n]:
        if not it["duration_s"]:
            continue
        segs = jsonstate.read(root / it["id"] / "transcript.json", {}).get("segments") or []
        chars += sum(len(s.get("text") or "") for s in segs)
        secs += it["duration_s"]
    return chars / secs if secs else 15.0  # ~15 characters a second of talk


# ---------- the facts index ----------

def canon(name: str) -> str:
    """A name as a key: lower case, no possessive, punctuation or leading "the"."""
    s = name.lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^the ", "", " ".join(s.split()))
    return s


def squash(key: str) -> str:
    """"the quartering", "thequartering", "thequartering com" -> "quartering"."""
    s = key.replace(" ", "")
    s = s.removesuffix("com") if len(s) > 6 else s
    return s.removeprefix("the") if len(s) > 6 else s


def merge_aliases(counts: Counter, extra: dict[str, str] | None = None) -> dict[str, str]:
    """key -> the key it is merged into: aliases.json pairs, then names that are the same
    without spaces, "the" or ".com", then Whisper's spellings onto a more frequent
    near-identical name (same first letters, ratio >= 0.88, 6+ letters), then a lone surname
    onto the one full name it ends."""
    out = {canon(a): canon(b) for a, b in (extra or {}).items() if canon(a) and canon(b)}
    kept: dict[str, list[str]] = defaultdict(list)  # first two letters -> kept keys
    squashed: dict[str, str] = {}
    for key, _ in counts.most_common():
        if key in out:
            continue
        if squash(key) in squashed:
            out[key] = squashed[squash(key)]
            continue
        squashed[squash(key)] = key
        if len(key) >= 6:
            near = [k for k in kept[key[:2]] if abs(len(k) - len(key)) <= 2
                    and difflib.SequenceMatcher(None, k, key).ratio() >= 0.88]
            if near:
                out[key] = near[0]
                continue
        kept[key[:2]].append(key)
    full = defaultdict(list)
    for key in counts:
        if " " in key and key not in out:
            full[key.rsplit(" ", 1)[1]].append(key)
    for key in counts:
        if " " not in key and key not in out and len(full.get(key, [])) == 1 and len(key) >= 4:
            out[key] = full[key][0]
    for k in list(out):  # follow chains: a -> b -> c
        seen = {k}
        while out[k] in out and out[k] not in seen:
            seen.add(out[k])
            out[k] = out[out[k]]
    return out


def parse_value(s: str) -> float | None:
    """ "$1.2 million" -> 1200000.0, "250k" -> 250000.0, "" -> None."""
    m = re.search(r"(-?\d[\d,]*(?:\.\d+)?)\s*(k|m|b|thousand|million|billion)?\b", (s or "").lower())
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
    return v * mult.get(m.group(2) or "", 1)


def load_facts(d: Path) -> list[dict]:
    """Every verified fact from facts/*.json, with its item's date, title and link fields."""
    out = []
    for p in sorted((d / "facts").glob("*.json")):
        rec = jsonstate.read(p, {})
        meta = {k: rec.get(k) or "" for k in ("item", "date", "title", "channel", "url", "source")}
        out += [{**f, **meta} for f in rec.get("facts", [])]
    return out


def add_keys(facts: list[dict], aliases: dict[str, str] | None = None) -> None:
    """Each fact's canonical entities and thread keys: e:<name>, m:<measure>|<whose>, s:<type>.
    His own names (his channels' names, and aliases.json names mapped to "self") are him, not
    a thread."""
    counts = Counter(canon(e) for f in facts for e in f["entities"] if canon(e))
    merged = merge_aliases(counts, aliases)
    me = {squash(canon(f["channel"])) for f in facts if canon(f.get("channel") or "")} | {"self"}
    n_items = len({f["item"] for f in facts}) or 1
    for f in facts:
        names = {merged.get(canon(e), canon(e)) for e in f["entities"] if canon(e)}
        f["names"] = sorted(n for n in names if squash(n) not in me)
    per_name: dict[str, set] = defaultdict(set)
    for f in facts:
        for e in f["names"]:
            per_name[e].add(f["item"])
    common = {e for e, its in per_name.items() if len(its) >= 20 and len(its) / n_items > COMMON_SHARE}
    measures = merge_aliases(Counter(canon(f["measure"]) for f in facts if canon(f.get("measure") or "")))
    for f in facts:
        keys = [f"e:{e}" for e in f["names"] if e not in common]
        f["num"] = parse_value(f.get("value", ""))
        if canon(f.get("measure") or "") and f["num"] is not None:  # whose number: his, or the first name's
            whose = f["names"][0] if f.get("about") == "other" and f["names"] else "self"
            keys.append(f"m:{measures.get(canon(f['measure']), canon(f['measure']))}|{whose}")
        if f.get("about") == "self" and f["type"] in PERSONAL:
            keys.append(f"s:{f['type']}")
        f["keys"] = keys


def build_index(d: Path) -> list[dict]:
    """facts.sqlite from facts/*.json (rebuilt whole; no model calls), and the facts with keys."""
    facts = load_facts(d)
    add_keys(facts, jsonstate.read(d / "aliases.json", {}))
    p = d / "facts.sqlite"
    p.unlink(missing_ok=True)
    db = sqlite3.connect(p)
    db.executescript("""
        CREATE TABLE facts (id TEXT PRIMARY KEY, item TEXT, date TEXT, title TEXT, channel TEXT,
            url TEXT, source TEXT, t REAL, type TEXT, about TEXT, mode TEXT, polarity TEXT,
            statement TEXT, quote TEXT, measure TEXT, value TEXT, num REAL);
        CREATE TABLE entities (fact TEXT, raw TEXT, name TEXT);
        CREATE TABLE keys (fact TEXT, key TEXT);
        CREATE INDEX entities_name ON entities (name);
        CREATE INDEX keys_key ON keys (key);""")
    cols = ("id", "item", "date", "title", "channel", "url", "source", "t", "type", "about", "mode",
            "polarity", "statement", "quote", "measure", "value", "num")
    db.executemany(f"INSERT OR REPLACE INTO facts VALUES ({', '.join('?' * len(cols))})",
                   [tuple(f.get(c) for c in cols) for f in facts])
    db.executemany("INSERT INTO entities VALUES (?, ?, ?)",
                   [(f["id"], e, canon(e)) for f in facts for e in f["entities"]])
    db.executemany("INSERT INTO keys VALUES (?, ?)", [(f["id"], k) for f in facts for k in f["keys"]])
    db.commit()
    db.close()
    return facts


# ---------- candidate threads (code, no model) ----------

def day(s: str) -> int:
    return date.fromisoformat(s[:10]).toordinal()


def n_items(fs: list[dict]) -> int:
    return len({f["item"] for f in fs})


def spread(fs: list[dict], n: int = MAX_THREAD_FACTS) -> list[dict]:
    fs = sorted(fs, key=lambda f: (f["date"], f["t"]))
    if len(fs) <= n:
        return fs
    if n < 2:
        return fs[-1:]
    return [fs[round(k * (len(fs) - 1) / (n - 1))] for k in range(n)]  # first and last always in


def label(key: str) -> str:
    kind, name = key.split(":", 1)
    if kind == "m":
        name, whose = name.rsplit("|", 1)
        return f"the number for {name}" + ("" if whose == "self" else f" ({whose})")
    return {"s": f"his own {name} talk"}.get(kind, name)


def groups(facts: list[dict]) -> dict[str, list[dict]]:
    g = defaultdict(list)
    for f in facts:
        if f["date"]:
            for k in f["keys"]:
                g[k].append(f)
    return g


def trends(g: dict, newest: int, first: int, ic: dict) -> list[dict]:
    """first: the oldest date read. The baseline is the up to BASELINE_WEEKS read before the
    recent weeks; with under two weeks of it there is nothing to compare against."""
    out, recent_d = [], ic["trend_weeks"] * 7
    base_lo = max(newest - recent_d - BASELINE_WEEKS * 7, first)
    base_weeks = (newest - recent_d - base_lo) / 7
    if base_weeks < 2:
        return []
    for key, fs in g.items():
        recent = [f for f in fs if day(f["date"]) > newest - recent_d]
        base = [f for f in fs if base_lo <= day(f["date"]) <= newest - recent_d]
        r_rate, b_rate = n_items(recent) / ic["trend_weeks"], n_items(base) / base_weeks
        if n_items(recent) >= ic["min_facts_per_thread"] and r_rate >= 3 * max(b_rate, 0.25):
            out.append({"kind": "trend", "key": key, "score": r_rate / max(b_rate, 0.25),
                        "why": f"{label(key)}: {n_items(recent)} videos in the last {ic['trend_weeks']} weeks, "
                               f"{n_items(base)} in the {base_weeks:.0f} before",
                        "facts": spread(recent, MAX_THREAD_FACTS - 10) + spread(base, 10)})
    return out


def contradictions(g: dict, ic: dict) -> list[dict]:
    out = []
    for key, fs in g.items():
        if key.startswith("s:"):  # "his business talk" affirmed and denied is about different things
            continue
        by_type = defaultdict(list)
        for f in fs:
            if f["mode"] in ("stated", "rumor") and f["polarity"] != "neutral":
                by_type[f["type"]].append(f)
        for typ, tf in by_type.items():
            yes = [f for f in tf if f["polarity"] == "affirms"]
            no = [f for f in tf if f["polarity"] == "denies"]
            if yes and no and {f["date"] for f in yes} != {f["date"] for f in no} and n_items(tf) >= 2:
                out.append({"kind": "contradiction", "key": key, "score": min(n_items(yes), n_items(no)),
                            "why": f"{label(key)} ({typ}): affirmed in {n_items(yes)} videos, denied in {n_items(no)}",
                            "facts": spread(tf)})
    return out


def shifts(g: dict, ic: dict) -> list[dict]:
    out = []
    for key, fs in g.items():
        if not key.startswith("m:"):
            continue
        vals = [f for f in fs if f["num"] is not None and f["mode"] == "stated"]
        if len({f["date"] for f in vals}) < max(3, ic["min_facts_per_thread"]):
            continue
        nums = [f["num"] for f in sorted(vals, key=lambda f: f["date"])]
        lo, hi = min(nums), max(nums)
        if hi <= 0 or (hi - lo) / hi < 0.25:
            continue
        ups = sum(b > a for a, b in itertools.pairwise(nums))
        downs = sum(b < a for a, b in itertools.pairwise(nums))
        out.append({"kind": "shift", "key": key, "score": len(set(nums)) * (2 if ups and downs else 1),
                    "why": f"{label(key)}: {len(vals)} values from {lo:,.0f} to {hi:,.0f}"
                           + (", up and down" if ups and downs else ""),
                    "facts": spread(vals)})
    return out


def dropped(g: dict, newest: int, ic: dict) -> list[dict]:
    out, quiet = [], ic["dropped_weeks"] * 7
    for key, fs in g.items():
        last = max(day(f["date"]) for f in fs)
        first = min(day(f["date"]) for f in fs)
        if (n_items(fs) >= ic["min_facts_per_thread"] and newest - quiet - 365 < last < newest - quiet
                and last - first >= 14):
            out.append({"kind": "dropped", "key": key, "score": n_items(fs),
                        "why": f"{label(key)}: {n_items(fs)} videos, then nothing since "
                               f"{date.fromordinal(last).isoformat()}", "facts": spread(fs)})
        teases = [f for f in fs if f["type"] in ("tease", "promise")]
        for t in teases:
            later = [f for f in fs if f["date"] > t["date"]]
            if not later and day(t["date"]) < newest - 14:
                out.append({"kind": "dropped", "key": key, "score": 1 + n_items(fs) / 10,
                            "why": f"{label(key)}: {t['type']} on {t['date']} ({t['statement']}), "
                                   "never mentioned again", "facts": spread(fs)})
                break
    return out


def convergences(g: dict, ic: dict) -> list[dict]:
    out, w = [], ic["window_days"]
    for key, fs in g.items():
        if not key.startswith("s:"):
            continue
        fs = sorted(fs, key=lambda f: f["date"])
        best, lo = (0, 0, 0), 0
        for hi in range(len(fs)):
            while day(fs[hi]["date"]) - day(fs[lo]["date"]) > w:
                lo += 1
            n = n_items(fs[lo:hi + 1])
            if n > best[0]:
                best = (n, lo, hi)
        n, lo, hi = best
        span = max(day(fs[-1]["date"]) - day(fs[0]["date"]), w)
        usual = n_items(fs) * w / span  # videos in a window at his usual rate
        if n >= max(ic["min_facts_per_thread"], 2 * usual):
            win = fs[lo:hi + 1]
            out.append({"kind": "convergence", "key": key, "score": n / max(usual, 1),
                        "why": f"{label(key)}: {n} videos between {win[0]['date']} and {win[-1]['date']} "
                               f"(~{usual:.0f} usual)",
                        "facts": spread(win)})
    return out


def matches_about(f: dict, about: str) -> bool:
    words = [w for w in canon(about).split() if len(w) >= 3] or canon(about).split()
    hay = canon(" ".join([f["statement"], f["quote"], *f["entities"], *f.get("names", [])]))
    return canon(about) in hay or all(w in hay.split() for w in words)


def candidates(facts: list[dict], ic: dict, about: str | None = None, limit: int | None = None) -> list[dict]:
    """Candidate threads, the best of each kind in turn, overlapping ones dropped."""
    if about:
        facts = [f for f in facts if matches_about(f, about)]
    dated = [f for f in facts if f["date"]]
    if not dated:
        return []
    newest, first = max(day(f["date"]) for f in dated), min(day(f["date"]) for f in dated)
    # Threads are about his own claims; what he says of others is mostly the news he covers.
    # Numbers are followed for anyone (a fundraiser total, a lawsuit's damages).
    g = groups([f for f in dated if f.get("about") == "self"])
    kinds = [trends(g, newest, first, ic), contradictions(g, ic), shifts(groups(dated), ic),
             dropped(g, newest, ic), convergences(g, ic)]
    if about and n_items(dated) >= 2:
        kinds.insert(0, [{"kind": "about", "key": f"about:{about}", "score": n_items(dated),
                          "why": f"everything on {about!r}: {len(dated)} facts in {n_items(dated)} videos",
                          "facts": spread(dated)}])
    for k in kinds:
        k.sort(key=lambda c: -c["score"])
    out: list[dict] = []
    limit = limit or ic["max_candidates"]
    for rank in range(max((len(k) for k in kinds), default=0)):
        for k in kinds:
            if rank < len(k) and len(out) < limit:
                c = k[rank]
                ids = {f["id"] for f in c["facts"]}
                if not any(len(ids & {f["id"] for f in o["facts"]}) > 0.7 * len(ids) for o in out):
                    out.append(c)
    return out


def still_silent(cfg: dict, subject: str, cands: list[dict]) -> list[dict]:
    """A dropped thread is only dropped if the name isn't in any later transcript: the facts
    keep the checkable lines, the archive index (`archive index`) has every word. Without an
    index the candidates go through as they are."""
    from .archive_index import hits, open_index

    try:
        db = open_index(cfg, subject)
    except ClipperError:
        return cands
    out = []
    for c in cands:
        name = c["key"][2:] if c["kind"] == "dropped" and c["key"].startswith("e:") else ""
        if name:
            last = max(f["date"] for f in c["facts"])
            after = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
            try:
                later = hits(db, f'"{name}"', after, None, 1)
            except sqlite3.OperationalError:
                later = []
            if later:
                continue
            c["why"] += "; not in any later transcript"
        out.append(c)
    db.close()
    return out


# ---------- leads (model) ----------

def render(cand: dict) -> str:
    lines = [f"Candidate thread ({cand['kind']}): {cand['why']}", "", "<facts>"]
    for f in cand["facts"]:
        num = f" [{f['measure']}: {f['value']}]" if f.get("measure") and f.get("value") else ""
        lines.append(f"{f['id']} | {f['date']} | {f['title'][:80]} | {f['type']}, {f['mode']}, "
                     f"{f['polarity']}{num}\n  {f['statement']}\n  quote: \"{f['quote']}\"")
    return "\n".join(lines + ["</facts>"])


def make_lead(out: dict | None, cand: dict) -> dict | None:
    """The model's lead with receipts built from our verified facts, or None (rejected, or
    fewer than two receipts from the thread)."""
    from .archive_index import link

    if not out or not out["keep"]:
        return None
    by_id = {f["id"]: f for f in cand["facts"]}
    receipts = []
    for fid in dict.fromkeys(out["timeline"]):
        f = by_id.get(fid)
        if f:
            receipts.append({"fact": fid, "item": f["item"], "date": f["date"], "title": f["title"],
                             "channel": f["channel"], "t": f["t"], "statement": f["statement"],
                             "quote": f["quote"], "mode": f["mode"],
                             "link": link(f["url"], f["source"], f["t"]) if f["url"] else ""})
    if len(receipts) < 2:
        return None
    receipts.sort(key=lambda r: (r["date"], r["t"]))
    return {"keep": True, "headline": out["headline"], "story": out["story"], "kind": out["kind"],
            "confidence": out["confidence"], "confirm_or_kill": out["confirm_or_kill"],
            "people": out["people"], "search": out["search"], "thread": cand["why"],
            "first": receipts[0]["date"], "last": receipts[-1]["date"], "receipts": receipts,
            "coverage": {"label": "unchecked"}, "notes": ""}


def rank(leads: list[dict], n: int) -> list[dict]:
    """Best first (confidence, then receipts), dropping a lead that mostly repeats a better one."""
    leads = sorted(leads, key=lambda x: x["last"], reverse=True)  # ties: the more recent first
    leads.sort(key=lambda x: (CONFIDENCE[x["confidence"]], -len(x["receipts"])))
    out: list[dict] = []
    for lead in leads:
        ids = {r["fact"] for r in lead["receipts"]}
        if any(len(ids & {r["fact"] for r in o["receipts"]}) > 0.5 * len(ids) for o in out):
            continue
        out.append(lead)
    out = out[:n]
    for k, lead in enumerate(out, 1):
        lead["id"] = f"L{k:02d}"
    return out


def load_leads(cfg: dict, subject: str) -> dict:
    p = inv_dir(cfg, subject) / "leads.json"
    if not p.exists():
        raise ClipperError(f"no leads for {subject!r} yet: clipper investigate leads {subject}")
    return jsonstate.load(p)


def save_leads(cfg: dict, subject: str, data: dict) -> None:
    d = inv_dir(cfg, subject)
    d.mkdir(parents=True, exist_ok=True)
    jsonstate.write(d / "leads.json", data)
    (d / "leads.md").write_text(dossier(data), encoding="utf-8")


def run_leads(cfg: dict, subject: str, about: str | None = None, since: str | None = None,
              before: str | None = None, max_leads: int | None = None, confirm: bool = False) -> float:
    ic = cfg["investigate"]
    d = inv_dir(cfg, subject)
    facts = [f for f in build_index(d) if in_range(f["date"], since, before)]
    if not facts:
        raise ClipperError(f"no facts for {subject!r} in that range: clipper investigate facts {subject}")
    cands = still_silent(cfg, subject, candidates(facts, ic, about))
    kinds = Counter(c["kind"] for c in cands)
    print(f"{subject}: {len(facts)} facts from {n_items(facts)} videos -> {len(cands)} candidate threads "
          f"({', '.join(f'{n} {k}' for k, n in kinds.items()) or 'none'})")
    if not cands:
        print("nothing to write up: read more items (investigate facts) or lower "
              "[investigate] min_facts_per_thread")
        return 0.0
    claude, prompts = llm_setup(cfg, ic["leads_model"])
    prompts.vars.update(subject=subject)
    system = prompts.get("investigate_leads")
    budget = Budget(ic["budget_usd"], claude)

    rejected = []

    def one(c: dict) -> dict | None:
        user = render(c)
        est = estimate(cfg, ic["leads_model"], (len(system) + len(user)) / 4, 1500)
        budget.reserve(est)
        try:
            out, _ = claude.json_call(ic["leads_model"], system, user, LEADS_SCHEMA, ic["leads_effort"])
        finally:
            budget.release(est)
        lead = make_lead(out, c)
        if not lead:  # kept in leads.json, to tune the heuristics and prompts by
            rejected.append({"kind": c["kind"], "thread": c["why"],
                             "reason": (out or {}).get("reject") or ("refused" if out is None else "too few receipts")})
        return lead

    leads, why = [], ""
    with ThreadPoolExecutor(ic["parallel"]) as pool:
        for f in as_completed([pool.submit(one, c) for c in cands]):
            try:
                if lead := f.result():
                    leads.append(lead)
            except OverBudget as e:
                why = str(e)
    if why:
        print(f"stopped early: {why}")
    leads = rank(leads, max_leads or ic["max_leads"])
    p = d / "leads.json"
    if p.exists():
        p.replace(p.with_name(f"leads.{datetime.now():%Y%m%d-%H%M%S}.json"))
    data = {"subject": subject, "about": about or "", "since": since or "", "before": before or "",
            "made_at": datetime.now(UTC).isoformat(timespec="seconds"), "model": ic["leads_model"],
            "candidates": len(cands), "cost_usd": round(claude.cost, 3), "leads": leads,
            "rejected": sorted(rejected, key=lambda r: r["kind"])}
    save_leads(cfg, subject, data)
    print(f"{len(leads)} leads from {len(cands)} candidates (~${claude.cost:.2f}); {d / 'leads.md'}\n")
    log_run(d, {"step": "leads", "about": about or "", "candidates": len(cands), "leads": len(leads),
                "usd": round(claude.cost, 4), "stopped": why})
    spent = claude.cost
    if confirm and leads:
        spent += run_confirm(with_budget(cfg, ic["budget_usd"] - spent), subject)  # posts when done
    elif leads:
        maybe_post(cfg, subject)
    show_list(cfg, subject)
    return spent


# ---------- confirm: the subject's own archive, for or against ----------

def run_confirm(cfg: dict, subject: str, only: str | None = None) -> float:
    from .archive_index import answer, link

    ic = cfg["investigate"]
    data = load_leads(cfg, subject)
    claude, prompts = llm_setup(cfg, ic["check_model"])
    prompts.vars.update(subject=subject)
    system = prompts.get("investigate_confirm")
    budget = Budget(ic["budget_usd"], claude)
    root = subject_dir(cfg, subject)
    window_s = cfg["archive"]["index_window_s"]
    for lead in data["leads"]:
        if not lead["keep"] or (only and lead["id"] != only):
            continue
        print(f"\n{lead['id']} {lead['headline']}: {lead['confirm_or_kill']}")
        try:
            budget.reserve(0.02)
        except OverBudget as e:
            print(f"stopping: {e}")
            break
        try:
            picks = answer(cfg, subject, f"{lead['headline']}. {lead['confirm_or_kill']}", None, None, claude)
        finally:
            budget.release(0.02)
        seen = {(r["item"], int(r["t"] // 30)) for r in lead["receipts"]}
        evidence = []
        for h in picks:
            ws = Words(read_words(root / h["item"]))
            at = locate(ws, h["quote"], h["start"] - 1, h["start"] + window_s + 60)  # in its search window
            if at is None or (h["item"], int(ws.starts[at] // 30)) in seen:
                continue
            seen.add((h["item"], int(ws.starts[at] // 30)))
            evidence.append({"item": h["item"], "date": h["date"], "title": h["title"], "channel": h["channel"],
                             "t": round(ws.starts[at], 2), "quote": ws.text(at, len(tokens(h["quote"]))),
                             "why": h["why"], "link": link(h["url"], h["source"], ws.starts[at])})
        evidence = evidence[:10]
        if not evidence:
            lead["confirm"] = {"verdict": "nothing", "note": "no more in the archive", "evidence": []}
            continue
        timeline = "\n".join(f"- {r['date']}: \"{r['quote']}\"" for r in lead["receipts"])
        user = (f"Lead: {lead['headline']}\n{lead['story']}\nIts receipts (verified quotes):\n{timeline}\n"
                f"What would confirm or kill it: {lead['confirm_or_kill']}\n"
                f"Confidence so far: {lead['confidence']}\n\n<windows>\n"
                + "\n\n".join(f"[{n}] {e['date']} | {e['title']}\n\"{e['quote']}\"\n({e['why']})"
                              for n, e in enumerate(evidence)) + "\n</windows>")
        est = estimate(cfg, ic["check_model"], (len(system) + len(user)) / 4, 400)
        try:
            budget.reserve(est)
        except OverBudget as e:
            print(f"stopping: {e}")
            break
        try:
            out, _ = claude.json_call(ic["check_model"], system, user, CONFIRM_SCHEMA, ic["check_effort"])
        finally:
            budget.release(est)
        if not out:
            continue
        used = [evidence[n] for n in out["used"] if 0 <= n < len(evidence)]
        lead["confirm"] = {"verdict": out["verdict"], "note": out["note"], "was": lead["confidence"],
                           "evidence": used, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        lead["confidence"] = out["confidence"]
        print(f"  {out['verdict']}, {lead['confirm']['was']} -> {out['confidence']}: {out['note']}")
    save_leads(cfg, subject, data)
    log_run(inv_dir(cfg, subject), {"step": "confirm", "lead": only or "", "usd": round(claude.cost, 4)})
    print(f"\nconfirm ~${claude.cost:.2f}")
    maybe_post(cfg, subject)
    return claude.cost


# ---------- check: is anyone else on it? ----------

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "his", "her", "their", "about",
        "from", "at", "by", "is", "was", "are", "be", "this", "that", "it", "its", "as", "after", "before",
        "over", "new", "says", "said", "video", "videos"}


def terms(query: str) -> list[str]:
    return [w for w in canon(query).split() if len(w) >= 3 and w not in STOP]


def hit(text: str, ts: list[str]) -> bool:
    """At least half the terms (two or more when there are) in the text."""
    words = set(canon(text).split())
    need = max(1, min(2, len(ts)), math.ceil(len(ts) / 2))
    return sum(t in words for t in ts) >= need


def youtube_search(query: str, n: int) -> list[dict]:
    from . import ytdl

    r = ytdl.run("--flat-playlist", "-J", f"ytsearch{n}:{query}")
    if r.returncode:
        raise RuntimeError(ytdl.last_error(r))
    return [{"source": "youtube", "title": e.get("title") or "", "channel": e.get("channel") or "",
             "url": f"https://www.youtube.com/watch?v={e['id']}", "date": "",
             "views": e.get("view_count")} for e in json.loads(r.stdout).get("entries") or []]


def brave_search(cfg: dict, query: str, n: int, source: str) -> list[dict]:
    """Brave web search ($BRAVE_API_KEY or [paths] brave_key, as `clipper cast` uses):
    titles, sites and ages of the pages."""
    import requests

    from .cast import search_key

    key = search_key(cfg)
    if not key:
        raise RuntimeError("no $BRAVE_API_KEY or [paths] brave_key")
    r = requests.get("https://api.search.brave.com/res/v1/web/search", timeout=20,
                     params={"q": query, "count": n},
                     headers={"Accept": "application/json", "X-Subscription-Token": key})
    r.raise_for_status()
    return [{"source": source, "title": x.get("title") or "", "url": x.get("url") or "",
             "channel": (x.get("profile") or {}).get("name") or (x.get("meta_url") or {}).get("hostname") or "",
             "date": x.get("page_age", "")[:10] or x.get("age") or "",
             "summary": re.sub(r"<[^>]+>", "", x.get("description") or "")[:300]}
            for x in (r.json().get("web") or {}).get("results", [])]


def reddit_search(cfg: dict, query: str, n: int) -> list[dict]:
    """Reddit threads: through Brave (site:reddit.com) when there is a key, else Reddit's own
    search feed, which turns unsigned clients away after a few requests (403 / 429)."""
    import xml.etree.ElementTree as ET

    import requests

    from .cast import search_key

    if search_key(cfg):
        return brave_search(cfg, f"site:reddit.com {query}", n, "reddit")
    r = requests.get("https://www.reddit.com/search.rss", timeout=30, params={"q": query, "limit": n},
                     headers={"User-Agent": "clipper-research/1.0"})
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.content).findall("a:entry", ns)[:n]:
        cat, link = e.find("a:category", ns), e.find("a:link", ns)
        out.append({"source": "reddit", "title": e.findtext("a:title", "", ns),
                    "channel": cat.get("label", "") if cat is not None else "",
                    "url": link.get("href", "") if link is not None else "",
                    "date": (e.findtext("a:updated", "", ns) or "")[:10]})
    return out


def our_streams(cfg: dict, ts: list[str], n: int) -> list[dict]:
    out = []
    for sp in sorted(Path(cfg["paths"]["streams"]).glob("*/segments.json"), reverse=True):
        if sp.parent.name.startswith("story-"):  # our edit of other people's videos (#84), not a show
            continue
        for s in (jsonstate.read(sp, {}) or {}).get("segments", []):
            text = " ".join([s.get("title") or "", s.get("summary") or "", s.get("topic") or "",
                             *(s.get("figures") or [])])
            if hit(text, ts):
                out.append({"source": "our streams", "title": s.get("title") or "", "channel": sp.parent.name,
                            "url": f"{sp.parent.name} {s.get('id', '')}", "date": "",
                            "summary": (s.get("summary") or "")[:300]})
    return out[:n]


def own_titles(cfg: dict, subject: str, ts: list[str], after: str, n: int) -> list[dict]:
    return [{"source": "his own videos", "title": it["title"], "channel": it["channel"], "url": it["url"],
             "date": it["date"]}
            for it in items(cfg, subject, transcribed=False)  # a new upload's title counts before its words
            if it["date"] >= after and hit(it["title"], ts)][:n]


def findings(cfg: dict, subject: str, lead: dict) -> tuple[list[dict], dict[str, str]]:
    n = cfg["investigate"]["check_results"]
    q, ts = lead["search"] or lead["headline"], terms(lead["search"] or lead["headline"])
    sources = {"youtube": lambda: youtube_search(q, n), "reddit": lambda: reddit_search(cfg, q, n),
               "web": lambda: brave_search(cfg, q, n, "web"),
               "our streams": lambda: our_streams(cfg, ts, n),
               "his own videos": lambda: own_titles(cfg, subject, ts, lead["first"], n)}
    found, errors = [], {}
    for name, fn in sources.items():
        try:
            found += fn()
        except Exception as e:  # noqa: BLE001  # one source down doesn't stop the others
            errors[name] = str(e)[:200]
    return found, errors


def run_check(cfg: dict, subject: str, only: str | None = None) -> float:
    ic = cfg["investigate"]
    d = inv_dir(cfg, subject)
    data = load_leads(cfg, subject)
    todo = [x for x in data["leads"] if x["keep"] and (not only or x["id"] == only)]
    if not todo:
        raise ClipperError(f"no kept lead{' ' + only if only else ''} in leads.json")
    claude, prompts = llm_setup(cfg, ic["check_model"])
    prompts.vars.update(subject=subject)
    system = prompts.get("investigate_check")
    budget = Budget(ic["budget_usd"], claude)
    (d / "swarm").mkdir(parents=True, exist_ok=True)
    for lead in todo:
        found, errors = findings(cfg, subject, lead)
        print(f"{lead['id']} {lead['headline']}: {len(found)} results"
              + (f" ({', '.join(f'{k} failed' for k in errors)})" if errors else ""), flush=True)
        verdict = {"coverage": "fresh", "note": "nothing found", "matches": []}
        if found:
            user = (f"Lead: {lead['headline']}\n{lead['story']}\nPeople: {', '.join(lead['people'])}\n"
                    f"His receipts run {lead['first']} to {lead['last']}.\n\n<results>\n"
                    + "\n".join(f"[{k}] {r['source']} | {r['channel']} | {r['date'] or 'undated'} | {r['title']}"
                                + (f"\n    {r['summary']}" if r.get("summary") else "")
                                for k, r in enumerate(found)) + "\n</results>")
            est = estimate(cfg, ic["check_model"], (len(system) + len(user)) / 4, 400)
            try:
                budget.reserve(est)
            except OverBudget as e:
                print(f"stopping: {e}")
                break
            try:
                out, _ = claude.json_call(ic["check_model"], system, user, CHECK_SCHEMA, ic["check_effort"])
            finally:
                budget.release(est)
            verdict = out or {"coverage": "unchecked", "note": "the model declined to judge", "matches": []}
        matched = [found[k] for k in verdict["matches"] if 0 <= k < len(found)]
        if errors and not found:
            verdict = {"coverage": "unchecked", "note": "every search failed", "matches": []}
        lead["coverage"] = {"label": verdict["coverage"], "note": verdict["note"],
                            "links": [{"source": r["source"], "title": r["title"], "url": r["url"]} for r in matched],
                            "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}
        jsonstate.write(d / "swarm" / f"{lead['id']}.json",
                        {"lead": lead["id"], "headline": lead["headline"], "query": lead["search"],
                         "terms": terms(lead["search"] or lead["headline"]), "at": lead["coverage"]["checked_at"],
                         "errors": errors, "results": found, "verdict": verdict})
        print(f"  {verdict['coverage']}: {verdict['note']}", flush=True)
    save_leads(cfg, subject, data)
    log_run(d, {"step": "check", "lead": only or "", "usd": round(claude.cost, 4)})
    print(f"check ~${claude.cost:.3f}")
    maybe_post(cfg, subject)
    return claude.cost


def show_trail(cfg: dict, subject: str, only: str | None = None) -> None:
    for p in sorted((inv_dir(cfg, subject) / "swarm").glob("*.json")):
        t = jsonstate.load(p)
        if only and t["lead"] != only:
            continue
        print(f"{t['lead']} {t['headline']}\n  query {t['query']!r} at {t['at']}")
        for k, err in t["errors"].items():
            print(f"  {k}: failed ({err})")
        picked = set(t["verdict"]["matches"])
        for k, r in enumerate(t["results"]):
            print(f"  {'*' if k in picked else ' '} [{r['source']}] {r['channel']} {r['date']}  {r['title'][:90]}")
            print(f"      {r['url']}")
        print(f"  -> {t['verdict']['coverage']}: {t['verdict']['note']}\n")


# ---------- list / show / dossier / run ----------

def show_list(cfg: dict, subject: str) -> None:
    data = load_leads(cfg, subject)
    focus = f" about {data['about']!r}" if data.get("about") else ""
    print(f"{subject}: {len(data['leads'])} leads{focus} ({data['made_at'][:10]})")
    for x in data["leads"]:
        print(f"{x['id']} {'keep' if x['keep'] else 'skip'} {x['kind']:<13} {x['confidence']:<6} "
              f"{x['coverage']['label']:<14} {len(x['receipts']):>2} receipts, {x['first']} to {x['last']}")
        print(f"     {x['headline']}")


def show_lead(cfg: dict, subject: str, lead_id: str) -> None:
    data = load_leads(cfg, subject)
    lead = next((x for x in data["leads"] if x["id"].lower() == lead_id.lower()), None)
    if not lead:
        raise ClipperError(f"no lead {lead_id} (clipper investigate list {subject})")
    print(lead_md(lead))


def lead_md(x: dict) -> str:
    from .timefmt import clock

    out = [f"## {x['id']} {x['headline']}", "",
           f"{x['kind']}, confidence {x['confidence']}, coverage {x['coverage']['label']}"
           + ("" if x["keep"] else " (not kept)"), "", x["story"], "",
           f"Thread: {x['thread']}", f"People: {', '.join(x['people']) or '-'}",
           f"Would confirm or kill it: {x['confirm_or_kill']}", ""]
    if x.get("notes"):
        out += [f"Notes: {x['notes']}", ""]
    out.append("Timeline:")
    for r in x["receipts"]:
        tag = "" if r["mode"] == "stated" else f" ({r['mode']})"
        out += [f"- {r['date']}, {r['channel']}, \"{r['title']}\" at {clock(r['t'])}{tag}",
                f"  > {r['quote']}", f"  {r['link']}"]
    if c := x.get("confirm"):
        out += ["", f"Confirm: {c['verdict']}" + (f", {c['was']} -> {x['confidence']}" if c.get("was") else "")
                + f": {c['note']}"]
        out += [f"- {e['date']}, \"{e['title']}\": \"{e['quote']}\" {e['link']}" for e in c["evidence"]]
    cov = x["coverage"]
    if cov.get("note"):
        out += ["", f"Coverage ({cov['label']}): {cov['note']}"]
        out += [f"- [{r['source']}] {r['title']}: {r['url']}" for r in cov.get("links", [])]
    return "\n".join(out) + "\n"


def dossier(data: dict) -> str:
    head = [f"# Leads: {data['subject']}" + (f" (about {data['about']})" if data.get("about") else ""), "",
            (f"Made {data['made_at'][:16]} by {data['model']} from {data['candidates']} candidate threads. "
             "Research, not content: every quote links to the moment it was said; the story around "
             "the quotes is the lead's, not his."), ""]
    return "\n".join(head) + "\n".join(lead_md(x) for x in data["leads"])


# ---------- discord: a thread per investigation ----------

def thread_title(data: dict) -> str:
    """One investigation = one leads run: subject, focus, the day it was made."""
    return " · ".join(filter(None, [data["subject"], data.get("about"), data["made_at"][:10]]))[:100]


def header_message(data: dict) -> str:
    kept = sum(x["keep"] for x in data["leads"])
    span = (f", videos {data.get('since') or 'from the start'} to {data.get('before') or 'now'}"
            if data.get("since") or data.get("before") else "")
    return (f"**Investigation: {data['subject']}**" + (f" (about {data['about']})" if data.get("about") else "")
            + f"\n{kept} leads from {data['candidates']} candidate threads{span}, made {data['made_at'][:16]}. "
            "Research, not content: each quote links to the moment he said it.")[:2000]


def lead_message(x: dict, link: str = "") -> str:
    """A lead as a short Discord message: headline, labels, the story's first sentence and a
    link to the whole lead in the war room (the receipts are there and in leads.md)."""
    rs = x["receipts"]
    first = re.split(r"(?<=[.!?])\s+", x["story"].strip(), maxsplit=1)[0]
    lines = [f"**{x['id']} · {x['headline'][:200]}**",
             f"{x['kind']} · confidence {x['confidence']} · coverage {x['coverage']['label']} · "
             f"{len(rs)} receipts" + (f", {rs[0]['date']} to {rs[-1]['date']}" if rs else ""),
             first[:400] + ("…" if len(first) > 400 else "")]
    if x.get("notes"):
        lines.append(f"Notes: {x['notes'][:300]}")
    lines.append(f"<{link}>" if link else "The whole lead: leads.md")
    return "\n".join(lines)


def war_room_link(cfg: dict) -> str:
    """The war room's base URL for links in Discord: its tailnet address, else [serve] url, else ""."""
    from .serve import tailnet_link

    try:
        base = tailnet_link(cfg) if cfg["warroom"]["tailnet"] else ""
    except Exception:  # noqa: BLE001  # serve down: the link is a nicety
        base = ""
    return (base or str(cfg["serve"]["url"] or "")).rstrip("/")


def run_id(data: dict) -> str:
    """A leads run's id for links: its made_at as digits (lead ids like L01 repeat across runs)."""
    return re.sub(r"\D", "", data.get("made_at") or "")[:14]


def load_run(cfg: dict, subject: str, run: str = "") -> dict | None:
    """leads.json, or the earlier run with that id among its backups (leads.<time>.json); None
    when that run isn't on file any more."""
    d = inv_dir(cfg, subject)
    data = jsonstate.read(d / "leads.json", {})
    if not run or run_id(data) == run:
        return data or None
    for p in sorted(d.glob("leads.*.json"), reverse=True):
        old = jsonstate.read(p, {})
        if run_id(old) == run:
            return old
    return None


def post_discord(cfg: dict, subject: str, new_thread: bool = False) -> str:
    """Post the kept leads to [investigate] discord_channel: a thread per leads run, a message
    per lead, edited in place when check or confirm changes a lead, and marked withdrawn when a
    lead stops being kept. Returns the thread's link."""
    from .notify import Missing, Rest, named_channel, open_thread

    ic, dc = cfg["investigate"], cfg["discord"]
    data = load_leads(cfg, subject)
    leads = [x for x in data["leads"] if x["keep"]]
    dpath = inv_dir(cfg, subject) / "discord.json"
    state = jsonstate.read(dpath, {})
    this_run = state.get("made_at") == data["made_at"] and not new_thread
    if not leads and not (this_run and state.get("messages")):
        raise ClipperError(f"no kept leads for {subject!r} to post")
    api = Rest(cfg)
    channel_id = named_channel(api, dc, ic["discord_channel"],
                               "Investigations: a thread per run of `clipper investigate leads`", kinds=(0, 15))
    thread_id = state.get("thread_id") if this_run and state.get("channel_id") == channel_id else None
    if thread_id:
        try:
            api("GET", f"/channels/{thread_id}")
        except Missing:
            thread_id = None  # deleted: a new one (any other failure stops here, ids kept)
    if not thread_id:
        thread_id = open_thread(api, channel_id, thread_title(data), {"content": header_message(data)})
        state = {"made_at": data["made_at"], "channel_id": channel_id, "thread_id": thread_id, "messages": {}}
        jsonstate.write(dpath, state)
    base = war_room_link(cfg)
    by_id = {x["id"]: x for x in data["leads"]}
    texts = {x["id"]: lead_message(x, f"{base}/#inv={subject}&run={run_id(data)}&lead={x['id']}" if base else "")
             for x in leads}
    for lid in state["messages"]:  # posted, and no longer kept: say so rather than leave it standing
        if lid not in texts:
            x = by_id.get(lid)
            texts[lid] = f"~~{lid} · {x['headline'][:200]}~~" if x else f"~~{lid}~~"
            texts[lid] += "\nWithdrawn: no longer kept in leads.json."
    posted = edited = 0
    for lid, text in texts.items():
        sha = hashlib.sha1(text.encode()).hexdigest()[:12]
        old = state["messages"].get(lid)
        if old and old["sha"] == sha:
            continue
        if old:
            try:
                api("PATCH", f"/channels/{thread_id}/messages/{old['id']}", {"content": text})
                edited += 1
            except Missing:
                old = None  # deleted by hand: post it again
        if not old:
            msg = api("POST", f"/channels/{thread_id}/messages", {"content": text})
            old = {"id": msg["id"]}
            posted += 1
        state["messages"][lid] = {"id": old["id"], "sha": sha}
        jsonstate.write(dpath, state)
    url = f"https://discord.com/channels/{dc['guild_id']}/{thread_id}"
    print(f"discord: {posted} posted, {edited} edited, {len(texts) - posted - edited} unchanged: {url}")
    return url


def maybe_post(cfg: dict, subject: str) -> None:
    """After leads, check or confirm with [investigate] discord on: Discord follows leads.json.
    A failure is printed, not raised: leads.json is the record and is already written."""
    if not cfg["investigate"]["discord"]:
        return
    try:
        post_discord(cfg, subject)
    except Exception as e:  # noqa: BLE001  # the research is saved; Discord can be retried with `post`
        print(f"discord: not posted ({e}); clipper investigate post {subject}")


def run_all(cfg: dict, subject: str, since: str | None = None, before: str | None = None,
            about: str | None = None, max_leads: int | None = None) -> None:
    budget = cfg["investigate"]["budget_usd"]

    def left() -> dict:  # each step gets what the steps before it left of the budget
        return with_budget(cfg, budget - spent)

    spent, t0 = 0.0, time.time()
    spent += run_facts(left(), subject, since, before, new=not since)
    spent += run_leads(left(), subject, about, since, before, max_leads)
    p = inv_dir(cfg, subject) / "leads.json"
    if p.exists() and p.stat().st_mtime >= t0 and any(x["keep"] for x in load_leads(cfg, subject)["leads"]):
        spent += run_check(left(), subject)
    print(f"\ninvestigate run {subject}: ~${spent:.2f} in all; {inv_dir(cfg, subject) / 'leads.md'}")
