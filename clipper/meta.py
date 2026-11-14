"""Upload metadata: SEO-ready title, description, chapters, hashtags, tags, playlist and
captions for every cut asset (clips/, segments/, compilations/).

Writes metadata/<folder>/<video>.json and <video>.srt (beside the video, so players load
it). These are drafts: review
(Kitsu, Discord) is where they get edited and approved, and upload reads the result
from there.

  one call     titles (only reworked to put the searched name first), description lead,
               clip bodies, hashtags, tags, playlist, for every asset at once
  per segment  chapters from the transcript, for segments longer than chapters_min_s;
               compilations reuse their segments' chapters
  captions     SRT from the WhisperX word timings, offset to each video's own timeline
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import jsonstate
from .config import for_stream
from .errors import ClipperError
from .llm import Claude, estimate_lines, preflight, tokens
from .prompts import Prompts
from .timefmt import clock, hms
from .transcript import load_words, snap, transcript_lines

META_SCHEMA = {
    "type": "object",
    "properties": {
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "lead": {"type": "string"},
                    "body": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "playlist": {"type": "string"},
                },
                "required": ["id", "title", "lead", "body", "hashtags", "tags", "playlist"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["assets"],
    "additionalProperties": False,
}

CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"start_s": {"type": "number"}, "title": {"type": "string"}},
                "required": ["start_s", "title"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["chapters"],
    "additionalProperties": False,
}


# ---------- assets ----------

def load_assets(stream_dir: Path) -> list[dict]:
    """Every cut video with its source time range(s). parts = [(src_start, src_end, offset)]."""
    out = []
    for side in sorted((stream_dir / "clips").glob("*.json")):
        d = jsonstate.load(side)
        out.append({"kind": "clip", "id": d["id"], "video": side.with_suffix(".mp4"),
                    "title": d["title"], "hook": d.get("hook", ""), "body": "",
                    "summary": d.get("reason", ""), "topic": "", "figures": [],
                    "parts": [(d["in_s"], d["out_s"], 0.0)]})
    segs = {}
    for side in sorted((stream_dir / "segments").glob("*.json")):
        d = jsonstate.load(side)
        a = d.get("cut_start_s", d["start_s"])
        segs[d["id"]] = d
        out.append({"kind": "segment", "id": d["id"], "video": side.with_suffix(".mp4"),
                    "title": d["title"], "body": d["description"], "summary": d["summary"],
                    "topic": d["topic"], "figures": d.get("figures", []),
                    "parts": [(a, d["end_s"], 0.0)], "chapters": d.get("chapters") or []})
    for side in sorted((stream_dir / "compilations").glob("*.json")):
        d = jsonstate.load(side)
        offsets = {c["id"]: c["start_s"] for c in d["chapters"]}
        # exact spans and offsets from `clipper cut`; compilations cut before them: the chapters
        parts = ([tuple(p) for p in d["parts"]] if d.get("parts") else
                 [(segs[s].get("cut_start_s", segs[s]["start_s"]), segs[s]["end_s"], offsets[s])
                  for s in d["segments"]])
        out.append({"kind": "compilation", "id": f"comp-{side.stem}", "video": side.with_suffix(".mp4"),
                    "title": d["title"], "body": d["description"], "summary": "",
                    "topic": d["topic"], "segments": d["segments"],
                    "segment_titles": {c["id"]: c["title"] for c in d["chapters"]},
                    "figures": sorted({f for s in d["segments"] for f in segs[s].get("figures", [])}),
                    "parts": parts})
    return out


def duration(asset: dict) -> float:
    return sum(b - a for a, b, _ in asset["parts"])


# ---------- captions ----------

def srt_time(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def wrap(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    cut = text.rfind(" ", 0, len(text) // 2 + width // 4)
    cut = cut if cut > 0 else text.find(" ")
    return text[:cut] + "\n" + text[cut + 1:] if cut > 0 else text


def make_srt(words: list[dict], parts: list[tuple], mc: dict, mask=lambda s: s) -> str:
    """Cues of up to two lines of srt_line_chars, broken at pauses, sentence ends and
    srt_max_s, on each video's own timeline."""
    cues = []

    def flush(cur: list[dict], a: float, off: float) -> None:
        if cur:
            cues.append((cur[0]["start"] - a + off, cur[-1]["end"] - a + off,
                         wrap(mask(" ".join(w["word"] for w in cur)), mc["srt_line_chars"])))
            cur.clear()

    for a, b, off in parts:
        cur: list[dict] = []
        for w in words:
            if w["start"] < a or w["end"] > b:
                continue
            if cur and (w["start"] - cur[-1]["end"] > 0.8
                        or len(" ".join(x["word"] for x in cur)) + len(w["word"]) > 2 * mc["srt_line_chars"]
                        or w["end"] - cur[0]["start"] > mc["srt_max_s"]
                        or cur[-1]["word"][-1:] in ".?!"):
                flush(cur, a, off)
            cur.append(w)
        flush(cur, a, off)
    return "".join(f"{i}\n{srt_time(s)} --> {srt_time(max(e, s + 0.3))}\n{t}\n\n"
                   for i, (s, e, t) in enumerate(cues, 1))


