"""Vertical Shorts: each Pass B pick's short window as a 1080x1920 video.

Usage:
    clipper shorts <stream> [--ids t33,w19] [--layout watch] [--force]

For every pick in candidates.json (pass_b) this renders shorts/v<NN>_<id>_<slug>.mp4:
  1. the clip's window (in_s..out_s; [shorts] window = "short" uses scoring's 15-60 s
     short_in_s..short_out_s instead), widened by whole transcript lines to at least min_s
     (two lines of setup before for each line after, inside the clip's topic segment),
     at most max_s, snapped to word gaps with pad_s of room, cut with [bleep] applied;
  2. a layout: a stack of panels, each a crop of the source frame scaled to the full
     width, e.g. the video being watched on top and the webcams under it. Layouts are
     per show ([shows.<key>.shorts.layouts]); a vision call on a few frames picks one per
     Short, or set it: --layout, the Kitsu "Layout" column, or "layout" in shorts.json;
  3. burned-in captions from words.jsonl, a few words at a time with the spoken word
     highlighted, masked like every other caption ([bleep]);
  4. audio normalized to [shorts] lufs (two-pass loudnorm) with short fades.
Writes shorts.json (per-Short state: window, layout, how it was chosen) and an upload
metadata draft per Short in metadata/shorts/ (the clip's draft plus #Shorts). Unchanged
Shorts are skipped unless --force.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from .bleep import Bleeper
from .config import for_stream
from .cut import encode_args, ffmpeg_cut, keyframe_before, source_file
from .scoring import Claude, load_words
from .segments import slug

CENTER = {"description": "anything else: the middle of the frame",
          "panels": [{"src": [0.0, 0.0, 1.0, 1.0]}], "caption_y": 0.72}


# ---------- window ----------

def snap_window(words: list[dict], a: float, b: float, pad: float) -> tuple[float, float]:
    """Move a..b out to word gaps: start pad before the first word that ends after a, end
    pad after the last word that starts before b, never into a neighbouring word."""
    inside = [i for i, w in enumerate(words) if w["end"] > a and w["start"] < b]
    if not inside:
        return a, b
    i, j = inside[0], inside[-1]
    start = words[i]["start"] - pad
    if i > 0:
        start = max(start, (words[i - 1]["end"] + words[i]["start"]) / 2)
    end = words[j]["end"] + pad
    if j + 1 < len(words):
        end = min(end, (words[j]["end"] + words[j + 1]["start"]) / 2)
    return max(0.0, round(start, 3)), round(end, 3)


def grow_window(words: list[dict], a: float, b: float, min_s: float, max_s: float,
                lo: float, hi: float) -> tuple[float, float]:
    """Widen a..b to at least min_s by whole transcript lines (WhisperX segments): two
    earlier lines (setup) for each later one, never past lo..hi or longer than max_s."""
    spans: dict[int, list[float]] = {}
    for w in words:
        s = spans.setdefault(w["seg"], [w["start"], w["end"]])
        s[0], s[1] = min(s[0], w["start"]), max(s[1], w["end"])
    lines = sorted(tuple(v) for v in spans.values())
    before = [s for s in lines if s[1] <= a + 0.05 and s[0] >= lo][::-1]  # nearest first
    after = [s for s in lines if s[0] >= b - 0.05 and s[1] <= hi]
    turn = 0
    while b - a < min_s and (before or after):
        take_before = before and (turn % 3 != 2 or not after)
        s = before[0] if take_before else after[0]
        na, nb = (s[0], b) if take_before else (a, s[1])
        if nb - na > max_s:
            (before if take_before else after).clear()
            continue
        a, b = na, nb
        (before if take_before else after).pop(0)
        turn += 1
    return a, b


# ---------- layout ----------

def layouts_of(sc: dict) -> dict[str, dict]:
    out = dict(sc.get("layouts") or {})
    out.setdefault("center", CENTER)
    return out


def panel_crops(layout: dict, W: int, H: int, out_w: int, out_h: int) -> list[tuple]:
    """(x, y, w, h, panel_h) per panel: the panel's src box (fractions of the frame),
    cut down to the aspect of its slot (out_w x panel_h) around its focus point. Panels
    without an "h" share what's left of out_h."""
    panels = layout["panels"]
    fixed = sum(p.get("h", 0) for p in panels)
    free = [p for p in panels if not p.get("h")]
    rest = (out_h - fixed) // max(1, len(free))
    crops = []
    for p in panels:
        ph = p.get("h") or rest
        x, y, w, h = (p["src"][0] * W, p["src"][1] * H, p["src"][2] * W, p["src"][3] * H)
        fx, fy = p.get("focus", [0.5, 0.5])
        aspect = out_w / ph
        if w / h > aspect:  # too wide: keep the height, narrow around the focus
            nw = h * aspect
            x += (w - nw) * fx
            w = nw
        else:
            nh = w / aspect
            y += (h - nh) * fy
            h = nh
        even = lambda v: int(v) // 2 * 2  # noqa: E731
        crops.append((even(x), even(y), even(w), even(h), ph))
    if sum(c[4] for c in crops) != out_h:
        raise SystemExit(f"layout panels add up to {sum(c[4] for c in crops)} px, not {out_h}")
    return crops


