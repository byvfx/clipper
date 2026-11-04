"""The search swarm (#109): many small searches at once, each free to follow up on what it
finds, instead of one plan up front and the searches one by one.

Library code for `clipper story` (#84) and `clipper investigate` (#108): they call `run`
and expose it through their own flags. One run:

1. Planner ([swarm] planner_model, Sonnet): the goal (a story brief, a lead) and the
   caller's context (the people, their channels) -> the first tasks, at most first_tasks.
   A task is {id, kind, target, why, parent, round}; kind is one of KINDS.
2. Workers ([swarm] worker_model, Haiku), [swarm] workers at once: each runs its tool in
   code (a search or a listing, no model), then one json_call reads the numbered results and
   returns findings (a result's number, strong | maybe, why) and up to followups_per_task
   follow-ups (new tasks, each with a why).
3. Coordinator (code): merges findings by platform id (a video found twice is one finding,
   with the better relevance and both tasks in found_by), queues follow-ups not seen before
   (kind + normalized target) as the next round, and stops at max_rounds, max_tasks,
   budget_usd or max_wall_s for this run, whichever comes first, and says which.

The trail is the caller's swarm.json (through jsonstate): every task with its parent, what
its tool returned, its findings and follow-ups, cost and time, plus the merged findings and
why each run stopped. A rerun resumes it: finished tasks are kept, failed and unstarted ones
run (follow-ups left by a run that stopped are its unstarted tasks). `print_trail` prints
the tree: who found what, from which search.

Guardrails (#108's rule, for every task):
- The model never fetches anything. A finding is one of the tool's own numbered results,
  and a target must parse as its kind's (a query, or a channel of a supported platform, or
  an archive subject on file); anything else is dropped and noted in the trail.
- Public content only, no logins beyond the YouTube cookies the archive already uses. No
  searches for addresses, whereabouts or private people's details: the prompts say so, and
  `private` drops a target that asks for them.
- No downloads: the swarm finds and ranks; the caller ingests within its own budget.
Each source has its own rate ([swarm] rates, requests a second; 0 = no limit), waited on
before every request (each YouTube tab, Odysee page, Kick retry), so the real concurrency is
the sources', not only `workers`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import jsonstate, ytdl
from .errors import ClipperError
from .kick import RateLimiter
from .llm import Budget, OverBudget, estimate

KINDS = {
    "youtube_search": "YouTube search: video titles, channels, approximate dates (target: a query)",
    "youtube_channel": "a YouTube channel's recent uploads and streams (target: its url or @handle)",
    "kick_videos": "a Kick channel's past broadcasts (target: the channel name, as in kick.com/<name>)",
    "odysee_channel": "an Odysee channel's uploads (target: @name)",
    "reddit_search": "Reddit threads (target: a query)",
    "web_search": "web pages: news, blogs, forums, titles and snippets (target: a query)",
    "archive_search": "words said in our transcribed archive of a person's videos "
                      "(target: '<subject>: <words>')",
    "our_streams": "whether our own shows covered it: segment titles and summaries (target: a few words)",
}
RELEVANCE = {"strong": 0, "maybe": 1}

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["tasks"],
    "properties": {"tasks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["kind", "target", "why"],
        "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "target": {"type": "string"},
                       "why": {"type": "string"}}}}},
}
WORKER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["findings", "followups"],
    "properties": {
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["n", "relevance", "why"],
            "properties": {"n": {"type": "integer"}, "relevance": {"type": "string", "enum": list(RELEVANCE)},
                           "why": {"type": "string"}}}},
        "followups": PLAN_SCHEMA["properties"]["tasks"]},
}
PLAN_OUT_TOKENS, WORKER_OUT_TOKENS = 1500, 700   # for the budget's estimates


def settings(cfg: dict, overrides: dict | None = None) -> dict:
    """[swarm], with a caller's own overrides on top (e.g. its [story] swarm table)."""
    from .config import DEFAULTS

    s = {**cfg["swarm"], **(overrides or {})}
    unknown = sorted(set(s) - set(DEFAULTS["swarm"]))
    if unknown:
        raise ClipperError(f"unknown swarm setting(s) {', '.join(unknown)} (see [swarm] in clipper.example.toml)")
    s["rates"] = {**DEFAULTS["swarm"]["rates"], **(cfg["swarm"].get("rates") or {}),
                  **((overrides or {}).get("rates") or {})}
    return s