# ---------- chapters ----------

def chapter_user(asset: dict, words: list[dict]) -> str:
    """The chapters call's input: the segment's title, description and transcript."""
    a, b, _ = asset["parts"][0]
    return (f"Title: {asset['title']}\nDescription: {asset['body']}\n\n<transcript>\n"
            + "\n".join(transcript_lines(words, a, b)) + "\n</transcript>")


def segment_chapters(asset: dict, words: list[dict], mc: dict, claude: Claude,
                     prompts: Prompts) -> list[dict]:
    a, b, _ = asset["parts"][0]
    out, _ = claude.json_call(mc["model"], prompts.get("chapters"), chapter_user(asset, words),
                              CHAPTER_SCHEMA, mc["effort"])
    if not out:
        return []
    span = [w for w in words if a <= w["start"] < b]
    chapters = []
    for c in sorted(out["chapters"], key=lambda c: c["start_s"]):
        t = 0.0 if not chapters else snap(span, c["start_s"], "start") - a
        if chapters and t - chapters[-1]["t"] < mc["chapter_min_gap_s"]:
            continue
        chapters.append({"t": round(max(t, 0.0), 1), "title": c["title"].strip()})
    return chapters if len(chapters) >= 3 else []


# ---------- description ----------

def credit_line(meta: dict, mc: dict, cfg: dict) -> str:
    try:
        dt = datetime.fromisoformat(meta.get("started_at", ""))
        date = f"{dt:%b} {dt.day}, {dt.year}"
    except ValueError:
        date = ""
    if meta.get("kind") == "story":  # other people's videos (#84): each one, at its moment
        # the time last: a line starting with one would read to YouTube as a second chapter list
        lines = [f"- {s['channel']}{', ' + s['date'] if s['date'] else ''}: {s['link']} (at {clock(s['t'])})"
                 for s in meta.get("sources", [])]
        return "\n".join([cfg["story"]["credit"].format(show=mc["show_name"], date=date), *lines])
    fmt = cfg["preshow"]["credit"] if meta.get("kind") == "preshow" else mc["credit"]
    return fmt.format(show=mc["show_name"], date=date, channel=cfg["kick"]["channel"],
                      url=meta.get("url", ""))


def description(lead: str, body: str, chapters: list[dict], credit: str,
                hashtags: list[str]) -> str:
    parts = [lead.strip(), body.strip()]
    if chapters:
        parts.append("\n".join(f"{hms(c['t'])} {c['title']}" for c in chapters))
    parts += [credit, " ".join(hashtags)]
    return "\n\n".join(p for p in parts if p)


# ---------- stage ----------

