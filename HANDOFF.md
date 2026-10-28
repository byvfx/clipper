# Clipper handoff (current state, rewritten 2026-10-28)

This file is the **current state** between sessions: what runs where, what the user decided,
what is waiting on them, open work, recipes, gotchas. Keep it short (under ~300 lines): update
the sections in place, don't append diaries here. The session-by-session diary is
**`docs/log/2026-10.md`** (one file per month; grep it, don't read it whole). At the end of a
session: fix what changed here, then add a dated entry at the end of the log.

Stream clipper for Kino Casino (Kick channel `kinocasinogaming`) and Channel 404 News → YouTube
channel **Lolcow Cinema**. Repo `C:\clipper`, remote https://github.com/byvfx/clipper (private).
Python package `clipper/`, config `clipper.toml` (gitignored; `clipper.example.toml` is tracked),
prompts `prompts/`. README.md says what it is, SETUP.md how to install it, CLAUDE.md the conventions.
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
`schedule` → `upload` (YouTube). Discord bot runs always-on (`clipper bot`, scheduled task `clipper-bot`).

Around it (each a subcommand; details in README and the log): `record` (NAS Docker recorder),
`worker` (GPU machine: fast lane clips/Shorts first, then segments), `shorts` (vertical, scene-aware
layouts, 15-59 s), `retake` (Kitsu notes → re-cut/re-cover automatically), `preshow` (Kino's YouTube
pre-show whole; also one-offs with `--from/--to/--about`), `pick` (Shorts from a stretch you name),
`weekly` (ten-page tabloid issue, Saturdays), `receipts` (old clips of an archive subject for a
stream's stories, Mother Moo voice-over), `archive` (back catalogs on Modal: Jer complete, FTS index,
`ask`), `speakers` (diarization names), `narrate` / `perform` (Mother Moo: ElevenLabs read, lip-sync
pieces, stitch), `tiktok`, `rumble`, `analytics`, `costs`, `modal`, `alert`, `warroom`, `doctor`.

## User decisions (keep)
- Titles/descriptions/cut points approved as-is; fun descriptions are liked.
- Long video per recurring topic (compilations, e.g. "The Quartering" 3h13m) plus segments plus short picks.
- `third_party_audio = "allow_reactions"`, `audience_knowledge = "fans"`, Victor/blackjack segments stay.
- Kitsu is the record for review/edits (no separate DB). Discord approvals must keep working.
- Thumbnails: `[thumbnail] style = "enquirer"` (1990s tabloid: KINO CASINO masthead, no "THE NEW" roundel,
  no barcode, small "AS SEEN ON LOLCOW CINEMA" badge). Prompt `thumbnail_enquirer_v3` (face = headline
  subject; may use `[thumbnail] photos` portraits for people not on screen). Pre-show covers are one
  story, no side boxes (`[preshow] teasers = false`, user 2026-10-28: "single issue for preshows").
- Never use the word "hosts" in viewer-facing text (was "rarely" until 2026-10-20) → `[scoring] house_style` rules, appended to all viewer-facing prompts.
  Put future style notes there. Co-hosts are **Andy (Warski)** and **PPP**. Never call PPP by any other name (user,
  2026-10-19; Kick bio says "Andy Warski and PPP"). Who-looks-like-what is in [shows.kino.scoring] stream_context.
  Older drafts/Kitsu titles that say "Ashton" stay as they are (user, 2026-10-19); PPP from now on.
- Bleep: `[bleep] mode = "bleep"`, `profanity = true` (all forms of "fuck"), slurs always. Captions/titles masked;
  `youtube.metadata()` masks again at upload (#8).
- Shorts: 15-59 s hot takes, 3 slots a day (10/15/20); horizontal clips are cut and drafted but never
  scheduled (user, 2026-10-21). A Short switches layout at each scene cut (Haiku + reference frames).
- Uploads: automatic since 2026-10-20 (bot: `clipper schedule` then `clipper upload` hourly), private with
  publishAt, 15 a day hourly 08:00-22:00, `[youtube] max_per_24h` 15 (channel allows 100; user leaves room),
  shows take turns (`mix_shows`). `manual_publish = false`: **"asap" items (pre-shows, one-offs) publish
  public the moment they upload.** Every upload also joins its show's playlist. Source links are added
  at upload (clip → segment `?t=`, segment → compilation) and backfilled hourly.
- TikTok: **auto-send off since 2026-10-28** (TikTok flagged bot activity); the user posts by hand. Don't
  send drafts unless asked (`clipper tiktok --items ... [--resend]`).
- Transcription on Modal (L40S, ~$0.34 per 8 h) since 2026-10-23; Kino streams are diarized
  (`[shows.kino.transcribe] diarize = true`, ~$0.30 more) and the scoring v5 / segments v3 / meta v3
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
- Model calls are logged to `logs/costs.jsonl` (`clipper costs`); say what a rerun costs before doing it.
- The taste layer is the asset (prompts, clipper.toml show tables, house_style, Kitsu): see "Back up".

## State now (2026-10-28)
- **Running on this laptop** as scheduled tasks: `clipper-bot` (Discord cards, hourly replan + upload,
  pre-show polls every 5 min, retakes, DM alerts, war room on :8765 and https://thor.taile415cd.ts.net/)
  and `clipper-worker` (runs each finished NAS recording). Restart the bot after scheduler/war-room Python
  changes (see Gotchas); stages run as fresh processes, so most other changes need no restart.
- **NAS** (192.168.0.155): recorder container (`deploy/record`, redeployed 2026-10-28 11:05 at 8060532,
  watching kinocasinogaming + donflurgundy), Kitsu :8090, the streams share, photos share, cut container.
- **Shows:** kino and c404 live through review → schedule; dsp and blade are config-only tests; `trish` is a
  placeholder show for the 2026-10-27 one-off (public https://youtu.be/Mh6JcTACFlI).
- **Schedule:** full 14 days out (207/207 slots on 10-28), backlog mostly caught up. Upload cap 15/day.
- **Archive:** Jer (The Quartering) 11,571 items / 2,732 h transcribed on Modal, FTS index built,
  `archive ask` works; link checks built but not yet run against the real services. Other archive
  projects (#44 Jerry Banfield, #45 Papa Meat, #47 Ben Avery) wait on the desktop install and permissions.
- **Receipts for Don:** first video private at https://youtu.be/q6gemx2kKMs (2026-10-26); sending it is the
  user's call. On-screen source links would need a re-cut.
- **Weekly:** issue 02 posted 2026-10-24; issue 03 is Saturday 2026-10-31 (`clipper weekly --headline "..."`).
- **The Udder Truth:** storyboard complete, voice and pipeline chosen; next is the newsroom set (Brass Eye
  refs in mockups/udder_truth/refs/, blank-screen set for Fusion), new desk plates, then Moo's WAN renders
  (~1,550 credits; 18,457 Runway credits left on 10-24).
- **Costs:** October ~$200 in model/GPU spend, ~$184 of it the Jer archive; `clipper costs`, war room
  Costs and Modal panels. Monthly analytics check-in task runs 2026-11-01 09:00 (#42).
- **Analytics:** YouTube Analytics API enabled 10-28; October so far 12.7k views, 68.5 h watch, +28 subs,
  Shorts feed 80% of views.
- **Compilation joins fixed 2026-10-28** (user spotted Andy "repeating himself" at 2:18:42 of the H3
  compilation): each segment file starts on the keyframe before its start, so back-to-back segments
  replayed 1-5 s of the previous one at every join (13 of 15 in the H3 video, also Quartering/DSP/Angry
  Joe). `cut_compilations` now ends each part with a concat `outpoint` where the next file begins.
  Only new compilations get it; the user chose not to re-cut or re-upload the ones already out.

## Waiting on the user
- TikTok drafts to post by hand: last night's four resent 10-27; the 10/25 batch (kick-133940023 v04_t04,
  v05_t05, v07_t14, v08_t18) is still unposted and would need `--resend` once there is room.
- Re-approve in Kitsu (re-rendered revisions, pending): several Shorts after the scene-cut and
  hosts-only re-renders, the kick-132733064 clock-fix republish, kick-131151926's recut items.
  Approved-and-scheduled Shorts that a scene re-render would change (v06_w11, v03_w06, v03_t23) are
  left alone until the user says.
- Review today's Kino pre-show (yt-hB61VKYaOQ0, 10/28) in Kitsu/Discord, rev 3 (whole video, original
  text, single-story cover "ANDY STUNNED BY H3!"); it publishes asap once approved. Rev 2 was the
  retake reading "single issue" as one topic (cut to 63 s); undone by hand with retake.apply.
  (Stuck 12:53-15:00 on stale cookies; fresh export copied over secrets/youtube_cookies.txt, old one in
  youtube_cookies.before_2026-10-28.txt. A new export saved under another name isn't read: copy it over.)
- Content ID: all five pre-show/one-off uploads checked in Studio 2026-10-28, no claims, no region
  blocks. Keep checking new ones (Studio → video → Copyright; the Data API only sees region blocks).
- Shorts "related video" (Studio only, the API can't): set on all 19 uploaded Shorts whose segment was
  public, 2026-10-28 (marked `related_video` in schedule.json). `clipper upload --related` lists what's
  due and the 09:00 digest carries the same list; after setting them, `clipper upload --related <keys or
  links>` records them. Still to do as their segments go up:
  kick-133423456 v01_w10 (10-29) and v04_t28 (10-30), kick-132380636 v01_t29 (11-02), kick-132733064
  v01_t27 (11-04). kick-133423456 v02_t12 and kick-133940023 v14_t35 fall outside every segment: pick one
  by hand or leave them. Verify whether `thumbnails.set` works on Shorts before turning on
  `[youtube] short_thumbnails`.
- Old approved Short titles/descriptions that still say "hosts" (Kitsu text; uploads go as written).
  A war-room find-and-replace dry run found 14; not applied.
- Decisions on #66 (transcript-framed edits): which item kinds, replace or sit beside the plain cut,
  sound effects, order after #67. #27 TikTok content rules. Sloth Beat handle check (@slothbeat).
- Papa Meat permission (#45), Ben Avery archive pull (#47), Jerry Banfield pull waits on desktop/storage (#44).
- Recorder warnings arrive twice (webhook channel + DM) until the webhook in deploy/record/env.local is dropped.
- Issue 03 of the weekly on Saturday; the monthly check-in report on 11-01.

## Open work
Tracked as GitHub issues (byvfx/clipper). Still open from the audit: #77 (M12 quota tracking, L6, L10,
L11, L13, L16-L18, repo hygiene), #72 (provider side-by-side, `clipper edit`), #50 (revenue once
monetized), #42 (closes after the first check-in). Roadmaps, issues only: NAS hub #14 (#9 war room on
the NAS, #12 worker/bot on the NAS, #64 single-owner daemon `clipper serve`, #65 Claude Code on the NAS),
transcript-framed edits #66 (#67-#71), YouTube Posts magazine #30 (#32-#38), cast library #41,
lore-order playlists #43, archive fun #63, Udder Truth #58/#59/#60, Modal ideas #53-#56.
Older notes not on an issue: republish the 2026-10-19 re-cut streams to Kitsu (user said not yet);
Blade compilation's Discord card never posted (`review notify kick-131151926 --keys comp-blade-in-vegas`);
a daily "post kit" folder; prove SETUP.md on a clean machine (desktop or MacBook `[mac]` extra);
thumbs: reject a picked frame with no face, `--frame-at <s>`; Discord excerpts for one-offs could
start after the intro; pick/preshow/retake prompts don't read speaker tags yet.

## Recipes (how we did it last time; details in the log)
- **Restart the bot:** `deploy\discord\bot-task.ps1 -Restart` (stopping the task alone leaves the
  supervisor's process alive). Worker: `deploy\worker\worker-task.ps1 -Status|-Restart`.
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
- **Checks:** `clipper doctor --role gpu --offline`, `uvx ruff check clipper`, test stream `kick-131151926`.

## Back up the taste layer (user, 2026-10-24)
The Python is replaceable; the months of editorial judgement are in prompts and config. Treat them
like footage. Nothing automates this yet (a `clipper backup` or a NAS cron is an open idea, not built).
- **In git (push it):** `prompts/` (every version, never edit one that produced reviewed output),
  `clipper.example.toml`, `mockups/`, `research_notes/`, `reports/`, `streamers.json`, this file.
- **Not in git, copy by hand or script:** `clipper.toml` and the `clipper.before_*.toml` backups
  (show tables, house_style, stream_context, scoring rules), each `streams/<id>/config.toml`
  (per-stream overrides; lost with the stream folder), `secrets/` (YouTube/TikTok tokens are a
  re-consent to recreate), the photos share `//192.168.0.155/docker/clipper/photos`, `layouts/`
  (Shorts layout references per show), `schedule.json` and its `schedule.before_*.json` backups,
  `deploy/*/env.local`, and the Kitsu volumes on the NAS (every review decision and note).
- Also the project memory `~/.claude/projects/C--clipper/memory/` on the laptop: it holds decisions
  that never made it into HANDOFF.

## Gotchas learned
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
- `schtasks /End /TN clipper-bot` leaves the supervisor's bot process alive (lock in streams/.bot.lock);
  to restart the bot, also stop the old bot-supervisor.ps1 powershell and its clipper.exe children.
- schedule: a compilation approved as its own Kitsu item needs segment_keys (fixed 2026-10-18).
- The bot runs `schedule.run` in-process (replan after a decision): scheduler changes need a bot restart.
- Retake planner can't edit cover layout; a note about the cover's boxes comes back "unsupported",
  or worse, gets read as an edit it can make ("single issue" became a 63 s trim, 10-28). A layout
  rule belongs in config; undo a bad retake with `retake.apply` + `publish_retake` and a hand plan.
- A recording with dropped HLS segments used to shift the transcript clock (kick-132733064, 46.9 s);
  `extract_audio` now fills gaps with silence. Check new recorders' gaps against the source length.
- Discord review cards carry only the first 60 s of a segment; the full preview is in Kitsu. A reject
  from the card can be about the excerpt, not the cut.
- YouTube bot-checks this IP for yt-dlp; pre-show/one-off grabs retry signed in with
  secrets/youtube_cookies.txt, which browsers rotate: re-export when "cookies are no longer valid".
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
