# Clipper handoff (2026-10-18)

Stream clipper for Kino Casino (Kick channel `kinocasinogaming`) → YouTube channel **Lolcow Cinema**.
Repo: `C:\clipper`, remote https://github.com/byvfx/clipper (private, pushed 2026-10-18). Python package
`clipper/`, config `clipper.toml` (gitignored; `clipper.example.toml` is tracked), prompts `prompts/`.
Plan doc: https://claude.ai/code/artifact/a9bb9e38-ced0-4455-8d8e-18a78122f45f
Keys are Windows **User** env vars (ANTHROPIC_API_KEY, KITSU_TOKEN); agent shells must load them, e.g.
`export KITSU_TOKEN="$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('KITSU_TOKEN','User')" | tr -d '\r')"`.
README.md (what it is) and CLAUDE.md (conventions for agents) were added 2026-10-19.
Project memory (`~/.claude/projects/C--clipper/memory/`) has the details; read MEMORY.md first.

## Pipeline (one stream = `streams/<id>/`, test stream `kick-131151926`)
`clipper fetch <kick url>` → `transcribe` (WhisperX, GPU) → `signals` (chat/loudness heat) →
`score` (Haiku Pass A + transcript scan, Sonnet Pass B) → `segments` (Opus topic split + compilations) →
`cut` (segments lossless w/ keyframe starts, compilations joined, picks re-encoded; slurs+"fuck" bleeped) →
`meta` (SEO title/description/chapters/tags + .srt) → `thumbs` (Enquirer tabloid covers) →
`review publish` (Kitsu on NAS 192.168.0.155:8090 + Discord cards) → `review sync` / Discord bot →
`schedule` → `upload` (YouTube). Discord bot runs always-on (`clipper bot`, scheduled task `clipper-bot`).

## User decisions (keep)
- Titles/descriptions/cut points approved as-is; fun descriptions are liked.
- Long video per recurring topic (compilations, e.g. "The Quartering" 3h13m) plus segments plus short picks.
- `third_party_audio = "allow_reactions"`, `audience_knowledge = "fans"`, Victor/blackjack segments stay.
- Kitsu is the record for review/edits (no separate DB). Discord approvals must keep working.
- Thumbnails: `[thumbnail] style = "enquirer"` (1990s tabloid: KINO CASINO masthead, no "THE NEW" roundel,
  no barcode, small "AS SEEN ON LOLCOW CINEMA" badge). Prompt `thumbnail_enquirer_v2` (face = headline subject).
- Avoid the word "hosts" in viewer-facing text → `[scoring] house_style` rules, appended to all viewer-facing prompts.
  Put future style notes there. Co-hosts are **Andy (Warski)** and **Ashton**.
- Bleep: `[bleep] mode = "bleep"`, `profanity = true` (all forms of "fuck"), slurs always. Captions/titles masked.
- YouTube API uploads are NOT locked private on this project (user made one public in Studio). Audit status unconfirmed.

## Last run (finished 23:41)
- kick-131151926 fully recut with profanity bleeps, captions re-masked, and republished to Kitsu
  (`--no-notify`): all 23 items at new revisions, 23 thumbnails attached, compilation playlists updated.
  Existing Discord cards were not re-posted; their Kitsu links show the new previews.
  Log: `streams/kick-131151926/recut_profanity.log`.

## To do next
1. ~~Clear upload state for the videos the user deleted on YouTube~~ — done 2026-10-17: kick-132380636
   c04_t49/c05_t14/c06_t32 and kick-131151926 c01_t20 (verified gone via API) reset to scheduled,
   Kitsu "YouTube URL" blanked, `clipper schedule` rerun. Backup: `schedule.before_reset_2026-10-17.json`.
   kick-131151926 items are all "pending" again (recut revisions need re-approval). Nothing uploads
   until someone runs `clipper upload` (no scheduled upload job).
2. Post Blade compilation's Discord card (it was skipped): `clipper review notify kick-131151926 --keys comp-blade-in-vegas` (confirm with user first).
3. Optionally rerun `clipper meta kick-131151926` (~$0.90) so titles follow house_style; publish only
   overwrites Kitsu columns nobody edited.
