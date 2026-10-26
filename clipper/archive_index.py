"""`clipper archive index` / `search`: keyword search over a subject's transcripts (#48, #53).

Usage:
    clipper archive index <subject> [--rebuild]
    clipper archive search <subject> <query> [--limit N] [--since YYYY[-MM-DD]] [--before ...]
                                             [--by rank|date|oldest] [--years]

The index is <subject>/index.sqlite: an FTS5 table of ~[archive] index_window_s windows of
transcript text (porter stemming, so "lie" finds "lying"), each with its item and start time,
plus an items table of title, date, channel and url. `index` adds new and re-transcribed items
only; --rebuild starts over. No model calls: it reads transcript.json and costs nothing.

A query is FTS5 syntax: `costco dog` (both words in one window), `"costco dog"` (the phrase),
`costco OR walmart`, `huck NOT dog`, `coff*`. Each hit prints the date, channel, title, a link
at the moment (YouTube ?t=, a few seconds early for context) and the matching words in
[brackets]. --years counts matching windows per year instead: when did he talk about it.
Meaning search (embeddings, "the warehouse store" for "Costco") is #53 and can add a table here.

    clipper archive ask <subject> <question> [--since ...] [--before ...] [--limit N]

`ask` takes a plain question: [archive] ask_model (Haiku) writes up to ask_max_queries FTS5
searches (synonyms, names, Whisper's misspellings), the index pools their best windows, and
the model reads the pool and returns the ones that fit, strong or maybe, each with a quote
and the link. About a cent a question.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from .archive import load_catalog, subject_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, title TEXT, date TEXT, channel TEXT, url TEXT, source TEXT,
    duration_s REAL, indexed_mtime REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS windows USING fts5(
    text, item UNINDEXED, start UNINDEXED, tokenize = 'porter unicode61');
"""


def connect(d: Path) -> sqlite3.Connection:
    db = sqlite3.connect(d / "index.sqlite")
    db.executescript(SCHEMA)
    return db


def windows(segments: list[dict], size_s: float) -> list[tuple[float, str]]:
    """Consecutive segments joined into windows of about size_s: (start, text)."""
    out, start, parts = [], None, []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        if start is None:
            start = s["start"]
        parts.append(text)
        if s["end"] - start >= size_s:
            out.append((start, " ".join(parts)))
            start, parts = None, []
    if parts:
        out.append((start, " ".join(parts)))
    return out


def build(cfg: dict, subject: str, rebuild: bool) -> None:
    d = subject_dir(cfg, subject)
    cat = load_catalog(d)
    if not cat:
        raise SystemExit(f"no catalog for {subject!r}: clipper archive add {subject} <url>")
    if rebuild:
        (d / "index.sqlite").unlink(missing_ok=True)
    db = connect(d)
    have = dict(db.execute("SELECT id, indexed_mtime FROM items"))
    size_s = cfg["archive"]["index_window_s"]
    t0, added, n_win = time.time(), 0, 0
    todo = []
    for i in cat:
        tp = d / i / "transcript.json"
        if tp.exists() and have.get(i) != tp.stat().st_mtime:
            todo.append((i, tp))
    print(f"{subject}: {len(todo)} items to index ({len(have)} already in index.sqlite)")
    for k, (i, tp) in enumerate(todo, 1):
        segs = json.loads(tp.read_text(encoding="utf-8")).get("segments") or []
        mp = d / i / "meta.json"
        meta = {**cat[i], **(json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {})}
        wins = windows(segs, size_s)
        db.execute("DELETE FROM windows WHERE item = ?", (i,))
        db.executemany("INSERT INTO windows (text, item, start) VALUES (?, ?, ?)",
                       [(text, i, round(start, 1)) for start, text in wins])
        db.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (i, meta.get("title") or "", meta.get("date") or "", meta.get("channel") or "",
                    meta.get("url") or "", meta.get("source") or "", meta.get("duration_s") or 0,
                    tp.stat().st_mtime))
        added += 1
        n_win += len(wins)
        if k % 500 == 0:
            db.commit()
            print(f"[{k}/{len(todo)}] {n_win} windows, {time.time() - t0:.0f} s", flush=True)
    db.commit()
    if added:
        db.execute("INSERT INTO windows(windows) VALUES ('optimize')")
        db.commit()
    total = db.execute("SELECT count(*) FROM items").fetchone()[0]
    db.close()
    mb = (d / "index.sqlite").stat().st_size / 1e6
    print(f"{subject}: {added} items indexed ({n_win} windows) in {time.time() - t0:.0f} s; "
          f"{total} items, {mb:.0f} MB")


