"""Topic segments: split a stream into long subject-level clips ("the DSP segment").

One call over the whole transcript returns contiguous segments with a title, topic,
kind, how much played-video audio they contain, and whether they stand alone. Segments
that pass the [segments] filters are marked `cut: true`. Topics with at least
compile_min_segments cut segments also get a compilation (every segment on that topic,
in order) with its own title and description.

The split is cached in segments.json; rerunning re-applies the filters and fills in any
missing compilation text without paying for a new split (--force re-splits).
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

from . import jsonstate
from .config import for_stream
from .errors import ClipperError
from .scoring import Claude, Prompts, load_words, preflight, snap, transcript_lines
from .timefmt import hms

KINDS = ["intro", "commentary", "reaction", "call", "gambling", "ad_read", "break",
         "outro", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "title": {"type": "string"},
                    "topic": {"type": "string"},
                    "figures": {"type": "array", "items": {"type": "string"}},
                    "kind": {"type": "string", "enum": KINDS},
                    "source_share": {"type": "string", "enum": ["none", "some", "most"]},
                    "standalone": {"type": "boolean"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["start_s", "end_s", "title", "topic", "figures", "kind",
                             "source_share", "standalone", "summary", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["segments"],
    "additionalProperties": False,
}

COMPILATION_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
    "required": ["title", "description"],
    "additionalProperties": False,
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def apply_filters(segs: list[dict], seg_cfg: dict) -> None:
    allowed = set(seg_cfg["source_share"])
    for s in segs:
        reasons = []
        if not s["standalone"]:
            reasons.append("not standalone")
        if s["kind"] in seg_cfg["skip_kinds"]:
            reasons.append(f"kind {s['kind']}")
        if s["source_share"] not in allowed:
            reasons.append(f"source_share {s['source_share']}")
        if not seg_cfg["min_s"] <= s["duration_s"] <= seg_cfg["max_s"]:
            reasons.append(f"{s['duration_s'] / 60:.0f} min")
        s["cut"] = not reasons
        s["skip_reasons"] = reasons


def split(stream_dir: Path, cfg: dict, claude: Claude, prompts: Prompts) -> dict:
    seg_cfg = cfg["segments"]
    words = load_words(stream_dir)
    transcript = "\n".join(transcript_lines(words, 0, words[-1]["end"] + 1))
    out, meta = claude.json_call(seg_cfg["model"], prompts.get("segments"), transcript, SCHEMA,
                                 seg_cfg["effort"], fallback=True, stream=True)
    if out is None:
        raise ClipperError(f"segments: model refused ({meta.get('refusal')})")
    segs = sorted(out["segments"], key=lambda s: s["start_s"])
    for i, s in enumerate(segs, 1):
        s["id"] = f"s{i:02d}"
        s["start_s"] = snap(words, s["start_s"], "start")
        s["end_s"] = snap(words, s["end_s"], "end")
        s["duration_s"] = round(s["end_s"] - s["start_s"], 1)
        s["file"] = f"{s['id']}_{slug(s['topic'])}.mp4"
    gaps = [(a["id"], b["id"], round(b["start_s"] - a["end_s"], 1))
            for a, b in pairwise(segs) if abs(b["start_s"] - a["end_s"]) > 30]
    return {"stream_id": stream_dir.name, "model": seg_cfg["model"],
            "prompt_version": prompts.version, "api": meta, "gaps": gaps, "segments": segs}


def compilations(data: dict, cfg: dict, claude: Claude, prompts: Prompts) -> list[dict]:
    seg_cfg = cfg["segments"]
    known = {c["topic"]: c for c in data.get("compilations", [])}
    by_topic: dict[str, list[dict]] = {}
    for s in data["segments"]:
        if s["cut"]:
            by_topic.setdefault(s["topic"], []).append(s)
    out = []
    for topic, segs in by_topic.items():
        if len(segs) < seg_cfg["compile_min_segments"]:
            continue
        ids = [s["id"] for s in segs]
        prev = known.get(topic)
        if prev and prev["segments"] == ids:
            out.append(prev)
            continue
        user = f"Subject: {topic}\n\n" + "\n\n".join(
            f"<segment id=\"{s['id']}\" minutes=\"{s['duration_s'] / 60:.0f}\">\n"
            f"title: {s['title']}\ndescription: {s['description']}\n</segment>" for s in segs)
        text, _ = claude.json_call(seg_cfg["model"], prompts.get("compilation"), user,
                                   COMPILATION_SCHEMA, seg_cfg["effort"], fallback=True)
        if text is None:
            text = {"title": f"{topic}: every segment", "description": ""}
        out.append({"topic": topic, "segments": ids, "file": f"{slug(topic)}.mp4", **text})
    return out


def run(stream_dir: Path, cfg: dict, force: bool = False) -> list[dict]:
    from . import preshow

    if preshow.is_preshow(stream_dir):  # one whole-video segment, no split
        return preshow.write_segments(stream_dir, cfg, force=force)
    cfg = for_stream(cfg, stream_dir)
    seg_cfg = cfg["segments"]
    prompts = Prompts(cfg)
    prompts.version = seg_cfg["prompt_version"]
    prompts.vars["min_aside_s"] = seg_cfg["min_aside_s"]
    path = stream_dir / "segments.json"
    claude = Claude(cfg)

    if path.exists() and not force:
        data = jsonstate.load(path)
    else:
        preflight(claude, seg_cfg["model"])
        data = split(stream_dir, cfg, claude, prompts)
    segs = data["segments"]
    apply_filters(segs, seg_cfg)
    data["compilations"] = compilations(data, cfg, claude, prompts)
    data["cost_usd"] = round(data.get("cost_usd", 0) + claude.cost, 4)
    jsonstate.write(path, data)

    print(f"{len(segs)} segments, {sum(s['cut'] for s in segs)} to cut; "
          f"this run ${claude.cost:.3f}")
    if data["gaps"]:
        print(f"gaps/overlaps over 30 s: {data['gaps']}")
    for s in segs:
        mark = "CUT " if s["cut"] else "    "
        why = "" if s["cut"] else f"  ({', '.join(s['skip_reasons'])})"
        print(f"{mark}{s['id']} {hms(s['start_s'])}-{hms(s['end_s'])} {s['duration_s'] / 60:5.1f}m "
              f"[{s['kind']}, src {s['source_share']}] {s['topic']}: {s['title']}{why}")
    for c in data["compilations"]:
        mins = sum(s["duration_s"] for s in segs if s["id"] in c["segments"]) / 60
        print(f"COMPILATION {c['topic']} ({', '.join(c['segments'])}, {mins:.0f} min): {c['title']}")
    return segs