# ---------- targets ----------

YT_CHANNEL = re.compile(r"(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[\w.-]+|channel/UC[\w-]{22}|c/[\w.-]+|user/[\w.-]+)",
                        re.IGNORECASE)
KICK_CHANNEL = re.compile(r"(?:https?://)?(?:www\.)?(?:kick\.com/)?@?([A-Za-z0-9_-]{2,30})/?")
ODYSEE_CHANNEL = re.compile(r"(?:https?://)?(?:odysee\.com/)?(@[\w.-]+)(?::\w+)?/?")
PRIVATE = re.compile(r"\b((?:home|house|street|residential|mailing|physical|postal|email|ip) address(?:es)?|"
                     r"address (?:of|for) (?:his|her|their)|phone numbers?|cell numbers?|lives at|lives in|"
                     r"where (?:does|do|did) \S+(?: \S+)? (?:live|stay|sleep)|"
                     r"where (?:is|are|was) \S+(?: \S+)? (?:right now|now|today|tonight|staying|living|located|based)|"
                     r"(?:current|exact|live|gps) location|located at|whereabouts|doxx?(?:ed|ing)?|"
                     r"license plates?|ssn|social security|zip ?codes?|workplace|employer|"
                     r"(?:kids?|children|son|daughter)'?s? school)\b", re.IGNORECASE)


def private(target: str) -> bool:
    """A target that asks for someone's whereabouts or private details (#108's rule)."""
    return bool(PRIVATE.search(target))


def target_of(kind: str, raw: str, archives: list[str]) -> str | None:
    """The task target in its kind's form, or None when it isn't one: channels must be a
    supported platform's channel, an archive search one of `archives`, and a query not a url."""
    t = " ".join(str(raw or "").split())
    if not t or len(t) > 200 or private(t):
        return None
    if kind == "youtube_channel":
        if re.fullmatch(r"@[\w.-]+", t):
            return f"https://www.youtube.com/{t}"
        m = YT_CHANNEL.fullmatch(t.rstrip("/").removesuffix("/videos").removesuffix("/streams"))
        return f"https://www.youtube.com/{m.group(1)}" if m else None
    if kind == "kick_videos":
        m = KICK_CHANNEL.fullmatch(t)
        return m.group(1).lower() if m else None
    if kind == "odysee_channel":
        m = ODYSEE_CHANNEL.fullmatch(t)
        return m.group(1) if m else None
    if kind == "archive_search":
        subject, _, q = t.partition(":")
        return f"{subject.strip()}: {q.strip()}" if subject.strip() in archives and q.strip() else None
    if kind not in KINDS or re.match(r"(https?://|www\.)", t, re.IGNORECASE):
        return None
    return t


def terms(text: str) -> list[str]:
    """The words that matter in a goal or query (investigate's coverage terms; imported here,
    not at the top, so investigate can import this module)."""
    from .investigate import terms as words

    return words(text)


def task_key(kind: str, target: str) -> str:
    """Two tasks are the same search when kind and target match, ignoring case and spacing."""
    return f"{kind}|{' '.join(target.lower().split())}"


def archives_on_file(cfg: dict) -> list[str]:
    """Archive subjects with a search index (`clipper archive index`)."""
    root = Path(cfg["paths"]["archive"])
    return sorted(p.parent.name for p in root.glob("*/index.sqlite")) if root.is_dir() else []


