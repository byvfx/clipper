"""Vertical Shorts: each Pass B pick's short window as a 1080x1920 video.

Usage:
    clipper shorts <stream> [--ids t33,w19] [--layout watch] [--force] [--rescene] [--check]

For every pick in candidates.json (pass_b) this renders shorts/v<NN>_<id>_<slug>.mp4:
  1. the clip's window (in_s..out_s; [shorts] window = "short" uses scoring's 15-60 s
     short_in_s..short_out_s instead), widened by whole transcript lines to at least min_s
     (two lines of setup before for each line after, inside the clip's topic segment),
     at most max_s; the short window also ends on a sentence end or an end_pause_s pause;
     snapped to word gaps with pad_s of room, cut with [bleep] applied;
  2. a layout: a stack of panels, each a crop of the source frame scaled to the full
     width, e.g. the video being watched on top and the webcams under it. Layouts are
     per show ([shows.<key>.shorts.layouts]). One vision call labels a frame every
     scene_step_s; where the stream cuts to another scene the Short switches layout at
     the cut (placed on the frame with the highest ffmpeg scene score). Or set one layout
     for the whole Short: --layout, the Kitsu "Layout" column, or "layout" in shorts.json;
  3. burned-in captions from words.jsonl, a few words at a time with the spoken word
     highlighted, masked like every other caption ([bleep]);
  4. audio normalized to [shorts] lufs (two-pass loudnorm) with short fades.
Writes shorts.json (per-Short state: window, layout, how it was chosen) and an upload
metadata draft per Short in metadata/shorts/ (the clip's draft plus #Shorts), and a 9:16
tabloid cover beside each new Short from its clip's thumbnail choice (thumbs.shorts_only;
`clipper thumbs` redraws them too). Unchanged Shorts, and Shorts already uploaded
(schedule.json), are skipped unless --force.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from itertools import pairwise
from pathlib import Path

from . import jsonstate
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


SENTENCE_END = re.compile(r"[.!?…][\"')\]]*$")


def end_on_sentence(words: list[dict], a: float, b: float, min_s: float, max_s: float,
                    hi: float, pause_s: float) -> float:
    """Move the end b to a sentence end (a word ending in . ! ?) or a pause of pause_s
    after a word: forward to the first one that keeps a..b within max_s and hi, else back
    to the last one that leaves at least min_s. Unchanged if neither exists."""
    def ends(k: int) -> bool:
        return bool(SENTENCE_END.search(words[k]["word"].strip())) or (
            k + 1 < len(words) and words[k + 1]["start"] - words[k]["end"] >= pause_s)

    last = [k for k, w in enumerate(words) if a < w["end"] and w["start"] < b]
    if not last:
        return b
    j = last[-1]
    if words[j]["end"] - a <= max_s and ends(j):
        return b
    for k in range(j + 1, len(words)):
        if words[k]["end"] - a > max_s or words[k]["end"] > hi:
            break
        if ends(k):
            return words[k]["end"]
    for k in range(j, -1, -1):
        if words[k]["end"] - a < min_s:
            break
        if words[k]["end"] - a <= max_s and ends(k):
            return words[k]["end"]
    return b


# ---------- layout ----------

def layouts_of(sc: dict) -> dict[str, dict]:
    out = dict(sc.get("layouts") or {})
    out.setdefault("center", CENTER)
    return out


def pickable(layouts: dict) -> dict[str, dict]:
    """The layouts scene detection may choose. pick = false marks another way of showing a
    scene that already has a layout (Kino's watch_hosts: the watch scene's cams only), so
    it would only compete with that one; it is chosen by hand (--layout, Kitsu, a retake)."""
    return {n: lay for n, lay in layouts.items() if lay.get("pick", True)}


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
        even = lambda v: int(v) // 2 * 2
        crops.append((even(x), even(y), even(w), even(h), ph))
    if sum(c[4] for c in crops) != out_h:
        raise SystemExit(f"layout panels add up to {sum(c[4] for c in crops)} px, not {out_h}")
    return crops


def sample_frames(src: Path, a: float, dur: float, step: float, out_dir: Path,
                  stem: str) -> list[tuple[float, Path]]:
    """A small frame every step seconds of src[a, a+dur]: (time in the Short, jpg)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{stem}_f*.jpg"):
        old.unlink()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.3f}",
                    "-t", f"{dur:.3f}", "-i", str(src), "-vf", f"fps=1/{step},scale=768:-2",
                    "-q:v", "5", str(out_dir / f"{stem}_f%03d.jpg")], check=True)
    frames = sorted(out_dir.glob(f"{stem}_f*.jpg"))
    return [(k * step, f) for k, f in enumerate(frames)]


