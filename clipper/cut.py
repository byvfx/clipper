"""Cut clips out of the stream source with ffmpeg.

  segments      topic segments from segments.json (cut=true, or --ids) -> segments/
  compilations  one long video per topic with >= compile_min_segments segments,
                joined in stream order, with YouTube chapters -> compilations/
  picks         Pass B picks from candidates.json, horizontal cut -> clips/

Segments use stream copy (lossless, instant). A copy can only start on a keyframe
(Kick VODs: every ~4 s), so the start is moved back to the keyframe at or before it;
otherwise the video track starts late and players that ignore the offset play the
audio seconds behind. Picks are short and must start on a word, so they re-encode
(clip_codec) with frame-accurate seeking.
"""

from __future__ import annotations

import bisect
import json
import subprocess
import time
from pathlib import Path

from .config import for_stream
from .segments import hms, slug


def source_file(stream_dir: Path) -> Path:
    for p in sorted(stream_dir.glob("source.*")):
        if p.suffix not in (".part", ".ytdl") and "Frag" not in p.name:
            return p
    raise SystemExit(f"no source video in {stream_dir}")


def keyframes(stream_dir: Path, src: Path) -> list[float]:
    """Video keyframe times in seconds from the start of the file (what -ss uses)."""
    cache = stream_dir / "keyframes.json"
    if cache.exists():
        return json.loads(cache.read_text())
    probe = lambda args: subprocess.run(
        ["ffprobe", "-v", "error", *args, str(src)], check=True, capture_output=True, text=True
    ).stdout
    start = float(probe(["-show_entries", "format=start_time", "-of", "csv=p=0"]).strip())
    out = probe(["-select_streams", "v:0", "-skip_frame", "nokey",
                 "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0"])
    kf = sorted(round(float(t) - start, 3) for t, *flags in
                (line.split(",") for line in out.splitlines() if line)
                if "K" in "".join(flags) and t != "N/A")
    cache.write_text(json.dumps(kf))
    return kf


def encode_args(codec: str, crf: int) -> list[str]:
    if codec == "copy":
        return ["-c", "copy"]
    audio = ["-c:a", "aac", "-b:a", "192k"]
    if codec == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(crf), *audio]
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf), *audio]
    if codec == "h264_vaapi":
        return ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload",
                "-c:v", "h264_vaapi", "-qp", str(crf), *audio]
    return ["-c:v", codec, "-crf", str(crf), "-preset", "veryfast", *audio]


def ffmpeg_cut(src: Path, dst: Path, start: float, end: float, codec: str, crf: int,
               keyframe: float | None = None, bleep: tuple | None = None) -> int:
    """keyframe: for re-encodes, seek the input to this keyframe (at or before start) and
    trim to `start` after decoding. Seeking a .ts straight to `start` can land past a
    keyframe, and the video then starts seconds after the audio.
    bleep: (Bleeper, words) to cover listed words in the audio; with codec copy the video
    is still copied and only the audio is re-encoded. Returns the number of spans covered."""
    seek = start if keyframe is None else keyframe + 0.005
    trim = [] if keyframe is None else ["-ss", f"{start - seek:.3f}"]
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    maps, codecs, spans = ["-map", "0:v:0", "-map", "0:a:0"], encode_args(codec, crf), []
    script = dst.with_name(dst.stem + ".bleep.txt")
    if bleep and bleep[0].on:
        bleeper, words = bleep
        # Filters see time from the seek point, before the output trim.
        spans = bleeper.intervals(words, start, end, seek)
        if spans:
            bleeper.filter_script(spans, end - seek, script)
            maps = ["-/filter_complex", str(script), "-map", "0:v:0", "-map", "[aout]"]
            if codec == "copy":
                codecs = ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seek:.3f}",
             "-i", str(src), *trim, "-t", f"{end - start:.3f}", *maps,
             *codecs, "-avoid_negative_ts", "make_zero",
             "-movflags", "+faststart", str(tmp)],
            check=True,
        )
    finally:
        script.unlink(missing_ok=True)
        script.with_suffix(".cmd").unlink(missing_ok=True)
    tmp.replace(dst)
    return len(spans)


def make_bleep(stream_dir: Path, cfg: dict) -> tuple | None:
    from .bleep import Bleeper
    from .scoring import load_words

    b = Bleeper(cfg["bleep"])
    return (b, load_words(stream_dir)) if b.on else None


def keyframe_before(stream_dir: Path, src: Path, t: float) -> float:
    kf = keyframes(stream_dir, src)
    return kf[max(0, bisect.bisect_right(kf, t + 1e-3) - 1)]


