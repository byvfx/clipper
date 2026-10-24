# Clipper handoff (2026-10-18)

Stream clipper for Kino Casino (Kick channel `kinocasinogaming`) → YouTube channel **Lolcow Cinema**.
Repo: `C:\clipper`, remote https://github.com/byvfx/clipper (private, pushed 2026-10-18). Python package
`clipper/`, config `clipper.toml` (gitignored; `clipper.example.toml` is tracked), prompts `prompts/`.
Plan doc: https://claude.ai/code/artifact/a9bb9e38-ced0-4455-8d8e-18a78122f45f
Walkthrough (usage only, for presenting; Claude Docs, updated 2026-10-21): https://claude.ai/code/artifact/0d0e04da-54f6-4f15-bad0-2df72a286383
Walkthrough (usage only, for presenting; Claude Docs, updated 2026-10-21): https://claude.ai/code/artifact/0d0e04da-54f6-4f15-bad0-2df72a286383
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

## Roadmap: NAS hub (agreed 2026-10-20, issues only, nothing built)
NAS becomes the always-on hub, laptop optional. Issues: #9 war room on the NAS
(docs/brief-warroom-on-nas.md), #10 Tailscale access (user's choice, no port forwarding), #11 Modal for
WhisperX (user's choice), #12 worker and bot on the NAS (goal: laptop can sleep; NAS is a Pentium Gold
8505, 6 threads, 31 GB, Intel iGPU in Docker at /dev/dri, no CUDA), #13 Claude chat panel in the war room
(clipper actions only, no code changes). Tracking issue #14. #9 and #11 can start in parallel.
War room ease of life, also under #14: #15 watch a live show here or in a new window (user's
idea: quick access while live; the header's live chips become clickable), #26 pop out item videos / open
the full recording at a clip, #16 keyboard review, #17 bulk actions with dry run, #18 cover editor, #19 inline style checks,
#20 drag-and-drop schedule, #21 upload status, #22 notifications, #23 undo, #24 search, #25 phone
layout. User is big on ease-of-life features. Suggested first: #15-#18, #25, then #22.
TikTok (user wants an account, 2026-10-20), also under #14: #27 account/permission/content rules (user's
decisions; clipper can't create accounts), #28 send Shorts to the phone as drafts (route the user
chose: Content Posting API upload to inbox, user taps post), #29 direct posting after TikTok's app audit
(later, optional; unaudited direct posts are private only).

## TikTok drafts (#28, built 2026-10-21)
- User made the TikTok account and a developer app (Login Kit desktop + Content Posting API, scopes
  user.info.basic + video.upload, redirect `http://localhost:3455/callback/`). Privacy/terms copy for
  byvfx.com (Squarespace, "Not linked" pages /lolcow-privacy, /lolcow-terms): docs/tiktok-app-pages.md.
  Keys in `secrets/tiktok_client.json` (the user saved them as two plain lines; converted to JSON).
- `clipper tiktok --sign-in | --items <keys> [--dry-run] | [stream]` (clipper/tiktok.py): inbox upload
  (FILE_UPLOAD, 32 MB chunks over 64 MB), caption = Kitsu title + hashtags (minus #Shorts) + [tiktok]
  hashtags, masked; the API takes no caption for drafts, so it goes to Discord via record.Alerts
  ([tiktok] caption_channel_id, else the alert channel/webhook). Recorded as schedule.json
  platforms.tiktok {status sent|posted|failed, publish_id, caption, sent_at}; `clipper tiktok` asks
  TikTok's status endpoint and marks PUBLISH_COMPLETE as posted. Cap [tiktok] max_per_24h = 3 (TikTok:
  max 5 pending in 24 h). `rumble.save_platform` now takes the platform name. Doctor: check_tiktok.
- Picked by hand with --items until #27 (which Shorts, pace, hashtags) is decided; not in the bot loop.
- 2026-10-22: signed in (Sandbox app "clipper sandbox", account "Lol Cow Cinema"); first test draft
  kick-131151926/v01_t20 sent. Setup gotchas: Sandbox has its own key/secret, products, scopes and redirect
  URI (editing Production changes nothing for it); the website field is not the redirect URI; the form won't
  save until byvfx.com is DNS-verified (TXT at @, done) and a demo video is attached
  (docs/tiktok-demo-placeholder.mp4, untracked placeholder, not for review). Captions go to Discord #tiktok
  (1562904458575482980, user-made; the bot lacks Manage Channels), set in clipper.toml [tiktok];
  `--caption <item>` reposts. Draft status SEND_TO_USER_INBOX = in the app's Inbox notifications.
  Two early test messages in #tiktok (one-message format, one with the old 'hosts' schedule title) can be deleted.
- 2026-10-22 ~16:40 (user: "all the Shorts as drafts to my phone", chose 5/day automatic): new
  `clipper tiktok --auto` (next unsent scheduled Shorts by publish_at, up to the cap; skips captions with
  host(s)) and `[tiktok] auto_send_minutes` in the bot's refresh loop. clipper.toml: max_per_24h = 5,
  auto_send_minutes = 60. Sent 4 at once (kick-131151926 v02_t42/v03_t01/v04_t21, kick-132548228
  v01_t33); 47 left, ~10 days at 5 a day. Bot restarted 16:39 on this code (nothing recording).
  Unknown: whether TikTok counts posted drafts toward its 5 pending; if sends fail with spam_risk,
  lower max_per_24h.

