# Clipper code audit (2026-11-08): architecture, maintainability, ease of use

The question this time is the one the user asked: **can someone else work on this later, and
is it something to be proud of?** Three lenses: the architecture (how the parts fit and talk),
maintainability (tests, lint, docs, repo, the pace of change) and ease of use for two end users:
an operator installing clipper for their own channel, and a second person who only reviews
clips. The 10-28 audit (`reports/code-audit-2026-10-28.md`) asked how easy clipper is to change;
the 11-03 one (`reports/code-audit-2026-11-03.md`) checked correctness and listed 50 edge-case
bugs. This audit does not repeat the bug hunt; it checks what those two asked for and looks at
the shape as it is now.

Method: read-only. Metrics were computed over the checkout (ast, ruff, git, pytest); two
independent readers each read a slice of the code and docs with no sight of the other's or of
these conclusions (a new maintainer's read of the package, a new operator's read of the docs,
`clipper -h` and the config), and every claim of theirs used here was re-checked at the cited
line. Nothing was changed, no model or service was called, `doctor` and `setup` were not run.
The deployment's `clipper.toml` on the NAS was read for counts only.

## Baseline

| | 2026-10-28 | 2026-11-03 | 2026-11-08 |
|---|---|---|---|
| Python in `clipper/` | 20,692 lines, 44 modules | 27,110, 50 + 3 packages | 34,859 lines, 91 .py files, 1,755 functions |
| Commands | 39 | 44 in five groups | 47 top-level, 82 sub-subcommands, 272 flags |
| Config | 449 keys / 34 sections | 529 / 38 | 626 keys / 42 sections; example 1,020 lines |
| Tests | none | 281 offline, 7 s | 546 offline, 23 s; **1 fails on Windows** |
| Modules no test names | all | 22 | 21 (4,541 lines) |
| Functions over McCabe 15 | 22 | 13 | 17 |
| Import cycles (len <= 4) | | | 21 |
| Prompt files | | 70 | 101 |
| HANDOFF.md | | 357 lines (limit ~300) | 539 lines |
| Open GitHub issues | | 45 | 43 (almost all features) |
| Commits since 10-18 | | 342 | 506 (peak 70 on 11-04); 404 co-authored with Claude |

Other numbers used below: 85 % of functions fully type-annotated; every module has a docstring;
`clipper -h` starts in 0.23 s; every HTTP call carries a timeout; 77 `# noqa` comments, all
unused; 355 imports inside functions; 391 `cfg["section"]["key"]` reads of 148 distinct keys; the
real deployment's `clipper.toml` is 714 lines, 69 sections, 344 keys; 57 of 65 remote branches
are already merged; `mockups/` 18 MB and `reports/` 5.6 MB are tracked (of 28 MB tracked in all).

## Summary

**The core is good, and better than most three-week-old codebases by a wide margin.** The
things that make a project workable for a stranger are here: one entry point with grouped help,
one module per stage, a stages table everything reads, idempotent stages on plain files, a
documented config layering with `clipper config get`, a single write path to the record (Kitsu
through `actions.py`), a model layer no stage bypasses, module docstrings that state each
module's files and process boundary, 546 fast offline tests with CI, and a wiki built from the
repo. Most of all, the rules are **executable**: tests fail on a raw JSON read, a `SystemExit`
outside the CLI, an unlisted command, an undocumented default or a broken wiki link. Both
earlier audits' recommendations landed and held. That is the part to be proud of.

**The risks are not code quality; they are growth and shape.** Four things stand out:

1. **Surface area is still outrunning consolidation.** The 11-03 audit's one observation that got
   worse: +7,700 lines, +97 config keys (+18 % in five days), +31 prompt files, 17 functions over
   complexity 15 (was 13), HANDOFF at 539 lines against its own 300. Twenty-one modules still have
   no test naming them, including `bot.py`, `story_edit.py`, `transcribe.py`, `signals.py` and
   `segments.py`; `review.py` (889 lines, the record) has six tests. The suite is red on the
   Windows machine the project is developed on, and CI would not notice.