4. ~~Add Andy and Ashton to `[scoring] stream_context`~~ (done 2026-10-17; per-stream notes like
   costumes go in `CLIPPER_SCORING_STREAM_CONTEXT` for that run, not the file);
   ~~re-pick s17~~ (done 2026-10-18 via the first retake: Jasko's face).
5. Nice-to-have: daily "post kit" folder (video + cover + paste-ready title/description) for manual posting.
6. **Portability: prove it on a second machine.** Code side is done (pyproject extras per role, clipper.toml
   + CLIPPER_* env, `clipper doctor`, SETUP.md, deploy/). Nobody has followed SETUP.md on a clean box yet.
   Best first test: the MacBook (`[mac]` extra, `[transcribe] backend = "mlx"`), then `clipper doctor --role gpu`.
   Known gaps: no container for the GPU worker or bot (Windows scheduled tasks only, see deploy/worker and
   deploy/discord); deploy/*/env.local hold machine-specific values (keep them out of any shared copy);
   `[review] host` must be set per machine (no default since 2026-10-18); the NAS still runs the old
   recorder until `bash deploy/record/deploy.sh` is run between shows.

## Multi-show (done 2026-10-18)
One clipper.toml, one `[shows.<key>]` table per show (kino, c404, dsp, blade) at its end: `channels`
(Kick slugs) plus `[shows.<key>.<section>]` overrides (show_name, stream_context, known_figures...).
`config.for_stream(cfg, sdir)` picks the show from meta.json "channel"; the CLI, review/actions/bot,
schedule, upload and rumble all resolve per stream, so each show writes to its own Kitsu production.
All shows share Lolcow Cinema + one schedule.json. `clipper shows` lists them; `--show <key>` forces one.
Worker skips recordings of channels no show claims. `clipper.404.toml` is gone (it's `[shows.c404]`);
`clipper.before_shows.toml` is the pre-migration backup (delete once happy).
- dsp / blade stream_context were written from Kick bios + VOD titles only: review after a first run.
- NAS copy of clipper.toml (deploy/record/deploy.sh) doesn't need shows for recording, but any NAS
  stage that runs segments needs `prompts/segments_v2.md` + `compilation_v2.md` (default is v2 now).

## Channel 404 News test stream (kick-132548228, Oct 16 VOD, 5.27 h)
- transcribe → thumbs DONE 2026-10-18 (~$3.10). 16 segments, 5 picks, compilations The Quartering
  (11 segments, 2h59m; topics relabeled by hand, backup `segments.before_relabel.json`) and Jerry
  Banfield (2, 49m), 23 Channel 404 covers. User said hold publishing for now.
- When asked: `clipper review publish kick-132548228 --no-notify` (no --config needed now), then ask
  before Discord cards. Flag pick t32 (Jerry Banfield / Rosa Parks) for a careful look.

## Next projects (user wants these, in this order)
1. **WhisperX worker: DONE 2026-10-18.** `clipper worker` runs the whole pipeline on finished NAS
   recordings. Added `[transcribe] backend = "mlx"` (mlx-whisper, `uv sync --extra mac`, optional
   WhisperX CPU alignment; untested here, no Apple Silicon) and recorder segment retries
   (`[record] segment_retry_s` = 20: a failed segment holds the line and is retried on later playlist
   polls; simulated both recover and lose cases). NOT yet deployed to the NAS recorder: run
   `bash deploy/record/deploy.sh` when no show is live (the restart starts a new part). Original plan:
   `clipper worker` loop on the laptop (scheduled task at logon, keep awake) that
   finds NAS streams with audio.wav but no words.jsonl, pulls the audio over the NAS share, transcribes,
   writes words.jsonl back; NAS then runs the rest. Add a `backend` setting: `whisperx` (CUDA) and
   `mlx` (mlx-whisper on the user's MacBook Neo, Apple Silicon; WhisperX alignment still works there).
   First job: the live recording `kick-132733064` on the NAS (/volume1/docker/clipper/streams), 7.9 h,
   25 GB source.mkv, chat 123k msgs, signals/windows done, not transcribed. It also had ~64 s of
   missing video in ~30 two-second drops: worth making the recorder retry segment fetches.
2. **Vertical Shorts: BUILT 2026-10-18** (`clipper shorts <stream>`, clipper/shorts.py; worker stage
   between thumbs and review). Per pick: the clip window widened by whole transcript lines to 90-179 s
   (user wants 1.5-3 min Shorts; [shorts] window/min_s/max_s), snapped to word gaps, cut with bleeps,
   layout = stack of panel crops into 1080x1920 (per show: [shows.<key>.shorts.layouts], measured for
   kino watch/cams and c404 watch/desk; dsp/blade only have "center" until someone measures theirs),
   Haiku picks the layout from 3 frames (~$0.0002 each), ASS captions 3 words with the spoken word
   yellow (masked per [bleep]), two-pass loudnorm to -14 LUFS, fades. Output shorts/v<NN>_<id>_*.mp4 +
   sidecar, shorts.json state, metadata/shorts/ draft (clip draft + #Shorts). Kitsu: own shot v<NN>_<id>
   beside the clip, task type "Vertical", "Layout" column (edit + rerun `clipper shorts` re-renders),
   "Shorts" playlist. Schedule kind "short" (short_days/short_times, 10:00 daily, counts toward
   max_per_day). Upload: no srt/thumbnail for Shorts (captions burned in).
   kick-131151926 v01-v06 published to Kitsu (rev 2, pending review, no Discord cards);
   kick-132548228 v01-v05 rendered only (404 publishing on hold). None uploaded. Possible next: Discord layout button, per-scene layout switches
   within one Short.
2b. **Retake automation: BUILT 2026-10-18** (clipper/retake.py, prompts/retake_v1.md, `clipper retake`).
   A Kitsu "retake" + comment (or Discord Changes button) -> Sonnet plans edits from a fixed list (trim
   start/end of clip/Short/segment, Short layout, title/description/hashtags, thumbnail redo with the note
   as guidance (3x candidate frames), thumbnail words; else "unsupported") -> re-cut just that item
   (segment trims shift chapters and re-cut its compilations) -> new Kitsu revision (wfa) + "[clipper]
   Retake applied" note -> fresh Discord card with what changed. Each Kitsu comment handled once
   (streams/<id>/retakes.json). The bot runs it after every card refresh and on Changes buttons
   ([retake] auto). Try a note without changing anything: `clipper retake <stream> --item c03 --comment
   "..." --dry-run`. First live use: s17 thumbnail (Jasko face) and the 5 kick-132733064 intro trims.
3. **Portability: BUILT 2026-10-18**: `clipper doctor [--role gpu|bot|nas] [--offline]` (clipper/doctor.py:
   python, config/shows, streams write + free space, prompt files, ffmpeg filters + test-encodes the
   configured codecs, fonts incl. libass caption font, torch/CUDA/whisperx, keys/tokens, then Anthropic,
   Kitsu productions per show, Discord, Kick) and SETUP.md (Windows GPU/bot, macOS, NAS, adding a show).
   2026-10-18: [review] host default is now empty (was the NAS IP); clipper.toml keeps the real URL,
   the example toml has a placeholder, doctor fails and review.connect exits if it's unset. Doctor on the
   laptop (--role gpu): 24 ok, 0 failures; warnings are 60 GB free and 3 shows without a Kitsu production yet.
3b. **War room: BUILT 2026-10-18**: `clipper warroom` (clipper/warroom.py + clipper/web/warroom.html,
   stdlib HTTP, no build). http://127.0.0.1:8765/: Kick live status per show channel, counters, pipeline
   stage bars per stream, retake feed, bot health, review queue (tabs/filters, Approve/Changes/Reject/
   Hold, modal with preview video, cover, title/description/hashtags/thumbnail words/pin), 14-day
   schedule. Actions go through actions.py (Kitsu stays the record), replan like the bot, Changes starts
   `clipper retake`. The bot serves it ([warroom] with_bot), so it's up whenever the bot is.
4. **Automatic uploads: BUILT 2026-10-18, OFF.** `[youtube] auto_upload_minutes` (0 = by hand): the bot runs
   `clipper schedule` then `clipper upload` that often. Waiting on the user to choose a value. Old
   schedule had missed slots (replan fixes). API-upload publish rights still unconfirmed.
5. **YouTube Analytics: BUILT 2026-10-18.** `clipper analytics [--sign-in] [--show]` -> analytics.json
   beside schedule.json (Data API stats; 7-day views/avg view %/watch time/subs via Analytics API after
   a one-time `--sign-in`, not done yet). Pass B gets a <past_performance> block per show once
   [analytics] min_videos (5) have 7-day numbers. Bot refreshes it every auto_hours (24) once there are
   uploads. Nothing uploaded yet, so it's dormant.

## Gotchas learned
- Kick: yt-dlp VOD extractor 404s; resolve via channel videos API (kick.py). Chat history API ~2 req/s,
  429 with Retry-After: 0 → adaptive limiter.
- Stream-copy cuts must start on a keyframe (every ~4 s) or audio drifts; `.ts` seeking for re-encodes
  needs keyframe seek + output trim (cut.py handles both).
- ffmpeg 8.1 NVENC needs NVIDIA driver 610+ (laptop has 596.72) → use `h264_qsv` locally.
- Bleeping uses asendcmd + a .cmd file (one big volume expression breaks past ~dozens of spans);
  spans ending before the cut point must be dropped.
- Kitsu's proxy rejects big uploads; previews over `[review] max_upload_mb` (480) re-encode to fit.
- review.py reads every `*.json` beside cut videos as sidecars; metadata lives in `metadata/<folder>/`.
- Windows: a file open in another process can't be replaced (fork sessions render previews from cuts).
- `schtasks /End /TN clipper-bot` leaves the supervisor's bot process alive (lock in streams/.bot.lock);
  to restart the bot, also stop the old bot-supervisor.ps1 powershell and its clipper.exe children.
- schedule: a compilation approved as its own Kitsu item needs segment_keys (fixed 2026-10-18).
