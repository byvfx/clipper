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
- Never use the word "hosts" in viewer-facing text (was "rarely" until 2026-10-20) → `[scoring] house_style` rules, appended to all viewer-facing prompts.
  Put future style notes there. Co-hosts are **Andy (Warski)** and **PPP**. Never call PPP by any other name (user,
  2026-10-19; Kick bio says "Andy Warski and PPP"). Who-looks-like-what is in [shows.kino.scoring] stream_context.
- Bleep: `[bleep] mode = "bleep"`, `profanity = true` (all forms of "fuck"), slurs always. Captions/titles masked.
- YouTube API uploads are NOT locked private on this project (user made one public in Studio). Audit status unconfirmed.

## Last run (finished 23:41)
- kick-131151926 fully recut with profanity bleeps, captions re-masked, and republished to Kitsu
  (`--no-notify`): all 23 items at new revisions, 23 thumbnails attached, compilation playlists updated.
  Existing Discord cards were not re-posted; their Kitsu links show the new previews.
  Log: `streams/kick-131151926/recut_profanity.log`.

## Backlog push (started 2026-10-19 22:20)
- User wants the approved backlog (10/8-10/17 streams) on YouTube. clipper.toml `[schedule]`:
  segments every day at 14:00/16:00/20:00 (was mon-fri 16:00), so days fill to max_per_day 6. Revert
  when the backlog clears (comment in the file). Replan: 97 scheduled, 65 left for later replans
  (45 pending review, the rest no slot yet; compilations come after their segments, Sat 14:00 only).
  Backup: schedule.before_backlog_2026-10-19.json. Bot restarted 22:16 so its replans use the new slots.
- First batch (`clipper upload --max 6`, private, manual_publish), Tue 10/20 slots, publish in Studio:
  10:00 Short 4vnv3JOsW2k, 12:00 ihRedRw66oU, 14:00 -V1wTp0A3ok, 16:00 r8xVoG7DC1s, 18:00 XztAeLsQvZ8,
  20:00 btWh90PKYAQ. All with captions, playlist, Kitsu link; covers on all but the Short. Log:
  logs/upload_backlog_2026-10-19.log. **User is watching these for Content ID blocks before more go up.**
  The Short's title still says "The hosts disagree" (old approved draft).
- Bot's pre-show poll 404 (fixed 2026-10-19): from 17:23 the YouTube RSS feed for the Kino channel
  (`feeds/videos.xml?channel_id=UCy58Oc...`) returned 404 on every poll (some other channels' feeds
  were fine; the channel itself was up). Not a config problem. `preshow.feed()` now falls back to
  `yt-dlp --flat-playlist` on the channel's /videos page when RSS fails (logs one INFO line;
  `published_at()` looks up the date only for title matches not yet grabbed). The 10/19 pre-show
  (yt-RGPltEFULV0) was already grabbed. No bot restart needed: the bot runs `clipper preshow check`
  as a fresh process each poll, so polls exit 0 again from 22:37.

## Thumbnails 10/10, 10/13, 10/15 (2026-10-19)
- kick-131498615 (10/10) had no covers either: 27 made ($1.10), 24 attached in Kitsu (3 compilations on
  disk only), checked against Kitsu.
