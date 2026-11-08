"""The weekly issue: the week's streams -> a ten-page tabloid issue (issue #35).

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
  clipper weekly --format youtube         only the square pages (default: [weekly] formats)

Three idempotent steps in one folder:
- gather: every stream started in the week (shows and pre-shows) -> week.json (segments
  with their titles and summaries, compilations, uploads, Shorts, the hooks of the picks
  as a verbatim quote pool, the faces faces.json named and thumbnails.json found) and stats.json (hours live,
  chat, top emote, screen time per topic, word counts in the transcripts).
- draft: [weekly] model writes issue.json from week.json and prompts/weekly_<v>.md: every
  page's copy, who is pictured where (people the renderer can find a face for), the poll.
  Then the writer pass (write_articles): one call per article page (03 cover story, 04 and
  05 features) with weekly_article_<v>.md and the owner's own piece as the example
  (clipper/prompts/weekly_article_example.md) writes the deck, both columns and the pull quote to
  the word budget the page holds; a draft that would overflow is sent back once to be cut.
  Edit issue.json by hand and --render; a draft never overwrites an edited file unless
  --draft is given, and --write redoes only the articles named.
- render: 1080x1440 pages 01-10 (cover, contents, cover story, two features, news in brief,
  quote, numbers, marketplace, next week + poll), contact_sheet.jpg and post_text.md; and,
  for YouTube Posts (which show images square), the same pages laid out on a 1440x1440
  canvas and scaled to 1080x1080 in youtube/ ([weekly] formats).

Faces come from the streams' thumbnails.json (the frame the thumbnail model picked and its
face box, by main_person), then [thumbnail] photos (from the cast library, the photo whose
label issue.json "photo_labels" names), else initials. The cover subject is cut
out with torchvision DeepLabV3 when torch is installed (the GPU machine); otherwise the
cover shows the frame in a rounded panel.

The look (canvas, palette, the cover's geometry, the fixed copy) is data in weekly/style.py;
<paths.weekly>/style.toml overrides any of it.

  gather.py  the week -> week.json, stats.json; next week's shows
  draft.py   the draft and the writer pass (the model calls)
  faces.py   person -> face crop, scene, cut-out or initials
  render.py  the pages, contact sheet and post text
  style.py   the look as data, and style.toml
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .. import wiki
from ..errors import ClipperError
from . import draft, gather, render, style

# ---------- entry ----------

ISSUE_PAGES = len(render.PAGES)   # an issue's pages, 01-10: a thread or a Discord post carries all or none


def issue_folder(cfg: dict, day: str | None) -> Path:
    """weekly/<Saturday>/ of the week holding `day`, or the newest issue drafted."""
    root = Path(cfg["paths"]["weekly"])
    if day:
        d = datetime.strptime(day, "%Y-%m-%d")
        here = root / (d + timedelta(days=(5 - d.weekday()) % 7)).strftime("%Y-%m-%d")
    else:
        drafted = sorted(p.parent for p in root.glob("*/issue.json"))
        if not drafted:
            raise ClipperError(f"no weekly issue in {root}; run: clipper weekly")
        here = drafted[-1]
    if not (here / "issue.json").exists():
        raise ClipperError(f"no issue.json in {here}; run: clipper weekly --week {day}")
    return here



def issue_pages(here: Path) -> list[Path]:
    """Pages 01-10 of an issue, each exactly once: a half-finished render would otherwise go
    out as a complete thread and the missing page could never follow."""
    pages = sorted(here.glob("[0-9][0-9]_*.jpg"))
    nums = [p.name[:2] for p in pages]
    want = [f"{n:02d}" for n in range(1, ISSUE_PAGES + 1)]
    if nums != want:
        missing = sorted(set(want) - set(nums))
        extra = sorted(n for n in set(nums) if nums.count(n) > 1 or n not in want)
        raise ClipperError(f"{here} needs pages 01-{ISSUE_PAGES:02d} once each (missing {missing or '-'}, "
                           f"extra {extra or '-'}); run: clipper weekly --render")
    return pages


def run(cfg: dict, day: str | None = None, headline: str = "", gather_only: bool = False,
        redraft: bool = False, render_only: bool = False, only: str = "", write: str = "",
        fmt: str = "") -> Path:
    start, end = gather.week_of(cfg, day)
    here = gather.issue_dir(cfg, start)
    here.mkdir(parents=True, exist_ok=True)
    print(f"{gather.week_label(start)} -> {here}")
    wk, st = here / "week.json", here / "stats.json"
    if render_only and wk.exists() and st.exists():
        week, stats = gather._load(wk), gather._load(st)
    else:
        print("gathering the week...")
        week, stats = gather.gather(cfg, start, end, here)
        wk.write_text(json.dumps(week, indent=1, ensure_ascii=False), encoding="utf-8")
        st.write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {stats['shows']} shows, {stats['preshows']} pre-shows, {stats['hours']:.1f} h live, "
              f"{stats['chat_messages']:,} chat messages")
    if gather_only:
        return here
    if style.masthead(cfg["weekly"]) is None:
        raise ClipperError('[weekly] masthead is not set: the cover needs two words, red then chrome '
                           '("WEEKLY" follows), e.g. ["DAILY", "PLANET"]; ' + wiki.page("Weekly-issue"))
    ij = here / "issue.json"
    if render_only and not ij.exists():
        raise ClipperError(f"--render: no issue.json in {here} to render; a draft costs model calls, "
                           "so run without --render (or --week <saturday> for an earlier issue)")
    if redraft or not ij.exists():
        if ij.exists():
            backup = here / f"issue.before_{datetime.now():%Y-%m-%d_%H%M}.json"
            ij.replace(backup)
            print(f"  previous issue.json kept as {backup.name}")
        issue = draft.draft(cfg, here, week, stats, headline)
        ij.write_text(json.dumps(issue, indent=1, ensure_ascii=False), encoding="utf-8")
        if cfg["weekly"]["write_articles"] and not write:
            write = "03,04,05"
    else:
        issue = gather._load(ij)
        print(f"  issue.json kept (issue {issue.get('issue')}); --draft rewrites it")
    if write:
        pages = sorted({int(x) for x in write.split(",") if x.strip()} & set(draft.ARTICLE_PAGES))
        if not pages:
            raise ClipperError("--write takes article pages: 03, 04, 05")
        backup = here / f"issue.before_write_{datetime.now():%Y-%m-%d_%H%M}.json"
        backup.write_text(json.dumps(issue, indent=1, ensure_ascii=False), encoding="utf-8")
        issue = draft.write_articles(cfg, here, week, stats, issue, pages, headline)
        ij.write_text(json.dumps(issue, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  issue.json updated (previous copy: {backup.name})")
    formats = [f.strip() for f in fmt.split(",") if f.strip()] or list(cfg["weekly"]["formats"])
    bad = [f for f in formats if f not in ("tiktok", "youtube")]
    if bad:
        raise ClipperError(f"--format takes tiktok and/or youtube, not {', '.join(bad)}")
    look = style.load(cfg)
    which = {x.strip() for x in only.split(",") if x.strip()} or None
    warnings: set[str] = set()
    for f in formats:
        print(f"rendering {f} ({'1080x1080' if f == 'youtube' else '1080x1440'})...")
        r = render.Renderer(cfg, here, week, stats, issue, square=(f == "youtube"), style=look)
        pages = r.render(which)
        warnings.update(r.warnings)
        if not only:
            render.contact_sheet(pages, (here / "youtube" if f == "youtube" else here) / "contact_sheet.jpg")
    if not only:
        render.post_text(issue, here)
    for w in sorted(warnings):
        print(f"  ! {w}", file=sys.stderr)
    return here