2. **The product is still shaped like this one deployment.** The defaults and the example carry
   the author's shows and people (`[receipts] subject = "jer"`, `[weekly] masthead = LOLCOW
   CINEMA`, `[record] channels = ["kinocasinogaming"]`, a full `[shows.kino]`); Kitsu is a hard
   requirement of `clipper run`; the Discord app, the Google OAuth client and Kitsu itself have
   no setup page; wiki pages describe this NAS and disagree with each other about where the
   services run; two deployment paths (Windows scheduled tasks, Docker compose) are both
   documented and only one is used.
3. **Knowledge is in seven places and has started to drift.** CLAUDE.md, HANDOFF, the monthly
   log (2,849 lines), 41 wiki pages, `reports/`, 39 memory files outside the repo, 43 issues,
   plus three claude.ai artifacts. Examples of drift are in §3.3.
4. **A few shapes will cost the next person a day each**: a 1,739-line `warroom.py` that is a
   data model, an HTTP server and six feature panels with nine module-level singletons; a raw
   config dict with bookkeeping keys and colliding two-letter aliases; five inter-process
   mechanisms (file locks, instance locks, heartbeats, an outbox, HTTP plus an in-process hook
   dict) that only docstrings describe; prompt versions applied by mutating a `Prompts` object;
   five private JSON readers with five failure semantics.

None of this is hard to fix, and almost none of it changes behaviour. The plan in §6 is about
two weeks of consolidation. The question it raises for the user is in §7.

## 1. What held from the earlier audits

Everything the 10-28 audit asked for is in and tested (the 11-03 audit confirmed it). Of the
11-03 audit's ten architecture observations:

- **Done:** liveness and quiet failures (heartbeats in `daemon.py`, read by Health, `services
  status` and doctor); the `clipper serve` split out of the bot; cost guards (`score` refuses a
  rerun, `cut` keeps unchanged picks, `thumbs` keeps covers, `weekly --render` never drafts);
  most input edges; deployability moved (setup roles incl. the Mac's `control`, sandbox and
  Docker bootstrap tests, the Install checklist page); orchestration tests for `upload_due` /
  `upload_one` exist now (`tests/test_youtube.py`).
- **Partly:** the publishing state still has three truths written forward (#7, #16, #17 were
  fixed individually; the single hourly `reconcile` was not built); the test suite doubled but
  the untested list barely moved (22 → 21); `draw.py` is still shared by thumbs only
  (`weekly/render.py:38-83` has its own fonts, `wrap` and `fit`).
- **Not done:** the docs items (HANDOFF trim, a decisions file for the taste layer, the state
  files table, a commented example: #15); the lock edges #24/#25; the lows #29-#35, #37, #41,
  #46-#50.

## 2. Architecture

### 2.1 The shape

```
NAS (Docker, compose project clipper-hub)            laptop / Mac (control)          cloud
  serve   war room :8765 (tailnet) + jobs loop         clipper <cmd> by hand          Modal (WhisperX, archive)
  bot     Discord cards, buttons, DMs                  Claude Code sessions           Claude API, OpenRouter
  worker  every finished recording, stage by stage                                    YouTube, X, Brave
  recorder (own compose project), Kitsu :8090, cut container
  /data: clipper.toml, streams/ (every state file), secrets/, logs/, photos
