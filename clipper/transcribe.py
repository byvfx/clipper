"""Stage 2: transcribe a stream with WhisperX and write words.jsonl.

Usage:
    clipper transcribe <stream_id>     (settings from [transcribe] in clipper.toml)

Reads the first source.* file in the stream dir, extracts 16 kHz mono
audio.wav (skipped if present), transcribes it with word timings and writes:
    transcript.json  raw WhisperX segments (aligned)
    words.jsonl      one word per line: word, start, end, score, speaker, seg
    diarization.json with [transcribe] diarize: speaker turns and voice embeddings (pyannote,
                     issue #52); `clipper speakers` then names them and fills `speaker`

[transcribe] backend:
    whisperx  faster-whisper on CUDA + wav2vec2 word alignment (the GPU worker; `uv sync
              --extra gpu`)
    mlx       mlx-whisper on Apple Silicon (`uv sync --extra mac`), with its own word
              timestamps; when WhisperX is installed too (mlx_align), its alignment model
              refines them on the CPU
    modal     the same WhisperX on a Modal cloud GPU (`uv sync --extra modal`, issue #11);
              audio.wav goes up as FLAC, the aligned segments come back. Deploy the app
              first: `clipper transcribe --deploy` (clipper/modal_app.py)
"""

from __future__ import annotations

import gc
import json
import os
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


def transcribe_modal(wav: Path, tc: dict, runs: Path | None = None, method: str = "transcribe") -> tuple[dict, dict]:
    """WhisperX on Modal: the call's result (segments, dur, diarization) plus what it cost (also
    logged to `runs`, [paths] modal_runs, for `clipper modal` and the war room). method
    "diarize" gets the speaker turns alone."""
    from .modalmon import tracked

    suffix, codec = {"flac": (".flac", ["-c:a", "flac"]),
                     "opus": (".ogg", ["-c:a", "libopus", "-b:a", "48k"])}[tc["modal_audio"]]
    enc = wav.with_name(f"audio.modal{suffix}")
    t1 = time.time()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), *codec,
                    str(enc)], check=True)
    data = enc.read_bytes()
    enc.unlink()
    print(f"encoded {len(data) / 1e6:.0f} MB {tc['modal_audio']} in {time.time() - t1:.0f}s", flush=True)

    with tracked(runs, "stream" if method == "transcribe" else method, wav.parent.name, tc["modal_gpu"],
                 max(0, wav.stat().st_size - 44) / 32000) as rec:  # 16 kHz mono s16
        t2 = time.time()
        worker = modal_worker(tc, tc["modal_gpu"])
        out = getattr(worker, method).remote(data, suffix, modal_settings(tc, diarize=tc["diarize"] or method == "diarize"))
        cost = modal_cost(tc["modal_gpu"], time.time() - t2, out)
        rec.update(cost, audio_s=round(out["dur"], 1))
    print(f"modal {out['gpu']}: {cost['call_s']:.0f}s call ({out['timings']}), ~${cost['est_usd']:.2f}",
          flush=True)
    return out, cost


def modal_worker(tc: dict, gpu: str):
    """An instance of the deployed WhisperX class, on this GPU ("" = the app's default)."""
    import modal

    cls = modal.Cls.from_name(tc["modal_app"], "WhisperX")
    return (cls.with_options(gpu=gpu) if gpu else cls)()


def modal_settings(tc: dict, diarize: bool = False) -> dict:
    keys = ("model", "language", "compute_type", "batch_size")
    if diarize:
        keys += ("diarize", "diarize_model", "min_speakers", "max_speakers")
    return {k: tc[k] for k in keys}


def diarize_local(audio, tc: dict) -> dict:
    """The Modal app's _diarize on this machine's GPU (backend whisperx). Needs HF_TOKEN."""
    import os

    from whisperx.diarize import DiarizationPipeline

    pipe = DiarizationPipeline(model_name=tc["diarize_model"], token=os.environ.get("HF_TOKEN"),
                               device=tc["device"])
    df, emb = pipe(audio, min_speakers=tc["min_speakers"] or None, max_speakers=tc["max_speakers"] or None,
                   return_embeddings=True)
    return {"model": tc["diarize_model"], "embeddings": emb or {},
            "turns": [[round(a, 3), round(b, 3), spk] for a, b, spk in zip(df["start"], df["end"], df["speaker"])]}


