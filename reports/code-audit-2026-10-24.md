# Clipper code audit (2026-10-24)

Scope: all of `clipper/` (38 modules, ~16,500 lines), `clipper/web/`, prompts in use, config
layering, tracked files. Read-only; nothing was changed. Five reviewers covered the package in
parallel, and every High plus most Mediums were re-read at the cited lines before going in here.
Findings marked *(plausible)* have a traced code path but depend on timing or outside behaviour.

Baseline: `uvx ruff check clipper` is clean. Still no tests. No secrets in tracked files; no
machine-specific defaults in `config.py`; the one `shell=True` is still the `[record] on_finish` hook.

**About yesterday's report** (`code-audit-2026-10-23.md`): its findings mostly hold, but a number of
its line numbers are wrong (e.g. `perform.py:1115`, `cut.py:860`, `meta.py:788` in files of 240-330
lines). Use the status table below for current locations. Its hygiene fix for CRLF
(`git add --renormalize .`) is also wrong: the index is already LF, so that is a no-op.

Since then: `33b2f69` made review.json writes atomic (fixes the torn-read half of M1/M8 for that
one file); `c7fdca4`, `weekly`, `pick`, `modalmon` and archive check/links/cookies are new code.

## Summary

Four new problems matter more than the rest:

1. **One typo in any stream's `config.toml` puts the bot in a crash loop** every morning (A1).
2. **The "uploaded" record is the weakest write in the upload path**, so a lucky collision
   uploads a video twice (A2). The same stale-dict writes now also wipe TikTok marks, which
   re-sends TikTok drafts (A3).
3. **The new YouTube-cookie retry signs in on bot checks, not just age gates** (A4), so under an
   IP block the whole Quartering run goes out on the user's Google account.
4. **One Kitsu hiccup anywhere stops all YouTube uploads** (B1), because upload only runs if the
   replan exited 0.

The shared-JSON cluster from yesterday is still the root of most issues, with one new twist:
`worker.write_json` (now used for review.json) always uses the same `<name>.tmp`, so two
concurrent writers collide (B3). A single helper — unique tmp name + `os.replace` + a per-file lock,
and "re-read, merge, write" for schedule.json — closes A2, A3, B3, B5, M1, M2, M4, M8 and L2.

## A. High

**A1. Bot exits on SystemExit from a stream's config.toml, then crash-loops all day.**
`alerts.checks` builds the 09:00 digest (`alerts.py:120-123`) via `warroom.overview` (`alerts.py:190`),
which calls `stream_info` → `for_stream` for every stream (`warroom.py:131`, `:309`) with no guard.
`for_stream` raises `SystemExit` on an unknown key (`config.py:698-699`). `notify_loop` catches only
`Exception` (`bot.py:274`); `SystemExit` passes through `asyncio.to_thread` and stops the event loop.
The supervisor restarts the bot, `st["digest"]` was never saved, so it dies again on the next pass,
until midnight. A TOML syntax error is a ValueError instead: caught, but it fires every minute
before the outbox, so no DM of any kind goes out that day. The same missing guard blanks the war
room (`all_rows` catches Exception only). Exposure is real: `kick-133940023/config.toml` exists and
per-stream overrides are how the H3 night was tuned. Fix: catch `(Exception, SystemExit)` per
stream in `overview`/`all_rows` and around `digest()`; turn SystemExit into an error in the bot's
`to_thread` calls the way `blocking()` already does.

**A2. A finished upload can go unrecorded → uploaded again next hour** *(timing plausible)*.
After `upload_video` returns, `youtube.py:640` re-reads schedule.json with a bare `json.loads`, and
`:646` writes it with plain `write_text`. Other writers (bot replan `schedule.py:273`, TikTok child
`rumble.save_platform`, `note_links`) also write in place. A re-read during a truncate raises, the run
aborts, and the "uploaded" record exists only in memory; the entry is still `scheduled` next hour.
The comment at `:633` says this record is there to prevent a double upload; the record is the weak
step. TikTok has the same shape (`tiktok.py:306-309`). Fix: atomic writes for schedule.json
everywhere, and retry the post-upload record (or append to an `uploads.jsonl` journal that
`upload_due` also consults) instead of dying.