def label_frames(claude: Claude, sc: dict, layouts: dict, frames: list[tuple[float, Path]]
                 ) -> list[str] | None:
    """One layout name per frame, scene_batch frames per vision call (with 25-45 frames in
    one call the labels drifted from run to run), or None if a batch doesn't come back."""
    n = max(1, sc["scene_batch"])
    labels: list[str] = []
    for k in range(0, len(frames), n):
        got = label_batch(claude, sc, layouts, frames[k:k + n])
        if got is None:
            return None
        labels += got
    return labels


def label_batch(claude: Claude, sc: dict, layouts: dict, frames: list[tuple[float, Path]]
                ) -> list[str] | None:
    import base64

    names = list(layouts)
    # With a screenshot of every real layout, "center" (anything else) only invited
    # mistakes: a zoomed or busy screen box got called center.
    if all(layouts[n].get("example") for n in names if n != "center") and len(names) > 2:
        names = [n for n in names if n != "center"]
    system = ("You classify frames from a livestream by which screen layout they show. "
              "Layouts:\n" + "\n".join(f"- {n}: {layouts[n].get('description', '')}" for n in names)
              + "\nThe frames are in time order, a few seconds apart; the stream can switch "
              "layout between any two. Give the layout of every frame, in order. Judge the "
              "arrangement of the frame (where the boxes and webcams are), not what is playing "
              "inside them: a screen box showing a tall phone video with black bars, a dark "
              "page or a paused video is still the same layout. Pick the most specific layout "
              "that fits; use center only when none of the others does.")
    # A screenshot per layout ("example") anchors the labels: without them the model
    # judged by what played in the screen box (a zoomed-in face read as "center").
    content = []
    for n in names:
        ex = layouts[n].get("example")
        if ex and Path(ex).exists():
            content += [{"type": "text", "text": f"Reference screenshot of layout {n}:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(Path(ex).read_bytes()).decode()}}]
    if content:
        content.append({"type": "text", "text": "Now the frames to label:"})
    for i, (t, f) in enumerate(frames, 1):
        content += [{"type": "text", "text": f"Frame f{i:02d} ({t:.0f} s):"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(f.read_bytes()).decode()}}]
    # One required key per frame: a free-length list came back short or long ~40% of the time.
    keys = [f"f{i:02d}" for i in range(1, len(frames) + 1)]
    schema = {"type": "object", "additionalProperties": False, "required": keys,
              "properties": {k: {"type": "string", "enum": names} for k in keys}}
    out, _ = claude.json_call(sc["layout_model"], system, content, schema, sc["layout_effort"])
    labels = [(out or {}).get(k) for k in keys]
    return labels if all(labels) else None


def scene_runs(labels: list[str], step: float, min_scene: float) -> list[list]:
    """Runs of equal labels as [first frame, last frame, name]; a run between two others
    that is shorter than min_scene is folded into its longer neighbour until none is
    left (one stray frame can't flip the layout)."""
    runs: list[list] = []
    for i, n in enumerate(labels):
        if runs and runs[-1][2] == n:
            runs[-1][1] = i
        else:
            runs.append([i, i, n])
    while len(runs) > 2:
        # Only runs between two others fold: a short run at the start or end is a scene
        # the window just catches the edge of (a stream cut 4 s in still needs its crop).
        k = min(range(1, len(runs) - 1), key=lambda j: runs[j][1] - runs[j][0])
        if (runs[k][1] - runs[k][0] + 1) * step >= min_scene:
            break
        nbrs = [j for j in (k - 1, k + 1) if 0 <= j < len(runs)]
        into = max(nbrs, key=lambda j: runs[j][1] - runs[j][0])
        runs[into][0], runs[into][1] = min(runs[into][0], runs[k][0]), max(runs[into][1], runs[k][1])
        del runs[k]
        merged: list[list] = []
        for r in runs:  # neighbours that now share a name become one run
            if merged and merged[-1][2] == r[2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged
    return runs


def find_cut(src: Path, a: float, t0: float, t1: float, fps: int = 20) -> float:
    """The scene change between t0 and t1 (times in the Short). Stream scene switches are
    often animated (slides, fades), so frame-to-frame scores stay low; instead each frame,
    as a tiny grey thumbnail, is compared with the first and the last: the cut is the
    first frame that looks more like the new scene than the old one, for good."""
    w, h = 64, 36
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{a + t0:.3f}",
                          "-t", f"{t1 - t0:.3f}", "-i", str(src), "-an", "-vf",
                          f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=False).stdout
    frames = [raw[k:k + w * h] for k in range(0, len(raw) - w * h + 1, w * h)]
    if len(frames) < 3:
        return round((t0 + t1) / 2, 3)
    first, last = frames[0], frames[-1]
    dist = lambda x, y: sum(abs(p - q) for p, q in zip(x, y, strict=True))
    newer = [dist(f, last) < dist(f, first) for f in frames]
    k = next((k for k in range(len(newer)) if all(newer[k:])), len(newer) - 1)
    return round(min(t1, t0 + k / fps), 3)


def detect_scenes(need_claude, sc: dict, layouts: dict, src: Path, a: float, dur: float,
                  work: Path, stem: str) -> tuple[list[list] | None, str]:
    """([start, end, layout] stretches covering 0..dur, or None if labelling failed; how).
    A vision model labels a frame every scene_step_s, with the show's reference
    screenshots (layouts/<show>/<layout>.jpg) as anchors. Matching frames to those
    screenshots by pixels was tried and failed: a bright page or a webcam filling the
    screen box looked like the other layout."""
    step = sc["scene_step_s"]
    frames = sample_frames(src, a, dur, step, work, stem)
    try:
        labels = label_frames(need_claude(), sc, layouts, frames) if frames else None
    finally:
        for _, f in frames:
            f.unlink(missing_ok=True)
    how = "model"
    if not labels:
        return None, how
    runs = scene_runs(labels, step, sc["min_scene_s"])
    times = [k * step for k in range(len(labels))]
    scenes, start = [], 0.0
    for prev, nxt in pairwise(runs):
        cut = find_cut(src, a, times[prev[1]], times[nxt[0]])
        scenes.append([start, cut, prev[2]])
        start = cut
    scenes.append([start, round(dur, 3), runs[-1][2]])
    return scenes, how


def example_dir(cfg: dict) -> Path:
    """Reference screenshots of this show's layouts: <paths.layouts>/<show>/<layout>.jpg."""
    return cfg["paths"]["layouts"] / ((cfg.get("show") or {}).get("key") or "default")


def with_examples(layouts: dict, cfg: dict) -> dict:
    d = example_dir(cfg)
    return {n: {**lay, "example": str(d / f"{n}.jpg")}
            if not lay.get("example") and (d / f"{n}.jpg").exists() else lay
            for n, lay in layouts.items()}


def save_example(stream_dir: Path, cfg: dict, name: str, at: float) -> Path:
    """The source frame at `at` s as the reference screenshot of layout `name`."""
    cfg = for_stream(cfg, stream_dir)
    if name not in layouts_of(cfg["shorts"]):
        raise SystemExit(f"no layout {name!r}; this show has {', '.join(layouts_of(cfg['shorts']))}")
    out = example_dir(cfg) / f"{name}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.3f}",
                    "-i", str(source_file(stream_dir)), "-frames:v", "1", "-vf", "scale=768:-2",
                    "-q:v", "3", str(out)], check=True)
    print(f"{out}  (layout {name}, {stream_dir.name} at {at:.1f} s)")
    return out