# ---------- tools (code, no model) ----------

def yt_filter(days: int) -> str:
    """YouTube's search upload-date filter (sp) for a window of days. Its sort by date is
    ignored for flat listings; the filter works."""
    if not days:
        return ""
    return "EgQIAxAB" if days <= 7 else "EgQIBBAB" if days <= 31 else "EgQIBRAB" if days <= 366 else ""


Pace = Callable[[], None]   # waits for the source's next request slot ([swarm] rates)


def no_wait() -> None:
    pass


def flat_youtube(url: str, n: int, pace: Pace = no_wait) -> list[dict]:
    """Up to n videos of a YouTube search or channel tab: titles, channels and approximate
    dates (YouTube's "3 days ago", which yt-dlp turns into a day), no download."""
    pace()
    r = ytdl.run("--flat-playlist", "--playlist-end", str(n), "--extractor-args", "youtubetab:approximate_date",
                 "-J", url)
    if r.returncode:
        raise RuntimeError(ytdl.last_error(r))
    data = json.loads(r.stdout)
    out = []
    for e in data.get("entries") or []:
        if not e.get("id") or (e.get("ie_key") or "Youtube") != "Youtube":
            continue  # a channel or playlist among the results
        ts = e.get("timestamp") or e.get("release_timestamp")
        out.append({"source": "youtube", "title": e.get("title") or "",
                    "channel": e.get("channel") or e.get("uploader") or data.get("channel") or "",
                    "channel_url": e.get("channel_url") or data.get("channel_url") or "",
                    "url": f"https://www.youtube.com/watch?v={e['id']}",
                    "date": datetime.fromtimestamp(ts, UTC).date().isoformat() if ts else "",
                    "date_approx": bool(ts), "duration_s": float(e.get("duration") or 0),
                    "views": e.get("view_count")})
    return out


def youtube_search(query: str, n: int, days: int = 0, pace: Pace = no_wait) -> list[dict]:
    params = {"search_query": query} | ({"sp": sp} if (sp := yt_filter(days)) else {})
    return flat_youtube("https://www.youtube.com/results?" + urlencode(params), n, pace)


def youtube_channel(url: str, n: int, pace: Pace = no_wait) -> list[dict]:
    """The channel's newest uploads and streams (its /videos and /streams tabs), newest first."""
    out, errors = [], []
    for tab in ("videos", "streams"):
        try:
            out += [{**r, "kind": tab} for r in flat_youtube(f"{url}/{tab}", n, pace)]
        except RuntimeError as e:
            errors.append(str(e))  # e.g. no streams tab
    if not out and errors:
        raise RuntimeError(errors[0])
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:n]


def kick_videos(cfg: dict, channel: str, n: int, limiter: RateLimiter | None = None) -> list[dict]:
    from .kick import list_videos

    return [{**it, "source": "kick"} for it in list_videos(channel, cfg, limiter)[:n]]


def odysee_channel(name: str, n: int, pace: Pace = no_wait) -> list[dict]:
    from .archive import list_odysee

    return [{**it, "url": f"https://odysee.com/{it['url'].removeprefix('lbry://').replace('#', ':')}",
             "lbry": it["url"]} for it in list_odysee(name, limit=n, pace=pace)]


def archive_search(cfg: dict, target: str, n: int, since: str | None, before: str | None) -> list[dict]:
    from . import archive_index

    subject, _, query = target.partition(":")
    db = archive_index.open_index(cfg, subject.strip())
    try:
        found = archive_index.hits(db, query.strip(), since, before, n)
    except sqlite3.OperationalError as e:  # FTS5 syntax the normalizer couldn't save
        raise RuntimeError(f"search {query.strip()!r}: {e}") from None
    finally:
        db.close()
    return [{"source": f"archive {subject.strip()}", "title": h["title"], "channel": h["channel"],
             "date": h["date"], "url": archive_index.link(h["url"], h["source"], h["start"]),
             "summary": " ".join(h["text"].split())[:400], "item": h["item"], "t": h["start"]} for h in found]