**A3. `finish()` writes the start-of-run schedule dict, wiping TikTok/Rumble marks → duplicate TikTok
drafts.** `finish` writes `sched` (loaded at `youtube.py:536`) at `:482` after the thumbnail and at
`:498` after each playlist add; `backfill_thumbnails` does the same at `:468`. The bot starts the upload
chain and `tiktok --auto` in the same refresh pass (`bot.py:207-217`), both hourly. A TikTok mark saved
in between is overwritten; `schedule.run` only carries platforms over from the file, so it is gone.
`tiktok --auto` then re-queues the Short (`tiktok.py:325`) and `sent_last_24h` undercounts the
5-pending cap. Fix: route these through the re-read-and-merge path `note_playlist` already uses
(`:424`). This is yesterday's M2 with a worse consequence.

**A4. Archive cookie retry fires on YouTube's bot check, not only the age gate.** `archive.py:194`
retries signed in when stderr contains "Sign in to confirm", which both "…your age" and "…you're not
a bot" contain. Under a bot check every item retries signed in, usually succeeds, `streak` resets
(`:264`), and the `stop_after_failures` guard (`:273`) written for exactly this never trips. Nothing
logs that a signed-in retry happened. Over the ~2,730 h Quartering run that means the Google account
behind the cookies carries the whole catalog, the opposite of the comment at `:195`. Fix: match
"confirm your age" only (or probe `age_limit`), and log one line per cookie use with a count in the
run summary.

## B. Medium (new)

**B1. Any Kitsu problem stops every YouTube upload.** `_auto_upload` runs `upload` only if `schedule`
exits 0 (`bot.py:318`). `schedule.run` syncs every published stream with no per-stream try
(`schedule.py:159-160`) and `push_to_kitsu` is unguarded (`:333-343`). A Kitsu outage, or one task
deleted from an old dsp/blade test stream, fails the replan every hour and nothing uploads, though
`upload_due` itself falls back without Kitsu. Fix: per-stream try in the sync loop, and run upload
even when the replan failed.

**B2. `refresh_cards` can die silently, taking auto-upload, TikTok, analytics and auto-retake with it.**
The analytics gate reads schedule.json on the event loop outside any try (`bot.py:218-224`). A read
mid-rewrite can hit a split UTF-8 character (`ensure_ascii=False`) → UnicodeDecodeError, and the task
ends; buttons still work, so nothing looks wrong. `notify_loop` similarly calls `tailnet_status`
before its loop (`bot.py:250-253`); if that raises, no DMs for the bot's lifetime. Fix: wrap the whole
loop bodies, do the read in `to_thread`.

**B3. `worker.write_json` uses one fixed tmp name.** `worker.py:106` always writes
`review.json.tmp`. The bot, the war room (separate locks, `warroom.py:42` vs `bot.py:162`; Sync button
unlocked), the upload child and the worker can all sync one stream. The second `os.replace` raises
FileNotFoundError (only PermissionError is retried, `:112`): a war room Approve that Kitsu recorded
shows a 500 and skips the replan; in cardsync it aborts the rest of the pass. Fix: unique tmp per
write (`tempfile.mkstemp(dir=...)`) and one shared lock for review writes in `with_bot` mode.

**B4. analytics.json is written in place and read unguarded by Pass B, after Pass A is paid.**
`analytics.py:34-36`, `:123`; `scoring.py:436-438`. A torn or truncated file fails every later score
at Pass B and every `clipper analytics` until deleted. Fix: `write_json`, and treat an unreadable
file as no feedback.

**B5. Scoring: the summary blocks are unguarded** (`scoring.py:310-315`, surfaced at `:494`). One
failed 15-min summary call kills `run()` after all of Pass A is paid, with nothing written. Same
class as H2 below.

