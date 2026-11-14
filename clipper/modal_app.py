"""WhisperX on Modal: the `[transcribe] backend = "modal"` worker (issue #11).

Deploy once per change to this file (needs MODAL_TOKEN_ID / MODAL_TOKEN_SECRET):
    clipper transcribe --deploy

The image pins the same torch/WhisperX versions as the `gpu` extra (uv.lock) and bakes in
large-v3 and the English alignment model, so a call loads from the image, not the network.
`WhisperX.transcribe` takes encoded audio (FLAC from audio.wav by default, so the samples are
the ones the local backend reads) and returns the aligned segments the local backend would
write; transcribe.write() turns them into transcript.json and words.jsonl as before.
`WhisperX.transcribe_url` does the same for a URL ffmpeg can read, so archive sources
(Odysee, Rumble) are fetched in the cloud and never pass through the home connection.

Speaker turns (issue #52): with `diarize` in the settings, the same call also runs pyannote's
diarization and returns `turns` ([start, end, SPEAKER_xx]) and one voice embedding per speaker;
`WhisperX.diarize` does that alone, for a stream transcribed before. The pyannote model is gated:
the Modal secret HF_SECRET holds HF_TOKEN for a Hugging Face account that accepted its terms, and
the deploy bakes the model into the image with it.

This file is self-contained (no clipper imports): `modal deploy` loads it as a top-level
module.
"""

from __future__ import annotations

import os
import time

import modal

APP_NAME = "clipper-whisperx"
DEFAULT_GPU = "L4"
GPU_PRICES = {  # $ per hour (modal.com/pricing, 2026-10-23), for the cost line only
    "T4": 0.59, "L4": 0.80, "A10": 1.10, "A10G": 1.10, "L40S": 1.95,
    "A100": 2.10, "A100-40GB": 2.10, "A100-80GB": 2.50, "H100": 3.95,
}
SPEED = {"L4": 24.5, "L40S": 46.5, "H100": 55.0}  # x realtime, 8 h stream incl. align (2026-10-23)
DIARIZE_SPEED = 59.0  # x realtime on L40S, pyannote alone (kick-131151926, 2026-10-25); others scaled by SPEED
BAKED_MODEL = "large-v3"
DIARIZE_MODEL = "pyannote/speaker-diarization-community-1"  # [transcribe] diarize_model default
HF_SECRET = "huggingface"  # Modal secret with HF_TOKEN (modal.com -> Secrets)
MAX_CONTAINERS = 10  # GPUs at once across every caller; the war room shows containers against it


def _bake_models() -> None:
    import whisperx

    whisperx.load_model(BAKED_MODEL, "cpu", compute_type="int8", language="en")
    whisperx.load_align_model(language_code="en", device="cpu")


def _bake_diarizer() -> None:
    from pyannote.audio import Pipeline

    Pipeline.from_pretrained(DIARIZE_MODEL, token=os.environ["HF_TOKEN"])


image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .apt_install("ffmpeg")
    .pip_install("torch==2.8.0", "torchaudio==2.8.0", "torchvision==0.23.0",
                 index_url="https://download.pytorch.org/whl/cu128")
    .pip_install("whisperx==3.8.6", "faster-whisper==1.2.1", "ctranslate2==4.8.3",
                 "transformers==4.57.6", "pyannote-audio==4.0.7", "numpy==2.5.4",
                 "torch==2.8.0", "torchaudio==2.8.0")
    .env({"HF_HOME": "/models/hf", "TORCH_HOME": "/models/torch"})
    .run_function(_bake_models)
    .run_function(_bake_diarizer, secrets=[modal.Secret.from_name(HF_SECRET, required_keys=["HF_TOKEN"])])
)

app = modal.App(APP_NAME, image=image)


@app.cls(gpu=DEFAULT_GPU, timeout=4 * 3600, scaledown_window=30, max_containers=MAX_CONTAINERS,
         secrets=[modal.Secret.from_name(HF_SECRET, required_keys=["HF_TOKEN"])])
