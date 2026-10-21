"""Stage 2: transcribe a stream with WhisperX and write words.jsonl.

Usage:
    clipper transcribe <stream_id>     (settings from [transcribe] in clipper.toml)

Reads the first source.* file in the stream dir, extracts 16 kHz mono
audio.wav (skipped if present), transcribes it with word timings and writes:
    transcript.json  raw WhisperX segments (aligned)
    words.jsonl      one word per line: word, start, end, score, speaker, seg

[transcribe] backend:
    whisperx  faster-whisper on CUDA + wav2vec2 word alignment (the GPU worker; `uv sync
              --extra gpu`)
    mlx       mlx-whisper on Apple Silicon (`uv sync --extra mac`), with its own word
              timestamps; when WhisperX is installed too (mlx_align), its alignment model
              refines them on the CPU
"""

from __future__ import annotations

import gc
import json
import subprocess
import sys
import time
from pathlib import Path


def extract_audio(stream_dir: Path) -> Path:
    wav = stream_dir / "audio.wav"
    if wav.exists():
        return wav
    sources = sorted(p for p in stream_dir.glob("source.*") if p.suffix != ".part")
    if not sources:
        sys.exit(f"no source.* in {stream_dir}")
    tmp = wav.with_suffix(".tmp.wav")
    # A recording with dropped segments has timestamp jumps; WAV has no timestamps, so
    # without aresample the samples close up over each gap and every transcript time
    # after it runs early against the video (kick-132733064 ended 47 s apart). async=1
    # fills each gap with silence, first_pts=0 keeps the start where the source starts.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(sources[0]), "-vn", "-af", "aresample=async=1:first_pts=0",
         "-ac", "1", "-ar", "16000", str(tmp)],
        check=True,
    )
    tmp.replace(wav)
    return wav


def fill_missing_times(words: list[dict]) -> int:
    """WhisperX can't align some tokens (digits, symbols). Give them times by
    spreading them evenly between their aligned neighbours."""
    missing = 0
    i = 0
    while i < len(words):
        if "start" in words[i]:
            i += 1
            continue
        j = i
        while j < len(words) and "start" not in words[j]:
            j += 1
        lo = words[i - 1]["end"] if i > 0 else words[j]["start"] if j < len(words) else 0.0
        hi = words[j]["start"] if j < len(words) else lo
        step = (hi - lo) / (j - i) if hi > lo else 0.0
        for k in range(i, j):
            words[k]["start"] = round(lo + step * (k - i), 3)
            words[k]["end"] = round(lo + step * (k - i + 1), 3)
            words[k]["score"] = None
            words[k]["interpolated"] = True
        missing += j - i
        i = j
    return missing


def transcribe_mlx(wav: Path, tc: dict) -> tuple[list[dict], float]:
    """Segments with words from mlx-whisper (Apple Silicon), optionally re-aligned."""
    import mlx_whisper

    t1 = time.time()
    result = mlx_whisper.transcribe(str(wav), path_or_hf_repo=tc["mlx_model"],
                                    language=tc["language"], word_timestamps=True,
                                    condition_on_previous_text=False)
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"],
                 "words": [{"word": w["word"].strip(), "start": w["start"], "end": w["end"],
                            "score": w.get("probability")} for w in s.get("words", [])]}
                for s in result["segments"]]
    print(f"transcribed in {time.time() - t1:.0f}s (mlx {tc['mlx_model']})", flush=True)
    dur = segments[-1]["end"] if segments else 0.0
    if tc["mlx_align"]:
        try:
            import whisperx
        except ImportError:
            return segments, dur
        t2 = time.time()
        audio = whisperx.load_audio(str(wav))
        model, meta = whisperx.load_align_model(language_code=tc["language"], device="cpu")
        segments = whisperx.align(segments, model, meta, audio, "cpu",
                                  return_char_alignments=False)["segments"]
        dur = len(audio) / 16000
        print(f"aligned in {time.time() - t2:.0f}s (cpu)", flush=True)
    return segments, dur


def run(stream_dir: Path, tc: dict) -> None:
    t0 = time.time()
    wav = extract_audio(stream_dir)
    if tc["backend"] == "mlx":
        segments, dur = transcribe_mlx(wav, tc)
        return write(stream_dir, tc, segments, dur, t0)
    if tc["backend"] != "whisperx":
        raise SystemExit(f"[transcribe] backend must be whisperx or mlx, not {tc['backend']!r}")
    import torch
    import whisperx

    audio = whisperx.load_audio(str(wav))
    dur = len(audio) / 16000
    print(f"audio {dur / 60:.1f} min, extracted in {time.time() - t0:.0f}s", flush=True)

    t1 = time.time()
    model = whisperx.load_model(tc["model"], tc["device"],
                                compute_type=tc["compute_type"], language=tc["language"])
    result = model.transcribe(audio, batch_size=tc["batch_size"], language=tc["language"])
    del model
    gc.collect()
    torch.cuda.empty_cache()
    print(f"transcribed in {time.time() - t1:.0f}s", flush=True)

    t2 = time.time()
    align_model, meta = whisperx.load_align_model(language_code=tc["language"], device=tc["device"])
    result = whisperx.align(result["segments"], align_model, meta, audio, tc["device"],
                            return_char_alignments=False)
    del align_model
    gc.collect()
    torch.cuda.empty_cache()
    print(f"aligned in {time.time() - t2:.0f}s", flush=True)

    write(stream_dir, tc, result["segments"], dur, t0)


def write(stream_dir: Path, tc: dict, segments: list[dict], dur: float, t0: float) -> None:
    (stream_dir / "transcript.json").write_text(
        json.dumps({"model": tc["mlx_model"] if tc["backend"] == "mlx" else tc["model"],
                    "backend": tc["backend"], "language": tc["language"], "segments": segments},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    words = []
    for si, seg in enumerate(segments):
        for w in seg.get("words", []):
            words.append({
                "word": w["word"],
                **({"start": round(w["start"], 3), "end": round(w["end"], 3)} if "start" in w else {}),
                "score": round(w["score"], 3) if "score" in w else None,
                "speaker": w.get("speaker"),
                "seg": si,
            })
    missing = fill_missing_times(words)
    with open(stream_dir / "words.jsonl", "w", encoding="utf-8") as f:
        for w in words:
            f.write(json.dumps(w, ensure_ascii=False) + "\n")

    total = time.time() - t0
    print(f"{len(segments)} segments, {len(words)} words ({missing} interpolated); "
          f"total {total:.0f}s = {dur / max(total, 1e-9):.1f}x realtime")
