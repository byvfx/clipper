`[transcribe] backend = "modal"` runs the same WhisperX (same pinned versions, large-v3, word
alignment) on a [Modal](https://modal.com) cloud GPU, so any machine can be the worker and the
transcripts match the local ones (99.9% of words identical on an 8 h test stream). The Starter
plan includes $30 of compute a month. Measured on that 8 h stream: L40S (default) 10 min,
~$0.34; L4 19 min, ~$0.26 (cheapest per hour of audio, for bulk); H100 8 min, ~$0.59.

1. Make a Modal account, then Settings → API Tokens → New token. Set the two values as
   Windows *user* environment variables `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` (or run
   `.venv/Scripts/python.exe -m modal token new`, which writes `~/.modal.toml`).
2. `uv sync --extra modal` (add it to the other extras you use; `--extra gpu` isn't needed for
   this backend).
3. `clipper transcribe --deploy` builds and deploys the app in `clipper/modal_app.py`. The
   first build takes several minutes (it bakes the models into the image); re-run it after
   changing that file.
4. In clipper.toml (or `CLIPPER_TRANSCRIBE_BACKEND=modal` on one machine):
   ```toml
   [transcribe]
   backend = "modal"
   modal_gpu = "L40S"      # T4 | L4 | A10 | L40S | A100 | H100
   modal_audio = "flac"    # lossless upload, ~60 MB per hour of audio; "opus" is smaller
   ```
5. `clipper doctor --role gpu` checks the package, the token and that the app is deployed.

Each run prints the GPU, the call time and an estimated cost, and keeps them under `"modal"` in
transcript.json. The estimate is an upper bound (it counts upload and cold start).

## Watching Modal

Every Modal call also writes a start and an end line to `[paths]
modal_runs` (`logs/modal_runs.jsonl`). `clipper modal` (and the war room's Modal panel) shows
GPUs up, runs going now, failures, recent runs, each archive's progress and ETA, and the
month's spend from Modal's own billing: metered, credit, billed, the last 24 h and the rate
over the last 3 hours. The per-run estimates are only a guide; billing is the real number.
The bot DMs (`[notify]` kind `spend`) each time the month passes a multiple of `[modal]
alert_every_usd` (25); set `monthly_budget_usd` to show the month against your own ceiling.
The process that serves the war room (`clipper serve`) needs the Modal token, as the worker does.

On a new install, `clipper modal --backfill` adds the runs from before the log, once.