def grab_frames(video: Path, dur: float, n: int, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k in range(n):
        t = dur * (k + 1) / (n + 1)
        p = out_dir / f"{video.stem}_{k}.jpg"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}",
                        "-i", str(video), "-frames:v", "1", "-vf", "scale=768:-2", str(p)], check=True)
        paths.append(p)
    return paths


def pick_layout(claude: Claude, sc: dict, layouts: dict, frames: list[Path]) -> str:
    """Ask a vision model which layout the frames show; the most common answer wins."""
    from .thumbs import b64_jpeg

    names = list(layouts)
    system = ("You classify frames from a livestream by which screen layout they show. "
              "Layouts:\n" + "\n".join(f"- {n}: {layouts[n].get('description', '')}" for n in names)
              + "\nFor each frame give the layout it shows. Pick the most specific layout "
              "that fits; use center only when none of the others does.")
    content = []
    for i, f in enumerate(frames, 1):
        content += [{"type": "text", "text": f"Frame {i}:"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": b64_jpeg(f)}}]
    schema = {"type": "object", "additionalProperties": False, "required": ["frames"],
              "properties": {"frames": {"type": "array", "items": {"type": "string", "enum": names}}}}
    out, _ = claude.json_call(sc["layout_model"], system, content, schema, sc["layout_effort"])
    votes = (out or {}).get("frames") or []
    return max(names, key=lambda n: (votes.count(n), n == sc["default_layout"])) if votes \
        else sc["default_layout"]


# ---------- captions ----------

def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def chunks(words: list[dict], sc: dict) -> list[list[dict]]:
    out: list[list[dict]] = []
    for w in words:
        cur = out[-1] if out else None
        if (cur is None or len(cur) >= sc["caption_max_words"]
                or w["start"] - cur[-1]["end"] > sc["caption_gap_s"]
                or len(" ".join(x["text"] for x in cur + [w])) > sc["caption_max_chars"]
                or re.search(r"[.?!]$", cur[-1]["word"])):
            out.append([w])
        else:
            cur.append(w)
    return out


def write_ass(words: list[dict], t0: float, dur: float, sc: dict, layout: dict,
              bleeper: Bleeper, path: Path) -> int:
    """Captions for t0..t0+dur: chunks of a few words; while a word is spoken it is drawn
    in the highlight colour, a little bigger. Returns the number of caption events."""
    W, H = sc["width"], sc["height"]
    ws = []
    for w in words:
        if w["end"] <= t0 or w["start"] >= t0 + dur:
            continue
        text = re.sub(r"[{}\\]", "", bleeper.mask(w["word"].strip()))
        if sc["caption_uppercase"]:
            text = text.upper()
        if text:
            ws.append({**w, "text": text, "start": max(0.0, w["start"] - t0),
                       "end": min(dur, w["end"] - t0)})
    hi = ass_color(sc["caption_highlight"])
    pos = f"{{\\pos({W // 2},{int(layout.get('caption_y', 0.7) * H)})}}"
    lines = []
    groups = chunks(ws, sc)
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else dur
        g_end = min(g[-1]["end"] + 0.25, nxt)
        for i, w in enumerate(g):
            a = w["start"]
            b = g[i + 1]["start"] if i + 1 < len(g) else g_end
            if b - a < 0.02:
                continue
            text = " ".join(
                f"{{\\c{hi}\\fscx{sc['caption_pop']}\\fscy{sc['caption_pop']}}}{x['text']}{{\\r}}"
                if k == i else x["text"] for k, x in enumerate(g))
            lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Cap,,0,0,0,,{pos}{text}")
    style = ",".join(str(v) for v in [
        "Cap", sc["caption_font"], sc["caption_size"], ass_color(sc["caption_color"]),
        ass_color(sc["caption_highlight"]), ass_color(sc["caption_outline"]), "&H80000000",
        -1, 0, 0, 0, 100, 100, 1, 0, 1, sc["caption_outline_px"], sc["caption_shadow_px"],
        5, 60, 60, 0, 1])
    path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, "
        "PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\nStyle: %s\n\n[Events]\nFormat: Layer, Start, "
        "End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n%s\n"
        % (W, H, style, "\n".join(lines)), encoding="utf-8")
    return len(lines)


# ---------- render ----------

def probe_size(video: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(video)],
                         check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def measure_loudness(video: Path, sc: dict) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
                        f"loudnorm=I={sc['lufs']}:TP={sc['true_peak']}:LRA={sc['lra']}"
                        ":print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise SystemExit(f"loudnorm measurement failed for {video.name}")
    return json.loads(m.group(0))


def esc_path(p: Path) -> str:
    return p.resolve().as_posix().replace(":", "\\:")  # filtergraph-escaped (C\:/...)


def render(src_cut: Path, dst: Path, crops: list[tuple], ass: Path | None, dur: float,
           sc: dict, codec: str) -> dict:
    W = sc["width"]
    loud = measure_loudness(src_cut, sc)
    n = len(crops)
    graph = [f"[0:v]fps={sc['fps']},split={n}" + "".join(f"[s{i}]" for i in range(n))
             if n > 1 else f"[0:v]fps={sc['fps']}[s0]"]
    for i, (x, y, w, h, ph) in enumerate(crops):
        graph.append(f"[s{i}]crop={w}:{h}:{x}:{y},scale={W}:{ph}:flags=lanczos,setsar=1[p{i}]")
    tail = "".join(f"[p{i}]" for i in range(n)) + (f"vstack=inputs={n}" if n > 1 else "null")
    if ass:
        fonts = f":fontsdir='{esc_path(Path(sc['caption_fontsdir']))}'" if sc["caption_fontsdir"] else ""
        tail += f",subtitles=filename='{esc_path(ass)}'{fonts}"
    if codec == "h264_qsv":
        tail += ",format=nv12"
    graph.append(tail + "[vout]")
    fo = min(sc["fade_out_s"], dur / 4)
    graph.append(
        f"[0:a]loudnorm=I={sc['lufs']}:TP={sc['true_peak']}:LRA={sc['lra']}"
        f":measured_I={loud['input_i']}:measured_TP={loud['input_tp']}"
        f":measured_LRA={loud['input_lra']}:measured_thresh={loud['input_thresh']}"
        f":offset={loud['target_offset']}:linear=true,aresample=48000,"
        f"afade=t=in:d={sc['fade_in_s']},afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}[aout]")
    script = dst.with_name(dst.stem + ".graph.txt")
    script.write_text(";\n".join(graph), encoding="utf-8")
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src_cut),
                        "-/filter_complex", str(script), "-map", "[vout]", "-map", "[aout]",
                        "-t", f"{dur:.3f}", *encode_args(codec, sc["crf"]),
                        "-movflags", "+faststart", str(tmp)], check=True)
    finally:
        script.unlink(missing_ok=True)
    tmp.replace(dst)
    return {"input_lufs": float(loud["input_i"]), "lufs": sc["lufs"]}