def scenes_label(scenes: list[list]) -> str:
    """'watch' for one scene, else e.g. 'cams 0:00 > watch 0:38' (the Kitsu Layout column)."""
    if len(scenes) == 1:
        return scenes[0][2]
    return " > ".join(f"{n} {int(s) // 60}:{int(s) % 60:02d}" for s, _, n in scenes)


# ---------- captions ----------

def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = round(t * 100)
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


def write_ass(words: list[dict], t0: float, dur: float, sc: dict, scenes: list[tuple],
              bleeper: Bleeper, path: Path) -> int:
    """Captions for t0..t0+dur: chunks of a few words; while a word is spoken it is drawn
    in the highlight colour, a little bigger. scenes: (start, end, layout) in the Short;
    each caption sits at its scene's caption_y, and no chunk runs across a scene cut.
    Returns the number of caption events."""
    W, H = sc["width"], sc["height"]

    def scene_at(t: float) -> int:
        return next((k for k, s in enumerate(scenes) if t < s[1]), len(scenes) - 1)

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
    lines = []
    groups = [g for k in range(len(scenes))
              for g in chunks([w for w in ws if scene_at(w["start"]) == k], sc)]
    for gi, g in enumerate(groups):
        layout = scenes[scene_at(g[0]["start"])][2]
        pos = f"{{\\pos({W // 2},{int(layout.get('caption_y', 0.7) * H)})}}"
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
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n"  # noqa: UP031
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
                        (f"loudnorm=I={sc['lufs']}:TP={sc['true_peak']}:LRA={sc['lra']}"
                         ":print_format=json"), "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       check=False)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise SystemExit(f"loudnorm measurement failed for {video.name}")
    return json.loads(m.group(0))