def captions_only(stream_dir: Path, cfg: dict) -> None:
    """Rewrite every .srt and re-mask existing metadata text, without model calls
    (e.g. after changing [bleep] patterns)."""
    cfg = for_stream(cfg, stream_dir)
    from .bleep import Bleeper

    bleeper, words = Bleeper(cfg["bleep"]), load_words(stream_dir)
    mask = bleeper.mask if bleeper.mode != "off" else (lambda s: s)
    for x in load_assets(stream_dir):
        x["video"].with_suffix(".srt").write_text(make_srt(words, x["parts"], cfg["meta"], mask),
                                                  encoding="utf-8")
        side = stream_dir / "metadata" / x["video"].parent.name / f"{x['video'].stem}.json"
        if side.exists():
            d = jsonstate.load(side)
            for k in ("title", "description", "lead", "body"):
                d[k] = mask(d[k])
            d["chapters"] = [{**c, "title": mask(c["title"])} for c in d["chapters"]]
            jsonstate.write(side, d)
    print("captions rewritten" + (", text masked" if bleeper.mode != "off" else ""))


def draft_path(stream_dir: Path, asset: dict) -> Path:
    # Own folder: review.py reads every *.json beside the videos as a cut sidecar.
    return stream_dir / "metadata" / asset["video"].parent.name / f"{asset['video'].stem}.json"


def asset_lines(assets: list[dict], words: list[dict], drafted: dict[str, dict]) -> list[str]:
    """The metadata call's input: one <asset> per video (a clip with its transcript), then
    the playlists the already drafted videos use."""
    lines = []
    for x in assets:
        text = ""
        if x["kind"] == "clip":
            text = " ".join(transcript_lines(words, *x["parts"][0][:2]))[:1500]
        lines.append(
            f"<asset id=\"{x['id']}\" kind=\"{x['kind']}\" minutes=\"{duration(x) / 60:.1f}\">\n"
            f"title: {x['title']}\n"
            + (f"topic: {x['topic']}\n" if x["topic"] else "")
            + (f"people: {', '.join(x['figures'])}\n" if x["figures"] else "")
            + (f"description: {x['body']}\n" if x["body"] else f"editor note: {x['summary']}\n")
            + (f"transcript: {text}\n" if text else "") + "</asset>")
    used = sorted({d["playlist"] for d in drafted.values() if d.get("playlist")})
    if used:
        lines.append("Playlists this episode's other videos already use (reuse the name for "
                     "the same subject): " + "; ".join(used))
    return lines


def generate(stream_dir: Path, mc: dict, assets: list[dict], lines: list[str],
             long_segs: list[dict], words: list[dict], claude: Claude,
             prompts: Prompts) -> tuple[dict, dict, Path]:
    """The metadata call and a chapters call per long segment, in parallel:
    (metadata, chapters by segment id, the checkpoint file)."""
    # #75: every call that succeeded is kept here until the drafts are written, so one failed
    # chapters call doesn't throw away the big metadata call (or the other chapters).
    ckpt, saved = checkpoint(stream_dir, mc, assets)
    errors = []
    with ThreadPoolExecutor(mc["concurrency"]) as pool:
        f_meta = None if saved["gen"] else pool.submit(
            claude.json_call, mc["model"], prompts.get("meta"), "\n\n".join(lines), META_SCHEMA,
            mc["effort"], True, True)
        f_ch = {x["id"]: pool.submit(segment_chapters, x, words, mc, claude, prompts)
                for x in long_segs if x["id"] not in saved["chapters"]}
        for k, f in f_ch.items():
            try:
                saved["chapters"][k] = f.result()
            except Exception as e:  # noqa: BLE001
                errors.append(f"chapters {k}: {type(e).__name__}: {e}")
        if f_meta:
            try:
                saved["gen"] = f_meta.result()[0]
            except Exception as e:  # noqa: BLE001
                errors.append(f"metadata: {type(e).__name__}: {e}")
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    jsonstate.write(ckpt, saved)
    if errors:
        raise ClipperError("meta: " + "; ".join(errors)[:600] + f"\n(what worked is kept in {ckpt.name}; "
                         "rerun clipper meta to finish)")
    if saved["gen"] is None:
        raise ClipperError("meta: model refused")
    return saved["gen"], dict(saved["chapters"]), ckpt


