"""Lolcow Cinema Weekly: the week's streams -> a ten-page tabloid issue (issue #35).

  clipper weekly                          this week (Sun-Sat, [schedule] timezone): gather,
                                          draft, render -> <paths.weekly>/<saturday>/
  clipper weekly --week 2026-10-24        any day of the week wanted
  clipper weekly --headline "Ethan Klein" editor's note for the draft: the cover story
  clipper weekly --render                 only render issue.json again (after editing it)
  clipper weekly --draft                  draft again from week.json (overwrites issue.json)
  clipper weekly --gather                 only rebuild week.json and stats.json
  clipper weekly --only 01,07             render only these pages
  clipper weekly --write 04,05            write these articles again with the writer prompt
                                          (default after a fresh draft: all three)

Three idempotent steps in one folder:
- gather: every stream started in the week (shows and pre-shows) -> week.json (segments
  with their titles and summaries, compilations, uploads, Shorts, the hooks of the picks
  as a verbatim quote pool, which faces thumbnails.json found) and stats.json (hours live,
  chat, top emote, screen time per topic, word counts in the transcripts).
- draft: [weekly] model writes issue.json from week.json and prompts/weekly_<v>.md: every
  page's copy, who is pictured where (people the renderer can find a face for), the poll.
  Then the writer pass (write_articles): one call per article page (03 cover story, 04 and
  05 features) with prompts/weekly_article_<v>.md and the owner's own piece as the example
  (prompts/weekly_article_example.md) writes the deck, both columns and the pull quote to
  the word budget the page holds; a draft that would overflow is sent back once to be cut.
  Edit issue.json by hand and --render; a draft never overwrites an edited file unless
  --draft is given, and --write redoes only the articles named.
- render: 1080x1440 pages 01-10 (cover, contents, cover story, two features, news in brief,
  quote, numbers, marketplace, next week + poll), contact_sheet.jpg and post_text.md.

Faces come from the streams' thumbnails.json (the frame the thumbnail model picked and its
face box, by main_person), then [thumbnail] photos, else initials. The cover subject is cut
out with torchvision DeepLabV3 when torch is installed (the GPU machine); otherwise the
cover shows the frame in a rounded panel.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .signals import EMOTE

W, H, MX = 1080, 1440, 56
CW = W - 2 * MX
PAPER, INK, MUTED, CARD, RULE = (246, 243, 237), (18, 18, 18), (112, 108, 100), (236, 232, 224), (214, 208, 198)
RED, YELLOW, WHITE, GREY = (226, 30, 38), (255, 226, 0), (255, 255, 255), (170, 166, 158)
NAVY = (14, 26, 64)
PAGES = ["01_cover", "02_contents", "03_feature", "04_article", "05_article", "06_brief", "07_quote",
         "08_numbers", "09_market", "10_back"]
# Words each article layout holds (left column, right column beside the pull quote, deck
# characters): the cover story's 22 px layout and the two features' 25 px layout.
BUDGET = {"feature": (150, 110, 150), "article": (80, 52, 130)}  # 04/05 had a spare 100 px at 70/45
ARTICLE_PAGES = {3: ("feature", True), 4: ("articles", 0), 5: ("articles", 1)}
LAUGH = re.compile(r"\b(lol|lmao|lmfao|kek|rofl|haha+|omegalul)\b|😂|🤣", re.IGNORECASE)
FWORD = re.compile(r"\bf+u+c+k", re.IGNORECASE)
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
INITIAL_COLOURS = [(120, 60, 160), (30, 90, 160), (14, 120, 60), (200, 120, 20), (160, 40, 40), (40, 110, 110)]


# ---------- week ----------

def week_of(cfg: dict, day: str | None) -> tuple[datetime, datetime]:
    """[start, end) of the Sun-Sat week holding `day` (default today), in [schedule] timezone."""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz) if day else datetime.now(tz)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    start = d - timedelta(days=(d.weekday() + 1) % 7)   # back to Sunday
    return start, start + timedelta(days=7)


def issue_dir(cfg: dict, start: datetime) -> Path:
    return Path(cfg["paths"]["weekly"]) / (start + timedelta(days=6)).strftime("%Y-%m-%d")


def week_label(start: datetime) -> str:
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b').upper()} {start.day}-{end.day}, {end.year}"
    return f"{start.strftime('%b').upper()} {start.day} - {end.strftime('%b').upper()} {end.day}, {end.year}"


def next_issue_number(cfg: dict, here: Path) -> int:
    nums = []
    for p in Path(cfg["paths"]["weekly"]).glob("*/issue.json"):
        if p.parent != here:
            try:
                nums.append(int(json.loads(p.read_text(encoding="utf-8")).get("issue") or 0))
            except (OSError, ValueError):
                pass
    return max(nums + [cfg["weekly"]["first_issue"] - 1]) + 1


# ---------- gather ----------

def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def chat_stats(sdir: Path, meta: dict, hours: float) -> dict:
    p = sdir / "chat.jsonl"
    if not p.exists():
        return {}
    t0 = datetime.fromisoformat(meta["started_at"]).timestamp()
    n, per_min, emotes, laughs, users = 0, Counter(), Counter(), 0, set()
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("type", "message") != "message":
                continue
            t = m.get("t", m.get("ts", t0) - t0)
            if not 0 <= t <= hours * 3600 + 600:
                continue
            n += 1
            per_min[int(t // 60)] += 1
            users.add(m.get("user"))
            c = m.get("content") or ""
            emotes.update(EMOTE.findall(c))
            if LAUGH.search(EMOTE.sub("", c)):
                laughs += 1
    peak_min, peak = max(per_min.items(), key=lambda kv: kv[1]) if per_min else (0, 0)
    return {"messages": n, "chatters": len(users), "laughs": laughs, "peak_per_min": peak,
            "peak_at_s": peak_min * 60, "emotes": dict(emotes.most_common(8))}


def word_counts(sdir: Path, words: list[str]) -> tuple[dict, int]:
    p = sdir / "words.jsonl"
    if not p.exists():
        return {}, 0
    text = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                text.append(json.loads(line)["word"])
            except (ValueError, KeyError):
                pass
    joined = " ".join(text).lower()
    counts = {w: len(re.findall(r"\b" + re.escape(w.lower()) + r"\b", joined)) for w in words}
    counts["_fuck"] = len(FWORD.findall(joined))
    return counts, len(text)


def faces_of(sdir: Path) -> list[dict]:
    """Every (person, frame, face box) the thumbnail stage recorded, largest faces first."""
    t = _load(sdir / "thumbnails.json", {}) or {}
    out = []
    for key, e in t.items():
        person, face = e.get("main_person"), e.get("face")
        frames = e.get("frames") or []
        if not (person and face and frames) or e.get("frame") is None:
            continue
        try:
            path = sdir / "thumbs" / key / frames[e["frame"]]["path"]
        except (IndexError, KeyError, TypeError):
            continue
        if face["width"] > 0.36 or face["height"] > 0.55:
            continue  # a whole video region, not a face
        out.append({"person": person, "key": key, "frame": str(path), "face": face,
                    "size": round(face["height"], 3), "photo": e.get("photo") or ""})
    out.sort(key=lambda f: -f["size"])
    return out


def gather(cfg: dict, start: datetime, end: datetime, here: Path) -> tuple[dict, dict]:
    root = Path(cfg["paths"]["streams"])
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    wc = cfg["weekly"]
    streams: dict[str, dict] = {}
    for sdir in sorted(root.iterdir()):
        meta = _load(sdir / "meta.json")
        if not meta or not meta.get("started_at"):
            continue
        at = datetime.fromisoformat(meta["started_at"]).astimezone(tz)
        if not start <= at < end:
            continue
        seg = _load(sdir / "segments.json", {}) or {}
        review = _load(sdir / "review.json", {}) or {}
        cands = _load(sdir / "candidates.json", {}) or {}
        hours = (meta.get("duration_s") or 0) / 3600
        preshow = sdir.name.startswith("yt-")
        show = next((k for k, s in cfg["shows"].items() if (meta.get("channel") or "").lower() in s["channels"]), "")
        show_name = cfg["shows"][show]["overrides"].get("meta", {}).get("show_name", show) if show else meta.get("channel", "")
        segments = []
        for x in seg.get("segments", []):
            if preshow and len(seg.get("segments", [])) == 1:
                x = {**x, "topic": "Pre-show"}
            segments.append({"id": x["id"], "topic": x.get("topic") or "", "title": x.get("title", ""),
                             "summary": x.get("summary", ""), "figures": x.get("figures", []),
                             "kind": x.get("kind", ""), "duration_s": round(x.get("duration_s", 0), 1),
                             "cut": x.get("cut", True)})
        uploads = {k: v["fields"].get("YouTube URL") for k, v in review.items() if v.get("fields", {}).get("YouTube URL")}
        hooks = [{"id": p["id"], "hook": p.get("hook", ""), "title": p.get("title", "")}
                 for p in (cands.get("pass_b") or {}).get("picks", []) if p.get("hook")]
        shorts = [k for k, v in review.items() if v.get("kind") == "short"]
        counts, n_words = word_counts(sdir, wc["words"])
        streams[sdir.name] = {
            "show": show, "show_name": show_name, "channel": meta.get("channel"),
            "preshow": preshow, "started_at": at.isoformat(), "day": DAYS[at.weekday()],
            "date": at.strftime("%b %d"), "hours": round(hours, 2), "title": meta.get("title", ""),
            "segments": segments, "compilations": [{"topic": c.get("topic"), "title": c.get("title")}
                                                    for c in seg.get("compilations", [])],
            "shorts": len(shorts), "uploaded": len(uploads), "hooks": hooks,
            "chat": chat_stats(sdir, meta, hours) if not preshow else {},
            "words": counts, "spoken_words": n_words, "faces": faces_of(sdir),
        }
        print(f"  {sdir.name}: {show_name} {at:%a %b %d}, {hours:.1f} h, {len(segments)} segments, "
              f"{len(hooks)} hooks, {len(streams[sdir.name]['faces'])} faces")
    if not streams:
        raise SystemExit(f"no streams started {start:%Y-%m-%d} to {end:%Y-%m-%d} under {root}")
    week = {"start": start.strftime("%Y-%m-%d"), "end": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
            "label": week_label(start), "streams": streams}
    return week, stats_of(week)


def stats_of(week: dict) -> dict:
    live = {k: s for k, s in week["streams"].items() if not s["preshow"]}
    pre = {k: s for k, s in week["streams"].items() if s["preshow"]}
    minutes: Counter = Counter()
    for s in live.values():
        for x in s["segments"]:
            if x["kind"] != "intro" and x["topic"]:
                minutes[x["topic"]] += x["duration_s"] / 60
    words: Counter = Counter()
    for s in week["streams"].values():
        words.update(s["words"])
    emotes: Counter = Counter()
    chat_n = laughs = chatters = 0
    peak = (0, "", 0)
    for k, s in live.items():
        c = s.get("chat") or {}
        chat_n += c.get("messages", 0)
        laughs += c.get("laughs", 0)
        chatters += c.get("chatters", 0)
        emotes.update(c.get("emotes", {}))
        if c.get("peak_per_min", 0) > peak[0]:
            peak = (c["peak_per_min"], k, c.get("peak_at_s", 0))
    hours = sum(s["hours"] for s in live.values())
    fucks = words.pop("_fuck", 0)
    spoken = sum(s["spoken_words"] for s in week["streams"].values())
    return {
        "shows": len(live), "preshows": len(pre), "hours": round(hours, 2),
        "hours_with_preshows": round(hours + sum(s["hours"] for s in pre.values()), 2),
        "segments": sum(len(s["segments"]) for s in live.values()),
        "shorts": sum(s["shorts"] for s in week["streams"].values()),
        "chat_messages": chat_n, "chatters": chatters, "laughs": laughs,
        "peak_per_min": peak[0], "peak_stream": peak[1], "peak_at_s": peak[2],
        "top_emotes": emotes.most_common(5),
        "screen_minutes": {t: round(m, 1) for t, m in minutes.most_common()},
        "spoken_words": spoken, "fucks": fucks,
        "fuck_every_s": round(hours * 3600 / fucks) if fucks else 0,
        "said": {w: n for w, n in words.most_common()},
    }


# ---------- draft ----------

def json_text(claude, model: str, system: str, user: str, effort: str) -> tuple[dict, dict]:
    """A streamed JSON answer without structured output: a field-by-field schema of the
    issue is too large a grammar for the API, and a free object isn't allowed, so the prompt
    describes the fields and check_issue() looks for them."""
    with claude.client.messages.stream(
            model=model, max_tokens=32000, system=system, output_config={"effort": effort},
            messages=[{"role": "user", "content": user + "\n\nAnswer with the issue as one JSON object and nothing else."}]) as st:
        resp = st.get_final_message()
    meta = {"model": resp.model, "request_id": getattr(resp, "_request_id", None),
            "stop_reason": resp.stop_reason, **claude._account(model, resp.usage)}
    if resp.stop_reason == "refusal":
        return None, meta
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    i, j = text.find("{"), text.rfind("}")
    return json.loads(text[i:j + 1]), meta


REQUIRED = {
    "cover": ["person", "kicker", "kicker_hot", "kicker_line2", "headline", "bullets", "inset_person",
              "inset_name", "inset_role", "teasers", "burst"],
    "contents": ["rows"], "brief": ["strap", "cards"], "quote": ["text", "person", "name", "where", "runners_up"],
    "numbers": ["strap", "cast", "lolcow", "lolcow_line", "cards"],
    "market": ["strap", "big_name", "big_body", "big_tag", "big_price", "ads"],
    "back": ["strap", "poll", "options", "note"], "post": ["part1", "part2"],
}
ARTICLE = ["section", "kicker", "topic", "person", "watch", "headline", "highlight", "deck", "col1", "quote", "quote_by", "col2"]


def check_issue(issue: dict) -> list[str]:
    missing = [f"{page}.{k}" for page, keys in REQUIRED.items() for k in keys if k not in (issue.get(page) or {})]
    for name, a in [("feature", issue.get("feature") or {})] + [(f"articles[{i}]", a) for i, a in enumerate(issue.get("articles") or [])]:
        missing += [f"{name}.{k}" for k in ARTICLE if k not in a]
    if len(issue.get("articles") or []) < 2:
        missing.append("articles (needs 2)")
    return missing


def hm(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def draft_user(week: dict, stats: dict, headline: str, issue: int, photos: dict[str, Path]) -> str:
    lines = [f"Issue {issue:02d}, week of {week['label']}.",
             f"Editor's note: {headline}" if headline else "Editor's note: none; pick the biggest story.", ""]
    for sid, s in week["streams"].items():
        kind = "pre-show" if s["preshow"] else "show"
        lines.append(f"## {s['show_name']} {kind}, {s['day']} {s['date']} ({sid}), {s['hours']:.1f} h")
        for x in s["segments"]:
            if x["kind"] == "intro" and not s["preshow"]:
                continue
            lines.append(f"- {x['id']} [{x['topic']}] {round(x['duration_s'] / 60)} min: {x['title']}. {x['summary']}")
        if s["hooks"]:
            lines.append("  Verbatim lines from this stream (the quote pool; quote these and nothing else):")
            lines += [f"  - \"{h['hook']}\" ({h['id']}: {h['title']})" for h in s["hooks"]]
        lines.append("")
    lines.append("## Numbers (computed; use them as they are)")
    lines.append(f"- {stats['shows']} shows and {stats['preshows']} pre-shows, {stats['hours']:.1f} h live "
                 f"({stats['hours_with_preshows']:.1f} h with pre-shows), {stats['segments']} segments, {stats['shorts']} Shorts")
    lines.append(f"- chat: {stats['chat_messages']:,} messages from {stats['chatters']:,} chatters, "
                 f"{stats['laughs']:,} laughing messages, peak {stats['peak_per_min']} in one minute ({stats['peak_stream']})")
    lines.append(f"- top emotes: {', '.join(f'{e} ({n:,})' for e, n in stats['top_emotes'])}")
    lines.append(f"- {stats['fucks']:,} f-words on air, one every {stats['fuck_every_s']} s")
    lines.append("- screen time per topic (segment minutes): " + ", ".join(f"{t} {hm(m)}" for t, m in list(stats["screen_minutes"].items())[:16]))
    lines.append("- times a word was said on air: " + ", ".join(f"{w} {n}" for w, n in stats["said"].items() if n))
    lines.append("")
    people = Counter()
    for s in week["streams"].values():
        for f in s["faces"]:
            people[f["person"]] += 1
    lines.append("## Faces available (person: frames found). Use these names exactly for `person` fields; "
                 "anyone else gets initials.")
    lines.append(", ".join(f"{p} ({n})" for p, n in people.most_common()))
    if photos:
        lines.append("Portrait photos on file (also usable): " + ", ".join(sorted(photos)))
    return "\n".join(lines)


def draft(cfg: dict, here: Path, week: dict, stats: dict, headline: str) -> dict:
    from .scoring import Claude, preflight
    from .thumbs import photos as photo_dir

    wc = cfg["weekly"]
    prompt = Path(cfg["paths"]["prompts"]) / f"weekly_{wc['prompt_version']}.md"
    system = prompt.read_text(encoding="utf-8")
    style = cfg["scoring"].get("house_style") or []
    if style:
        system = system.rstrip() + "\n\nHouse style for everything on the page:\n" + "\n".join(f"- {r}" for r in style) + "\n"
    issue = next_issue_number(cfg, here)
    user = draft_user(week, stats, headline, issue, photo_dir(cfg["thumbnail"]))
    claude = Claude()
    preflight(claude, wc["model"])
    print(f"  drafting issue {issue:02d} with {wc['model']} ({len(user) // 4:,} tokens of week)...")
    out, meta = json_text(claude, wc["model"], system, user, wc["effort"])
    if out is None:
        raise SystemExit(f"the model refused to draft the issue ({meta})")
    if missing := check_issue(out):
        print(f"  ! the draft is missing {', '.join(missing)}; edit issue.json", file=sys.stderr)
    out = {"issue": issue, "week": week["label"], "start": week["start"], "end": week["end"],
           "headline": headline, "model": wc["model"], "prompt_version": wc["prompt_version"],
           "api": meta, "cost_usd": round(claude.cost, 4), **out}
    print(f"  draft done: ${claude.cost:.2f}")
    return out


# ---------- the writer pass: one call per article ----------

def _article_of(issue: dict, page: int) -> dict:
    key, which = ARTICLE_PAGES[page]
    return issue[key] if which is True else issue[key][which]


def article_user(week: dict, a: dict, page: int, budget: tuple, note: str = "") -> str:
    """Everything the writer needs for one article: the laid-out headline, the week's segments
    about its subject (full summaries), the rest of the week in one line each, and the quote pool."""
    subject = {a.get("topic", ""), a.get("person", "")} - {""}
    words = {w.lower() for t in subject for w in re.findall(r"[A-Za-z0-9']+", t) if len(w) > 2}
    head = " / ".join(a.get("headline", []))
    lines = [(f"Article for page {page}: section {a.get('section')}, headline \"{head}\", "
              f"subject {a.get('topic')}, pictured {a.get('person')}, aired {a.get('watch')}."),
             f"Budget: left column {budget[0]} words, right column {budget[1]} words, deck under {budget[2]} characters."]
    if note:
        lines.append(f"Editor's note: {note}")
    lines.append("")
    lines.append("## Segments about the subject (full summaries; the facts come from here)")
    rest, pool = [], []
    for st in week["streams"].values():
        for x in st["segments"]:
            if x["kind"] == "intro" and not st["preshow"]:
                continue
            hay = f"{x['topic']} {' '.join(x.get('figures', []))} {x['title']}".lower()
            hay_words = set(re.findall(r"[a-z0-9']+", hay))
            about = any(t.lower() in hay for t in subject) or (words and len(words & hay_words) >= max(1, len(words) - 1))
            line = f"- {st['show_name']}, {st['day']} {st['date']}, {x['id']} [{x['topic']}] {round(x['duration_s'] / 60)} min: {x['title']}."
            if about:
                lines.append(f"{line} {x['summary']}")
            else:
                rest.append(line)
        for h in st["hooks"]:
            pool.append(f"- \"{h['hook']}\" ({st['show_name']}, {st['day']} {st['date']}: {h['title']})")
    lines += ["", "## The rest of the week (titles only, for context)"] + rest[:60]
    lines += ["", "## Quote pool (the only lines you may quote, verbatim)"] + pool
    return "\n".join(lines)


def write_articles(cfg: dict, here: Path, week: dict, stats: dict, issue: dict, pages: list[int],
                   note: str = "") -> dict:
    """Rewrite the deck, columns and pull quote of these article pages with the writer prompt,
    one call each; a page that would overflow is sent back once with how much to cut."""
    from .scoring import Claude, preflight

    wc = cfg["weekly"]
    pdir = Path(cfg["paths"]["prompts"])
    template = (pdir / f"weekly_article_{wc['article_prompt_version']}.md").read_text(encoding="utf-8")
    example = (pdir / wc["article_example"]).read_text(encoding="utf-8")
    style = cfg["scoring"].get("house_style") or []
    claude = Claude()
    preflight(claude, wc["model"])
    for page in pages:
        a = _article_of(issue, page)
        kind = "feature" if page == 3 else "article"
        budget = BUDGET[kind]
        system = template.format(example=example, col1_words=budget[0], col2_words=budget[1], deck_chars=budget[2])
        if style:
            system = system.rstrip() + "\n\nHouse style:\n" + "\n".join(f"- {r}" for r in style) + "\n"
        user = article_user(week, a, page, budget, note)
        for attempt in range(2):
            again = " again, shorter" if attempt else ""
            print(f"  writing page {page:02d} ({a.get('topic')}) with {wc['model']}{again}...")
            out, meta = json_text(claude, wc["model"], system, user, wc["effort"])
            if out is None:
                print(f"  ! page {page:02d}: the model refused ({meta.get('refusal')}); kept as it was", file=sys.stderr)
                break
            draft = {k: out.get(k, a.get(k, "")) for k in ("deck", "col1", "quote", "quote_by", "col2")}
            probe = Renderer(cfg, here, week, stats, issue)
            probe.p_article(page, {**a, **draft}, feature=(page == 3))
            over = next((int(m.group(1)) for w in probe.warnings for m in [re.search(r"runs (\d+)px", w)] if m), 0)
            if over and attempt == 0:
                cut = over // (28 if page == 3 else 34) * 8 + 10
                user += (f"\n\nYour last draft ran {over} px past the page. Cut about {cut} words in all, "
                         "from whichever column is longer, keeping the last line.")
                continue
            a.update(draft)
            a["written_by"] = {"model": wc["model"], "prompt_version": wc["article_prompt_version"], **meta}
            if over:
                print(f"  ! page {page:02d}: still {over} px over after a retry; trim issue.json by hand", file=sys.stderr)
            break
    issue["articles_cost_usd"] = round(issue.get("articles_cost_usd", 0) + claude.cost, 4)
    print(f"  articles written: ${claude.cost:.2f}")
    return issue


# ---------- faces ----------

class Faces:
    """person -> a square face crop, a frame, or initials; from the week's thumbnails.json."""

    def __init__(self, cfg: dict, week: dict, pinned: dict | None = None, here: Path | None = None):
        from .thumbs import photos as photo_dir

        self.by_person: dict[str, list[dict]] = defaultdict(list)
        for s in week["streams"].values():
            for f in s["faces"]:
                self.by_person[f["person"].lower()].append(f)
        for v in self.by_person.values():
            v.sort(key=lambda f: -f["size"])
        self.photos = {k.lower(): v for k, v in photo_dir(cfg["thumbnail"]).items()}
        root = Path(cfg["paths"]["streams"])
        for person, pin in (pinned or {}).items():
            # issue.json "faces": {"Name": "kick-1/segments/s16_x"} picks that stream's thumbnail
            # frame; {"Name": {"frame": "kick-1/thumbs/.../05_1.0.jpg", "face": {x, y, width, height}}}
            # pins any frame with a box the crops centre on, and "bottom" (0-1) where the cut-out
            # stops, to keep a caption out of it.
            if isinstance(pin, dict) and pin.get("photo"):
                # {"Name": {"photo": "hila_klein.png"}}: a portrait in the issue folder, the whole
                # picture is the face (as [thumbnail] photos)
                photo = Path(pin["photo"])
                self.photos[person.lower()] = photo if photo.is_absolute() else (here or Path()) / photo
                self.by_person.pop(person.lower(), None)
                continue
            if isinstance(pin, str):
                sid, _, key = pin.partition("/")
                hit = next((f for f in week["streams"].get(sid, {}).get("faces", []) if f["key"] == key), None)
                if not hit:
                    raise SystemExit(f"issue.json faces: no thumbnail {key!r} in {sid}")
                pin = {"frame": hit["frame"], "face": hit["face"]}
            frame = Path(pin["frame"])
            if not frame.is_absolute():
                frame = root / frame
            self.by_person[person.lower()].insert(0, {"person": person, "key": "pinned", "frame": str(frame),
                                                      "face": pin["face"], "size": pin["face"]["height"],
                                                      "bottom": pin.get("bottom", 1.0)})  # cut-out stops here (a caption below)
        self.colours: dict[str, tuple] = {}
        self.used: dict[str, int] = Counter()

    def best(self, person: str, nth: int = 0) -> dict | None:
        fs = self.by_person.get((person or "").lower())
        if not fs:
            return None
        return fs[min(nth, len(fs) - 1)]

    def square(self, person: str, D: int, nth: int | None = None) -> Image.Image:
        """A DxD face crop (face a little above centre), the photo on file, or initials."""
        key = (person or "").lower()
        fs = self.by_person.get(key) or []
        pinned = bool(fs) and fs[0]["key"] == "pinned"
        f = self.best(person, 0 if pinned else (self.used[key] if nth is None else nth))
        if f and Path(f["frame"]).exists():
            self.used[key] += 1
            im = Image.open(f["frame"]).convert("RGB")
            b = f["face"]
            w, h = im.size
            cx, cy = (b["x"] + b["width"] / 2) * w, (b["y"] + b["height"] / 2) * h
            r = max(b["width"] * w, b["height"] * h) * 0.68
            r = min(r, cx, cy, w - cx, h - cy) if r > min(cx, cy, w - cx, h - cy) else r
            crop = im.crop((int(cx - r), int(cy - r * 1.05), int(cx + r), int(cy + r * 0.95)))
            return ImageOps.fit(crop, (D, D), Image.LANCZOS)
        if key in self.photos:
            im = Image.open(self.photos[key]).convert("RGB")
            r = int(im.width * 0.42)
            cx, cy = im.width // 2, int(im.height * 0.45)
            return im.crop((cx - r, cy - r, cx + r, cy + r)).resize((D, D), Image.LANCZOS)
        return self.initials(person, D)

    def initials(self, person: str, D: int) -> Image.Image:
        col = self.colours.setdefault(person, INITIAL_COLOURS[len(self.colours) % len(INITIAL_COLOURS)])
        txt = "".join(p[0] for p in (person or "?").split()[:2]).upper() or "?"
        img = Image.new("RGB", (D, D), col)
        ImageDraw.Draw(img).text((D // 2, D // 2), txt, font=self.fonts.bahn(int(D * 0.46)), fill=WHITE, anchor="mm")
        return img

    def avatar(self, person: str, D: int, ring=None) -> Image.Image:
        img = self.square(person, D)
        m = Image.new("L", (D, D), 0)
        ImageDraw.Draw(m).ellipse([0, 0, D - 1, D - 1], fill=255)
        out = Image.new("RGBA", (D + 8, D + 8), (0, 0, 0, 0))
        if ring:
            ImageDraw.Draw(out).ellipse([0, 0, D + 7, D + 7], fill=ring)
        out.paste(img, (4, 4), m)
        return out

    def frame(self, person: str) -> Image.Image | None:
        f = self.best(person)
        if f and Path(f["frame"]).exists():
            return Image.open(f["frame"]).convert("RGB")
        return None

    def scene(self, person: str, size: tuple[int, int], centering=(0.5, 0.4)) -> Image.Image:
        """A wide photo around the person's face for article headers."""
        f = self.best(person)
        if not f and (person or "").lower() in self.photos:
            return ImageOps.fit(Image.open(self.photos[person.lower()]).convert("RGB"), size, Image.LANCZOS,
                                centering=(0.5, 0.3))
        if not (f and Path(f["frame"]).exists()):
            im = Image.new("RGB", size, CARD)
            av = self.avatar(person, min(size) - 80)
            im.paste(av, ((size[0] - av.width) // 2, (size[1] - av.height) // 2), av)
            return im
        im = Image.open(f["frame"]).convert("RGB")
        b = f["face"]
        w, h = im.size
        cx, cy = (b["x"] + b["width"] / 2) * w, (b["y"] + b["height"] / 2) * h
        fh = b["height"] * h
        hb = h * f.get("bottom", 1.0)  # a pinned frame may end above a burned-in caption
        ch = min(hb, max(fh * 3.2, size[1] * 0.6))
        cw = min(w, ch * size[0] / size[1])
        ch = cw * size[1] / size[0]
        x0 = min(max(cx - cw / 2, 0), w - cw)
        y0 = min(max(cy - ch * 0.42, 0), hb - ch)
        return im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize(size, Image.LANCZOS)

    def cutout(self, person: str, here: Path) -> Image.Image | None:
        """RGBA cut-out of the person (DeepLabV3, cached in the issue folder); None without torch."""
        f = self.best(person)
        if not (f and Path(f["frame"]).exists()):
            return None
        cache = here / f"cutout_{re.sub(r'[^a-z0-9]+', '-', person.lower())}_{Path(f['frame']).stem}.png"
        if cache.exists():
            return Image.open(cache).convert("RGBA")
        try:
            import numpy as np
            import torch
            from torchvision.models.segmentation import (
                DeepLabV3_ResNet101_Weights,
                deeplabv3_resnet101,
            )
        except ImportError:
            return None
        im = Image.open(f["frame"]).convert("RGB")
        w, h = im.size
        b = f["face"]
        fw, fh = b["width"] * w, b["height"] * h
        cx, top = (b["x"] + b["width"] / 2) * w, b["y"] * h
        box = (int(max(0, cx - fw * 1.8)), int(max(0, top - fh * 0.5)),
               int(min(w, cx + fw * 1.8)), int(min(h * f.get("bottom", 1.0), top + fh * 4.2)))
        crop = im.crop(box)
        weights = DeepLabV3_ResNet101_Weights.DEFAULT
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        model = deeplabv3_resnet101(weights=weights).eval().to(dev)
        x = weights.transforms()(crop).unsqueeze(0).to(dev)
        with torch.no_grad():
            logits = model(x)["out"][0]
        prob = torch.softmax(logits, 0)[15]  # VOC class 15: person
        prob = torch.nn.functional.interpolate(prob[None, None], size=crop.size[::-1], mode="bilinear")[0, 0]
        mask = Image.fromarray((prob.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8))
        mask = mask.point(lambda v: 255 if v > 140 else (0 if v < 90 else int((v - 90) * 255 / 50)))
        mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
        if np.asarray(mask).mean() < 8:  # nothing found
            return None
        crop.putalpha(mask)
        crop.save(cache)
        return crop


# ---------- fonts and drawing ----------

class Fonts:
    def __init__(self, wc: dict):
        self.paths = {}
        for key in ("headline", "condensed", "serif_italic", "serif_bold_italic", "black", "bold"):
            p = next((f for f in wc[f"{key}_fonts"] if Path(f).exists()), None)
            if not p:
                raise SystemExit(f"[weekly] {key}_fonts: none of {wc[f'{key}_fonts']} exist (clipper doctor)")
            self.paths[key] = p

    def impact(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["headline"], size)

    def bahn(self, size: int, style: str = "Bold Condensed") -> ImageFont.FreeTypeFont:
        f = ImageFont.truetype(self.paths["condensed"], size)
        try:
            f.set_variation_by_name(style)
        except (OSError, ValueError):
            pass
        return f

    def serif(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["serif_bold_italic" if bold else "serif_italic"], size)

    def black(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["black"], size)

    def bold(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.paths["bold"], size)


def wrap(d: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=f) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    return lines + [cur] if cur else lines


def para(d, xy, text, f, width, fill, lh) -> int:
    """Draw wrapped text; a blank line in the text is a paragraph break (half a line of air)."""
    x, y = xy
    for i, chunk in enumerate(t for t in re.split(r"\n\s*\n", text) if t.strip()):
        if i:
            y += lh // 2
        for line in wrap(d, chunk, f, width):
            d.text((x, y), line, font=f, fill=fill)
            y += lh
    return y


def fit(text: str, make, max_w: int, hi: int, max_h: int = 0) -> int:
    lo, best = 8, 8
    while lo <= hi:
        mid = (lo + hi) // 2
        b = make(mid).getbbox(text)
        if b[2] - b[0] <= max_w and (not max_h or b[3] - b[1] <= max_h):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def rounded(img: Image.Image, r: int) -> Image.Image:
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, img.width - 1, img.height - 1], r, fill=255)
    img = img.convert("RGBA")
    img.putalpha(m)
    return img


def sheared(img: Image.Image, k: float = 0.18) -> Image.Image:
    w, h = img.size
    extra = int(h * k)
    return img.transform((w + extra, h), Image.AFFINE, (1, k, -extra, 0, 1, 0), Image.BICUBIC)


def shadow_text(base, xy, text, f, fill, stroke=0, stroke_fill=INK, shadow=(5, 6), blur=3, anchor="la"):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((xy[0] + shadow[0], xy[1] + shadow[1]), text, font=f, fill=(0, 0, 0, 200),
           stroke_width=stroke, stroke_fill=(0, 0, 0, 200), anchor=anchor)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(layer)
    ImageDraw.Draw(base).text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill, anchor=anchor)


def rich_line(d, x, y, parts, f) -> float:
    for t, c in parts:
        d.text((x, y), t, font=f, fill=c)
        x += d.textlength(t, font=f)
    return x


def up(s: str) -> str:
    return (s or "").upper()


# ---------- renderer ----------

class Renderer:
    def __init__(self, cfg: dict, here: Path, week: dict, stats: dict, issue: dict):
        self.cfg, self.here, self.week, self.stats, self.issue = cfg, here, week, stats, issue
        self.wc = cfg["weekly"]
        self.F = Fonts(self.wc)
        self.faces = Faces(cfg, week, issue.get("faces"), here)
        self.faces.fonts = self.F
        self.ISSUE = f"ISSUE {int(issue.get('issue') or 1):02d}"
        self.WEEK = issue.get("week") or week["label"]
        self.TOTAL = len(PAGES)
        self.warnings: list[str] = []

    # chrome
    def page(self, dark=False) -> Image.Image:
        return Image.new("RGBA", (W, H), INK if dark else PAPER)

    def chrome(self, p, n, section, dark=False, cta="SWIPE"):
        F = self.F
        d = ImageDraw.Draw(p)
        fg, sub = (WHITE, GREY) if dark else (INK, MUTED)
        f = F.bahn(36)
        m1, m2 = self.wc["masthead"]
        d.text((MX, 38), m1, font=f, fill=RED)
        d.text((MX + d.textlength(m1 + " ", font=f), 38), f"{m2} WEEKLY", font=f, fill=fg)
        d.text((W - MX, 48), f"{section}  ·  {self.ISSUE}", font=F.bahn(22, "SemiBold"), fill=sub, anchor="ra")
        d.line([MX, 90, W - MX, 90], fill=fg, width=3)
        d.line([MX, H - 84, W - MX, H - 84], fill=RULE if not dark else (60, 60, 60), width=2)
        ff = F.bahn(26)
        d.text((MX, H - 66), f"{n:02d} / {self.TOTAL:02d}", font=ff, fill=fg)
        d.text((W // 2, H - 62), self.WEEK, font=F.bahn(22, "SemiBold"), fill=sub, anchor="ma")
        tw = d.textlength(cta, font=ff)
        d.text((W - MX - 44 - tw, H - 66), cta, font=ff, fill=fg)
        ax, ay = W - MX - 30, H - 52
        d.line([ax - 6, ay, ax + 26, ay], fill=RED, width=5)
        d.polygon([(ax + 30, ay), (ax + 16, ay - 10), (ax + 16, ay + 10)], fill=RED)
        return d

    def kicker_bar(self, d, x, y, parts, size=34, pad=14) -> int:
        f = self.F.bahn(size)
        w = sum(d.textlength(t, font=f) for t, _ in parts)
        d.rectangle([x, y, x + w + 2 * pad, y + size + 18], fill=RED)
        cx = x + pad
        for t, c in parts:
            d.text((cx, y + 7), t, font=f, fill=c)
            cx += d.textlength(t, font=f)
        return y + size + 18

    def title(self, d, text, y, size=132, fill=INK, max_w=CW) -> int:
        size = min(size, fit(text, self.F.impact, max_w, size))
        d.text((MX - 4, y), text, font=self.F.impact(size), fill=fill)
        return y + int(size * 1.08)

    def highlight_text(self, d, xy, parts, f):
        x, y = xy
        for t, hl in parts:
            w = d.textlength(t, font=f)
            if hl:
                b = f.getbbox(t)
                d.rectangle([x - 6, y + b[1] + 6, x + w + 6, y + b[3] + 4], fill=YELLOW)
            d.text((x, y), t, font=f, fill=INK)
            x += w

    def bullet(self, d, x, y, size=18):
        d.rectangle([x, y, x + size, y + size], fill=YELLOW, outline=INK, width=2)

    def tag(self, p, d, x, y, text):
        tf = self.F.bahn(24)
        tw = d.textlength(text, font=tf)
        d.rounded_rectangle([x, y, x + tw + 28, y + 40], 20, fill=RED)
        d.text((x + 14, y + 6), text, font=tf, fill=WHITE)

    def overflow(self, n: int, end: int, bottom: int = H - 84 - 8):
        """end: the y after the last text line (its line height included), vs the footer rule."""
        if end - 8 > bottom:
            self.warnings.append(f"page {n:02d}: text runs {end - bottom}px past the footer")

    # 01 cover
    def p_cover(self) -> Image.Image:
        F, c = self.F, self.issue["cover"]
        M = 14
        page = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        MAIN_TOP = 336
        subject = c["person"]
        room = self.faces.frame(subject)
        bg_size = (W - 2 * M, H - MAIN_TOP - M)
        if c.get("background"):
            # a picture of the editor's choosing as a shadow behind the subject: grey, dark, navy
            bgp = Path(c["background"])
            bg = Image.open(bgp if bgp.is_absolute() else self.here / bgp).convert("L")
            cx, cy = c.get("background_focus", [0.5, 0.35])
            bg = ImageOps.autocontrast(ImageOps.fit(bg, bg_size, Image.LANCZOS, centering=(cx, cy)), cutoff=1)
            bg = ImageEnhance.Brightness(bg.convert("RGB")).enhance(0.8).filter(ImageFilter.GaussianBlur(1.2))
            bg = Image.blend(bg, Image.new("RGB", bg.size, (16, 30, 96)), 0.35)
            grad = Image.linear_gradient("L").resize(bg.size)
            bg = Image.composite(Image.new("RGB", bg.size, (6, 10, 30)), bg, grad.point(lambda v: int(v * 0.45)))
        elif room is None:
            bg = Image.new("RGB", bg_size, NAVY)
        else:
            w, h = room.size
            bg = ImageOps.fit(room.crop((0, int(0.12 * h), int(0.74 * w), int(0.96 * h))), bg_size, Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(9))
            bg = ImageEnhance.Brightness(bg).enhance(0.55)
            bg = Image.blend(bg, Image.new("RGB", bg.size, (20, 40, 120)), 0.45)
            grad = Image.linear_gradient("L").resize(bg.size)
            bg = Image.composite(Image.new("RGB", bg.size, (6, 10, 30)), bg, grad.point(lambda v: int(v * 0.55)))
        page.paste(bg, (M, MAIN_TOP))

        # top strip: three teasers
        STRIP_Y, STRIP_H = M, 312
        widths = [340, 370, W - 2 * M - 340 - 370 - 16]
        styles = [((30, 70, 190), YELLOW, WHITE, False), ((255, 255, 255), RED, INK, True), ((14, 120, 60), YELLOW, WHITE, False)]
        d = ImageDraw.Draw(page)
        KH, PW = 50, 0.44
        panels = []
        x = M
        for t, wdt, (bgc, kc, hc, framed) in zip(c["teasers"], widths, styles):
            panels.append({"x": x, "w": wdt, "bg": bgc, "kc": kc, "hc": hc, "framed": framed, **t})
            x += wdt + 8
        k_size = min(fit(p["kicker"], lambda s: F.bahn(s), p["w"] - 28, 36, 36) for p in panels)
        h_size = min(fit(up(line), lambda s: F.bahn(s), p["w"] - int(p["w"] * PW) - 34, 90, (STRIP_H - KH - 22) // 4)
                     for p in panels for line in p["headline"])
        kf, hf = F.bahn(k_size), F.bahn(h_size)
        for p in panels:
            x, w = p["x"], p["w"]
            h = STRIP_H + (46 if p["framed"] else 0)
            d.rectangle([x, STRIP_Y, x + w, STRIP_Y + h], fill=p["bg"])
            d.text((x + 14, STRIP_Y + 10), p["kicker"], font=kf, fill=p["kc"])
            d.line([x + 14, STRIP_Y + KH - 6, x + 14 + d.textlength(p["kicker"], font=kf), STRIP_Y + KH - 6], fill=p["kc"], width=3)
            pw = int(w * PW)
            ph = self.faces.scene(p["person"], (pw, h - KH - 12), centering=(0.5, 0.3))
            page.paste(ph, (x + w - pw - 10, STRIP_Y + KH))
            lf = F.bahn(22)
            lw = d.textlength(up(p["label"]), font=lf) + 14
            d.rectangle([x + w - lw - 10, STRIP_Y + h - 42, x + w - 10, STRIP_Y + h - 12], fill=INK)
            d.text((x + w - lw - 3, STRIP_Y + h - 40), up(p["label"]), font=lf, fill=WHITE)
            y = STRIP_Y + KH + 2
            for line in p["headline"]:
                d.text((x + 14, y), up(line), font=hf, fill=p["hc"])
                y += int(h_size * 0.95)
            if p["framed"]:
                d.rectangle([x - 4, STRIP_Y - 4, x + w + 4, STRIP_Y + h + 4], outline=YELLOW, width=12)

        # masthead
        m1, m2 = self.wc["masthead"]
        lol = Image.new("RGBA", (420, 70), (0, 0, 0, 0))
        ImageDraw.Draw(lol).text((4, 0), m1, font=F.black(56), fill=RED, stroke_width=2, stroke_fill=WHITE)
        page.alpha_composite(sheared(lol), (50, MAIN_TOP + 6))
        MW = W - 2 * M - 60
        mf = F.black(fit(m2, F.black, MW, 190))
        bb = mf.getbbox(m2)
        mw, mh = bb[2] - bb[0] + 20, bb[3] - bb[1] + 20
        mask = Image.new("L", (mw, mh), 0)
        ImageDraw.Draw(mask).text((10 - bb[0], 10 - bb[1]), m2, font=mf, fill=255)
        stops = [(0.0, (255, 255, 255)), (0.42, (226, 232, 240)), (0.50, (120, 132, 150)), (0.56, (205, 212, 222)), (1.0, (250, 252, 255))]
        chrome = Image.new("RGB", (mw, mh))
        cd = ImageDraw.Draw(chrome)
        for yy in range(mh):
            t = yy / (mh - 1)
            for (t0, c0), (t1, c1) in itertools.pairwise(stops):
                if t0 <= t <= t1:
                    u = (t - t0) / (t1 - t0)
                    cd.line([(0, yy), (mw, yy)], fill=tuple(int(a + (b - a) * u) for a, b in zip(c0, c1)))
                    break
        mast = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        mast.paste((20, 24, 40, 255), (0, 0), mask.filter(ImageFilter.MaxFilter(9)))
        mast.paste(chrome, (0, 0), mask)
        mast = sheared(mast)
        mshadow = Image.new("RGBA", mast.size, (0, 0, 0, 0))
        mshadow.paste((0, 0, 0, 170), (0, 0), mast.split()[3])
        mshadow = mshadow.filter(ImageFilter.GaussianBlur(6))
        MAST_Y = MAIN_TOP + 58
        page.alpha_composite(mshadow, (M + 26 + 8, MAST_Y + 10))
        page.alpha_composite(mast, (M + 26, MAST_Y))

        # cover subject
        clip = Image.new("L", page.size, 0)
        ImageDraw.Draw(clip).rectangle([M, M, W - M - 1, H - M - 1], fill=255)
        cut = self.faces.cutout(subject, self.here)
        if cut is not None:
            bbox = cut.split()[3].getbbox()
            cut = cut.crop(bbox)
            scale = int(c.get("subject_height", 940)) / cut.height  # issue.json cover.subject_height
            cut = cut.resize((int(cut.width * scale), int(cut.height * scale)), Image.LANCZOS)
            cx = min(W - M - cut.width + 300, W - M - int(cut.width * 0.72))
            cy = H - M - cut.height + 10
            glow = Image.new("RGBA", page.size, (0, 0, 0, 0))
            glow.paste((0, 0, 0, 150), (cx + 10, cy + 8), cut.split()[3])
            glow = glow.filter(ImageFilter.GaussianBlur(12))
            glow.putalpha(Image.composite(glow.split()[3], Image.new("L", page.size, 0), clip))
            page.alpha_composite(glow)
            layer = Image.new("RGBA", page.size, (0, 0, 0, 0))
            layer.paste(cut, (cx, cy), cut)
            layer.putalpha(Image.composite(layer.split()[3], Image.new("L", page.size, 0), clip))
            page.alpha_composite(layer)
        else:
            self.warnings.append("cover: no cut-out (torch missing or no person found); using a panel")
            ph = rounded(self.faces.scene(subject, (430, 560), centering=(0.5, 0.3)), 24)
            page.alpha_composite(ph, (W - M - 430 - 20, H - M - 560 - 240))
        d = ImageDraw.Draw(page)

        # red kicker
        KY = MAIN_TOP + 334
        kf = F.bahn(50)
        kw = max(d.textlength(c["kicker"] + " " + c["kicker_hot"], font=kf), d.textlength(c["kicker_line2"], font=kf)) + 40
        d.rectangle([M + 26, KY, M + 26 + max(520, int(kw)), KY + 118], fill=RED)
        rich_line(d, M + 44, KY + 6, [(c["kicker"] + " ", WHITE), (c["kicker_hot"], YELLOW)], kf)
        rich_line(d, M + 44, KY + 60, [(c["kicker_line2"], WHITE)], kf)

        # headline
        lines = [up(line) for line in c["headline"]]
        HX, HY, HW = M + 26, KY + 134, 620
        HB = H - M - 3 * 58 - 18 - 14
        size = min(min(fit(line, F.impact, HW, 230) for line in lines), int((HB - HY) / len(lines) / 0.96 / 1.02))
        hf = F.impact(size)
        y = HY
        for line in lines:
            shadow_text(page, (HX, y), line, hf, YELLOW, stroke=6, stroke_fill=INK, shadow=(7, 8), blur=4)
            y += int(size * 0.96)
        d = ImageDraw.Draw(page)

        # circle inset
        D = 205
        inset = self.faces.square(c["inset_person"], D)
        circ = Image.new("L", (D, D), 0)
        ImageDraw.Draw(circ).ellipse([0, 0, D - 1, D - 1], fill=255)
        # the role under the name may run to two lines ("H3 co-founder, soon to be ex-wife":
        # split at ", " or " | "); the strip widens and the inset moves up to make room
        role = [r.strip() for r in re.split(r"\s*\|\s*|,\s+", c["inset_role"]) if r.strip()][:2]
        IX, IY = 548, H - M - 205 - 66 - (24 if len(role) > 1 else 0)
        d.ellipse([IX - 9, IY - 9, IX + D + 9, IY + D + 9], fill=WHITE)
        page.paste(inset, (IX, IY), circ)
        nf, sf = F.bahn(30), F.bahn(22)
        sw = max([d.textlength(up(c["inset_name"]), font=nf)] + [d.textlength(r, font=sf) for r in role]) + 28
        sw = max(sw, D - 40)
        d.rectangle([IX + D // 2 - sw / 2, IY + D - 6, IX + D // 2 + sw / 2, IY + D + 28 + 24 * len(role)], fill=INK)
        d.text((IX + D // 2, IY + D - 2), up(c["inset_name"]), font=nf, fill=WHITE, anchor="ma")
        for i, r in enumerate(role):
            d.text((IX + D // 2, IY + D + 26 + 24 * i), r, font=sf, fill=YELLOW, anchor="ma")

        # starburst
        SBX, SBY, R = 112, MAIN_TOP + 240, 86
        pts = []
        for i in range(48):
            r = R if i % 2 == 0 else R * 0.86
            a = math.pi * 2 * i / 48
            pts.append((SBX + r * math.cos(a), SBY + r * math.sin(a)))
        burst = Image.new("RGBA", page.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(burst)
        bd.polygon([(x + 5, y + 6) for x, y in pts], fill=(0, 0, 0, 120))
        burst = burst.filter(ImageFilter.GaussianBlur(4))
        bd = ImageDraw.Draw(burst)
        bd.polygon(pts, fill=WHITE)
        bd.polygon([(SBX + (x - SBX) * 0.9, SBY + (y - SBY) * 0.9) for x, y in pts], fill=RED)
        txt = Image.new("RGBA", (2 * R, 2 * R), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        bl = [up(line) for line in c["burst"]]
        bf = F.bahn(min(24, fit(max(bl, key=len), lambda s: F.bahn(s), 2 * R - 40, 24)))
        y0 = R - len(bl) * 25 / 2 + 2
        for i, line in enumerate(bl):
            td.text((R, y0 + i * 25), line, font=bf, fill=WHITE, anchor="ma")
        burst.alpha_composite(txt.rotate(-10, resample=Image.BICUBIC), (SBX - R, SBY - R))
        page.alpha_composite(burst)
        d = ImageDraw.Draw(page)

        # date stamp
        df = F.bahn(24)
        stamp = (datetime.strptime(self.issue["end"], "%Y-%m-%d")).strftime("%b %d, %Y").upper()
        sw = d.textlength(stamp, font=df) + 30
        d.rectangle([W - M - sw - 8, H - M - 212 - 8 - 46, W - M - 8, H - M - 212 - 8 - 12], fill=INK)
        d.text((W - M - 8 - sw / 2, H - M - 212 - 8 - 43), stamp, font=df, fill=WHITE, anchor="ma")

        # bullets
        bf = F.bahn(40)
        BY = H - M - 3 * 58 - 18
        for i, b in enumerate(c["bullets"]):
            parts = [(b["text"] + " ", WHITE), (up(b["hot"]), YELLOW)] if b.get("hot") else [(b["text"], WHITE)]
            y = BY + i * 58
            wtxt = sum(d.textlength(t, font=bf) for t, _ in parts)
            d.rectangle([M + 26, y, M + 26 + 46 + wtxt + 18, y + 50], fill=RED)
            d.rectangle([M + 38, y + 14, M + 60, y + 36], fill=YELLOW)
            rich_line(d, M + 72, y + 3, parts, bf)

        # barcode block
        BW, BH = 196, 212
        bx, by = W - M - BW - 8, H - M - BH - 8
        d.rectangle([bx, by, bx + BW, by + BH], fill=WHITE)
        d.text((bx + BW // 2, by + 8), f"WEEK OF {self.WEEK}", font=F.bahn(min(17, fit(f"WEEK OF {self.WEEK}", lambda s: F.bahn(s), BW - 10, 17))), fill=INK, anchor="ma")
        d.text((bx + BW // 2, by + 30), self.wc["price"], font=F.bahn(15), fill=INK, anchor="ma")
        random.seed(int(self.issue.get("issue") or 1) + 40)
        x = bx + 14
        while x < bx + BW - 16:
            w = random.choice([1, 1, 2, 2, 3, 4])
            d.rectangle([x, by + 52, x + w - 1, by + 52 + 112], fill=INK)
            x += w + random.choice([1, 2, 2, 3])
        d.text((bx + 10, by + 168), f"7  25274 405{int(self.issue.get('issue') or 1):02d}  1", font=F.bold(15), fill=INK)
        d.text((bx + BW - 12, by + 52), f"{int(self.issue.get('issue') or 1):02d}>", font=F.bold(14), fill=INK, anchor="ra")
        d.rectangle([bx, by + BH - 26, bx + BW, by + BH], fill=INK)
        d.text((bx + BW // 2, by + BH - 23), f"{m1} {m2}", font=F.bahn(19), fill=WHITE, anchor="ma")
        return page

    # 02 contents
    def p_contents(self) -> Image.Image:
        F, c = self.F, self.issue["contents"]
        p = self.page()
        d = self.chrome(p, 2, "CONTENTS")
        y = self.title(d, "IN THIS ISSUE", 104)
        st = self.stats
        strap = f"{st['shows']} SHOWS  ·  {st['preshows']} PRE-SHOWS  ·  " if st["preshows"] else f"{st['shows']} SHOWS  ·  "
        self.kicker_bar(d, MX, y + 4, [(strap, WHITE), (f"{round(st['hours'])} HOURS LIVE", YELLOW)], size=30)
        y0, rh = y + 86, 136
        rows = c["rows"][:7]
        for i, r in enumerate(rows):
            ry = y0 + i * rh
            d.text((MX, ry + 30), str(i + 1), font=F.serif(60), fill=RED)
            p.alpha_composite(self.faces.avatar(r["person"], 96), (MX + 64, ry + 16))
            tx = MX + 184
            head = up(r["headline"])
            hf = F.bahn(min(42, fit(head, lambda s: F.bahn(s), W - MX - 100 - tx, 42)))
            d.text((tx, ry + 22), head, font=hf, fill=INK)
            line = r["line"]
            lf = F.serif(26)
            while d.textlength(line, font=lf) > W - MX - 100 - tx and len(line) > 10:
                line = line[:-4].rstrip(" ,.") + "…"
            d.text((tx, ry + 74), line, font=lf, fill=MUTED)
            pill_w = 84
            d.rounded_rectangle([W - MX - pill_w, ry + 26, W - MX, ry + 64], 19, fill=INK)
            d.text((W - MX - pill_w // 2, ry + 45), up(r["day"])[:3], font=F.bahn(24), fill=WHITE, anchor="mm")
            d.text((W - MX - pill_w // 2, ry + 74), f"p. {r['page']}", font=F.serif(26), fill=RED, anchor="ma")
            if i < len(rows) - 1:
                d.line([MX, ry + rh - 4, W - MX, ry + rh - 4], fill=RULE, width=2)
        return p

    # 03-05 articles
    def p_article(self, n: int, a: dict, feature: bool = False) -> Image.Image:
        F = self.F
        p = self.page()
        d = self.chrome(p, n, up(a["section"]))
        y = self.kicker_bar(d, MX, 112, [(up(a["kicker"]) + "  ·  ", WHITE), (up(a["topic"]), YELLOW)], size=30)
        # the cover story gets a shorter photo and smaller type: room for twice the words
        ph_h = 230 if feature else 360
        ph = self.faces.scene(a["person"], (CW, ph_h), centering=(0.5, 0.35))
        p.alpha_composite(rounded(ph, 22), (MX, y + 18))
        self.tag(p, d, MX + 18, y + 36, up(a["watch"]))
        y += 18 + ph_h + (22 if feature else 18)
        l1, l2 = up(a["headline"][0]), up(a["headline"][1])
        hi = up(a.get("highlight") or "")
        cap = 84 if feature else 104
        hf = F.impact(min(cap, fit(l1, F.impact, CW, cap), fit(l2, F.impact, CW, cap)))
        d.text((MX - 2, y), l1, font=hf, fill=INK)
        y += int(hf.size * 1.04)
        if hi and hi in l2:
            i = l2.index(hi)
            parts = [(l2[:i], False), (hi, True), (l2[i + len(hi):], False)]
        else:
            parts = [(l2, False)]
        self.highlight_text(d, (MX - 2, y), [(t, h) for t, h in parts if t], hf)
        y += int(hf.size * 1.14)
        y = para(d, (MX, y), a["deck"], F.serif(27 if feature else 32), CW, INK, 34 if feature else 40) + 8
        d.text((MX, y), self.wc["byline"], font=F.bahn(20, "SemiBold"), fill=RED)
        y += 40
        colw = (CW - 40) // 2
        bf, lh = (F.bahn(22, "Regular"), 28) if feature else (F.bahn(25, "Regular"), 34)
        end1 = para(d, (MX, y), a["col1"], bf, colw, INK, lh)
        x2 = MX + colw + 40
        qy = para(d, (x2 + 22, y), f"“{a['quote']}”", F.serif(31), colw - 22, INK, 39)
        d.rectangle([x2, y + 4, x2 + 6, qy + 30], fill=RED)
        d.text((x2 + 22, qy + 2), up(a["quote_by"]), font=F.bahn(20, "SemiBold"), fill=MUTED)
        end2 = para(d, (x2, qy + 52), a["col2"], bf, colw, INK, lh)
        self.overflow(n, max(end1, end2))
        return p

    # 06 brief
    def p_brief(self) -> Image.Image:
        F, c = self.F, self.issue["brief"]
        p = self.page()
        d = self.chrome(p, 6, "NEWS IN BRIEF")
        y = self.title(d, "NEWS IN BRIEF", 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=MUTED)
        gx, gy, gap = MX, y + 54, 22
        cw, ch = (CW - gap) // 2, (H - 84 - 24 - (y + 54) - 2 * gap) // 3
        for i, card in enumerate(c["cards"][:6]):
            x0, y0 = gx + (i % 2) * (cw + gap), gy + (i // 2) * (ch + gap)
            ad = "PARODY" in up(card["label"]) or "SPONSORED" in up(card["label"])
            d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 22, fill=INK if ad else CARD)
            p.alpha_composite(self.faces.avatar(card["person"], 84), (x0 + 18, y0 + 18))
            self.bullet(d, x0 + 124, y0 + 34, 16)
            lf = F.bahn(min(22, fit(up(card["label"]), lambda s: F.bahn(s, "SemiBold"), cw - 170, 22)), "SemiBold")
            d.text((x0 + 150, y0 + 30), up(card["label"]), font=lf, fill=YELLOW if ad else RED)
            hl = wrap(d, up(card["headline"]), F.bahn(44), cw - 44)
            hs = 44 if len(hl) <= 2 else 36
            yy = para(d, (x0 + 22, y0 + 122), up(card["headline"]), F.bahn(hs), cw - 44, WHITE if ad else INK, hs + 4) + 8
            end = para(d, (x0 + 22, yy), card["body"], F.bahn(26, "Regular"), cw - 44, GREY if ad else MUTED, 34)
            if end > y0 + ch - 6:
                self.warnings.append(f"page 06: card {i + 1} text runs {end - (y0 + ch - 6)}px past its card")
        return p

    # 07 quote
    def p_quote(self) -> Image.Image:
        F, c = self.F, self.issue["quote"]
        p = self.page(dark=True)
        d = self.chrome(p, 7, "QUOTE OF THE WEEK", dark=True)
        d.text((MX - 6, 92), "“", font=F.serif(260, bold=True), fill=YELLOW)
        text = c["text"]
        qs = 74
        while len(wrap(d, text, F.serif(qs), CW)) > 5 and qs > 44:
            qs -= 4
        y = para(d, (MX, 318), text, F.serif(qs), CW, WHITE, int(qs * 1.22)) + 34
        p.alpha_composite(self.faces.avatar(c["person"], 112, ring=YELLOW), (MX, y))
        d.text((MX + 140, y + 16), up(c["name"]), font=F.bahn(46), fill=WHITE)
        d.text((MX + 140, y + 70), c["where"], font=F.bahn(25, "Regular"), fill=GREY)
        y += 170
        d.line([MX, y, W - MX, y], fill=(70, 70, 70), width=2)
        y += 34
        d.rectangle([MX, y + 8, MX + 40, y + 14], fill=YELLOW)
        d.text((MX + 54, y - 4), "RUNNERS-UP", font=F.bahn(28), fill=YELLOW)
        y += 56
        for r in c["runners_up"][:3]:
            y = para(d, (MX, y), f"“{r['text']}”", F.serif(39), CW, WHITE, 50) + 4
            d.text((MX, y), r["by"], font=F.bahn(25, "SemiBold"), fill=GREY)
            y += 56
        self.overflow(7, y - 20)
        return p

    # 08 numbers
    def p_numbers(self) -> Image.Image:
        F, c, st = self.F, self.issue["numbers"], self.stats
        p = self.page()
        d = self.chrome(p, 8, "BY THE NUMBERS")
        y = self.title(d, "THE WEEK IN NUMBERS", 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=MUTED)
        y = self.kicker_bar(d, MX, y + 50, [("SCREEN TIME  ·  ", WHITE), ("HOURS ON AIR THIS WEEK", YELLOW)], size=28)
        mins = st["screen_minutes"]
        cast = [m for m in c["cast"] if mins.get(m["topic"])][:6]
        cast.sort(key=lambda m: -mins[m["topic"]])
        if not cast:
            cast = [{"person": t, "name": up(t), "topic": t} for t in list(mins)[:6]]
        top = max(mins[m["topic"]] for m in cast)
        rows_y, rh = y + 18, 66
        bx0, bx1 = MX + 300, W - MX - 130
        for i, m in enumerate(cast):
            ry = rows_y + i * rh
            p.alpha_composite(self.faces.avatar(m["person"], 52), (MX, ry + 2))
            d.text((MX + 72, ry + 10), up(m["name"]), font=F.bahn(min(32, fit(up(m["name"]), lambda s: F.bahn(s), 215, 32))), fill=INK)
            bw = max(8, int((bx1 - bx0) * mins[m["topic"]] / top))
            d.rounded_rectangle([bx0, ry + 12, bx1, ry + 48], 8, fill=CARD)
            d.rounded_rectangle([bx0, ry + 12, bx0 + bw, ry + 48], 8, fill=RED if i == 0 else INK)
            d.text((W - MX, ry + 12), hm(mins[m["topic"]]), font=F.bahn(30, "SemiBold"), fill=RED if i == 0 else INK, anchor="ra")
        cx, cy = MX + 30, rows_y - 14
        d.polygon([(cx - 22, cy + 18), (cx - 22, cy), (cx - 11, cy + 9), (cx, cy - 6), (cx + 11, cy + 9),
                   (cx + 22, cy), (cx + 22, cy + 18)], fill=YELLOW, outline=INK, width=2)
        y = rows_y + len(cast) * rh + 8
        rest = sum(mins[m["topic"]] for m in cast[1:])
        line = c["lolcow_line"]
        if "put together" in line.lower() and mins[cast[0]["topic"]] <= rest:
            line = f"More airtime than anyone else this week: {hm(mins[cast[0]['topic']])} of it."
            self.warnings.append("page 08: 'put together' line was false; replaced")
        lf = F.bahn(36)
        label = f"LOLCOW OF THE WEEK: {up(c['lolcow'] or cast[0]['name'])}"
        d.rectangle([MX - 6, y + 4, MX + d.textlength(label, font=lf) + 6, y + 44], fill=YELLOW)
        d.text((MX, y), label, font=lf, fill=INK)
        y = para(d, (MX, y + 50), line, F.serif(29), CW, INK, 36) + 20
        gap = 22
        cw = (CW - gap) // 2
        ch = (H - 84 - 24 - y - gap) // 2
        for i, card in enumerate(c["cards"][:4]):
            x0, y0 = MX + (i % 2) * (cw + gap), y + (i // 2) * (ch + gap)
            d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 22, fill=CARD)
            nf = F.impact(min(96, fit(card["number"], F.impact, cw - 48, 96)))
            d.text((x0 + 24, y0 + 14), card["number"], font=nf, fill=RED if card.get("red") else INK)
            d.text((x0 + 24, y0 + ch - 84), up(card["label"]), font=F.bahn(min(28, fit(up(card["label"]), lambda s: F.bahn(s), cw - 48, 28))), fill=INK)
            d.text((x0 + 24, y0 + ch - 46), card["sub"], font=F.bahn(min(23, fit(card["sub"], lambda s: F.bahn(s, "Regular"), cw - 48, 23)), "Regular"), fill=MUTED)
        return p

    # 09 marketplace
    def p_market(self) -> Image.Image:
        F, c = self.F, self.issue["market"]
        p = self.page()
        d = self.chrome(p, 9, "MARKETPLACE")
        y = self.title(d, "THE MARKETPLACE", 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=MUTED)
        y += 54
        bh = 400
        d.rounded_rectangle([MX, y, W - MX, y + bh], 24, fill=INK)
        d.rounded_rectangle([MX + 30, y + 30, MX + 190, y + 70], 20, fill=YELLOW)
        d.text((MX + 110, y + 50), "AD  ·  PARODY", font=F.bahn(22), fill=INK, anchor="mm")
        name = up(c["big_name"])
        nf = F.impact(min(96, fit(name, F.impact, 600, 96)))
        d.text((MX + 30, y + 96), name, font=nf, fill=WHITE)
        para(d, (MX + 32, y + 214), c["big_body"], F.bahn(32, "Regular"), 560, GREY, 42)
        d.text((MX + 32, y + bh - 64), c["big_tag"], font=F.serif(28), fill=YELLOW)
        # product: a price starburst on a yellow badge
        bx, by = W - MX - 290, y + 70
        pts = []
        for i in range(32):
            r = 130 if i % 2 == 0 else 104
            a = math.pi * 2 * i / 32
            pts.append((bx + 145 + r * math.cos(a), by + 140 + r * math.sin(a)))
        d.polygon(pts, fill=YELLOW, outline=WHITE, width=4)
        price = c["big_price"]
        pf = F.impact(min(84, fit(price, F.impact, 190, 84)))
        d.text((bx + 145, by + 140), price, font=pf, fill=INK, anchor="mm")
        y += bh + 22
        gap = 22
        cw, ch = (CW - gap) // 2, (H - 84 - 24 - y - gap) // 2
        for i, ad in enumerate(c["ads"][:4]):
            x0, y0 = MX + (i % 2) * (cw + gap), y + (i // 2) * (ch + gap)
            d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 22, fill=CARD)
            d.text((x0 + 22, y0 + 22), f"AD  ·  {up(ad['label'])}", font=F.bahn(20, "SemiBold"), fill=RED)
            p.alpha_composite(self.faces.avatar(ad["person"], 76), (x0 + cw - 100, y0 + 16))
            yy = para(d, (x0 + 22, y0 + 60), up(ad["name"]), F.bahn(38), cw - 140, INK, 42) + 6
            end = para(d, (x0 + 22, max(yy, y0 + 112)), ad["body"], F.bahn(24, "Regular"), cw - 44, MUTED, 31)
            if end > y0 + ch - 6:
                self.warnings.append(f"page 09: ad {i + 1} text runs {end - (y0 + ch - 6)}px past its card")
        return p

    # 10 next week + poll
    def p_back(self) -> Image.Image:
        F, c = self.F, self.issue["back"]
        p = self.page()
        d = self.chrome(p, 10, "NEXT WEEK", cta="FOLLOW")
        y = self.title(d, "NEXT WEEK", 104)
        d.text((MX, y - 6), c["strap"], font=F.serif(32), fill=MUTED)
        y += 50
        sched = next_week(self.cfg, self.week)
        rh = 74
        for i, (day, date, show, t) in enumerate(sched[:6]):
            ry = y + i * rh
            d.text((MX, ry + 6), day, font=F.impact(58), fill=RED)
            d.text((MX + 140, ry + 14), date, font=F.serif(34), fill=INK)
            d.text((MX + 300, ry + 16), up(show), font=F.bahn(36), fill=INK)
            d.text((W - MX, ry + 20), t, font=F.bahn(28, "SemiBold"), fill=MUTED, anchor="ra")
            d.line([MX, ry + rh - 2, W - MX, ry + rh - 2], fill=RULE, width=2)
        y += max(len(sched[:6]), 3) * rh + 30
        bottom = H - 84 - 24
        d.rounded_rectangle([MX, y, W - MX, bottom], 26, fill=INK)
        d.rounded_rectangle([MX + 30, y + 30, MX + 230, y + 70], 20, fill=YELLOW)
        d.text((MX + 130, y + 50), "READERS' POLL", font=F.bahn(22), fill=INK, anchor="mm")
        q = up(c["poll"])
        d.text((MX + 30, y + 92), q, font=F.impact(min(84, fit(q, F.impact, CW - 60, 84))), fill=WHITE)
        ow = (CW - 60 - 2 * 18) // 3
        oy = y + 200
        oh = min(220, bottom - oy - 70)
        for i, o in enumerate(c["options"][:3]):
            ox = MX + 30 + i * (ow + 18)
            d.rounded_rectangle([ox, oy, ox + ow, oy + oh], 18, fill=(44, 44, 44))
            av = min(128, oh - 70)
            p.alpha_composite(self.faces.avatar(o["person"], av, ring=YELLOW), (ox + (ow - av - 8) // 2, oy + 18))
            d.text((ox + ow // 2, oy + oh - 34), up(o["name"]), font=F.bahn(min(40, fit(up(o["name"]), lambda s: F.bahn(s), ow - 20, 40))), fill=WHITE, anchor="mm")
        d.text((MX + 30, oy + oh + 30), c["note"], font=F.bahn(28, "Regular"), fill=GREY)
        return p

    def render(self, only: set[str] | None = None) -> list[Path]:
        fns = {"01_cover": self.p_cover, "02_contents": self.p_contents,
               "03_feature": lambda: self.p_article(3, self.issue["feature"], feature=True),
               "04_article": lambda: self.p_article(4, self.issue["articles"][0]),
               "05_article": lambda: self.p_article(5, self.issue["articles"][1]),
               "06_brief": self.p_brief, "07_quote": self.p_quote, "08_numbers": self.p_numbers,
               "09_market": self.p_market, "10_back": self.p_back}
        out = []
        for name in PAGES:
            if only and name[:2] not in only and name not in only:
                continue
            self.faces.used.clear()
            img = fns[name]().convert("RGB")
            dst = self.here / f"{name}.jpg"
            img.save(dst, quality=92)
            out.append(dst)
            print(f"  {dst.name}")
        return out


def next_week(cfg: dict, week: dict) -> list[tuple[str, str, str, str]]:
    """Next week's shows: this week's live streams' weekdays and start times, a week later."""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    rows = []
    for s in week["streams"].values():
        if s["preshow"]:
            continue
        at = datetime.fromisoformat(s["started_at"]).astimezone(tz) + timedelta(days=7)
        minute = 30 if at.minute >= 15 else 0  # shows start "at" a round time
        if at.minute >= 45:
            at += timedelta(hours=1)
            minute = 0
        at = at.replace(minute=minute)
        zone = at.strftime("%Z").replace("PDT", "PT").replace("PST", "PT")
        rows.append((at, DAYS[at.weekday()], at.strftime("%b %d").replace(" 0", " "), s["show_name"],
                     f"{at.strftime('%I:%M %p').lstrip('0')} {zone}"))
    rows.sort()
    return [r[1:] for r in rows]


def contact_sheet(pages: list[Path], dst: Path) -> None:
    tw, th = 360, 480
    cols = 5
    rows = (len(pages) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 10) + 10, rows * (th + 10) + 10), (40, 40, 40))
    for i, p in enumerate(pages):
        im = Image.open(p).convert("RGB").resize((tw, th), Image.LANCZOS)
        sheet.paste(im, (10 + (i % cols) * (tw + 10), 10 + (i // cols) * (th + 10)))
    sheet.save(dst, quality=85)


def post_text(issue: dict, here: Path) -> None:
    n = int(issue.get("issue") or 1)
    post = issue.get("post") or {}
    text = (f"# {issue.get('week')}: issue {n:02d}\n\n"
            f"Pages: 01-10 (1080x1440). TikTok: one carousel of all ten. YouTube Posts take 5 images: part 1 = 01-05, part 2 = 06-10.\n\n"
            f"## Part 1 (01-05)\n\n{post.get('part1', '')}\n\n## Part 2 (06-10)\n\n{post.get('part2', '')}\n")
    (here / "post_text.md").write_text(text, encoding="utf-8")


# ---------- entry ----------

def run(cfg: dict, day: str | None = None, headline: str = "", gather_only: bool = False,
        redraft: bool = False, render_only: bool = False, only: str = "", write: str = "") -> Path:
    start, end = week_of(cfg, day)
    here = issue_dir(cfg, start)
    here.mkdir(parents=True, exist_ok=True)
    print(f"{week_label(start)} -> {here}")
    wk, st = here / "week.json", here / "stats.json"
    if render_only and wk.exists() and st.exists():
        week, stats = _load(wk), _load(st)
    else:
        print("gathering the week...")
        week, stats = gather(cfg, start, end, here)
        wk.write_text(json.dumps(week, indent=1, ensure_ascii=False), encoding="utf-8")
        st.write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {stats['shows']} shows, {stats['preshows']} pre-shows, {stats['hours']:.1f} h live, "
              f"{stats['chat_messages']:,} chat messages")
    if gather_only:
        return here
    ij = here / "issue.json"
    if redraft or not ij.exists():
        if ij.exists():
            backup = here / f"issue.before_{datetime.now():%Y-%m-%d_%H%M}.json"
            ij.replace(backup)
            print(f"  previous issue.json kept as {backup.name}")
        issue = draft(cfg, here, week, stats, headline)
        ij.write_text(json.dumps(issue, indent=1, ensure_ascii=False), encoding="utf-8")
        if cfg["weekly"]["write_articles"] and not write:
            write = "03,04,05"
    else:
        issue = _load(ij)
        print(f"  issue.json kept (issue {issue.get('issue')}); --draft rewrites it")
    if write:
        pages = sorted({int(x) for x in write.split(",") if x.strip()} & set(ARTICLE_PAGES))
        if not pages:
            raise SystemExit("--write takes article pages: 03, 04, 05")
        backup = here / f"issue.before_write_{datetime.now():%Y-%m-%d_%H%M}.json"
        backup.write_text(json.dumps(issue, indent=1, ensure_ascii=False), encoding="utf-8")
        issue = write_articles(cfg, here, week, stats, issue, pages, headline)
        ij.write_text(json.dumps(issue, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  issue.json updated (previous copy: {backup.name})")
    print("rendering...")
    r = Renderer(cfg, here, week, stats, issue)
    pages = r.render({x.strip() for x in only.split(",") if x.strip()} or None)
    if not only:
        contact_sheet(pages, here / "contact_sheet.jpg")
        post_text(issue, here)
    for w in r.warnings:
        print(f"  ! {w}", file=sys.stderr)
    return here
