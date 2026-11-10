"""`clipper story dig <slug>`: dig a story deeper, round by round, after `story run`.

A story's first pass finds what the brief's own words find. Dig follows what that material
mentions and doesn't explain (a name, an earlier fight, a claim, a vague "you know what
happened"), the way a researcher snowballs: each round reads what the last one found, picks
threads, searches them, and reads what came back. Every thread stays tied to the moment that
mentions it, so the dig can't wander off into unrelated news (`[story.dig] max_depth`).

One round, each step saved to dig.json so a rerun picks up where it stopped:
1. threads ([story.dig] model, Sonnet): the moments and dossier gaps not read before (M.., D..,
   G..) -> new threads: person | event | claim | allusion | other, each linking the ids that
   mention it, with transcript queries and a web search. A thread linking nothing is
   rejected; one more than max_depth steps from the story's own moments is "too deep". Both
   are kept, with why.
2. search: a small swarm per thread ([story.dig] swarm over [swarm]), YouTube, Kick, Odysee,
   the web and Reddit, back to lookback_days before the story's window. Results the story's
   own swarm already had are skipped; the rest are candidates.
3. judge ([story] find_model, Haiku): every candidate gets a label, core | side | hint |
   noise, the thread it connects to and why; code scores it. The best videos, within
   max_videos / max_hours a round, join the story's catalog.
4. ingest: Modal, as `story run` (--dry-run stops before it, with the estimate). A video that
   fails is taken out of the catalog and marked failed here.
5. find (Haiku): the threads' queries over the story's transcripts (new and old) and the
   archive subjects the searches touched; the model picks moments (D01 ..) with a label and
   a thread, and a quote that isn't in the transcript word for word is dropped (kept under
   "unverified").

The rounds stop at --rounds, when nothing new is left to follow, or at [story.dig] budget_usd
(the swarms' calls count). Then the dossier is written again with the dig in it (a "Side
stories and hints" section; D moments and the dig's sources are citable); the dossier from
before the first dig is kept as dossier.before_dig.md / .json.

Nothing is thrown away: dig.json keeps every thread (rejected and too deep ones too), every
candidate with its label, score and why (noise too), every moment and every window read.
Flag things there between runs: a thread's or candidate's "flag": "follow" (search the thread
next round / transcribe the video whatever its label) or "drop" (never). `--thread "words"`
adds a thread by hand. graph.json is the same as nodes and edges: the brief, threads,
moments and sources, and what links them.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import jsonstate, story, swarm
from .archive import load_catalog, save_catalog
from .errors import ClipperError
from .llm import Budget, estimate

LABELS = ["core", "side", "hint", "noise"]
WEIGHT = {"core": 3.0, "side": 2.0, "hint": 1.0, "noise": 0.0}
KINDS = ["person", "event", "claim", "allusion", "other"]
STEPS = ["threads", "search", "judge", "ingest", "find"]

THREADS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["threads"],
    "properties": {"threads": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "kind", "what", "links", "queries", "search"],
        "properties": {"name": {"type": "string"}, "kind": {"type": "string", "enum": KINDS},
                       "what": {"type": "string"}, "links": {"type": "array", "items": {"type": "string"}},
                       "queries": {"type": "array", "items": {"type": "string"}},
                       "search": {"type": "string"}}}}},
}
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["labels"],
    "properties": {"labels": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "label", "thread", "why"],
        "properties": {"n": {"type": "integer"}, "label": {"type": "string", "enum": LABELS},
                       "thread": {"type": "string"}, "why": {"type": "string"}}}}},
}
MOMENTS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["picks"],
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["n", "thread", "label", "role", "fit", "quote", "why"],
        "properties": {"n": {"type": "integer"}, "thread": {"type": "string"},
                       "label": {"type": "string", "enum": LABELS[:3]},
                       "role": {"type": "string", "enum": story.ROLES},
                       "fit": {"type": "string", "enum": ["strong", "maybe"]},
                       "quote": {"type": "string"}, "why": {"type": "string"}}}}},
}
JUDGE_BATCH = 60


# ---------- dig.json ----------

def settings(cfg: dict) -> dict:
    """[story] dig, each key over its default (a [story.dig] table may set only some)."""
    from .config import DEFAULTS

    base = DEFAULTS["story"]["dig"]
    own = cfg["story"].get("dig") or {}
    if unknown := sorted(set(own) - set(base)):
        raise ClipperError(f"unknown [story.dig] setting(s) {', '.join(unknown)} (see clipper.example.toml)")
    return {**base, **own, "swarm": {**base["swarm"], **(own.get("swarm") or {})}}


def load(d: Path) -> dict:
    dig = jsonstate.read(d / "dig.json", None) or {}
    for k in ("threads", "candidates", "moments", "rounds", "unverified", "read", "subjects"):
        dig.setdefault(k, [])
    return dig


def save(d: Path, dig: dict, b: dict) -> None:
    dig["at"] = datetime.now(UTC).isoformat(timespec="seconds")
    jsonstate.write(d / "dig.json", dig)
    moments = (jsonstate.read(d / "moments.json", {}) or {}).get("moments", [])
    jsonstate.write(d / "graph.json", graph(b, moments, dig))


def material(d: Path) -> dict | None:
    """What the dossier takes from a dig: its moments, the sources it judged worth keeping and
    the threads; None before any dig found something."""
    dig = jsonstate.read(d / "dig.json", None)
    if not dig or not (dig.get("moments") or dig.get("candidates")):
        return None
    keep = [c for c in dig["candidates"] if c.get("label") in ("core", "side", "hint") and c.get("flag") != "drop"
            or c.get("flag") == "follow"]
    keep.sort(key=lambda c: -c.get("score", 0))
    return {"moments": dig["moments"], "sources": keep,
            "threads": [t for t in dig["threads"] if t["status"] not in ("rejected", "too deep")]}


def graph(b: dict, moments: list[dict], dig: dict) -> dict:
    """The story as nodes and edges: brief, threads, moments (M first pass, D dig) and the
    dig's sources that aren't noise, each edge saying how two connect."""
    nodes = [{"id": "brief", "type": "brief", "label": b["brief"]}]
    edges = []
    for m in moments:
        nodes.append({"id": m["id"], "type": "moment", "label": m["quote"][:120], "role": m["role"],
                      "date": m.get("date") or "", "channel": m.get("channel") or "", "link": m.get("link") or ""})
        edges.append({"from": m["id"], "to": "brief", "kind": m["role"]})
    for t in dig["threads"]:
        if t["status"] == "rejected":
            continue
        nodes.append({"id": t["id"], "type": "thread", "kind": t["kind"], "label": t["name"], "what": t["what"],
                      "depth": t["depth"], "status": t["status"], "flag": t.get("flag", "")})
        edges += [{"from": t["id"], "to": x, "kind": "mentioned in"} for x in t["links"]]
    for m in dig["moments"]:
        nodes.append({"id": m["id"], "type": "moment", "label": m["quote"][:120], "role": m["role"],
                      "dig": m["label"], "date": m.get("date") or "", "channel": m.get("channel") or "",
                      "link": m.get("link") or ""})
        edges.append({"from": m["id"], "to": m["thread"], "kind": m["label"]})
    for c in dig["candidates"]:
        if c.get("label") in ("core", "side", "hint") or c.get("flag") == "follow":
            nodes.append({"id": c["key"], "type": "source", "source": c.get("source") or "", "label": c.get("title") or "",
                          "url": c.get("url") or "", "date": c.get("date") or "", "dig": c.get("label") or "",
                          "score": c.get("score", 0), "flag": c.get("flag", "")})
            edges.append({"from": c["key"], "to": c.get("link_thread") or c["thread"], "kind": c.get("label") or ""})
    ids = {n["id"] for n in nodes}
    return {"nodes": nodes, "edges": [e for e in edges if e["from"] in ids and e["to"] in ids]}


def add_thread(dig: dict, text: str) -> dict:
    t = {"id": f"T{len(dig['threads']) + 1:02d}", "name": text, "kind": "other", "what": "added by hand",
         "links": [], "queries": [text], "search": text, "round": 0, "depth": 1, "status": "open",
         "flag": "follow", "by": "hand"}
    dig["threads"].append(t)
    return t


# ---------- pure parts ----------

def depth_of(links: list[str], dig: dict) -> int:
    """Steps from the story's own material: 1 for a thread an M moment or a gap mentions, one
    more than its parent thread for one only D moments mention."""
    by_thread = {t["id"]: t["depth"] for t in dig["threads"]}
    of_moment = {m["id"]: by_thread.get(m["thread"], 1) for m in dig["moments"]}
    ds = [0 if x[0] in "MG" else of_moment.get(x, 1) for x in links]
    return 1 + min(ds, default=0)


def score(c: dict, since: str, before: str) -> float:
    """A candidate's rank: its label first, then how strongly and how often the search found
    it, then whether it is dated inside the story's window. A flag wins over all of it."""
    if c.get("flag") == "drop":
        return 0.0
    s = WEIGHT.get(c.get("label") or "noise", 0.0)
    if s:
        s += 0.5 * (c.get("relevance") == "strong") + 0.25 * min(max(len(c.get("found_by") or []) - 1, 0), 2)
        day = (c.get("date") or "")[:10]
        if day and day >= since and (not before or day < before):
            s += 0.5
    return s + (10.0 if c.get("flag") == "follow" else 0.0)


