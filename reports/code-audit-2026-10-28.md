# Clipper code audit (2026-10-28): ease of use and maintainability

Scope: all of `clipper/` (44 modules, 20,692 lines), `clipper/web/warroom.html`, `deploy/`,
`clipper.example.toml` against `config.DEFAULTS`, README/SETUP/HANDOFF/CLAUDE, and repo hygiene.
Read-only: no files were changed and nothing that calls a model or service was run. Five reviewers
covered the package by area, plus cross-cutting checks (ruff complexity, vulture, greps across the
package). Findings marked **(verified)** were re-read at the cited line by a second pass. The rest
were checked once by the reviewer who found them. The two earlier audits (10-23, 10-24) were bug
hunts. This one asks a different question: **how easy is clipper to use, change and hand to
someone else?**

## Baseline numbers

| | |
|---|---|
| Python | 20,692 lines in 44 modules. The biggest are weekly 1,462, thumbs 1,309, warroom 1,177, cli 973, record 919 |
| Subcommands | 39 top-level (62 parsers with sub-actions), all in one 428-line `cli.main` |
| Config | 449 keys in 34 sections (`DEFAULTS` is 624 lines); `clipper.example.toml` covers 307 of them |
| Tests / CI | none / none |
| Lint | `uvx ruff check clipper` is clean, but under a user-level rule set that isn't in the repo |
| Complexity | 22 functions over McCabe 15. Worst: `warroom.make_handler` 51, `shorts.run` 30, `schedule.plan` 30, `doctor.check_online` 27, `record.finalize` 25 |
| Error style | 201 `raise SystemExit` in library code; 20 `except (Exception, SystemExit)` guards to survive them |
| Shared JSON | about 150 raw `json.loads(path.read_text())` against 11 `jsonstate.read` |
| Dead code | almost none (vulture: 2 unused locals). The code is used; it is just repeated |

## Summary

The pipeline works, and the earlier audits' shared-state race fixes did land: every writer of
schedule.json, review.json, retakes.json and discord*.json now goes through `jsonstate`. The code
is hard to maintain for five reasons, and each has one structural fix:

1. **No shared building blocks.** The same helper is written again in each module: `hms` 7 times,
   ffprobe duration 7, the yt-dlp signed-in retry 3, font loading 4, face crop 3, item-key parsing
   about 40 string operations, and the Kitsu Shot/Sequence column update 7. They have already
   drifted: different rounding, different error handling, two brand reds. The fix is about six
   small modules: `media`, `ytdl`, `timefmt`, `draw`, `transcript`/`prompts`, `states`.
2. **No stage registry.** A new stage touches 6-8 places: a `cmd_x` in cli, the parser block,
   `worker.STAGES`, `FAST_TWIN`, `AFTER_CUT`, `DONE_MARKERS`, `stage_args`, and doctor's prompt
   list. Each stage also decides "done" in its own way. One table of (name, inputs, outputs,
   marker, prompts, argv) would drive cli, worker, `clipper run`, doctor and the README.
3. **`SystemExit` is the library's error type.** That is why the bot and the war room have to
   wrap every call, why a typo in one stream's config once crash-looped the bot (10-24 A1), and
   why war-room GET errors read "SystemExit". The fix: a `ClipperError(Exception)` that only
   `cli.main` turns into an exit.
4. **Config is big and only partly validated.** Unknown keys in `clipper.toml` and `[shows.*]`
   pass silently. Enum values aren't checked (`[bleep] mode = "Bleep"` turns bleeping **off**).
   The example file is missing 142 keys, and `clipper setup` copies the whole example into a new
   `clipper.toml`, which pins about 300 defaults on every new install. Nothing shows which layer
   a value came from.
5. **No safety net.** No tests, no CI, and the lint rules live only on this laptop. The riskiest
   functions (`cut_compilations`, `schedule.plan`, `review.publish`, `record.finalize`) are also
   the most complex and the least isolated from I/O.

## Bugs found along the way

These are real behaviour problems, not style issues, and most are a few lines each.

