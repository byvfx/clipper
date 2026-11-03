# Clipper code audit (2026-11-03): correctness, then architecture and usability

Scope: all of `clipper/` (27,110 lines in 50 modules and 3 packages), `clipper/web/` (2,125 lines),
`deploy/`, `clipper.example.toml` against `config.DEFAULTS`, the docs, and the 45 open GitHub issues.
Read-only: nothing changed, nothing run that calls a model or a service. Five reviewers covered the
package by area (core, pipeline, review/publish, services, extras); every finding was verified at
the cited line by its reviewer, and the ones marked **(checked)** were re-read by a second pass.
The 2026-10-28 audit asked "how easy is clipper to change and hand over"; this one asks two
questions: **is what landed since then right**, and **what should the next month be spent on**.

## Baseline

| | 2026-10-28 | 2026-11-03 |
|---|---|---|
| Python | 20,692 lines, 44 modules | 27,110 lines, 50 modules + record/, thumbs/, weekly/ packages |
| Subcommands | 39 | 44 in five groups (`clipper -h` is grouped) |
| Config | 449 keys / 34 sections; example covers 307 | 529 keys / 38 sections; example covers every key (test) |
| Tests / CI | none / none | 281 offline tests in 7 s; ruff + pytest on every push |
| `raise SystemExit` outside cli.py | 201 | 0 (test) |
| Raw `json.loads(read_text())` | ~150 | 11, none on a state file (test) |
| Functions over McCabe 15 | 22 (worst 51) | 13 (worst 24: `record/recording._video`) |
| Modules with no test naming them | all | 22 (bot, llm, segments, signals, transcribe, kick, tiktok, rumble, analytics, archive, costs, alerts, cardsync, ...) |
| Commits | | 342 since 2026-10-18; 65 on 10-30, 54 on 11-02 |

**Did the 10-28 fixes hold?** Yes, all of them, and they are tested rather than cosmetic:
stages table, `ClipperError` boundary, config validation and `clipper config get`, `--items` and
`time_arg`, exclusive mode flags, jsonstate everywhere, the bot's job loop, the war room's route
tables and js split, shorts.json merged per item, done markers on real outputs, the retake lock,
dry runs that write nothing. Two halves were not finished: nothing reads `last_error` (B2's second
half), and `draw.py` is shared by thumbs only (weekly and receipts still carry their own fonts,
fit and face crop, §1 of the old audit).

## Summary