def merge_thread(dig: dict, new: dict, known: set[str], max_depth: int, rnd: int) -> dict | None:
    """A thread the model found, as dig.json keeps it; a name already followed adds its links
    to that thread instead (None)."""
    from .investigate import canon

    links = [x for x in dict.fromkeys(new["links"]) if x in known]
    for t in dig["threads"]:
        if canon(t["name"]) == canon(new["name"]):
            t["links"] = list(dict.fromkeys([*t["links"], *links]))
            return None
    t = {"id": f"T{len(dig['threads']) + 1:02d}", "name": new["name"], "kind": new["kind"], "what": new["what"],
         "links": links, "queries": new["queries"], "search": new["search"], "round": rnd,
         "depth": depth_of(links, dig), "status": "open", "flag": ""}
    if not links:
        t.update(status="rejected", note=f"links nothing given ({', '.join(new['links']) or 'none'})")
    elif t["depth"] > max_depth:
        t.update(status="too deep", note=f"depth {t['depth']} > max_depth {max_depth}")
    dig["threads"].append(t)
    return t


def next_threads(dig: dict, n: int) -> list[dict]:
    """The open threads to search this round: flagged ones first, then the closest to the story."""
    todo = [t for t in dig["threads"] if t["status"] == "open" and t.get("flag") != "drop"
            or t.get("flag") == "follow" and t["status"] in ("too deep", "rejected")]
    todo.sort(key=lambda t: (t.get("flag") != "follow", t["depth"], t["id"]))
    return todo[:n]