```

Processes talk through the streams share and a little HTTP. A new maintainer must know five
mechanisms to answer "why didn't X happen": `jsonstate` file locks (`<file>.lock`, stale after
120 s; `jsonstate.py:28`); single-instance locks `<streams>/.<name>.lock` (`daemon.py:45-64`),
plus `.retake.lock` made differently (`retake.py:541`) and `.run.lock` with a heartbeat inside
`run.json` (`worker.py:160-183`); heartbeats `<streams>/.<name>/status.json` (beat 60 s, down
after 180 s, `daemon.py:27-28`); the DM outbox `<streams>/.notify/outbox/` the bot drains
(`alerts.py:53-70, 183-224`); and HTTP with `X-Key` to serve's `/api/replan|retake|health`
(`serve.py:261-308`) plus the in-process `warroom.HOOKS` dict serve fills (`serve.py:227`).
Children: serve spawns `clipper ...` (`serve.py:125-156`), the worker spawns each stage into
`run_<stage>.log` (`worker.py:308-321`), the page detaches a retake (`warroom.py:457-473`), cut
sshes itself to the NAS (`cut.on_nas`). All of it is sound and all of it lives only in
docstrings. One wiki page listing the five with paths and timeouts is the cheapest high-value
doc in this report (§6, item 7).

### 2.2 Strengths to keep and extend

- `stages.py:29-56`: one table, with `argv`, `order`, `cut_before` derived from it and
  `test_cli.py:22-29` proving every row parses against the real parsers.
- The boundary modules nothing depends upward on: `errors.py`, `jsonstate.py`, `daemon.py`,
  `states.py`, `timefmt.py`, `media.py`. Their docstrings are the contract.
- `actions.py` as the one write path to Kitsu; the war room's `do_action` (`warroom.py:612-625`)
  wraps it with a lock and undo rather than re-implementing it.
- `llm.py`: provider by model prefix, one class, refusals, max_tokens and cost handled once
  (`_account`); stages never touch an SDK. `Budget` shared by swarm, story and investigate.
- `config.explain` / `clipper config get` (where a value came from, by layer) and `CHOICES` /
  `EFFORTS` refusing bad values at load.
- Lazy imports in `cmd_*` keep the CLI at 0.23 s despite numpy, scipy, Pillow and OpenCV.
- Every `requests` call has a timeout; no secret is printed or logged; the war room's threat
  model (constant-time token, SameSite cookie, same-origin POSTs, `parents` checks) was judged
  sound on 11-03 and has not been loosened.

### 2.3 Findings

**A1. Five import cycles with a wrong-direction edge each** (21 cycles of length <= 4 in all,
most of them these five seen through different modules; 355 in-function imports hide them).
Each is one or two hours, no behaviour change, tests on both sides:

| Cycle | Wrong edge | Small move |
|---|---|---|
| actions → review → notify → review | `review.publish` posting Discord cards (`review.py:688`) | `publish` returns the published keys; its four callers (`cli.py:549`, `pick.py:389`, `retake.py:493`, `story_edit.py:603`) call `notify.post`, or a 20-line publish-and-notify helper outside `review` |
| alerts → warroom → modalmon → alerts | the DM digest importing `warroom.overview` (`alerts.py:236`) | lift `overview` / `health` / `stream_infos` (`warroom.py:157-455`) into `overview.py`; page and digest import a model, not a server |
| notify → xpost → review | `notify.post_issue` importing `issue_folder` / `issue_pages` from `xpost` (`notify.py:362`) | they describe the weekly folder: move to `weekly/__init__.py` |
| preshow → worker → preshow | `preshow.run_pending` importing `worker` (`preshow.py:233`) | worker needs only `PREFIX` and `is_preshow` (`worker.py:226, 378`): move both to `states.py` |
| investigate → serve → warroom → investigate | `investigate.war_room_link` calling `serve.tailnet_link` over HTTP (`investigate.py:1251`) | the serve heartbeat already carries `warroom` and `tailnet` (`serve.py:214-218`): read `daemon.heartbeat(cfg, "serve")` |

**A2. `warroom.py` is three programs** (1,739 lines, 91 defs): a data model (`overview`,
`health`, row builders, lines 157-455), the HTTP server (`Handler`, 191 lines at 1312, routes
`GET_API` / `POST_API` at 1265-1308) and six feature panels (faces, cast, news, investigations,
low-res copies, layouts: 628-1260, 1513-1606), held together by nine module-level mutable
singletons (`SYNC`, `TAILNET`, `HOOKS`, `LIVE_CACHE`, `CACHE`, `SRT_CACHE`, `LOWRES`, `CAST_JOB`,
`LOCK`). That is why `alerts` imports a web server to build a digest, why tests must monkeypatch
module globals, and why one war room can run per process. The route tables make a mechanical
split safe: `warroom/server.py` (Handler, auth, static), `overview.py` (data), one module per
panel, and the singletons gathered into one state object the server owns.

**A3. The config dict.** It works, and `for_stream` / `pin_show` / `explain` are good; the cost
is in reading it. Bookkeeping lives under underscore keys beside settings (`_root`, `_resolved`,
`_pinned`, `_file`, `_dir`, `_local`: `config.py:1149-1156`); `_root` is imported by eight
modules as if public (`actions.py:38`, `serve.py:48`, `warroom.py:44`, `doctor.py:30`); two-letter
aliases collide (`sc` is scoring in ten modules, story in six, schedule in six, shorts in four;
`rc` is review, rumble or record; `wc` is worker, weekly or warroom); 391 untyped
`cfg["x"]["y"]` reads mean a typo is a `KeyError` at run time in a child process. Not a rewrite:
a public `config.root()`, a rule against two-letter aliases in modules touching more than one
section, and (later) a thin typed accessor per hot section.

**A4. Prompt versions by mutation.** `Prompts.__init__` takes `[scoring] prompt_version` as
*the* version (`prompts.py:58`); every other stage sets `prompts.version = xc["prompt_version"]`
afterwards (`archive_index.py:249-250`, `cast.py:343-344`, and the same pattern elsewhere), and
`doctor.prompt_files` hand-lists all 30-odd prompt names and their config keys
(`doctor.py:163-196`): a third place to edit per prompt. A small registry (each stage declares
`(prompt name, section, key)`; `Prompts.get(name, version=...)`; doctor and a test read the
registry) removes the mutation and the hand list.

**A5. The hubs.** `config.py` (123 commits), `cli.py` (97) and `doctor.py` (70) are touched by
every feature; `cli.py` is 1,676 lines, `add_extras` alone 355. The five-place ritual (DEFAULTS,
parser + `cmd_`, doctor, example, wiki) is **mostly a strength**: three of the five are
machine-checked and the comments on DEFAULTS are the documentation. Its one real cost is that
each comment is written twice, in DEFAULTS and in a 1,020-line example kept in step by hand for
626 keys, and the example restates every default uncommented (626 live, 8 commented), so copying
it pins defaults (the 11-03 #15). Two options: a `clipper config --example` that generates the
file from DEFAULTS so the test diffs them, or go further and make a commented
`clipper/defaults.toml` the single source (config loads it; the example becomes a 40-line
starter). TOML cannot say `None`, and exactly one default is `None` (`[preshow] teasers`,
`config.py:392`), so the move is feasible.

**A6. Small duplications with different semantics.** Five private JSON readers:
`actions.load_json:66`, `warroom.load:73` (swallows `ValueError`), `worker.read_json:74`
(returns `{}`), `retake.load:44`, `pick.load:112`; `alerts.save_state:144-152` with its own
tmp-and-replace instead of `jsonstate.write`; `now_iso` three times (`worker.py:64`,
`costs.py:36`, `modalmon.py:42`); `age_s` twice with different sentinels (`worker.py:68`,
`warroom.py:80`); `cli.cmd_review`'s verb dict (`cli.py:569-580`) beside `actions.decide`
(`actions.py:341-357`) with a different verb set, so a new verb is added twice; `serve.jobs()`
as positional four-tuples unpacked in `due` and `status` (`serve.py:65-69, 90-123, 198-211`).

**A7. Two deployment paths.** `deploy/serve`, `deploy/discord`, `deploy/worker` and
`common.ps1` (Windows scheduled tasks, 232 + 135 lines) are still the install path in
Install-Windows (`:54-56`) and Roles-and-services (`:21-23`), while HANDOFF says they are
disabled and must never run beside the containers. `services.py` supports both (`TASKS`,
`HUB_ACTION`). A Windows GPU worker genuinely needs the task path (no CUDA in Windows Docker);
serve and the bot do not. Decide per role and say so on one page.

**A8. Packaging for the stated release.** `prompts/` sits outside the package and is copied by
`deploy/hub/deploy.sh` by hand (`config` falls back to the checkout's `prompts/`); there is no
`__version__` (`clipper/__init__.py` is empty), no `--version`, no LICENSE, no CHANGELOG, and the
version in `pyproject.toml` is a static 0.1.0.

## 3. Maintainability

### 3.1 Tests

- **Red on the dev machine.** `tests/test_setup_paths.py:47-49` expects `Path("~nobody").
  expanduser()` to raise; on Windows it returns `C:\Users\nobody\...`, so `setup.config_file_ok`
  gives a different message and the test fails (`setup.py:62-66`). The 11-07 log entry noted it;
  it is still red, and CI runs Ubuntu only (`.github/workflows/check.yml`), so nothing will say
  so until it is fixed. Add `windows-latest` to the matrix: the project is developed on Windows
  and runs on Linux, and both paths matter.
- **Where it is thin.** No test names `bot.py` (620 lines, the one process holding the Discord
  token), `story_edit.py` (616), `rumble.py`, `modalmon.py`, `tiktok.py`, `transcribe.py`,
  `signals.py`, `record/watch.py`, `perform.py`, `speakers.py`, `segments.py`, `record/chat.py`,
  `cardsync.py` and eight smaller ones (4,541 lines). `review.py` has six tests, `actions.py`
  two (none for `describe`, the row every card and panel is built from), `worker.run_stream`'s
  failure and takeover paths none.
- **Speed.** 23 s, not "under a second" as CLAUDE.md:121 and Developing.md:41 say: 4.1 s is one
  test waiting on a down serve (`test_serve.py`), 2 s one in `test_llm.py`, and every war room
  test pays a 0.5 s server teardown.
- **Naming.** Test names read as sentences and files have docstrings: unusually good. Two
  detractors: `test_audit_fixes.py` and `test_audit_fixes_2.py` (25 tests) are filed by date,
  not subject (scoring's number retirement lives at `test_audit_fixes_2.py:161, 188`), and
  several tests monkeypatch module globals (`warroom.for_stream`, `warroom.actions.resolve`),
  which couples them to import style.
- **The convention tests are the real asset.** They encode cross-process invariants at zero cost
  per feature and cover new modules automatically. Two more would pay for themselves: *every
  module in `clipper/` is named by some test* (with an allowlist that may only shrink), and *no
  default or example names a show or a person* (§4.1).

### 3.2 Lint

Ruff runs with its default rule set only (`pyproject.toml:[tool.ruff]` pins the version, selects
nothing). All 77 `# noqa` comments are unused under that set (`ruff --select RUF100` fixes
them); 70 of them are `BLE001`, which means the code was already annotated for a broad-except
rule that is no longer on. A stricter set would flag, today: 125 functions with too many
arguments, 60 over McCabe 10 (17 over 15: `recording._video` 24, `setup.step_machine` 24,
`chat.listen` 22, `bot.register_commands` 21, `retake.apply` 21, `archive.check_links` 20,
`llm._compat` 20, `worker.watch` 20, `news.watchlist` 19, `shorts.run` 19, `thumbs/stage.run`
19, `editor._clean_changes` 19, `record/watch.tick` 18, `story_edit.cut` 18, `youtube.upload_due`
17, `worker.run_stream` 16, `setup.step_keys` 16), 23 `zip` without `strict`, 15 implicit
returns. Suggested: enable `I` (import order), `B`, `UP`, `BLE`, `RUF100` and `C901` at 15 with
the 17 above listed as explicit exceptions to burn down; 457 lines are over 110 characters, so
pick a line length and enforce it or decide not to.