def stream_offsets(path: Path) -> dict[str, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time",
         "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True).stdout
    return {kind: float(t) for kind, t in (l.split(",") for l in out.splitlines() if l)}


def copy_cut(stream_dir: Path, src: Path, dst: Path, start: float, end: float,
             bleep: tuple | None = None) -> tuple[float, int]:
    """Lossless cut starting on the keyframe at or before `start`.
    Returns that start and the number of bleeped spans."""
    k = keyframe_before(stream_dir, src, start)
    # A hair past the keyframe: seeking lands on it, and no earlier one gets pulled in.
    n = ffmpeg_cut(src, dst, k + 0.005, end, "copy", 0, bleep=bleep)
    check_sync(dst)
    return k, n


def check_sync(path: Path) -> None:
    # ~0.1 s of video offset is normal (B-frame delay, covered by the MP4 edit list).
    off = stream_offsets(path)
    if abs(off.get("video", 0) - off.get("audio", 0)) > 0.25:
        print(f"  warning: {path.name} audio/video start {off}")


def write_sidecar(path: Path, data: dict) -> None:
    path.with_suffix(".json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                         encoding="utf-8")


def cut_segments(stream_dir: Path, cfg: dict, ids: list[str] | None = None) -> None:
    cfg = for_stream(cfg, stream_dir)
    data = json.loads((stream_dir / "segments.json").read_text(encoding="utf-8"))
    segs = [s for s in data["segments"] if (s["id"] in ids if ids else s["cut"])]
    src = source_file(stream_dir)
    out_dir = stream_dir / "segments"
    out_dir.mkdir(exist_ok=True)
    bleep = make_bleep(stream_dir, cfg)
    for s in segs:
        dst = out_dir / s["file"]
        t = time.time()
        s["cut_start_s"], s["bleeps"] = copy_cut(stream_dir, src, dst, s["start_s"], s["end_s"],
                                                 bleep)
        write_sidecar(dst, s)
        print(f"{s['id']} {s['duration_s'] / 60:5.1f} min -> {dst.name} "
              f"(+{s['start_s'] - s['cut_start_s']:.1f}s lead-in, {s['bleeps']} bleeps, "
              f"{time.time() - t:.0f}s)")


def cut_compilations(stream_dir: Path, cfg: dict, topics: list[str] | None = None) -> None:
    """One video per topic with several segments, e.g. every Quartering segment in order."""
    cfg = for_stream(cfg, stream_dir)
    data = json.loads((stream_dir / "segments.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in data["segments"]}
    seg_dir, out_dir = stream_dir / "segments", stream_dir / "compilations"
    out_dir.mkdir(exist_ok=True)
    for comp in data.get("compilations", []):
        topic = comp["topic"]
        if topics is not None and topic not in topics:
            continue
        segs = [by_id[i] for i in comp["segments"]]
        missing = [s["id"] for s in segs if not (seg_dir / s["file"]).with_suffix(".json").exists()]
        if missing:
            cut_segments(stream_dir, cfg, missing)
        # The real cut starts (keyframes) live in each segment's sidecar.
        segs = [json.loads((seg_dir / s["file"]).with_suffix(".json").read_text(encoding="utf-8"))
                for s in segs]
        listing = out_dir / f"{slug(topic)}.txt"
        listing.write_text("".join(f"file '{(seg_dir / s['file']).resolve().as_posix()}'\n"
                                   for s in segs), encoding="utf-8")
        dst = out_dir / f"{slug(topic)}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                        "-safe", "0", "-i", str(listing), "-c", "copy",
                        "-movflags", "+faststart", str(dst)], check=True)
        listing.unlink()
        # Chapters: each part's offset in the compilation, from the actual cut lengths.
        chapters, t = [], 0.0
        for s in segs:
            chapters.append({"start_s": round(t, 1), "id": s["id"], "title": s["title"]})
            t += s["end_s"] - s.get("cut_start_s", s["start_s"])
        chapter_text = "\n".join(f"{hms(c['start_s'])} {c['title']}" for c in chapters)
        write_sidecar(dst, {**comp, "duration_s": round(t, 1), "chapters": chapters,
                            "chapter_text": chapter_text,
                            "youtube_description": f"{comp['description']}\n\n{chapter_text}"})
        print(f"{topic}: {len(segs)} segments, {t / 60:.0f} min -> {dst.name}")


def cut_picks(stream_dir: Path, cfg: dict, ids: list[str] | None = None) -> None:
    cfg = for_stream(cfg, stream_dir)
    path = stream_dir / "candidates.json"
    if not path.exists():  # not scored, e.g. a pre-show: no picks by design
        print("no candidates.json: no picks to cut")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["pass_a"]}
    src = source_file(stream_dir)
    out_dir = stream_dir / "clips"
    out_dir.mkdir(exist_ok=True)
    cc = cfg["cut"]
    bleep = make_bleep(stream_dir, cfg)
    for i, p in enumerate(data["pass_b"].get("picks", []), 1):
        if ids is not None and p["id"] not in ids:
            continue
        c = by_id[p["id"]]
        dst = out_dir / f"{i:02d}_{p['id']}_{slug(p['title'])}.mp4"
        t = time.time()
        n = ffmpeg_cut(src, dst, c["in_s"], c["out_s"], cc["clip_codec"], cc["crf"],
                       keyframe=keyframe_before(stream_dir, src, c["in_s"]), bleep=bleep)
        check_sync(dst)
        write_sidecar(dst, {**p, "in_s": c["in_s"], "out_s": c["out_s"],
                            "short_in_s": c["short_in_s"], "short_out_s": c["short_out_s"],
                            "category": c["category"], "score_a": c["score"], "bleeps": n})
        print(f"{p['id']} {c['out_s'] - c['in_s']:5.0f}s -> {dst.name} "
              f"({n} bleeps, {time.time() - t:.0f}s)")
