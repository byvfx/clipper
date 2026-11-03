"""`clipper news` (#110): headlines about our people from news feeds, Google News and Reddit.

    clipper news check [--no-judge]       fetch what's due, match the watchlist, judge the matches
                                          (the bot runs it every [news] check_minutes)
    clipper news list [--days 3] [--person X] [--all]
    clipper news search "<query>"         Google News now, and what's stored
    clipper news feeds                    each source: last fetch, items, error
    clipper news watch                    the watchlist: who, under which names, which shows
    clipper news dismiss <story> ...
    clipper news context <story> [--show KEY]   the story as the show's next stream_context

Sources ([news]): `feeds` (RSS or Atom: TMZ, Dexerto), `reddit` subreddits (their RSS), and
with `google_news` one Google News search per watched person whose name can't be taken for
a word (two words or more, or listed in [news] watch), every `search_minutes`. Only the
headline, a short snippet and the link are kept (paths.news/news.sqlite, `keep_days`);
articles are never fetched. A feed is fetched at most every `min_fetch_minutes`, with ETag
and If-Modified-Since.

The watchlist: each show's [meta] cast, [scoring] known_figures ("Jer (The Quartering)") and
show name, the cast library's people and aliases (#41), and [news] watch. A one-word name
matches only as written ("Jer", not "jer"); longer names in any case.

Matches go to the judge ([news] judge_model, prompts/news_judge_<v>.md) in batches: about our
person and not a namesake, news and not evergreen, a public figure's public conduct (never
family or private people around them); strong, maybe or no, and which story it belongs to,
so five outlets on one event are one story.

The bot posts new stories to [news] channel_id (else the review channel) with Dismiss and
Tonight's context, and [news] preshow_minutes before a show's usual start it suggests that
day's stories about its people. Context waits in paths.news/context.json until the show's
next stream: the worker adds it to that stream's config.toml [scoring] stream_context before
the stages run."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import time
import tomllib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from . import jsonstate
from .errors import ClipperError

UA = "clipper-news/1.0 (headline watch; https://github.com/byvfx/clipper)"
GOOGLE = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
REDDIT = "https://www.reddit.com/r/{sub}/new/.rss"
RATINGS = ("strong", "maybe", "no")
SNIPPET = 300
MAX_BYTES = 5_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (url TEXT PRIMARY KEY, name TEXT, etag TEXT, modified TEXT,
    fetched_at TEXT, error TEXT, items INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS items (url TEXT PRIMARY KEY, tkey TEXT, title TEXT, snippet TEXT,
    published TEXT, source TEXT, feed TEXT, seen_at TEXT, matched TEXT, rating TEXT,
    story INTEGER, why TEXT, dup_of TEXT, claim TEXT, claimed_at TEXT);
CREATE INDEX IF NOT EXISTS items_tkey ON items(tkey);
CREATE TABLE IF NOT EXISTS stories (id INTEGER PRIMARY KEY AUTOINCREMENT, headline TEXT, person TEXT,
    show TEXT, rating TEXT, why TEXT, first_at TEXT, last_at TEXT, posted_at TEXT,
    message_id TEXT, dismissed INTEGER DEFAULT 0, context_at TEXT);
CREATE TABLE IF NOT EXISTS suggested (id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT UNIQUE, at TEXT,
    show TEXT, stories TEXT);
"""

# AUTOINCREMENT: a story id is never reused after old ones are forgotten, so a Discord button
# that outlives its story can't act on a newer one.


def now() -> datetime:
    return datetime.now(UTC)


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds")