**B6. Pass B ids are not checked against the kept set** (`scoring.py:445-449`). `unknown_ids` is
computed but `picks` is returned unfiltered. An invented id raises KeyError in `cut.py:234` /
`shorts.py:582` on every rerun; an id of a rejected Pass A window (third-party audio, casino) is
found in `by_id` and cut with raw unsnapped times. Fix: filter and dedupe, or make `id` an enum.

**B7. segments: the paid Opus split is lost if any compilation call fails** (`segments.py:152-158`).
Write segments.json right after `split()`.

**B8. The retake prompt doesn't get the "never hosts" rule.** `VIEWER_FACING` (`scoring.py:180-181`)
lacks `"retake"`, and `retake_v1.md` has no rule of its own, yet retake writes titles, descriptions,
hashtags and thumbnail words straight to Kitsu via `actions.edit_meta`/`thumbtext`. One-word fix.
Related (low): `segments_v2.md` and `scan_v4.md` call the people on the show "hosts" in their own
instructions, which invites leaks; add v3/v5 that name Andy/PPP.

**B9. `clipper pick` runs whole stages without the worker's lock** (`pick.py:141-151`, then
`make_picks` at `:366-383`). It checks `.run.lock` once at the start, then spends minutes on model
calls and runs cut/meta/thumbs/shorts/review publish unlocked; a worker pass on the same stream
rewrites the same files from stale copies. It also writes candidates.json (`:362`) from the copy read
at `:312`, dropping a retake trim made meanwhile. Fix: hold `worker.Run` around save + make_picks.

**B10. weekly: `--render` can pay for a draft, and `--draft` loses the old issue first.**
`weekly.py:1390-1395`: the draft branch is `redraft or not ij.exists()`, with no `render_only`
check, and `--draft` moves issue.json to a backup before calling Opus. If the call fails, the next
"free" `--render` runs a full draft plus three writer calls. Fix: copy (not move) the backup and
replace only after the new draft parses; `--render` without issue.json exits with a hint.

**B11. weekly writer pass saves nothing until all pages are done** (`weekly.py:436-466`, write at
`:1409`). An exception on page 05 loses 03 and 04. Font/face validation also runs only after the paid
call (`:447`→`:452`). Write issue.json after each page; build the Renderer before `preflight`.

**B12. archive: Ctrl+C keeps spending.** `archive.py:255-277`: every item is submitted up front and
`with ThreadPoolExecutor` exits with `shutdown(wait=True)` and no `cancel_futures`, so after a
KeyboardInterrupt the threads keep sending every queued item to Modal until the process is killed.
Fix: `try/except BaseException: pool.shutdown(cancel_futures=True); raise`, or submit `parallel` at
a time.

**B13. Recorder: the stall alert only fires on playlist fetch errors** (`record.py:329-338`).
If every segment download 403s (dropped as "lost", `:357`) or the playlist keeps returning 200 with
no new segments, `fails` stays 0: no alert, no re-resolve, and the show goes unrecorded for hours.
Yesterday's M10 is the same logic. Fix: check `last_seen` every pass; re-resolve after
`stall_alert_s` with no new segment.

**B14. War room in tokenless mode: UNC paths and DNS rebinding** *(credential leak plausible)*.
On Windows `streams_root / "//evil/s/x.jpg"` becomes `\\evil\s\x.jpg`, and `.resolve()` touches it
before the parent check (`warroom.py:553-554`, `:673-674`; `config.stream_dir` for POST refs), which can
leak an NTLM hash via `<img>`. No Host header check means a rebinding page can read the API, not just
blind-POST. **This machine runs in token mode**, so it is not exposed here; it matters for a fresh
install or the NAS hub. Fix: reject absolute/drive/UNC/`..` refs before any filesystem call; check
Host. Together with H5, the simplest fix is "always require a token".

