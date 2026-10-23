"""LatentSync on Modal: lip-sync a video to an audio track (`clipper perform lipsync`, #58).

Deploy once per change to this file: clipper perform lipsync --deploy

LatentSync 1.6 (ByteDance, open source) redraws only the mouth region so it matches the audio;
the rest of the frame is the input's. It needs ~18 GB of VRAM (an L40S). The image clones the
repo, installs its pinned requirements, downloads the checkpoints from Hugging Face and runs
the repo's own demo once on a GPU, so every model the pipeline fetches on first use (VAE, face
detection) is baked in. Output frame rate is the pipeline's (25 fps).

Self-contained (no clipper imports): `modal deploy` loads it as a top-level module.
"""

from __future__ import annotations

import modal

APP_NAME = "clipper-lipsync"
REPO = "/opt/LatentSync"
GPU = "L40S"


def _bake() -> None:
    import subprocess

    subprocess.run(["python", "-m", "scripts.inference", "--unet_config_path", "configs/unet/stage2_512.yaml",
                    "--inference_ckpt_path", "checkpoints/latentsync_unet.pt",
                    "--video_path", "assets/demo1_video.mp4", "--audio_path", "assets/demo1_audio.wav",
                    "--video_out_path", "/tmp/bake.mp4", "--inference_steps", "5", "--guidance_scale", "1.5"],
                   cwd=REPO, check=True)


image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0")
    .run_commands(f"git clone --depth 1 https://github.com/bytedance/LatentSync {REPO}",
                  f"pip install -r {REPO}/requirements.txt")
    .run_commands(f"cd {REPO} && huggingface-cli download ByteDance/LatentSync-1.6 whisper/tiny.pt "
                  "latentsync_unet.pt --local-dir checkpoints")
    .env({"HF_HOME": "/models/hf"})
    .run_function(_bake, gpu=GPU)
)

app = modal.App(APP_NAME, image=image)


@app.function(gpu=GPU, timeout=2 * 3600, scaledown_window=30)
def lipsync(video: bytes, audio: bytes, steps: int = 20, guidance: float = 1.5, seed: int = 1247) -> dict:
    import subprocess
    import time

    t0 = time.time()
    with open("/tmp/in.mp4", "wb") as f:
        f.write(video)
    with open("/tmp/in.wav", "wb") as f:
        f.write(audio)
    subprocess.run(["python", "-m", "scripts.inference", "--unet_config_path", "configs/unet/stage2_512.yaml",
                    "--inference_ckpt_path", "checkpoints/latentsync_unet.pt", "--video_path", "/tmp/in.mp4",
                    "--audio_path", "/tmp/in.wav", "--video_out_path", "/tmp/out.mp4",
                    "--inference_steps", str(steps), "--guidance_scale", str(guidance), "--seed", str(seed),
                    "--enable_deepcache"], cwd=REPO, check=True)
    with open("/tmp/out.mp4", "rb") as f:
        return {"video": f.read(), "seconds": round(time.time() - t0, 1)}