def home(cfg: dict) -> Path:
    d = Path(cfg["paths"]["news"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def connect(cfg: dict) -> sqlite3.Connection:
    db = sqlite3.connect(home(cfg) / "news.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


# ---------- the watchlist ----------

@dataclass
class Watch:
    name: str
    terms: list[str] = field(default_factory=list)
    shows: list[str] = field(default_factory=list)   # show keys
    search: bool = False                              # one Google News search for them


def parse_figures(text: str) -> list[tuple[str, list[str]]]:
    """"Jer (The Quartering), DSP (Phil), the detractors" -> [(name, aliases)]."""
    out = []
    for part in re.split(r",(?![^()]*\))", text or ""):
        part = part.strip()
        m = re.match(r"(.+?)\s*\((.+)\)$", part)
        if m:
            out.append((m[1].strip(), [a.strip() for a in m[2].split(",") if a.strip()]))
        elif part:
            out.append((part, []))
    return out


def watchlist(cfg: dict) -> dict[str, Watch]:
    """{lowercased name: Watch}: the terms in [news] watch and each show's [shows.<key>.news]
    watch, and with [news] auto_watch the shows' people and names and the cast library too."""
    from . import cast
    from .config import for_show

    nc = cfg["news"]
    ignore = {x.lower() for x in nc["ignore"]}
    out: dict[str, Watch] = {}

    def add(name: str, aliases=(), show: str = "", search: bool = False) -> None:
        name = name.strip()
        # A real name starts with a capital or a digit; "Rich (review tech)" is a note, not a name.
        if not name or not (name[0].isupper() or name[0].isdigit()) or name.lower() in ignore:
            return
        w = out.setdefault(name.lower(), Watch(name))
        for t in (name, *aliases):
            t = t.strip()
            if t and t.lower() not in {x.lower() for x in w.terms} and t.lower() not in ignore:
                w.terms.append(t)
        if show and show not in w.shows:
            w.shows.append(show)
        w.search = w.search or search or any(len(t.split()) > 1 for t in w.terms)

    def terms(watch: dict, show: str = "") -> None:
        for person, aliases in (watch or {}).items():
            add(person, aliases if isinstance(aliases, list) else [aliases], show=show, search=True)

    root = cfg.get("_root") or cfg
    terms((root.get("news") or nc)["watch"])
    for key, show in (cfg.get("shows") or {}).items():
        terms((show.get("overrides", {}).get("news") or {}).get("watch") or {}, key)
    if not nc["auto_watch"]:
        return out
    for key in cfg.get("shows") or {}:
        sc = for_show(cfg, key)
        if sc["meta"]["show_name"]:
            add(sc["meta"]["show_name"], show=key)
        for person in sc["meta"]["cast"] or []:
            add(person, show=key)
        for person, aliases in parse_figures(sc["scoring"]["known_figures"]):
            if aliases and not any(a[0].isupper() or a[0].isdigit() for a in aliases):
                continue  # "Rich (review tech)": someone described, not a public name
            add(person, aliases, show=key)
    try:  # everyone in the cast library, with or without a kept photo
        library = cast.people(cfg) if cfg["thumbnail"].get("photos") else {}
    except OSError:  # a machine without the photos share
        library = {}
    for person, d in library.items():
        if "(" not in person:
            add(person, d.get("aliases") or [])
    return out


def matcher(watch: dict[str, Watch]):
    """text -> the watched names it mentions. One-word terms as written, longer ones in any case."""
    pats = []
    for w in watch.values():
        for t in w.terms:
            flags = re.IGNORECASE if len(t.split()) > 1 else 0
            # "Don's" is Don; "Don't" isn't.
            pats.append((w.name, re.compile(rf"(?<!\w){re.escape(t)}(?!\w)(?!['’]t\b)", flags)))

    def match(text: str) -> list[str]:
        return sorted({name for name, p in pats if p.search(text or "")})
    return match


# ---------- sources ----------

def sources(cfg: dict, watch: dict[str, Watch]) -> list[tuple[str, str, int]]:
    """(name, url, minutes between fetches) for every feed, subreddit and Google News search."""
    nc = cfg["news"]
    out = [(f.get("name") or f["url"], f["url"], nc["min_fetch_minutes"]) for f in nc["feeds"]]
    out += [(f"r/{sub}", REDDIT.format(sub=sub), nc["min_fetch_minutes"]) for sub in nc["reddit"]]
    if nc["google_news"]:
        for w in watch.values():
            if w.search:
                terms = [t for t in w.terms if len(t.split()) > 1] or w.terms
                q = " OR ".join(f'"{t}"' for t in terms[:4]) + f" when:{max(1, nc['max_age_hours'] // 24)}d"
                out.append((f"Google News: {w.name}", GOOGLE.format(q=quote(q)), nc["search_minutes"]))
    return out


def fetch(url: str, etag: str | None = None, modified: str | None = None) -> tuple[bytes | None, str, str]:
    """(body or None when unchanged, etag, last-modified)."""
    headers = {"User-Agent": UA}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ClipperError(f"{url}: over {MAX_BYTES // 1_000_000} MB")
            return body, r.headers.get("ETag", "") or "", r.headers.get("Last-Modified", "") or ""
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag or "", modified or ""
        raise


def _text(el) -> str:
    return "".join(el.itertext()) if el is not None else ""


def clean(text: str, n: int = SNIPPET) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(text or "")))
    return " ".join(text.split())[:n]


def when(text: str) -> str:
    """An RSS or Atom date as ISO UTC; "" if there is none or it can't be read."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        t = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            t = datetime.fromisoformat(text)
        except ValueError:
            return ""
    return iso(t if t.tzinfo else t.replace(tzinfo=UTC))


def parse_feed(body: bytes, feed: str = "") -> list[dict]:
    """RSS 2.0 or Atom -> [{url, title, snippet, published, source}]."""
    root = ET.fromstring(body)
    a = "{http://www.w3.org/2005/Atom}"
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        out.append({"url": (it.findtext("link") or it.findtext("guid") or "").strip(),
                    "title": clean(it.findtext("title"), 300),
                    "snippet": clean(it.findtext("description")),
                    "published": when(it.findtext("pubDate")),
                    "source": clean(_text(src), 80) if src is not None else feed})
    for e in root.iter(f"{a}entry"):
        link = next((x.get("href") for x in e.findall(f"{a}link") if x.get("rel", "alternate") == "alternate"), "")
        out.append({"url": (link or e.findtext(f"{a}id") or "").strip(),
                    "title": clean(e.findtext(f"{a}title"), 300),
                    "snippet": clean(_text(e.find(f"{a}summary")) or _text(e.find(f"{a}content"))),
                    "published": when(e.findtext(f"{a}published") or e.findtext(f"{a}updated")),
                    "source": feed})
    return [x for x in out if x["url"].startswith(("http://", "https://")) and x["title"]]


def title_key(title: str) -> str:
    """The same headline from two places (Google News adds " - Outlet")."""
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\s+-\s+[^-]+$", "", title.lower()))[:120]


def store(db: sqlite3.Connection, entries: list[dict], feed: str, max_age_hours: int) -> int:
    """New items (not seen by url); returns how many. The same headline from another place in
    the last 3 days is kept as a copy of the first (dup_of): its link joins the story, and it
    takes the first one's verdict instead of being judged again."""
    t, cut, n = now(), now() - timedelta(hours=max_age_hours), 0
    for e in entries:
        if e["published"] and datetime.fromisoformat(e["published"]) < cut:
            continue
        if db.execute("SELECT 1 FROM items WHERE url = ?", (e["url"],)).fetchone():
            continue
        tk = title_key(e["title"])
        first = db.execute("SELECT url FROM items WHERE tkey = ? AND dup_of IS NULL AND seen_at > ?",
                           (tk, iso(t - timedelta(days=3)))).fetchone()
        db.execute("INSERT INTO items (url, tkey, title, snippet, published, source, feed, seen_at, dup_of) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (e["url"], tk, e["title"], e["snippet"], e["published"] or iso(t), e["source"], feed, iso(t),
                    first["url"] if first else None))
        n += 1
    return n