**B15. Retitle/Pin/Thumbnail-text buttons read the NAS on the event loop** *(plausible)*.
`bot.py:359-377` calls `actions.resolve`/`describe` before `send_modal`; on a slow share that misses
Discord's 3 s window ("This interaction failed") and freezes every other button meanwhile.

**B16. Priority slots can land inside `min_lead_minutes`** (`schedule.py:233`, `:237` vs
`youtube.py:567`). Since `c7fdca4` slots start from today; `fill` skips only `when <= now`, so a 14:45
replan can fill 15:00, which upload immediately calls missed. A jump also overrides a future
`[schedule] start`. Fix: skip `when <= now + min_lead`.

## C. Low (new)

- **Priority jump stays on forever** when a priority item never gets a slot (`schedule.py:213`), freeing
  non-priority entries every replan, possibly one mid-upload; `done` (`youtube.py:635`) lacks
  `publish_at`, so the record can disagree with YouTube.
- **Any Discord error is read as "thread deleted"** (`notify.py:193-198`): a 503 re-posts every card.
- **Captions failure skips playlists and the Kitsu URL** (`youtube.py:488-503`, one try for all three).
- **Titles truncated at 100 bytes, not characters** (`youtube.py:96`, `:135`); review checks characters,
  so em dashes/emoji pass review and get cut at upload.
- **A failed `note_links` duplicates the link line** next run (`youtube.py:290-292`).
- **Modal spend alert failures log at DEBUG** (`alerts.py:124-130`) under an INFO root: the $25 alert can
  silently never run.
- **`last_upload`/`last_tiktok`/`last_analytics` start at 0.0 vs `time.monotonic()`** (`bot.py:166-171`):
  after a reboot the first auto-upload waits an hour of uptime and analytics 24 h.
- **analytics: one HttpError aborts the run** and loses every count fetched (`analytics.py:107-121`).
- **`youtube_url` from Kitsu rendered as `href` without a scheme check** (`warroom.html:598`).
- **Archive cookies file shared by 8 yt-dlp processes**, which write the jar back (`archive.py:155-169`)
  *(plausible)*; no lock against two `archive transcribe` runs (`:220-245`); in-flight results after
  the failure-streak break are never logged or costed (`:273-277`).
- **Lip-sync paid output lives only in a TemporaryDirectory** (`perform.py:223-233`); a mux failure loses it,
  no skip-if-exists, sync.so job id never printed.
- **Pre-show meta.json and keyframes.json written in place** (`preshow.py:166`, `cut.py:36-48`); both are
  done-markers/caches.
- **Pre-show format ending `/b` can produce source.webm**, which `preshow.py:153-157` never finds, so it
  re-downloads every 5 min *(plausible)*.
- **pick**: partial `make_picks` failure strands picks that a rerun skips (`:332-338`); `--about` reads the
  current prompt version's summary only (`:193`); hand picks hard-code `third_party_audio: False`
  (`:359`); no 15-59 s check on windows (`:239-249`; also `retake.snap_trim` allows 3 s Shorts).
- **retake `trim_short` vs a running `shorts.run`** lose `window_override` (`retake.py:217-224`) *(plausible)*.
- **weekly**: `check_issue` checks top-level keys only (int `number` → TypeError, string headline renders
  one letter per line); `p_numbers` `max()` on an empty week (`:1172`); cut-out cache key ignores face
  box and `bottom` (`:594`); `Claude()` without `workspace_id` (`:368`, `:434`); `--only 1,7` matches
  nothing silently; `--headline` ignored without `--draft`; `--format` checked after the paid calls;
  minute-resolution backup names; cut-out catches only ImportError; "one every N s" counts pre-show
  words over live hours.
- **config**: `bot_log = ""` (documented as console-only) resolves to the config dir and breaks the war room
  `health()` read (`config.py:585-588`); a show with `[shows.x.paths]` re-applies `CLIPPER_PATHS_*` as a
  str (`:667-669`; the NAS image sets `CLIPPER_PATHS_STREAMS`); empty unknown table / top-level scalar
  raise raw errors (`:698-701`, `:601-602`).