def compilation_chapters(assets: list[dict], chapters: dict[str, list],
                         drafted: dict[str, dict]) -> None:
    """Compilations: each segment's title at its start, then that segment's own chapters
    (this run's, else its draft's). Added to chapters."""
    for x in assets:
        if x["kind"] == "compilation":
            ch = []
            for sid, (_, _, off) in zip(x["segments"], x["parts"]):
                ch.append({"t": round(off, 1), "title": x["segment_titles"][sid]})
                seg_ch = chapters.get(sid) or drafted.get(sid, {}).get("chapters", [])
                ch += [{"t": round(off + c["t"], 1), "title": c["title"]} for c in seg_ch[1:]]
            chapters[x["id"]] = ch


def draft(x: dict, g: dict, ch: list[dict], credit: str, playlist: str, mc: dict,
          mask=lambda s: s) -> dict:
    """An asset's upload draft from what the model wrote for it (g); playlist: a fixed one
    (pre-shows), else the model's. problems: what YouTube would refuse."""
    tags = [t.strip() for t in g["hashtags"]][:3]
    desc = mask(description(g["lead"], g["body"] or x["body"], ch, credit, tags))
    out = {"kind": x["kind"], "id": x["id"], "file": x["video"].name,
           "title": mask(g["title"].strip()), "title_original": x["title"],
           "description": desc, "lead": g["lead"].strip(), "body": g["body"] or x["body"],
           "chapters": ch, "credit": credit, "hashtags": tags, "tags": g["tags"],
           "playlist": playlist or g["playlist"].strip(), "duration_s": round(duration(x), 1),
           "srt": x["video"].with_suffix(".srt").name,
           "model": mc["model"], "prompt_version": mc["prompt_version"]}
    problems = []
    if len(out["title"]) > 100:
        problems.append(f"title {len(out['title'])} chars")
    if len(desc) > 5000:
        problems.append(f"description {len(desc)} chars")
    out["problems"] = problems
    return out


def checkpoint(stream_dir: Path, mc: dict, assets: list[dict]) -> tuple[Path, dict]:
    """The metadata checkpoint (#75): the calls an earlier run already paid for, when its asset
    ids are these; else a fresh one. (its path, {ids, gen, chapters})."""
    ckpt = stream_dir / "metadata" / f".checkpoint_{mc['prompt_version']}.json"
    ids = sorted(x["id"] for x in assets)
    saved = jsonstate.read(ckpt) or {}
    if saved.get("ids") != ids:
        saved = {"ids": ids, "gen": None, "chapters": {}}
    return ckpt, saved


def estimate(stream_dir: Path, cfg: dict, mc: dict, prompts: Prompts, assets: list[dict], lines: list[str],
             long_segs: list[dict], words: list[dict]) -> list[str]:
    """`--dry-run`: the metadata call and a chapters call per long segment, priced with [llm]
    prices, nothing called. What the checkpoint of a failed run already holds isn't counted,
    as `generate` won't ask for it again."""
    ckpt, saved = checkpoint(stream_dir, mc, assets)
    rows = []
    if not saved["gen"]:
        rows.append(("metadata", mc["model"], 1, tokens(prompts.get("meta") + "\n\n".join(lines)),
                     250 * len(assets)))
    todo = [x for x in long_segs if x["id"] not in saved["chapters"]]
    if todo:
        rows.append(("chapters", mc["model"], len(todo),
                     sum(tokens(prompts.get("chapters") + chapter_user(x, words)) for x in todo), 300 * len(todo)))
    held = (len(long_segs) - len(todo)) + (1 if saved["gen"] else 0)
    note = [f"  {held} call(s) already in {ckpt.name} from an earlier run: not counted"] if held else []
    if not rows:
        return note + ["  nothing to pay: a rerun writes the drafts from the checkpoint"]
    return estimate_lines(cfg, rows) + note