def copy_verdicts(db: sqlite3.Connection) -> None:
    """Copies of a judged headline take its rating and story."""
    db.execute("UPDATE items SET rating = (SELECT o.rating FROM items o WHERE o.url = items.dup_of), "
               "story = (SELECT o.story FROM items o WHERE o.url = items.dup_of) "
               "WHERE dup_of IS NOT NULL AND rating IS NULL "
               "AND (SELECT o.rating FROM items o WHERE o.url = items.dup_of) IS NOT NULL")


# ---------- check ----------

def check(cfg: dict, judge: bool = True, claude=None, say=print) -> list[int]:
    """Fetch what's due, store new items, match them, judge the matches. Returns the stories
    the bot should post (not posted, not dismissed, strong, or maybe with post_maybe)."""
    nc = cfg["news"]
    watch = watchlist(cfg)
    if not watch:  # no terms: nothing is fetched or searched
        say("nothing to watch: add terms to [news] watch (or turn on [news] auto_watch)")
        return []
    match = matcher(watch)
    db = connect(cfg)
    try:
        new = 0
        for name, url, every in sources(cfg, watch):
            row = db.execute("SELECT etag, modified, fetched_at FROM feeds WHERE url = ?", (url,)).fetchone()
            if row and row["fetched_at"] and now() - datetime.fromisoformat(row["fetched_at"]) < timedelta(minutes=every):
                continue
            err, got = "", 0
            etag, modified = (row["etag"], row["modified"]) if row else ("", "")
            try:
                body, new_etag, new_modified = fetch(url, etag, modified)
                if body:
                    got = store(db, parse_feed(body, name), name, nc["max_age_hours"])
                etag, modified = new_etag, new_modified  # only once the body was read and stored
            except (OSError, ET.ParseError, ValueError, ClipperError) as e:
                err = f"{type(e).__name__}: {e}"[:200]
                say(f"  {name}: {err}")
            db.execute("INSERT OR REPLACE INTO feeds (url, name, etag, modified, fetched_at, error, items) "
                       "VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT items FROM feeds WHERE url = ?), 0) + ?)",
                       (url, name, etag, modified, iso(now()), err, url, got))
            db.commit()
            new += got
            if url.startswith("https://news.google.com"):
                time.sleep(1.0)  # one search at a time, politely
        hits = 0
        for r in db.execute("SELECT url, title, snippet FROM items WHERE matched IS NULL").fetchall():
            names = match(f"{r['title']} {r['snippet']}")
            hits += bool(names)
            db.execute("UPDATE items SET matched = ?, rating = ? WHERE url = ?",
                       (json.dumps(names), None if names else "", r["url"]))
        db.commit()
        say(f"{new} new item(s), {hits} name match(es)")
        if judge:
            judge_items(cfg, db, watch, claude, say)
        copy_verdicts(db)
        cut = iso(now() - timedelta(days=nc["keep_days"]))
        db.execute("DELETE FROM items WHERE seen_at < ?", (cut,))
        db.execute("DELETE FROM stories WHERE last_at < ? AND id NOT IN (SELECT story FROM items WHERE story IS NOT NULL)", (cut,))
        db.commit()
        return to_post(cfg, db)
    finally:
        db.close()


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "person": {"type": "string"}, "show": {"type": "string"},
                       "rating": {"type": "string", "enum": list(RATINGS)},
                       "story": {"type": "integer"}, "same_as": {"type": "integer"},
                       "headline": {"type": "string"}, "why": {"type": "string"}},
        "required": ["i", "person", "show", "rating", "story", "same_as", "headline", "why"],
        "additionalProperties": False}}},
    "required": ["items"],
    "additionalProperties": False,
}


