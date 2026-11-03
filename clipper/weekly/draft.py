"""The model's part: the draft of the whole issue (prompts/weekly_<v>.md) and the writer pass,
one call per article page (prompts/weekly_article_<v>.md), fitted to what the page holds."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from ..errors import ClipperError
from ..prompts import Prompts
from .faces import usable
from .gather import hm, next_issue_number
from .render import Renderer

# Words each article layout holds (left column, right column beside the pull quote, deck
# characters): the cover story's 22 px layout and the two features' 25 px layout.
BUDGET = {"feature": (150, 110, 150), "article": (80, 52, 130)}  # 04/05 had a spare 100 px at 70/45
ARTICLE_PAGES = {3: ("feature", True), 4: ("articles", 0), 5: ("articles", 1)}


# ---------- draft ----------

def json_text(claude, model: str, system: str, user: str, effort: str) -> tuple[dict, dict]:
    """A streamed JSON answer without structured output: a field-by-field schema of the
    issue is too large a grammar for the API, and a free object isn't allowed, so the prompt
    describes the fields and check_issue() looks for them."""
    return claude.json_text(model, system, user + "\n\nAnswer with the issue as one JSON object "
                            "and nothing else.", effort, max_tokens=32000)


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


def credit(hook: dict) -> str:
    """The quote pool's "said by" (weekly v2 prompts); nothing for a stream without names."""
    by = hook.get("said_by")
    if not by:
        return ""
    return "said in a clip they played or by a guest; " if by == "?" else f"said by {by}; "


def draft_user(week: dict, stats: dict, headline: str, issue: int, photos: dict[str, Path],
               labels: dict[str, list[str]] | None = None) -> str:
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
            lines += [f"  - \"{h['hook']}\" ({credit(h)}{h['id']}: {h['title']})" for h in s["hooks"]]
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
        for f in usable(s["faces"]):
            people[f["person"]] += 1
    lines.append("## Faces available (person: frames found). Use these names exactly for `person` fields; "
                 "anyone else gets initials.")
    lines.append(", ".join(f"{p} ({n})" for p, n in people.most_common()))
    if photos:
        labels = labels or {}
        lines.append("Portrait photos on file (also usable): " + ", ".join(
            f"{p} ({', '.join(labels[p])})" if labels.get(p) else p for p in sorted(photos)))
    return "\n".join(lines)


def draft_system(cfg: dict) -> str:
    """prompts/weekly_<v>.md with the house style."""
    prompts = Prompts(cfg)
    prompts.version = cfg["weekly"]["prompt_version"]
    return prompts.get("weekly")


def draft(cfg: dict, here: Path, week: dict, stats: dict, headline: str) -> dict:
    from ..llm import Claude, preflight
    from ..thumbs import photos as photo_dir
    from ..thumbs.frames import photo_labels

    wc = cfg["weekly"]
    system = draft_system(cfg)
    issue = next_issue_number(cfg, here)
    tc = {**cfg["thumbnail"]}
    tc["_photos"] = photo_dir(tc)
    user = draft_user(week, stats, headline, issue, tc["_photos"], photo_labels(tc))
    claude = Claude(cfg)
    preflight(claude, wc["model"])
    print(f"  drafting issue {issue:02d} with {wc['model']} ({len(user) // 4:,} tokens of week)...")
    out, meta = json_text(claude, wc["model"], system, user, wc["effort"])
    if out is None:
        raise ClipperError(f"the model refused to draft the issue ({meta})")
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
            pool.append(f"- \"{h['hook']}\" ({credit(h)}{st['show_name']}, {st['day']} {st['date']}: {h['title']})")
    lines += ["", "## The rest of the week (titles only, for context)"] + rest[:60]
    lines += ["", "## Quote pool (the only lines you may quote, verbatim)"] + pool
    return "\n".join(lines)


def article_system(cfg: dict, budget: tuple) -> str:
    """prompts/weekly_article_<v>.md for this word budget, with the owner's own piece as the
    example and the house style."""
    wc = cfg["weekly"]
    prompts = Prompts(cfg)
    prompts.version = wc["article_prompt_version"]
    example = (Path(cfg["paths"]["prompts"]) / wc["article_example"]).read_text(encoding="utf-8")
    prompts.vars.update(example=example, col1_words=budget[0], col2_words=budget[1], deck_chars=budget[2])
    return prompts.get("weekly_article")


def write_articles(cfg: dict, here: Path, week: dict, stats: dict, issue: dict, pages: list[int],
                   note: str = "") -> dict:
    """Rewrite the deck, columns and pull quote of these article pages with the writer prompt,
    one call each; a page that would overflow is sent back once with how much to cut."""
    from ..llm import Claude, preflight

    wc = cfg["weekly"]
    claude = Claude(cfg)
    preflight(claude, wc["model"])
    for page in pages:
        a = _article_of(issue, page)
        budget = BUDGET["feature" if page == 3 else "article"]
        system = article_system(cfg, budget)
        user = article_user(week, a, page, budget, note)
        for attempt in range(2):
            again = " again, shorter" if attempt else ""
            print(f"  writing page {page:02d} ({a.get('topic')}) with {wc['model']}{again}...")
            out, meta = json_text(claude, wc["model"], system, user, wc["effort"])
            if out is None:
                print(f"  ! page {page:02d}: the model refused ({meta.get('refusal')}); kept as it was", file=sys.stderr)
                break
            draft = {k: out.get(k, a.get(k, "")) for k in ("deck", "col1", "quote", "quote_by", "col2")}
            _, over = Renderer(cfg, here, week, stats, issue).p_article(page, {**a, **draft}, feature=(page == 3))
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