def in_window(results: list[dict], since: str | None, before: str | None) -> list[dict]:
    """Results dated inside [since, before); undated ones stay."""
    return [r for r in results if not r.get("date")
            or ((not since or r["date"] >= since) and (not before or r["date"] < before))]


def focus(results: list[dict], words: list[str], n: int) -> list[dict]:
    """The n results whose titles share most of the goal's words (ties: the tool's order),
    in the tool's order: a busy channel's 100 newest uploads -> the 20 a worker reads."""
    if len(results) <= n:
        return results
    ws = set(words)
    score = [(-len(ws & set(re.findall(r"[a-z0-9]+", (r.get("title") or "").lower()))), k)
             for k, r in enumerate(results)]
    return [results[k] for k in sorted(k for _, k in sorted(score)[:n])]


def limiters(rates: dict[str, float]) -> dict[str, RateLimiter]:
    """One limiter per source with a rate ([swarm] rates; 0 = none), shared by every task."""
    return {src: RateLimiter(rps) for src, rps in rates.items() if rps}


def default_tools(cfg: dict, days: int = 0, since: str | None = None, before: str | None = None,
                  depth: int = 200, limits: dict[str, RateLimiter] | None = None
                  ) -> dict[str, Callable[[str, int], list[dict]]]:
    """kind -> tool(target, n). days bounds YouTube searches (and is since when since isn't
    given); since/before bound every dated result (the channel listings read up to depth
    videos) and the archive. limits: each source's limiter, waited on before every request
    (each YouTube tab, each Odysee page, each Kick retry)."""
    from datetime import timedelta

    from . import investigate

    limits = limits or {}

    def pace(src: str) -> Pace:
        return limits[src].wait if src in limits else no_wait

    def paced(src: str, fn: Callable[[], list[dict]]) -> list[dict]:
        pace(src)()
        return fn()

    since = since or ((datetime.now(UTC) - timedelta(days=days)).date().isoformat() if days else None)
    return {
        "youtube_search": lambda q, n: in_window(youtube_search(q, n, days, pace("youtube")), since, before),
        "youtube_channel": lambda u, n: in_window(youtube_channel(u, depth, pace("youtube")), since, before),
        "kick_videos": lambda ch, n: in_window(kick_videos(cfg, ch, depth, limits.get("kick")), since, before),
        "odysee_channel": lambda name, n: in_window(odysee_channel(name, depth, pace("odysee")), since, before),
        "reddit_search": lambda q, n: paced("reddit", lambda: investigate.reddit_search(cfg, q, n)),
        "web_search": lambda q, n: paced("web", lambda: investigate.brave_search(cfg, q, n, "web")),
        "archive_search": lambda t, n: archive_search(cfg, t, n, since, before),
        "our_streams": lambda q, n: investigate.our_streams(cfg, investigate.terms(q), n),
    }


# ---------- findings ----------

TRACKING_PREFIX = ("utm_",)
TRACKING = {"fbclid", "gclid", "igshid", "si", "ref", "ref_src", "ref_url", "feature", "share_id", "context"}

def finding_key(r: dict) -> str:
    """One video, VOD, thread or archive moment is one finding however it was found."""
    u = r.get("url") or ""
    if r.get("item") is not None:
        return f"{r['source']}:{r['item']}:{int(r.get('t') or 0)}"
    if m := re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([\w-]{11})", u):
        return f"yt:{m.group(1)}"
    if m := re.search(r"kick\.com/[^/]+/videos/([0-9a-f-]{36})", u):
        return f"kick:{m.group(1)}"
    if m := re.search(r"odysee\.com/.*[:#]([0-9a-f]{40})", u):
        return f"od:{m.group(1)}"
    p = urlsplit(u)
    if not p.netloc:  # not a url (e.g. "kick-135553454 s08", one of our segments)
        return u.strip() or f"title:{(r.get('title') or '').lower()}"
    # Host case and tracking parameters don't make another page; the path's case and the
    # other parameters (viewtopic.php?t=123) can.
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not k.lower().startswith(TRACKING_PREFIX) and k.lower() not in TRACKING])
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}" + (f"?{query}" if query else "")