def judge_request(batch: list[sqlite3.Row], watch: dict[str, Watch], open_stories: list[sqlite3.Row],
                  names: dict[str, str]) -> str:
    lines = ["Our people (name, other names, shows):"]
    named = sorted({n for r in batch for n in json.loads(r["matched"])})
    for n in named:
        w = watch.get(n.lower())
        if w:
            lines.append(f"- {w.name}: {', '.join(w.terms[1:]) or '-'}; shows: "
                         + (", ".join(f"{names.get(s, s)} [{s}]" for s in w.shows) or "-"))
    lines += ["", "Open stories (story id: headline, person):"]
    lines += [f"- {s['id']}: {s['headline']} ({s['person']})" for s in open_stories] or ["- none"]
    lines += ["", "Items:"]
    for i, r in enumerate(batch, 1):
        lines.append(f"{i}. [{r['source']}, {r['published'][:16]}] {r['title']}"
                     + (f" | {r['snippet']}" if r["snippet"] else "") + f" (names: {', '.join(json.loads(r['matched']))})")
    return "\n".join(lines)


def judge_items(cfg: dict, db: sqlite3.Connection, watch: dict[str, Watch], claude=None, say=print) -> int:
    """Rate the matched, unjudged items, newest first, at most [news] max_judge per check."""

    nc = cfg["news"]
    # A claim per check, so a `news check` by hand while the bot's runs judges other rows; a
    # claim older than half an hour was left by a check that died. Unjudged rows (a refusal, a
    # verdict missing) are tried again by later checks until they're older than max_age_hours.
    token, t = f"{os.getpid()}-{time.monotonic_ns()}", now()
    db.execute("UPDATE items SET claim = NULL WHERE claim IS NOT NULL AND claimed_at < ?",
               (iso(t - timedelta(minutes=30)),))
    db.execute("UPDATE items SET claim = ?, claimed_at = ? WHERE url IN (SELECT url FROM items WHERE rating IS NULL "
               "AND claim IS NULL AND dup_of IS NULL AND seen_at > ? ORDER BY published DESC LIMIT ?)",
               (token, iso(t), iso(t - timedelta(hours=nc["max_age_hours"])), nc["max_judge"]))
    db.commit()
    todo = db.execute("SELECT * FROM items WHERE claim = ? ORDER BY published DESC", (token,)).fetchall()
    if not todo:
        return 0
    try:
        return _judge(cfg, db, watch, todo, claude, say)
    finally:
        db.execute("UPDATE items SET claim = NULL WHERE claim = ?", (token,))
        db.commit()