def esc_path(p: Path) -> str:
    return p.resolve().as_posix().replace(":", "\\:")  # filtergraph-escaped (C\:/...)


def stack(graph: list[str], src: str, crops: list[tuple], W: int, tag: str) -> str:
    """Filters that cut src into its panels and stack them; returns the output label."""
    n = len(crops)
    graph.append(f"[{src}]split={n}" + "".join(f"[{tag}s{i}]" for i in range(n)) if n > 1
                 else f"[{src}]null[{tag}s0]")
    for i, (x, y, w, h, ph) in enumerate(crops):
        graph.append(f"[{tag}s{i}]crop={w}:{h}:{x}:{y},scale={W}:{ph}:flags=lanczos,setsar=1[{tag}p{i}]")
    graph.append("".join(f"[{tag}p{i}]" for i in range(n))
                 + (f"vstack=inputs={n}" if n > 1 else "null") + f"[{tag}]")
    return tag


def render(src_cut: Path, dst: Path, parts: list[tuple], ass: Path | None, dur: float,
           sc: dict, codec: str) -> dict:
    """parts: (start, end, crops) per scene stretch of the Short, in order."""
    W = sc["width"]
    loud = measure_loudness(src_cut, sc)
    k = len(parts)
    graph = [f"[0:v]fps={sc['fps']}" + (f",split={k}" + "".join(f"[v{j}]" for j in range(k))
                                        if k > 1 else "[v0]")]
    outs = []
    for j, (s, e, crops) in enumerate(parts):
        src = f"v{j}"
        if k > 1:  # this stretch only, its clock restarted for concat
            bounds = ":".join(([f"start={s:.3f}"] if j else []) + ([f"end={e:.3f}"] if j < k - 1 else []))
            graph.append(f"[v{j}]trim={bounds},setpts=PTS-STARTPTS[t{j}]")
            src = f"t{j}"
        outs.append(stack(graph, src, crops, W, f"c{j}"))
    tail = "".join(f"[{o}]" for o in outs) + (f"concat=n={k}:v=1:a=0" if k > 1 else "null")
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
    jsonstate.write(out, draft)


# ---------- stage ----------