# ---------- metadata ----------

def write_draft(stream_dir: Path, clip_video: Path, short_video: Path, pick: dict,
                sc: dict) -> None:
    """The clip's upload draft with the Shorts hashtag first (YouTube files any vertical
    video under 3 minutes as a Short; the tag helps it surface there)."""
    src = stream_dir / "metadata" / "clips" / f"{clip_video.stem}.json"
    draft = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {
        "title": pick["title"], "description": pick.get("reason", ""), "hashtags": [], "tags": []}
    tag = sc["hashtag"].lstrip("#")
    old_line = " ".join(f"#{h.lstrip('#')}" for h in draft.get("hashtags", []))
    draft["hashtags"] = [tag] + [h for h in draft.get("hashtags", [])
                                 if h.lstrip("#").lower() != tag.lower()]
    new_line = " ".join(f"#{h.lstrip('#')}" for h in draft["hashtags"])
    desc = draft.get("description", "").rstrip()
    if old_line and desc.endswith(old_line):
        desc = desc[:-len(old_line)].rstrip()
    draft["description"] = f"{desc}\n\n{new_line}".strip()
    draft["chapters"] = []
    out = stream_dir / "metadata" / "shorts" / f"{short_video.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- stage ----------

def run(stream_dir: Path, cfg: dict, ids: list[str] | None = None, layout: str | None = None,
        force: bool = False) -> list[dict]:
    cfg = for_stream(cfg, stream_dir)
    sc = cfg["shorts"]
    codec = sc["codec"] or cfg["cut"]["clip_codec"]
    layouts = layouts_of(sc)
    if sc["default_layout"] not in layouts:
        raise SystemExit(f"shorts.default_layout {sc['default_layout']!r} isn't one of {list(layouts)}")
    if layout and layout not in layouts:
        raise SystemExit(f"no layout {layout!r}; this show has {', '.join(layouts)}")
    cands = json.loads((stream_dir / "candidates.json").read_text(encoding="utf-8"))
    segs = []
    if (stream_dir / "segments.json").exists():
        segs = json.loads((stream_dir / "segments.json").read_text(encoding="utf-8"))["segments"]
    end_of_stream = json.loads((stream_dir / "meta.json").read_text(encoding="utf-8"))["duration_s"]
    by_id = {r["id"]: r for r in cands["pass_a"]}
    picks = cands["pass_b"].get("picks", [])
    words = load_words(stream_dir)
    src = source_file(stream_dir)
    bleeper = Bleeper({**cfg["bleep"], "patterns": cfg["bleep"]["patterns"]
                       if cfg["bleep"]["mode"] != "off" else []})
    audio_bleeper = Bleeper(cfg["bleep"])
    state_path = stream_dir / "shorts.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    review = {}
    if (stream_dir / "review.json").exists():
        review = json.loads((stream_dir / "review.json").read_text(encoding="utf-8"))
    out_dir, work = stream_dir / "shorts", stream_dir / "shorts" / "work"
    out_dir.mkdir(exist_ok=True)
    claude = None
    done = []
    for i, p in enumerate(picks, 1):
        if ids and p["id"] not in ids and f"v{i:02d}" not in ids:
            continue
        c = by_id[p["id"]]
        key = f"v{i:02d}_{p['id']}"
        if sc["window"] == "short":
            a, b = c.get("short_in_s", c["in_s"]), c.get("short_out_s", c["out_s"])
        else:
            a, b = c["in_s"], c["out_s"]
        topic = next((s for s in segs if s["start_s"] <= c["in_s"] < s["end_s"]), None)
        lo, hi = (topic["start_s"], topic["end_s"]) if topic else (0.0, end_of_stream)
        a, b = grow_window(words, a, b, sc["min_s"], sc["max_s"] - 2 * sc["pad_s"],
                           min(lo, a), max(hi, b))
        a, b = snap_window(words, a, b, sc["pad_s"])
        b = min(b, a + sc["max_s"])
        dur = round(b - a, 3)
        dst = out_dir / f"{key}_{slug(p['title'])}.mp4"
        prev = state.get(key, {})
        if prev.get("window_override"):  # set by a retake: used as it is
            a, b = prev["window_override"]
            dur = round(b - a, 3)
        # Layout: --layout, then a changed Kitsu "Layout" column, then the one already in
        # shorts.json (edit it there too), else the model picks. --force keeps the layout.
        kitsu_layout = (review.get(key, {}).get("fields") or {}).get("Layout") or ""
        if layout:
            chosen, how = layout, "cli"
        elif kitsu_layout in layouts and kitsu_layout != prev.get("layout"):
            chosen, how = kitsu_layout, "kitsu"
        elif prev.get("layout") in layouts:
            chosen, how = prev["layout"], prev.get("layout_by", "model")
        else:
            chosen, how = None, "model"
        t = time.time()
        clips = sorted((stream_dir / "clips").glob(f"{i:02d}_{p['id']}_*.mp4"))
        clip_video = clips[0] if clips else dst
        sig_base = {"a": a, "b": b, "captions": sc["captions"], "codec": codec,
                    "style": {k: v for k, v in sc.items() if k.startswith("caption_")},
                    "lufs": sc["lufs"], "w": sc["width"], "h": sc["height"]}
        if chosen and not force and dst.exists() and prev.get("sig") == signature(
                {**sig_base, "layout": layouts[chosen], "name": chosen}):
            continue
        work.mkdir(parents=True, exist_ok=True)
        cut = work / f"{key}.cut.mp4"
        n_bleeps = ffmpeg_cut(src, cut, a, b, sc["cut_codec"] or cfg["cut"]["clip_codec"], sc["cut_crf"],
                              keyframe=keyframe_before(stream_dir, src, a),
                              bleep=(audio_bleeper, words))
        if chosen is None:
            if len(layouts) == 1:
                chosen, how = next(iter(layouts)), "only"
            else:
                claude = claude or Claude(cfg["scoring"]["workspace_id"])
                frames = grab_frames(cut, dur, sc["layout_frames"], work)
                chosen = pick_layout(claude, sc, layouts, frames)
                for f in frames:
                    f.unlink(missing_ok=True)
        W, H = probe_size(cut)
        crops = panel_crops(layouts[chosen], W, H, sc["width"], sc["height"])
        ass = work / f"{key}.ass" if sc["captions"] else None
        n_caps = write_ass(words, a, dur, sc, layouts[chosen], bleeper, ass) if ass else 0
        loud = render(cut, dst, crops, ass, dur, sc, codec)
        if not sc["keep_work"]:
            cut.unlink(missing_ok=True)
            if ass:
                ass.unlink(missing_ok=True)
        for old in out_dir.glob(f"{key}_*.mp4"):  # a retitled pick leaves its old file
            if old != dst:
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)
        side = {**p, "key": key, "in_s": a, "out_s": b, "duration_s": dur, "layout": chosen,
                "layout_by": how, "bleeps": n_bleeps, "captions": n_caps, **loud,
                "category": c.get("category"), "score_a": c.get("score"),
                "clip": clip_video.name if clip_video != dst else ""}
        dst.with_suffix(".json").write_text(json.dumps(side, indent=2, ensure_ascii=False),
                                            encoding="utf-8")
        write_draft(stream_dir, clip_video, dst, p, sc)
        state[key] = {"file": f"shorts/{dst.name}", "layout": chosen, "layout_by": how,
                      "in_s": a, "out_s": b,
                      **({"window_override": prev["window_override"]}
                         if prev.get("window_override") else {}),
                      "sig": signature({**sig_base, "layout": layouts[chosen], "name": chosen})}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        done.append(side)
        print(f"{key}  {dur:5.1f}s  {chosen:<8} ({how})  {n_caps} captions, {n_bleeps} bleeps, "
              f"{loud['input_lufs']:.1f} -> {sc['lufs']} LUFS  -> {dst.name} ({time.time() - t:.0f}s)",
              flush=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")  # marks the stage done
    if claude and claude.cost:
        print(f"layout picks cost ${claude.cost:.4f}")
    print(f"{len(done)} shorts rendered, {len(picks) - len(done)} unchanged or skipped")
    return done


def signature(d: dict) -> str:
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]