def write_diarization(stream_dir: Path, diarization: dict, cost: dict | None = None) -> None:
    turns = diarization["turns"]
    (stream_dir / "diarization.json").write_text(
        json.dumps({**diarization, **({"modal": cost} if cost else {})}), encoding="utf-8")
    print(f"diarized: {len(turns)} turns, {len(diarization['embeddings'])} speakers", flush=True)


def diarize(stream_dir: Path, tc: dict, runs: Path | None = None) -> None:
    """Speaker turns for a stream already transcribed (no new transcript): diarization.json."""
    wav = extract_audio(stream_dir)
    if tc["backend"] == "modal":
        out, cost = transcribe_modal(wav, tc, runs, method="diarize")
        return write_diarization(stream_dir, out["diarization"], cost)
    if tc["backend"] != "whisperx":
        raise SystemExit("diarization needs [transcribe] backend = whisperx or modal")
    import whisperx

    t0 = time.time()
    write_diarization(stream_dir, diarize_local(whisperx.load_audio(str(wav)), tc))
    print(f"in {time.time() - t0:.0f}s", flush=True)


def modal_cost(gpu: str, wall: float, out: dict) -> dict:
    """Billed time is the container's life: about the call's wall clock (upload and cold
    start included) plus scaledown_window idle, so this is an upper estimate."""
    from .modal_app import GPU_PRICES

    price = GPU_PRICES.get(gpu or "L4", 0.0)
    return {"gpu": out["gpu"], "call_s": round(wall, 1), **out["timings"],
            "est_usd": round((wall + 30) / 3600 * price, 3)}


def run(stream_dir: Path, tc: dict, runs: Path | None = None) -> None:
    t0 = time.time()
    wav = extract_audio(stream_dir)
    if tc["backend"] == "mlx":
        if tc["diarize"]:
            print("[transcribe] diarize needs backend whisperx or modal; skipped on mlx", flush=True)
        segments, dur = transcribe_mlx(wav, tc)
        return write(stream_dir, tc, segments, dur, t0)
    if tc["backend"] == "modal":
        out, cost = transcribe_modal(wav, tc, runs)
        if out.get("diarization"):
            write_diarization(stream_dir, out["diarization"], cost)
        return write(stream_dir, tc, out["segments"], out["dur"], t0, {"modal": cost})
    if tc["backend"] != "whisperx":
        raise SystemExit(f"[transcribe] backend must be whisperx, mlx or modal, not {tc['backend']!r}")
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

    if tc["diarize"]:
        t3 = time.time()
        write_diarization(stream_dir, diarize_local(audio, tc))
        gc.collect()
        torch.cuda.empty_cache()
        print(f"diarized in {time.time() - t3:.0f}s", flush=True)

    write(stream_dir, tc, result["segments"], dur, t0)


def write(stream_dir: Path, tc: dict, segments: list[dict], dur: float, t0: float,
          extra: dict | None = None) -> None:
    from . import jsonstate

    # #75: both files appear whole or not at all; words.jsonl is the stage's done marker, so a
    # half-written one would pass for a finished transcript.
    jsonstate.write(stream_dir / "transcript.json",
                    {"model": tc["mlx_model"] if tc["backend"] == "mlx" else tc["model"],
                     "backend": tc["backend"], "language": tc["language"], **(extra or {}),
                     "segments": segments}, indent=None)

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
    jsonstate.write_text(stream_dir / "words.jsonl",
                         "".join(json.dumps(w, ensure_ascii=False) + "\n" for w in words))

    total = time.time() - t0
    print(f"{len(segments)} segments, {len(words)} words ({missing} interpolated); "
          f"total {total:.0f}s = {dur / max(total, 1e-9):.1f}x realtime")


def deploy() -> None:
    """Deploy (or update) the Modal app in clipper/modal_app.py."""
    from . import modal_app

    # UTF-8: modal prints a check mark, which cp1252 can't encode when output is piped.
    subprocess.run([sys.executable, "-m", "modal", "deploy", modal_app.__file__], check=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