def _judge(cfg: dict, db: sqlite3.Connection, watch: dict[str, Watch], todo: list, claude, say) -> int:
    from .prompts import Prompts

    nc = cfg["news"]
    if claude is None:
        from .llm import Claude, preflight

        claude = Claude(cfg)
        preflight(claude, nc["judge_model"])
    prompts = Prompts(cfg)
    prompts.version = nc["prompt_version"]
    system = prompts.get("news_judge")
    names = show_names(cfg)
    done = 0
    for b in range(0, len(todo), 20):
        batch = todo[b:b + 20]
        # Dismissed stories too: a later headline on a dismissed event joins it (and stays unposted).
        opened = db.execute("SELECT id, headline, person FROM stories WHERE last_at > ? "
                            "ORDER BY last_at DESC LIMIT 40", (iso(now() - timedelta(days=3)),)).fetchall()
        out, _meta = claude.json_call(nc["judge_model"], system, judge_request(batch, watch, opened, names),
                                      JUDGE_SCHEMA, nc["judge_effort"])
        if out is None:  # refused: leave the rest unjudged for the next check
            say(f"the judge refused a batch of {len(batch)}; trying again next check")
            break
        verdicts = {v["i"]: v for v in out.get("items", []) if isinstance(v.get("i"), int)}
        apply_verdicts(db, batch, verdicts, {s["id"] for s in opened}, set(names))
        db.commit()
        done += len(verdicts)
    say(f"judged {done} item(s)" + (f", ${claude.cost:.3f}" if getattr(claude, "cost", 0) else ""))
    return done


def apply_verdicts(db: sqlite3.Connection, batch: list, verdicts: dict[int, dict], open_ids: set[int],
                   shows: set[str]) -> None:
    """Each item's rating, and its story: an open one the judge named, the new one of an earlier
    item in this batch it is the same event as (same_as), else a new one. An item the judge left
    out stays unjudged (a later check tries it again)."""
    made: dict[int, int] = {}  # item number -> the story it went into, in this batch
    for i, r in enumerate(batch, 1):
        v = verdicts.get(i)
        if not v:
            continue
        rating = v["rating"] if v["rating"] in RATINGS else "no"
        sid = v["story"] if v["story"] in open_ids else made.get(v.get("same_as") or 0)
        if rating == "no":
            sid = None
        elif sid:
            t = r["published"] or iso(now())
            cur = db.execute("SELECT rating, first_at, last_at FROM stories WHERE id = ?", (sid,)).fetchone()
            db.execute("UPDATE stories SET rating = ?, first_at = ?, last_at = ? WHERE id = ?",
                       ("strong" if "strong" in (cur["rating"], rating) else "maybe",
                        min(cur["first_at"], t), max(cur["last_at"], t), sid))
        else:
            t = r["published"] or iso(now())
            title = re.sub(r"\s+-\s+[^-]+$", "", r["title"])  # Google News: "Headline - Outlet"
            sid = db.execute("INSERT INTO stories (headline, person, show, rating, why, first_at, last_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (clean(v["headline"], 120) or title, v["person"], v["show"] if v["show"] in shows else "",
                              rating, clean(v["why"], 300), t, t)).lastrowid
            open_ids.add(sid)
        if sid:
            made[i] = sid
        db.execute("UPDATE items SET rating = ?, story = ?, why = ? WHERE url = ?",
                   (rating, sid, clean(v.get("why", ""), 300), r["url"]))


# ---------- stories ----------

def to_post(cfg: dict, db: sqlite3.Connection) -> list[int]:
    """Stories to post now: at most [news] max_posts, the shows' own people before others (the
    cast library's), strong before maybe, then the newest; in the order they broke."""
    nc = cfg["news"]
    ok = ("strong", "maybe") if nc["post_maybe"] else ("strong",)
    rows = db.execute(
        f"SELECT id, first_at FROM stories WHERE posted_at IS NULL AND dismissed = 0 AND rating IN "
        f"({','.join('?' * len(ok))}) AND last_at > ? "
        "ORDER BY show != '' DESC, rating = 'strong' DESC, last_at DESC LIMIT ?",
        (*ok, iso(now() - timedelta(days=2)), nc["max_posts"])).fetchall()
    return [r["id"] for r in sorted(rows, key=lambda r: r["first_at"])]


def unposted(cfg: dict) -> list[dict]:
    db = connect(cfg)
    try:
        ids = to_post(cfg, db)
    finally:
        db.close()
    return stories(cfg, ids=ids) if ids else []