### 3.3 Docs

Seven places hold project knowledge, and drift has started:

- CLAUDE.md:121 and Developing.md:41: tests "take under a second" (23 s).
- Getting-started.md:42-47 says serve, the bot and the worker are Windows scheduled tasks;
  Roles-and-services.md:11-13 says NAS containers since 11-05. Install-Windows.md:54-56 still
  installs the tasks.
- HANDOFF's pipeline line has `transcribe → signals`; `stages.py` runs signals first (it extracts
  the audio itself, `signals.py:212`).
- `docs/brief-warroom-on-nas.md` (121 lines) is a 10-19 brief for work finished 11-05;
  `docs/warroom.md` is a four-line redirect; `docs/tiktok-app-pages.md` documents a parked
  feature. None is linked from the wiki except the TikTok one.
- HANDOFF.md: 539 lines against its own 300. "Open work" (153 lines) and "Waiting on the user"
  (74) are diaries of 11-03 to 11-07; "User decisions" (68 lines, the taste layer) is interleaved
  with state that goes stale weekly. The 11-03 audit's `docs/decisions.md` for the taste layer
  and the state-files table were not written.
- The print-vs-log rule (stages print, services log, parents capture children's stdout) is
  coherent but unwritten, and the service layer breaks it: `warroom.fail` prints inside `clipper
  serve` (`warroom.py:1352-1356`), as do the warm-up thread and parts of preshow, archive and
  modalmon. 545 `print(` to 97 log calls.

What is good: the wiki is well organised (Home, grouped sidebar, a Developing page that is the
short CLAUDE.md), `test_wiki.py` keeps links and the sidebar honest, module docstrings are the
design record, and comments cite issue and bug ids (#75, B13, #77 L14).

### 3.4 Repo hygiene

- `mockups/` (18 MB: fonts, JPGs, HTML covers) and `reports/` (5.6 MB: mp3, .otio, PDFs) are
  the studio's working files inside the code repo; 23 of 28 tracked MB are not code or docs.
  `.git` is 69 MB. A stranger cloning "the clipper package" gets Mother Moo's takes.
- 57 of 65 remote branches are merged and never deleted (`audit/phase2`, `audit/phase2b`, ...).
- `.claude/launch.json` has the absolute path `C:/clipper/.venv/Scripts/clipper.exe` in a
  tracked file.
- No LICENSE, CHANGELOG or CONTRIBUTING; no `--version`.

### 3.5 Pace

506 commits in 21 days, 404 of them co-authored with Claude, about 1,600 lines a day. The pace
itself is not the problem; the conventions and the tests are what make it safe, and they must
grow at the same rate. Two signals say they have not quite: the untested list (22 → 21 while the
package grew 28 %) and complexity (13 → 17 functions over 15). The 43 open issues are almost
all features (story and investigate part 2, the six edit issues, the Posts family, four
archives, Instagram, crisis mode, live clips, the Udder Truth). A consolidation window before
the next wave is the single decision that most affects whether the next person has an easy time.

## 4. Ease of use

### 4.1 The operator (installing it for their own channel)

**Zero to first upload, as the docs present it:** bootstrap (installs uv, ffmpeg, the shim, loops
`uv sync` on exit 3), `clipper setup` in five steps (join/new, roles and transcribe backend,
machine, keys, doctor), then `clipper fetch <url>` and `clipper run <id>`, review, `schedule`,
`upload --sign-in`, `upload`. Install.md:80-81 and Install-checklist.md:83-87 admit the walkthrough
stops short of: creating the Discord app and its ids, the Google OAuth client and the YouTube
Data API, the first show, the Modal deploy, the unattended services. Grep of `docs/wiki` for
"invite", "intents" or "Desktop client" finds nothing; the only guidance for the OAuth file is
"copy from the main machine" (Install-Windows.md:30-32); the Discord recipe is a config comment
(`clipper.example.toml:518-519`) and an error string (`notify.py:61`). Kitsu "runs in Docker"
(Review.md:3), but `deploy/kitsu/deploy.sh:15` assumes `ssh nas` and there is no Kitsu page.

**Accounts.** Truly required: an LLM key, a running Kitsu (`review publish`, the last stage of
`clipper run`, raises without `[review] host`: `review.py:159-168`), a Google Cloud project with
an OAuth Desktop client and a YouTube channel, and a GPU or Modal. Optional but assumed by most
pages: Discord (bot, alerts, DMs, the reviewer), Tailscale, a NAS. Then Brave, ElevenLabs,
OpenAI/Gemini/OpenRouter, TikTok, X, an Anthropic Admin key, a Hugging Face token: four required,
up to about twelve.

**Where this deployment leaks into the product.** Defaults: `[receipts] subject = "jer"`, the
Quartering topics, figures and title (`config.py:130-136`); `[weekly] masthead = ["LOLCOW",
"CINEMA"]`, `byline = "BY THE LOLCOW DESK"` (`:293-294`); the `house_style` text naming Andy, PPP
and Jer (`:902`); `[schedule] timezone = "America/Los_Angeles"` (`:818`). Example:
`[record] channels = ["kinocasinogaming"]` (`:67`) and a full `[shows.kino]` with a real YouTube
channel id (`:967-985`). A user who never opens `[weekly]` renders a LOLCOW CINEMA cover and
`clipper receipts` targets Jer. Docs: Install-checklist is about Lolcow Cinema, "the Mac" and
#46; YouTube-channel.md:1 is "what we learned bringing up Lolcow Cinema"; Phone-access.md:22-51
is this NAS; Getting-started.md:2 and Install-checklist.md:69-71 tell a new install to copy the
author's test stream `kick-131151926`. The config is found through a Windows registry fallback
the user cannot see (`config.py:1102-1107`); `cookies_from_browser = "firefox:clipper"` assumes a
Firefox profile; the bootstrap says nothing about the multi-GB torch download or that it accepts
winget agreements for you (`bootstrap.ps1:46, 58`).

**Config.** One stream end to end needs roughly 10-12 keys plus two secret files and 2-4 env
vars: setup's starter (`setup.py:401-417`), `[review] host`, `[schedule] timezone`, in
`clipper.local.toml` `[paths] streams`, `[cut] clip_codec`, `[transcribe] backend`, by hand
`[shows.<key>] channels` and `meta.show_name`, the four `[discord]` ids, `[youtube]
auto_upload_minutes` (default 0: nothing ever uploads by itself, and nothing warns). That is
reasonable. The problem is finding them in 1,020 lines and 42 sections, most of which are
research features. The layering is explained (Configuration.md:1-5) and `config get` is
excellent; under-explained are how the file is found, that relative `[paths]` resolve against
the config's folder, that `clipper.local.toml` refuses `[shows]`, and the `"<placeholder>"`
convention doctor treats as unset (`doctor.py:473`).

**The CLI.** 47 commands, 82 sub-subcommands, positional modes on several more: about 130
reachable verbs, of which some 25 are what a Kick-to-YouTube operator uses. Consistent and good:
`--items` everywhere (old spellings kept hidden), `--dry-run` on twelve commands, `--sign-in` on
four, `--force` always "redo even if unchanged", incompatible mode flags refused together,
grouped `-h`. Inconsistent: `--from` is a time on `pick` and `preshow fetch` and a stage on
`run` (`cli.py:932, 1032, 1051`); `--only` is stages, pages or kinds; `run --from/--only` take
internal twin names (`clips`, `clips_meta`, `clips_thumbs`, `clips_review`: `stages.py:33-45`)
that are not commands, so redoing Short covers means guessing; `score --dry-run` writes request
files and prints no estimate (`scoring.py:382-390`) while `compare score` and `story new` do;
`--limit` exists on two commands though Getting-started.md:58 lists it as general; `fetch`,
`run`, `alert`, `receipts`, `cast find` and `archive transcribe` have blank help for their
positionals (`cli.py:917-921`); sub-subcommands are declared three ways (real subparsers, a
positional `action` with choices, mutually exclusive mode flags). Discoverability: approving a
clip is obvious; redoing a thumbnail has four routes with no cross-reference; "what goes out
next" has four answers (`schedule --list`, `review status`, `upload --next`, `upload --dry-run`);
there is no `clipper status`.

**Errors and feedback.** Of about 70 `ClipperError` messages sampled, roughly 70 % say what to do
next, and the best are models of the form (`review.py:159-168`, `youtube.py:86-91`,
`scoring.py:374-375`, `config.py:947`, `warroom.py:1686-1690`). The weak ones name a file the user
has never heard of (`candidates.json`, `words.jsonl`) or stop at the fact ("not a face
embedding", "unknown action", "Claude API unavailable" with no retry advice). Nothing estimates
cost before spending: `transcribe` prints GPU, time and estimate first; `score`, `meta` and
`shorts` print the cost after; `segments` (Opus over ~100k tokens) prints none; Costs.md:41-51
tabulates weekly, archive and receipts but not the per-stream figures a new operator needs.
Doctor covers a new machine well (config, shows, streams, time zone, keys with a fix each,
ffmpeg, fonts, GPU, every online service); gaps: a missing YouTube client secret is a warning
whose fix is "copy it" (`doctor.py:496-502`), the bot's guild permissions are checked only at
post time (`notify.py:336`), and `auto_upload_minutes = 0` passes silently.

### 4.2 The reviewer (a second person approving clips)

To use Discord, the operator adds the reviewer's id to `[discord] allowed_user_ids` and restarts
the bot; everyone else gets "You're not on this bot's allowlist" (`bot.py:382-389`). The
reviewer then sees a card per item in a per-stream thread with Approve, Reject, Changes, Hold,
Reopen, Pin, Retitle and Thumbnail text, 480p previews capped at 10 MB and the first 60 s of long
items, and five slash commands. None of this is on Review.md (which says only "the bot mirrors
each item as a card with buttons"); the ids recipe is a config comment and the allowlist is
mentioned only in Investigations.md:62. Kitsu is "the record" but no page says how a reviewer
gets a login or which columns matter (Hold, Publish at, Layout, Playlist are scattered over
three pages and the example). The war room page is the right one for this person (keys, bulk
edits, phone tabs, swipe, 360p copies), but access is the hard part: localhost by default, a
second person's phone must join the operator's tailnet (Phone-access never addresses a second
person), the `?key=` link is printed once (`warroom.py:1718-1719`) and is one shared token with
no per-person identity or revocation, and "Who's this?" is desktop-only.

Three surfaces, three vocabularies: Kitsu "retake" / Discord "Changes" / war room "retake note";
"hold" vs "Not going out" vs "stale" vs "over budget"; "pin" meaning Publish at; Review.md:14
sends a reviewer to a CLI they do not have. The 11-03 audit called the review surface a product
question; the user answered it (keep the cards for the phone). The remaining work is a single
"Reviewing" page and one vocabulary across the three.

## 5. Questions the earlier audits left that this one answers

- *Did the structure fixes hold?* Yes, all of them, and the pace since has kept to them: zero raw
  JSON reads of state, zero `SystemExit` outside `cli.py`, every command grouped, every default
  documented. The conventions are working.
- *Is the five-place ritual a cost?* Mostly a strength (§2.3 A5). Make it a six-line checklist in
  Developing.md and stop writing each comment twice.
- *Is the war room's hand-rolled server a liability?* Not for security; for shape (A2). A web
  framework would remove some plumbing but add a dependency and a deployment change for no
  user-visible gain; splitting the module is the right size of fix now.

## 6. The plan, ranked by benefit over effort and risk

*Order agreed with the user on 2026-11-08 (§7): tier 0, then tier 1 with item 20 pulled into it,
both before any new feature; then tier 2 paced behind the deployment's own work, with item 14's
Kitsu part done as a page plus a local compose recipe, and no Kitsu-optional work.*

**Tier 0: a day, no behaviour change.** Do these first; each is an hour or less.

1. Fix the Windows-only test (`test_setup_paths.py:47`; `setup.config_file_ok` should say the
   same thing on both OSes) and add `windows-latest` to the CI matrix.
2. `ruff check --select RUF100 --fix` (77 dead `noqa`), then enable `I`, `B`, `UP`, `BLE`,
   `RUF100` and `C901` at 15 with the 17 current offenders as explicit exceptions.
3. Delete the 57 merged remote branches; make `.claude/launch.json` relative.
4. Fix the drift: "under a second" (CLAUDE.md, Developing), Getting-started's "Stop and restart",
   HANDOFF's pipeline line; archive `docs/brief-warroom-on-nas.md`, delete the `docs/warroom.md`
   stub; mark Install-Windows's task scripts as "a single Windows machine without Docker".
5. Refile `test_audit_fixes*.py` by subject (scoring, schedule, cast, ...).
6. Add `clipper --version` and `__version__`; pick a LICENSE (even "all rights reserved" written
   down); start a CHANGELOG from the log.

**Tier 1: about a week, structure only.** One PR each; tests exist on both sides of every move.

7. The "How the processes talk" wiki page (five mechanisms, paths, timeouts, which log to read)
   plus the print-vs-log rule and the feature checklist in Developing.md.
8. Break the five cycles as in §2.3 A1.
9. Split `warroom.py` into server + auth, `overview.py` and panel modules; gather its module
   singletons into one object (A2). This also kills the `alerts` cycle.
10. One JSON helper (`jsonstate.read_or(path, default)`), one `now_iso`, one `age_s`;
    `alerts.save_state` through `jsonstate.write`; a dataclass for serve jobs; one review-verb
    table used by the CLI, the bot and the page (A6).
11. The prompt registry (A4): `Prompts.get(name, version=)`, doctor and a test read it.
12. Tests where the risk is: `bot.py`'s handlers with a fake interaction, `actions.describe`,
    `review.sync`'s column round trip, `worker.run_stream`'s failure and takeover paths, the pure
    parts of `transcribe`, `signals` and `segments`; and the convention test "every module is
    named by a test" with a shrinking allowlist. Also `config.root()` instead of `_root`, and no
    two-letter section aliases in modules that touch several sections.

**Tier 2: before anyone else installs it.** Product work, two to three weeks spread over time.

13. Scrub this deployment from the defaults and the example (`[receipts]`, `[weekly]` masthead
    and byline, the `house_style` names, `[record] channels`, `[shows.kino]` → a `[shows.myshow]`
    placeholder) and add the convention test that fails on known names.
14. Setup phase 2 as pages first, code later: the Discord app (create, bot, intents, invite,
    Developer Mode ids), the Google OAuth Desktop client and Data API, Kitsu on one machine
    without ssh (a `deploy/kitsu/local.sh`) and its bot and reviewer accounts; a "Reviewing" page
    for the second person, and `clipper review invite <discord id>`.
15. Separate "this deployment" from the product pages: NAS hub, Lolcow, the Mac and #46 move to
    NAS-hub or HANDOFF; Getting-started says "fetch any short VOD".
16. Estimate before spending on `score`, `segments`, `meta`, `thumbs` (`--dry-run` prints tokens
    times `[llm] prices`); per-stream costs in Costs.md; a `clipper status` that unites
    `worker --status`, `review status`, `schedule --list`, `upload --next` and the month's spend.
17. Help and naming: fill the blank help strings; let `run --from thumbs` run both twins or
    explain them in `run -h`; stop claiming `--limit` and `--from` are universal; doctor warns on
    `auto_upload_minutes = 0` and on a bot role with `[discord] enabled = false`.
18. Deploy: one supported way per role (compose for serve and the bot on any Docker host, the
    task scripts for a Windows GPU worker only), on one page.
19. Config and packaging: generate the example from DEFAULTS, or make a commented
    `defaults.toml` the single source (A5); `prompts/` into the package via `importlib.resources`.
20. Move `mockups/`, `reports/` media and `research_notes/` to a `studio/` folder excluded from
    the package, or to their own repo; keep the audits and analytics reports under `docs/`.

**And one rule of growth**, enforced the way the existing rules are: a new command ships in the
same PR with a test naming its module, its wiki page, a doctor check if it needs a key or a
service, and a pre-spend estimate if it calls a model. The convention tests already make three
of those mechanical; items 12 and 13 add the other two.

## 7. Questions for the user, and the answers (2026-11-08)

1. **Who is the release for?** Collaborators on this deployment, or strangers with their own
   channel? The 11-03 answer was "as if for a public package, simple for the user first". If
   strangers: tier 2 is required work and Kitsu-optional (review.json already carries the state)
   becomes the biggest item on the list. If collaborators: tier 2 shrinks to items 13-15 and 17.
   **Answer: both, this deployment first.** Keep building as a public package with little debt;
   Lolcow Cinema's needs come first, and tier 2 is paced behind the user's own work, not dropped.
2. **A consolidation window?** Tiers 0 and 1 are about seven working days with no new features.
   The alternative is to interleave them with story/investigate part 2 and the Posts family,
   which is how the untested list and the complexity count got where they are.
   **Answer: yes.** Tiers 0 and 1 first, about seven days, no new features until they are merged.
3. **The studio files.** Keep `mockups/`, `reports/` media and `research_notes/` in this repo (and
   accept a 69 MB clone that is mostly not code), or move them out (item 20)?
   **Answer: move them under `studio/`, out of the package.** A `git mv` inside this repo, no
   history rewrite; item 20 moves into tier 1.
4. **The Windows task scripts.** Keep them for a Windows GPU worker only (item 18), or drop them
   and require Docker for every unattended role?
   **Answer: keep them for a Windows GPU worker only** (the auditor's pick, not put to the user
   separately; the docs say so on one page, and serve and the bot are compose-only there).
5. **Kitsu.** Stay the record forever, or is "Kitsu optional" (the war room and review.json as
   the record for small installs) a goal? It changes how much of §4.1 is worth doing.
   **Answer: Kitsu stays the record.** Document a one-machine Kitsu (a Kitsu page and a local
   compose recipe without ssh, item 14); Kitsu-optional is not a goal.