def link(url: str, source: str, start: float) -> str:
    t = max(0, int(start) - 3)  # a few seconds early, so the clip starts with its context
    if source == "youtube":
        return f"{url}&t={t}s"
    return f"{url} at {t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def open_index(cfg: dict, subject: str) -> sqlite3.Connection:
    d = subject_dir(cfg, subject)
    if not (d / "index.sqlite").exists():
        raise SystemExit(f"no index for {subject!r} yet: clipper archive index {subject}")
    return connect(d)


def match_clause(query: str, since: str | None, before: str | None) -> tuple[str, list]:
    where, params = ["windows MATCH ?"], [normalize(query)]
    if since:
        where.append("items.date >= ?")
        params.append(since)
    if before:
        where.append("items.date < ?")
        params.append(before)
    return f"FROM windows JOIN items ON items.id = windows.item WHERE {' AND '.join(where)}", params


TOKEN = re.compile(r'"[^"]*"|[()]|[^\s()"]+')
OPS = {"AND", "OR", "NOT"}


def normalize(query: str) -> str:
    """Make a loosely written query valid FTS5: punctuation out of terms and phrases (FTS5
    rejects apostrophes and hyphens), and AND between groups, which FTS5 won't imply next to
    parentheses: `(huck OR huk) food` -> `(huck OR huk) AND food`."""
    out: list[str] = []
    for tok in TOKEN.findall(query):
        if tok.startswith('"'):
            words = re.findall(r"\w+", tok)
            if not words:
                continue
            tok = '"' + " ".join(words) + '"'
        elif tok not in OPS and tok not in ("(", ")"):
            words = re.findall(r"\w+", tok)
            if not words:
                continue
            # co-op / huck's: the index split them at the punctuation, so search the phrase
            tok = f'"{" ".join(words)}"' if len(words) > 1 else words[0] + ("*" if tok.endswith("*") else "")
        prev = out[-1] if out else None
        if tok == "NOT" and prev == "AND":  # FTS5's NOT is already binary: a AND NOT b is an error
            out.pop()
            prev = out[-1] if out else None
        if prev and prev not in OPS and prev != "(" and tok not in OPS and tok != ")" \
                and ("(" in (prev, tok) or ")" in (prev, tok)):
            out.append("AND")
        out.append(tok)
    return " ".join(out)


def hits(db: sqlite3.Connection, query: str, since: str | None, before: str | None,
         limit: int) -> list[dict]:
    """Best-ranked windows for one FTS5 query, with their full text."""
    base, params = match_clause(query, since, before)
    rows = db.execute(
        "SELECT windows.item, windows.start, windows.text, items.date, items.channel, items.title, "
        f"items.url, items.source {base} ORDER BY rank LIMIT ?", [*params, limit]).fetchall()
    keys = ("item", "start", "text", "date", "channel", "title", "url", "source")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def search(cfg: dict, subject: str, query: str, limit: int, since: str | None, before: str | None,
           by: str, years: bool) -> None:
    db = open_index(cfg, subject)
    base, params = match_clause(query, since, before)
    try:
        if years:
            rows = db.execute(f"SELECT substr(items.date, 1, 4), windows.item {base}", params).fetchall()
            per = Counter(y or "????" for y, _ in rows)
            vids = len({i for _, i in rows})
            print(f"{query!r}: {len(rows)} windows in {vids} videos")
            top = max(per.values(), default=1)
            for y in sorted(per):
                print(f"  {y}  {per[y]:>6}  {'#' * max(1, round(40 * per[y] / top))}")
            return
        order = {"rank": "rank", "date": "items.date DESC, windows.start",
                 "oldest": "items.date, windows.start"}[by]
        total, vids = db.execute(f"SELECT count(*), count(DISTINCT windows.item) {base}",
                                 params).fetchone()
        rows = db.execute(
            "SELECT items.date, items.channel, items.title, items.url, items.source, windows.start, "
            f"snippet(windows, 0, '[', ']', ' ... ', 40) {base} ORDER BY {order} LIMIT ?",
            [*params, limit]).fetchall()
    except sqlite3.OperationalError as e:  # bad FTS5 syntax, e.g. an unclosed quote
        raise SystemExit(f"query {query!r}: {e}") from e
    print(f"{query!r}: {total} windows in {vids} videos; showing {len(rows)} by {by}\n")
    for date, channel, title, url, source, start, snip in rows:
        print(f"{date or '????-??-??'}  {channel}  {title[:70]!r}")
        print(f"  {link(url, source, start)}")
        print(f"  {' '.join(snip.split())}\n")


