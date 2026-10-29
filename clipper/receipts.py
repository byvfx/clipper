"""`clipper receipts`: the night's stories about an archive subject -> his own old clips (#48).

Usage:
    clipper receipts find <stream> [--segments s02,s05]   stories + archive picks -> receipts.json
    clipper receipts list <stream>                        what receipts.json holds
    clipper receipts script <stream>                      Mother Moo's voice-over -> receipts.json
    clipper receipts cut <stream>                         render receipts/receipts.mp4
    clipper receipts upload <stream>                      upload it private (never scheduled)

`find` takes the stream's segments about [receipts] subject (by topic or figure), has
[receipts] model write the stories the show went after, each with a search request for
the back catalog, and runs every request through `archive ask` (archive_index.answer).
The fitting windows become receipts: an archive item, a clip around the quote (snapped to
sentence starts, [receipts] lead_s / tail_s / max_clip_s), source url, date, quote.
The best [receipts] per_story strong fits per story get use = true. Cost: one model call
over the segments' transcripts plus ~$0.003 per story; it prints the total.

receipts/receipts.json is the edit: set `use`, reorder stories or receipts, trim `start` /
`end` (seconds in the source video), rewrite a story's `story` / `now` (the card) or the
video's `title` / `description`. `find` again keeps the old file as receipts.<time>.json.

`cut` downloads each used clip's section from YouTube (yt-dlp, cached in receipts/work/ by
item and range, so a trim downloads again), re-encodes it to 1920x1080 30 fps with a lower
third (channel and date, video title) and a corner bug, bleeps it from the archive item's
word timings like our own cuts, puts a story card (the story, "now") before each story,
and joins it all into receipts/receipts.mp4. The description lists every receipt's
source link at its moment, with chapters per story.

`script` has [receipts] script_model write a voice-over for the used stories and clips:
`open`, `close` (in receipts.json "script"), a `vo` per story (read over its card) and per
receipt (a lead-in over a "THEN / date" card before the clip; "" for none). Edit the lines
there; `cut` voices each line with ElevenLabs ([narrate] voice_id, tts_model, tts_settings),
once per text (receipts/vo/), so a changed line is the only one read again. Cards with a
read last as long as it. No script: silent cards, as before.

`upload` sends receipts.mp4 to the channel as private, outside schedule.json, so
`[youtube] auto_upload` never touches it and nothing goes public until someone does it in
Studio. The video id goes into receipts.json. Sending the link on is the user's call.
"""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path

from . import jsonstate
from .archive import netscape_cookies, subject_dir
from .bleep import Bleeper
from .config import for_stream
from .cut import encode_args
from .errors import ClipperError
from .timefmt import clock

STORIES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["stories"],
    "properties": {"stories": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["story", "now", "question", "before", "segments"],
        "properties": {"story": {"type": "string"}, "now": {"type": "string"},
                       "question": {"type": "string"}, "before": {"type": "string"},
                       "segments": {"type": "array", "items": {"type": "string"}}}}}},
}
W, H, FPS = 1920, 1080, 30


def out_dir(sdir: Path) -> Path:
    return sdir / "receipts"


def load(sdir: Path) -> dict:
    p = out_dir(sdir) / "receipts.json"
    if not p.exists():
        raise ClipperError(f"no receipts for {sdir.name} yet: clipper receipts find {sdir.name}")
    return jsonstate.load(p)


def save(sdir: Path, data: dict) -> None:
    p = out_dir(sdir) / "receipts.json"
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def about_subject(seg: dict, rc: dict) -> bool:
    if seg.get("kind") == "intro":
        return False
    return seg.get("topic") in rc["topics"] or bool(set(seg.get("figures") or []) & set(rc["figures"]))


# ---------- find ----------

TOK = re.compile(r"[a-z0-9']+")


def tokens(text: str) -> list[str]:
    return TOK.findall(text.lower().replace("’", "'"))


