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

Segments run [cut] parallel at once. With [cut] backend = "nas" the whole command runs on
the NAS instead (on_nas), in a one-off container of the recorder image, next to the source.
"""

from __future__ import annotations

import bisect
import io
import json
import shlex
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path

from . import jsonstate
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
    jsonstate.write(cache, kf, indent=None)
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
            # inline, not -/filter_complex <file>: the NAS image's ffmpeg 5.1 lacks that (the
            # graph is one short line; the spans are in its .cmd file)
            maps = ["-filter_complex", script.read_text(encoding="utf-8"), "-map", "0:v:0", "-map", "[aout]"]
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


def stream_entry(path: Path, entry: str) -> dict[str, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", f"stream=codec_type,{entry}",
         "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True).stdout
    return {kind: float(t) for kind, t in (l.split(",") for l in out.splitlines() if l)}


def stream_offsets(path: Path) -> dict[str, float]:
    return stream_entry(path, "start_time")


def stream_durations(path: Path) -> dict[str, float]:
    return stream_entry(path, "duration")


def copy_cut(stream_dir: Path, src: Path, dst: Path, start: float, end: float,
             bleep: tuple | None = None) -> tuple[float, int]:
    """Lossless cut starting on the keyframe at or before `start`.
    Returns that start and the number of bleeped spans."""
    k = keyframe_before(stream_dir, src, start)
    # ffmpeg backs an input seek off by 3/23 s when the video has B-frames (Kick's does).
    # MPEG-TS then lands on the keyframe at or after that point, so a hair past k is right;
    # indexed containers (the recorder's .mkv) land on the one at or before it, so the seek
    # has to clear the back-off or it copies a whole extra GOP ahead of the audio.
    n = ffmpeg_cut(src, dst, k + (0.005 if src.suffix == ".ts" else 0.15), end, "copy", 0,
                   bleep=bleep)
    check_sync(dst)
    got = stream_durations(dst).get("video", 0)
    if abs(got - (end - k)) > 0.5:
        raise SystemExit(f"{dst.name}: {got:.1f}s of video for {end - k:.1f}s from keyframe "
                         f"{k}; the seek missed it")
    return k, n


def check_sync(path: Path) -> None:
    # ~0.1 s of video offset is normal (B-frame delay, covered by the MP4 edit list).
    off = stream_offsets(path)
    if abs(off.get("video", 0) - off.get("audio", 0)) > 0.25:
        raise SystemExit(f"{path.name}: audio/video start {off}; the cut is out of sync")


def write_sidecar(path: Path, data: dict) -> None:
    jsonstate.write(path.with_suffix(".json"), data)


def cut_segments(stream_dir: Path, cfg: dict, ids: list[str] | None = None) -> None:
    cfg = for_stream(cfg, stream_dir)
    data = json.loads((stream_dir / "segments.json").read_text(encoding="utf-8"))
    segs = [s for s in data["segments"] if (s["id"] in ids if ids else s["cut"])]
    src = source_file(stream_dir)
    out_dir = stream_dir / "segments"
    out_dir.mkdir(exist_ok=True)
    bleep = make_bleep(stream_dir, cfg)
    keyframes(stream_dir, src)  # once, before the threads share the cache

    def one(s: dict) -> None:
        dst = out_dir / s["file"]
        t = time.time()
        s["cut_start_s"], s["bleeps"] = copy_cut(stream_dir, src, dst, s["start_s"], s["end_s"],
                                                 bleep)
        write_sidecar(dst, s)
        print(f"{s['id']} {s['duration_s'] / 60:5.1f} min -> {dst.name} "
              f"(+{s['start_s'] - s['cut_start_s']:.1f}s lead-in, {s['bleeps']} bleeps, "
              f"{time.time() - t:.0f}s)", flush=True)

    # Each cut is one ffmpeg (copy + the bleeped audio's AAC encode), so a few run at once.
    with ThreadPoolExecutor(max(1, cfg["cut"]["parallel"])) as pool:
        list(pool.map(one, segs))


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
        # A part's keyframe lead-in replays the end of the part before it when the two are
        # back to back in the stream, so each part ends where the next one's file begins.
        # A copy can stop anywhere (only starts need keyframes); nothing is dropped.
        begin = [s.get("cut_start_s", s["start_s"]) for s in segs]
        ends = [min(s["end_s"], b) if b > a else s["end_s"]
                for s, (a, b) in zip(segs, pairwise(begin))] + [segs[-1]["end_s"]]
        lines = []
        for s, b, end in zip(segs, begin, ends):
            lines.append(f"file '{(seg_dir / s['file']).resolve().as_posix()}'\n")
            if end < s["end_s"]:
                lines.append(f"outpoint {end - b:.3f}\n")
        listing = out_dir / f"{slug(topic)}.txt"
        listing.write_text("".join(lines), encoding="utf-8")
        dst = out_dir / f"{slug(topic)}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                        "-safe", "0", "-i", str(listing), "-c", "copy",
                        "-movflags", "+faststart", str(dst)], check=True)
        listing.unlink()
        # Chapters: each part's offset in the compilation, from the actual cut lengths.
        chapters, t = [], 0.0
        for s, b, end in zip(segs, begin, ends):
            chapters.append({"start_s": round(t, 1), "id": s["id"], "title": s["title"]})
            t += end - b
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


# ---------- on the NAS ([cut] backend = "nas", #51) ----------
# Cutting is copying: segments are stream copies plus an AAC encode of the bleeped audio. On
# the laptop every byte crosses Wi-Fi twice (the share writes at ~23 MB/s), so a stream's cut
# took 1.5-2.5 h; on the NAS it reads and writes its own disk. Measured 2026-10-26 on
# kick-131151926, one 5 min segment: laptop to the share 20.4 s, on the NAS 10 s (0.5 s of it
# the copy, the rest the AAC encode; three at once share the NAS's 6 cores).

def on_nas(stream_dir: Path, cfg: dict, argv: list[str]) -> None:
    """Run `clipper cut <argv>` in a one-off container of the recorder image on the NAS, with
    this checkout's clipper/ package and config (the image's own copy may be older)."""
    cc = for_stream(cfg, stream_dir)["cut"]
    missing = [k for k in ("nas_host", "nas_data") if not cc[k]]
    if missing:
        raise SystemExit(f"[cut] backend = \"nas\" needs [cut] {', '.join(missing)} in clipper.toml")
    if stream_dir.resolve().parent != Path(cfg["paths"]["streams"]).resolve():
        raise SystemExit(f"{stream_dir} is not under paths.streams; the NAS only sees those "
                         f"(CLIPPER_CUT_BACKEND=local to cut it here)")
    toml = (cfg.get("_root") or cfg).get("_file")  # a resolved config keeps the loaded one in _root
    if not toml:
        raise SystemExit("[cut] backend = \"nas\" needs a clipper.toml to send along")
    buf = io.BytesIO()
    pkg = Path(__file__).parent
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(pkg, "clipper", filter=lambda t: None if "__pycache__" in t.name else t)
        tar.add(toml, "clipper.toml")
    sid = stream_dir.name
    env = {"PYTHONPATH": "/code", "CLIPPER_CONFIG": "/code/clipper.toml",
           "CLIPPER_PATHS_STREAMS": "/data/streams", "CLIPPER_CUT_BACKEND": "local"}
    docker = ["docker", "run", "--rm", "--init", "--name", f"clipper-cut-{sid}",
              *(["--user", cc["nas_user"]] if cc["nas_user"] else []),
              "-v", f"{cc['nas_data']}:/data",
              *[a for k, v in env.items() for a in ("-e", f"{k}={v}")]]
    run = [cc["nas_image"], "cut", sid, *argv]
    # One folder per run (two cuts at once must not share code), gone when the run ends.
    remote = ('set -e; d=$(mktemp -d "$HOME/clipper-cut.XXXXXX"); trap \'rm -rf "$d"\' EXIT; '
              'tar -xzf - -C "$d"; chmod -R a+rX "$d"; '
              + shlex.join(docker) + ' -v "$d":/code:ro ' + shlex.join(run))
    print(f"cutting on {cc['nas_host']} ({cc['nas_image']}, {cc['parallel']} at once)", flush=True)
    t = time.time()
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", cc["nas_host"], remote], input=buf.getvalue(),
                       check=False)
    if r.returncode:
        raise SystemExit(f"cut on {cc['nas_host']} failed (exit {r.returncode}); a run still going "
                         f"there: ssh {cc['nas_host']} docker rm -f clipper-cut-{sid}")
    print(f"cut on {cc['nas_host']} in {time.time() - t:.0f}s", flush=True)