def run(stream_dir: Path, cfg: dict, missing: bool = False, dry_run: bool = False) -> list[dict]:
    """missing: only the videos with no draft yet (the worker's second pass, after the
    clips went to review); the model is told which playlists the drafted ones use.
    dry_run: print the estimate and write nothing."""
    cfg = for_stream(cfg, stream_dir)
    mc = cfg["meta"]
    prompts = Prompts(cfg)
    prompts.vars.update(show_name=mc["show_name"], min_chapters=mc["min_chapters"],
                        max_chapters=mc["max_chapters"])
    meta = jsonstate.load(stream_dir / "meta.json")
    words = load_words(stream_dir)
    assets = load_assets(stream_dir)
    from .bleep import Bleeper
    bleeper = Bleeper({**cfg["bleep"], "patterns": cfg["bleep"]["patterns"]
                       if cfg["bleep"]["mode"] != "off" else []})
    if not assets:
        raise ClipperError("no cut assets; run `clipper cut` first")
    drafted: dict[str, dict] = {}
    if missing:
        drafted = {x["id"]: jsonstate.load(draft_path(stream_dir, x))
                   for x in assets if draft_path(stream_dir, x).exists()}
        assets = [x for x in assets if x["id"] not in drafted]
        if not assets:
            print(f"all {len(drafted)} videos have a draft")
            return []
        print(f"{len(assets)} videos without a draft ({len(drafted)} kept)")

    lines = asset_lines(assets, words, drafted)
    # a story's segment has its chapters already (a part each, #84): no transcript call for it
    long_segs = [x for x in assets if x["kind"] == "segment" and duration(x) >= mc["chapters_min_s"]
                 and not x.get("chapters")]
    if dry_run:
        print("\n".join(["estimate:"] + estimate(stream_dir, cfg, mc, prompts, assets, lines, long_segs, words)))
        return []
    claude = Claude(cfg)
    preflight(claude, mc["model"])
    gen, chapters, ckpt = generate(stream_dir, mc, assets, lines, long_segs, words, claude, prompts)
    chapters.update({x["id"]: x["chapters"] for x in assets if x.get("chapters")})
    by_id = {g["id"]: g for g in gen["assets"]}
    compilation_chapters(assets, chapters, drafted)

    credit = credit_line(meta, mc, cfg)
    fixed = {"preshow": cfg["preshow"]["playlist"], "story": cfg["story"]["playlist"]}.get(meta.get("kind"), "")
    playlist = fixed.format(show=mc["show_name"])
    results = []
    for x in assets:
        g = by_id.get(x["id"])
        if not g:
            print(f"  warning: no metadata returned for {x['id']}")
            continue
        out = draft(x, g, chapters.get(x["id"], []), credit, playlist, mc, bleeper.mask)
        dst = draft_path(stream_dir, x)
        dst.parent.mkdir(parents=True, exist_ok=True)
        jsonstate.write(dst, out)
        x["video"].with_suffix(".srt").write_text(make_srt(words, x["parts"], mc, bleeper.mask),
                                                  encoding="utf-8")
        results.append(out)
    ckpt.unlink(missing_ok=True)

    print(f"{len(results)} assets, {len(long_segs)} with transcript chapters; cost ${claude.cost:.3f}")
    for r in results:
        changed = "" if r["title"] == r["title_original"] else f"   (was: {r['title_original']})"
        flag = f"  !! {', '.join(r['problems'])}" if r["problems"] else ""
        print(f"{r['kind']:<11} {r['id']:<22} [{r['playlist']}] {r['title']}{changed}"
              f"  {len(r['chapters'])} ch{flag}")
    return results