def near(a: dict, b: dict, gap_s: float) -> bool:
    return (a["subject"], a["item"]) == (b["subject"], b["item"]) and abs(a["t"] - b["t"]) < gap_s


# ---------- the steps ----------

class Dig:
    def __init__(self, cfg: dict, b: dict, d: Path, claude, budget: Budget, prompts):
        self.cfg, self.b, self.d, self.claude, self.budget, self.prompts = cfg, b, d, claude, budget, prompts
        self.sc, self.dc = cfg["story"], settings(cfg)
        self.dig = load(d)
        self.since = (date.fromisoformat(b["since"]) - timedelta(days=self.dc["lookback_days"])).isoformat()

    def ask(self, model: str, effort: str, system: str, user: str, schema: dict, out_tokens: int) -> dict | None:
        return story.ask(self.cfg, self.claude, self.budget, model, effort, system, user, schema, out_tokens,
                         section="story.dig")

    def save(self) -> None:
        save(self.d, self.dig, self.b)

    def thread(self, tid: str) -> dict | None:
        return next((t for t in self.dig["threads"] if t["id"] == tid), None)

    def who(self) -> str:
        return ", ".join(p["name"] for p in self.b["people"]) or "not on file"

    def threads_list(self, ts: list[dict]) -> str:
        return "\n".join(f"{t['id']} | {t['kind']} | {t['name']}: {t['what']}" for t in ts)

    # 1
    def threads(self, r: dict) -> None:
        dig, read = self.dig, set(self.dig["read"])
        moments = (jsonstate.read(self.d / "moments.json", {}) or {}).get("moments", [])
        gaps = (jsonstate.read(self.d / "dossier.json", {}) or {}).get("dossier", {}).get("gaps", [])
        items = [(m["id"], (f"{m['id']} | {m['role']} | {m.get('date') or 'undated'} | {m.get('channel') or '?'} | "
                            f"{m['title']}\n    \"{m['quote']}\"\n    {m['why']}")) for m in moments + dig["moments"]]
        items += [(f"G{k:02d}", f"G{k:02d} | gap | {g}") for k, g in enumerate(gaps, 1)]
        new = [(i, text) for i, text in items if i not in read]
        if new:
            followed = "\n".join(f"- {t['name']} ({t['status']})" for t in dig["threads"]) or "(none yet)"
            user = (f"Story: {self.b['brief']}\nPeople: {self.who()}\n\nThreads already followed:\n{followed}\n\n"
                    f"<material>\n" + "\n".join(text for _, text in new) + "\n</material>")
            out = self.ask(self.dc["model"], self.dc["effort"],
                           self.prompts.get("story_dig_threads", max_threads=self.dc["max_threads"],
                                            queries_per_thread=self.dc["queries_per_thread"]),
                           user, THREADS_SCHEMA, 2000)
            known = read | {i for i, _ in items}
            made = [t for x in (out or {}).get("threads", []) if (t := merge_thread(
                dig, {**x, "queries": x["queries"][:self.dc["queries_per_thread"]]}, known, self.dc["max_depth"], r["n"]))]
            dig["read"] = sorted(known)
            ok = [t for t in made if t["status"] == "open"]
            print(f"threads: {len(new)} new ids read, {len(ok)} new threads"
                  + (f" ({len(made) - len(ok)} rejected or too deep)" if len(made) > len(ok) else ""))
            for t in made:
                print(f"  {t['id']} [{t['kind']}, {t['status']}] {t['name']}: {t['what']} ({', '.join(t['links'])})")
        r["threads"] = [t["id"] for t in next_threads(dig, self.dc["max_threads"])]
        if not r["threads"]:
            r["stopped"] = "nothing new to follow"

    # 2
    def search(self, r: dict) -> None:
        known = {f["key"] for f in (jsonstate.read(self.d / "swarm.json", {}) or {}).get("findings", [])}
        have = {c["key"]: c for c in self.dig["candidates"]}
        s = swarm.settings(self.cfg, {**self.sc["swarm"], **self.dc["swarm"]})
        days = max(1, (datetime.now(UTC).date() - date.fromisoformat(self.since)).days)
        for tid in r["threads"]:
            t = self.thread(tid)
            if t["status"] == "searched":
                continue
            if self.budget.spent + s["budget_usd"] > self.budget.limit:
                r["stopped"] = f"budget ${self.budget.limit:.2f}: ${self.budget.spent:.2f} spent"
                print(f"search: {r['stopped']}; {tid} and the rest wait for the next dig")
                break
            print(f"search {tid}: {t['name']}", flush=True)
            goal = f"{t['name']}: {t['what']} (a thread of the story: {self.b['brief']})"
            seeds = [{"kind": k, "target": t["search"], "why": f"thread {tid}"}
                     for k in ("web_search", "reddit_search", "youtube_search")]
            out = swarm.run(self.cfg, goal, self.d / "dig" / f"{tid}.swarm.json", context=story.context(self.b),
                            seeds=seeds, overrides={**self.sc["swarm"], **self.dc["swarm"]}, claude=self.claude,
                            days=days, since=self.since, before=self.b["before"] or None)
            n = 0
            for f in out["findings"]:
                src = f.get("source") or ""
                if src.startswith("archive "):
                    if (sub := src.removeprefix("archive ").strip()) not in self.dig["subjects"]:
                        self.dig["subjects"].append(sub)
                    continue
                if f["key"] in known:
                    continue
                if f["key"] in have:
                    have[f["key"]].setdefault("also", [])
                    if tid != have[f["key"]]["thread"] and tid not in have[f["key"]]["also"]:
                        have[f["key"]]["also"].append(tid)
                    continue
                c = {**f, "thread": tid, "round": r["n"], "label": "", "label_why": "", "score": 0.0, "flag": ""}
                self.dig["candidates"].append(c)
                have[f["key"]] = c
                n += 1
            t["status"] = "searched"
            print(f"  {n} new candidates")
            self.save()

    # 3
    def judge(self, r: dict) -> None:
        todo = [c for c in self.dig["candidates"] if c["round"] == r["n"] and not c["label"]]
        ts = [self.thread(x) for x in r["threads"]]
        for k in range(0, len(todo), JUDGE_BATCH):
            batch = todo[k:k + JUDGE_BATCH]
            lst = "\n".join(
                f"[{n}] {c.get('source') or '?'} | {c.get('channel') or '?'} | {c.get('date') or 'undated'} | "
                f"{c.get('title') or ''}" + (f"\n    {c['summary'][:900]}" if c.get("summary") else "")
                + f"\n    found for {c['thread']}: {c.get('why') or ''}" for n, c in enumerate(batch))
            user = (f"Story: {self.b['brief']}\nPeople: {self.who()}\n\n<threads>\n{self.threads_list(ts)}\n</threads>"
                    f"\n\n<results>\n{lst}\n</results>")
            out = self.ask(self.sc["find_model"], self.sc["find_effort"], self.prompts.get("story_dig_judge"), user,
                           JUDGE_SCHEMA, 60 * len(batch))
            for x in (out or {}).get("labels", []):
                if 0 <= x["n"] < len(batch):
                    c = batch[x["n"]]
                    c.update(label=x["label"], label_why=x["why"],
                             link_thread=x["thread"] if self.thread(x["thread"]) else c["thread"])
        for c in todo:
            if not c["label"]:
                c.update(label="noise", label_why="the judge passed over it")
        for c in self.dig["candidates"]:
            c["score"] = score(c, self.b["since"], self.b["before"])
        got = {lab: sum(c["label"] == lab for c in todo) for lab in LABELS}
        print("judge: " + ", ".join(f"{v} {k}" for k, v in got.items()))
        # the videos worth reading: this round's, and any flagged to follow that isn't in yet
        pool = [c for c in self.dig["candidates"] if c.get("flag") != "drop" and not c.get("item")
                and (c["round"] == r["n"] and c["label"] in ("core", "side", "hint") or c.get("flag") == "follow")]
        pool.sort(key=lambda c: -c["score"])
        cat = load_catalog(self.d)
        ours, cands = story.candidates(self.cfg, pool, cat)
        keep, left = story.fit(cands, self.dc["max_videos"], self.dc["max_hours"])
        by_key = {c["key"]: c for c in pool}
        for it in ours + keep:
            if c := by_key.get(it.get("key")):
                c["item"] = it["id"]
        for it in left:
            if c := by_key.get(it.get("key")):
                c["over_limits"] = r["n"]
        save_catalog(self.d, {**cat, **{it["id"]: it for it in ours + keep}})
        r["picked"] = list(dict.fromkeys([*r.get("picked", []), *(it["id"] for it in ours + keep)]))  # a re-judge adds
        print(f"judge: {len(keep)} videos to transcribe, {len(ours)} of our own streams linked"
              + (f", {len(left)} over the round's limits" if left else ""))
        story.print_items(ours + keep)

    # 4
    def ingest(self, r: dict, dry_run: bool) -> bool:
        """False when the dry run stops here."""
        # this round's picks only: a first-pass pick still waiting is `story run`'s to transcribe
        mine = set(r["picked"])
        print(story.to_transcribe(self.cfg, self.d, mine))
        if left := [it["id"] for it in story.untranscribed(self.d) if it["id"] not in mine]:
            print(f"  not transcribing {len(left)} first-pass pick(s) still waiting ({', '.join(left)}): "
                  f"clipper story run {self.b['slug']} --from ingest")
        if dry_run:
            print("dry run: stopping before ingest (clipper story dig <slug> goes on from here)")
            return False
        try:
            story.ingest(self.cfg, self.d, mine)
        except ClipperError as e:  # a dig goes on without a video that won't download
            print(f"ingest: {e}")
            cat = load_catalog(self.d)
            for c in self.dig["candidates"]:
                it = cat.get(c.get("item") or "")
                if it and (err := self.d / it["id"] / "error.txt").exists():
                    c["failed"] = err.read_text(encoding="utf-8").strip()[:300]
                    cat.pop(it["id"])
            save_catalog(self.d, cat)
        return True

    # 5
    def find(self, r: dict) -> None:
        from . import archive, archive_index

        subs = story.searched_subjects(self.cfg, self.d)
        subs += [s for s in self.dig["subjects"]
                 if s not in subs and (archive.subject_dir(self.cfg, s) / "index.sqlite").exists()]
        if not subs:
            print("find: nothing transcribed to search")
            return
        moments = (jsonstate.read(self.d / "moments.json", {}) or {}).get("moments", []) + self.dig["moments"]
        win = self.cfg["archive"]["index_window_s"]
        pool: dict[tuple, dict] = {}
        for tid in r["threads"]:
            t = self.thread(tid)
            for sub in subs:
                db = archive_index.open_index(self.cfg, sub)
                own = sub == self.d.name
                for q in t["queries"][:self.dc["queries_per_thread"]]:
                    try:
                        found = archive_index.hits(db, q, None if own else self.since,
                                                   None if own else self.b["before"] or None, self.dc["per_query"])
                    except sqlite3.OperationalError as e:
                        print(f"  skip {q!r}: {e}")
                        continue
                    for h in found:
                        h = {**h, "subject": sub, "t": h["start"], "thread": tid, "query": q}
                        if not any(near(h, m, win) for m in moments):
                            pool.setdefault((sub, h["item"], h["start"]), h)
                db.close()
        cands = list(pool.values())[:self.dc["candidates"]]
        print(f"find: {len(cands)} windows over {', '.join(subs)}")
        r["windows"] = [{k: h[k] for k in ("subject", "item", "start", "thread", "query", "title", "date")}
                        for h in cands]
        if not cands:
            return
        ts = [self.thread(x) for x in r["threads"]]
        lst = "\n\n".join(f"[{n}] {h['date'] or 'undated'} | {h['channel'] or '?'} | {h['title']} | "
                          f"searched for {h['thread']}\n{h['text']}" for n, h in enumerate(cands))
        out = self.ask(self.sc["find_model"], self.sc["find_effort"], self.prompts.get("story_dig_moments"),
                       f"Story: {self.b['brief']}\nPeople: {self.who()}\n\n<threads>\n{self.threads_list(ts)}\n"
                       f"</threads>\n\n<windows>\n{lst}\n</windows>", MOMENTS_SCHEMA, 3000)
        tr, new = story.Transcripts(self.cfg), []
        for p in (out or {}).get("picks", []):
            if not 0 <= p["n"] < len(cands):
                continue
            h = cands[p["n"]]
            r["windows"][p["n"]]["picked"] = True
            got = story.verify(tr, h["subject"], h, p["quote"], win)
            if not got:
                self.dig["unverified"].append({**{k: h[k] for k in ("subject", "item", "start", "title")},
                                               "quote": p["quote"], "round": r["n"], "why": p["why"]})
                continue
            t, quote = got
            it = tr.item(h["subject"], h["item"])
            tid = p["thread"] if self.thread(p["thread"]) else h["thread"]
            m = {"subject": h["subject"], "item": h["item"], "source": h["source"], "channel": h["channel"],
                 "date": h["date"], "title": h["title"], "url": h["url"], "page": it.get("page") or "",
                 "ours": bool(it.get("ours")), "t": round(t, 2),
                 "link": archive_index.link(it.get("page") or h["url"], h["source"], t),
                 "quote": quote, "role": p["role"], "fit": p["fit"], "why": p["why"],
                 "label": p["label"], "thread": tid, "round": r["n"]}
            if not any(near(m, x, 20) for x in moments + new):
                new.append(m)
        for m in sorted(new, key=lambda m: (m["date"] or "9999", m["item"], m["t"])):
            m["id"] = f"D{len(self.dig['moments']) + 1:02d}"
            self.dig["moments"].append(m)
        print(f"find: {len(new)} moments")
        for m in new:
            print(f"  {m['id']} [{m['label']}, {m['thread']}] {m['date'] or 'undated'} {m['channel']}: \"{m['quote']}\"")


    # -- the rounds --

    def next_round(self, began: int, rounds: int) -> dict | None:
        """The round left unfinished, else a new one while `rounds` allow, else None."""
        last = self.dig["rounds"][-1] if self.dig["rounds"] else None
        if last and last["step"] != "done":
            print(f"--- dig round {last['n']}, from {last['step']}", flush=True)
            return last
        if began >= rounds:
            return None
        r = {"n": len(self.dig["rounds"]) + 1, "step": "threads", "threads": [], "picked": [], "stopped": "",
             "at": datetime.now(UTC).isoformat(timespec="seconds")}
        self.dig["rounds"].append(r)
        print(f"--- dig round {r['n']}", flush=True)
        return r

    def run_round(self, r: dict, dry_run: bool) -> bool:
        """Runs a round's steps from where it stopped; False when a dry run stops before ingest."""
        for step in STEPS[STEPS.index(r["step"]):]:
            r["step"] = step
            self.save()
            if step == "ingest":
                if not self.ingest(r, dry_run):
                    return False
            else:
                getattr(self, step)(r)
            if step == "threads" and not r["threads"]:
                break
        r["step"] = "done"
        return True

    def rounds(self, rounds: int, dry_run: bool) -> tuple[int, bool, str]:
        """(rounds begun, whether one searched anything, why they stopped or "")."""
        began, dug = 0, False
        while r := self.next_round(began, rounds):
            began += 1
            if not self.run_round(r, dry_run):
                return began, dug, "dry run"
            dug = dug or bool(r["threads"])
            if r["stopped"]:
                return began, dug, r["stopped"]
        return began, dug, ""


