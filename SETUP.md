# Setting up clipper on another machine

clipper runs in three roles. A machine can do more than one.

| Role | What runs there | Needs |
|---|---|---|
| **GPU worker** | `clipper worker`: transcribe, score, segments, then Shorts (and clips, if `[review] include` has `"picks"`) to review, then long segments to review | NVIDIA GPU (8 GB+), the NAS share, ffmpeg |
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

## 2b. Transcribe on Modal (no local GPU)

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

**Watching Modal.** Every Modal call also writes a start and an end line to `[paths]
modal_runs` (`logs/modal_runs.jsonl`). `clipper modal` (and the war room's Modal panel) shows
GPUs up, runs going now, failures, recent runs, each archive's progress and ETA, and the
month's spend from Modal's own billing: metered, credit, billed, the last 24 h and the rate
over the last 3 hours. The per-run estimates are only a guide; billing is the real number.
The bot DMs (`[notify]` kind `spend`) each time the month passes a multiple of `[modal]
alert_every_usd` (25); set `monthly_budget_usd` to show the month against your own ceiling.
The process that serves the war room (the bot) needs the Modal token, as the worker does.
On a new install, `clipper modal --backfill` adds the runs from before the log, once.

## 2c. Narration voice (ElevenLabs, optional)

`clipper narrate voice <take>` re-voices a performance (a phone take, a webcam clip, an audio
file) with the ElevenLabs Voice Changer: the words, timing and delivery stay the performer's,
the voice becomes the character's. A video comes back as `<take>.voiced.mp4` with the picture
untouched. The performer's accent carries through.

1. Make an ElevenLabs account (a paid tier for commercial use; WAV/PCM 44.1 kHz output needs
   Pro). Profile → API keys → create a key (it needs the Voice Changer and Voices permissions).
   Paste it, alone on one line, into `secrets/elevenlabs_api_key.txt` (gitignored), or set the
   Windows *user* environment variable `ELEVENLABS_API_KEY`, which wins if both are set.
2. Pick the voice: `clipper narrate voices --accent british --gender female --age old` lists your
   voices and matching library voices. Add a library voice with
   `clipper narrate voices --add OWNER:VOICE_ID --name "Mother Moo"`.
3. Put the printed id in clipper.toml:
   ```toml
   [narrate]
   voice_id = "..."
   ```
4. `clipper doctor` checks the key, the plan and the voice.

Takes longer than `[narrate] max_s` (300 s) are split at pauses first.

`clipper narrate speak script.txt` reads a script in the same voice (text to speech,
`[narrate] tts_model` = eleven_v3; inline cues like `[sighs]`, `[pause]`, `[whispers]` steer the
delivery) and writes `script.read.mp3`. The performer can then lip-sync to it on camera, so the
face and timing are theirs and the voice and accent are the read's.

## 2d. Phone access (Tailscale)

The war room listens on localhost only. To open it from the phone anywhere, put it on your
tailnet with `tailscale serve`: HTTPS at the machine's tailnet name, reachable only by your
own Tailscale devices, nothing on the LAN or the internet, no port forwarding.

1. Install Tailscale on the machine that runs the bot (https://tailscale.com/download) and
   on the phone (App Store / Play Store), and sign in to the same account on both.
2. In the Tailscale admin console, DNS page: turn on MagicDNS and HTTPS certificates
   (`tailscale serve` asks for this the first time if it is off).
   The first `on` may also print a link to enable Serve for the tailnet: open it, allow, and
   the command carries on.
3. `clipper warroom --tailnet on`. It makes a war room token in `secrets/warroom_token.txt`
   if `[warroom] token` is empty, starts `tailscale serve` (it persists across reboots), and
   prints the link to open once on the phone (`https://<name>.ts.net/?key=...`). The key
   is printed only there; the phone keeps a cookie for a year.
4. Restart the bot (`deploy\discord\bot-task.ps1 -Restart`) so its war room asks for the
   token, and set `[warroom] tailnet = true` in clipper.toml.
5. On the phone, open the link, then Share / menu → Add to Home Screen.

Check: `clipper doctor --role bot` (fetches the war room through the tailnet name), or
`clipper warroom --tailnet status`. Off again: `clipper warroom --tailnet off`. When the bot
moves to the NAS (#9), do the same there with the Tailscale container.

## 3. The NAS recorder

`bash deploy/record/deploy.sh` from the repo root (ssh alias `nas`). It copies clipper.toml
to the NAS; the recorder only reads `[record]` and `[kick]`. Re-run it after changing
`[record] channels`.

## Adding a show

Add a `[shows.<key>]` table to clipper.toml (see the end of clipper.example.toml):
`channels = ["<kick slug>"]`, `[shows.<key>.meta] show_name`, and
`[shows.<key>.scoring] stream_context` / `known_figures`. For Shorts, measure the show's
screen layouts into `[shows.<key>.shorts.layouts.<name>]` (panels of the frame, as fractions), one per
scene the show switches between: a Short that crosses a scene cut changes layout there.
Save a reference screenshot of each one from a recorded stream (`clipper shorts <stream>
--save-example <layout> --at <seconds>`); `clipper doctor` warns while any is missing.
A layout that frames an existing scene another way (say, only the webcams of a watch scene)
gets `pick = false`: scene detection never chooses it and it needs no screenshot; pick it by
hand with `clipper shorts <stream> --ids <vNN> --layout <name>`, the Kitsu Layout column or a retake.
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
   that second verification lifted the cap for Studio *and* the API: uploads went through
   again right away.
3. **Expect a small daily upload cap.** `uploadLimitExceeded` ("The user has exceeded the
   number of videos they may upload") is YouTube's limit on the *channel*, not the API
   quota (that one is `quotaExceeded`). Private uploads count too. The window looks
   rolling (about 24 h from each upload), and the API hit it at 8 uploads in 24 h on the
   new channel. It grows with the channel's history and verification; YouTube doesn't
   publish the number. Keep `[schedule] max_per_day`, `[youtube] max_per_run` and `max_per_24h` under it
   (`max_per_24h` counts every upload in the last 24 h, so hourly automatic runs stay under too).
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
   `secrets/youtube_playlists.json`. If you delete one in Studio, the next add finds it gone,
   looks the name up again and makes a new one if needed. A playlist renamed in Studio keeps
   its id, so videos keep going into it. Playlist names come from each item's Kitsu
   "Playlist" column.
7. **Source links** (`[youtube] source_links`, on by default) add a line such as "Full
   segment: <link>" to a clip's or segment's description once its longer video is public.
   They are not in Kitsu. Each upload run edits at most `link_updates_per_run` (20)
   descriptions of videos already up (about 51 quota units each).
   `clipper upload --links --dry-run` shows what it would change. Change the wording per show
   with `clip_source` / `segment_source` in `[shows.<key>.youtube]`.

## TikTok drafts

`clipper tiktok` sends scheduled Shorts to the TikTok app's drafts (Content Posting API,
Upload route). You open the notification on the phone, paste the caption and post. TikTok's
API takes no caption for drafts, so clipper posts it to Discord (`[tiktok] caption_channel_id`,
else the `[record]` alert webhook or channel) and keeps it in schedule.json.

1. **Developer app** at developers.tiktok.com, owned by an organization: platform Desktop,
   products **Login Kit** (redirect URI `http://localhost:3455/callback/`, the port is
   `[tiktok] redirect_port`) and **Content Posting API** (leave Direct Post off), scopes
   `user.info.basic` and `video.upload`. It needs a website, privacy and terms URL; ours are
   pages on byvfx.com (docs/tiktok-app-pages.md). Testing works in **Sandbox** with the TikTok
   account added as a target user.
2. **Keys**: save `{"client_key": "...", "client_secret": "..."}` as `secrets/tiktok_client.json`.
3. **Sign in** once, as the TikTok account: `clipper tiktok --sign-in`. The token lands in
   `secrets/tiktok_token.json`; it refreshes itself and the sign-in lasts 365 days
   (`clipper doctor` warns two weeks before).
4. **Send**: `clipper tiktok --items kick-131151926/v01 --dry-run` shows the file and caption;
   without `--dry-run` it sends. `clipper tiktok` lists what was sent and marks drafts you
   posted as posted. The draft arrives as a notification in the TikTok app's **Inbox**
   (System notifications), not under Profile > Drafts; it becomes a draft once opened.
5. **Captions**: create a Discord text channel (ours is #tiktok; the bot can't create channels)
   and set `[tiktok] caption_channel_id`. Each send posts a note and then the bare caption, so
   Copy Text on the phone copies just the caption. `clipper tiktok --caption <item>` posts it again.
6. **Automatic** (optional): `clipper tiktok --auto` sends the next unsent scheduled Shorts, earliest
   YouTube slot first, as many as `max_per_24h` allows (captions saying "host(s)" are skipped).
   Set `[tiktok] auto_send_minutes` (e.g. 60) and restart the bot to have it run that for you.

Limits: TikTok allows at most 5 pending drafts in 24 h; `[tiktok] max_per_24h` (default 3,
at most 5) stays under it. Most accounts can post up to 3 minutes. Direct posting without the phone step needs
TikTok's app audit (issue #29).
