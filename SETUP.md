# Setting up clipper on another machine

clipper runs in three roles. A machine can do more than one.

| Role | What runs there | Needs |
|---|---|---|
| **GPU worker** | `clipper worker`: transcribe, score, segments, cut, meta, thumbs, shorts, review | NVIDIA GPU (8 GB+), the NAS share, ffmpeg |
| **Bot** | `clipper bot`: Discord review cards, buttons, retakes | always on, the NAS share (or local streams), ffmpeg |
| **Recorder** | `clipper record watch` in Docker on the NAS | Docker; see `deploy/record/deploy.sh` |

After any setup, run `clipper doctor` (add `--role gpu`, `--role bot` or `--role nas`). It
checks everything below and prints a fix for each problem.

## 1. Windows GPU worker or bot

1. **Tools**
   ```powershell
   winget install astral-sh.uv
   winget install Gyan.FFmpeg      # the "full" build: libass captions, loudnorm, QSV/NVENC
   ```
   Open a new terminal so both are on PATH.

2. **Code**: copy the repo folder (everything except `streams/`, `.venv/`, `logs/`) to
   `C:\clipper` (or anywhere), then in it:
   ```powershell
   uv sync --extra gpu --extra discord   # GPU worker; a bot-only machine can skip --extra gpu
   ```
   The `gpu` extra pulls torch 2.8 with CUDA 12.8 from the PyTorch index (pinned in
   pyproject.toml). The CUDA toolkit is not needed; the wheels bring the runtime and cuDNN.
   If `clipper doctor` says torch sees no CUDA GPU, the CPU wheel slipped in:
   ```powershell
   uv pip install --reinstall torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
   ```

3. **Config**: copy `clipper.toml` from the main machine. Things to change per machine:
   - `[paths] streams`: the NAS share, e.g. `"//192.168.0.155/docker/clipper/streams"` or a
     mapped drive. Every machine must see the same streams folder.
   - `[cut] clip_codec`: `h264_qsv` needs an Intel GPU; on an NVIDIA-only desktop use
     `h264_nvenc` (driver 610+ for ffmpeg 8.1) or `libx264`. `clipper doctor` test-encodes it.
   - `[transcribe] compute_type = "int8_float16"` and `batch_size = 4` on cards under 8 GB.
   Anything can also be set per machine with an env var `CLIPPER_<SECTION>_<KEY>`, e.g.
   `CLIPPER_CUT_CLIP_CODEC=libx264`, so one shared clipper.toml can stay identical.

4. **Prompts and secrets**: copy `prompts/` (it comes with the repo) and `secrets/`
   (`discord_token.txt`, `youtube_client_secret.json`, `youtube_token.json`; only the
   machine that uploads needs the YouTube files).

5. **Keys** as *user* environment variables (System > About > Advanced system settings >
   Environment Variables, or):
   ```powershell
   [Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', '<key>', 'User')
   [Environment]::SetEnvironmentVariable('KITSU_TOKEN', '<Kitsu bot token>', 'User')
   ```
   Add `ANTHROPIC_WORKSPACE_ID` if the key isn't scoped to a workspace. Open a new terminal.

6. **Fonts**: thumbnails and Short captions use Impact and Bahnschrift, both standard on
   Windows. On Linux install `ttf-mscorefonts-installer` (Impact) and Roboto Condensed.

   **Photos** (optional): tabloid covers can use a portrait of someone the show talks about
   who is never on screen. Make a folder every machine can reach (e.g. `photos` beside
   `streams` on the NAS), add one head-and-shoulders crop per person named `<Person>.jpg`
   (the whole picture is treated as the face, so crop out watermarks and logos), and set
   `[thumbnail] photos` to it. The model may then use one for a cover's face or teaser box;
   `clipper thumbs <stream> --ids s04 --teaser-photo "Hasan Piker"` sets one by hand.

7. **Check**: `uv run clipper doctor --role gpu` (or `--role bot`).