# ---------- running ----------

def run(cfg: dict, slug: str, rounds: int | None = None, threads: list[str] = (), dry_run: bool = False,
        claude=None) -> float:
    """Dig rounds until --rounds, nothing new or the budget, then the dossier again; returns
    what the models cost (the swarms' calls included)."""
    from .archive_index import quiet_http
    from .llm import Claude, preflight
    from .prompts import Prompts

    b, d = story.load_brief(cfg, slug), story.story_dir(cfg, slug)
    dc = settings(cfg)
    if not (d / "moments.json").exists():
        raise ClipperError(f"{slug}: no moments to dig from yet (clipper story run {slug})")
    if claude is None:
        claude = Claude(cfg)
        quiet_http()
        preflight(claude, dc["model"])
    prompts = Prompts(cfg)
    budget = Budget(dc["budget_usd"], claude, spent_before=claude.cost)
    g = Dig(cfg, b, d, claude, budget, prompts)
    for text in threads:
        t = add_thread(g.dig, text)
        print(f"{t['id']} added: {text}")
    rounds = dc["rounds"] if rounds is None else rounds
    print(f"dig {slug}: up to {rounds} new round(s), models up to ~${estimate_round(cfg):.2f} a round "
          f"(budget ${dc['budget_usd']:.2f}), Modal up to {dc['max_videos']} videos / {dc['max_hours']} h a round")
    began, dug, stopped = 0, False, ""
    try:
        began, dug, stopped = g.rounds(rounds, dry_run)
        if dug:
            g.save()
            backup(d)
            print("--- dossier", flush=True)
            story.dossier(cfg, b, d, claude, budget, prompts)
        print(f"dig: {stopped or f'{began} round(s)'}; {len(g.dig['threads'])} threads, "
              f"{len(g.dig['candidates'])} candidates, {len(g.dig['moments'])} dig moments (dig.json, graph.json)")
    finally:
        g.save()
        with jsonstate.update(d / "runs.json", []) as runs:
            runs.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "from": "dig",
                         "dry_run": dry_run, "usd": round(claude.cost, 4)})
        print(f"story {slug} dig: models ~${claude.cost:.3f} (Modal is logged in `clipper modal`)")
    return claude.cost