def stories(cfg: dict, days: float = 3, ids: list[int] | None = None, everything: bool = False) -> list[dict]:
    """Recent stories (or these), newest first, each with its sources."""
    db = connect(cfg)
    try:
        if ids:
            rows = db.execute(f"SELECT * FROM stories WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        else:
            rows = db.execute("SELECT * FROM stories WHERE last_at > ?" + ("" if everything else " AND dismissed = 0")
                              + " ORDER BY last_at DESC", (iso(now() - timedelta(days=days)),)).fetchall()
        out = []
        for s in rows:
            items = db.execute("SELECT url, title, source, published FROM items WHERE story = ? "
                               "ORDER BY published", (s["id"],)).fetchall()
            out.append({**dict(s), "sources": [dict(x) for x in items]})
        return out
    finally:
        db.close()


def mark(cfg: dict, sid: int, **fields) -> None:
    allowed = {"posted_at", "message_id", "dismissed", "context_at"}
    if not fields or set(fields) - allowed:
        raise ClipperError(f"can't set {sorted(set(fields) - allowed)} on a story")
    db = connect(cfg)
    try:
        if not db.execute("SELECT 1 FROM stories WHERE id = ?", (sid,)).fetchone():
            raise ClipperError(f"no story {sid}")
        db.execute(f"UPDATE stories SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), sid))
        db.commit()
    finally:
        db.close()


def dismiss(cfg: dict, ids: list[int]) -> None:
    for sid in ids:
        mark(cfg, sid, dismissed=1)


def show_names(cfg: dict) -> dict[str, str]:
    from .config import for_show

    return {k: for_show(cfg, k)["meta"]["show_name"] or k for k in cfg.get("shows") or {}}


def post_text(cfg: dict, s: dict) -> str:
    """A story as a Discord message (under 2000 characters)."""
    who = " · ".join(x for x in (s["person"], show_names(cfg).get(s["show"], "")) if x)
    lines = [f"📰 **{s['headline']}**" + (" *(maybe)*" if s["rating"] == "maybe" else ""),
             *([who] if who else []), s["why"]]
    lines[-1] = lines[-1][:400]
    shown = 0
    for x in s["sources"]:  # whole links only (a Google News link alone can be 600 characters)
        line = f"- [{clean(x['title'], 110).replace(']', ')')}](<{x['url']}>) · {x['source'] or 'web'}"
        if shown == 6 or len("\n".join(lines)) + len(line) > 1900:
            break
        lines.append(line)
        shown += 1
    if len(s["sources"]) > shown:
        lines.append(f"- and {len(s['sources']) - shown} more (war room > News)")
    return "\n".join(lines)


# ---------- tonight's context ----------

CONTEXT_MAX = 1500  # one queued note, at most (the Discord preview shows all of it)


def context_text(s: dict) -> str:
    return f"In the news: {s['headline']}. {s['why']}".strip()


def context_block(found: list[dict]) -> tuple[str, list[int]]:
    """(text, story ids) for these stories as one note: whole stories while they fit in
    CONTEXT_MAX, so a preview of it shows exactly what Add queues."""
    parts, ids = [], []
    for s in found:
        t = context_text(s)
        if parts and len(" ".join([*parts, t])) > CONTEXT_MAX:
            continue
        parts.append(t[:CONTEXT_MAX])
        ids.append(s["id"])
    return " ".join(parts), ids


def story_show(cfg: dict, s: dict) -> str:
    """The show a story's context goes to: the judge's, else the person's first show."""
    if s["show"]:
        return s["show"]
    w = watchlist(cfg).get((s["person"] or "").lower())
    return w.shows[0] if w and w.shows else ""


def set_context(cfg: dict, ids: list[int], show: str = "", who: str = "") -> dict:
    """These stories as context for the show's next stream: waits in context.json until the
    worker takes it (apply_pending). Returns the entry."""
    found = stories(cfg, ids=ids)
    if len(found) != len(set(ids)):
        raise ClipperError(f"no story {sorted(set(ids) - {s['id'] for s in found})}")
    show = show or story_show(cfg, found[0])
    if show not in (cfg.get("shows") or {}):
        raise ClipperError(f"no show for story {ids[0]}: give one with --show ({', '.join(cfg.get('shows') or {})})")
    found.sort(key=lambda s: ids.index(s["id"]))
    text, used = context_block(found)
    entry = {"show": show, "stories": used, "text": text, "at": iso(now()), "by": who, "used": ""}
    with jsonstate.update(home(cfg) / "context.json", {}) as data:
        data.setdefault("pending", []).append(entry)
    for sid in used:
        mark(cfg, sid, context_at=entry["at"])
    return entry


def queued(cfg: dict, show: str) -> set[int]:
    """Stories waiting (or used) as context for this show."""
    data = jsonstate.read(Path(cfg["paths"]["news"]) / "context.json", {}) or {}
    return {sid for e in data.get("pending", []) if e["show"] == show for sid in e["stories"]}


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # a JSON string is a TOML basic string
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {toml_value(x)}" for k, x in v.items()) + " }"
    raise ClipperError(f"can't write {type(v).__name__} to TOML")


def write_toml(path: Path, data: dict) -> None:
    out = []
    for section, values in data.items():
        out.append(f"[{section}]")
        out += [f"{k} = {toml_value(v)}" for k, v in values.items()]
        out.append("")
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text("\n".join(out), encoding="utf-8")
    tmp.replace(path)


def add_stream_context(cfg: dict, sdir: Path, text: str) -> None:
    """Append text to the stream's own [scoring] stream_context (streams/<id>/config.toml,
    copied first to config.toml.before_news_<time>); starts from the show's when it has none."""
    from .config import STREAM_CONFIG, for_stream

    root = cfg.get("_root") or cfg
    if os.environ.get("CLIPPER_SCORING_STREAM_CONTEXT") is not None or \
            "stream_context" in ((root.get("_local") or {}).get("scoring") or {}):
        raise ClipperError("[scoring] stream_context is set in clipper.local.toml or $CLIPPER_SCORING_STREAM_CONTEXT, "
                           "which win over a stream's config.toml: take it out there, or the news note can't reach "
                           "scoring (it stays queued)")
    path = sdir / STREAM_CONFIG
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if path.exists():
        shutil.copy2(path, sdir / f"{STREAM_CONFIG}.before_news_{now().strftime('%Y%m%d_%H%M%S')}")
    sc = data.setdefault("scoring", {})
    base = sc.get("stream_context") or for_stream(cfg, sdir)["scoring"]["stream_context"]
    sc["stream_context"] = f"{base.rstrip()}\n{text}".strip()
    write_toml(path, data)


def apply_pending(cfg: dict, sdir: Path, say=print) -> int:
    """The worker, before a stream's scoring: context.json entries for its show go into its
    config.toml, once: notes added up to 12 h after the stream started (so not an older stream
    being redone), however long before. A note no stream took in [news] context_days is
    dropped. Returns how many were added."""
    from .config import show_of_channel, stream_channel

    path = Path(cfg["paths"]["news"]) / "context.json"
    if not path.exists():
        return 0
    meta = jsonstate.read(sdir / "meta.json", {}) or {}
    show = show_of_channel(cfg, stream_channel(sdir))
    try:
        start = datetime.fromisoformat(meta["started_at"])
    except (KeyError, TypeError, ValueError):
        return 0
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    stale = now() - timedelta(days=cfg["news"]["context_days"])
    with jsonstate.update(path, {}) as data:
        for e in data.get("pending", []):
            if not e["used"] and datetime.fromisoformat(e["at"]) < stale:
                e["used"] = "expired"
        due = [e for e in data.get("pending", []) if e["show"] == show and not e["used"]
               and datetime.fromisoformat(e["at"]) <= start + timedelta(hours=12)]
        if due:
            add_stream_context(cfg, sdir, "\n".join(e["text"] for e in due))
            for e in due:
                e["used"] = sdir.name
    if due:
        say(f"news: {len(due)} context note(s) added to {sdir.name}'s stream_context")
    return len(due)


# ---------- the pre-show suggestion ----------

_SLOTS: dict = {}


def show_slots(cfg: dict, days: int = 21) -> dict[str, list[tuple[int, int]]]:
    """{show: [(weekday, minute of day)]}: when each show went live in the last few weeks
    ([schedule] timezone, rounded to the half hour). Kept for 6 hours."""
    from .config import show_of_channel

    if _SLOTS.get("at") and time.monotonic() - _SLOTS["at"] < 6 * 3600:
        return _SLOTS["slots"]
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    cut, slots = time.time() - days * 86400, {}
    with os.scandir(cfg["paths"]["streams"]) as it:
        recent = [e for e in it if e.name.startswith("kick-") and e.is_dir() and e.stat().st_mtime > cut]
    for e in recent:
        meta = jsonstate.read(Path(e.path) / "meta.json", {}) or {}
        show = show_of_channel(cfg, meta.get("channel", ""))
        try:
            at = datetime.fromisoformat(meta["started_at"]).astimezone(tz)
        except (KeyError, TypeError, ValueError):
            continue
        if not show or datetime.now(tz) - at > timedelta(days=days):
            continue
        at = at.replace(second=0, microsecond=0)
        at += timedelta(minutes=round(at.minute / 30) * 30 - at.minute)  # 23:50 -> the next day's 00:00
        slot = (at.weekday(), at.hour * 60 + at.minute)
        if slot not in slots.setdefault(show, []):
            slots[show].append(slot)
    _SLOTS.update(at=time.monotonic(), slots=slots)
    return slots


def next_start(slots: list[tuple[int, int]], at: datetime) -> datetime | None:
    """The first of these weekly slots after `at` (an aware datetime in the show's zone)."""
    best = None
    for wd, minute in slots:
        day = at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=(wd - at.weekday()) % 7)
        t = day + timedelta(minutes=minute)
        if t <= at:
            t += timedelta(days=7)
        best = t if best is None or t < best else best
    return best