The core is sound and the conventions are enforced by tests. The defects found are at the
**edges**: inputs nobody has typed wrong yet, states nobody has hit yet, and a class of
**"quietly broken"** bugs where a service keeps running but stops doing one of its jobs with
no signal. For a one-person operation that is the expensive class. The architecture risks are
the ones the roadmap already names (#64 one owner of the shared state) plus two it does not:
the publishing state has three sources of truth, and the project's surface area is growing
much faster than its tests and docs.

## 1. Bugs (ranked)

Severity: how much it costs when it happens × how likely. H/M/L.

| # | Where | What | Scenario | Sev | Fix |
|---|---|---|---|---|---|
| 1 | `record/finalize.py:109` **(checked)** | `gather_chat` does `json.loads` on every line of `live/chat.jsonl` with no guard | container killed mid-write (compose `stop_grace_period` 60 s < the 30 s × threads joins) or NAS power loss → last line cut short → finalize raises, recording marked failed, `clipper record finalize` fails the same way; the stream is stuck until someone hand-edits the file | **H** | skip undecodable lines, count them in `meta["recording"]` |
| 2 | `bot.py:296` **(checked)** | `warroom.tailnet_status` (two `tailscale` calls, 20 s timeout each) runs before `notify_loop`'s `while/try` | Tailscale wedged after sleep → `TimeoutExpired` kills the notify task; no DM for the bot's life, only an asyncio "never retrieved" line at GC | **M** | `try/except Exception: link = ""` |
| 3 | `bot.py:726` + `warroom.py:274` **(checked)** | `.bot.lock` is opened `a+` and never unlinked; Health reads `exists()` | bot task dies → war room says "Discord bot: running / quiet N min" forever | **M** | probe the byte lock non-blocking, or a heartbeat file (see §3) |
| 4 | `alerts.py:110-121` **(checked)** | `quiet_hours` / `digest_time` parsed with `_hm` every minute, never validated, no doctor check | an en dash or "9:00pm" → `ValueError` in `alerts.pending` → "notify pass failed" every minute, no DM ever | **M** | validate in `config.problems` |
| 5 | `record/recording.py:176` + `watch.py:132-167` | a crashed video thread sets `ended=True`; the watcher sees the stream still live and restarts it every poll | disk full on the share or a permission change → a new part and a webhook post every 30 s for the rest of the show (`alerts.py:92` webhook path has no cooldown) | **M** | back off after N consecutive crashes; repeats through the keyed outbox only |
| 6 | `schedule.py:383-386` **(checked)** | `queues` drops a passed Publish-at pin as stale once its stream is past `stale_days`, although `place_pinned` (:317) just said it "takes the next free slot" and `over_budget` exempted it | pin an old stream's segment for 20:00, bot down that evening → next replan: "stale", Kitsu Scheduled column blanked, the explicit pin never goes out | **M** | treat `override_text` as current in `queues` |
| 7 | `youtube.py:815-825` **(checked)** | the `done` dict merged under the lock has no `publish_at`; the hourly upload child read schedule.json once while a button press replans in-process | a replan moves Short S's slot while S uploads → schedule.json/Kitsu/digest say Fri, YouTube has Thu; `related_todo`/`source_links` use the wrong time; Fri's slot holds a video already out | **M** | include `publish_at` and `source` in `done` |
| 8 | `scoring.py:332-335` **(checked)** + `cut.py:276` | Pass B's `picks` are stored as returned; `unknown_ids` recorded but never acted on; duplicates kept | B names a window A rejected for third-party audio → it is cut and goes to review; an id not in A at all → `KeyError` traceback; same id twice → same number, second cut overwrites the first | **M** | filter to kept ids, dedupe, print what was dropped |
| 9 | `cli.py:393` **(checked)** | `clipper score` has no "already scored" guard (transcribe has one); the Pass A checkpoint is deleted after candidates.json is written | `clipper score <stream>` twice pays every Pass A window again | **M** cost | refuse without `--force` |
| 10 | `cut.py:273-286` + `thumbs/stage.py:268` | `cut_picks` re-encodes every pick every run; thumbs invalidates covers on the video's mtime | a hand `clipper cut` then `clipper thumbs` re-pays a vision call per clip, re-attaches every cover in Kitsu, and silently replaces clips in review | **M** cost + churn | skip a pick whose sidecar has the same in/out; key thumbs on sidecar times |
| 11 | `weekly/__init__.py:81` **(checked)** | `--render` drafts whenever the folder has no issue.json | Sunday `clipper weekly --render` without `--week` lands in next Saturday's folder, pays the Opus draft + 3 writer calls, leaves a one-stream junk issue that next Saturday's plain run keeps (HANDOFF:125 documents the workaround) | **M** cost | `render_only and not ij.exists()` → ClipperError |
| 12 | `cast.py:158-164` **(checked)** | for an enrolled person `choose_face` takes the best face ≥ `[cast] match` (0.5) against *their* refs and never asks whether it scores higher against someone else; the "looks like X, not Y" refusal runs only for non-enrolled people | a Jer grab hits a frame of Don (both enrolled): 0.68 vs Jer, 0.92 vs Don → added as Jer, auto-kept (≥ 0.65, clean label), offered to covers, the weekly and `known_figures` | **M** | in the enrolled branch, refuse when another enrolled person scores ≥ `other` or beats `score` |
| 13 | `config.py:796` **(checked)** | `stream_dir` never checks the stream exists | `clipper signals kick-nope` → raw `FileNotFoundError` traceback; `clipper config get x --stream kick-nope` prints the base config labelled as that stream | **M** usability | `ClipperError("no stream ... (clipper worker --status lists them)")` |
| 14 | `config.py` `check_choices` | effort keys (14 of them) never validated | `[meta] effort = "med"` loads, `preflight` passes, the first call fails with an API 400; for `openai:` models it silently becomes "medium" | **M** | pattern-check every `*effort*` key against the five values |
| 15 | `clipper.example.toml` + `doctor.py:104` | the example sets 524/525 keys uncommented at default; doctor's fix text says "copy clipper.example.toml to clipper.toml", the thing `setup.py:341` says it stopped doing | a fresh install following doctor pins every default; later DEFAULTS changes (a prompt_version bump) never reach it | **M** install | doctor → "run `clipper setup`"; comment the restated defaults |
| 16 | `youtube.py:638-647` | `kitsu_url` write sits in the same `try` as the playlist adds | a playlist 404/409/quota → Kitsu "YouTube URL" never written, nothing retries it; the Discord card's YouTube button and the war room link stay empty though schedule.json has `url` | **M/L** | own `try`; `describe` falls back to `entry["url"]`; backfill hourly |
| 17 | `youtube.py` / `bot.py:351` | nothing reads `last_error`; the failure DM is keyed `bg:upload` | a different entry failing the next hour inside `cooldown_hours` (6) sends nothing | **M/L** | show `last_error` in `upload --next` and the war room; put the failed keys in the DM key |
| 18 | `review.py:806-833` | a whole-stream sync reads Kitsu for tens of seconds, then rewrites review.json under the lock; a war-room decision made meanwhile is overwritten with the pre-decision status | approve c07 during a card refresh → c07 "pending" again → next replan drops it from the schedule; heals within `refresh_minutes` | **L/M** | keep the entry whose `updated_at` is newer |
| 19 | `llm.py:159` | a model string with no `[llm] prices` entry is costed at $0, no warning, no doctor check | a show sets a dated model id → `clipper costs` under-reports for weeks | **L/M** | longest-prefix price match; doctor warns per configured model |
| 20 | `services.py:103` | `services restart recorder` reads `RECORD_SSH_HOST` (default "nas"), `RECORD_DIR`, `RECORD_SUDO`: documented only in deploy.sh, not in SETUP, doctor or `common.ps1`'s key list | from the bot or war room it sshes to "nas" and fails with a bare ssh error | **L/M** | `[record] ssh_host` / `compose_dir`, doctor-checked |
| 21 | `doctor.py` | no check that `[warroom] host` off localhost has a token (the bot refuses to start, `bot.py:761` catches only `OSError` → restart loop); no check of the `[notify]` time strings (#4) | | **M** | two checks |
| 22 | `xpost.py:404-410` | a tweet call that times out after X created the post is recorded failed and auto-retried (3 tries) | the same Short posted and billed twice | **L** | status "unknown", listed for a hand check |
| 23 | `youtube.py:792-798` | `at_once` recomputed per entry while `upload_due` decided "missed" with the run's start | a run of long uploads carries a later entry past `slot - min_lead` → it goes **public at once** with `notify_subscribers` | **L** | pass the run's `now` |
| 24 | `jsonstate.py:109-112` **(checked)** | two waiters that both see a >120 s lock can both unlink and both create it; `locked.__exit__` unlinks without checking ownership | two holders after one stale lock | **L** | token in the lock file, unlink only your own (as `retake.Lock` does) |
| 25 | `retake.py:528-540` | `pending()` computed before the lock | ms window: two runs re-plan the same note (a model call, a re-cut) | **L** | re-check inside the lock |
| 26 | `schedule.py:64` | `budget_hours = 0` → `ZeroDivisionError` inside every replan (each button press, the hourly job) | | **L** | doctor `> 0` |
| 27 | `schedule.py:396` vs `youtube.py:717` | `fill` accepts any slot `> now`; upload calls anything within `min_lead_minutes` missed | approved 11:45 → takes 12:00 → "missed" → moved: one slot wasted, a Kitsu time that never happens | **L** | `fill` skips slots before `now + min_lead` |
| 28 | `scoring.py:247,262` | pick ids are positional (`t{n}`, `w{rank}`) while `carry_numbers` keys by id | signals rerun with new params, then re-score → `t03` is a different moment, keeps number 5, `v05_t03` in Kitsu means a different clip | **L/M** | hash windows.json into candidates.json and refuse to re-score over a changed one |
| 29 | `scoring.py:376-388` | Pass A checkpoint written only after summary, scan and A all return | scan thread fails after A finished → paid A lost | **L** | checkpoint as soon as `f_a.result()` returns |
| 30 | `bot.py:301-307` | DMs sent one at a time, `alerts.done` only after all succeed | second DM rate-limited → first re-sent every minute | **L** | `done` per message |
| 31 | `record/watch.py:71-79` | adopted recordings get `status = "paused"` in memory, not saved | state.json and the war room chip say "recording" until the next save | **L** | `st.save(d)` |
| 32 | `warroom.py:1305-1309` | Range parsing has no 416 path | `bytes=5000-` past EOF → negative Content-Length; `bytes=abc-` → 500 | **L** | 416 |
| 33 | `warroom.py:1445-1455` | warm-up, low-res and prewarm threads start before the server binds | port busy → "war room not started" yet it re-encodes low-res copies every 5 min | **L** | bind first |
| 34 | `review.js:5-16` + `core.js:33` | an in-flight 15 s overview resolves after an action and overwrites the patched row | approve → "approved" → "waiting" for a second → correct on the next tick | **L** | sequence the responses |
| 35 | `meta.py:336,207`, `shorts.py:656` | three text-mask Bleepers; `mode = "off"` with `profanity = true` masks titles/SRT while audio isn't bleeped; `--captions-only` masks nothing | two different SRTs for one stream | **L** | one `bleep.for_text(cfg)` |
| 36 | `cli.py:423` vs `:861` | `--items` only reaches `cut_segments` | `clipper cut <s> picks --items t03` silently cuts every pick | **L** | pass or refuse |
| 37 | `shorts.py:144` | `min(b, a + max_s)` runs after `snap_window` | a 75 s model window with no sentence end inside 59 s is hard-cut at 59.000 mid-word, captions included | **L** | clamp before snapping |
| 38 | `kick.py:83-129`, `segments.py:515`, `meta.py:169`, `weekly/gather.py:27`, `prompts.py:84`, `media.py:12`, `cli.py:119`, `pick.py:247` | `RuntimeError`/`ValueError`/`IndexError`/`KeyError`/`FileNotFoundError` instead of `ClipperError` | a VOD not in the channel list, a music-only stream, `--week 11/07`, a prompt with an unfilled `{x}`, no ffprobe, `--from garbage` → tracebacks | **L** | ClipperError each |
| 39 | `cli.py:1298` | `news context --show` shares dest `show` with the global flag | `clipper --show kino news context 5` drops the pin silently | **L** | `dest="context_show"` or read `cfg["show"]` |
| 40 | `config.py:918` | a non-table top-level key in clipper.toml crashes `load` (`ValueError`) instead of warning; `[shows] foo = 1` → `AttributeError` | every command including doctor | **L** | skip non-dict sections |
| 41 | `worker.py:415` | `watch()` passes the base cfg to `run_stream`; `clipper run` passes a `for_stream` one | a `[shows.x.worker]` override is honoured by hand runs, ignored by the worker (none exists today) | **L** | resolve in `run_stream` |
| 42 | `cast.py:267,629` | same-photo check runs on data loaded before the lock and only against photos still in the index; `drop` forgets the embedding | a dropped Commons photo comes back as "new" under a different file name; two concurrent adds both pay a label | **L** | keep dropped embeddings; check inside the lock |
| 43 | `cast.py:388`, `:561` | source resolved before `names` is known non-empty; `/videos/videos` for a channel saved with `/videos` | an old stream with a cleaned-up source fails the `cast` stage every run; a channel yields nothing | **L** | two one-liners |
| 44 | `weekly/draft.py:361`, `render.py:553,650` | `check_issue` checks key presence only; the renderer indexes `headline[1]`, `max()` on an empty cast | a one-line headline or a pre-show-only week → traceback after the draft is paid | **L** | shape checks per field |
| 45 | `weekly/gather.py:44` | `next_issue_number` is max + 1 | `--week <old> --draft` renumbers issue 02 as 04 | **L** | keep the existing number |
| 46 | `weekly/__init__.py:292-318`, `receipts.py:82`, `archive.py:66,197`, `alerts.py:136`, `setup.py:156`, `finalize.py:142`, `news.py:645` | raw `write_text(json.dumps())` or a private tmp+replace writer instead of `jsonstate.write` | not atomic; the read convention is tested, the write one isn't | **L** | `jsonstate.write`; a test like `test_jsonstate_reads` for writes |
| 47 | `transcribe.py:255`, `cut.py:41`, `faces.py:419` | audio.wav, keyframes.json, faces.json cached by existence only | a replaced `source.*` keeps stale caches: "the seek missed it", faces off | **L** | store source name+size |
| 48 | `segments.py:556` / `cut.py:243` | compilation file is `slug(topic).mp4` | two topics with the same slug overwrite each other | **L** | suffix duplicates |
| 49 | `llm.py:339-351`, `:250` | last-attempt 400 "reasoning" reply falls through to `r.json()["choices"]` → KeyError; `preflight` misses `RateLimitError`/`APIConnectionError` → raw SDK traceback | | **L** | |
| 50 | `perform.py:811`, `receipts.py:109,368` | unchecked `requests.get` body written as video; words.jsonl read line by line with no guard though `transcript.load_words` is imported | | **L** | |

Operational notes, not bugs: `Stop-ClipperTask` force-kills the worker without `Run.release()`, so a
restarted worker waits `[worker] stale_minutes` (10) to resume (the task script's header says "the
next start resumes"); `bot.py:341` still buffers a child's output until it exits, so a 30-minute
upload shows nothing in the log; compose's HEALTHCHECK marks the recorder unhealthy but nothing
restarts it; `?key=` stays in the phone's address bar after the cookie is set; the suppressed old
flag spellings (`--ids`, `--keys`, `--item`, `--segments`, `--max`) are still registered.

Clean, by the reviewers' reading: jsonstate (apart from #24), errors, states, timefmt, costs, llm
routing and retries, stages, the `Run` takeover, ytdl, media, transcript, modal_app, speakers,
draw, enquirer/classic/editor, faces, bleep spans at cut edges, DST/timezone in the scheduler,
quota accounting, `mix_shows`, Kitsu sync's status handling, X approval and 24 h counting, the war
room's auth (constant-time token, SameSite cookie, no CORS, same-origin POSTs, `parents` checks on
every file route), the web UI's escaping (every state string through `esc()`), the recorder's
part/gap/clock logic, pre-show grabs, news OFF really being off, setup's key handling.

## 2. Architecture: what the shape is now

```
laptop (Windows, scheduled tasks)        NAS 192.168.0.155 (Docker)        cloud
  clipper bot ─ discord.py client          recorder (clipper record)        Modal: WhisperX, archive
    ├ jobs loop: schedule+upload hourly,   Kitsu :8090 (the record)         Claude API
    │   x, cast, news, analytics, retake   cut container (clipper cut)      YouTube, X, Brave
    ├ war room (thread, :8765, tailnet)    streams share (every state file)
    └ child `clipper ...` processes        photos share
  clipper worker ─ runs stages per recording as child processes
  Claude Code sessions ─ the operator's console (and most hand edits)
```

Four things write the same files (schedule.json, review.json, retakes.json, the per-stream JSONs):
the bot in-process (every button press runs `schedule.run`), the war room thread, the bot's child
processes (hourly `clipper schedule` and `clipper upload`), and the worker and its children. They
coordinate with `jsonstate`'s file locks, which is sound per write, but every multi-second
read-then-act window is a race the code heals "next hour" rather than prevents: findings #7, #18,
#25, #24 are all this one shape. Issue #64 (`clipper serve`: one process owns review.json and
schedule.json, everything else calls it) is the real fix and the roadmap already has it as the
keystone of the NAS hub (#9, #12, #14). Nothing on the NAS hub has moved since 10-24 except the
recorder, Kitsu and the cut container.

**Observations, in order of how much they will cost if left:**

1. **"Quietly broken" is the dominant failure mode.** Findings #2, #3, #4, #5, #16, #17 and the
   recorder's unhealthy-but-running container all share a shape: a service keeps running, one of
   its jobs stops, and nothing says so. The DM outbox, the war room Health panel and doctor are
   three partial answers. One pattern would cover them: every long-running thing (bot, each bot
   job, worker, recorder, war room) writes a small heartbeat file `{name, pid, host, at, last_ok,
   last_error}` into `<streams>/.health/`, and doctor and the war room read only those. The
   recorder already does this (`.record/status.json`); the bot and worker don't.
2. **Publishing state has three truths and no reconciliation.** YouTube, `schedule.json` and the
   Kitsu columns are each written once, forward, by `upload_one`/`finish`/`push_to_kitsu`. Each
   follow-up field (captions, playlist, links, related, gone_at, last_error) adds another ad-hoc
   `mark`. Findings #7, #16, #17 are the symptoms. A single `reconcile` step in the hourly run
   (for every uploaded entry: read YouTube's status/publishAt, fix the schedule entry and the Kitsu
   URL/Scheduled columns, retry whatever is missing) would replace the five backfills in
   `youtube.py` and make `upload --links/--captions/--playlists/--related` one command.
3. **The bot is three programs.** `clipper bot` is a Discord client, the job scheduler and the war
   room host. A scheduler change needs a Discord restart; a Discord outage takes the war room
   with it; the war room can't move to the NAS while it lives in the bot. The cheap first step
   toward #64 is to split `clipper serve` (jobs loop + war room, no discord.py) out of `clipper
   bot` (cards and buttons only, calling `actions`), with `[warroom] with_bot` gone. That is a
   move of existing code, not new code, and it unblocks #9/#12.
4. **Three review surfaces for one reviewer.** Kitsu (the record), Discord cards (60 s excerpts,
   buttons, a thread per stream) and the war room (everything, undo, bulk, cover editor, faces,
   cast, news). bot.py + notify.py + cardsync.py are ~1,270 lines that exist to mirror Kitsu
   state into Discord messages, and the card refresh is one of the race partners in #18. If the
   war room is where decisions actually get made now, the cards could become notifications only
   (one message per stream: "N items ready", link to the war room) and the bot shrinks to DMs,
   #news and #weekly. That is the biggest single simplification available and it is a product
   question, not a code one (see Q3).
5. **Surface area is outrunning tests and docs.** 44 commands, 529 keys, 70 prompt files, 45 open
   issues of which about 35 are new features (story, investigate, swarm, blind items, crisis
   mode, live clips, the six edit issues, the Udder Truth, the Posts family, four archives,
   Instagram, playlists). 342 commits in 16 days. Meanwhile 22 modules have no test, including
   `llm.py` (the one seam every model call goes through), `bot.py`, `segments.py`, `signals.py`,
   `transcribe.py` and `kick.py`, and the three functions where this audit found the publishing
   bugs (`upload_due`, `upload_one`, `review.sync`) have none. The pure-logic tests that exist are
   good; the gap is the I/O-adjacent orchestration, which is testable with a fake YouTube client
   and a tmp streams folder.
6. **Cost safety is uneven.** `transcribe` refuses a rerun; `score` (#9), `cut`+`thumbs` (#10)
   and `weekly --render` (#11) don't; an unpriced model logs $0 (#19). The rule "say what a rerun
   costs" lives in CLAUDE.md and in the operator's head, not in the commands. A `--force`
   convention on every paid stage, and a one-line "this will call <model> N times (~$x)" before
   it, would make the rule mechanical.
7. **Input validation stops one step short.** The config layer refuses unknown keys and bad
   enums, but a bad stream id (#13), a bad time (#38), a bad effort (#14), a stray top-level key
   (#40), a bad `[notify]` time string (#4) and a missing prompt placeholder (#38) all still reach
   the operator as a traceback or, worse, a silent wrong answer. Each is a one-line check at a
   place that already exists.
8. **Docs.** HANDOFF.md is 357 lines against its own 300 limit, and its "User decisions" section
   (the taste layer, the thing the user said to treat like footage) is interleaved with "State
   now" lines that go stale weekly. The 10-28 audit's "one-page state files table" was never
   written. `clipper.example.toml` is a full default dump that doctor still tells people to copy
   (#15). Docs/log is 2,000 lines and growing by ~100 a day, which is fine as a diary.
9. **Deployability is a stated goal with no second install yet.** `clipper setup` phase 1 passed
   in a sandbox on 10-28; #7 (MacBook) and #46 (desktop, the second channel) have waited since
   10-20/10-23. Every week of new features before the first clean install makes that install
   harder (#9 above is the proof: the recorder's ssh settings never made it into config).
10. **Shared helpers stopped at thumbs.** `draw.py` is imported by thumbs only; weekly and
    receipts have their own `Fonts`/`wrap`/`fit`/face-crop and three font lists in config; four
    private atomic writers and two TOML value writers remain. Not urgent, but it is the kind of
    drift the 10-28 audit was about.

**What is good and should be kept:** the stages table; the config layering with `clipper config
get`; the ClipperError boundary; jsonstate; prompts as versioned files pinned by config; the cost
ledger; the war room's threat model and its per-window tokens; the recorder's small, tested state
machine with a restartable finalize; the structural tests (no raw reads, no SystemExit, every
command grouped, every default documented, every stage row a real command); the `--dry-run`
discipline; the retake planner's refusal to touch layout.

## 3. Proposed order of work (for discussion, see the questions)

1. **Liveness and the quiet failures** (#1-#5, #16, #17, #21): a day. Chat-line guard, notify
   loop guard, heartbeat files for bot/worker read by Health and doctor, `[notify]` and
   `[warroom]` validation, `last_error` surfaced, recorder restart backoff.
2. **Cost guards** (#9, #10, #11, #19): half a day. `--force` on score/cut/thumbs/weekly, price
   warning in doctor.
3. **Scheduler and upload consistency** (#6, #7, #18, #22, #23, #26, #27): a day, plus the tests
   for `upload_due`/`upload_one`/`review.sync` that don't exist.
4. **Input edges** (#13, #14, #38, #39, #40): half a day, mostly one-liners.
5. **Pass B hygiene and positional ids** (#8, #28): half a day.
6. **Cast and weekly** (#12, #42-#45): half a day; #12 before the next `cast auto` keeps a wrong
   face.
7. **Split `clipper serve` out of `clipper bot`** (observation 3): the first real step of #64 and
   the NAS hub. One or two days, no new behaviour.
8. **Decide the review surface** (observation 4): a conversation, then possibly a large deletion.
9. **Docs**: HANDOFF trim, a `docs/decisions.md` for the taste layer, the state-files table,
   example file commented, doctor's fix text.

Items 1-6 are about four days and could go as one PR per line. Item 7 is the architectural one.