8. **Run unattended** (scheduled tasks at logon, restarted if they exit, no admin needed):
   ```powershell
   deploy\worker\worker-task.ps1 -Install     # GPU worker
   deploy\discord\bot-task.ps1 -Install       # Discord bot (only one machine runs it)
   ```
   `-Status`, `-Restart`, `-Stop`, `-Uninstall` work on both. Logs go to `logs\`.

## 2. macOS (Apple Silicon)

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

## 3. The NAS recorder

`bash deploy/record/deploy.sh` from the repo root (ssh alias `nas`). It copies clipper.toml
to the NAS; the recorder only reads `[record]` and `[kick]`. Re-run it after changing
`[record] channels`.

## Adding a show

Add a `[shows.<key>]` table to clipper.toml (see the end of clipper.example.toml):
`channels = ["<kick slug>"]`, `[shows.<key>.meta] show_name`, and
`[shows.<key>.scoring] stream_context` / `known_figures`. For Shorts, measure the show's
screen layouts into `[shows.<key>.shorts.layouts.<name>]` (panels of the frame, as fractions).
Add the slug to `[record] channels` to record it live. `clipper shows` lists every show and
its streams; the show's Kitsu production is created on its first publish.

If the show posts a pre-show on YouTube, add `[shows.<key>.preshow] youtube_channel =
"UC..."` (the channel id: the `channel_id` in `yt-dlp -J <any video url>`, or the
`channelId` in the channel page's source). `clipper preshow list` should then list its
recent pre-shows, and `clipper doctor` checks the feed. The bot polls it ([preshow]
poll_minutes) and runs new pre-shows on the bot's machine, transcribe included, so the
bot's machine needs the GPU extra (or `[transcribe] device = "cpu"`); set poll_minutes = 0
on a bot that shouldn't. yt-dlp needs deno for YouTube; `uv sync` installs it (yt-dlp's
`deno` extra). Without it YouTube answers downloads with HTTP 403 partway through.

## A new YouTube channel

What we learned bringing up Lolcow Cinema (October 2026). A new channel is held back in
ways the API reports badly, so do these before the first big upload run.

1. **Sign in**: `clipper upload --sign-in` once, on the machine that uploads. The token
   lands in `secrets/youtube_token.json`.
2. **Verify the channel** at https://www.youtube.com/verify (phone). Without it custom
   thumbnails are refused (403); videos still go up, and `clipper upload --thumbnails` sets
   the covers afterwards. Verification can take more than one round: if Studio later
   refuses an upload, it may offer another verification link. Follow it. On Lolcow Cinema
   Studio accepted uploads again right after that second verification.
3. **Expect a small daily upload cap.** `uploadLimitExceeded` ("The user has exceeded the
   number of videos they may upload") is YouTube's limit on the *channel*, not the API
   quota (that one is `quotaExceeded`). Private uploads count too. The window looks
   rolling (about 24 h from each upload), and the API hit it at 8 uploads in 24 h on the
   new channel. It grows with the channel's history and verification; YouTube doesn't
   publish the number. Keep `[schedule] max_per_day` and `[youtube] max_per_run` under it.
   A refused upload leaves nothing behind on YouTube or in schedule.json, so it is safe to
   rerun later.
4. **Past the cap, upload in Studio** (it may still accept uploads when the API is
   refused): `clipper upload --next 5` lists what goes out next with each file; drag the
   file into Studio, set it Private, skip the details, then
   `clipper upload --attach <item> <link>` sets the title, description and tags, adds
   the thumbnail, captions, playlist and Kitsu link, and records the video as uploaded so
   clipper never sends it twice.
5. **Publishing through the API** (`videos.update` to public) worked on our Cloud project
   before any API audit, so `[youtube] manual_publish` can be turned off once you trust
   the uploads. Before that, check the first private uploads for Content ID claims in
   Studio. The Data API can't see claims; it only shows region blocks.
6. **Playlists** are created on first use and their ids cached in
   `secrets/youtube_playlists.json`. If you delete or rename one in Studio, delete its line
   there too, or videos will fail to be added to it. Playlist names come from each item's
   Kitsu "Playlist" column.