- kick-132040888 (10/13) had no covers: `clipper thumbs` made 33 ($1.42, log batch_thumbs.log) and
  attached 28 in Kitsu (its 5 compilations aren't Kitsu items, so their .jpgs sit on disk only).
- kick-132380636 (10/15) already had all 21 attached. Both checked against Kitsu (main preview = our .jpg).
- Nothing from either stream is on YouTube yet; `clipper upload` sets the covers when they go up.

## Cover photos (2026-10-20)
- Problem: on the 10/16 404 stream (kick-132548228) s04 "Hasan debunks himself" had no usable Hasan
  frame, so its cover face was someone else, and side boxes reuse each video's cover face: 21 covers showed
  the wrong person beside the Hasan teaser. The user's Kitsu retakes on s12/s13 re-picked but could not fix
  it (side boxes aren't the item's own pick).
- Built `[thumbnail] photos`: a folder of `<Person>.jpg` head-and-shoulders crops (whole picture = face),
  here `//192.168.0.155/docker/clipper/photos` (in clipper.toml). Entries can carry `photo` (main face) and
  `teaser_photo` (beside the teaser on other covers). Prompt `thumbnail_enquirer_v3` (now the default)
  lets the model choose them when the person isn't on screen. Set by hand, no model calls:
  `clipper thumbs <stream> --ids s04 --teaser-photo "Hasan Piker"` (`""` clears). Doctor checks the folder.
- Photos: Hasan Piker (user-supplied; site watermark and logo cropped out).
- Applied: s04 teaser_photo = Hasan Piker, all 23 covers re-rendered, 22 attached in Kitsu (the s12/s13
  retake asks are covered). Backup: thumbnails.before_photos.json. None of these are on YouTube yet.

## Upload test (2026-10-20)
- User asked to upload 10 unslotted segments and make them public via the API. Pinned 10 in Kitsu
  ("Publish at" 10/20 12:00-16:30, by "Claude (upload test)"), replanned, `clipper upload --max 10`.
  Backup: schedule.before_upload_test_2026-10-20.json. Log: logs/upload_test_2026-10-20.log.
- 2 went up: kick-132380636/s10 (vM_AZRUCAIU), kick-132548228/s14 (BOuoEjNO6VM). Then YouTube refused with
  `uploadLimitExceeded` (channel upload cap, private uploads count, not API quota): 8 uploads in 24 h
  (6 on 10/19 22:18-22:21 + these 2). Retried 11:38 after the user verified the account: same error.
- **API publishing works**: videos.update privacyStatus=public on both succeeded (oEmbed 200). So
  `manual_publish` may no longer be needed (user's call).
- User chose to stagger as planned: the other 8 (s12-s15 of kick-132380636, s15-s18 of kick-132548228)
  were unpinned, their 10/20 entries dropped from schedule.json and replanned; they are back in the
  queue (no free segment slot in 21 days for now). Jerry Banfield playlist gets created when s17 uploads.
- User deleted the "Kino Casino Hosts" playlist; its only item (kick-132380636/c05_t14, not uploaded) now
  has Playlist "Kino Casino" in Kitsu, the stale id is out of youtube_playlists.json, and an empty public
  "Kino Casino" playlist was created for it. That clip's title still says "hosts".

- Built `clipper upload --next N` (what goes out next, with files) and `--attach ITEM LINK` (a video the
  user uploaded in Studio gets title/description/tags, thumbnail, captions, playlist, Kitsu link and is
  recorded uploaded with source "studio", publish_at = now so its slot frees up). Test: user uploading
  kick-132380636/s12 in Studio. User re-verified the channel after a Studio error (10/20 ~12:00); Studio
  then accepted uploads. Studio upload KghV1FnuK2k attached to s12 and checked (title, tags, cover,
  captions, playlist, Kitsu URL). Then `clipper upload --max 1` worked too (kick-131151926/v02_t42 Short,
  eSa1J0PK2t4, 10th upload in 24 h): the second verification lifted the API cap as well. Committed 9ecff7b.
- Backlog via API, 13:00: pinned 5 unslotted segments for 10/20 13:30-15:30 ("Claude (backlog upload)"),
  `clipper upload --max 5`: all up, private (esCySfEIUJE, 6-83Js5jPx4, aN_CabrkUXU, Bv5qbmYe_W8,
  XJXgU5Dv-RQ). 15 uploads in 24 h, no refusal. Backup schedule.before_backlog5_2026-10-20.json, log
  logs/upload_backlog_2026-10-20.log.
- 15 a day (user, 14:45): clipper.toml [schedule] hourly 08:00-22:00 (11 segment slots, Short 10:00, clips
  12/18, compilation daily 21:00), max_per_day 15, min_gap 60; [youtube] max_per_run 15. Revert notes in
  the file; backup schedule.before_15_per_day_2026-10-20.json (the clipper.toml copy was deleted;
  the old values are in the comment).
  `clipper schedule --reshuffle`: 171 scheduled through 11/07, 1 compilation waiting. Bot restarted 14:48.
  "hosts" cleanup (user, 17:00): the 4 titles retitled "Andy and PPP ..." in Kitsu; 61 scheduled
  descriptions rewritten (54 via Kitsu, 7 compilation drafts on disk with .before_hosts_fix.json backups;
  script and log in this session's scratchpad), 3 descriptions' profanity masked. Ashton = PPP. house_style is
  now "Never use hosts" (clipper.toml, config.py, example, thumbnail_enquirer_v3). Bot restarted 17:03.
  Still on YouTube: Short 4vnv3JOsW2k "The hosts disagree" (issue #3). `clipper meta` masks descriptions, but
  Kitsu text from before profanity = true isn't re-checked at upload: issue #8 (mask at upload time).

## Vertical Short covers (issue #1, 2026-10-20)
- `thumbs.compose_vertical`: 1080x1920 tabloid cover per Short from its clip's thumbnails.json choice
  (masthead, green band, headline split by `fill_lines` to fill the width, face to the bottom, inset and
  badge kept ~380 px above the bottom for YouTube's mobile title overlay; no side teasers; up to 2.5x
  zoom). Drawn by `clipper thumbs` (with the clip covers), by `clipper shorts` after rendering, and by
  `clipper thumbs <stream> --shorts` (just those, attaches just those in Kitsu). Upload/Kitsu pick up
  `shorts/<name>.jpg` like any cover.
- Done on kick-131151926 only (`--no-kitsu`). Other streams: `clipper thumbs <stream> --shorts`.
- Not yet known: whether `thumbnails.set` works on Shorts through the API (Studio allows it). The next
  scheduled Short upload will tell; a refusal is only a warning. `clipper upload --thumbnails` would also
  try the already-uploaded Shorts (only if the user asks).

## To do next
Open work is now tracked as GitHub issues (byvfx/clipper #1-#7, created 2026-10-20): Shorts covers,
backlog catch-up, "hosts" in t20 titles, stale playlist cache, source links, Kitsu republish (0 below),
MacBook setup (6 below). Add new work there; this list stays for history.
0. **Republish re-cut videos to Kitsu (user said not yet, 2026-10-19).** Segments + compilations of
   kick-132733064, -133040747, -133095550, -133112185 were re-cut (seek fix, see Gotchas) and SRTs
   rewritten; Kitsu still has the old previews with the silent 2-4 s head (132733064: 24 wfa,
   1 approved, 1 retake; 133112185: 10 wfa; 133095550: published just before the re-cut;
   133040747 is a DSP test show, never published). Thumbnails and meta text were not redone.
1. ~~Clear upload state for the videos the user deleted on YouTube~~ — done 2026-10-17: kick-132380636
   c04_t49/c05_t14/c06_t32 and kick-131151926 c01_t20 (verified gone via API) reset to scheduled,
   Kitsu "YouTube URL" blanked, `clipper schedule` rerun. Backup: `schedule.before_reset_2026-10-17.json`.
   kick-131151926 items are all "pending" again (recut revisions need re-approval). Nothing uploads
   until someone runs `clipper upload` (no scheduled upload job).
2. Post Blade compilation's Discord card (it was skipped): `clipper review notify kick-131151926 --keys comp-blade-in-vegas` (confirm with user first).
3. Optionally rerun `clipper meta kick-131151926` (~$0.90) so titles follow house_style; publish only
   overwrites Kitsu columns nobody edited.
4. ~~Add Andy and PPP to `[scoring] stream_context`~~ (done 2026-10-17; per-stream notes like
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
7. **Link videos to their longer source (user wants it, brainstormed 2026-10-19, not built).** In the
   description: clip -> its segment with a `?t=` timestamp at the clip's offset; segment -> its
   compilation; segments of one stream -> previous/next part (optional). Compilations already have
   chapters. Add the link at upload time as a per-show footer template (e.g. "Full segment: {url}"),
   not in the reviewed Kitsu description, because the parent's URL doesn't exist at review time. The
   schedule often puts a clip out before its segment, and with `manual_publish` the parent may still
   be private, so add a backfill pass: when a parent goes public, `videos.update` its children's
   descriptions (50 quota units each); run it from the upload/sync loop. No Kick VOD links (they send
   people off YouTube and may expire). Shorts are out of scope (user not worried): the API can't set
   the Shorts "related video" field or pin comments, and links in Shorts descriptions aren't clickable.

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

## Pre-shows (built 2026-10-19)
Kino posts a pre-recorded pre-show on its YouTube channel "The Kino Casino" (@TheKinoCasinoIncognito,
UCy58OcrZ-Y6ebDYKcJAHyxw; the older @TheKinoCasino stopped in Aug): Mon/Wed/Fri, 7-25 min, published
11:45-14:45 PT (once 18:47, once the next morning). The same video opens the Kick stream (~0:05-0:35),
where segments marks it kind "intro" and skips it. User wants it uploaded **whole, no clips/Shorts,
reviewed, then posted ASAP**.
- clipper/preshow.py + `clipper preshow check [--run] | list | fetch <url>`; `[preshow]` config, per show
  `[shows.kino.preshow] youtube_channel`. Feed = YouTube RSS (no API quota). Grabs into
  `streams/yt-<video id>/` (source.mp4 720p60 + meta.json kind "preshow", channel = Kick slug).
- Stages `[preshow] stages`: transcribe, segments (one segment `s00`, Sonnet writes title/description,
  prompts/preshow_v1.md, ~$0.02), cut, meta (credit = [preshow] credit, playlist "{show} Pre-shows"),
  thumbs, review. Whole run ~5 min, ~$0.25. Kitsu: lands in that day's episode ("2026-10-19").
- **The bot polls** every `[preshow] poll_minutes` (5): `clipper preshow check --run`. The worker
  only processes yt-* streams it finds (worker task installed on this laptop 2026-10-20).
- Schedule: approved pre-show -> source "asap", publish_at = time of the schedule run, outside slots
  and spacing; moves with each replan until uploaded. `clipper upload` treats asap entries as due and
  uploads them **public at once** (no publishAt). Uploads are still manual.
- Covers for a one-video stream (no siblings to tease): band = [preshow] band ("Pre-show M/D!"),
  side boxes = its own teaser + chapter titles that don't repeat the headline/band (thumbs.own_context).
- yt-dlp needs deno for YouTube now (else 403 mid-download): pyproject uses `yt-dlp[default,deno]`;
  on this laptop deno was added with `uv pip install deno` (uv sync can't replace clipper.exe while
  the bot runs).
- First one: yt-RGPltEFULV0 (10/19). User fixed the cover by hand: main photo PPP at 170.5 s (hands on
  head), inset = Jer from Don's kick-133095550 c02 cover (copied in as
  thumbs/.../ext_jer_kick-133095550_c02.jpg), text "PPP STUNNED BY JER!", band "Pre-show 10/19!".
  The model had mixed up Andy and PPP (stream_context now describes both).
  **Posted 2026-10-19 15:24: https://youtu.be/eaKnICCK_Ps** (public, captions, playlist "Kino Casino
  Pre-shows"; cover set after the user phone-verified the channel, so custom thumbnails work now).
  Its Discord card still shows the first cover.
  **Blocked the same day**: YouTube made it unavailable and the only option Studio offered was to
  delete it, so the user deleted it (~16:30). Most likely a Content ID match on The Kino Casino's
  own upload of the same video (a full re-upload). Until that is sorted (e.g. Kino whitelisting Lolcow
  Cinema), uploading pre-shows will be blocked again. schedule.json still lists it as uploaded.
  User (2026-10-19): keep grabbing pre-shows for review; they are sorting the YouTube side out with
  Kino.
  **Re-uploaded 15:40 at the user's request: https://youtu.be/l5w5cmwJu1E** (public and playable at
  15:42, cover set). The first copy was blocked after it went up, so watch this one for the same block.
  Backup of the schedule before the reset: schedule.before_preshow_reupload_2026-10-19.json.
- **Uploads are private-only for now** (user, 2026-10-19): `[youtube] manual_publish = true` in
  clipper.toml; `clipper upload` sets no publishAt and never public. The user publishes in Studio
  (suspects API-set visibility misbehaves until the API audit). Pre-shows too.
- Bot restarted 15:03 on this code. Upload with `clipper upload --max 1` to send only the pre-show
  (plain `clipper upload` also sends everything else due within 48 h).

## Channel 404 News (permission from Don 2026-10-19)
- Don gave the go-ahead 2026-10-19: c404 goes through review → schedule like Kino (uploads still manual).
  The scheduler only takes Kitsu-approved items, so nothing of 404's is scheduled until it is approved.
- kick-132548228 (Oct 16 VOD, 5.27 h): 16 segments, 5 picks, compilations The Quartering (11 segments,
  2h59m; topics relabeled by hand, backup `segments.before_relabel.json`) and Jerry Banfield (2, 49m),
  23 covers, Shorts v01-v05. Published to Kitsu 2026-10-19 with `--no-notify` (log run_review.log).
  Not yet asked: Discord cards. Flag pick t32 (Jerry Banfield / Rosa Parks) for a careful look.
- kick-133095550 (Oct 18, recorded live): in Kitsu since 10/19. Its Kitsu previews predate the .mkv seek
  fix (silent 2-4 s head); the local re-cuts (what uploads) are fine. See To do 0.
  2026-10-20: 4 approved in Kitsu (c05_t08, s02, s04, comp-the-quartering), 19 pending. The Kino backlog
  filled every segment slot through 11/10, so the user asked for the shows to take turns: new
  `[schedule] mix_shows = true` (default; round-robin per kind, each show keeps its own order) and
  `clipper schedule --reshuffle` (re-places auto entries not yet uploaded; pinned/asap/uploaded stay).
  Ran it once (backup schedule.before_reshuffle_2026-10-20.json; 78 Kino entries moved, all 7 uploaded
  kept): s02 Baxter Bulletin Wed 10/21 16:00, c05_t08 Wed 10/21 18:00, s04 Destiny Thu 10/22 14:00.
  comp-the-quartering waits for its other 5 segments. Kino now fills oldest stream first again, so
  10/13's Blade segments got slots and 7 segments of kick-132380636 (10/15) fell past the 21-day horizon.
  Bot restarted 08:54 on this code (its replans use mix_shows; they don't reshuffle).
- Discord cards posted 2026-10-20 (user asked): kick-132548228 28 cards (thread 1562150123956609166;
  26 new, 2 were already there), kick-133095550 23 (thread 1562262846887170091). The 133095550 cards come
  from its old Kitsu previews (silent head). With discord.json present, the bot's refresh loop now syncs
  both streams, so approvals made directly in Kitsu reach the schedule too (cardsync.stream_dirs only
  covers streams that have cards).
- 2026-10-20 evening: user approved everything in the war room; all 51 items of both 404 streams are
  scheduled (10/20-11/04).

## First hands-off night (2026-10-20)
- User goal: get kino + c404 running fully hands-off (recorder → worker → Kitsu + Discord cards for both
  shows) before adding more shows.
- The GPU worker task was NOT installed until now (kick-133095550 had been run by hand). Installed
  17:47 with `deploy\worker\worker-task.ps1 -Install` (doctor --role gpu --offline: 21 ok). It began at
  once on last night's Kino recording kick-133260144 (7.54 h, est. $4-5); tonight's 404 recording
  kick-133423456 (started 17:00 PT) follows when the recorder marks it done. `[worker] notify = true`,
  so the review stage posts Discord cards per show thread.
- The worker runs the working tree, which includes another session's uncommitted worker.py/config.py
  edits (write_json retry on PermissionError; running/failed stages count as not done).
- Check tomorrow: `deploy\worker\worker-task.ps1 -Status`, logs\worker.log, both streams' run.json and
  discord.json, then approvals → schedule.

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
- ffmpeg backs input seeks off by 3/23 s when video has B-frames. `.ts` then lands on the keyframe
  at/after the target, `.mkv` (NAS recorder) on the one at/before it, so `copy_cut` seeks k+0.005 for
  .ts and k+0.15 otherwise (fixed 2026-10-19). Before that, every .mkv segment carried one extra GOP
  (2-4 s) of silent video at the head (bleeped audio started late) and SRTs/chapters ran early.
  Affected: kick-132733064, kick-133040747, kick-133095550, kick-133112185; all re-cut 2026-10-19
  (`clipper cut <id> segments compilations` + `clipper meta <id> --captions-only`, no model cost).
  `copy_cut` now fails the cut on a sync gap or wrong length instead of printing a warning.
- ffmpeg 8.1 NVENC needs NVIDIA driver 610+ (laptop has 596.72) → use `h264_qsv` locally.
- Bleeping uses asendcmd + a .cmd file (one big volume expression breaks past ~dozens of spans);
  spans ending before the cut point must be dropped.
- Kitsu's proxy rejects big uploads; previews over `[review] max_upload_mb` (480) re-encode to fit.
- review.py reads every `*.json` beside cut videos as sidecars; metadata lives in `metadata/<folder>/`.
- Windows: a file open in another process can't be replaced (fork sessions render previews from cuts).
- `schtasks /End /TN clipper-bot` leaves the supervisor's bot process alive (lock in streams/.bot.lock);
  to restart the bot, also stop the old bot-supervisor.ps1 powershell and its clipper.exe children.
- schedule: a compilation approved as its own Kitsu item needs segment_keys (fixed 2026-10-18).
- The bot runs `schedule.run` in-process (replan after a decision): scheduler changes need a bot restart.
- Retake planner can't edit cover layout; a note about the cover's boxes comes back "unsupported".

- Older Kino drafts/Kitsu titles that say "Ashton" stay as they are (user, 2026-10-19); PPP from now on.