- **doctor**: no check for weekly/weekly_article/pick prompt files (`doctor.py:103-115`); unguarded
  `json.loads` of the TikTok token (`:312`).

## D. Yesterday's findings: current status

| ID | Status | Where now | Note |
|---|---|---|---|
| H1 | Open, narrower | `schedule.py:165`→`:273` | Read is after the sync, window is ms. Real duplicate paths are A2, M5. |
| H2 | Open | `scoring.py:506-512`; `meta.py:268-276` (old ref wrong) | Plus B5, B7. |
| H3 | Open | `scoring.py:410-416` | Empty span → `min(range(0))`; no in<out or 15-59 s check. |
| H4 | Open | `retake.py:404-425` | `record()` still last. |
| H5 | Open | `warroom.py:452-454`, `:524-529` | Not exposed here (token mode). See B14. |
| M1 | Partly fixed | `review.py:741`, `:769` | Torn reads gone; lost updates and separate locks remain; see B3. |
| M2 | Open, worse | `youtube.py:468`, `:482`, `:498` | See A3. (`note_playlist` part was wrong: it re-reads.) |
| M3 | Open | `schedule.py:112` vs `youtube.py:195` | Not hit locally (clipper.toml includes compilations). |
| M4 | Open | `thumbs.py:1093`/`:1150`, `:1022`/`:1037` | |
| M5 | Open | `youtube.py:606-648` | No per-entry try; only 5xx retried. |
| M6 | Open | `youtube.py:556`, `:476`, `:393`; `rumble.py:101`, `:120` | |
| M7 | Open | `schedule.py:203-206`; `actions.py:160` | |
| M8 | Partly fixed | review.json only | Every other file listed is still `write_text`. |
| M9 | Open | `scoring.py:296-347`; `segments.py:122-132` | |
| M10 | Open | `record.py:333`, `:849` | See B13. |
| M11 | Open | `transcribe.py:197`, `:216-218`; `run()` `:156-164` | Archive treats a truncated words.jsonl as done for good. |
| M12 | Open | `youtube.py:274`, `:281-284`, `:574-576` | |
| M13 | Open | `perform.py:203-210` (old ref wrong) | |
| L1 | Open | `retake.py:32`, `:438-451` | Also `__exit__` unlinks a lock it may not own. |
| L2 | Open | `bot.py:428-429`; `cardsync.py:63`, `:121` | |
| L3 | Open | `config.py:562-563` | UNC variant in B14. |
| L4, L5 | Open | `warroom.py:459`, `:455`, `:492`; `:360`, `:419`, `:522`, `:551` | |
| L6 | Open | `record.py:708-711` | Safe today. |
| L7 | Open, slightly wider | `warroom.html:493-494`, `:522`, `:583-584` | |
| L8 | Open, worse | `kick.py:71-78` | Also hangs `record.finalize`'s chat backfill. |
| L9, L10 | Open | `config.py:550-582`, `:665-666` | |
| L11 | Open, worse | `worker.py:159-201` | Also: the old holder's `release()` clobbers the new holder after a takeover. |
| L12 | Open | `doctor.py:270-282`, `:477-494` | guild_id/allowed_user_ids, record channels, timezone. |
| L13 | Open | `review.py:225-228` | Not hit locally (no "picks" in include). |
| L14 | Open | `cli.py:355-361` | |
| L15 | Open | `thumbs.py:1147-1150` | Also IndexError if `frames()` is empty (`:267`). |
| L16 | Partly fixed | clipper.toml sets 15/59 | Min is best-effort, override path skips both, defaults still 90/179. |
| L17 | Open | `record.py:583`, `:596-597`, `:424`, `:262` | ts container only. |
| L18 | Open | `youtube.py:488-503` | 50 vs 51 units still disagree. |

## E. Repo hygiene