## "Baring" -> "Bearing" (2026-10-22, user)
- Whisper writes the YouTuber Bearing as "Baring". kick-133597196 (10/21 Kino): fixed in Kitsu text of
  s08/s09/s10/comp-the-quartering (signed "Claude (Bearing spelling)"), s09's thumbnail text, captions,
  transcript/words/segments/metadata files, and all 25 covers re-rendered (`thumbs --compose-only`; the
  s09 and compilation teasers sit on every other cover, which is what the user's s11 retake meant).
  Backups: streams/kick-133597196/before_bearing_fix_2026-10-22/. kick-131151926: chapter line fixed in
  comp-the-quartering (Kitsu, before upload) and s11 (Kitsu + live YouTube description of JGV65Y-qUT8,
  user OK'd). Expect it again whenever Bearing comes up.

## Jerry Banfield archive (issue #44, 2026-10-22, side project, nothing built)
- For once the backlog is mostly caught up. Census done: ~3,650 long-form videos / ~1,740 h. That's his
  7 current YouTube channels (433 h, captions) plus his deleted main channel's Odysee mirror (1,310 h,
  2011-2024, needs WhisperX). Shorts are out of scope. Idea: index it, then cut "receipts" into Don's
  weekly Jerry segments (every c404 stream has one). Channel ids, formats, guardrails, costs and a
  < $2 pilot are in the issue.
- User FYI 2026-10-22: Lolcow Cinema is verified with advanced features, up to 100 uploads/day.
  `[youtube] max_per_24h` stays 15 (user: leaves room to try new things).
- Issue #45 (same day, same status): Papa Meat (MeatCanyon's second channel) story supercuts, ~715 h
  across Papa Meat, CreepCast, Meaty Magic, all with captions. The user loves his stories; he isn't a
  lolcow. Open: his permission (no commentary from us = reupload) and which channel it goes on.
- Both run on the user's desktop (user, 2026-10-22): it doubles as the first from-scratch install on a
  second machine (SETUP.md + doctor only). Separate `[paths] streams` there so the bot/worker/auto-upload
  never see archive items. Plan is in comments on #44/#45; desktop GPU/disk/network not checked yet.
- Issue #46 (user, same day): a second YouTube channel for non-lolcow clips/subjects the user likes,
  starting with Papa Meat. Lolcow Cinema stays lolcow-only (Jerry fits there). Plan: the desktop's
  own clipper.toml (own token, schedule, streams) = the second channel; gap is a second Discord bot
  app/token. Name, branding and permission are open.
- Issue #47 (user, same day): Ben Avery's "The Show" for the second channel; user says he doesn't mind
  clippers. Live free on Twitch `benaveryisgood`, Wednesdays 4-9 PM PT; no public VODs. Members
  archive on benavery.live (user has an account; cookies in secrets/, never a password). His YouTube
  was terminated for guidelines, so review carefully. User then chose: pull episodes from the
  benavery.live archive (backfill + weekly after Wednesday), not live Twitch recording (that stays
  a fallback in the issue). No chat, so the transcript scan carries it.
- Issue #48 (user, same day): the same archive/receipts method on Jer (The Quartering), the backbone
  of both shows (91 Jer segments in 13 streams). YouTube: ~11,570 long-form / ~2,730 h, captions
  free; Rumble (his main now) not yet counted. Idea: send "receipts packs" (old clips with source
  links) to Don and Kino for use on stream, for reach; the user makes contact, nothing auto-sent.
  Lolcow Cinema content. 2026-10-23: **On Cinema at the Cinema** is the second channel's third subject
  (user loves it). The full show is on HEI Network (paid membership, 16 seasons + 12 Oscar specials);
  YouTube has only promos plus Tim Heidecker's channel (~400 h with lives). Details on #46.
  2026-10-23: **Modal backend built (#11), not deployed yet.** `[transcribe] backend = "modal"`,
  `clipper/modal_app.py` (CUDA 12.8 image, torch 2.8 / whisperx 3.8.6 as uv.lock, large-v3 + en
  alignment baked in, L4 default, max 10 containers, `transcribe_url` for cloud-side archive pulls),
  `clipper transcribe --deploy`, doctor checks (package, token, app deployed), SETUP 2b, extra `modal`.
  Tested with the cloud call stubbed: FLAC upload, settings and cost fields right, words.jsonl identical
  (394/394) for kick-131151926's first segments. Waiting on the user's Modal account + MODAL_TOKEN_ID /
  MODAL_TOKEN_SECRET (user env vars). Then: deploy, transcribe a copy of kick-131151926 (not the NAS
  dir), compare with the local words/timings, write cost per stream on #11. Live config unchanged
  (backend still whisperx). User wants the bulk archive transcription done this way, uploads queued on
  YouTube as now.
  Update: token saved (`~/.modal.toml`, profile byvfx, active); app deployed 2026-10-23 (first build
  404 s): modal.com/apps/byvfx/main/deployed/clipper-whisperx. Measured on a copy of kick-131151926
  (7.85 h): L4 19 min ~$0.26, **L40S 10 min ~$0.34 (now the default)**, H100 8.4 min ~$0.59; 99.9% of
  words and timings match the laptop (table on #11). **Live since 2026-10-23 (user):** clipper.toml
  `[transcribe] backend = "modal"`, `modal_gpu = "L40S"`; `backend = "whisperx"` goes back to the
  laptop GPU. modal is installed with `uv pip` (bot was up); a later `uv sync` must include `--extra modal`.
- **`clipper archive` (built 2026-10-23):** `add <subject> <url>` (YouTube channels via yt-dlp, Odysee
  via its API), `transcribe <subject> [--limit --oldest --gpu --parallel --dry-run]` (8 at once on L4
  by default), `status`. Data in `paths.archive` (`archive/`, gitignored), one dir per item with
  meta.json/transcript.json/words.jsonl. Jer cataloged: 11,571 items / 2,732 h (~$89 on L4 for all).
  3-item test fine; the 100 newest: 20.7 h in ~11 min, 0 failures, ~61 GPU-min (~$0.85; the
  built-in estimate says $1.50, it assumes a cold container per item). **Full run started
  2026-10-23 03:25 PT (user: "run em all")**: 11,468 items / 2,711 h, expect ~23 h and ~$110-130,
  detached process (survives the session; Win32_Process create), log `logs/archive-jer.log`,
  progress `clipper archive status jer`. Stops itself after 20 failures in a row (YouTube bot
  check) and a rerun resumes. The laptop must stay awake and online for the downloads. Odysee CDN refuses direct ffmpeg pulls
  (401 then 429), so Jerry needs yt-dlp's extractor (#44). #49 (user): Modal monitoring in the war
  room, on the #14 roadmap. #50 (user): running P&L, a costs.jsonl ledger (Claude costs are only
  printed today) plus YouTube estimatedRevenue once monetized (needs the monetary analytics scope).
  More Modal ideas filed (user): #51 render cuts on Modal (cut is the slowest stage; source upload is
  the hard part, test fetching the Kick VOD from a container) and #52 speaker labels (words have
  speaker null today) first; #53 embeddings for receipts, #54 face recognition, #55 face-following
  Shorts crop, #56 old-footage cleanup. User is fine paying for a bigger GPU if it's worth it.
  #57 (user): narration in the user's own voice through ElevenLabs Voice Changer (user chose best
  quality/ease), first on a Jer video; `clipper narrate` = script, record, voice change, ducked mix,
  review. Needs the user's ElevenLabs account + ELEVENLABS_API_KEY. Original/licensed voice only.
  Same issue (user, 2026-10-23): an **AI avatar host** from pre-recorded webcam takes (LivePortrait
  on Modal preferred). Character: a salacious, campy gossip dame in the Joan Rivers / Harvey Fierstein
  *type*, but an original face, voice and name (not recognizable as either). **Name: Mother Moo**
  (user, 2026-10-23), the Lolcow Cinema host. Bit ideas on #57 (not decided).