def merge(found: dict[str, dict], task_id: str, result: dict, relevance: str, why: str) -> bool:
    """Into found (key -> finding): a new finding, or the better relevance of an old one.
    True when it was new."""
    key = finding_key(result)
    old = found.get(key)
    if old is None:
        found[key] = {**result, "key": key, "relevance": relevance, "why": why, "found_by": [task_id]}
        return True
    if task_id not in old["found_by"]:
        old["found_by"].append(task_id)
    if RELEVANCE[relevance] < RELEVANCE[old["relevance"]]:
        old.update(relevance=relevance, why=why)
    return False


def ranked(found: dict[str, dict]) -> list[dict]:
    """Strong first, then the most often found, then newest."""
    out = sorted(found.values(), key=lambda f: f.get("date") or "", reverse=True)
    return sorted(out, key=lambda f: (RELEVANCE[f["relevance"]], -len(f["found_by"])))


# ---------- the run ----------

def listing(results: list[dict]) -> str:
    lines = []
    for k, r in enumerate(results):
        when = (r.get("date") or "undated") + ("~" if r.get("date_approx") else "")
        dur = f" | {r['duration_s'] / 60:.0f} min" if r.get("duration_s") else ""
        lines.append(f"[{k}] {r.get('source', '')} | {r.get('channel') or '?'} | {when}{dur} | {r.get('title', '')}")
        # the real ids, so a follow-up names the channel found, not a guess from its display name
        lines.append(f"    {r.get('url', '')}" + (f" | channel {r['channel_url']}" if r.get("channel_url") else ""))
        if r.get("summary"):
            lines.append(f"    {r['summary'][:300]}")
    return "\n".join(lines)