- **Test fixtures committed by accident:** `lt/jer/catalog.json`, `links.json`, `yt-B/transcript.json`,
  `yt-B/words.jsonl` came in with `f9b3d9a`; `archive/` is ignored but `lt/` is not.
  `git rm -r --cached lt` and ignore it.
- **CRLF:** 42 tracked files (20 `.py`) are LF in the index, CRLF in the working copy. Not a repo
  problem; with nothing uncommitted, a fresh checkout of those files puts them back to LF
  (`.gitattributes` wins over `core.autocrlf`). Then keep the editor on LF.
- **HANDOFF.md:** the duplicated "Walkthrough" line (7-8) is still there.
- **clipper.example.toml:** `[archive]`, `[modal]`, `paths.archive` added since yesterday; still
  missing `transcribe.backend`, `youtube.previous_part`/`next_part`, `preshow.format`, and nearly all of
  `[weekly]` (including the six font lists a Linux install needs) and `paths.weekly`.
- **Media in git:** ~12.4 MB of JPG/PNG under `mockups/` and `docs/`, plus 11 third-party TTFs in
  `mockups/sloth_beat/fonts` (check their licences before the repo goes anywhere). `*.mp4` still not
  ignored (`docs/tiktok-demo-placeholder.mp4` is untracked).
- **Docs:** README/SETUP cover `weekly` and `modal`; `pick` and `archive check` are README-only (fine).
- **No tests.** The atomic-write helper, `schedule.place`/`fill`, and config layering are still the
  cheapest three to cover.

## F. Checked and found fine

Discord: every button and slash command requires an allowlisted user in the configured guild; modals
only go to their opener. Kitsu writes go only through `actions.py`, all with `for_stream`.
`schedule.run` never moves an uploaded entry; `attach` refuses a recorded video id; `max_per_24h`
counts Studio attaches. War room `/media` and `/api/lowres` stop `..` traversal; `esc()` covers model,
Kitsu and Kick text, including the new Modal panel; the cookie is HttpOnly + SameSite=Lax. modalmon
only reads billing and never spends; its month boundary is UTC like Modal's. alerts' outbox and state
are atomic, with cooldown and quiet hours. Recorder, cut, transcribe audio, archive catalog/links,
and Kick chat parts all write via tmp+replace. Bleep and chapter timestamps map correctly across
cuts. TikTok sign-in uses state + PKCE on loopback. No key reaches a log or error message; archive
cookies are never logged. Writer-pass retries are bounded to two; weekly prompts mention "hosts" only
in the ban.

## G. Suggested order of fixes

1. **Today, small:** A1 (catch SystemExit per stream; maybe 10 lines), A4 (match "confirm your age",
   log cookie use), B8 (`"retake"` in VIEWER_FACING), `git rm --cached lt`. All four are low-risk.
   *Done 2026-10-24, same day:* war room `stream_infos`/`_show_name` and `all_rows` skip a stream
   whose config raises SystemExit; a failed digest sends "Daily digest failed: ..." once; the bot's
   card, retake-check and notify loops catch SystemExit; the archive retries signed in only on
   "confirm your age" / "age-restricted" and logs each one, with a count in the run summary;
   retake gets the house style; `lt/` untracked and ignored.
2. **One PR: shared JSON state.** A `state.write_json` with a unique tmp + replace + per-file lock,
   and a `schedule.update(key, fn)` re-read-merge-write; use it in `youtube.py` (A2, A3, M2), `rumble`,
   `tiktok`, `schedule`, `review`, `thumbs`, `cardsync`, `notify`, `analytics`. Fold in B1 (per-stream try,
   upload even if replan failed) and M5 (per-entry try). Try it on `kick-131151926` first.
3. **One PR: don't lose paid work.** H2, H3, B5, B6, B7, B11, B10, H4, M9: write after each paid step,
   validate model output, don't cache failures.
4. **Bot robustness:** B2, B15, the monotonic start values, Modal spend logging.
5. **When the NAS hub or a second install happens:** H5 + B14 (always require a token, Host check),
   B12, B13, doctor gaps (L12, prompt files).