| # | Where | What happens | Fix | Sev |
|---|---|---|---|---|
| B1 | `bot.py:179`, `:207-229` **(verified)** | Auto-upload, TikTok, analytics and the auto-retake check are scheduled *inside* `refresh_cards`. With `[discord] refresh_minutes = 0` ("card refresh off"), all of them stop silently | A separate scheduler loop (see §6) | High |
| B2 | `youtube.py:688-693`, `bot.py:305` **(verified)** | A failed upload, or a quota stop, prints a line and exits 0. The bot only sends a DM on a non-zero exit, so the user is never told. Nothing reads `last_error` | Exit 1 when `failed`; show `last_error` in the digest, the war room and `upload --next` | High |
| B3 | `shorts.py:561,682,691` **(verified)** | shorts.json is read once and rewritten whole after each render, so a retake's `window_override` saved during a running `shorts` stage is lost | `jsonstate.merge(state_path, state, [key])` per item | High |
| B4 | `cli.py:953` vs `for_stream` (`config.py:826`) **(verified)** | The global `--show` is dropped by every stream stage (score, segments, cut, meta re-resolve the show from the channel), though the help says it overrides | Keep an already-resolved show in `for_stream`, or refuse `--show` together with a stream | Med |
| B5 | `worker.py:98-102` **(verified)** | `read_json` turns a mid-replace PermissionError into `{}`, then `Run.acquire` saves rebuilt data over run.json, losing `clips_ready`/`finished`. This is the same bug class e15900f fixed elsewhere | `jsonstate.read(path, {})` | Med |
| B6 | `doctor.py:627` **(verified)** | The guild_id/allowed_user_ids check is gated on `dc.get("enabled", True)`, but `[discord] enabled` defaults to False, so `doctor --role bot` skips the check the bot refuses to start without. `review_channel_id` is never checked | Gate on `role`, not `enabled`; add `review_channel_id` | Med |
| B7 | `bleep.py:19,30` **(verified)** | `mode` is compared case-sensitively and never validated. Any value other than "bleep"/"mute" silently means slurs go out unbleeped | Validate enum keys at load (§4) | Med |
| B8 | `bot.py:316-318` | `_auto_upload` skips the upload when `clipper schedule` fails, so one Kitsu outage misses every slot that hour (10-24 B1, still open) | Always run upload; alert on the replan failure separately | Med |
| B9 | `youtube.py:649`, `schedule.py:159-162` | `upload --dry-run` and `schedule --dry-run` run `review.sync`, which writes review.json and can post Kitsu comments and re-render covers | Skip the sync on dry-run | Med |
| B10 | `retake.py:505` vs `:510`, `:476` | `finish_pending` runs outside the retake lock, so two runs can both publish one pending retake. `__exit__` deletes a lock it may not own | Move it inside the lock; reuse `jsonstate.locked` with a heartbeat | Med |
| B11 | `warroom.py:871` | GET errors (bad ref on `/api/cover`, `/watch`) show "SystemExit (details in the bot log)". POST shows the real message | Catch SystemExit as a 400 like `do_POST` does | Low |
| B12 | `warroom.html` `refresh()` | The 15 s refresh rebuilds `#queueBar`, so the search box, selects and date pickers lose focus mid-typing | Render only what changed, or skip while an input has focus | Low |
| B13 | `warroom.py` (no Host check) | With no war-room token set (the default local install), a DNS-rebinding page counts as same-origin. This laptop is covered because the tailnet token is set | Refuse any request whose Host isn't localhost/127.0.0.1/the tailnet name | Med |
| B14 | `cut.py:177,254`, `worker.py:58` | The "cut" and "clips" done markers are directories created before any cutting, so a crashed cut with no run.json counts as done | Markers on `segments/*.json`, `clips/*.json` | Low |
| B15 | `cut.py:228`, `signals.py:253`, `transcribe.py:170`, `speakers.py:104` | Compilation mp4s and the windows/diarization/speakers JSONs are written in place, not tmp+replace (against the #75 convention) | `jsonstate.write`; tmp+replace for the mp4 | Low |
| B16 | `README.md:66` **(verified)** | `clipper preshow fetch <url> --show <key>` fails: `--show` is a top-level option (`clipper --show <key> preshow fetch ...`) | Fix the README | Low |

## 1. Shared building blocks (duplication)

The biggest maintainability cost. One change ("ffprobe timed out", "Kitsu column renamed")
currently means finding every copy.

| Helper | Copies today | Proposed home |
|---|---|---|
| `hms` / `mmss` / time parsing | `pick:94`, `receipts:93`, `segments:64`, `signals:197`, `speakers:157`, `actions:437`, `pick.parse_time` (imported by cli/preshow). Rounding differs | `clipper/timefmt.py` |
| ffprobe duration / size, frame grab, source picker | `narrate:61`, `perform:92`, `notify:138`, `preshow:259`, `receipts:477`, `record:554`, `cut:137`, `shorts:430`; grabs at `thumbs:154`, `shorts:173,332`; three source pickers that disagree (`transcribe:39` doesn't skip `.ytdl`/`Frag`, `cut.source_file` does) | `clipper/media.py`: `probe`, `duration`, `size`, `grab_frame`, `source_file`, `run_ffmpeg` |
| yt-dlp runner + signed-in cookie retry | `archive:198-209`, `receipts:290-299`, `preshow:155`; preshow and archive import each other | `clipper/ytdl.py`: `run(args, cfg, retry_on="age")` |
| Transcript loading, `snap`, `Prompts`, `Claude` | Live in `scoring.py` and are imported by about 12 modules; `segments.hms`/`slug` are imported by 5 | `clipper/transcript.py`, `clipper/prompts.py` |
| Fonts, fit/wrap, face crop, palette | `thumbs:372,510,527,535,562`, `weekly:670,700,725,563,610`, `receipts:244`; font lists in 4 places in config (already drifted); RED (214,18,32) vs (226,30,38) | `clipper/draw.py` + one `[fonts]` section |
| Item keys (`stream/key`, `v04_t04`, `comp-…`), path slashes | `split("/",1)` ×12, `split("_")[0]` ×11, `"comp-"+stem` ×5, `replace("\\","/")` ×11; three resolvers (`actions.resolve`, `rumble.resolve`, `youtube.related`) | `clipper/states.py`: `ItemKey`, one `resolve()` |
| Kitsu column write (Shot vs Sequence switch) | `review:535,710,743`, `actions:103`, `schedule:356`, `rumble:227`, `youtube:589` | `review.set_columns(gazu, cfg, st, {col: value})` |
| Decision dispatch, replan + note, background `clipper` child | bot `do` (`bot:388`) and war room `_apply` (`warroom:436`); replan note ×4; retake start ×2 (the war room's skips the bot's queue) | `actions.decide(cfg, ref, action, payload, who)` and `actions.run_bg()` |
| Discord sending | `notify.Rest`, `record.Alerts` (used by worker and tiktok), the `alerts` outbox, discord.py | One `alerts.send` path; move `Alerts` out of record.py |
| Shared JSON reads | about 150 raw reads, including schedule.json at `youtube:248,604,616,768,802` and `schedule:189`; own loaders in `warroom:49` (returns `{}` on PermissionError, so the war room can show an empty schedule), `actions:64`, `retake:42`, `pick:138`, `weekly:108`; own atomic writers in `alerts:104`, `receipts:72`, `archive:62`, `record:68`, `setup:155` | `jsonstate.read`/`write` everywhere, with a test that greps for `json.loads(` on known state file names |
| Misc | `ZoneInfo(cfg[...])` ×23, logging setup ×5, secret lookup (env, else file) ×3, `truthy` ×2, "hosts" regex ×3, `read_text()` without encoding (`youtube:464,673,850`, `cut:47`) | `config.tz(cfg)`, `log.setup()`, `secrets.get()`, `style.has_hosts()` |

## 2. Errors

- **`ClipperError` instead of `SystemExit`** (201 raises; heaviest in warroom 21, thumbs 19,
  actions 15, youtube 11, tiktok 11). `notify.py:92` turns any Discord HTTP error into SystemExit,
  which `notify.py:209` and `cardsync.py:111` then catch to mean "message deleted". `llm.py:313,317`
  raises it inside thread pools. With one exception class, `cli.main` has the only handler and the
  bot and war room can catch `ClipperError` normally. **High.**
- The same handler gives friendly messages for the common mistakes: a mistyped stream id is
  currently a raw FileNotFoundError (`stream_dir`, `config.py:666`); a missing `clipper.toml`
  silently runs on defaults relative to the current folder (`config.py:732`). **Medium.**
- Retry policies differ for no reason: llm retries 5xx and network errors, notify only 429, and
  youtube only 5xx `HttpError`. Because of the last one, a connection drop on the final chunk
  re-uploads the video next hour (#77 M5 residue). **Medium.**

## 3. CLI

- **`cli.main` is 428 lines** (`cli.py:542-969`). Give each module a `register(sub)` and drive
  the stage commands from the registry. Repeated argument help (stream help ×15) becomes
  `add_stream(p)`. **High.**
- **The same idea has different flag names.** Item selection is `--ids` (comma string), `--ids`
  (nargs+), `--keys`, `--items`, `--item` or `--segments`. `--from` is a time in preshow/pick but a
  stage in `run`. `--at` is seconds in shorts but H:MM:SS in pick. `--max` vs `--limit`.
  `review status --sync` vs `--no-sync` elsewhere. `schedule --show` and `analytics --show` are
  hidden aliases for `--list` that clash with the global `--show KEY`. Fix: one `--items`, one
  `parse_time` for every time flag, `--no-sync` everywhere, drop the aliases. **Medium.**
- **Mode flags aren't exclusive.** On upload, rumble, tiktok and thumbs a hidden if-order decides
  (e.g. `rumble --status --done X` marks X done). Use `add_mutually_exclusive_group`. `tiktok
  --dry-run` silently also means "don't check status". **Medium.**
- **`clipper -h` is a 39-line wall** that mixes pipeline stages, services (bot, worker, record,
  warroom) and side projects (narrate, perform, receipts, archive, weekly). A grouped epilog
  ("Pipeline / Review and publish / Services / Extras / Setup") would make it scannable. `run
  --from` should list its choices. **Medium.**
- **Naming:** `notify.py` posts review cards and `[worker] notify` means cards, but the `[notify]`
  section configures DMs in `alerts.py`. Rename one side (`cards.py`, or `[alerts]`). **Medium.**

## 4. Config

- **Validate everything at load.** Unknown keys are checked in `clipper.local.toml` and a stream's
  `config.toml`, but not in `clipper.toml` or `[shows.*]` (`config.py:737,806`; #77 L10). Add a
  small `CHOICES` table for enum keys (`bleep.mode`, transcribe/cut `backend`, `thumbnail.style`,
  `clip_order`, `publish`, `quiet_hours`/`digest_time` formats). Warn on `CLIPPER_*` env vars that
  match no key. One residue of L9: a key whose default is None (`preshow.teasers`) gets the raw
  env string, so "false" is truthy. **High** (B7 is the consequence).
- **Make the example file small and stop `setup` copying it whole.** `setup.py:350` copies
  `clipper.example.toml` (about 320 restated defaults) into a new `clipper.toml`, and each one
  becomes an override that later `DEFAULTS` changes never reach. The example should hold only
  required or per-install keys, with everything else commented, plus a test that every `DEFAULTS`
  key is at least mentioned. 142 are missing now: shorts 22, receipts 21, weekly 17, perform 13,
  transcribe 10, pick 9, narrate 8, archive 7, paths 6. The README says the example "documents
  every key". **High.**
- **`clipper config get [--stream X] section.key`**: print the value and which layer set it
  (default, clipper.toml, show, stream config.toml, clipper.local.toml, env). Five layers plus
  show and stream is too many to trace by hand. **Medium.**
- **Fix bad defaults:** Shorts are still 90/179 s with `window = "clip"` (`config.py:436-438` and
  the example; only this laptop's clipper.toml says 15/59), so a fresh install makes the wrong
  Shorts (#77 L16). `perform.lipsync_engine` defaults to `latentsync`, but the user chose Sync.
  `paths.costs` has its comment on the `warroom_undo` line (`config.py:40`). Unit naming is mixed
  (`poll_s` / `poll_minutes` / `auto_hours`) and so is prefix/suffix naming (`model_a` /
  `locate_model`); the prompt-version keys too (`prompt_version`, `enquirer_prompt_version`,
  `ask_prompt_version`). **Medium/Low.**
- **Layout as data.** Every cover and magazine pixel is a literal: the enquirer masthead, band,
  teasers, face and inset boxes (`thumbs.py:704-796`), vertical covers (`:816-896`), weekly geometry
  and palette (`weekly.py:56-70`, `p_cover`), and fixed copy ("CAN YOU BELIEVE IT?", "SOURCES SAY",
  "LOLCOW OF THE WEEK", the barcode digits). These are what get tuned most. A per-show
  `layouts/<show>/enquirer.toml` and `weekly/style.toml` would make a cover tweak a TOML edit.
  `weekly.py:1331` switches to square by rebinding module globals `W`/`CW`; pass the canvas size
  to `Renderer` instead. Receipts are hardcoded to one subject ("The Quartering Receipts", the
  Hambly tags, `receipts.py:210,215,567`); make those per-subject keys. **Medium.**

## 5. State model

- **No status enums.** The literal `"uploaded"` appears 38 times in 13 modules. review.json
  `decision` (approved / changes_requested / rejected / pending) is compared 15 times, and an
  unknown Kitsu status passes straight through (`review.py:752`). schedule entries have `source`
  auto / pinned / asap / studio; platforms have their own status sets; retakes.json has `result`
  and `publish`. The same item is a "pick" in review/actions and a "clip" in schedule/youtube
  (`schedule.py:34-35` remaps it). Fix: `clipper/states.py` with `StrEnum`s, one kind map and
  `ItemKey`. **Medium.**
- **Positional keys** (#77 L13, still open): `c{i:02d}` (`review.py:229`), `v{i:02d}_…`
  (`shorts.py:584`, `pick.py:320,369`). Rerunning `score` rewrites `pass_b` and renumbers every
  vNN, which drops hand picks. Store each pick's key when it's created. **Medium.**
- **Write a one-page "state files" table** (file, owner, writers, readers, fields, lock) into
  the README. Today it can only be pieced together from module docstrings. **Low.**

## 6. Big modules and functions to split

| Module | What it mixes | Split |
|---|---|---|
| `record.py` (919) | alerts, Kick client, HLS parsing, video/chat threads, finalize (139 lines), watcher (9-branch `tick`) | `record/{hls,chat,finalize,watch}.py`; `Alerts` → alerts.py. Makes finalize restartable and testable (and fixes L17 on the way) |
| `thumbs.py` (1,309) | frame grabs, model picks (`pick` and `pick_enquirer` are the same code twice), classic compose, PIL helpers, enquirer/vertical layout, stage/state/editor | `thumbs/{frames,pick,enquirer,stage}.py` + `draw.py` |
| `weekly.py` (1,462) | gather/stats, draft + writer calls, faces, a 590-line `Renderer` (`p_cover` alone 224 lines). Overflow is read back by regex from a warning string (`:489`) | `weekly/{gather,draft,faces,render}.py`; `p_article` returns overflow px |
| `bot.py` | the hourly loop is hidden in `refresh_cards`; `register_commands` is 111 lines; child output only appears when the child exits (`communicate()`), so a 30-minute upload shows nothing until it ends; docstring still says uploads are a separate job | a table-driven `scheduler_loop` of `(name, every_s, guard, argv)`; stream child output line by line |
| `warroom.py` (1,177) + `warroom.html` (105 KB) | two long if/elif route chains (`make_handler` complexity 51); about 20 mutable JS globals, 6 timers, 34 `innerHTML` writes | a `ROUTES` table; split into `warroom.css` + `warroom.js` (ES modules, no build) with a panel registry `{id, url, every, render}` |
| `review.publish` (134 lines), `schedule.plan` (107, nested closures sharing mutable state), `shorts.run` (164, nesting 6), `meta.run` (131), `pick.run` (111), `doctor.check_online` (115) | | named steps (`render_previews` / `push_previews` / `fill_columns` / ...); `short_window()` and `choose_layout()` out of `shorts.run` |

Also: every bot button press runs `schedule.run` → `push_to_kitsu`, which does one Kitsu GET per
entity in every stream under `bot.lock`, so other buttons queue behind it (`schedule.py:342-365`).
Write only the changed values instead.

## 7. Prompts

- Inline prompt with no file or version: the Shorts layout labeller (`shorts.py:203-210`). Move it
  to `prompts/shorts_layout_v1.md` with a config key. Retry and feedback wording is inline in
  `thumbs.py:233,260,334`. **Medium.**
- `weekly.py:397,466` read prompts by path instead of `Prompts`, and append house style with two
  different headers. **Low.**
- Prompt text that primes the banned word: `thumbnail_v1.md:29` ("the hosts"), `receipts_script_v1.md:3`
  ("{channel_name}'s host"), `thumbnail_enquirer_v3.md:16,20` ("a host") while line 53 forbids it.
  Reword these in new versions. **Low.**
- Doctor's prompt check misses pick/pick_locate, receipts, weekly (+ the example file), archive
  and oneoff prompts, and checks only the base config, not each show (`doctor.py:144-156`). **Medium.**

## 8. Deploy and services

- **There's no single "status / stop / restart everything" command.** `bot-task.ps1 -Stop` and
  `worker-task.ps1 -Restart` exist, but the recorder container and leftover `clipper-cut-*`
  containers are separate, and CLAUDE.md/HANDOFF still describe the manual supervisor-kill
  workaround. A `clipper services status|stop|restart` would cover all of it. **Medium.**
- **The two task scripts and two supervisors are near copies and have drifted.** Only the
  worker's Stop kills stage children; only the bot's Install has `-StopExisting`; the bot copies
  2 env keys and the worker 5, and neither copies the Modal or ElevenLabs keys. Use one
  `deploy/common.ps1`. **Medium.**
- **Hidden coupling:** `worker-supervisor.ps1:17-21` takes the alert webhook from
  `deploy/record/env.local`. Dropping that webhook to stop duplicate recorder alerts (a HANDOFF
  to-do) would also silently remove the worker's alert fallback. **Medium.**
- `deploy/record/deploy.sh` has no "is a show live?" guard, though CLAUDE.md requires one. Read the
  recorder status over ssh and refuse without `--force`. **Medium.**
- Small: `C:\clipper` hardcoded in task descriptions; supervisor logs never rotate; bootstrap pulls
  an unpinned "latest" ffmpeg (the source of the 8.1/NVENC driver gotcha); `perform` (Sync key,
  LatentSync app) isn't in doctor or SETUP. **Low.**

## 9. Docs

- **There's no "start here" for a person.** CLAUDE.md tells Claude what to read; nothing tells a
  human. Add a short README section: read order, run one stream end to end, check / stop /
  restart services. **Medium.**
- **Stale or false claims:** README:66 (B16); README:132 "documents every key"; README:256 lists
  three war-room tabs (there are four); SETUP:7 says the worker needs an 8 GB NVIDIA GPU (Modal
  doesn't); SETUP:121 uses `uv run` (CLAUDE.md warns against it while the bot is up); README:10
  shows `transcribe → signals`, but the worker runs signals first; CLAUDE.md says
  `auto_upload_minutes (60)`, but 60 is this install's value and the default is 0. **Low.**
- README "Review" (:217-260) is a 44-line paragraph that's really the war-room manual; give it
  its own page. HANDOFF's "Waiting on the user" and "Open work" carry item-key lists that would sit
  better in issues. **Low.**

## 10. Tests, lint, repo hygiene

- **Add pytest and a GitHub Action.** The first ten tests are all pure or nearly pure:
  1. compilation part ends and chapter offsets (extract from `cut.py:217-236`; just changed in 0510a18)
  2. `Bleeper.intervals`/`hit`/`mask` (merge, pad, seek offset, stems)
  3. recorder segment classification (`parse_media`, `_have`, gap and sequence reset)
  4. chat wall-clock to source-time mapping across parts (extract `finalize.to_t`)
  5. `worker.plan`/`stage_args`/`cut_before` with fast lane on and off
  6. `make_srt` with multi-part offsets
  7. config layering and unknown-key/enum validation
  8. `clipper.example.toml` mentions every `DEFAULTS` key
  9. `schedule.plan` slot placement on a small fixture (pins, asap, `mix_shows`, `max_per_24h`)
  10. no raw `json.loads(` on a known shared state file outside `jsonstate`

  **High.**
- **Move the ruff rule set into `pyproject.toml [tool.ruff]`.** Today "the tree is clean" means a
  different thing on each machine, because the rules come from a user-level config. Add
  `tests/`, ruff and the parity test to the Action. **Medium.**
- **Clean up the repo root:** 15 `schedule.before_*.json` and 3 `clipper.before_*.toml` backups
  sit there (gitignored, but they're the first thing anyone sees); write them to `backups/`.
  `streamers.ics/json` are unreferenced; move them to `research_notes/`. Add a root `*.mp4` ignore
  (`docs/tiktok-demo-placeholder.mp4` is one `git add .` away). `.claude/launch.json` has an
  absolute `C:/clipper/...` path. History is 25.6 MiB, all loose objects (`git gc`). **Low.**

## Status of #77 (from the 10-23/10-24 audits)

| Item | Status now |
|---|---|
| M3 source links id vs key | Fixed in source links (`youtube.py:203-206`); a non-Kitsu compilation's `id` is still its topic (`schedule.py:114`) |
| M5 one bad entry aborts upload | Half fixed: each entry has its own try. Open: only 5xx is retried (a dropped last chunk re-uploads), and a broken entry retries hourly with no backoff |
| M6 show youtube overrides | Mostly fixed (`upload_one`). Still base config in `attach`, `finish`, `backfill_playlists`, rumble |
| M7 past Publish-at pins forever | Fixed (`schedule.py:235`); pins still skip `kick_first_hours`; `actions.pin` accepts past dates |
| M10 recorder stall alert | Mostly fixed; the stall alert still fires only on playlist errors (a 200 with no new segments never alerts) |
| M12 quota tracking | Open. No quota units counted; link backfill capped per run, not per day; a deleted video takes a cap slot every hour |
| M13 lipsync polls forever | Fixed (`perform.py:206-222`: 1 h cap, 10 poll errors) |
| L1 retake lock stale | Partly fixed; see B10 |
| L6 on_finish shell=True | Open (`record.py:709-712`); a stream folder with spaces also breaks it |
| L8 Kick 429 forever | Fixed (900 s cap) |
| L9 env list/dict as string | Fixed; None-default residue (§4) |
| L10 for_show unknown keys | Open (`config.py:737,806`) |
| L11 worker heartbeat | Open: catches only OSError; `run.data` changed outside `_mu`; `release()` unlinks after a takeover |
| L12 doctor misses keys | Mostly fixed; see B6 |
| L13 pick keys renumber | Open (§5) |
| L14 `run --from` bad value | Fixed |
| L16 Shorts bounds | Open for new installs (§4); retake `window_override` and `snap_trim` skip the min |
| L17 single-.ts finalize | Open |
| L18 captions backfill | Open (`youtube.py:556-571`); `config.py:221` says 50 units, the example says 51 |
| Example toml missing keys | Open: 142 keys |

## Suggested order

Each phase is useful on its own, and the earlier ones make the later ones safe.

**Phase 0: bug fixes (an afternoon, no structure change).** B1 (scheduler out of `refresh_cards`,
at least a guard), B2 (loud upload failures), B3 (shorts.json merge), B5 (worker run.json), B6
(doctor gate), B8 (upload after a failed replan), B9 (dry-run side effects), B10 (retake lock),
B11, B16, and the Shorts 15/59 defaults. The bot needs a restart after B1/B2/B8.

**Phase 1: safety net.** pytest with the ten tests above, the ruff config in pyproject, a GitHub
Action, and the example-coverage test. Nothing user-visible, but it makes the next phases safe.

**Phase 2: shared modules.** `ClipperError`; `jsonstate.read` everywhere; `timefmt`, `media`,
`ytdl`, `transcript`/`prompts` out of scoring; `states.py`; `review.set_columns`;
`actions.decide`/`run_bg`. Each is a mechanical move that the tests cover, one PR per module.

**Phase 3: structure.** The stage registry (drives cli, worker, `run`, doctor, the README table);
`cli.main` split into `register(sub)`; split record, thumbs and weekly; the bot's scheduler table;
the war-room route table and the HTML split.

**Phase 4: ease of use.** Unified flags and grouped `clipper -h`; config validation (L10, enums,
env typos) and `clipper config get`; a slim example file that `setup` no longer copies whole;
`clipper services`; layout-as-data for covers and the weekly; README "Start here" and a war-room
page; backups out of the root.
