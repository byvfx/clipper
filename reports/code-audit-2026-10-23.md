# Clipper code audit (2026-10-23)

Scope: the whole `clipper/` package (36 modules, ~14,000 lines) plus `clipper/web/warroom.html`,
`deploy/`, config layering, tracked files and docs. Read-only; nothing was changed. Every finding
below was verified against the source at the line given (working tree as of this date, including
the uncommitted source-links work in `youtube.py`, `config.py`, `cli.py`).

Baseline: `uvx ruff check clipper` is clean. There are no tests. No secrets in tracked files. No
machine-specific defaults in `config.py`. Every subprocess call is an argv list (the one `shell=True`
is the user's own `[record] on_finish` hook). Every text/JSON read and write passes `encoding="utf-8"`.

## Summary

The pipeline stages themselves are in good shape: the recorder, worker and archive write state
atomically, ffmpeg calls are careful, and model calls have schemas, retries and preflight. The
problems cluster in three places:

1. **Shared JSON state is written non-atomically and without a common lock.** `schedule.json`,
   `review.json`, `review_state.json`, `thumbnails.json`, `candidates.json`, `shorts.json`,
   `retakes.json`, `discord.json` and `discord_cards.json` are all written with plain `write_text`
   and are read and written by several processes at once (the bot, its hourly `clipper upload`
   child, the war room, the worker, a human at the CLI). The worst case is a duplicate YouTube
   upload (H1). The common case is a lost edit or a truncated file that every later reader
   crashes on.
2. **Expensive model work is not checkpointed.** Pass A results, meta drafts and retake plans are
   only written at the very end of their stage, so one late failure repays everything (H2, H3, H4).
3. **The war room trusts localhost.** With no token set, any web page the dev machine visits can
   POST decisions into Kitsu (H5).

Fixing the first cluster is one change: a small `atomic_write_json` + a per-file lock helper used by
every writer (the worker already has the atomic half). That alone closes H1, M1, M2, M4, M8, L2.

## High

**H1. Duplicate YouTube upload via the schedule.json race.** `schedule.run` reads the old plan
(`clipper/schedule.py:165`), spends minutes statting every stream, then rewrites the whole file
(`:272`), copying each entry's `status` from the old copy (`:180`). The Discord decision buttons and
`/schedule replan` run it in the bot process (`clipper/bot.py:402`, `:530`) while the hourly
`clipper upload` child is mid-run. The child records an upload by re-reading and merging
(`clipper/youtube.py:640-646`), but a replan that read before that write and writes after it puts
the entry back to `scheduled` with no `video_id`. The next hourly run uploads it again: two public
copies, two counts against `max_per_24h`. Same race with a human running `clipper schedule`,
`rumble --done` or `tiktok`. Fix: lock + atomic write around every schedule.json writer; have
`schedule.run` re-read immediately before writing and keep any entry that became `uploaded`.

**H2. One failure in scoring Pass B throws away every paid Pass A call.** `run_pass_b` is
unguarded and `candidates.json` is only written after it (`clipper/scoring.py:506-511`). A 529 or
max_tokens error on an 8 h stream means a rerun pays all of Pass A again. `meta.py:788-836` has
the same shape (big meta call plus every chapters call before any draft is written). Fix: write
`candidates.json` with `pass_b: {"status": "pending"}` after Pass A, then update.

**H3. An unguarded `snap()` after a paid call can kill the whole scoring run.** In Pass A's `one()`
(`clipper/scoring.py:411-416`) `snap(span, ...)` runs outside the try that wraps the model call.
`span` is empty when a window and its lead-in have no transcript words (music, a break with chat
heat), and `min(range(0))` raises, which escapes `pool.map` and kills `run()` with nothing written.
Same spot: nothing checks `out_s > in_s`, so swapped times from the model become a negative
`duration_s` that later feeds `ffmpeg -t` in `cut.py`. Fix: guard empty span (mark the window
`error`), validate ordering and the Short's 15-59 s range.

**H4. A retake is marked handled last, after Kitsu and Discord side effects.** `process()`
(`clipper/retake.py:383-425`) runs the model plan, re-cuts and thumbnail re-picks, then
`review.publish`, Kitsu comments, `review.sync`, `notify.post`, and only then `record()`. Only
`apply()` is in a try. If Kitsu is down the note stays pending and the bot re-runs it on every
card refresh: a paid planning call (plus thumbnail picks) per cycle until Kitsu returns. Fix:
record the entry right after `apply()` succeeds, then do the notifications.

**H5. War room has no authentication or CSRF protection in its default localhost mode.**
`authorized()` returns True when `[warroom] token` is empty (`clipper/warroom.py:442-449`);
`do_POST` checks no Origin, Sec-Fetch-Site or Content-Type and JSON-decodes any body
(`:512-539`). With `[warroom] with_bot` on the dev machine, any web page the user visits can
auto-submit a `text/plain` form to `http://127.0.0.1:8765/api/action` and approve, reject, pin,
replan or start a retake; the decision lands in Kitsu as "war room". `GET /api/lowres?src=` also
has a side effect (queues an ffmpeg encode) and can be hammered via `<img>` tags. Token mode
(tailnet) is not exposed because the cookie is SameSite=Lax. Fix: always require a token, or
on POST require `Content-Type: application/json` and reject requests whose `Sec-Fetch-Site`
is not `same-origin`/`none`.

## Medium

**M1. Review state races between the bot and the war room.** `warroom.py:42` has a
`threading.Lock`, `bot.py:162` an `asyncio.Lock`; with `with_bot` both run in one process and never
take each other's lock. `sync_all` (`warroom.py:398-412`) runs `review.sync` in a bare thread with
no lock. `review.py:737,763` partial-syncs by reading review.json, merging one key and rewriting
the whole file. A Discord Approve and a war room Reject on the same stream lose one decision
locally and then replan from the stale file. A reader hitting a half-written file gets
JSONDecodeError, which aborts the bot's whole refresh pass (`bot.py:203`).

**M2. `finish()` rewrites schedule.json from the stale start-of-run dict.** `youtube.py:648`
passes `sched` (loaded at `:536`) into `finish`, which writes it at `:468` and `:482`, right after
`:640-646` carefully re-read and merged. Any replan or rumble/tiktok platform mark since the run
started is discarded. `backfill_thumbnails` (`:468`) and `note_playlist` (`:428`) do the same.

**M3. Segment → compilation source links never match unless compilations are Kitsu items (new
code).** `source_parents` looks up `("compilation", "comp-<stem>")` (`youtube.py:196`), but a
non-Kitsu compilation entry has `id = comp["topic"]` (`schedule.py:111-112`); only its key is
`comp-<stem>`. The local clipper.toml includes compilations so it works here, but with the
default `[review] include` (`config.py:399`) the `segment_source` lines SETUP.md promises are
silently never added. Fix: match on key, or set `id = key` in `schedule.py` for non-Kitsu comps.

**M4. thumbnails.json lost-update race.** `thumbs.run` loads the whole state (`thumbs.py:1093`)
and rewrites the whole file after each pick (`:1150`); `review.sync` / `set_texts` in the bot
process does the same read-modify-write (`:1017-1038`). A reviewer's thumbnail-text edit that
lands while the worker is picking is overwritten with no error.

**M5. One bad due entry aborts the whole hourly upload, every hour.** `upload_due`'s loop has no
per-entry try (`youtube.py:626`): a missing file or non-retryable HttpError on the earliest slot
blocks every later one until someone fixes it. An upload YouTube completed but whose final
response was lost (only 5xx is retried, `:304-318`) is unrecorded and re-uploaded next run.

**M6. `[shows.<key>.youtube]` overrides apply inconsistently.** `upload_due` (`youtube.py:565`),
`finish` (`:476`) and `backfill_playlists` read the base config; `metadata` and the new
source-links code use `for_stream`. Per-show `manual_publish`, `notify_subscribers`, `captions`,
`made_for_kids`, `playlist_privacy` and `link_updates_per_run` silently do nothing.

**M7. A Kitsu "Publish at" in the past pins an item forever.** Pinned items are placed without a
`when > now` check (`schedule.py:195-206`); `youtube.py:555-558` then lists it as missed every
hour and the replan re-pins it in the past. Pinned items also skip the `earliest` /
`kick_first_hours` check.

**M8. Non-atomic writes of nearly every stage and state file.** `shorts.py:682,691`,
`segments.py:381`, `scoring.py:319,347,511`, `thumbs.py:1037,1073,1150`,
`retake.py:213,223,263,349,361`, `meta.py:836`, `cut.py:860`, `review.py:518,554,599,629,671,772`,
`schedule.py:272`, all `youtube.py` schedule writes, `rumble.py:70`, `cardsync.py:121`,
`notify.py:241,261`, `analytics.py:349`, and the OAuth token at `youtube.py:82` (rewritten on
every `credentials()` call). Readers `json.loads` them with no handling, and the war room and
bot read them concurrently on Windows. Only `record._write_json`, `worker.write_json`, `alerts`,
`archive` and `kick` chat parts do it right. A crash mid-write leaves a stage that fails on every
rerun until the file is deleted.

**M9. Scoring failures are cached forever.** `transcript_scan` returns `[]` on any exception and
`stream_summary` `""` on refusal, and both are written into the per-version cache files
(`scoring.py:310-347`) that every rerun honours. A transient outage silently loses those blocks
for the prompt version with no way back except deleting the file. `segments.py:344-355` keeps a
refused compilation's placeholder title the same way.

**M10. Recorder: `last_seen or _now()` disables the stall alert and the offline pause when
nothing was ever received.** `record.py:333` and `:849`. A stream that never delivers a segment
(variant 403, stale playback URL) never alerts, never pauses, never finalizes, and absorbs the
channel's next real stream as a new session of the zombie.

**M11. transcribe writes words.jsonl in place and never skips.** `transcribe.py:189-212` opens
the done-marker file with `"w"` and streams lines; a kill or disk-full mid-write leaves a
truncated transcript that later stages accept as complete. `run()` also never checks for an
existing words.jsonl, so a manual rerun redoes the full GPU or Modal job.

**M12. Quota is tracked in uploads only, and the new link backfill has no daily cap.**
`youtube.py:566-573` counts a rolling 24 h of uploads, nothing counts API units. `backfill_links`
spends up to `link_updates_per_run` × ~51 units every hourly run; changing `clip_source`
wording makes every uploaded clip stale again. A deleted video stays in `todo` forever
(`:294-297`). With 15 uploads a day plus captions, thumbnails, playlists and links the spend
exceeds 10,000 units unless the project has an extension, and a 403 then hits M5.

**M13. `perform.lipsync_sync` polls forever with no error handling.** `perform.py:1115-1122`:
a transient connection error kills a paid job; an unexpected status loops forever.

## Low

**L1. Retake lock goes stale after 1 h and is never refreshed** (`retake.py:32,440-445`). A long
retake over the NAS share gets a second concurrent run that deletes its lock.

**L2. cardsync / discord.json lost updates** (`bot.py:429` outside the lock, `cardsync.py:87,121`,
`notify.py:241,261`). Benign re-edits; a torn write re-posts cards.

**L3. `actions.resolve` accepts absolute paths and `..` in refs from the network.**
`config.stream_dir` (`config.py:503-506`) returns `Path(ref)` unchanged if it is a directory.
Bounded (needs a valid review_state.json there) but reachable from the unauthenticated POST in H5.

**L4. War room token: plain `==` compare, token in URLs, year-long cookie without `Secure`**
(`warroom.py:449,437-445,482`). Use `hmac.compare_digest`.

**L5. Error responses echo exception text** (`warroom.py:510,539`), which routinely includes
NAS paths and gazu bodies. Log it, return a generic message.

**L6. `on_finish` is `shell=True` with `.format`** (`record.py:709-711`). Safe today (numeric
ids and config only); becomes an injection the day `{title}` is added.

**L7. `decision` and `kind` reach HTML class attributes unescaped** (`warroom.html:479-482,510`).
Needs Kitsu admin to exploit.

**L8. Kick client retries 429 forever** (`kick.py:70-78`) with no wall-clock cap; a banned IP
hangs the worker stage silently.

**L9. Env override of list/dict keys yields a string** (`config.py:493-500`):
`CLIPPER_WORKER_CHANNELS=kino` makes `worker.py:337` a substring test. `int("1.5")` is an
uncaught ValueError on every command. The module docstring claims any key is overridable.

**L10. `for_show` accepts unknown keys silently; `for_stream` rejects them** (`config.py:591` vs
`:634`). A typo in `[shows.kino.youtube]` does nothing and doctor does not check.

**L11. Worker heartbeat thread can die silently** (`worker.py:187-193` catches only OSError;
`save()` iterates `self.data` while the main thread mutates it). Stale heartbeat → a second
worker takes the stream over; two takers race on the O_EXCL open (`:175-176`) and one reports
"crashed".

**L12. doctor misses settings that make a role exit at start:** `[discord] guild_id` /
`allowed_user_ids`, `[record] channels` for `--role nas`, `[schedule] timezone` validity.

**L13. Pick keys renumber when the clip sidecar set changes** (`review.py:224-226`): a re-score
that adds or removes a pick shifts later `cNN` keys, orphaning Kitsu shots and breaking the
review.json ↔ schedule.json key match.

**L14. `clipper run --from <bad>` raises a raw ValueError** (`cli.py:313`, the check at `:314` is
unreachable); `--from` with `--only` silently ignores `--from`; `--from` forces clip stages on a
pre-show.

**L15. Thumbnail picks lost on a pool failure** (`thumbs.py:1147-1150`): one bad grab aborts
iteration and paid picks for later assets are never written.

**L16. Shorts have no lower length bound in code** (`shorts.py:592-599`); the 15-59 s rule
from the review prefs is not expressed anywhere (defaults are `min_s` 90 / `max_s` 179).

**L17. Single-`.ts` recordings are not restartable at finalize** (`record.py:596-597,583`),
only with `[record] container = "ts"`. A broadcast restart within 30 s loses up to 30 s
(`:423-425`). `chat_http` session is created and never used (`:262`).

**L18. Captions have no backfill path** and a `captions.insert` failure swallowed at
`youtube.py:487-489` is permanent. The `link_updates_per_run` comment says 50 units, SETUP says 51.

## Repo hygiene

- 19 of the tracked `.py` files have CRLF line endings in the working copy although
  `.gitattributes` says `eol=lf`; git warns it will normalise them on the next touch. One
  `git add --renormalize .` commit settles it.
- `HANDOFF.md` has a duplicated "Walkthrough" line near the top and carries the NAS IP; the
  repo is private so this is cosmetic.
- `clipper.example.toml` lacks 79 keys that have defaults in `config.py`. Most are fine as
  defaults, but `transcribe.backend` (modal/mlx), `paths.archive`, `archive.*`, `preshow.*` and
  `youtube.previous_part`/`next_part` are things a new install would want to see.
- ~4.5 MB of mockup JPEGs are tracked and more JPEGs plus an MP4 sit untracked under
  `mockups/` and `docs/`. Consider ignoring `*.mp4` and keeping contact sheets out of git.
- No tests. The three stage-independent helpers that matter most (atomic JSON write, schedule
  `place()`, config layering) are small enough to unit-test without fixtures.

## Checked and found fine

War room binds 127.0.0.1 by default and refuses a non-loopback bind without a token; `/media/`
and `/api/lowres` resolve under the streams root with a parent check and suffix whitelist; all
user- and model-generated text goes through `esc()`. Discord bot requires `allowed_user_ids`
and `guild_id` on every button and slash command and refuses to start without them; custom_ids
are bot-made and routed only to the owning app. Kitsu decisions are written only through
`actions.py`. Secrets come from env or `secrets/` and never reach prints, logs, exceptions or
Discord messages. Config layering order and env precedence are correct; every stage calls
`for_stream`. All datetimes are tz-aware and publishAt is sent as UTC. Resumable uploads retry
5xx with backoff; token refresh failure gives a clear fix. The recorder handles gap, sequence
reset and discontinuity, SIGTERM pause/resume, disk-full, and chat backfill with coverage spans.
Anthropic calls use structured-output schemas, `max_retries=4`, a preflight call and a bounded
pool. Video outputs are written to `.tmp.mp4` then replaced.