def clip_bounds(idir: Path, win_start: float, quote: str, rc: dict) -> tuple[float, float, bool]:
    """(start, end, found): the quote located in the item's words near its index window,
    widened by lead_s / tail_s to segment edges where they are close. found is False when
    the quote isn't in the words (the model paraphrased): then the window itself is used."""
    words = [json.loads(x) for x in (idir / "words.jsonl").read_text(encoding="utf-8").splitlines()]
    near = [w for w in words if win_start - 10 <= w["start"] <= win_start + 60]
    wt = [(tokens(w["word"]) or [""])[0] for w in near]
    q = tokens(quote)
    best, at = 0, None
    for i in range(len(near)):
        score = sum(1 for j, t in enumerate(q) if i + j < len(wt) and wt[i + j] == t)
        if score > best:
            best, at = score, i
    found = bool(q) and at is not None and best >= max(2, len(q) // 2)
    if found:
        q_start, q_end = near[at]["start"], near[min(at + len(q), len(near)) - 1]["end"]
    else:
        q_start, q_end = win_start, win_start + 25
    segs = jsonstate.load(idir / "transcript.json").get("segments") or []
    a, b = q_start - rc["lead_s"], q_end + rc["tail_s"]
    starts = [s["start"] for s in segs if a - 8 <= s["start"] <= a]
    ends = [s["end"] for s in segs if b <= s["end"] <= b + 6]
    a, b = (max(starts) if starts else a), (min(ends) if ends else b)
    if b - a > rc["max_clip_s"]:  # keep the quote, give up lead-in first
        a = max(a, q_end + rc["tail_s"] - rc["max_clip_s"])
        b = min(b, a + rc["max_clip_s"])
    return round(max(0.0, a), 2), round(b, 2), found


def find(sdir: Path, cfg: dict, only: list[str] | None = None) -> None:
    from . import archive_index
    from .scoring import Claude, Prompts, load_words, preflight, transcript_lines

    cfg = for_stream(cfg, sdir)
    rc = cfg["receipts"]
    subject = rc["subject"]
    adir = subject_dir(cfg, subject)
    segs = jsonstate.load(sdir / "segments.json")["segments"]
    picked = [s for s in segs if (s["id"] in only if only else about_subject(s, rc))]
    if not picked:
        raise ClipperError(f"{sdir.name}: no segments about {subject} (topics {rc['topics']}, "
                         f"figures {rc['figures']}); name some with --segments")
    words = load_words(sdir)
    blocks = []
    for s in picked:
        lines = transcript_lines(words, s["start_s"], s["end_s"])
        blocks.append(f'<segment id="{s["id"]}" title="{s["title"]}">\n{s.get("summary", "")}\n\n'
                      + "\n".join(lines) + "\n</segment>")
    user = "\n\n".join(blocks)
    print(f"{sdir.name}: {len(picked)} segments about {subject} "
          f"({', '.join(s['id'] for s in picked)}), ~{len(user) // 4 / 1000:.0f}k tokens to {rc['model']}")

    prompts = Prompts(cfg)
    prompts.version = rc["prompt_version"]
    prompts.vars.update(subject=subject, max_stories=rc["max_stories"])
    claude = Claude(cfg)
    archive_index.quiet_http()
    preflight(claude, rc["model"])
    out, _ = claude.json_call(rc["model"], prompts.get("receipts_stories"), user, STORIES_SCHEMA,
                              rc["effort"], stream=True)
    stories = (out or {}).get("stories", [])[:rc["max_stories"]]
    print(f"{len(stories)} stories (~${claude.cost:.2f} so far)")

    seen: set[tuple[str, int]] = set()  # one archive moment in one story only
    result = []
    for k, st in enumerate(stories, 1):
        print(f"\n[{k}] {st['story']}: {st['question']}")
        picks = archive_index.answer(cfg, subject, st["question"], None, st["before"] or None, claude)
        receipts, used = [], 0
        for h in picks:
            key = (h["item"], int(h["start"] // 30))
            if key in seen:
                continue
            seen.add(key)
            a, b, found = clip_bounds(adir / h["item"], h["start"], h["quote"], rc)
            ok = h["fit"] == "strong" or rc["maybe"]
            use = ok and found and used < rc["per_story"]
            used += use
            receipts.append({
                "id": f"r{k}{chr(96 + len(receipts) + 1)}", "use": use, "fit": h["fit"],
                "item": h["item"], "date": h["date"], "channel": h["channel"], "title": h["title"],
                "url": h["url"], "source": h["source"], "start": a, "end": b,
                "link": archive_index.link(h["url"], h["source"], a + 3),
                "quote": h["quote"], "why": h["why"], "quote_found": found})
        result.append({"id": f"r{k}", "use": bool(used), "story": st["story"], "now": st["now"],
                       "question": st["question"], "before": st["before"],
                       "segments": st["segments"], "receipts": receipts})

    p = out_dir(sdir) / "receipts.json"
    if p.exists():
        p.replace(p.with_name(f"receipts.{datetime.now():%Y%m%d-%H%M%S}.json"))
    data = {"stream": sdir.name, "subject": subject, "show": cfg["meta"]["show_name"],
            "found_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "cost_usd": round(claude.cost, 3), "title": "", "description": "", "stories": result}
    data["title"], data["description"] = default_text(data, cfg)
    save(sdir, data)
    print(f"\n~${claude.cost:.2f} in all; wrote {p}\n")
    show(sdir, data)


def default_text(data: dict, cfg: dict) -> tuple[str, str]:
    """Title and the description's opening line; `cut` adds the sources and chapters."""
    names = [s["story"] for s in data["stories"] if s["use"]]
    title = "The Quartering Receipts: " + ", ".join(names) if data["subject"] == "jer" \
        else f"{data['subject']} receipts: " + ", ".join(names)
    while len(title) > 100 and len(names) > 1:
        names.pop()
        title = title.rsplit(",", 1)[0]
    intro = (f"His own words, from his own channels, on the stories {data['show']} covered. "
             "Every clip links to the full video at that moment, so you can check the context.")
    return title[:100], intro


def show(sdir: Path, data: dict | None = None) -> None:
    data = data or load(sdir)
    print(f"{data['title']}\n")
    for st in data["stories"]:
        print(f"{st['id']} {'USE ' if st['use'] else 'skip'} {st['story']}  ({', '.join(st['segments'])})")
        print(f"     now: {st['now']}")
        for r in st["receipts"]:
            mark = "x" if r["use"] else " "
            warn = "" if r["quote_found"] else "  (quote not found in words: check the range)"
            print(f"  [{mark}] {r['id']} {r['fit']:<6} {r['date'] or '????-??-??'} {r['channel']} "
                  f"{clock(r['start'])}-{clock(r['end'])} ({r['end'] - r['start']:.0f} s){warn}")
            print(f"        {r['title'][:80]}")
            print(f"        \"{r['quote']}\"")
            print(f"        {r['link']}")
        print()
    if data.get("script"):
        print_script(data)
        print()
    if data.get("youtube"):
        print(f"uploaded {data['youtube']['privacy']}: {data['youtube']['url']}")


# ---------- cut ----------

def first_font(paths: list[str]) -> Path:
    for f in paths:
        if Path(f).exists():
            return Path(f)
    raise ClipperError(f"no font found among {paths}; set [receipts] fonts / text_fonts")


def esc(p: Path) -> str:
    """A path inside an ffmpeg filtergraph: C:/x -> 'C\\:/x'."""
    return "'" + p.resolve().as_posix().replace(":", "\\:") + "'"


def text_file(work: Path, name: str, text: str) -> Path:
    p = work / f"{name}.txt"
    p.write_bytes(text.encode("utf-8"))  # not write_text: drawtext draws Windows' \r as a blank line
    return p


def drawtext(font: Path, txt: Path, size: int, color: str, x: str, y: str, extra: str = "") -> str:
    return (f"drawtext=fontfile={esc(font)}:textfile={esc(txt)}:fontsize={size}:fontcolor={color}"
            f":x={x}:y={y}{extra}")


def ffmpeg(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=False,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(f"ffmpeg: {r.stderr.strip()[-800:]}")


def loudnorm(rc: dict) -> str:
    """Every part to one loudness: the reads and Jer's mics differ by 6 LU or more."""
    return f"loudnorm=I={rc['lufs']}:TP=-1.5:LRA=11"


def out_args(rc: dict) -> list[str]:
    return [*encode_args(rc["codec"], rc["crf"]), "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]


def download(cfg: dict, r: dict, dst: Path) -> None:
    from .preshow import ytdlp

    if dst.exists():
        return
    if r["source"] != "youtube":
        raise ClipperError(f"{r['id']}: only YouTube receipts can be cut so far ({r['url']})")
    tmp = dst.with_name(dst.stem + ".dl.mp4")
    args = ["--download-sections", f"*{r['start']:.2f}-{r['end']:.2f}", "--force-keyframes-at-cuts",
            "-f", cfg["receipts"]["format"], "--merge-output-format", "mp4", "--no-playlist",
            "-o", str(tmp), r["url"]]
    res = ytdlp(*args)
    cookies = cfg["paths"]["youtube_cookies"]
    err = res.stderr.lower()
    if res.returncode and ("confirm your age" in err or "age-restricted" in err) and cookies.is_file():
        res = ytdlp("--cookies", str(netscape_cookies(cookies)), *args)
    if res.returncode or not tmp.exists():
        raise RuntimeError(f"{r['id']} yt-dlp: {res.stderr.strip().splitlines()[-1:]}")
    tmp.replace(dst)


def still(work: Path, name: str, lines: list[tuple], rc: dict, bug: str, dst: Path,
          vo: Path | None = None) -> None:
    """A card: lines of (font, size, color, y, text) centered on dark grey, the corner bug,
    and the voice-over if any. It lasts [receipts] card_s, or the read plus vo_pad_s."""
    tag = text_file(work, "bug", bug)
    vf = [drawtext(font, text_file(work, f"{name}_{k}", text), size, color, "(w-text_w)/2", str(y),
                   ":line_spacing=14")
          for k, (font, size, color, y, text) in enumerate(lines)]
    vf.append(drawtext(lines[-1][0], tag, 30, "white@0.6", "w-text_w-48", "h-text_h-40"))
    dur = max(rc["card_s"], duration(vo) + rc["vo_pad_s"]) if vo else rc["card_s"]
    d = f"{dur:.2f}"
    audio = ["-i", str(vo), "-af", f"{loudnorm(rc)},aresample=48000,aformat=channel_layouts=stereo,apad"] if vo \
        else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    ffmpeg("-f", "lavfi", "-i", f"color=c=0x101010:s={W}x{H}:r={FPS}:d={d}", *audio, "-t", d,
           "-vf", ",".join(vf), "-map", "0:v", "-map", "1:a", *out_args(rc), str(dst))


def card(work: Path, st: dict, rc: dict, fonts: tuple[Path, Path], bug: str, dst: Path,
         vo: Path | None = None) -> None:
    big, small = fonts
    now = "NOW: " + "\n".join(textwrap.wrap(st["now"], 58))
    still(work, st["id"], [(big, 84, "0xFFD400", 200, "RECEIPTS"), (big, 120, "white", 330, st["story"].upper()),
                           (small, 44, "0xDDDDDD", 560, now)], rc, bug, dst, vo)


def nice_date(date: str | None) -> str:
    if not date:
        return "date unknown"
    d = datetime.fromisoformat(date)
    return f"{d:%b} {d.day}, {d.year}"


# ---------- voice-over ----------

SCRIPT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["open", "close", "stories"],
    "properties": {
        "open": {"type": "string"}, "close": {"type": "string"},
        "stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "vo", "receipts"],
            "properties": {"id": {"type": "string"}, "vo": {"type": "string"},
                           "receipts": {"type": "array", "items": {
                               "type": "object", "additionalProperties": False, "required": ["id", "vo"],
                               "properties": {"id": {"type": "string"}, "vo": {"type": "string"}}}}}}}},
}


def used(data: dict) -> list[tuple[dict, list[dict]]]:
    out = []
    for st in data["stories"]:
        rs = [r for r in st["receipts"] if r["use"]]
        if st["use"] and rs:
            out.append((st, rs))
    return out


def script(sdir: Path, cfg: dict) -> None:
    """Mother Moo's read for the used stories and receipts -> receipts.json (open, close,
    each story's and receipt's vo). Edit the lines there; `cut` voices what changed."""
    from .scoring import Claude, Prompts, preflight

    cfg = for_stream(cfg, sdir)
    rc = cfg["receipts"]
    data = load(sdir)
    adir = subject_dir(cfg, data["subject"])
    blocks = []
    for st, rs in used(data):
        parts = [f'<story id="{st["id"]}" name="{st["story"]}">\nNow: {st["now"]}']
        for r in rs:
            words = [json.loads(x) for x in (adir / r["item"] / "words.jsonl").read_text(encoding="utf-8").splitlines()]
            said = " ".join(w["word"] for w in words if r["start"] <= w["start"] < r["end"])
            parts.append(f'<receipt id="{r["id"]}" date="{r["date"]}" channel="{r["channel"]}" '
                         f'title="{r["title"]}">\n{said}\n</receipt>')
        blocks.append("\n".join(parts) + "\n</story>")
    if not blocks:
        raise ClipperError("no story has a receipt with use = true")
    prompts = Prompts(cfg)
    prompts.version = rc["script_prompt_version"]
    prompts.vars.update(subject=data["subject"], show_name=data["show"])
    claude = Claude(cfg)
    preflight(claude, rc["script_model"])
    out, _ = claude.json_call(rc["script_model"], prompts.get("receipts_script"), "\n\n".join(blocks),
                              SCRIPT_SCHEMA, rc["script_effort"])
    if not out:
        raise ClipperError("the model returned no script (refusal); write it in receipts.json by hand")
    lines = {s["id"]: s for s in out["stories"]}
    data["script"] = {"open": out["open"], "close": out["close"],
                      "model": rc["script_model"], "written_at": datetime.now(UTC).isoformat(timespec="seconds")}
    for st, rs in used(data):
        got = lines.get(st["id"], {})
        st["vo"] = got.get("vo", "")
        by = {r["id"]: r["vo"] for r in got.get("receipts", [])}
        for r in rs:
            r["vo"] = by.get(r["id"], "")
    save(sdir, data)
    print(f"script by {rc['script_model']} (~${claude.cost:.2f}):\n")
    print_script(data)


def print_script(data: dict) -> None:
    sc = data.get("script") or {}
    print(f"open: {sc.get('open', '')}")
    for st, rs in used(data):
        print(f"\n{st['id']} {st['story']}: {st.get('vo', '')}")
        for r in rs:
            print(f"  {r['id']} ({r['date']}): {r.get('vo', '')}")
    print(f"\nclose: {sc.get('close', '')}")


def voiced(cfg: dict, sdir: Path, text: str) -> Path | None:
    """The read of one line, voiced once per text, voice and settings (receipts/vo/)."""
    import hashlib

    from . import narrate

    if not text.strip():
        return None
    nc = narrate.settings(cfg)
    sig = json.dumps([text, nc["voice_id"], nc["tts_model"], nc["tts_settings"]], sort_keys=True)
    out = out_dir(sdir) / "vo" / f"{hashlib.sha1(sig.encode()).hexdigest()[:12]}.{nc['output_format'].split('_')[0]}"
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        narrate.speak(text, nc, out)
    return out


def clip(cfg: dict, adir: Path, r: dict, src: Path, fonts: tuple[Path, Path], bug: str,
         bleeper: Bleeper, dst: Path) -> int:
    from .scoring import load_words

    rc, work = cfg["receipts"], dst.parent
    big, small = fonts
    when = nice_date(r.get("date"))
    line1 = text_file(work, f"{r['id']}_l1", f"THEN: {r['channel'].upper()}  ·  {when.upper()}")
    line2 = text_file(work, f"{r['id']}_l2", bleeper.mask(r["title"])[:90])
    tag = text_file(work, "bug", bug)
    video = ",".join([
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease",
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2", "setsar=1", f"fps={FPS}",
        "drawbox=x=0:y=ih-176:w=iw:h=176:color=black@0.75:t=fill",
        "drawbox=x=0:y=ih-176:w=14:h=176:color=0xFFD400@1:t=fill",
        drawtext(big, line1, 58, "0xFFD400", "56", "h-158"),
        drawtext(small, line2, 36, "white", "58", "h-78"),
        drawtext(small, tag, 30, "white@0.6", "w-text_w-48", "40"),
    ]) + "[v]"
    spans = []
    if bleeper.on:
        spans = bleeper.intervals(load_words(adir / r["item"]), r["start"], r["end"], r["start"])
    graph = work / f"{r['id']}.filter.txt"
    if spans:
        bleeper.filter_script(spans, r["end"] - r["start"], graph)
        audio = graph.read_text(encoding="utf-8") + f";[aout]{loudnorm(rc)},aresample=48000[a]"
    else:
        audio = f"[0:a]{loudnorm(rc)},aresample=48000[a]"
    graph.write_text(video + ";" + audio, encoding="utf-8")
    ffmpeg("-i", str(src), "-/filter_complex", str(graph), "-map", "[v]", "-map", "[a]",
           *out_args(rc), str(dst))
    return len(spans)


def describe(data: dict, chapters: list[tuple[float, str]]) -> str:
    parts = [data["description"].strip(), ""]
    for st, rs in used(data):
        parts.append(f"{st['story']}: {st['now']}")
        for r in rs:
            parts.append(f"- {r['date'] or 'undated'}, {r['channel']}, \"{r['title']}\": {r['link']}")
        parts.append("")
    if len(chapters) >= 3:
        parts += ["Chapters:", *(f"{clock(t)} {name}" for t, name in chapters), ""]
    return "\n".join(parts).strip()


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def cut(sdir: Path, cfg: dict) -> Path:
    cfg = for_stream(cfg, sdir)
    rc = cfg["receipts"]
    data = load(sdir)
    adir = subject_dir(cfg, data["subject"])
    work = out_dir(sdir) / "work"
    work.mkdir(parents=True, exist_ok=True)
    fonts = (first_font(rc["fonts"]), first_font(rc["text_fonts"]))
    bug = rc["bug"] or cfg["scoring"]["channel_name"]
    bleeper = Bleeper(cfg["bleep"])
    big, small = fonts
    sc = data.get("script") or {}
    parts, chapters, t, t0 = [], [], 0.0, time.time()

    def add(p: Path) -> None:
        nonlocal t
        parts.append(p)
        t += duration(p)

    if not used(data):
        raise ClipperError("no story has a receipt with use = true")
    if vo := voiced(cfg, sdir, sc.get("open", "")):
        chapters.append((t, "Open"))
        still(work, "open", [(big, 150, "0xFFD400", 360, "RECEIPTS"),
                             (small, 48, "white", 560, f"for {data['show']}")], rc, bug, work / "open.mp4", vo)
        add(work / "open.mp4")
    for st, rs in used(data):
        chapters.append((t, st["story"]))
        c = work / f"{st['id']}_card.mp4"
        card(work, st, rc, fonts, bug, c, voiced(cfg, sdir, st.get("vo", "")))
        add(c)
        for r in rs:
            if vo := voiced(cfg, sdir, r.get("vo", "")):
                lead = work / f"{r['id']}_lead.mp4"
                still(work, f"{r['id']}_lead", [(big, 64, "0xFFD400", 380, "THEN"),
                                                (big, 130, "white", 470, nice_date(r["date"]).upper()),
                                                (small, 44, "0xDDDDDD", 640, r["channel"])], rc, bug, lead, vo)
                add(lead)
            src = work / f"{r['item']}_{r['start']:.2f}-{r['end']:.2f}.mp4"
            print(f"{r['id']} {r['date']} {r['channel']} {clock(r['start'])}-{clock(r['end'])}", flush=True)
            download(cfg, r, src)
            dst = work / f"{r['id']}.mp4"
            n = clip(cfg, adir, r, src, fonts, bug, bleeper, dst)
            if n:
                print(f"  {n} bleeped")
            add(dst)
    if vo := voiced(cfg, sdir, sc.get("close", "")):
        still(work, "close", [(big, 150, "0xFFD400", 360, "RECEIPTS"),
                              (small, 48, "white", 560, bug)], rc, bug, work / "close.mp4", vo)
        add(work / "close.mp4")
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    final = out_dir(sdir) / "receipts.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart",
           str(final))
    desc = describe(data, chapters)
    (out_dir(sdir) / "description.txt").write_text(f"{data['title']}\n\n{desc}\n", encoding="utf-8")
    data.update(cut_at=datetime.now(UTC).isoformat(timespec="seconds"), duration_s=round(t, 1))
    save(sdir, data)
    print(f"{final} ({clock(t)}, {len(chapters)} stories, {time.time() - t0:.0f} s); "
          f"title and description in {out_dir(sdir) / 'description.txt'}")
    return final


# ---------- upload ----------

def upload(sdir: Path, cfg: dict, again: bool = False) -> None:
    from .youtube import clean, service, upload_video

    cfg = for_stream(cfg, sdir)
    data = load(sdir)
    final = out_dir(sdir) / "receipts.mp4"
    if not final.exists() or not data.get("cut_at"):
        raise ClipperError(f"cut it first: clipper receipts cut {sdir.name}")
    if data.get("youtube") and not again:
        raise ClipperError(f"already uploaded: {data['youtube']['url']} (--again for a second copy)")
    if final.stat().st_mtime < datetime.fromisoformat(data["cut_at"]).timestamp() - 5:
        raise ClipperError("receipts.mp4 is older than the last cut; cut again")
    yc = cfg["youtube"]
    mask = Bleeper({**cfg["bleep"], "mode": "bleep"}).mask
    desc_file = (out_dir(sdir) / "description.txt").read_text(encoding="utf-8")
    description = desc_file.split("\n\n", 1)[1] if "\n\n" in desc_file else desc_file
    body = {
        "snippet": {"title": clean(mask(data["title"]), 100), "description": clean(mask(description), 5000),
                    "tags": ["The Quartering", "Jeremy Hambly", "receipts", data["show"]],
                    "categoryId": yc["category_id"], "defaultLanguage": yc["language"],
                    "defaultAudioLanguage": yc["language"]},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": yc["made_for_kids"]},
    }
    print(f"uploading {final.name} private: {body['snippet']['title']}", flush=True)
    vid = upload_video(service(cfg["paths"]), final, body, False)
    data["youtube"] = {"id": vid, "url": f"https://youtu.be/{vid}", "privacy": "private",
                       "studio": f"https://studio.youtube.com/video/{vid}/edit",
                       "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds")}
    save(sdir, data)
    print(f"  {data['youtube']['url']} (private; Studio: {data['youtube']['studio']})")