class WhisperX:
    @modal.enter()
    def load(self) -> None:
        self.models: dict = {}
        self.aligners: dict = {}
        self.diarizers: dict = {}

    def _model(self, tc: dict):
        import whisperx

        key = (tc["model"], tc["compute_type"], tc["language"])
        if key not in self.models:
            self.models = {key: whisperx.load_model(tc["model"], "cuda", compute_type=tc["compute_type"],
                                                    language=tc["language"])}
        return self.models[key]

    def _aligner(self, language: str):
        import whisperx

        if language not in self.aligners:
            self.aligners[language] = whisperx.load_align_model(language_code=language, device="cuda")
        return self.aligners[language]

    def _diarize(self, audio, tc: dict) -> dict:
        """Speaker turns and one embedding per speaker (the same space for every stream, so
        clipper can match them against a show's enrolled voices)."""
        from whisperx.diarize import DiarizationPipeline

        name = tc.get("diarize_model") or DIARIZE_MODEL
        if name not in self.diarizers:
            self.diarizers = {name: DiarizationPipeline(model_name=name, token=os.environ.get("HF_TOKEN"),
                                                        device="cuda")}
        df, emb = self.diarizers[name](audio, min_speakers=tc.get("min_speakers") or None,
                                       max_speakers=tc.get("max_speakers") or None, return_embeddings=True)
        return {"model": name, "embeddings": emb or {},
                "turns": [[round(a, 3), round(b, 3), spk] for a, b, spk in zip(df["start"], df["end"], df["speaker"])]}

    def _run(self, path: str, tc: dict, t0: float) -> dict:
        import torch
        import whisperx

        audio = whisperx.load_audio(path)
        t1 = time.time()
        result = self._model(tc).transcribe(audio, batch_size=tc["batch_size"], language=tc["language"])
        t2 = time.time()
        align_model, meta = self._aligner(tc["language"])
        result = whisperx.align(result["segments"], align_model, meta, audio, "cuda",
                                return_char_alignments=False)
        t3 = time.time()
        out = {"segments": result["segments"], "dur": len(audio) / 16000,
               "gpu": torch.cuda.get_device_name(0),
               "timings": {"load_s": round(t1 - t0, 1), "transcribe_s": round(t2 - t1, 1),
                           "align_s": round(t3 - t2, 1)}}
        if tc.get("diarize"):
            out["diarization"] = self._diarize(audio, tc)
            out["timings"]["diarize_s"] = round(time.time() - t3, 1)
        return out

    @modal.method()
    def transcribe(self, audio: bytes, suffix: str, tc: dict) -> dict:
        t0 = time.time()
        path = f"/tmp/audio{suffix}"
        with open(path, "wb") as f:
            f.write(audio)
        return self._run(path, tc, t0)

    @modal.method()
    def transcribe_url(self, url: str, tc: dict) -> dict:
        import subprocess

        t0 = time.time()
        path = "/tmp/audio.wav"
        # Same extraction as transcribe.extract_audio: gaps filled with silence, 16 kHz mono.
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", url, "-vn",
                        "-af", "aresample=async=1:first_pts=0", "-ac", "1", "-ar", "16000", path],
                       check=True)
        out = self._run(path, tc, time.time())
        out["timings"]["fetch_s"] = round(time.time() - t0 - sum(out["timings"].values()), 1)
        return out

    @modal.method()
    def diarize(self, audio: bytes, suffix: str, tc: dict) -> dict:
        """Speaker turns alone, for a stream transcribed before diarization was on."""
        import torch
        import whisperx

        t0 = time.time()
        path = f"/tmp/audio{suffix}"
        with open(path, "wb") as f:
            f.write(audio)
        samples = whisperx.load_audio(path)
        t1 = time.time()
        return {"diarization": self._diarize(samples, tc), "dur": len(samples) / 16000,
                "gpu": torch.cuda.get_device_name(0),
                "timings": {"load_s": round(t1 - t0, 1), "diarize_s": round(time.time() - t1, 1)}}