- **Archive link checks (built 2026-10-24, user's idea):** `clipper archive check <subject>
  [--ids ...] [--all] [--limit N]` asks whether each catalog item's source link still plays
  (YouTube oEmbed: 200 alive, 401/403 restricted = private or embedding off, 400/404/410 dead;
  Odysee resolve: NOT_FOUND dead, BLOCKED etc. restricted). No downloads, no API quota, no cost.
  `<subject>/links.json` per item: status, reason, checked_at, since. A link that can't be checked
  (429, network) keeps its last result; 20 in a row stops the check (`stop_after_failures`).
  Rechecks items older than `[archive] link_recheck_days` (7); reruns print alive -> dead changes.
  `clipper archive links <subject> [--status]` lists them (default: not alive), `status` adds
  counts, `transcribe` skips dead links (they would only fail and count toward the stop streak).
  **Not run against the real services yet**: built in a cloud session whose network blocks YouTube
  and Odysee, tested with stubbed responses only. First real run: `clipper archive check jer
  --limit 50`, then compare a few dead/restricted ones in a browser. Not in the bot loop. Ideas
  not built: Kick VOD links in `streams/<id>/meta.json`, a war room view, "went dead" DMs.
  Same session, #63 (user brainstorm, ideas only): fun with the archive as a database (ask it
  questions, guess the year, prediction tracker, the Memory Hole, receipt radar, Lolcow Almanac).
- **#58 The Udder Truth** (user, 2026-10-23): weekly Brass Eye-style show (the 1997 Channel 4 one)
  hosted by Mother Moo; same weekly data as the magazine (its parody ads become TV spots), The
  Receipt Drawer is a segment (#48). Pipeline in the issue: Claude script → user's webcam take →
  ElevenLabs → character animation via the user's ComfyUI workflows run on Modal → user comps and
  finishes from a clipper-built timeline. User knows post (VFX) and some ComfyUI, little AI video.
  Satire rules: real people only say what they said, no pranks, fake experts are original.
  User decided: finish in **DaVinci Resolve** (clipper hands off FCPXML). Structure: cold-open montage
  → Inside Edition-style logo → Mother Moo at the desk throwing to reporter characters and back.
  First test: one 60-90 s throw (desk → reporter → desk), filmed vs. audio-driven reporters.
  User wants quality over automation/cost: a model bake-off on #58 (Act-Two, Wan-Animate-2 on Modal,
  Ray3 Modify/MiniMax H3, 3D face capture; HeyGen/Hedra/Kling Avatar for reporters; Sync for lip
  touch-ups). User may block the show out in 3D (tool and iPhone not yet known).
  2026-10-23: user knows **Houdini** (grey playblast synced to the take's audio for the throw; camera
  out as Alembic/FBX for Fusion; moves that suit a 2D character card). User is signing up for
  **Runway Max** ($95/mo, 9,500 credits: ~13 min Gen-4.5 or ~4 min Seedance Pro 1080p a month, so
  Runway for bake-off/design/plates/ads, weekly volume likely Wan-Animate-2 on Modal). **Runway MCP
  added** to Claude Code (local scope, `https://mcp.runwayml.com/mcp`, OAuth, spends plan credits);
  needs `claude mcp login runway` (or `/mcp`) at the laptop, then a new session to load its tools.
  Test-take script: `mockups/udder_truth/scripts/test_take_script.md` (phone take is fine).
  2026-10-23: Runway MCP signed in (Max, 29,500 credits incl. 20,000 bought). User's take:
  `2026-10-23 05-44-59.mov` (now in mockups/udder_truth/takes/recordings/, gitignored), 84 s 1080p60 SDR; transcribed on Modal.
  Mother Moo = Runway design option 4, **British** (accent: user performs it, or TTS; #57); hero
  image task fe671c52-2ab8-4da9-9ef8-f89d39c02acc, files and ids in mockups/udder_truth/mother_moo/.
  **Bake-off started** on a 10 s clip (4.5-14.5 s, 30 fps, `mockups/udder_truth/takes/`): Kling 3
  Motion Control 7342a353-f243-4859-9431-74f790c8cc3a (170 cr), Kling 4 v2v e115cf43-4da9-4e44-
  93de-3041bd57ff71 (212 cr), WAN 3 Prime v2v 1e77fcfd-46f9-434b-b897-f8c025fc8513 (280 cr), Aleph
  keyframe 5a1f162d-97a3-4bfd-8303-ba5dc5f48ad8 then the Aleph edit (~280 cr). Results: WAN 3
  Prime leads (wide desk framing the user likes, original audio kept, mouth follows speech); Aleph
  (2c852f6d-36f5-4b3d-ab6a-bc7ed0279ebc) tight framing, original audio, less mouth movement; Kling 4
  stiff, generated audio, 24 fps; Motion Control stuck at 98% at Runway. Table on #58; grids in
  mockups/udder_truth/takes/bakeoff/ (local). ~1,100 credits used so far (28,398 left).
  **User picked WAN 3 Prime.** Motion Control finished late (decent, regenerated audio, 9.87 s).
  WAN 3 Prime takes max 15 s of reference video, so longer takes run in 15 s pieces cut at pauses:
  30 s test = bc93ee7c-2eea-47eb-8b0b-a5c4edb8a6a0 (16.5-31.5 s) + 130fd02e-c588-4569-935c-f3525d3d76ff
  (31.5-46.5 s), 420 credits each; stitch and check identity across the cut. User prefers performing
  + voice change over AI-performed (AI = a new dice roll per take). Runway has no speech-to-speech,
  so the voice changer is ElevenLabs direct (user's account/key) or Seed-VC-class on Modal. Voice
  auditions (Runway TTS presets Marlene/Miriam/Paula, British) in mockups/udder_truth/voice/ (local).
  30 s test done (wan3_30s.mp4 + side-by-side): identity holds across the two 15 s pieces, but her
  head position jumps at the splice (each piece restarts from the hero image), so plan a cutaway or
  angle change at every splice, or overlap renders and dissolve. ~1,960 credits used in all (27,843 left).
- **`clipper narrate` (built 2026-10-23, untested until the key exists):** `voice <take>` → ElevenLabs
  Voice Changer (POST /v1/voice-changer/{voice_id}, eleven_multilingual_sts_v2, background noise off)
  → `<take>.voiced.mp4` with the picture copied; `voices [--accent --gender --age --search]`, `--add
  OWNER:VOICE_ID`. `[narrate]` config, doctor (warn if no key; online: key, plan, voice), SETUP 2c.
  User chose ElevenLabs and is making the account + `ELEVENLABS_API_KEY`. Next: pick her voice, run
  the 30 s take, mux under wan3_30s.mp4.
  Update 2026-10-23: key in `secrets/elevenlabs_api_key.txt` (Creator plan; the env var still wins).
  **Mother Moo's voice = "Harriet - British Actress"** (library voice, `voice_id aAsWcN5jdLdiYG7Hq0YL`,
  set in clipper.toml), picked from 5 finalists after the user's take ran through each (reel in
  mockups/udder_truth/voice/, local; the other four "MM test - …" voices are still in the account).
  **User prefers the AI read over their own British accent** → workflow "perform to playback":
  `clipper narrate speak script.txt` (eleven_v3 TTS with [cue] tags) → the user lip-syncs to it on
  camera → WAN 3 Prime → the TTS audio is the final audio. Test read: test_take_read.txt → 62.8 s.
  User notes: "more punchy and British, stern, not over the top… leave a little camp". Picked read
  **C** (test_take_read_v3.txt): defaults now in clipper.toml `[narrate] tts_settings = {stability
  0.5, style 0, speed 1.05}`. Delivery/writing guide: mockups/udder_truth/mother_moo_voice.md (use it
  for any Mother Moo script, including Claude drafts for #58).
  **First real clip (2026-10-23):** user's lip-sync take `2026-10-23 07-08-23.mov` (takes/recordings/, 3 takes;
  synced to read C by audio correlation and by matching transcript words: takes at 9.61/74.21/145.96 s).
  Take 1, cut at read pauses 0/14.95/28.6/42.0/51.5 s into `mockups/udder_truth/takes/first_clip/`,
  WAN 3 Prime tasks 0df59de0…, 0c9eef30…, 3fe40128…, a8c447c9… (~1,400 credits; 26,443 left).
  WAN rounds outputs down to whole seconds, so stitching places each piece at its read time and
  holds the last frame over the gap (scratchpad stitch_moo.sh; checked within 1 frame). This hand
  workflow (sync, split at pauses, render, stitch) should become a `clipper` command (#58).
  **Done: `clipper perform` (2026-10-23).** `prepare <recording> --read <read> [--take N]` finds takes
  by FFT cross-correlation with the read (needs the read audible in the recording), cuts take N
  inside the read's pauses (ffmpeg silencedetect; greedy, ≤ `[perform] max_piece_s` 15) into
  `<recording>.perform/pieceN.mp4` + manifest.json; `status`; `stitch` places renderN.mp4 at the read
  times with last-frame holds, read as audio → final.mp4 + final_phone.mp4 (720p). Tested: takes
  found at the same offsets as by hand; stitch of the real renders is pixel-identical to the hand
  stitch. **Render is not a clipper step**: Runway plan credits are only reachable through the Runway
  MCP (Claude) or the web app; its developer API is a separate key and credit pool.
  Brass Eye opening reference from the user (cold open/titles): youtu.be/yXv73GGSEag (28 s, 1997).
  **Lip-sync pass (2026-10-23):** the user's own lip sync was off except in spots, so
  `clipper perform lipsync <dir> --engine latentsync|sync|both` redraws the mouth to the read:
  LatentSync 1.6 as Modal app `clipper-lipsync` (~9 min / ~$0.30 for 50 s on L40S) and sync.so
  lipsync-2 (key in secrets/sync_api_key.txt, ~5 min). Both ran on the first clip; 3-way
  comparison sent. **User picked Sync** (2026-10-23): `[perform] lipsync_engine = "sync"` in
  clipper.toml (the config.py default stays latentsync, which needs no account). User will also
  record in shorter takes broken at the pauses.
  **Editor (user, 2026-10-23): DaVinci Resolve Studio** (buying the license; not installed here yet).
  #60: `clipper udder timeline <week>`: FCPXML first, then Studio's external Python scripting to
  build the project, bins, timeline, Fusion titles and render queue.
  Cold-open reference is now the **Brass Eye 2001 Special intro** (youtu.be/Fh7UgrHMjbQ): narrated,
  story-led, ends on the presenter beside a giant video wall pun (#59, cold_open_breakdown.md).
  **Full-series reference** (user, 2026-10-23): youtube.com/watch?v=pQCrcAFuI-E, all 6 episodes + the
  2001 special (2 h 49 m, unofficial upload; study only). Studied from its auto-captions (no video
  download): episode format breakdown in `mockups/udder_truth/brass_eye_formats.md` (#58).
  `mockups/udder_truth/` tidied (2026-10-23, README there): scripts/, storyboards/ (first: throw_v1,
  the user's 8-panel desk → reporter → desk board + Runway animatics of panels 2-8; the full-size
  board and animatics live on the NAS under \\192.168.0.155\docker\clipper\udder_truth\, only
  board.jpg + README in git), takes/recordings/ (the two .mov takes, moved from the repo root;
  perform manifests updated). Script `scripts/throw_v1.md` (+ `_moo.txt` / `_rupert.txt` reads):
  user wants it **biting satire**. **Build version = v1b** (user, 2026-10-23): after the satellite
  delay Rupert starts and Mother Moo cuts him off, then says his punchline herself. Build audio
  `throw_v1b_mix.mp3` (90.9 s; spliced, recipe in throw_v1.md) on the NAS under
  udder_truth\audio\throw_v1\. Rupert's voice is a temporary stand-in (ElevenLabs "Daniel").
  Next: the user performs Mother Moo's shots to the mix (`clipper perform`). Panel 4 could still be sharper. Jer archive restarted
  detached (CREATE_NEW_PROCESS_GROUP) after an app-restart Ctrl+C killed the first run at 1,283.
  Earlier: the user asked about cloud transcription; it's #11 (Modal, already chosen 2026-10-20).
  Starter plan has $30/month free; L4 $0.80/h; estimate ~$0.03-0.05 per audio hour, so live shows
  fit the free credits and archives cost ~$40-65 per 1,000 h, downloading in the cloud too. It also
  shrinks the desktop question to storage. #44's WhisperX estimate corrected to ~40 GPU h locally.
  User decided (2026-10-22): **Jer goes first** of the four archive projects,
  but it waits on hardware logistics the user is still working out (desktop / storage). Don't start
  the pull until they say so.

## Roadmap: YouTube Posts as a gossip magazine (user's idea 2026-10-21, issues only, nothing built)
2026-10-22: Posts take at most 5 images (Google Help; recommends 1:1, feed shows square, tap for
full). Issue 01 (`mockups/weekly/tiktok/`, 10 pages, 3:4) goes out as two posts of five; text in
`mockups/weekly/youtube_post_test.md`, images sent to the user's phone. The user is testing it by hand
(the API can't post). Waiting on: private/draft option, feed crop of 3:4, poll in part 2 (#31, #35).
Re-checked 2026-10-23: the Data API still has no Posts endpoint, so the user posts by hand from
`mockups/weekly/youtube_post/01-10.jpg` (upload order; not committed). Plan (user): schedule the test,
delete it if it works, then **Saturday 2026-10-25 is the first real issue**. Before then: page 05
says "PART 2 IN THE NEXT POST" instead of SWIPE, and the aspect follows the test.
Tracking issue #30. The YouTube Data API can't create Posts, so clipper drafts copy and images, the bot
posts a Discord card, and the user posts in Studio or the app. #31 check the channel can post and the
limits (user's, first), #32 `clipper posts` stage + square covers + Discord drafts, then #33 cover stories
and #34 reader polls (suggested first), #35 weekly issue carousel, #36 blind items and reveals, #37
"Who said it?" quiz, #38 STOP THE PRESSES live/pre-show alerts.
- Weekly issue look decided 2026-10-22 (user, #35): **Lolcow Cinema Weekly**, one issue for the whole
  channel (all shows), all eight slides (cover, contents, cover story, news in brief, quote, numbers,
  marketplace of parody ads, next week + poll), a modern editorial style (Anton / Instrument Serif /
  Inter, paper and ink, red labels, yellow highlighter, rounded cards), and a reader poll every week.
  Mockups and the design spec: `mockups/weekly/` (README). Rejected along the way: the dense Enquirer
  look and the newsprint page (parts looked out of place with each other). Faces must be cropped on
  a detected face, whole head kept. Open: whether a Post can carry both images and a poll (#31); if
  not, the poll is its own post beside the issue. Still nothing built.
- Later 2026-10-22 (user, after merging the mockups into main): the weekly fits TikTok better than
  YouTube (photo carousel to drafts; needs 3:4 or 9:16 slides, images on a verified URL, no native
  poll). The **cover can go full tabloid** ("we can get crazy"; gen AI allowed but probably not
  needed); inside pages stay modern. Barcode block on the magazine cover is fine. YouTube
  thumbnails stay as they are until the magazine is settled. Cover research (1970-2025 Enquirer
  covers, the 2020s template anatomy, what to borrow): research_notes/enquirer-covers.md. User is
  still collecting references. Full issue mockup, cover + 7 inside pages at 3:4 from real Oct
  18-24 data: mockups/weekly/tiktok/ (contact_sheet.jpg; notes in mockups/weekly/README.md).
  Cover approved after two rounds of markup; user then said "this is great" and asked for the
  inside pages; later rounds: funnier numbers page (screen time per regular, Lolcow of the
  Week, joke stats from transcripts/chat) and two real articles, so the issue is now 10 pages.
  User (2026-10-22): "a good test"; posting this issue to TikTok by hand. Next: pick the weekly's
  day (suggested: week = Sun-Sat, build Saturday after Friday's Kino is processed, post Sunday
  late morning PT before Channel 404's 5 PM show), then build it as a `clipper weekly` stage. Person cut-out via torchvision DeepLabV3 (weights cached
  in ~/.cache/torch on this laptop). Render scripts were scratch files, not in the repo; a real
  `clipper weekly` stage is not built.
- #41 (user, 2026-10-22): a cast library, 3-4 photos per person (`photos/<Person>/<label>.jpg`),
  `clipper cast grab` (best face frames from a stream) and `clipper cast add`. Cast to start (user;
  adds more as they come up): Andy, PPP, Jer, Blade, Chef, Geezer, Mel, Jim Bakker, Mr. Morrow,
  Victor, Jasko, Bores, DSP, Don Flurgundy, Stacey Kinneson. Cloud sessions can't reach image hosts; a web image pass needs allowed domains or the
  Windows machine.

## Automatic uploads (2026-10-20, user)
- clipper.toml [youtube]: manual_publish = false (uploads private with publishAt = slot; public at once if
  the slot is under min_lead_minutes away), auto_upload_minutes = 60 (bot: `clipper schedule` then
  `clipper upload` hourly), max_per_24h = 15 (new: counts uploads in the last 24 h across runs; default 6).
  Bot restarted 19:26; first run at 19:28 uploaded nothing (15 in the last 24 h). Uploads resume as
  last night's batch ages out (~22:20). All 16 earlier uploads were already public (published in Studio).

## Vertical Short covers (issue #1, 2026-10-20)
- `thumbs.compose_vertical`: 1080x1920 tabloid cover per Short from its clip's thumbnails.json choice
  (masthead, green band, headline split by `fill_lines` to fill the width, face to the bottom, inset and
  badge kept ~380 px above the bottom for YouTube's mobile title overlay; no side teasers; up to 2.5x
  zoom). Drawn by `clipper thumbs` (with the clip covers), by `clipper shorts` after rendering, and by
  `clipper thumbs <stream> --shorts` (just those, attaches just those in Kitsu). Upload/Kitsu pick up
  `shorts/<name>.jpg` like any cover.
- Done and attached in Kitsu for every stream with Shorts (kick-131151926, -132548228, -133095550;
  16 covers). Kino streams 10/10-10/17 have no Shorts. Committed adf35ae, issue #1 closed.
- Not yet known: whether `thumbnails.set` works on Shorts through the API (Studio allows it). User
  (2026-10-20): don't send Short covers to YouTube until that is verified, so `[youtube] short_thumbnails`
  (default false) keeps uploads, `--attach` and `--thumbnails` from setting them. To verify: set one by
  hand on a test Short, check it in Studio and on the Shorts shelf, then turn the key on.

## Hot-take Shorts, no clips (issue #40, live 2026-10-21 ~17:15)
- User wants many more Shorts, mixed lengths, built on hot takes. Deep research (reports/YouTube Shorts
  growth strategy.md, notes in research_notes/; also the first comment on #40): length and linking matter
  more than volume; Shorts over 60 s with any Content ID claim are blocked worldwide.
- Trial on kick-131151926, then the user approved it: clipper.toml `[shorts] window = "short"`, min_s 15,
  max_s 59, end_pause_s 0.7 (new: `shorts.end_on_sentence` moves the end to a sentence end or a pause,
  forward within max_s, else back). Starts are still scoring's short_in_s (some open mid-phrase).
- **Clips dropped** (user: too short): `"picks"` out of `[review] include`, `[schedule] clip_days = []`
  (clip_times kept: a pinned clip still reads it), 12:00/18:00 moved to segment_times (still 15 a day).
  Picks are still cut, drafted and covered: Shorts reuse their titles and covers. Approved clips stay
  approved in Kitsu but never get a slot; the 4 uploaded stay up.
- Re-cut: the 43 queued Shorts (all approved, not uploaded; rejected ones and window_override retakes
  left alone) re-rendered at 15-59 s and published as new Kitsu revisions waiting for approval,
  `review publish --only shorts --no-notify` (new flag: only those kinds, so re-cut segments weren't
  republished). Log logs/recut_shorts_2026-10-21.log. They're in the war room's review queue; the user
  started approving at 17:04. Old cuts of the test stream in its `shorts_before_window_short_2026-10-21/`.
- `clipper shorts` now skips Shorts already uploaded (schedule.json) unless --force: the trial had
  re-rendered 3 uploaded test-stream Shorts; restored from that backup.
- `clipper schedule --reshuffle` run (backup schedule.before_noclips_2026-10-21.json): no clips queued,
  Shorts only as they get re-approved. Bot restarted 16:51 so its in-process replans use the new slots.
- Several older approved Short titles still say "hosts" (old drafts), e.g. "Two hosts fight over...".

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
- 10/21 pre-show (yt-6g2iMNJuyPc) approved 14:21; at the user's request `clipper schedule` then
  `clipper upload --max 1` at 14:23: **public https://youtu.be/Hyk2gKNvKk8** with cover, captions, playlist,
  Kitsu link (log logs/upload_preshow_2026-10-21.log). Watch it for the same Content ID block.
- **Uploads are private-only for now** (user, 2026-10-19): `[youtube] manual_publish = true` in
  clipper.toml; `clipper upload` sets no publishAt and never public. The user publishes in Studio
  (suspects API-set visibility misbehaves until the API audit). Pre-shows too.
- 10/23 pre-show (yt-NM9zvxSN1nY): user rejected the first cover ("say PPP, not Andy, picture PPP");
  retake applied by hand (a reject doesn't trigger auto-retake; Changes/retake does), "Huck" -> "Hawk"
  Blend fixed in the draft + Kitsu description. Approved, uploaded at the user's request
  (`clipper upload --max 1`, log logs/upload_preshow_2026-10-23.log):
  **public https://youtu.be/xfTIUuCGYdY**.
- **End trim (user, 2026-10-23):** `[preshow] trim_end_s` = 3 s off the end of each pre-show (they all
  end on the ~11-15 s "tune in tonight 8:30 Eastern on Kick" plug; user chose a fixed 3 s over cutting
  the plug). Applies to pre-shows grabbed from now on (segments stage); the three already up are
  untouched. No restart needed: the bot and worker run each stage as a fresh `clipper` process on the
  editable install.
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

## Shorts front-loaded (2026-10-20 ~22:00)
- User: Shorts get more views, so 3 a day. clipper.toml `[schedule]`: short_times 10:00/15:00/20:00,
  those two segment slots removed (still 15 a day). Backups: clipper.before_shorts3_2026-10-20.toml,
  schedule.before_shorts3_2026-10-20.json. `clipper schedule --reshuffle` run: the 14 queued Shorts now
  go 10/21 15:00 through 10/25 20:00.
- **Bot NOT restarted** (user was recording). Its hourly auto-upload runs `clipper schedule` as a fresh
  process, so that uses the new slots; only its in-process replans after a Discord/war-room decision
  still use the old slots. Restart it between shows: `deploy\discord\bot-task.ps1 -Restart`.
- Backlog Shorts: kick-131498615, -132040888, -132380636, -132733064 (Kino 10/10-10/17) had none.
  `clipper shorts` + a Shorts-only publish (log logs/backlog_shorts_2026-10-20.log, script in that
  session's scratchpad: config copy with review.include = ["shorts"] and `_resolved` cleared, since
  for_stream caches show configs there). A plain `review publish` on those streams would also re-render
  and republish their re-cut segments as new revisions (To do 0, user said not yet).
- Suggested to the user: set each Short's "related video" in Studio once its segment is public (the API
  can't).

## kick-132733064: transcript clock 46.91 s behind the video (found 2026-10-21)
- User rejected v06_t06 and v07_t34 "captions dont match". Cause: the old recorder dropped 31 HLS
  segments (63.8 s) in the first ~43 min; source.mkv keeps timestamp jumps there, but
  `transcribe.extract_audio` wrote audio.wav without timestamps, so the samples closed up over the
  gaps. audio.wav is 28394.4 s, source 28441.3 s; from ~0.7 h on, source time = transcript time
  + 46.91 s (measured by audio cross-correlation every 40 min). Every cut from this stream (segments,
  clips, compilations, Shorts, thumbnails) after that point starts ~47 s early and its captions are
  47 s off. Only this stream: kick-133095550's one gap is between two parts (joined end to end; its
  wav and source match), and 133260144/133423456 have no gaps.
- Fixed for new recordings: extract_audio uses `aresample=async=1:first_pts=0` (fills gaps with
  silence). Tested on this source: 28441.31 s and aligned at every probe.
- On YouTube from this stream: clip c01_t27 "Phil (DSP) says he won Evo" https://youtu.be/XztAeLsQvZ8
  (captions off). 29 more items scheduled from 10/28 (uploads 48 h ahead, so nothing before 10/26).
  The hosts-only re-render of v04_t35 and the scene re-render of v03_w06 also need the fix first.
- FIXED 2026-10-21 (user chose "shift and re-cut"): measured the old->source clock by correlating
  the old audio.wav against a gap-filled extraction (27 steps of 1-4 s between 453 s and 1993 s old
  time, +46.916 s after; one false match at ~2470 s removed). Shifted words.jsonl, transcript.json,
  candidates pass_a in/out/short, segments.json, scan/summary, retakes trims, shorts.json windows;
  audio.wav replaced with the gap-filled one. Left alone: chat.jsonl, windows.json, pass_a window[]
  (chat heat, already source clock). Backups + clock_map.json in streams/kick-132733064/clockfix_backup/.
  Re-cut all segments, compilations, clips; captions rewritten; all 7 Shorts re-rendered (v04_t35
  keeps watch_hosts). Checked: Shorts vs audio.wav peak 0.89-0.96 at 0.00 s (was 0.02), s14 exact.
  `clipper review publish kick-132733064` (with cards) republishes everything for re-review; log
  logs/clockfix_publish_kick-132733064.log.
- Uploaded clip c01_t27 (https://youtu.be/XztAeLsQvZ8): `cut --ids` filters segments only, so it was
  re-cut too; restored to the old cut (old candidates times, sidecar has a note) and dated before its
  review preview so publish skips it; its .srt now matches the YouTube video. ALSO its bleeps were on
  the old clock: on YouTube "fucking" (1:21) and "fuck" (1:24) are audible and bleeps sit on normal
  speech at 0:34, 0:37, 1:37. User chose to replace it (2026-10-21): corrected cut (in_s 18015.741,
  3 bleeps on the right words, sync -0.02 s) uploaded first as https://youtu.be/uKrHtUrU6Uk (pinned
  in Kitsu "Publish at" 2026-10-21 08:30, private until then; one upload over max_per_24h via
  CLIPPER_YOUTUBE_MAX_PER_24H=16 for that run; log logs/upload_t27_replace_2026-10-21.log), then
  XztAeLsQvZ8 deleted via the API once the new one was confirmed. schedule.json entry has a
  "replaces" note; backup schedule.before_t27_replace_2026-10-21.json. The running publish will
  give c01 a new Kitsu revision (pending) with the corrected preview: re-approve it in Kitsu; it
  stays uploaded either way.
- Publish done 2026-10-21: 35 items republished (18 segments, 7 clips, 7 Shorts, 3 compilations),
  34 Discord cards, no errors. All pending re-review. This also covers To do 0 for kick-132733064
  (its Kitsu previews no longer have the silent head).

## Shorts switch layout at scene cuts (built 2026-10-20 late)
- User rejected kick-131498615 v01_w14: "the first 38 seconds is a weird format, after that the format
  looks great" — the stream was on its cams scene for 39 s, then switched to watch; the old picker voted
  one layout from 3 frames. Rule (user chose it): a Short switches layout at each scene cut.
- shorts.py: `sample_frames` (one 768 px frame every `[shorts] scene_step_s` = 4 s from the source),
  Haiku labels them `scene_batch` = 8 per call with one required key per frame (`label_frames`, ~$0.002
  a Short), anchored by a reference screenshot per layout in `layouts/<show>/<layout>.jpg` ([paths]
  layouts, gitignored; made with `clipper shorts <stream> --save-example <layout> --at <s>`; saved for
  kino cams/watch from kick-131498615 at 7139/7182 s and c404 desk/watch from kick-132548228 at
  1947/15975 s). With references for every real layout, "center" is not offered. `scene_runs` folds
  interior runs under `min_scene_s` (6 s); runs at the start/end stay (a cut 4 s in is real).
  `find_cut` puts each change on the first frame that looks more like the new scene than the old
  (64x36 grey diff at 20 fps; Kino's switch is a ~0.5 s crossfade, ffmpeg scene scores stayed < 0.05).
  Render trims the cut per scene and concats; captions take each scene's caption_y and never cross a
  cut. State: shorts.json "scenes" [[start, end, name]], "layout" = label like "cams 0:00 > watch 0:39"
  (also the Kitsu Layout column). One-scene Shorts keep their old signature (no re-render).
- What failed on the way (don't retry): all frames in one call (40% wrong count, labels drifted run to
  run); no references (zoomed/busy screen box read as "center"); whole-frame pixel distance to the
  references (bright page or a webcam filling the screen box matched the other layout); Sonnet (20x
  the cost, still one spurious center). Haiku + references + no center: 10/10 known cases, same
  answer on repeat runs. `clipper doctor` warns when a show with 2+ layouts lacks references.
- Layout set by hand (--layout, a Kitsu Layout edit, a retake) still means one layout for the whole
  Short. A Kitsu Layout value now counts as an edit only when sync lists it in "edited".
- New flags: `clipper shorts <stream> --check` (print scenes, render nothing) and `--rescene` (re-label
  model-chosen Shorts, re-render the ones that change). `[shorts] layout_frames` is gone.
- Fixed in review.publish: re-publishing an item dropped its "fields" (what clipper last proposed), so
  columns never updated on a new revision and the next sync flagged them as reviewer edits.
- v01_w14 re-rendered (cams 0:00-0:39.4, watch after), published rev 2 (pending) with a new Discord card.
  Its YouTube description (clip draft from the 10/10 meta run) still says "hosts" twice.
- `--check` over all 8 streams with Shorts: 9 would change. Re-rendered + published (pending, new cards):
  kick-131498615 v01_w14, kick-132040888 v03_w10 (was rejected "weird cut around 5 s in"; now 6 scenes).
  Waiting on the user: APPROVED + scheduled, re-render would reset approval and slot:
  kick-131151926 v06_w11 (cams -> cams>watch 1:20, slot 10/23 10:00), kick-132733064 v03_w06 (cams ->
  4 scenes, 10/28 20:00), kick-133095550 v03_t23 (desk -> watch, 10/25 10:00). REJECTED for other reasons:
  kick-132380636 v04_t49 + v06_t32 and kick-132733064 v04_t35 want "just PPP and Andy" (no hosts-only
  crop of Kino's watch scene exists; would be a new layout), kick-132733064 v06_t06 "captions dont
  match" (not investigated; re-encode cuts seek accurately, so not the .mkv copy-cut offset).

## Kino hosts-only Short layout (2026-10-20 late)
- New layout `[shows.kino.shorts.layouts.watch_hosts]` in clipper.toml: Kino's watch scene without the
  video, the two right-hand webcams (same src boxes as "watch") stacked 960 + 960, cropped to 1080x960 with
  focus x 0.9 (top cam: the person sits right of centre) and 0.55 (bottom), caption_y 0.49 (on the seam,
  over mics/chests, clear of faces). Checked on 7 frames from 4 streams.
- `pick = false` (new layout key, `shorts.pickable()`): scene detection never offers it, since it is the
  watch scene shown another way, and `clipper doctor` wants no reference screenshot for it. Choose it by
  hand: `clipper shorts <stream> --ids vNN --layout watch_hosts`, the Kitsu Layout column, or a retake
  (the planner still lists every layout). Documented in clipper.example.toml [shorts] and SETUP.md.
- Re-rendered with it and published Shorts-only (rev 2, pending, new Discord cards): kick-132380636
  v04_t49 + v06_t32, kick-132733064 v04_t35 (all three rejected asking for just PPP and Andy).
  Their titles still say "host"/"hosts" (v04_t49 "Host explains why Phil…", v06_t32 "…and the hosts"):
  not touched here.

## Clips first: worker fast lane (built 2026-10-20 ~23:30)
- User: speed is key for clips. The worker already started only after the recorder marked a stream
  done (5 min end_grace_s + ~6 min finalize + up to 5 min poll), but clips and Shorts came out last:
  on kick-133260144 (7.5 h Kino) cut took 2 h 27 min (all long segments; the 4 picks ~2.5 min) and the
  review stage 2 h+ (segment/compilation previews), so Shorts reached review ~6 h after the show.
- `[worker] fast_lane = true` (default): stages are now signals, transcribe, score, segments, then the
  fast lane `clips` (`cut picks`), `clips_meta`, `clips_thumbs`, `shorts`, `clips_review`, then the slow
  lane `cut` (`cut segments compilations`), `meta`, `thumbs`, `review`. Both meta/thumbs passes use the
  new `--missing` flag (only videos with no draft/cover; the model is told the playlists already used),
  so clips under review keep their drafts and covers. review publish was already incremental (cached
  previews, skips unchanged mtime, notifies only newly published keys). `[worker] stages` keeps the
  old names; the clips_* twins are added for scored streams (not pre-shows).
- `clipper worker`: every waiting stream's fast lane first, then slow lanes oldest first; a slow lane
  checks between stages and pauses (run.json "partial", "paused") when another stream's clips wait.
  Granularity is a stage: a 2.5 h segment cut isn't interrupted. Alerts: "<id> clips and Shorts are in
  review (N min after the stream ended); segments next", then "<id> is in review". run.json gets
  "clips_ready". Expected: Shorts in review ~75-90 min after an 8 h show (transcribe ~48 min).
- DONE_MARKERS are per stage, globs allowed (`worker.output_found`, also used by the war room bars).
  Streams finished by the old worker show the new bars as done from their files.
- Tested: simulated worker with fake stages (two streams interleaving, legacy stream marked done),
  `meta --missing` on a copy of kick-131151926 with the model mocked, `meta/thumbs --missing` on the
  real test stream (no-op). Not yet run on a real recording.
- Worker restarted with this code 2026-10-21 07:42 (one supervisor/worker chain, idle). By then the old
  worker had already finished both overnight streams the old way: kick-133423456 (404, ended 22:36 PT)
  started 00:07 PT after the Kino review, cut 75 min, review 72 min, in review 03:26 PT. The next
  recording is the fast lane's first real run: check its run.json "clips_ready" vs meta.json ended_at.

## Hashtags: meta prompt v2 (2026-10-22)
- User asked whether more hashtags would help. No: YouTube shows only the first 3 above the title,
  ignores all of them past 60, and treats over-tagging as spam; they are a small discovery signal.
  User wants better picks going forward, no rerun of `meta` on existing streams.
- prompts/meta_v2.md (chapters_v2.md is a copy of v1) and `[meta] prompt_version = "v2"` in config.py,
  clipper.toml, clipper.example.toml. Still exactly 3, now ordered: main person as fans search it,
  a second person or a broad tag the audience browses (#StreamerDrama, #Lolcow, #Kick), then the show
  (#KinoCasino). Bans generic words (#Groceries) and two tags for one person (#DSP + #DarksydePhil).
  Shorts prepend #Shorts, so only the first two of ours show there; that's why the show tag is last.
- Takes effect at the next stream's `meta` stage (the worker). Committed 53c1c96.
- Monthly analytics check-in: issue #42. Analytics scope granted 2026-10-22 (`clipper analytics
  --sign-in`, channel LOLCOW CINEMA; old token in secrets/youtube_token.before_analytics_2026-10-22.json).
  Bug fixed: youtube.credentials loaded the token with only the upload scopes and rewrote it, dropping
  yt-analytics.readonly from the file, so analytics never saw it. It now keeps extra saved scopes. Bot
  restarted 19:26 on the fix. 7-day numbers start ~2026-10-26. The `analytics --show` /
  `schedule --show` crash is fixed (2026-10-23, #61): use `--list`.
- Show playlists (2026-10-22, user): every upload also goes in its show's playlist, `[youtube]
  show_playlist = "{show}"` ("Kino Casino"), next to the model's subject playlist. Uploaded entries
  record `"playlists": [...]` in schedule.json. Backfill: `clipper upload --playlists [--max N]
  [--dry-run]` (50 quota units per add; skips videos already in the playlist). All 28 uploaded Kino
  videos are in "Kino Casino", all 17 Channel 404 videos in the new public "Channel 404 News" (user OK'd).
  Bot restarted 21:06 on this code, so new uploads join their show playlist.
- Wish list: lore-order playlists (storylines in the order they happened), issue #43.

## Bug sweep (2026-10-23, #61 #8 #4)
- #61: `clipper schedule --list` / `clipper analytics --list` (own `dest`; the old `--show`
  spelling still works, hidden). `clipper --show kino schedule` no longer loses the show.
- #8: `youtube.metadata()` masks banned words (slurs, and swearing while `[bleep] profanity`)
  in title, description and tags, so YouTube, Rumble and TikTok all get it; `clipper upload
  --dry-run` says when it masked something so Kitsu can be fixed. First catch, of 203 scheduled:
  kick-132040888/v05_t41 (Short, Nov 1), whose Kitsu description also says "The hosts" (not fixed).
- #4: a playlist deleted in Studio: the add 404s, the cache entry is dropped, the name is looked
  up again (or created) and the add retried once. `--playlists` backfill treats it as empty.
- Uploads run as `clipper upload` subprocesses, so no bot restart was needed.

## War room: live chips and keyboard (2026-10-23, #15 #16)
- #15: a live chip opens a menu: watch here (a dock bottom-right with Kick's embed,
  `player.kick.com/<slug>`, no frame blocking; ⤢ bigger, ↗ to kick.com) or a new window.
  "Always do this" remembers it per browser (localStorage); shift-click asks again. The chip
  shows how long it has been live and ● REC / ○ no rec (a stream of that channel whose
  `live/state.json` is recording/paused/finalizing). Offline chips open the channel.
- #16: J/K (also swaps the item in the open window), Enter, Esc, A (in the window: approve and
  go to the next), R (asks why), C, H, P, U reopen, Space, arrows ±5 s, F picture in picture,
  1-6 tabs, / search, L watch live, ? list. Off while typing. Tested in the bot's live war room
  with the page's `act` stubbed, so no Kitsu decisions were made.
- The page is read per request, so the bot needed no restart.
- Not done: #26 pop-out / open the recording at a clip.

## War room on the phone (2026-10-23, #25)
- Under 700 px wide: header scrolls away, bottom tabs Review / Schedule / Pipeline (remembered
  per browser; Schedule opens at today), one-column cards with 46 px buttons, the item window
  full screen with a fixed bar at the thumb (‹ Approve ✎ ✕ Hold ›; Approve then goes to the
  next item, on desktop too), swipe left/right for next/previous (not on the video's scrub bar).
- Low-res: phones tap to play a 360p copy, `lowres/<stem>.mp4` beside the preview (libx264
  crf 30, max 700k, low priority, one at a time). Made on first play (`/api/lowres`, jumps the
  queue; the page polls and offers full quality meanwhile) and ahead for pending/changes items
  every 5 min (`[warroom] lowres_prewarm`, `lowres_height` 360). 14.5 min segment: 70 MB ->
  19 MB in 21 s. Nothing scans the cut folders recursively, so the copies can't become items.
- Home screen: /manifest.webmanifest + icons (served without the token); the token cookie now
  lasts a year (was a session cookie), so the installed app stays signed in.
- Python changed (warroom.py, config.py): the bot serves the war room in-process, so it needs a
  restart before /api/lowres, the manifest and the cookie work there. The HTML alone was live.
- Tested on a second instance (`.claude/launch.json` "warroom-test", port 8766, prewarm off) at
  375x812 with the page's actions stubbed. First copies made: kick-133597196 review/lowres/v01_t40,
  kick-133761201 s11_the-quartering.
- Phone access itself is #10 (Tailscale): the user agreed it's needed, next up.

## Phone access through Tailscale (2026-10-23, #10, code done, user steps open)
- Not the NAS plan in the issue: `tailscale serve` on whichever machine runs the bot (the
  laptop for now) proxies https://<name>.<tailnet>.ts.net/ to the war room on 127.0.0.1:8765.
  Tailnet devices only; HTTPS also lets the phone install it as an app. Same on the NAS later (#9).
- `clipper warroom --tailnet on|off|status`. `on` makes `secrets/warroom_token.txt` (12 chars)
  if `[warroom] token` is empty, runs `tailscale serve --bg --https=443 http://127.0.0.1:<port>`,
  prints the phone link with ?key= once (terminal only). `[warroom] tailnet = true` makes doctor
  (--role bot/all) check install, sign-in, serve, token, and fetch the manifest through the name.
- The bot used to log the war room URL with ?key=; it now logs it without. Cookie SameSite is Lax.
- Live 2026-10-23 17:44: laptop is `thor.taile415cd.ts.net` (Tailscale 1.104.2), war room at
  https://thor.taile415cd.ts.net/, token in secrets/warroom_token.txt, `[warroom] tailnet = true`
  in clipper.toml, bot restarted. Checked: no key -> 401 (page and API), manifest 200, ?key= sets
  the year cookie, API and low-res work with the cookie, doctor "on the tailnet ... (answers)".
- First `serve` hung: the tailnet needed Serve enabled (login.tailscale.com/f/serve link) and the
  command hid that link behind captured output. Now `on` streams tailscale's output and waits.
- Localhost asks for the key too now: open http://127.0.0.1:8765/?key=<token> once on the laptop
  (`clipper warroom --tailnet status` prints the phone link; same key).
- User opened it on the phone and added it to the home screen; #10 closed 2026-10-23.

## Notifications as Discord DMs (2026-10-23, #22, user chose DMs over a channel or web push)
- clipper/alerts.py: any process `alerts.send(cfg, kind, text, key)` -> one JSON file in
  streams/.notify/outbox/ (on the share, so the NAS recorder can write too); the bot's
  notify_loop picks them up every minute and DMs [notify] user_id (default the first allowed
  user). Kinds ready / upload / failure / recorder / disk / digest; same key once per
  cooldown_hours (6; default key = text with digits ignored); quiet_hours 00:00-08:00 hold them
  and they arrive as one DM with [time] stamps; long batches split under 2000 chars. State
  (cooldowns, digest day, disk check) in streams/.notify/state.json.
- Sources: worker (ready when a stream reaches review, clips-first ready, failed with no retries
  left, crash), recorder Alerts warnings (stalls, crashes, finalize failures; "is live" isn't
  sent), the bot's background jobs exiting non-zero (upload/tiktok = upload kind, preshow /
  retake / analytics = failure), disk under disk_free_gb (hourly), digest at 09:00 (out today,
  tomorrow, waiting / changes / unslotted, failed streams), and the bot DMs directly if it
  can't read the share. Ready/digest DMs end with the war room's tailnet link.
- `clipper alert test` (a test DM), `clipper alert digest [--send]`.
- Bug found while testing: same-tick sends overwrote each other (Windows clock); names now
  carry pid + a per-process counter + random bytes.
- The recorder's part needs the NAS redeploy (`bash deploy/record/deploy.sh`, no show live).

## War room quick wins (2026-10-23, #19 #21 #26 pop-out; #6 closed as not needed)
- #19: `actions.style_issues(show_cfg, values)` warns (never raises): "host(s)", words the
  bleeper would mask, title over 100 with the show prefix, description + hashtags over 5000
  bytes, tags over 480. Rows carry `issues` (card badge ⚠ n); the item window shows them under
  each field and rechecks as you type (POST /api/check). First pass: 22 of 302 items, mostly
  "hosts" in descriptions of approved Shorts (not fixed; they upload as written).
- #21: `warroom.upload_status` (24 h count like `clipper upload`, room, when room frees, next
  6 with "due" in upload_ahead_hours) and `last_upload_run` from bot.log + bot.log.1 (the bot
  logs a job's output only when it ends; a start in the same minute as the replan's end shows
  as "uploading now"). Uploaded schedule slots show views (analytics.json) and a YouTube link.
- #26 (first half): ⧉ on cards / "Pop out" in the item window / key O opens `/watch?src=` (a
  bare player, same cookie) at the current time. "Open the full recording at a clip" is still open.
- #6: republishing would reset approved items to waiting; uploads already send the fixed cuts.

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