def new_trail(goal: str, s: dict) -> dict:
    return {"goal": goal, "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "planner": s["planner_model"], "worker": s["worker_model"], "tasks": [], "findings": [], "runs": []}


class Run:
    """One swarm run over a trail: the shared state its worker threads update."""

    def __init__(self, cfg: dict, s: dict, goal: str, context: str, trail: dict, path: Path, claude,
                 tools: dict, archives: list[str], log: Callable[[str], None]):
        from .prompts import Prompts

        self.cfg, self.s, self.goal, self.context, self.trail, self.path = cfg, s, goal, context, trail, path
        self.claude, self.tools, self.archives, self.log = claude, tools, archives, log
        self.budget = Budget(s["budget_usd"], claude, spent_before=claude.cost)
        self.t0, self.ran, self.stopped = time.monotonic(), 0, ""
        self.lock = threading.Lock()
        self.found = {f["key"]: f for f in trail["findings"]}
        self.words = terms(goal)
        self.seen = {task_key(t["kind"], t["target"]) for t in trail["tasks"]}
        self.prompts = Prompts(cfg)
        self.prompts.version = s["prompt_version"]
        kinds = {k: v for k, v in KINDS.items() if k in tools and (k != "archive_search" or archives)}
        self.kinds = kinds
        self.prompts.vars.update(
            kinds="\n".join(f"- {k}: {v}" for k, v in kinds.items()),
            archives=", ".join(archives) or "none", first_tasks=s["first_tasks"],
            followups=s["followups_per_task"], today=datetime.now(UTC).date().isoformat())

    # -- bookkeeping --

    def save(self) -> None:
        with self.lock:
            self.trail["findings"] = ranked(self.found)
            jsonstate.write(self.path, self.trail)

    def add_task(self, kind: str, raw: str, why: str, parent: str | None, rnd: int,
                 dropped: list[dict] | None = None) -> dict | None:
        """A new task, unless it isn't valid or was seen; dropped ones go in `dropped`."""
        target = target_of(kind, raw, self.archives) if kind in self.kinds else None
        if target is None:
            if dropped is not None:
                dropped.append({"kind": kind, "target": raw, "why": "not a valid or allowed target"})
            return None
        with self.lock:
            key = task_key(kind, target)
            if key in self.seen:
                return None
            self.seen.add(key)
            t = {"id": f"t{len(self.trail['tasks']) + 1:03d}", "kind": kind, "target": target, "why": why,
                 "parent": parent, "round": rnd, "status": "pending"}
            self.trail["tasks"].append(t)
            return t

    def stop(self, reason: str) -> None:
        with self.lock:
            self.stopped = self.stopped or reason

    def limits(self) -> str:
        """Why no more tasks may start, or ""."""
        if self.stopped:
            return self.stopped
        if time.monotonic() - self.t0 > self.s["max_wall_s"]:
            return f"wall time {self.s['max_wall_s']} s"
        if self.ran >= self.s["max_tasks"]:
            return f"max_tasks {self.s['max_tasks']}"
        return ""

    def call(self, model: str, effort: str, system: str, user: str, schema: dict,
             out_tokens: int) -> tuple[dict | None, float]:
        """(the answer, None on refusal; what this call cost)."""
        est = estimate(self.cfg, model, (len(system) + len(user)) / 4, out_tokens)
        self.budget.reserve(est)  # OverBudget stops the run
        try:
            out, meta = self.claude.json_call(model, system, user, schema, effort)
        finally:
            self.budget.release(est)
        return out, estimate(self.cfg, model, meta.get("input_tokens", 0), meta.get("output_tokens", 0))

    # -- planner --

    def plan(self) -> None:
        s = self.s
        user = f"Goal: {self.goal}\n" + (f"\nWhat we know:\n{self.context}\n" if self.context else "")
        try:
            out, usd = self.call(s["planner_model"], s["planner_effort"], self.prompts.get("swarm_plan"), user,
                                 PLAN_SCHEMA, PLAN_OUT_TOKENS)
        except OverBudget as e:
            self.stop(str(e))
            return
        out = out or {"tasks": []}
        self.trail.update(planned=True, planner_usd=round(usd, 5))
        dropped: list[dict] = []
        made = [t for x in out["tasks"][:s["first_tasks"]]
                if (t := self.add_task(x["kind"], x["target"], x["why"], None, 0, dropped))]
        self.trail["planner_dropped"] = dropped
        self.log(f"planner: {len(made)} tasks" + (f" ({len(dropped)} dropped)" if dropped else ""))

    # -- one task --

    def finish(self, t: dict, **fields) -> None:
        """A task's outcome, written under the lock (save() may be serializing the trail)."""
        with self.lock:
            t.pop("error", None)
            t.update(fields, at=datetime.now(UTC).isoformat(timespec="seconds"))
        self.save()

    def failed(self, t: dict, t0: float, e: Exception) -> list[dict]:
        error = f"{type(e).__name__}: {str(e)[:200]}"
        self.log(f"[r{t['round']} {t['id']}] {t['kind']} {t['target']!r}: failed ({error})")
        self.finish(t, status="failed", error=error, s=round(time.monotonic() - t0, 1))
        return []

    def work(self, t: dict) -> list[dict]:
        """Runs one task; returns the follow-up tasks it queued."""
        s = self.s
        with self.lock:
            why_not = self.limits()
            if not why_not:
                self.ran += 1
        if why_not:
            return []
        t0, usd = time.monotonic(), 0.0
        try:
            # the tools wait on their source's rate before each request
            results = focus(self.tools[t["kind"]](t["target"], s["results_per_task"]),
                            self.words + terms(f"{t['target']} {t['why']}"), s["results_per_task"])
        except Exception as e:  # noqa: BLE001  # one source down doesn't stop the swarm
            return self.failed(t, t0, e)
        picked, followups, dropped, new = [], [], [], 0
        if results:
            with self.lock:
                parent = next((p for p in self.trail["tasks"] if p["id"] == t["parent"]), None)
                searched = "\n".join(f"- {x['kind']} {x['target']}" for x in self.trail["tasks"][-60:])
            user = (f"Goal: {self.goal}\n" + (f"\nWhat we know:\n{self.context}\n" if self.context else "")
                    + f"\nThis search: {t['kind']} {t['target']!r} (why: {t['why']})"
                    + (f"\nIt follows up {parent['kind']} {parent['target']!r}." if parent else "")
                    + f"\n\n<results>\n{listing(results)}\n</results>\n\nAlready searched (don't repeat):\n{searched}")
            try:
                out, usd = self.call(s["worker_model"], s["worker_effort"], self.prompts.get("swarm_worker"), user,
                                     WORKER_SCHEMA, WORKER_OUT_TOKENS)
                out = out or {"findings": [], "followups": []}
            except OverBudget as e:
                self.stop(str(e))
                with self.lock:
                    self.ran -= 1  # it never ran: a rerun picks it up
                return []
            except Exception as e:  # noqa: BLE001  # a model error fails this task, not the run
                return self.failed(t, t0, e)
            for f in out["findings"]:
                if 0 <= f["n"] < len(results):
                    with self.lock:
                        new += merge(self.found, t["id"], results[f["n"]], f["relevance"], f["why"])
                    picked.append({"n": f["n"], "relevance": f["relevance"], "why": f["why"]})
            for x in out["followups"][:s["followups_per_task"]]:
                if task := self.add_task(x["kind"], x["target"], x["why"], t["id"], t["round"] + 1, dropped):
                    followups.append(task)
        strong = sum(f["relevance"] == "strong" for f in picked)
        self.log(f"[r{t['round']} {t['id']}] {t['kind']} {t['target']!r}: {len(results)} results, "
                 f"{len(picked)} found ({strong} strong, {new} new)"
                 + (f", +{len(followups)} follow-ups" if followups else ""))
        self.finish(t, status="done", findings=picked, followups=[x["id"] for x in followups], dropped=dropped,
                    results=[{k: r[k] for k in ("source", "title", "channel", "url", "date") if k in r}
                             for r in results],
                    usd=round(usd, 5), s=round(time.monotonic() - t0, 1))
        return followups

    # -- the rounds --

    def go(self) -> None:
        if not self.trail.get("planned"):  # once per trail; the caller's seeds run beside the plan
            self.plan()
        todo = [t for t in self.trail["tasks"] if t["status"] != "done"]
        for t in todo:
            t["status"] = "pending"
        rounds = 0
        with ThreadPoolExecutor(self.s["workers"]) as pool:
            while todo and not self.limits():
                if rounds >= self.s["max_rounds"]:
                    self.stop(f"max_rounds {self.s['max_rounds']}")
                    break
                nxt = [f for batch in pool.map(self.work, todo) for f in batch]
                rounds += 1
                todo = nxt + [t for t in todo if t["status"] == "pending"]  # a task a limit held back
                todo = list({t["id"]: t for t in todo}.values())
        self.stopped = self.stopped or (self.limits() if todo else "no follow-ups left")


def run(cfg: dict, goal: str, trail_path: Path, context: str = "", seeds: list[dict] = (),
        known: list[dict] = (), overrides: dict | None = None, claude=None, tools: dict | None = None,
        archives: list[str] | None = None, days: int = 0, since: str | None = None, before: str | None = None,
        log: Callable[[str], None] = print) -> dict:
    """Search for `goal` and return the merged findings, ranked, with why the run stopped and
    what it cost: {"findings", "stopped", "usd", "tasks"}.

    context: what the caller knows (people, their channels, dates), shown to the planner and
    every worker. seeds: tasks the caller wants run first ({kind, target, why}), e.g. a
    person's own channels. known: findings the caller already has (urls given by hand),
    merged as strong and found by "given". claude: the caller's LLM, so its cost context and
    total carry the swarm's calls. tools: kind -> tool(target, n), for tests or a caller's
    own sources; default_tools otherwise. A trail at trail_path is resumed."""
    from .archive_index import quiet_http
    from .llm import Claude, preflight

    s = settings(cfg, overrides)
    if claude is None:
        claude = Claude(cfg)
        quiet_http()
        preflight(claude, s["worker_model"])
    tools = tools or default_tools(cfg, days, since, before, s["channel_depth"], limiters(s["rates"]))
    archives = archives_on_file(cfg) if archives is None else archives
    trail = jsonstate.read(trail_path, None) or new_trail(goal, s)
    trail_path.parent.mkdir(parents=True, exist_ok=True)
    r = Run(cfg, s, goal, context, trail, trail_path, claude, tools, archives, log)
    for k in known:
        merge(r.found, "given", k, "strong", k.get("why") or "given by hand")
    for x in seeds:
        r.add_task(x["kind"], x["target"], x.get("why") or "given", None, 0, trail.setdefault("seed_dropped", []))
    r.go()
    usd = r.budget.spent
    trail["runs"].append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "tasks": r.ran,
                          "usd": round(usd, 4), "stopped": r.stopped,
                          "s": round(time.monotonic() - r.t0, 1)})
    r.save()
    found = trail["findings"]
    strong = sum(f["relevance"] == "strong" for f in found)
    log(f"swarm: {r.ran} tasks, {len(found)} findings ({strong} strong), ~${usd:.3f}; stopped: {r.stopped}")
    return {"findings": found, "stopped": r.stopped, "usd": usd, "tasks": r.ran}


