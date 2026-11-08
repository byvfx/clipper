# Clipper handoff (current state, rewritten 2026-10-28)

This file is the **current state** between sessions: what runs where, what the user decided,
what is waiting on them, open work, recipes, gotchas. Keep it short (under ~300 lines): update
the sections in place, don't append diaries here. The session-by-session diary is
**`docs/log/2026-11.md`** (one file per month, October in 2026-10.md; grep it, don't read it whole). At the end of a
session: fix what changed here, then add a dated entry at the end of the log.

Stream clipper for Kino Casino (Kick channel `kinocasinogaming`) and Channel 404 News → YouTube
channel **Lolcow Cinema**. Repo `C:\clipper`, remote https://github.com/byvfx/clipper (private).
Python package `clipper/`, config `clipper.toml` (gitignored; `clipper.example.toml` is tracked),
prompts `prompts/`. The user docs are the GitHub wiki, built from `docs/wiki/` (`.github/workflows/wiki.yml`
pushes it on every push to main; 2026-11-04); README.md and SETUP.md are short pointers; CLAUDE.md the conventions.
Project memory (`~/.claude/projects/C--clipper/memory/`, MEMORY.md first) holds durable preferences.
- Plan doc: https://claude.ai/code/artifact/a9bb9e38-ced0-4455-8d8e-18a78122f45f
- Walkthrough (usage, for presenting; Claude Docs, updated 2026-10-27): https://claude.ai/code/artifact/0d0e04da-54f6-4f15-bad0-2df72a286383
- Interactive demo walkthrough (HTML, 2026-10-27, numbers as of that day): https://claude.ai/artifact/ASL1grYiajRAELyoqTGitN
- Keys are Windows **User** env vars (ANTHROPIC_API_KEY, KITSU_TOKEN, ...); agent shells must load them:
  `export KITSU_TOKEN="$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('KITSU_TOKEN','User')" | tr -d '\r')"`

## Pipeline (one stream = `streams/<id>/`, test stream `kick-131151926`)
`clipper fetch <kick url>` → `transcribe` (WhisperX, GPU) → `signals` (chat/loudness heat) →
`score` (Haiku Pass A + transcript scan, Sonnet Pass B) → `segments` (Opus topic split + compilations) →
`cut` (segments lossless w/ keyframe starts, compilations joined, picks re-encoded; slurs+"fuck" bleeped) →
`meta` (SEO title/description/chapters/tags + .srt) → `thumbs` (Enquirer tabloid covers) →
`review publish` (Kitsu on NAS 192.168.0.155:8090 + Discord cards) → `review sync` / Discord bot →
`schedule` → `upload` (YouTube). Always on: `clipper serve` (task `clipper-serve`: the war room, hourly
replan + upload, pre-show grabs, retakes, daily jobs) and the Discord bot (`clipper bot`, task `clipper-bot`).

Around it (each a subcommand; details in the wiki pages and the log): `record` (NAS Docker recorder),
`worker` (GPU machine: fast lane clips/Shorts first, then segments), `shorts` (vertical, scene-aware
layouts, 15-59 s), `retake` (Kitsu notes → re-cut/re-cover automatically), `preshow` (Kino's YouTube
pre-show whole; also one-offs with `--from/--to/--about`), `pick` (Shorts from a stretch you name),
`weekly` (ten-page tabloid issue, Saturdays), `receipts` (old clips of an archive subject for a
stream's stories, Mother Moo voice-over), `archive` (back catalogs on Modal: Jer complete, FTS index,
`ask`), `investigate` (an archive subject's own videos -> verified facts -> leads + coverage check, #108), `speakers` (diarization names), `narrate` / `perform` (Mother Moo: ElevenLabs read, lip-sync
pieces, stitch), `tiktok`, `x`, `rumble`, `analytics`, `costs`, `modal`, `alert`, `warroom`, `doctor`.

## User decisions (keep)
- Stories retry YouTube's bot check signed in, as pre-shows do (user 11-03: "Yes, turn it on";
  `[story] youtube_bot_retry = true`, the default): a story is a handful of videos.
- Investigations (#108, 11-03): leads go to Discord `#investigations`, a thread per investigation (one
  `leads` run), each lead one short message (user: "shorten it up") edited in place by check/confirm,
  linking to the war room's Investigations window, where the whole dossier reads as rendered markdown
  (`[investigate] discord = true` in clipper.toml). First thread: jer · 2026-11-03.
- Titles/descriptions/cut points approved as-is; fun descriptions are liked.
- Long video per recurring topic (compilations, e.g. "The Quartering" 3h13m) plus segments plus short picks.
- `third_party_audio = "allow_reactions"`, `audience_knowledge = "fans"`, Victor/blackjack segments stay.
- Shorts over `max_source_audio_s` of other voices (speaker tags) arrive **held** in review with the
  seconds on the card, not dropped (user 11-04: "sounds good"); release it when the show carries it
  (`[shorts] hold_source_audio`). Kino and, from its next stream, Channel 404 (diarized 11-04).
- Kitsu is the record for review/edits (no separate DB). Discord approvals must keep working.
- Thumbnails: `[thumbnail] style = "enquirer"` (1990s tabloid: KINO CASINO masthead, no "THE NEW" roundel,
  no barcode, small "AS SEEN ON LOLCOW CINEMA" badge). Prompt `thumbnail_enquirer_v5` (face = headline
  subject; may use `[thumbnail] photos` portraits for people not on screen; v4 names the show's
  `[meta] cast`, live 2026-10-30; v5 trusts faces.json labels, live 2026-11-01). Cast per show: Kino = Andy + PPP, Channel 404 = Don (solo show,
  user 2026-10-30). Pre-show covers are one
  story, no side boxes (`[preshow] teasers = false`, user 2026-10-28: "single issue for preshows").
  The main face must never sit under the reaction inset or badge (user 2026-10-29): `face_crop(avoid=)`
  shifts the head, then shrinks it, until its box clears them (16:9 and Shorts). Library `photos` are
  whole-image boxes and aren't moved.
- Never use the word "hosts" in viewer-facing text (was "rarely" until 2026-10-20) → `[scoring] house_style` rules, appended to all viewer-facing prompts.
  Put future style notes there. Co-hosts are **Andy (Warski)** and **PPP**. Never call PPP by any other name (user,
  2026-10-19; Kick bio says "Andy Warski and PPP"). Who-looks-like-what is in [shows.kino.scoring] stream_context.
  Older drafts/Kitsu titles that say "Ashton" stay as they are (user, 2026-10-19); PPP from now on.