def due_suggestions(cfg: dict, at: datetime | None = None) -> list[dict]:
    """Shows starting within [news] preshow_minutes with stories about their people from the last
    day not yet queued for that show, once per show and day (a show that started at 18:00 one
    week and 18:30 another gets one): [{slot, show, show_name, start, stories, headlines, text}]."""
    nc = cfg["news"]
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    at = (at or now()).astimezone(tz)
    watch = watchlist(cfg)
    names = show_names(cfg)
    db = connect(cfg)
    try:
        done = {r["slot"] for r in db.execute("SELECT slot FROM suggested").fetchall()}
    finally:
        db.close()
    out = []
    for show, slots in show_slots(cfg).items():
        start = next_start(slots, at)
        if not start or start - at > timedelta(minutes=nc["preshow_minutes"]):
            continue
        slot = f"{show}@{start.date().isoformat()}"
        if slot in done:
            continue
        people = {w.name for w in watch.values() if show in w.shows}
        taken = queued(cfg, show)
        picked = [s for s in stories(cfg, days=1) if s["rating"] in ("strong", "maybe") and s["id"] not in taken
                  and (s["show"] == show or s["person"] in people)]
        text, ids = context_block(picked)
        if ids:
            heads = {s["id"]: s["headline"] for s in picked}
            out.append({"slot": slot, "show": show, "show_name": names.get(show, show), "start": iso(start),
                        "stories": ids, "headlines": [heads[i] for i in ids], "text": text})
    return out