def backup(d: Path) -> None:
    """The dossier from before the first dig, kept beside the new one for comparison."""
    for ext in ("json", "md"):
        src, dst = d / f"dossier.{ext}", d / f"dossier.before_dig.{ext}"
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


def estimate_round(cfg: dict) -> float:
    """A round's models at most: threads, the swarms, the judge and find."""
    dc, sc = settings(cfg), cfg["story"]
    s = swarm.settings(cfg, {**sc["swarm"], **dc["swarm"]})
    return (estimate(cfg, dc["model"], 6000, 2000) + dc["max_threads"] * s["budget_usd"]
            + estimate(cfg, sc["find_model"], 12000, 3000) + estimate(cfg, sc["find_model"], 30000, 3000))


def show(cfg: dict, slug: str) -> None:
    """dig.json, readable: the threads, then the candidates by label, then the dig's moments."""
    d = story.story_dir(cfg, slug)
    dig = jsonstate.read(d / "dig.json", None)
    if not dig:
        raise ClipperError(f"{slug}: not dug yet (clipper story dig {slug} --dry-run)")
    print(f"{slug}: {len(dig['rounds'])} round(s); edit \"flag\" in {d / 'dig.json'} (follow | drop)\n\nThreads:")
    for t in dig["threads"]:
        flag = f" flag={t['flag']}" if t.get("flag") else ""
        print(f"  {t['id']} [{t['kind']}, {t['status']}, depth {t['depth']}{flag}] {t['name']}: {t['what']}"
              + (f" ({', '.join(t['links'])})" if t["links"] else "") + (f" -- {t['note']}" if t.get("note") else ""))
    for lab in LABELS + [""]:
        cs = sorted((c for c in dig["candidates"] if c["label"] == lab), key=lambda c: -c.get("score", 0))
        if cs:
            print(f"\n{lab or 'not judged yet'} ({len(cs)}):")
            for c in cs:
                tags = [c["thread"]] + [x for x in ("item", "failed", "flag") if c.get(x)]
                print(f"  {c.get('score', 0):4.1f} [{', '.join(tags)}] {c.get('date') or 'undated'} "
                      f"{c.get('source')}: {(c.get('title') or '')[:80]}\n       {c.get('url')}"
                      + (f"\n       {c['label_why']}" if c.get("label_why") else ""))
    if dig["moments"]:
        print("\nMoments:")
        for m in dig["moments"]:
            print(f"  {m['id']} [{m['label']}, {m['thread']}, {m['role']}] {m['date'] or 'undated'} {m['channel']}: "
                  f"\"{m['quote']}\"\n       {m['link']}")