- Bleep: `[bleep] mode = "bleep"`, `profanity = true` (all forms of "fuck"), slurs always. Captions/titles masked;
  `youtube.metadata()` masks again at upload (#8).
- Shorts: 15-59 s hot takes, 3 slots a day (10/15/20); horizontal clips are cut and drafted but never
  scheduled (user, 2026-10-21). A Short switches layout at each scene cut (Haiku + reference frames).
- Uploads: automatic since 2026-10-20 (serve: `clipper schedule` then `clipper upload` hourly), private with
  publishAt, 15 a day hourly 08:00-22:00, `[youtube] max_per_24h` 15 (channel allows 100; user leaves room),
  shows take turns (`mix_shows`). `manual_publish = false`: **"asap" items (pre-shows, one-offs) publish
  public the moment they upload.** Every upload also joins its show's playlist. Source links are added
  at upload (clip → segment `?t=`, segment → compilation) and backfilled hourly.
- X (@lolcowcinema, 2026-10-31): `clipper x` posts Shorts as native video, never a link (X bills $0.20 a
  link post vs $0.015). Since 11-01 (user: "make both"): text is hook first (no "KINO CASINO |" prefix) +
  hashtags, and `--auto` posts approved Shorts from the last 72 h of streams, newest stream first,
  without waiting for YouTube. **Auto on since 2026-11-01 12:21** (user): `[x] auto_post_minutes = 60`,
  `max_per_24h` 6 (weekly thread posts count too), `max_height` 1280 kept, no "Automated" label (user
  skipped it). Since 11-03 (user: credit the footage's owners): up to 3 hashtags ending with the show's
  (#KinoCasino / #Channel404News) and a last line "From Kino Casino: @Andywarski @PoweroftheTruth" /
  "From Channel 404 News: @Channel404News" / "From Just Trish: @trishapaytas" (`[shows.<key>.x] mentions`). `clipper x --issue` posts the weekly as a thread (#115); #88 closed. Traction plan given the user: reposts from Kino/Don,
  hand replies under big accounts, posting while hot. Signed in 2026-11-01; first post
  https://x.com/lolcowcinema/status/2116800759941767587 (720x1280 re-encode accepted).
  Profile set 11-01 (bio, youtube.com/@lolcowcinema, YouTube avatar + banner crop in
  ~/Pictures/lolcow-x); the app browser pane is signed in to X.
- TikTok: **parked (2026-11-01)**. Auto-send off since 2026-10-28 (TikTok flagged bot activity); the user
  posts by hand. No direct posting: the app audit refuses own-account tools and the user won't release
  clipper to others (#62, #116 closed). Don't send drafts unless asked (`clipper tiktok --items ... [--resend]`).
- Transcription on Modal (L40S, ~$0.34 per 8 h) since 2026-10-23; Kino and (since 11-04, user) Channel
  404 streams are diarized (`[shows.<key>.transcribe] diarize = true`, ~$0.30 more; Don enrolled from
  kick-135875683 SPEAKER_06, one sample) and the scoring v5 / segments v3 / meta v3
  prompts read the speaker tags. Cut runs on the NAS (`[cut] backend = "nas"`) since 2026-10-26.
- Whisper writes "Bearing" as "Baring": fix it whenever he comes up.
- Weekly magazine (`clipper weekly`): Saturdays, cover can go full tabloid, inside pages modern; the user
  posts by hand (TikTok carousel + two YouTube Posts of square pages). Articles in the user's own voice
  (writer pass, `weekly_article_v1`).
- Mother Moo (The Udder Truth, #58): British, voice "Harriet" (ElevenLabs), "perform to playback"
  (the user lip-syncs to the AI read), WAN 3 Prime on Runway for the picture, Sync for lip touch-ups,
  finish in DaVinci Resolve. Rupert is recurring and voiced by the user. Quality over cost.
- Second channel **Sloth Beat** (#46: Papa Meat, Ben Avery, On Cinema; teen-fan-magazine look, HTML/CSS
  covers via Playwright when built). Runs from the desktop as the first clean install. Permissions open.
- Recorder alerts go to both the webhook channel (deploy/record/env.local; also "is live" / "video back")
  and DMs (warnings only), so warnings arrive twice: kept on purpose (user, 2026-10-28).
- Model calls are logged to `logs/costs.jsonl` (`clipper costs`); say what a rerun costs before doing it.
- The taste layer is the asset (prompts, clipper.toml show tables, house_style, Kitsu): see "Back up".

## State now (2026-10-30)
- **Running on the NAS** since 2026-11-05 (#9/#12, PR #152; wiki NAS-hub): containers `serve` (war room on
  127.0.0.1:8765, on the tailnet via the `tailscale` container as https://clipper.taile415cd.ts.net/ (signed in 11-05);
  hourly replan + upload, pre-show polls every 5 min, retakes, daily cast / news / analytics jobs;
  heartbeat `streams/.serve/status.json`), `bot` (Discord; heartbeat `streams/.bot/status.json`) and
  `worker` (each finished recording, every stage; cuts locally, QSV on the iGPU), compose project
  `clipper-hub` in `~/clipper-hub` on the NAS. Code changes go out with `bash deploy/hub/deploy.sh` from a
  main checkout (replaces restarting serve/the bot); `clipper services status|restart ...` runs docker
  compose there (`[serve] hub`). The laptop's three scheduled tasks are disabled, not removed (rollback:
  wiki NAS-hub). **The config and all state are on the NAS too:** `//192.168.0.155/docker/clipper`
  (`/data`) holds `clipper.toml` (the one copy; `CLIPPER_CONFIG` user var on the laptop points at it),
  `schedule.json` (files stored relative to streams/), secrets/, logs/, faces/, voices/, news/, weekly/,
  archive/, layouts/, rumble/, people.json, fonts/ (Impact etc. for the containers). The laptop's old
  copies are in C:\clipper\backups\laptop-state-2026-11-05\.
- **Mac `lofn`** (Apple Silicon, joined 2026-11-07 as setup's new `control` role, PR #166): a dev/control
  station that runs nothing unattended. Clone `~/clipper`; `CLIPPER_CONFIG=/Volumes/docker/clipper/clipper.toml`
  and `CLIPPER_LOCAL_CONFIG=~/clipper/clipper.local.toml` in `~/.zshrc` (the share must be mounted in
  Finder); local file sets `[paths] streams` (the share) and `[serve] url` = the tailnet war room. ssh
  alias `nas` in `~/.ssh/config`: user **`Brandon`** (capital B; lowercase is refused), key
  `~/.ssh/id_ed25519`. `clipper doctor --role control` clean (no Anthropic key or Kitsu token by
  choice); `clipper services status` reaches the hub. Homebrew ffmpeg there has no libass.
- **NAS** (192.168.0.155): recorder container (`deploy/record`, redeployed 2026-10-30 15:09 at 1e4f724,
  watching kinocasinogaming + donflurgundy + chudlogic since 2026-11-04 09:55), Kitsu :8090, the streams share, photos share, cut container.
- **Shows:** kino and c404 live through review → schedule; `chud` (ChudLogic, Kick `chudlogic`; user
  2026-11-04: "we have the clear to clip him") added 11-04: daily ~10 h from ~00:00-02:00 PT, budget
  segment 3 / short 5 per 8 h, 8-Bit Guy moments ranked up; first recording not yet run or reviewed;
  layouts `watch` and `cam` measured from a VOD (reports/chudlogic-2026-10-28.md has the background).
  His 10-18 VOD `kick-132975332` is fetched and transcribed only: its timestamps jump back 821.7 s before
  7:28 (transcript = source + 821.7 s), so don't `clipper run` it until fetch is fixed (proposed as its
  own task 11-04). An 8-Bit Guy explainer (Mother Moo v4 reads, edited in DaVinci Resolve: project
  "8-Bit Guy explainer" on the laptop, timeline v6, Resolve MCP registered locally) went out on X 11-04
  with burned-in captions (https://x.com/lolcowcinema/status/2118120833046741152); files and recipe in
  reports/8bitguy-explainer/ and its draft. ElevenLabs reads default to lossless pcm_48000 WAV since
  PR #143 (merged 11-04); dsp and blade are config-only tests; `trish` is a
  placeholder show for the 2026-10-27 one-off (public https://youtu.be/Mh6JcTACFlI). trish and blade are
  off the war room (`[warroom] hide_shows`, user 2026-11-05: "i doubt we will ever clip either right now").
- **Schedule: stay current, no backlog (live 2026-10-29 09:51).** 15/day couldn't keep up (~140
  approved a week vs 105 slots, newest stream 15 days behind). `stale_days = 3`, `[scoring]
  max_picks` 14 (was 10). **Since the October check-in (2026-11-02, #42):** `stream_budget = { segment
  = 10, short = 10, compilation = 0 }` per 8 h (`budget_hours`), Channel 404 `segment = 6` (Don ~4/7/0);
  slots a day: 9 Shorts (09/11/13/15/16/18/19/20/21), 6 segments (08/10/12/14/17/22), compilation 23:00
  for big-story nights only (`docs/big-story.toml` into the stream's config.toml; compilation = 0 means
  none unless pinned, PR #120 merged 11-02 as d12beb1). Backup
  backups/clipper.before_october_checkin_2026-11-02.toml. The old backlog's unuploaded slots dropped (backup
  backups/schedule.before_stay_current_2026-10-29.json); the 10-26/27/28 streams fill through 11-01. Items let
  go stay approved in Kitsu (war room filter "Not going out"); pin with "Publish at" to send one anyway.
  Watch the first week: if good items keep going stale while streams overlap, try `stale_days = 4`.
- **Archive:** Jer (The Quartering) 11,571 items / 2,732 h transcribed on Modal, FTS index built,
  `archive ask` works; link checks built but not yet run against the real services. Other archive
  projects (#44 Jerry Banfield, #45 Papa Meat, #47 Ben Avery) wait on the desktop install and permissions.
- **Receipts for Don:** first video private at https://youtu.be/q6gemx2kKMs (2026-10-26); sending it is the
  user's call. On-screen source links would need a re-cut.
- **Weekly:** issue 03 (Oct 25-31, Jer's Huck MRI grift cover) built 2026-11-01 in `weekly/2026-10-31/`,
  posting page https://claude.ai/artifact/TnP9uXjpyGVpJC2KPipM6o; the user posts TikTok/YouTube by hand.
  On X since 11-01 as a thread (`clipper x --issue`, after the user OKs the text):
  https://x.com/lolcowcinema/status/2116852491925254298. Issue 04 is
  Saturday 2026-11-07 (`clipper weekly --headline "..."`; on a Sunday add `--week <Saturday>`).
- **The Udder Truth:** storyboard complete, voice and pipeline chosen; next is the newsroom set (Brass Eye
  refs in mockups/udder_truth/refs/, blank-screen set for Fusion), new desk plates, then Moo's WAN renders
  (~1,550 credits; 18,457 Runway credits left on 10-24).
- **Costs:** October ~$200 in model/GPU spend, ~$184 of it the Jer archive; `clipper costs`, war room
  Costs, Modal and API usage panels (`clipper costs --providers`: Claude's and OpenRouter's own count next
  to our log; Claude's needs an Admin API key, `ANTHROPIC_ADMIN_KEY`, not set here: waiting on the user). Monthly analytics check-in task runs on the 1st at 09:00; October's
  (`reports/analytics/2026-10-checkin.md`) was gone through 11-02: 1-4 taken, 5 (titles without the
  show prefix) skipped; its Decisions section is what November's check-in follows up.
- **Analytics:** YouTube Analytics API enabled 10-28; October so far 12.7k views, 68.5 h watch, +28 subs,
  Shorts feed 80% of views.
- **Compilation joins fixed 2026-10-28** (user spotted Andy "repeating himself" at 2:18:42 of the H3
  compilation): each segment file starts on the keyframe before its start, so back-to-back segments
  replayed 1-5 s of the previous one at every join (13 of 15 in the H3 video, also Quartering/DSP/Angry
  Joe). `cut_compilations` now ends each part with a concat `outpoint` where the next file begins.
  Only new compilations get it; the user chose not to re-cut or re-upload the ones already out.

## Waiting on the user
- Bootstrap fixes from setting up the Mac, both open 11-07: PR #165 (setup path prompts take `~/`,
  quotes, a dragged-in file; Copilot's 3 comments handled) and PR #166 stacked on it (`control` role;
  join uses a config on the share in place via CLIPPER_CONFIG / CLIPPER_LOCAL_CONFIG; keys optional
  for control; doctor `online_hub`). Merge #165 first; #166 waits for Copilot's review.
- The DSP story video is in Kitsu for review (Kino Casino / "2026-11-04 story-dsp-credit-score-590",
  Discord card posted, user's go 11-04): "DSP Says the 590 Credit Score Letter Is Fake", 3:14.
- TikTok drafts to post by hand: last night's four resent 10-27; the 10/25 batch (kick-133940023 v04_t04,
  v05_t05, v07_t14, v08_t18) is still unposted and would need `--resend` once there is room.
- Nothing waits for approval in Kitsu (checked 2026-10-30: no WFA/retake task in any open production;
  the war room's Review queue shows the same). Approved-and-scheduled Shorts that a scene re-render would change (v06_w11, v03_w06, v03_t23) are
  left alone until the user says.
- Content ID: all five pre-show/one-off uploads checked in Studio 2026-10-28, no claims, no region
  blocks. Keep checking new ones (Studio → video → Copyright; the Data API only sees region blocks).
- Shorts "related video" (Studio only, the API can't): 33 of 54 uploaded Shorts have one (19 on 10-28,
  14 more on 11-03; marked `related_video` in schedule.json). `clipper upload --related` lists what's
  due and the 09:00 digest carries the same list; after setting them, `clipper upload --related <keys or
  links>` records them. The rest wait for their segment to go public, or have none: kick-133423456
  v01_w10, v04_t28 and v02_t12, kick-133940023 v14_t35, kick-132380636 v01_t29 and kick-132733064
  v01_t27 match no scheduled segment (pick one by hand or leave them). Doing it in the built-in browser
  (signed in to Studio): the field is `#linked-video-editor-link`; click its trigger, search the
  segment's video id, pick the one result, Save. A weekly scheduled Claude task does exactly this,
  Sundays 10:00 while the app is open (`clipper-weekly-related-videos`, set up 11-04). Verify whether `thumbnails.set` works on Shorts before turning on
  `[youtube] short_thumbnails`.
- Old approved Short titles/descriptions that still say "hosts" (Kitsu text; uploads go as written).
  A war-room find-and-replace dry run found 14; not applied.
- Decisions on #66 (transcript-framed edits): which item kinds, replace or sit beside the plain cut,
  sound effects, order after #67. Sloth Beat handle check (@slothbeat).
- Cast (#41): it fills itself now; look over Kept and New in the war room's Cast window
  (drop wrong or costume photos; image search photos always wait for you). Check Jer f868aa21db.
- Faces: enroll PPP in his maple-leaf cap from the first non-costume stream the worker scans (kick-135553454
  is named and enrolled: known.json has 12 people, Andy/PPP/Jer 4 samples each).
- `clipper investigate` (#108, merged 11-03: PR #131 343a062, Discord + war room PR #132 ecf07ba): jer Sept-Oct was a test; the user
  found all 3 leads good (L03's $8,000 is Whisper: he said $1,000, noted on the lead). User 11-03: leave his old
  videos alone for now (no back-catalog facts; `investigate run jer` reads only new ones). Still open:
  whether a lead may ever become content.
- Other model providers (#72, user 11-04: OpenAI, Gemini, local and OpenRouter): OpenRouter key in
  secrets/openrouter_secret.txt; no OpenAI/Gemini key and no Ollama yet. Try Pass A first:
  `clipper compare score <stream> --model openrouter:<vendor>/<model> --dry-run` for the estimate
  (Wiki Model-providers). Haiku vs Sonnet on kick-135553454 Pass B ($0.003): Haiku kept 4 of the 5 approved.
  User picked OpenRouter (11-04; key in secrets/openrouter_secret.txt). Sweep 11-04/05: 12 models x reasoning
  none/medium on kick-135553454 Pass A, leaders on kick-134467434 + kick-134812118 (`clipper compare list <stream>`).
  None near Haiku+Sonnet (18/18 approved): Gemini 4 Argon 7/18 (closest verdicts, fewest Haiku rejects kept,
  ~20x Haiku's Pass A cost), Flash Lite 7/18 (5/5 was luck), DeepSeek V4 Pro 5/18, GPT-6 Luna ~5/18, DeepSeek
  Flash 1-2/5. All keep 3-8x Haiku's windows, mostly ones Haiku rejected (third party, needs context). Reasoning
  on/off a wash for Pass A. Single runs are noisy (Luna 4/5 then 2/5). The approved set only holds the baseline's
  picks, so it favours Claude. Haiku scoring cost $0.67 in Oct-Nov; the spend is segments (~$16), thumbs, meta.
  v6 (stricter score_a + rule files, 11-05; config stays v5) and the control: Haiku v5 rerun (`compare --repeat`)
  picks only 7/18 of its own approved moments, so that metric is mostly noise. 18 approved on 3 streams: Haiku
  rerun 7, Haiku v6 7, Argon v5 7, Qwen 3.8 Flash v6 6, Luna v6 5, DeepSeek Pro/Flash v6 4, MiniMax v6 4.
  Verdicts shared with the original Haiku run: Haiku rerun ~73%, Haiku v6 ~59%, Argon ~57%, the cheap ones
  34-43%. v6 made Haiku keep more, not less: not adopted. A fair test needs blind review of each model's picks.
  User 11-05 (build it agnostically, "in case someone else ever wants to use it"): two tests (Wiki
  Model-providers). `clipper models test <model>` = built-in conformance (made-up windows through real Pass A,
  JSON, vision, text, 130k-token needle) + which configured stages it could take: Haiku, Qwen 3.8 Flash and
  DeepSeek V4 Pro (all @none) 12/12 verdicts, long ok; V4 Pro has no image input (not for shorts layout or cast
  labels). `clipper models gold propose|confirm|run` = answer key: logs/model_tests/gold.json, 66 windows from
  the 12 newest reviewed streams, all labelled by the user 11-05 (label page
  https://claude.ai/artifact/Jre3fSSFVMDc51deRLSUoE, db collection `labels`; 45 keep, 9 third party, 6 needs
  context, 6 no clip). The user's notes: keep Jer's gambling (they mock it), "Kino Casino" is the show's name,
  harsh/outrageous is fine (slurs get bleeped). Labels right, v5 / v6 / v7: Haiku 26 / 28 / 39, Qwen 3.8 Flash
  44 / 36 / 40, DeepSeek V4 Pro 46 / 44 / 48 (about 3, 3 and 27 cents a run). Prompt v7 (PR on branch
  prompt-v7): show setting `[scoring] gambling = reject|commentary` (rule files gambling_<mode>_v7), the cast
  reading a post/chat/statement aloud is their own audio (matches the #146 hold; this alone took Haiku's
  keep-called-third-party from 11 to 1), regulars' running stories count as known to fans, outrageous counts
  for a clip. LIVE 11-05 (PR #150): `prompt_version = "v7"`, `gambling = "commentary"` in
  [shows.kino.scoring] and [shows.c404.scoring] (backup backups/clipper.before_prompt_v7_2026-11-05.toml);
  chud and the rest stay on v5. Left: Haiku now
  over-calls needs_context (8 keeps); every model misses needs_context and keeps 5-11 of the 21 non-keeps.
- Papa Meat permission (#45), Ben Avery archive pull (#47), Jerry Banfield pull waits on desktop/storage (#44).
- `clipper setup` (#78) phase 1 passed in Windows Sandbox (2026-10-28). Next: the real install on the
  desktop (#46; clone into a short folder like `C:\clipper`, run `deploy\bootstrap.ps1`), then the
  MacBook (#7). Sandbox is enabled on the laptop; the test recipe is under Recipes.
- Weekly issue 03: post it by hand (TikTok carousel + two YouTube Posts) from the artifact page (X done).
  `clipper weekly --discord` (11-02) put its pages + captions in Discord `#weekly` (the bot made it) for the phone;
  run it each Saturday after the render (PR #125 merged as c019eab). The user gave the bot full permissions 11-02
  (could be cut back to Send Messages + Attach Files).
- YouTube sign-in died 10-29 19:54 (invalid_grant, 7 days after the 10-22 analytics sign-in = OAuth app in Testing); uploads failed hourly until the user signed in again 10-30 (~10:30, all 3 scopes kept). The user published the OAuth app (In production) 10-30 afternoon. The user signed in again 11-02 16:57 (In production), so the token shouldn't lapse.

## Open work
**Bootstrap follow-up (11-07):** `deploy/bootstrap.sh` installs Homebrew `ffmpeg`, which has no
`subtitles` (libass) filter, so a Mac worker fails Short captions; `ffmpeg-full` has it but is
keg-only (not on PATH). Pick one: install `ffmpeg-full` and point clipper at it, or document it.
**Story (#84) + swarm (#109), user 11-03: three PRs.** 1) `clipper/swarm.py` (library: planner →
Haiku workers in rounds → merged findings, trail `swarm.json`; tools YouTube search/channel, Kick,
Odysee, Reddit, web, archive, our streams; `kick.list_videos`; `llm.Budget` now shared with
investigate): merged 11-03 (PR #134; X credits PR #133 before it). Live test on Jer's Huck story: 30
tasks, 109 findings (44 strong), $0.025. YouTube flat listings: `ytsearchdate` is gone from yt-dlp;
search URL + upload-date filter `sp` + `youtubetab:approximate_date` instead (sort-by-date is ignored).
2) `story` phase 1 (`clipper/story.py`, merged 11-03 as PR #135): brief
(people from `research_notes/streamers.json` + `[story] people`, their channels seed the swarm) →
swarm → triage (Haiku, `--max-videos`/`--max-hours`; our own streams linked via the catalog's `dir`,
not re-transcribed) → ingest (archive's Modal path; new Kick VOD branch: smallest HLS variant →
flac) → find (Haiku queries + judge over `story-<slug>` and archive subjects the swarm hit; quotes
verified with investigate's `locate`) → dossier (Sonnet; M/S cites must resolve, one retry; `notes`
and `struck` survive). Files in `paths.archive/story-<slug>/`. Live 11-03 "DSP says his credit
score is 590": 33 tasks, 92 findings, triage 3 + 5 of our streams linked; the YouTube downloads hit
the bot check, so the dossier came from our streams alone (7 moments, good timeline and conflicts),
~$0.08 in models. Ingest now stops on a failure. After a fresh cookie export the four transcribed signed in
(Modal ~$0.82: cold starts dominate a story's few items, so `[story] parallel` 2). Kick audio path checked on 2 min of a DSP VOD.
3) phase 2 (`clipper/story_edit.py`, merged 11-04 as PR #136, serve restarted on it): `story
plan` (Sonnet, ~$0.01: moments → ordered parts with chapters; code bounds each around its quote, drops a
part repeating an earlier one's words, merges overlaps, caps at `[story] max_minutes`) →
`streams/story-<slug>/story.json` (hand-editable; meta.json kind "story" on the show's Kick channel) →
`story cut` (our streams from their source on the share, others by yt-dlp section; receipts' renderer with
a lower third; one segment s00 with a chapter per part and retimed words.jsonl) → meta (sidecar chapters,
`[story] credit` + a source line per part) → thumbs → Kitsu (episode "<date> story-<slug>"); schedule
asap. Weekly skips stories; a retake trim refuses one (edit story.json, cut again). 4) `investigate
check` on the swarm (branch `investigate-swarm`): the old four searches are the seeds of a small swarm
per lead (`[investigate] swarm`), then his own titles; the verdict as before. 5) narration (PR #149,
merged 11-05 as 7c2862e, services restarted on it): story_edit_v2 plans in the dossier's timeline order and
writes `tldr`, a `lead_in` per part and `close`; `story cut` reads them as cards in `[story] voice_id` =
Eric (cjVigY5qzO86Huf0OWal, set in clipper.toml 11-05, backup in backups/). The user's catch-up format for
any drama: `story new` → `story run` → `story plan` → `story cut --no-review` (never sent to review, so
never uploads), a <30 MB 720p copy to the phone. Check lead-ins against the dossier (one stated a
self-claimed role as fact). First: kevin-langue-show-drama (Kino show, 5:28).
**Investigate anyone (#138, user 11-04; part 1 merged 11-04, PR #141):** investigate on a person not yet archived (swarm
finds their channels and X handles, the user approves, a sample spread over time is transcribed into a normal
subject, then `investigate run`), cross-referenced with others' videos and X posts (API, not scraping),
every fact with a speaker and said/posted dates, one people record across archive/streamers/cast/facts,
and an Obsidian vault export. Order in the issue: people records → speaker attribution (needs #53
embeddings, new facts prompt) → `investigate start` → X reads (check pay-per-use read price and
full-archive search first) → vault → watchlist (serve, alert on change only) → public records (CourtListener,
needs a token) → `investigate eval` on known stories (Huck, DSP; build early) → Good/Noise buttons + `tune`.
Also: a review gate before a lead becomes content, Wayback snapshots of receipts, source reliability
notes + a never-read list (Kiwi Farms).
Part 1 (`clipper/people.py`, `clipper people list|show|set|import|merge`): people.json (`[paths] people`,
gitignored) is the record; streamers.json, `[story] people` and cast folders fold in by name; story's
roster and investigate's alias merging (subject's names = self, aliases.json wins) read it. Imported here
11-04: 23 people, Don and Mel mac merged into channel-404-news and melonie-mac, no shared names. Next: part 2.
**Code audit 2026-11-03** (`reports/code-audit-2026-11-03.md`): correctness pass on everything since
10-28 plus an architecture/usability look. 50 findings (1 high: a cut-short `live/chat.jsonl` line
wedges a stream's finalize; ~15 medium: quiet failures in the bot's notify loop and Health, `[notify]`
time strings never validated, a passed Publish-at pin dropped as stale, upload `done` without
publish_at, Pass B ids never checked against kept candidates, re-runs of score/cut+thumbs/weekly
--render that repay model calls, cast auto-keep one-sided for enrolled people, stream ids and effort
values unvalidated). Proposed order and the user's questions are in the report. **Done: the
`clipper serve` split (PR #128, merged 2026-11-03 as 71f29f9; State now), which also fixed audit #2 (tailnet
status before the notify loop's try) and #3 (Health from a lock file that never went away: now
heartbeats). The other findings are not fixed yet.** User's direction answers (2026-11-03): split the
jobs loop and the war room out of the bot into `clipper serve` so the laptop can sleep (first step of
#64/#12; done); the war room is the
review surface, Discord cards kept because they approve from anywhere (phone); keep building as if
for a public package (little tech debt) but simple for the user first, with a packaged release in a
few weeks once it has been used and tested; the next three features
are #84 story, #108 investigate, #109 swarm, everything else can wait; the second channel on the
desktop (#46) is still planned and goes with the install work; the day-to-day annoyance is switching
between Claude and the war room/Discord (#13 chat panel); services going quiet is the failure they
see. "Let's proceed" (11-03): groups 1 and 2 of the report's order (quiet failures, cost guards:
#1, #4, #5, #9, #10, #11, #16, #17, #19, #20, #21, #26, #36) merged as 86e04f9 (PR #129; serve and
the bot restarted 13:22); groups 3-6 (scheduler/upload consistency, input edges, Pass B hygiene, cast and
weekly: #6, #7, #8, #12, #13, #14, #18, #22, #23, #27, #28, #38, #39, #40, #42, #43, #44, #45) merged as
9f8a04e (PR #130; serve and the bot restarted). Defaults taken where a behaviour question was open: a pinned Publish at
never goes stale; a passed slot never goes public at once from a long run; an X post that times out is
"unknown" for a hand check, never retried; a cast grab is refused when another enrolled person fits
better. Left for later: #15 (example toml commented), #24/#25 (lock edges), #29-#35, #37, #41, #46-#50
(lows), #42's lock part.
**Maintainability audit 2026-10-28** (`reports/code-audit-2026-10-28.md`): **done**, every item merged
on GitHub main by 2026-10-30 (PRs #82-#100, #102-#104; 153 offline tests, CI on every push) and
running here since 14:59 that day. New shapes: `clipper/record/` and `clipper/thumbs/` packages,
`draw.py`, war room split into `web/warroom.css` + `web/js/*.js` (wiki War-room page), `clipper services
status|stop|restart`, `deploy/common.ps1` for both task scripts, per-show `[meta] cast`, Kitsu push of
changed schedule values only (`schedule --kitsu-all` for everything), doctor checks every show's prompts.
**#101 (weekly split) merged 2026-11-02** as d9e1477: `clipper/weekly/` package (gather, draft, render,
faces, style), prompts through Prompts, the look as data in `<paths.weekly>/style.toml` (doctor checks
it). Issue 04 (11-07) is the first one built from it. Worktree and branch removed (GitHub too).
Tracked as GitHub issues (byvfx/clipper). Still open from the 10-23/10-24 audits: #72 (`clipper compare score` built
11-04 with `openrouter:`; left: a stage moved after a compare, `clipper edit`), #50 (revenue once monetized); #42 closed 11-02 after the October check-in. #77 is done
(#106, 10-30): YouTube quota counted per Pacific day in `logs/youtube_quota.json` (each upload run
ends with the total; link updates and caption retries only spend what's left after `upload_units`
per upload still allowed today), worker takeover under a lock, pick numbers kept across re-scores
(pass_b `numbers`), Short retakes grown to min_s, failed captions retried hourly (3 tries,
`upload --captions`). Write "#77 L6" rather than "fix #77" in commit messages: GitHub closes
the issue on "fix". Roadmaps, issues only: NAS hub #14 (#9 war room and #12 worker/bot on the NAS done 2026-11-05,
#64 single-owner daemon `clipper serve` (split done 11-03), #65 Claude Code on the NAS (PR #155, running since 11-05: `deploy/claude`, wiki Claude-on-the-NAS; doctor --role claude clean after PR #156), #13 chat panel),
transcript-framed edits #66 (#67-#71), YouTube Posts magazine #30 (#32-#38), cast library #41,
lore-order playlists #43, archive fun #63, news watcher #110 (off since 2026-11-03 at the user's word: `[news] check_minutes = 0` in clipper.toml and no terms; since #127 only `[news] watch` / `[shows.<key>.news] watch` terms are watched, none = nothing fetched, `auto_watch = true` brings back the cast/figures/library list; #news exists), Udder Truth #58/#59/#60, Modal ideas #53, #55, #56.
**Faces #54 (merged 2026-11-01, PR #117):** `clipper faces <stream>` runs locally on the CPU (no Modal:
the cost is reading the video, not compute; YuNet + SFace via OpenCV 5, `--extra faces`). Groups each
stream's keyframe faces into faces.json + faces/F01.jpg sheets, names them against `faces/known.json`
(every show), `--name F03=Jer --enroll`. kick-131151926 scanned (191 groups, nobody named yet: the user
names from the sheets; enroll Andy/PPP from a non-costume stream). Covers done: thumbs adds
named people's best frames (labelled) and uses the found head box (`face_found`); clipper.toml
pins `enquirer_prompt_version = "v5"` since 2026-11-01 (backup in backups/). Spot check passed (t20 Jer).
kick-131151926 rescanned 2026-11-01 (7.3 min, names carried) and enrolled: known.json has Andy, Jer, PPP (+ the mouse costume); on re-apply F21/F44/F59 auto-named Andy, F36 PPP, all right on the sheets.
War room (merged 2026-11-02, PR #118, bot restarted): a stream's Faces window names/enrolls groups from the sheets,
"Who's this?" (B) boxes one face on a paused video and keeps it; × forgets someone; same on the CLI
(`--at/--box/--person`, `--forget`). Enrolling now re-applies names, so look-alikes are named at once.
Saved where: enrolled faces go in `[paths] faces` known.json (here `faces/known.json` in C:/clipper, local, not on the NAS: back it up; the desktop install needs a copy); hand names in the stream's faces.json on the NAS share.
**Stage + weekly (merged 2026-11-02, PR #119, #54 closed):** `faces` is a worker stage after the fast lane,
before the slow cut (user's pick: picks/Shorts don't wait; segment and compilation covers get faces); in
clipper.toml's `[worker] stages` since 11-02 (backup backups/clipper.before_faces_stage_2026-11-02.toml),
worker restarted; only new recordings get it (done runs are skipped). Doctor fails if the stage is listed
without OpenCV; `clipper setup` installs the faces extra for a worker. The weekly takes each named person's
3 best faces per stream (`[weekly] face_frames`) ahead of the thumbnail stage's guesses (frames cached in
`<issue>/faces/`). kick-135553454 (11-01) scanned: F02 Jer and F17 Mel mac auto-named; F01 = Andy (normal
look first hour, then costume), F04 = PPP in a crocodile mask all night. October was costumes every night,
so a normal-look PPP sample waits on the first non-costume stream (Waiting on the user).
**Cast library #41, part one (merged 2026-11-02, PR #121 as 41ddd18):** `clipper cast
list|grab|find|add|keep|drop|label|set` and the war room's **Cast** button (bot restarted 17:38, live).
Photos in `[thumbnail] photos/<Person>/` + cast.json (NAS photos share); sources: a stream's named faces,
Wikimedia Commons, the person's YouTube/Kick channels, Brave image search (`$BRAVE_API_KEY`, not set yet;
agencies blocked), a file or URL. Face-checked against known.json, duplicates skipped, 4:5, Haiku labels
(prompts/cast_label_v1.md, ~$0.0002 a photo). `thumbs.photos()` takes a person's best kept photo (neutral
first), so covers and the weekly use them already. Tried into a scratch folder only: the real folder still
has just Hasan Piker.jpg and luther morgan.jpg; Jer's YouTube thumbnails are designed graphics (flagged),
streams are the better source for him, Andy/PPP need a non-costume stream. Commons spells "Candace Owens":
add it as her alias. **Part two (merged 2026-11-02, PR #122 as 1b69a90, live):** covers pick a cast photo
by label (prompts/thumbnail_enquirer_v6.md, `photo_label`/`teaser_photo_label` in thumbnails.json; the war
room's cover editor offers "Jer · angry"); the weekly v3 pictures people only talked about from a photo
and picks the label (issue.json `photo_labels`); `{known_figures}` adds everyone with a kept photo plus
aliases (`[cast] known_figures`). clipper.toml now `enquirer_prompt_version = "v6"` (backup
backups/clipper.before_prompt_v6_2026-11-02.toml); bot restarted 18:26. #41 closes once a cover and a
weekly issue (04, 11-07) use kept cast photos.
**Self-filling (merged 2026-11-02, PR #123 as dd7d04e, live):** worker stage `cast` after `faces` (`cast grab
--needed`), the bot's daily `cast auto` (Commons, channels, Brave), sure photos auto-kept (stream/Commons/channel
only, enrolled match >= 0.65, unflagged label, up to [cast] target 6). The Brave key is in secrets/brave_secret.txt
(user, 11-02; works: Ethan Klein search added 6 New). clipper.toml `[worker] stages` now has `cast` after `faces`
(backup backups/clipper.before_cast_stage_2026-11-02.toml); bot + worker restarted 19:43; the bot ran the
first `cast auto` itself at 19:43:58 (its job starts 30 s after startup, then every 24 h). Jer f868aa21db was auto-kept at 0.73 from
kick-135553454 but may be a costume lookalike (his enrolled samples are from that stream): the user checks.
Older notes not on an issue: republish the 2026-10-19 re-cut streams to Kitsu (user said not yet);
Blade compilation's Discord card never posted (`review notify kick-131151926 --keys comp-blade-in-vegas`);
a daily "post kit" folder; `clipper setup` walkthrough #78 (phase 1 done 2026-10-28: bootstrap,
roles, `clipper.local.toml`, keys; next join mode on the MacBook #7, new stack on the desktop #46);
thumbs: reject a picked frame with no face, `--frame-at <s>`; Discord excerpts for one-offs could
start after the intro; pick/preshow/retake prompts don't read speaker tags yet.

## Recipes (how we did it last time; details in the log)
- **Restart serve / the bot:** `clipper services restart serve|bot|worker` (docker compose on the NAS over
  ssh); code changes: `bash deploy/hub/deploy.sh [serve|bot|worker]` from a main checkout (Git Bash).
  A serve restart or deploy kills a running upload: `clipper services status serve` lists its jobs; wait
  until none is running.
- **Upload one item now:** approve in Kitsu, `clipper schedule` (back up schedule.json first), then
  `clipper upload --max 1`; asap items go public at once. Logs in `logs/upload_*.log`.
- **One-off from a YouTube video:** `clipper --show <key> preshow fetch <url> --from H:MM:SS --about "..."`
  (add a `[shows.<key>]` table with a placeholder channel if the show isn't on Kick), then
  `clipper run yt-<id>`. YouTube bot checks: a fresh cookie export into secrets/youtube_cookies.txt.
- **Fix a cover by hand:** edit the entry in `thumbnails.json` (frame is 1-based; `t`, `face`, `headline`,
  `text`, inset, band), back it up, `clipper thumbs <id> --ids sNN --compose-only` (attaches in Kitsu);
  `youtube.set_thumbnail` if it's already up. The war room's cover editor does the same (#18).
- **Hand picks:** `clipper pick <id> --find WORD`, then `--from/--to` or `--at` with `--about`, `--dry-run` first.
- **Retake without changing anything:** `clipper retake <id> --item c03 --comment "..." --dry-run`.
- **TikTok by hand:** `clipper tiktok --items <keys> [--resend]`; captions land in Discord #tiktok.
- **NAS recorder deploy:** `bash deploy/record/deploy.sh`, only when nothing is live.
- **Per-stream overrides** (a big night): `streams/<id>/config.toml` (`[schedule] priority = true`,
  `kick_first_hours = 0`, more picks). `CLIPPER_SCHEDULE_START=<date> clipper schedule --reshuffle` refills today.
- **Checks:** `clipper doctor --role gpu --offline`, `uvx ruff@0.17.3 check clipper tests`,
  `.venv/Scripts/python.exe -m pytest -q`, test stream `kick-131151926`.
- **Clean-Windows test of the install (#78):** `git archive HEAD` into a folder, a `.wsb` that maps it
  read-only plus an answers file and a writable out folder, and a LogonCommand script that copies the code
  to `C:\clipper`, runs `deploy\bootstrap.ps1 --answers ... --offline`, then doctor and `setup --check`
  into the out folder. No keys go in. Launch the `.wsb`, wait for a DONE file (~1 min), close the Sandbox.

## Back up the taste layer (user, 2026-10-24)
The Python is replaceable; the months of editorial judgement are in prompts and config. Treat them
like footage. Nothing automates this yet (a `clipper backup` or a NAS cron is an open idea, not built).
- **In git (push it):** `prompts/` (every version, never edit one that produced reviewed output),
  `clipper.example.toml`, `mockups/`, `research_notes/` (with `streamers.json`), `reports/`, this file.
- **Not in git, copy by hand or script** (all in the NAS folder since 2026-11-05): `clipper.toml` and the `backups/` folder (`clipper.before_*`,
  `schedule.before_*`; hand-edit backups go there now, not the repo root)
  (show tables, house_style, stream_context, scoring rules), each `streams/<id>/config.toml`
  (per-stream overrides; lost with the stream folder), `secrets/` (YouTube/TikTok tokens are a
  re-consent to recreate), the photos share `//192.168.0.155/docker/clipper/photos`, `layouts/`
  (Shorts layout references per show, and per-show `enquirer.toml` cover overrides), `schedule.json`,
  `deploy/*/env.local`, and the Kitsu volumes on the NAS (every review decision and note).
- Also the project memory `~/.claude/projects/C--clipper/memory/` on the laptop: it holds decisions
  that never made it into HANDOFF.

## Gotchas learned
- The claude container (2026-11-05): its clone sits on a mount where every file is executable, so ruff
  there needs `--extend-ignore EXE002` (CI is unaffected); its GitHub token can't read checks or Actions
  (403), so the user confirms CI on the PR. Relative config paths outside `[paths]` must resolve against
  the config's folder: the hub runs in /data, this container in /app (`[thumbnail] photos`, PR #156).
- New Windows installs (found by `clipper setup`, 2026-10-28): keep the folder short (anthropic's files
  sit ~160 characters deep in .venv; past 260 imports fail with long paths off), and `tzdata` must be a
  direct dependency (Windows has no zone database; the laptop only had it through pandas).
- Kick: yt-dlp VOD extractor 404s; resolve via channel videos API (kick.py). Chat history API ~2 req/s,
  429 with Retry-After: 0 → adaptive limiter.
- Stream-copy cuts must start on a keyframe (every ~4 s) or audio drifts; `.ts` seeking for re-encodes
  needs keyframe seek + output trim (cut.py handles both).
- Reading a shared JSON file on the NAS while another process os.replace()s it raises PermissionError
  on Windows (analytics died on review.json 2026-10-28 14:57). Read state files with `jsonstate.read`,
  which retries for ~9 s like `write` does, not `json.loads(path.read_text())`.
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
- `schtasks /End /TN clipper-bot` (or clipper-serve) leaves the supervisor's process alive (locks in
  streams/.bot.lock, .serve.lock); `clipper services` / the task scripts stop the supervisor and its children too.
- `deploy/common.ps1` finds serve/bot/worker as a clipper.exe command line ending in "serve"/"bot"/"worker";
  `clipper.exe services restart bot` ends in "bot" too and killed itself mid-restart (10-30, bot down
  ~5 min). It now skips its own process chain; keep that if the match changes.
- The war room page loads `warroom.css` and `js/*.js` from disk: a pull that changes the page before
  serve restarts leaves the old server unable to serve them (bare, unstyled page). Restart serve.
- schedule: a compilation approved as its own Kitsu item needs segment_keys (fixed 2026-10-18).
- serve runs `schedule.run` in-process (the war room's and the bot's replans go through its /api/replan):
  scheduler changes need a serve restart. A bot whose serve is down still writes the decision to Kitsu and
  says "not replanned"; the next hourly run places it.
- Retake planner can't edit cover layout; a note about the cover's boxes comes back "unsupported",
  or worse, gets read as an edit it can make ("single issue" became a 63 s trim, 10-28). A layout
  rule belongs in config; undo a bad retake with `retake.apply` + `publish_retake` and a hand plan.
- A recording with dropped HLS segments used to shift the transcript clock (kick-132733064, 46.9 s);
  `extract_audio` now fills gaps with silence. Check new recorders' gaps against the source length.
- Discord review cards carry only the first 60 s of a segment; the full preview is in Kitsu. A reject
  from the card can be about the excerpt, not the cut.
- YouTube upload sign-in lasts 7 days while the OAuth app is in Testing (published 2026-10-30; tokens
  from before that keep the 7-day limit). `clipper doctor` (online) refreshes it to test it; `upload --sign-in` keeps the analytics scope now.
- YouTube bot-checks this IP for yt-dlp; pre-show/one-off grabs retry signed in with
  secrets/youtube_cookies.txt, which browsers rotate: re-export when "cookies are no longer valid", and
  leave the export in secrets/ (the newest `www.youtube.com_*.json` there is used, or copy it over
  that file). A pre-show stuck on the bot check after the signed-in retry DMs once per video. The
  block is on the IP: a PO-token provider (bgutil, tried 10-31) doesn't get past it.
  `[youtube] cookies_from_browser = "firefox:clipper"` (11-04) reads a dedicated Firefox profile live
  instead, so rotation can't strand an export (wiki Archives#signed-in-through-a-browser-profile); doctor
  checks the profile is signed in.
- Kino's pre-shows moved to the main channel @TheKinoCasino (UCt26i2K9JN7cPtEX5qjdwIw) on 11-04; the
  old @TheKinoCasinoIncognito pulled its 11/4 copy. `clipper.toml` points there since 11-06 (backup in
  backups/). That feed's RSS 404s from the NAS, so the check reads yt-dlp's undated listing: entries
  below a grabbed pre-show count as older (#162). After a channel switch, grab the latest by hand.
- A cut killed half way can leave `clipper-cut-<id>` running on the NAS: `ssh nas docker rm -f clipper-cut-<id>`.
- `uv sync` can't replace the running clipper.exe while the bot is up (`uv pip install` instead, and
  include `--extra modal` later). yt-dlp needs deno for YouTube.
- The worker runs the working tree: uncommitted edits in `clipper/` are live for the next stream.
- Modal per-run cost estimates overcount parallel archive runs; trust the billing line in `clipper modal`.

## Log index (docs/log/2026-10.md, by first date)
2026-10-18 Last run (profanity recut) · Multi-show · Next projects (worker, Shorts, retakes, doctor, war room, analytics: all built)
2026-10-19 Backlog push · Thumbnails 10/10-10/15 · Pre-shows · Channel 404 News
2026-10-20 Cover photos · Upload test · Roadmap: NAS hub · Automatic uploads · Vertical Short covers · To do next ·
           First hands-off night · Shorts front-loaded · Scene-cut layouts · Hosts-only layout · Worker fast lane
2026-10-21 TikTok drafts · Roadmap: YouTube Posts magazine · Hot-take Shorts, no clips · kick-132733064 clock fix
2026-10-22 Baring → Bearing · Jerry Banfield archive (and Modal, archive, Udder Truth, Mother Moo, Runway) · Hand picks · Hashtags v2
2026-10-23 Whale-fall night (H3) · Bug sweep · War room chips/keyboard · War room on the phone · Tailscale · Discord DMs ·
           War room quick wins · Source links
2026-10-24 Weekly issue 02 · Sloth Beat · Modal monitoring
2026-10-25 Receipts for Don · Roadmap: transcript-framed edits · Speaker labels
2026-10-26 Cut on the NAS
2026-10-27 Issue day (jsonstate, llm.py, costs, war room) · TikTok resend · One-off: Just Trish
2026-10-28 Issue day 2 (analytics, audit follow-ups, cover editor, schedule DnD) · Related videos and Content ID check
2026-10-29 Face out from under the inset · Audit phases 1-3 · Stay current instead of a backlog · War room layouts
2026-10-30 YouTube sign-in expired · Audit finished and merged; pull, restart, recorder redeploy, cast ·
           #77 finished (#105 reverted, #106 after Copilot's review)
2026-10-31 Halloween pre-show (stale cookies)