# ---------- the trail ----------

def print_trail(trail_path: Path, log: Callable[[str], None] = print) -> None:
    """The task tree, each task with the findings it was first to find."""
    trail = jsonstate.read(trail_path, None)
    if not trail:
        raise ClipperError(f"no swarm trail at {trail_path}")
    first: dict[str, list[dict]] = {}
    for f in trail["findings"]:
        first.setdefault(f["found_by"][0], []).append(f)
    kids: dict[str | None, list[dict]] = {}
    for t in trail["tasks"]:
        kids.setdefault(t["parent"], []).append(t)
    log(f"goal: {trail['goal']}")
    for f in first.get("given", []):
        log(f"  * given    {f.get('title') or f['url']}  {f['url']}")

    def show(t: dict, depth: int) -> None:
        pad = "  " * depth
        n = len(t.get("results") or [])
        state = {"done": f"{n} results", "failed": f"failed: {t.get('error', '')}"}.get(t["status"], t["status"])
        log(f"{pad}{t['id']} {t['kind']} {t['target']!r} ({t['why']}) -> {state}")
        for f in first.get(t["id"], []):
            also = f" (+{len(f['found_by']) - 1} more)" if len(f["found_by"]) > 1 else ""
            log(f"{pad}  * {f['relevance']:<6} {f.get('date') or 'undated'} {f.get('channel') or '?'}: "
                f"{(f.get('title') or '')[:80]}{also}\n{pad}      {f['url']}")
        for d in t.get("dropped") or []:
            log(f"{pad}  x dropped {d['kind']} {d['target']!r}")
        for k in kids.get(t["id"], []):
            show(k, depth + 1)

    for t in kids.get(None, []):
        show(t, 1)
    for run_ in trail["runs"]:
        log(f"run {run_['at']}: {run_['tasks']} tasks, ~${run_['usd']:.3f}, {run_['s']} s; stopped: {run_['stopped']}")
