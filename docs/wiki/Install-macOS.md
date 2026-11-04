Same steps with `brew install uv ffmpeg` and `uv sync --extra mac`, then set these
per machine (env vars, so the shared clipper.toml stays as it is):
```bash
export CLIPPER_TRANSCRIBE_BACKEND=mlx          # mlx-whisper on the Apple GPU
export CLIPPER_CUT_CLIP_CODEC=h264_videotoolbox
```
`[transcribe] mlx_model` picks the model (default `mlx-community/whisper-large-v3-turbo`,
downloaded on first use). mlx-whisper gives word timestamps itself; if WhisperX is also
installed (`uv pip install whisperx`), `mlx_align` refines them with its alignment model on
the CPU. `clipper doctor --role gpu` checks it, and `clipper worker` then runs every stage
there like on the Windows worker (no scheduled-task script for macOS yet: run it in a
terminal, or with launchd).