def run(stream_dir: Path, cfg: dict, ids: list[str] | None = None, layout: str | None = None,
        force: bool = False, rescene: bool = False, check: bool = False) -> list[dict]:
    """rescene: label the scenes again for Shorts whose layout the model chose (re-renders
    the ones that come out different). check: print each Short's scenes, render nothing."""
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
    uploaded = set()  # on YouTube already: their files stay what was uploaded
    if cfg["paths"]["schedule"].exists():
        sched = json.loads(cfg["paths"]["schedule"].read_text(encoding="utf-8"))
        uploaded = {e["key"] for e in sched["entries"] if e.get("status") == "uploaded"}
    out_dir, work = stream_dir / "shorts", stream_dir / "shorts" / "work"
    out_dir.mkdir(exist_ok=True)
    layouts = with_examples(layouts, cfg)
    claude = None

    def need_claude() -> Claude:
        nonlocal claude
        claude = claude or Claude(cfg)
        return claude

    done = []
    for i, p in enumerate(picks, 1):
        if ids and p["id"] not in ids and f"v{i:02d}" not in ids:
            continue
        c = by_id[p["id"]]
        key = f"v{i:02d}_{p['id']}"
        if f"{stream_dir.name}/{key}" in uploaded and not force:
            continue
        if sc["window"] == "short":
            a, b = c.get("short_in_s", c["in_s"]), c.get("short_out_s", c["out_s"])
        else:
            a, b = c["in_s"], c["out_s"]
        topic = next((s for s in segs if s["start_s"] <= c["in_s"] < s["end_s"]), None)
        lo, hi = (topic["start_s"], topic["end_s"]) if topic else (0.0, end_of_stream)
        a, b = grow_window(words, a, b, sc["min_s"], sc["max_s"] - 2 * sc["pad_s"],
                           min(lo, a), max(hi, b))
        if sc["window"] == "short" and sc["end_pause_s"] > 0:  # don't stop mid-sentence
            b = end_on_sentence(words, a, b, sc["min_s"], sc["max_s"] - 2 * sc["pad_s"],
                                max(hi, b), sc["end_pause_s"])
        a, b = snap_window(words, a, b, sc["pad_s"])
        b = min(b, a + sc["max_s"])
        dur = round(b - a, 3)
        dst = out_dir / f"{key}_{slug(p['title'])}.mp4"
        prev = state.get(key, {})
        if prev.get("window_override"):  # set by a retake: used as it is
            a, b = prev["window_override"]
            dur = round(b - a, 3)
        # Layout: --layout, then a changed Kitsu "Layout" column, then one set earlier by
        # hand (shorts.json "layout", a retake): one layout for the whole Short. Otherwise
        # frames through the window are labelled (detect_scenes) and each scene gets its
        # own layout (kept in shorts.json "scenes"; --rescene labels again). --force keeps them.
        # The column only counts once someone changed it from what clipper last proposed.
        kitsu_layout = ((review.get(key, {}).get("fields") or {}).get("Layout") or ""
                        if "Layout" in review.get(key, {}).get("edited", []) else "")
        by_model = prev.get("layout_by", "model") in ("model", "only")
        same_window = [prev.get("in_s"), prev.get("out_s")] == [a, b]
        scenes = None
        if layout:
            scenes, how = [[0.0, dur, layout]], "cli"
        elif kitsu_layout in layouts and kitsu_layout != prev.get("layout"):
            scenes, how = [[0.0, dur, kitsu_layout]], "kitsu"
        elif prev.get("layout") in layouts and not (by_model and (rescene or check)):
            scenes, how = [[0.0, dur, prev["layout"]]], prev.get("layout_by", "model")
        elif prev.get("scenes") and same_window and not (rescene or check):
            scenes, how = prev["scenes"], prev.get("layout_by", "model")
        elif len(pickable(layouts)) == 1:
            scenes, how = [[0.0, dur, next(iter(pickable(layouts)))]], "only"
        if scenes is None:
            scenes, how = detect_scenes(need_claude, sc, pickable(layouts), src, a, dur, work, key)
            if scenes is None:
                print(f"  {key}: frame labels didn't come back whole; using {sc['default_layout']}")
                scenes = [[0.0, dur, sc["default_layout"]]]
        label = scenes_label(scenes)
        if check:
            was = prev.get("scenes") and scenes_label(prev["scenes"]) or prev.get("layout", "-")
            print(f"{key}  {dur:5.1f}s  {label}" + (f"   (now: {was})" if was != label else ""),
                  flush=True)
            continue
        t = time.time()
        clips = sorted((stream_dir / "clips").glob(f"{i:02d}_{p['id']}_*.mp4"))
        clip_video = clips[0] if clips else dst
        sig_base = {"a": a, "b": b, "captions": sc["captions"], "codec": codec,
                    "style": {k: v for k, v in sc.items() if k.startswith("caption_")},
                    "lufs": sc["lufs"], "w": sc["width"], "h": sc["height"]}
        # One scene keeps the signature it always had, so Shorts made before scene
        # detection aren't re-rendered when it finds the same single layout.
        sig = signature({**sig_base, "layout": layouts[scenes[0][2]], "name": scenes[0][2]}
                        if len(scenes) == 1 else
                        {**sig_base, "scenes": [[s, e, n, layouts[n]] for s, e, n in scenes]})
        if not force and dst.exists() and prev.get("sig") == sig:
            continue
        work.mkdir(parents=True, exist_ok=True)
        cut = work / f"{key}.cut.mp4"
        n_bleeps = ffmpeg_cut(src, cut, a, b, sc["cut_codec"] or cfg["cut"]["clip_codec"], sc["cut_crf"],
                              keyframe=keyframe_before(stream_dir, src, a),
                              bleep=(audio_bleeper, words))
        W, H = probe_size(cut)
        parts = [(s, e, panel_crops(layouts[n], W, H, sc["width"], sc["height"])) for s, e, n in scenes]
        ass = work / f"{key}.ass" if sc["captions"] else None
        n_caps = write_ass(words, a, dur, sc, [(s, e, layouts[n]) for s, e, n in scenes],
                           bleeper, ass) if ass else 0
        loud = render(cut, dst, parts, ass, dur, sc, codec)
        if not sc["keep_work"]:
            cut.unlink(missing_ok=True)
            if ass:
                ass.unlink(missing_ok=True)
        for old in out_dir.glob(f"{key}_*.mp4"):  # a retitled pick leaves its old file
            if old != dst:
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)
                old.with_suffix(".jpg").unlink(missing_ok=True)
        multi = {"scenes": scenes} if len(scenes) > 1 else {}
        side = {**p, "key": key, "in_s": a, "out_s": b, "duration_s": dur, "layout": label,
                **multi, "layout_by": how, "bleeps": n_bleeps, "captions": n_caps, **loud,
                "category": c.get("category"), "score_a": c.get("score"),
                "clip": clip_video.name if clip_video != dst else ""}
        jsonstate.write(dst.with_suffix(".json"), side)
        write_draft(stream_dir, clip_video, dst, p, sc)
        state[key] = {"file": f"shorts/{dst.name}", "layout": label, **multi, "layout_by": how,
                      "in_s": a, "out_s": b,
                      **({"window_override": prev["window_override"]}
                         if prev.get("window_override") else {}),
                      "sig": sig}
        jsonstate.write(state_path, state)
        done.append(side)
        print(f"{key}  {dur:5.1f}s  {label:<8} ({how})  {n_caps} captions, {n_bleeps} bleeps, "
              f"{loud['input_lufs']:.1f} -> {sc['lufs']} LUFS  -> {dst.name} ({time.time() - t:.0f}s)",
              flush=True)
    if claude and claude.cost:
        print(f"layout picks cost ${claude.cost:.4f}")
    if check:
        return []
    jsonstate.write(state_path, state)  # marks the stage done
    print(f"{len(done)} shorts rendered, {len(picks) - len(done)} unchanged or skipped")
    if done:  # their vertical covers, from the clips' thumbnail choices (no model calls)
        from . import thumbs
        try:
            thumbs.shorts_only(stream_dir, cfg, {s["key"] for s in done})
        except Exception as e:  # noqa: BLE001  # a cover is optional; the Short is done
            print(f"  warning: Short covers: {type(e).__name__}: {e}")
    return done


def signature(d: dict) -> str:
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]