# ---------- ask: a cheap model writes the queries and picks the hits ----------

EXPAND_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["queries"],
    "properties": {"queries": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["fts", "why"],
        "properties": {"fts": {"type": "string"}, "why": {"type": "string"}}}}},
}
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["picks"],
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "fit", "quote", "why"],
        "properties": {"n": {"type": "integer"}, "fit": {"type": "string", "enum": ["strong", "maybe"]},
                       "quote": {"type": "string"}, "why": {"type": "string"}}}}},
}


def quiet_http() -> None:
    for name in ("httpx", "httpx2"):  # the SDK's HTTP client logs a line per call at INFO
        logging.getLogger(name).setLevel(logging.WARNING)


def answer(cfg: dict, subject: str, question: str, since: str | None, before: str | None,
           claude) -> list[dict]:
    """Question in plain words -> FTS5 queries (model) -> pooled hits -> the ones that fit (model).
    The fitting hits, strong first then oldest first, each with fit, quote and why added.
    claude keeps the running cost (`clipper receipts` asks one question per story)."""
    from .scoring import Prompts

    ac = cfg["archive"]
    db = open_index(cfg, subject)
    cat = load_catalog(subject_dir(cfg, subject))
    prompts = Prompts(cfg)
    prompts.version = ac["ask_prompt_version"]
    channels = ", ".join(sorted({i.get("channel") or "?" for i in cat.values()}))
    prompts.vars.update(subject=subject, channels=channels, max_queries=ac["ask_max_queries"])

    out, _ = claude.json_call(ac["ask_model"], prompts.get("archive_expand"), question,
                              EXPAND_SCHEMA, ac["ask_effort"])
    pool: dict[tuple[str, float], dict] = {}
    for q in (out or {}).get("queries", [])[:ac["ask_max_queries"]]:
        try:
            found = hits(db, q["fts"], since, before, ac["ask_per_query"])
        except sqlite3.OperationalError as e:  # the model wrote bad FTS5 syntax: skip that one
            print(f"  skip {q['fts']!r}: {e}")
            continue
        print(f"  {len(found):>3} for {normalize(q['fts'])}  ({q['why']})")
        for h in found:
            pool.setdefault((h["item"], h["start"]), h)
    db.close()
    cands = list(pool.values())[:ac["ask_candidates"]]
    if not cands:
        return []
    listing = "\n\n".join(f"[{n}] {h['date'] or 'undated'} | {h['channel']} | {h['title']}\n{h['text']}"
                          for n, h in enumerate(cands))
    out, _ = claude.json_call(ac["ask_model"], prompts.get("archive_judge"),
                              f"Question: {question}\n\n<windows>\n{listing}\n</windows>",
                              JUDGE_SCHEMA, ac["ask_effort"])
    picks = [p for p in (out or {}).get("picks", []) if 0 <= p["n"] < len(cands)]
    picks.sort(key=lambda p: (p["fit"] != "strong", cands[p["n"]]["date"] or ""))
    print(f"  {len(picks)} of {len(cands)} windows fit")
    return [{**cands[p["n"]], "fit": p["fit"], "quote": p["quote"], "why": p["why"]} for p in picks]


def ask(cfg: dict, subject: str, question: str, since: str | None, before: str | None,
        limit: int) -> None:
    from .scoring import Claude, preflight

    claude = Claude(cfg["scoring"]["workspace_id"])
    quiet_http()
    preflight(claude, cfg["archive"]["ask_model"])
    picks = answer(cfg, subject, question, since, before, claude)
    print(f"\n{question!r}: {len(picks)} fit (~${claude.cost:.3f}, {cfg['archive']['ask_model']})\n")
    for k, h in enumerate(picks[:limit], 1):
        print(f"{k:>2}. [{h['fit']}] {h['date'] or '????-??-??'}  {h['channel']}  {h['title'][:70]!r}")
        print(f"    {link(h['url'], h['source'], h['start'])}")
        print(f"    \"{h['quote']}\"")
        print(f"    {h['why']}\n")
