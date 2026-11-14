# Clippa handoff (current state, rewritten 2026-11-08)

This file is the **current state** between sessions: what runs where, what the user decided,
what is waiting on them, open work, recipes, gotchas. Keep it short (under ~300 lines): update
the sections in place, don't append diaries here. The session-by-session diary is
**`docs/log/2026-11.md`** (one file per month, October in 2026-10.md; `grep "^## " docs/log/2026-1*.md`
lists every entry by date; grep the log, don't read it whole). At the end of a session: fix what
changed here, then add a dated entry at the end of the log.

Stream clipper for Kino Casino (Kick channel `kinocasinogaming`), Channel 404 News and ChudLogic → YouTube
channel **Lolcow Cinema**. Repo `C:\clipper`, remote https://github.com/byvfx/clipper (public, on purpose:
the user said so 2026-11-10; every tracked file, issue and PR comment is readable by anyone).
Python package `clipper/` (`clipper/defaults.toml` is every setting, documented; prompts in `clipper/prompts/`),
config `clipper.toml` (gitignored, on the NAS; `clipper.example.toml` is a tracked starter). The user docs are
the GitHub wiki, built from `docs/wiki/` (`.github/workflows/wiki.yml` pushes it on every push to main);
README.md and SETUP.md are short pointers; CLAUDE.md the conventions; `reports/` the audits.
Project memory (`~/.claude/projects/C--clipper/memory/`, MEMORY.md first) holds durable preferences.
- Plan doc: https://claude.ai/code/artifact/a9bb9e38-ced0-4455-8d8e-18a78122f45f
- Walkthrough (usage, for presenting; Claude Docs, 2026-10-27): https://claude.ai/code/artifact/0d0e04da-54f6-4f15-bad0-2df72a286383
- Case study (2026-11-11, refreshed 11-13): https://byvfx.github.io/clippa/ (`clippa/index.html` in
  byvfx/byvfx.github.io, Pages from main). Its source is the private artifact "Clippa Overview"
  (https://claude.ai/artifact/HTF3noAawBG1M1JNYsLAMS): edit that, then splice the content from `<div class="figures">`
  to `<footer` into the site page, which keeps its own head, nav and footer links. The README's hero
  (`docs/readme/hero-*.png`, #237) is a screenshot of its header; the figures (347 videos, 799 tests) are dated
  11-13 and go stale: the 11-11 log entry has the re-render recipe. The page has no show names; its documentary
  case study names no subject, channel or title, and should stay that way.
- Interactive demo (HTML, numbers as of 2026-10-27): https://claude.ai/artifact/ASL1grYiajRAELyoqTGitN
- Keys are Windows **User** env vars (ANTHROPIC_API_KEY, KITSU_TOKEN, ...); agent shells must load them:
  `export KITSU_TOKEN="$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('KITSU_TOKEN','User')" | tr -d '\r')"`

## Pipeline (one stream = `streams/<id>/`, test stream `kick-131151926`)
`clipper fetch <kick url>` → `signals` (chat/loudness heat) → `transcribe` (WhisperX on Modal, diarized) →
`score` (Haiku Pass A + transcript scan, Sonnet Pass B) → `segments` (Opus topic split + compilations) →
`cut` (segments lossless w/ keyframe starts, compilations joined, picks re-encoded; slurs+"fuck" bleeped) →
`meta` (SEO title/description/chapters/tags + .srt) → `thumbs` (Enquirer tabloid covers) →
`review publish` (Kitsu on NAS 192.168.0.155:8090 + Discord cards) → `review sync` / Discord bot →
`schedule` → `upload` (YouTube). Always on, as NAS containers since 11-05: `clipper serve` (the war room, hourly
replan + upload, pre-show grabs, retakes, daily jobs), the Discord bot (`clipper bot`) and the worker.

Around it (each a subcommand; details in the wiki pages and the log): `record` (NAS Docker recorder),
`worker` (each finished recording: fast lane clips/Shorts first, then faces, cast, segments), `shorts`
(vertical, scene-aware layouts, 15-59 s), `retake` (Kitsu notes → re-cut/re-cover automatically), `preshow`
(Kino's YouTube pre-show whole; also one-offs with `--from/--to/--about`), `pick` (Shorts from a stretch you
name), `weekly` (ten-page tabloid issue, Saturdays), `receipts` (old clips of an archive subject for a
stream's stories, Mother Moo voice-over), `archive` (back catalogs on Modal: Jer complete, FTS index, `ask`),
`investigate` (an archive subject's own videos → verified facts → leads + coverage check), `story` (a brief →
the swarm, `clipper/swarm.py`: planner → Haiku workers over the web, YouTube, Kick, our streams → dossier →
narrated catch-up cut), `people`, `faces`, `cast`, `speakers`, `narrate` / `perform` (Mother Moo), `tiktok`, `x`,
`rumble`, `analytics`, `costs`, `compare` / `models` (other providers), `status`, `services`, `alert`, `doctor`.

## User decisions (keep)
- The NAS hub's YouTube cookies come from the laptop (11-09, user: "lets just setup that task but note it that it
  prevents full offline autonomous usage"): the `clipper-cookies` task runs `clipper cookies export` every 4 h.
  The NAS isn't laptop-free for signed-in yt-dlp retries: with the laptop off for days, pre-show grabs get stuck.
- The app is **Clippa** (2026-11-09, branding only): war room, Discord status card, wiki Home/sidebar/banner,
  README. The `clipper` command, package, `CLIPPER_*` env, config files, containers, Modal apps, the
  `clipper:` button ids and the `[clipper]` Kitsu note prefix keep the old name on purpose (old cards and
  notes depend on the last two). A full rename needs both prefixes accepted and the old compose project stopped first.
- Titles/descriptions/cut points approved as-is; fun descriptions are liked. Long video per recurring topic
  (compilations) plus segments plus short picks. `third_party_audio = "allow_reactions"`,
  `audience_knowledge = "fans"`, Victor/blackjack segments stay; Kino's gambling is commentary, not play
  (`[scoring] gambling = "commentary"` for kino and c404, prompt v7, 11-05).
- Never use the word "hosts" in viewer-facing text → `[scoring] house_style` rules, appended to every
  viewer-facing prompt; put future style notes there. The cast is **Andy (Warski)** and **PPP** (never any other
  name for PPP; old drafts that say "Ashton" stay). Channel 404 = Don, a solo show. Cast per show in `[meta] cast`.
- Thumbnails: `[thumbnail] style = "enquirer"` (1990s tabloid: show masthead, no roundel, no barcode, small
  "AS SEEN ON LOLCOW CINEMA" badge), `enquirer_prompt_version = "v6"` (face = headline subject, cast photos
  picked by label, faces.json names trusted). Pre-show covers are one story, no side boxes (`[preshow]
  teasers = false`). The main face must never sit under the reaction inset or badge (`face_crop(avoid=)`).
- Bleep: `[bleep] mode = "bleep"`, `profanity = true` (all forms of "fuck"), slurs always. Captions/titles
  masked; `youtube.metadata()` masks again at upload.
- Shorts: 15-59 s hot takes; horizontal clips are cut and drafted but never scheduled (2026-10-21). A Short
  switches layout at each scene cut. Shorts over `max_source_audio_s` of other voices arrive **held** in review
  with the seconds on the card (`[shorts] hold_source_audio`, 11-04); release when the show carries it.
  Stories retry YouTube's bot check signed in, like pre-shows (`[story] youtube_bot_retry = true`, 11-03).
- Kitsu is the record for review/edits (no separate DB; Kitsu-optional is not a goal). Discord approvals must
  keep working: the war room is the review surface, cards approve from the phone.
- Uploads: automatic since 2026-10-20 (serve: `clipper schedule` then `clipper upload` hourly), private with
  publishAt, `[youtube] max_per_24h` 15 (channel allows 100; the user leaves room), shows take turns
  (`mix_shows`). `manual_publish = false`: **"asap" items (pre-shows, one-offs, stories) publish public the
  moment they upload.** Every upload joins its show's playlist; source links are added at upload and
  backfilled hourly. Stay current, no backlog (10-29): `stale_days = 3`, items let go stay approved in Kitsu
  (war room filter "Not going out"; pin with "Publish at" to send one anyway).
- X (@lolcowcinema): `clipper x` posts Shorts as native video, never a link (X bills $0.20 a link post).
  **Auto on since 2026-11-01** (`[x] auto_post_minutes = 60`, `max_per_24h` 6, weekly thread posts count),
  hook first + up to 3 hashtags ending with the show's, a last line crediting the footage's owners
  (`[shows.<key>.x] mentions`, 11-03). `clipper x --issue` posts the weekly as a thread after the user OKs
  the text. The app browser pane is signed in to X.
- TikTok: **parked (2026-11-01)**; the user posts by hand, never direct posting (#62, #116 closed). Don't send
  drafts unless asked (`clipper tiktok --items ... [--resend]`).
- Transcription on Modal (L40S, ~$0.34 per 8 h); Kino and Channel 404 diarized (~$0.30 more; Don enrolled
  11-04). Cut runs on the NAS (`[cut] backend = "nas"`). Whisper writes "Bearing" as "Baring": fix it.
- Weekly magazine (`clipper weekly`, Saturdays): cover full tabloid, inside pages modern, articles in the
  user's own voice (writer pass, `weekly_article_v1`); `clipper weekly --discord` puts pages + captions in
  Discord `#weekly`; the user posts TikTok carousel + two YouTube Posts by hand.
- Investigations (#108): leads go to Discord `#investigations`, a thread per `leads` run, one short message
  per lead edited in place, linking to the war room's Investigations window. Leave Jer's old videos alone
  (`investigate run jer` reads only new ones). Whether a lead may ever become content is open.
- Mother Moo (The Udder Truth, #58): British, voice "Harriet" (ElevenLabs), "perform to playback", WAN 3
  Prime on Runway for the picture, Sync for lip touch-ups, finish in DaVinci Resolve. Rupert is recurring and
  voiced by the user. Quality over cost. ElevenLabs reads are lossless WAV (pcm_48000).
- Second channel **Sloth Beat** (#46: Papa Meat, Ben Avery, On Cinema; teen-fan-magazine look). Runs from the
  desktop as the first clean install. Permissions open.
- Recorder alerts go to both the webhook channel and DMs (warnings only), twice on purpose (2026-10-28).
- Model calls are logged to `logs/costs.jsonl` (`clipper costs`); say what a rerun costs before doing it
  (`--dry-run` prices score/segments/meta/thumbs). The taste layer is the asset: see "Back up".
- Hand-over (2026-11-08 audits, `reports/code-audit-2026-11-08.md` §7): build as a public package but this
  deployment first, little tech debt, simple for the user; `studio/` holds mockups, research notes and media
  reports, out of the package; serve and the bot are compose-only, the Windows task scripts stay for a GPU
  worker only. Merge policy (11-10): a code PR merges once CI is green on both OSes and the review of its
  head commit is in and each finding is fixed or answered, then delete the branch; green CI alone isn't
  enough. Reviews since 11-13 (user; Grok's `review` workflow removed, ~25 min a PR was too slow): Claude
  runs the `code-review` skill locally before the first push and fixes what it finds, then Copilot reviews
  when the PR opens (repo ruleset "Copilot reviews new PRs", not on later pushes: Claude reviews those, or
  `gh pr edit N --add-reviewer @copilot`). Wiki Developing#code-review-on-prs. `clipper codereview` (Grok on
  OpenRouter, ~$1) stays for a by-hand second opinion. Code means a PR touching `clipper/`, `tests/`,
  `deploy/`, `.github/` or `pyproject.toml`: any other PR (the wiki, `uv.lock` alone,
  `clipper.example.toml`, `streamers.json`) has CI but no review, and a notes-only one (e.g. HANDOFF and the
  log) has neither since 11-10 (`check.yml` paths); both merge on the user's word, the first once CI is green.
  A fork's PR: read the diff by hand, then the user's word. The repo secret `OPENROUTER_API_KEY` is unused now.

## State now (2026-11-08)
- **Hub on the NAS** since 2026-11-05 (wiki NAS-hub): compose project `clipper-hub` in `~/clipper-hub`, containers
  `serve` (war room on 127.0.0.1:8765, on the tailnet as https://clipper.taile415cd.ts.net/; heartbeat
  `streams/.serve/status.json`), `bot` (`streams/.bot/status.json`) and `worker` (cuts with QSV on the iGPU).
  Code changes go out with `bash deploy/hub/deploy.sh` from a main checkout; `clipper services status|restart ...`
  runs docker compose there (`[serve] hub`). Last deploy 2026-11-13 13:51: main as of #239 (with #233). **The config and
  all state are on the NAS:** `//192.168.0.155/docker/clipper` (`/data`) holds `clipper.toml` (the one copy;
  `CLIPPER_CONFIG` on the laptop points at it), `schedule.json`, secrets/, backups/, logs/, faces/, voices/,
  news/, weekly/, archive/, layouts/, people.json, fonts/. The laptop's old copies: `backups/laptop-state-2026-11-05/`.
  The laptop's scheduled tasks are disabled, not removed (rollback: wiki NAS-hub).
- **NAS** (192.168.0.155) also runs the recorder container (`deploy/record`, redeployed 2026-10-30 at 1e4f724,
  watching kinocasinogaming + donflurgundy + chudlogic), Kitsu :8090, the cut container, the streams and photos
  shares. The `claude` container (`deploy/claude`, wiki Claude-on-the-NAS) drives clipper from the phone.
- **Mac `lofn`** (Apple Silicon, setup's `control` role, 11-07): a dev/control station that runs nothing
  unattended. Clone `~/clipper`; `CLIPPER_CONFIG=/Volumes/docker/clipper/clipper.toml` and
  `CLIPPER_LOCAL_CONFIG=~/clipper/clipper.local.toml` in `~/.zshrc` (mount the share in Finder first); ssh
  alias `nas` (user **`Brandon`**, capital B). `doctor --role control` clean; Homebrew ffmpeg has no libass.
- **Shows:** kino and c404 live through review → schedule; `chud` (ChudLogic, since 11-04: daily ~10 h from
  ~00:00-02:00 PT, budget segment 3 / short 5 per 8 h, 8-Bit Guy moments ranked up, cam panels `focus = "face"`
  since 11-07). His 10-18 VOD `kick-132975332` is fetched and transcribed only: its timestamps jump back
  821.7 s before 7:28, so don't `clipper run` it until fetch is fixed. dsp and blade are config-only tests;
  `trish` is a placeholder for the 2026-10-27 one-off; trish and blade are off the war room (`[warroom] hide_shows`).
- **Schedule** (October check-in, 11-02): `stream_budget = { segment = 10, short = 10, compilation = 0 }` per
  8 h, Channel 404 `segment = 6`; slots a day: 9 Shorts (09/11/13/15/16/18/19/20/21), 6 segments
  (08/10/12/14/17/22), compilation 23:00 for big-story nights only (`docs/big-story.toml` into the stream's
  config.toml). If good items keep going stale while streams overlap, try `stale_days = 4`.
- **Chud overnight test (11-12 to 11-20, user):** chud's segment budget is 0 since 11-10 (the user stopped
  recording him and gave his slots to Kino and Don), which left 71 approved segments out. All 71 are pinned in Kitsu hourly
  23:00-07:00, Fri 11-13 00:00 through Fri 11-20 07:00 (9 a night, no compilations); the hub uploads them 48 h
  ahead. For it, in `clipper.toml`: `[schedule] max_per_day` 24 (was 15), `[youtube] max_per_24h` 26 (was 15),
  `[shows.chud.youtube] captions = false` (quota: captions are 400 units, ~7 extra a day fit). **After 11-20**
  (the scheduled task `chud-overnight-wrapup` does this Fri 11-20 09:00 and reports; it stops if a pin isn't
  uploaded; the pin list is `backups/chud_overnight_pins_2026-11-12.json`): put both caps back to 15 unless the user keeps them, remove the chud youtube table, set
  `captions_error = "deferred"` on those 71 schedule.json entries (back it up) so the hourly backfill adds the
  captions from spare quota, restart serve. Backup: `backups/clipper.toml.before_chud_overnight_2026-11-12`.
- **Archive:** Jer (The Quartering) 11,571 items / 2,732 h transcribed on Modal, FTS index built, `archive ask`
  works; link checks built but not yet run against the real services. #44 Jerry Banfield, #45 Papa Meat and
  #47 Ben Avery wait on the desktop install and permissions.
- **Stories:** the user's catch-up format for any drama: `story new` → `story run` → `story plan` →
  `story cut --no-review` (never sent to review, so never uploads), a <30 MB 720p copy to the phone; narrated by
  Eric (`[story] voice_id`). First: kevin-langue-show-drama (Kino show, 5:28). Check lead-ins against the
  dossier (one stated a self-claimed role as fact). The DSP story (11-04) went to Kitsu for review instead.
- **Receipts for Don:** first video private at https://youtu.be/q6gemx2kKMs (2026-10-26); sending it is the
  user's call. On-screen source links would need a re-cut.
- **Weekly:** issue 03 posted (X thread 11-01). Issue 04 (Nov 1-7, Jer's midterm meltdown cover, the user's
  pick) built 2026-11-08 in `weekly/2026-11-07/`, $1.11; Jer and PPP faces pinned by hand in issue.json (the
  auto picks were a Jer-movie actor and a costume torso: the faces stage names movie and costume look-alikes
  as the cast, worth a look before issue 05); in Discord `#weekly`. Not posted yet; the X thread text is in
  issue.json `post.x` (OK'd 11-08), held by the X cap: a one-time scheduled task (`clipper-weekly-04-x-thread`)
  posts it 11-09 11:42 when 3 slots free, unless the hourly auto job takes them. Issue 05 is Saturday
  2026-11-14 (on a Sunday add `--week <Saturday>`).
- **The Udder Truth:** storyboard complete, voice and pipeline chosen; next the newsroom set (Brass Eye refs in
  studio/mockups/udder_truth/refs/), new desk plates, then Moo's WAN renders (~1,550 Runway credits).
- **Costs and analytics:** October ~$200 in model/GPU spend, ~$184 of it the Jer archive; Haiku scoring
  $0.67 in Oct-Nov, the spend is segments, thumbs, meta. `clipper costs`, war room Costs, `--providers`
  (Claude's own count needs `ANTHROPIC_ADMIN_KEY`, not set). Monthly analytics check-in task on the 1st at
  09:00; October's (`reports/analytics/2026-10-checkin.md`): 1-4 taken, 5 skipped, its Decisions section is what
  November's follows up. October: 12.7k views, 68.5 h watch, +28 subs, Shorts 80% of views.
- **Model providers (#72, closed 11-09; its edit-plan proof moved to #67):** the baseline stays Haiku + Sonnet. Tools: `clipper compare score <stream> --model
  openrouter:<vendor>/<model> --dry-run`, `clipper models test <model>` (conformance), `clipper models gold
  propose|confirm|run` (the user's 66-window answer key, `logs/model_tests/gold.json`); OpenRouter key in
  secrets/openrouter_secret.txt, no OpenAI/Gemini key, no Ollama. Findings (wiki Model-providers, log 11-04/05):
  no model near the baseline; on the gold set v7 Haiku 39/66, DeepSeek V4 Pro 48/66 (27 cents a run, no image
  input), Qwen 3.8 Flash 40/66; Haiku over-calls needs_context, every model keeps 5-11 of the 21 non-keeps.
  Moving a stage (e.g. Pass A to DeepSeek V4 Pro) is a config change whenever the user wants it.

## Waiting on the user
- **`clipper streams prune`** stays by hand until you set `[streams] keep_source_days` in clipper.toml
  (111 GB would go at 30 days). The test stream is in `[streams] never_prune` (set 11-13).
- `secrets/youtube_cookies.netscape.txt` on the share is unused since PR #195 and can be deleted.
- Close Fusion and merge the `mockups/udder_truth/` leftovers into `studio/mockups/` (ignored by `/mockups/`).
- Branches and worktrees were cleaned up again 11-13: everything merged is gone, plus the old `ccr-*` and
  `claude/clipper-investigate-command-*` branches and the swarm worktree, all at the user's word. Kept on
  purpose: `story-dig` and `jev-lab` (`C:\clipper-jevlab`; it holds `jev-gate`'s commit), both pushed and
  unmerged. `.claude/worktrees/instagram` (`archive/instagram-source`) holds **uncommitted** Instagram-source
  work (`clipper/instagram.py`, its test, 12 files changed): the only copy, so commit it (then push the
  branch) before anything else. `.claude/worktrees/elated-snyder-52f189` is clean (detached on main) and can go once no
  session uses it.
- The DSP story video is in Kitsu for review (Kino Casino / "2026-11-04 story-dsp-credit-score-590", 3:14).
- TikTok drafts to post by hand: the 10/25 batch (kick-133940023 v04_t04, v05_t05, v07_t14, v08_t18) would
  need `--resend` once there is room.
- Approved-and-scheduled Shorts that a scene re-render would change (v06_w11, v03_w06, v03_t23) are left
  alone until the user says. The four Shorts from kick-136292920 went up with Chud's old cam crop; they stay
  that way unless deleted and re-uploaded.
- Content ID: check new pre-show/one-off uploads in Studio (video → Copyright; the API only sees region blocks).
- Shorts "related video" (Studio only): a weekly scheduled Claude task sets them Sundays 10:00 while the app
  is open (`clipper-weekly-related-videos`; 11-08 run: 7 set, 0 left due). `clipper upload --related` lists
  what's due and records what you set; six Shorts match no scheduled segment (pick one by hand or leave them).
  Verify whether `thumbnails.set` works on Shorts before turning on `[youtube] short_thumbnails`.
- Old approved Short titles/descriptions that still say "hosts" (Kitsu text): a war-room find-and-replace dry
  run found 14; not applied.
- Decisions on #66 (transcript-framed edits): which item kinds, replace or sit beside the plain cut, sound
  effects, order after #67. Sloth Beat handle check (@slothbeat).
- Cast (#41): it fills itself; look over Kept and New in the war room's Cast window (drop wrong or costume
  photos; image search photos always wait for you). Check Jer f868aa21db (auto-kept at 0.73 from a costume
  night). #41 closes once a cover and a weekly issue use kept cast photos (weekly 05,
  11-14, is the test). Candice Owens has the alias "Candace Owens" (Commons' spelling, 11-13).
- Faces: enroll PPP in his maple-leaf cap from the first non-costume stream (October was costumes every night).
- Permissions: Papa Meat (#45), MikeyNohMore (#80). Ben Avery archive pull (#47); Jerry Banfield pull waits on
  desktop/storage (#44).
- `clipper setup` (#78) phase 1 passed in Windows Sandbox (2026-10-28). Next: the real install on the desktop
  (#46; clone into a short folder like `C:\clipper`, run `deploy\bootstrap.ps1`), then the MacBook as #78's join-mode test (#7 closed into #78, 11-13).
- Weekly issue 04: post it by hand from Discord `#weekly`; the X thread goes out by the scheduled task
  (State now). The bot has full permissions since 11-02 (could be cut back to Send Messages + Attach Files).
- `ANTHROPIC_ADMIN_KEY` for Claude's own usage count on Costs, if wanted.

## Open work
**Priority labels (user, 2026-11-13):** every open issue carries one: `p1-now` (built 11-13, one PR each, all merged
and deployed 11-13: #199 PR #247, #204 PR #248, #228 PR #249,
#205 PR #250; #201, #203 and #206 closed; 11-13 also closed #7 into #78 and #57 as built), `p2-dated` (#192 after 11-16,
#41 after weekly 05), `p3-track` (one big track at a time, the user picks: desktop install #46 → #44/#47/#45/#78;
Udder Truth #58-#60; archive tooling #221-#227 before the next mini-doc; b-roll #229), `waiting` (#80, #48, #45),
`parked` (the rest). `gh issue list --label p1-now` is the queue. 11-13: #13 folded into #158, #200 closed (its
setup/doctor bug moved to #78), #70 narrowed to punch-ins/emphasis (tighten built).
Tracked as GitHub issues (byvfx/clipper); roadmaps are issues only: (NAS hub #14 and self-checks #185
closed 11-09; checkup's turn-on steps are under Checkup below),
Posts blind items #36 (the rest of the YouTube Posts roadmap #30 closed 11-09), Instagram Reels #39, cast library #41, lore-order playlists #43,
archive fun #63, transcript-framed edits #66 (#67-#71; 11-09 the user wasn't sure #67 is needed, so #70's tighten
came first: built 11-09, PR #191, `[edit] tighten`. The user found it "better than I thought" on the
test stream but wants it **off** until it clearly helps; turn it on per show (`[shows.<key>.edit]`)
only at their word. #67's model edit plan waits until edited Shorts prove themselves), crisis mode #73, setup #78,
live clips #81, blind items #111, investigate anyone #138, agent exploration #158 (on hold at the user's word 11-09; next would be a week of phone use, then
a small `clipper mcp`; #13 may fold into it), Modal ideas #53, #55, #56,
Udder Truth #58/#59/#60, P&L #50 (revenue once monetized). Write "#77 L6" rather than "fix #77" in commit
messages: GitHub closes the issue on "fix".
- **Documentaries (11-13, PR #233, merged and deployed):** `clipper doc
  new|voice|build|master|leak|kit` (wiki Documentaries; `clipper/doc.py`, `doc_resolve.py`), the reusable form
  of the first mini-documentary's one-off Resolve scripts. A piece is `[paths] documentaries/<slug>/` on the
  share (plan.toml, banned.txt, media), never the repo. Tried: take processing, word timing, the take
  split and mastering on real files; `build`, the render and `master` against live Resolve 21.2 with a
  synthetic piece (its Resolve project is deleted; the folder `C:\clipper-doc-test` on the laptop is the
  user's to delete). Not tried, because they cost: `voice` against ElevenLabs, `leak` on Modal; `--dry-run`
  first: `voice` prints the characters it would buy (not a price: work it out from the ElevenLabs plan),
  `leak` prints a dollar estimate. Reels at another frame rate than the timeline are untried too. On the
  first real piece, listen to every take and watch the first built timeline. `[story] lufs = -14` is new: a story cut before it is
  rendered again, and its cover picked again, on its next cut.
- **B-roll library #229 (11-12, steps 1-2 of 4, PR #230):** `clipper broll find|add|list|keep|drop`
  (wiki B-roll): NASA, DVIDS (the B-Roll category; keys in `secrets/dvids_secret.txt`, public key first,
  added by the user 11-12), Prelinger and Commons; public domain / CC0 only, no sound by default (NASA
  music). Next: the National Archives (key by email from NARA), `[story] broll` in story cuts, then a war
  room window. The library is `[paths] broll` on the share.
- **Usability review (11-10):** built and merged 11-13: `clipper status` with "needs you" first, 60 s to 12 s
  on the share (#199, PR #247); `clipper streams list|prune` and `clipper backup`, both also serve jobs (#204,
  PR #248); Modal per-call overhead in the archive/story estimates (#228, PR #249); CLI polish: `latest`,
  `latest:<show>` and id prefixes, `--json` on the read commands, `config diff`, `completion bash|powershell`
  (#205, PR #250; `--quiet`/`--verbose` left out on purpose). War room #201-#203 and #206 closed (#206's
  closing comment lists the three items left on purpose); #200 closed. Still open: new users and the public
  repo #207 (this install's values in tracked files, packaging).
- **Checkup (#185 closed 11-09, the rest is tracked here):** `clipper checkup` (services, streams, schedule,
  lint; `[checkup]`; wiki Checkup) and `clipper alert send` are merged (#189). The nightly run
  (`deploy/claude/checkup.sh`, prompt `checkup.md`, both baked into the image) is not on yet. Left:
  1. The user puts `CHECKUP_AT=08:30` in `deploy/claude/env.local` and redeploys the claude container.
  2. First run on the NAS: check that headless `claude -p` runs on the Remote Control sign-in, and that the
     `--allowedTools` rules work (`checkup.sh --dry-run` first).
  3. A week of nightly reports, then decide on hourly runs and PRs (#158's "act within limits").
- **Bootstrap follow-up (11-07):** `deploy/bootstrap.sh` installs Homebrew `ffmpeg`, which has no `subtitles`
  (libass) filter, so a Mac worker fails Short captions; `ffmpeg-full` has it but is keg-only. Pick one:
  install `ffmpeg-full` and point clipper at it, or document it. `deploy/hub/deploy.sh` from `lofn` needs
  `deploy/hub/env.local` (it overwrites the NAS `.env`).
- **Investigate anyone (#138; part 1 `clipper people` merged 11-04, people.json is the record, 23 people
  imported).** Next, in the issue's order: speaker attribution (needs #53 embeddings, new facts prompt) →
  `investigate start` (swarm finds a person's channels and X handles, the user approves, a sample is
  transcribed) → X reads (check the pay-per-use read price first) → Obsidian vault → watchlist → public
  records (CourtListener token) → `investigate eval` on known stories (Huck, DSP) → Good/Noise buttons +
  `tune`. Also a review gate before a lead becomes content, Wayback snapshots, a never-read list (Kiwi Farms).
- **Story/swarm leftovers:** `investigate check` on the swarm (branch `investigate-swarm`, unmerged: the old
  four searches seed a small swarm per lead, then his own titles). YouTube downloads in a story hit the bot
  check from this IP: a fresh cookie export or the Firefox profile. Modal cold starts dominate a story's few
  items (`[story] parallel` 2).
- **Code audits:** 10-28 (maintainability) and 11-03 (correctness, `reports/code-audit-2026-11-03.md`) done
  and merged; still open from 11-03: #15 (example toml commented), #24/#25 (lock edges), #29-#35, #37, #41,
  #46-#50 (lows), #42's lock part. 11-08 (hand-over, `reports/code-audit-2026-11-08.md`): all 20 plan items
  done the same day (PRs #170-#182 and #184; each change is a CHANGELOG.md 11-08 bullet, the log has the PR
  list) and deployed 21:51. Left for later: `tests/test_modules_have_tests.py` lists 13 untested modules (the
  list may only shrink); 17 functions over McCabe 15 carry `# noqa: C901`, split when touched.
- **Weekly and monthly audit (11-10, wiki Audit):** `clipper audit` (no model) DMs how the week went and saves
  `streams/.audit/weekly-<date>.json`; on the 1st a read-only `claude -p` reads the saved weeks, traces repeat
  problems and leaves `deep-<YYYY-MM>.md`. The claude container runs both after the checkup (`audit.sh`), so it
  needs `bash deploy/claude/deploy.sh` once. Since PR #248 the backup settings are `[backup]` (folder
  `backups/taste`, every 7 days by serve), and the digest and doctor read the newest zip there. Dependabot opens grouped weekly PRs (ruff ignored; no review, no secrets). Its security
  alerts are a separate repo switch, off as of 11-10.
- **News watcher #110:** off since 2026-11-03 at the user's word (`[news] check_minutes = 0`, no terms); only
  `[news] watch` / `[shows.<key>.news] watch` terms are watched, `auto_watch = true` brings back the
  cast/figures/library list; `#news` exists in Discord.
- **Older notes not on an issue:** republish the 2026-10-19 re-cut streams to Kitsu (user said not yet); Blade
  compilation's Discord card never posted (`review notify kick-131151926 --keys comp-blade-in-vegas`); a daily
  "post kit" folder; thumbs: reject a picked frame with no face, `--frame-at <s>`; Discord excerpts for one-offs
  could start after the intro; pick/preshow/retake prompts don't read speaker tags yet.

## Recipes (how we did it last time; details in the log)
- **Restart serve / the bot / the worker:** `clipper services restart serve|bot|worker` (docker compose on the
  NAS over ssh); code changes: `bash deploy/hub/deploy.sh [serve|bot|worker]` from a main checkout (Git Bash).
  A serve restart or deploy kills a running upload: `clipper services status serve` lists its jobs; wait until
  none is running, and until no show is live if the worker is busy.
- **What is going on:** `clipper status` (serve's, the bot's and the recorder's heartbeats, the worker's
  streams, review counts, the next releases, this month's spend; `--sync` reads Kitsu first).
- **Before a model rerun:** `clipper score|segments|meta|thumbs <stream> --dry-run` prices it from the prompts
  it would send; `score` refuses a rerun without `--force`.
- **Upload one item now:** approve in Kitsu, `clipper schedule` (back up schedule.json first), then
  `clipper upload --max 1`; asap items go public at once (serve's hourly runs log to `logs/serve.log`).
- **One-off from a YouTube video:** `clipper --show <key> preshow fetch <url> --from H:MM:SS --about "..."`
  (add a `[shows.<key>]` table with a placeholder channel if the show isn't on Kick), then `clipper run yt-<id>`.
- **Drama catch-up:** `clipper story new "<brief>" [--slug x]` (every step; `story run x --from <step>` redoes
  one) → `story plan x` → `story cut x --no-review`; edit `streams/story-x/story.json` by hand and cut again.
- **Fix a cover by hand:** edit the entry in `thumbnails.json` (frame is 1-based; `t`, `face`, `headline`,
  `text`, inset, band), back it up, `clipper thumbs <id> --ids sNN --compose-only` (attaches in Kitsu);
  `youtube.set_thumbnail` if it's already up. The war room's cover editor does the same.
- **Hand picks:** `clipper pick <id> --find WORD`, then `--from/--to` or `--at` with `--about`, `--dry-run` first.
- **Retake without changing anything:** `clipper retake <id> --item c03 --comment "..." --dry-run`.
- **Related video in Studio by hand:** the field is `#linked-video-editor-link`; click its trigger, search the
  segment's video id, pick the one result, Save; then `clipper upload --related <keys or links>` records it.
- **TikTok by hand:** `clipper tiktok --items <keys> [--resend]`; captions land in Discord #tiktok.
- **NAS recorder deploy:** `bash deploy/record/deploy.sh`, only when nothing is live (it refuses otherwise).
- **Per-stream overrides** (a big night): `streams/<id>/config.toml` (`[schedule] priority = true`,
  `kick_first_hours = 0`, more picks). `CLIPPER_SCHEDULE_START=<date> clipper schedule --reshuffle` refills today.
- **Checks:** `clipper doctor --role gpu --offline`, `uvx ruff@0.17.3 check clipper tests`,
  `.venv/Scripts/python.exe -m pytest -q`, test stream `kick-131151926`.
- **Clean-Windows test of the install (#78):** `git archive HEAD` into a folder, a `.wsb` that maps it
  read-only plus an answers file and a writable out folder, and a LogonCommand script that copies the code
  to `C:\clipper`, runs `deploy\bootstrap.ps1 --answers ... --offline`, then doctor and `setup --check`
  into the out folder. No keys go in. Launch the `.wsb`, wait for a DONE file (~1 min), close the Sandbox.
- **Clean-Linux test of bootstrap.sh:** Docker Desktop on the laptop (start it; restart it if the engine
  returns 500s), `ubuntu:24.04` with the archive, answers and out folder mounted; as root install sudo +
  curl, `rm -rf /var/lib/apt/lists/*`, add a sudo user, then `su - user -c` a script that runs
  `bash deploy/bootstrap.sh --answers ... --offline` and `bash -ic 'clipper doctor ...'` (a new terminal).
  ~5 min (ffmpeg from apt). The font EULA goes unanswered there. Then the by-hand wiki Install-checklist.

## Back up the taste layer (user, 2026-10-24)
The Python is replaceable; the months of editorial judgement are in prompts and config. Treat them
like footage. `clipper backup` (PR #248, #204; wiki Backups) zips the list below into `backups/taste/`
(about 17 MB, 390 files; the newest 8 kept; secrets only with `--with-secrets`), and serve makes one every 7 days. The first was made by hand 11-13 (`backups/taste/taste-2026-11-13-1707.zip`,
no secrets). Copy one off the NAS now and then. Not in it: `deploy/*/env.local` and the Kitsu volumes.
- **In git (push it):** `clipper/prompts/` (every version, never edit one that produced reviewed output),
  `clipper.example.toml`, `studio/`, `reports/`, this file.
- **Not in git, copy by hand or script** (all in the NAS folder since 2026-11-05): `clipper.toml` (show tables,
  house_style, stream_context, scoring rules) and `backups/` (`clipper.before_*`, `schedule.before_*`: hand-edit
  backups go there, not the repo root), each `streams/<id>/config.toml` (lost with the stream folder), `secrets/`
  (YouTube/TikTok tokens are a re-consent to recreate), `faces/known.json` (enrolled faces), the photos share
  (`photos/`, cast.json), `layouts/` (Shorts layout references and per-show `enquirer.toml`), `people.json`,
  `schedule.json`, `deploy/*/env.local`, and the Kitsu volumes on the NAS (every review decision and note).
- Also the project memory `~/.claude/projects/C--clipper/memory/` on the laptop: it holds decisions
  that never made it into HANDOFF.

## Gotchas learned
- Deploys from PowerShell: plain `bash` is WSL's (broken: "getpwuid(0) failed", "Failed to translate"). Use
  `& "C:\Program Files\Git\bin\bash.exe" deploy/hub/deploy.sh`; the user forgets, so always give that form.
- The claude container: its clone sits on a mount where every file is executable, so ruff there needs
  `--extend-ignore EXE002`; its GitHub token can't read checks or Actions (403), so the user confirms CI on the
  PR. Relative config paths outside `[paths]` resolve against the config's folder (hub /data, container /app).
- New Windows installs: keep the folder short (anthropic's files sit ~160 characters deep in .venv; past 260
  imports fail with long paths off); `tzdata` must be a direct dependency (Windows has no zone database).
- Kick: yt-dlp VOD extractor 404s; resolve via the channel videos API (kick.py). Chat history API ~2 req/s,
  429 with Retry-After: 0 → adaptive limiter.
- Stream-copy cuts must start on a keyframe (every ~4 s) or audio drifts; `.ts` seeking for re-encodes needs
  keyframe seek + output trim. ffmpeg backs input seeks off by 3/23 s when video has B-frames: `.ts` lands on
  the keyframe at/after the target, `.mkv` (NAS recorder) at/before it, so `copy_cut` seeks k+0.005 for .ts
  and k+0.15 otherwise, and fails the cut on a sync gap or wrong length. Each compilation part ends with a
  concat `outpoint` where the next file begins, or joins replay 1-5 s (the H3 video, 10-28).
- Reading a shared JSON file on the NAS while another process replaces it raises PermissionError on Windows
  and lands mid-replace over SMB. Read state files with `jsonstate.read`, which retries for ~9 s like `write`
  does, never `json.loads(path.read_text())`; heartbeats go through `daemon.read_heartbeat` (three reads, and
  an unreadable file is unknown, not absent: `record status` exits 1, deploy refuses).
- ffmpeg 8.1 NVENC needs NVIDIA driver 610+ (laptop has 596.72) → use `h264_qsv` locally.
- Bleeping uses asendcmd + a .cmd file (one big volume expression breaks past ~dozens of spans); spans
  ending before the cut point must be dropped.
- Kitsu's proxy rejects big uploads; previews over `[review] max_upload_mb` (480) re-encode to fit.
- review.py reads every `*.json` beside cut videos as sidecars; metadata lives in `metadata/<folder>/`.
- Windows: a file open in another process can't be replaced (fork sessions render previews from cuts).
- `deploy/common.ps1` (the worker task scripts) finds a service as a clipper.exe command line ending in its
  name and once killed its own `services restart` chain; it skips its own process chain now, keep that.
- The war room page loads `warroom.css` and `js/*.js` from disk: a pull that changes the page before serve
  restarts leaves a bare, unstyled page. Deploy or restart serve.
- serve runs `schedule.run` in-process (the war room's and the bot's replans go through its /api/replan):
  scheduler changes need a serve restart. A bot whose serve is down still writes the decision to Kitsu and
  says "not replanned"; the next hourly run places it.
- Retake planner can't edit cover layout; a note about the cover's boxes comes back "unsupported", or gets
  read as an edit it can make ("single issue" became a 63 s trim, 10-28). A layout rule belongs in config;
  undo a bad retake with `retake.apply` + `publish_retake` and a hand plan.
- A recording with dropped HLS segments used to shift the transcript clock; `extract_audio` fills gaps with
  silence. Check new recorders' gaps against the source length.
- Discord review cards carry only the first 60 s of a segment; the full preview is in Kitsu. A reject from
  the card can be about the excerpt, not the cut.
- YouTube sign-in: the OAuth app is In production since 2026-10-30 (tokens from before kept the 7-day
  Testing limit; the user signed in again 11-02, so the token shouldn't lapse). `clipper doctor` (online)
  refreshes it to test it; `upload --sign-in` keeps the analytics scope.
- YouTube bot-checks this IP for yt-dlp: pre-show, one-off and story grabs retry signed in through the
  dedicated Firefox profile (`[youtube] cookies_from_browser = "firefox:clipper"`, doctor checks it is signed
  in) or secrets/youtube_cookies.txt (browsers rotate exports: re-export when "cookies are no longer valid").
  A pre-show stuck on the bot check DMs once per video. A PO-token provider doesn't get past it. The hub
  containers have no browser (compose empties the setting), so since the 11-05 move they read the file; the
  laptop's `clipper-cookies` task refreshes it (wiki Archives#keeping-the-nas-hubs-cookies-fresh).
- Kino's pre-shows are on the main channel @TheKinoCasino since 11-04 (`clipper.toml` since 11-06). That
  feed's RSS 404s from the NAS, so the check reads yt-dlp's undated listing: entries below a grabbed pre-show
  count as older (#162). After a channel switch, grab the latest by hand.
- A cut killed half way can leave `clipper-cut-<id>` running on the NAS: `ssh nas docker rm -f clipper-cut-<id>`.
- `uv sync` can't replace a running clipper.exe (`uv pip install` instead, and include `--extra modal`
  later). yt-dlp needs deno for YouTube.
- The worker runs the code of the last hub deploy, not the checkout: a merged change reaches the containers
  only with `bash deploy/hub/deploy.sh`.
- Modal per-run cost estimates overcount parallel archive runs; trust the billing line in `clipper modal`.