def mark_suggested(cfg: dict, slot: str, show: str = "", ids: list[int] | None = None) -> int:
    """Record a suggestion; returns its number (the Discord button's short reference)."""
    db = connect(cfg)
    try:
        sid = db.execute("INSERT OR REPLACE INTO suggested (slot, at, show, stories) VALUES (?, ?, ?, ?)",
                         (slot, iso(now()), show, json.dumps(ids or []))).lastrowid
        db.commit()
        return sid
    finally:
        db.close()


def suggestion(cfg: dict, sid: int) -> tuple[str, list[int]]:
    """(show, story ids) of suggestion sid."""
    db = connect(cfg)
    try:
        r = db.execute("SELECT show, stories FROM suggested WHERE id = ?", (sid,)).fetchone()
    finally:
        db.close()
    if not r:
        raise ClipperError(f"no suggestion {sid}")
    return r["show"], json.loads(r["stories"] or "[]")


# ---------- the command line ----------

def search(cfg: dict, query: str, days: int = 7) -> list[dict]:
    """Google News for query now (not stored), then the stored headlines that mention it."""
    q = f"{query} when:{max(1, days)}d"
    body, _, _ = fetch(GOOGLE.format(q=quote(q)))
    found = parse_feed(body or b"<rss/>", "Google News")
    db = connect(cfg)
    try:
        like = f"%{query.strip('\"')}%"
        stored = db.execute("SELECT url, title, source, published FROM items WHERE (title LIKE ? OR snippet LIKE ?) "
                            "AND seen_at > ? ORDER BY published DESC LIMIT 50",
                            (like, like, iso(now() - timedelta(days=days)))).fetchall()
    finally:
        db.close()
    seen = {title_key(x["title"]) for x in found}
    return found + [dict(r) for r in stored if title_key(r["title"]) not in seen]


def feeds(cfg: dict) -> list[dict]:
    db = connect(cfg)
    try:
        return [dict(r) for r in db.execute("SELECT name, url, fetched_at, items, error FROM feeds ORDER BY name")]
    finally:
        db.close()


def show_list(cfg: dict, days: float, person: str = "", everything: bool = False) -> None:
    names = show_names(cfg)
    for s in stories(cfg, days, everything=everything):
        if person and person.lower() not in (s["person"] or "").lower():
            continue
        flags = "".join([" dismissed" if s["dismissed"] else "", " posted" if s["posted_at"] else "",
                         " context" if s["context_at"] else ""])
        print(f"#{s['id']} [{s['rating']}] {s['headline']} ({s['person'] or '?'}"
              f"{', ' + names.get(s['show'], s['show']) if s['show'] else ''}){flags}")
        print(f"    {s['why']}")
        for x in s["sources"][:4]:
            print(f"    {x['published'][:16]} {x['source']}: {x['url']}")
