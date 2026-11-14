# Changelog

Notable changes, newest first. The dated diary is `docs/log/<YYYY-MM>.md`; `HANDOFF.md` is the
current state; the version is `clipper --version` (set in `clipper/__init__.py` and
`pyproject.toml`, which a test keeps equal). Nothing has been released yet.

## Unreleased (0.1.0)

### 2026-11

- 11-13: command-line polish (#205): a stream argument takes `latest`, `latest:<show>` or a unique start
  of an id, and a mistyped one names the nearest; `--json` on `review list`, `schedule --list` and
  `shows`; `clipper config diff` lists what this install changes from the defaults, by layer; `clipper
  completion bash|powershell` prints a tab-completion script; `run --only` suggests the nearest stage;
  `clipper run` logs "stage N of M".
- 11-13: the `archive transcribe` and `investigate facts` dry runs count what every call costs
  beyond its content (a minute of GPU a transcribe call, from the subject's own Modal runs when it has
  20; the prompt, schema and header a facts call repeats). Items with no length yet are said, not
  priced. Back-tested on the 759-clip run: $10.87 vs $10.88 logged (was $0.01), facts $0.27 vs $0.26
  (was $0.11) (#228).
- 11-13: `clipper backup` zips the taste layer (config, hand-edit backups, schedule, people, faces,
  voices, layouts, cast photos, each stream's config and review state; secrets only when asked) into
  `[backup] folder`, and serve makes one every `[backup] every_days` (7). `clipper streams list` shows
  what each stream takes; `clipper streams prune` removes the source and audio of streams done with
  (`--cuts` also the long cuts of uploaded or rejected items), and serve does it daily with
  `[streams] keep_source_days`. `[audit] backup_folder` and `backup_max_days` moved to `[backup]`
  (#204; wiki Backups).
- 11-13: `clipper status` opens with what needs you (items waiting or held, checkup's findings on
  services, runs and late releases), shows each stream's show and date, leaves out week-old streams
  with nothing waiting (`--all`), and has `--json`. It counts reviews from review.json instead of
  every item's sidecar: 15 s from the laptop, was 60 s (#199).
- 11-13: the `review` workflow (Grok on every commit Copilot hadn't reviewed) is gone: too slow at ~25 min
  a PR. Code PRs now get a local Claude Code review before the first push and Copilot's when the PR opens;
  `clipper codereview` stays for a by-hand second opinion (wiki Developing).
- 11-10: `clipper audit`: a weekly digest of how the week went (no model), saved each week, and a monthly
  read-only deep audit that traces what keeps coming back (wiki Audit). Dependabot opens grouped weekly
  update PRs.
- 11-10: the `review` workflow switches between reviewers by itself: a commit Copilot reviewed gets no
  Grok review, and Copilot's out-of-credits notice, or no request or review from it, runs Grok
  (`[codereview] copilot_wait_s`; 0 = always Grok).
- 11-10: war room fixes from the usability review (#201, #202, #206): bulk actions only touch what is on
  screen and the bar counts the picks left out; a double tap can't send an action twice; bulk reject; the
  header says how old the data is; a hidden tab stops polling; `?key=` leaves the address bar once the
  cookie is set; the Health panel's mis-encoded dot, with a test for mis-encoded text in `clipper/web/`.
- 11-10: the `check` workflow skips a push or PR that changes only notes no test reads (root `*.md`,
  `LICENSE`, `docs/log/`, `docs/archive/`, `reports/`, `studio/`). `docs/wiki/` still runs it, and so
  does `studio/research_notes/streamers.json` (a test reads it).
- 11-10: `clipper --help` and every command with actions no longer print the whole list in braces, and a
  mistyped command or `--from` stage says "did you mean ...?" (#205).
- 11-09: `clipper codereview` and the `review` workflow: every code PR gets a second review in one
  OpenRouter call (`[codereview] reviewer`, Grok 4.7 at high effort, about $1 a PR), posted on the PR
  and on the issues it links, one comment each, edited in place. Needs the repo secret
  `OPENROUTER_API_KEY`. Copilot's review quota had run out (PR #191). wiki Developing has the 13-model
  comparison behind the choice.
- 11-09: `[edit] tighten` (#70, the first piece of the edits roadmap #66; off by default): `clipper
  shorts` cuts pauses over `max_gap_s` down to `keep_gap_s` (`keep_turn_s` at a change of voice),
  only where the audio is quiet too, never under `[shorts] min_s`; captions and scene changes move
  with the cuts, which sit on whole frames so the picture and sound stay together
  (`clipper/tighten.py`). Kitsu column "Tightened", `pauses_cut` in shorts.json for
  retakes, `clipper shorts --no-tighten/--tighten` per Short. Kitsu columns no longer get an empty
  value written onto an empty column on every publish.
- 11-08: the prompt files ship inside the package: `prompts/` moved to `clipper/prompts/`, beside the
  loader (`clipper/prompts/__init__.py`, which finds them with `importlib.resources`), so a wheel and
  the hub image carry them without a copy step. `[paths] prompts` is optional now (`""`, the default,
  means none): a folder of your own, read before the packaged files; doctor warns when it names a
  folder that isn't there. The Dockerfiles and `deploy/hub/deploy.sh` no longer copy `prompts/`.
- 11-08: a heartbeat read that lands while its writer replaces the file no longer counts as "no
  heartbeat": `daemon.read_heartbeat` reads a missing or unreadable heartbeat again (three reads,
  0.3 s apart) when its folder exists. `clipper record status` (exit 3 while a show is recorded,
  which `deploy/record/deploy.sh` trusts), `clipper status`, doctor and the war room read through it.
  A heartbeat that is there but still unreadable after that is unknown, not absent: Health and the
  status lines say "unreadable" with the reason, `record status` exits 1, and the recorder's stop,
  restart and deploy refuse without `--force`.
- 11-08: `clipper/defaults.toml` is the one source of the defaults, every setting with a comment
  (`config.py` loads it; a None default is a commented line there plus `config.NONE_DEFAULTS`);
  config.py is 900 lines shorter, and `clipper.example.toml` is a starter clipper.toml with the
  few values a new install changes and a show table.
- 11-08: pages for what `clipper setup` can't do: Kitsu (with `deploy/kitsu/local.sh` for one
  machine without ssh, and the bot and reviewer accounts), the Discord app and its ids, YouTube
  sign-in, and Reviewing for the second person; `clipper review invite <discord user id>` puts a
  reviewer on the bot's allowlist. One way to run each role unattended: compose (`deploy/hub`,
  any Docker host) for serve, the bot and the worker, the Windows task for a GPU worker only; the
  serve and bot task scripts are gone. The product pages no longer name this install's test
  stream, NAS address or channel.
- 11-08: `clipper status`: one screen with the services' and the recorder's heartbeats, the
  worker's streams, review counts, the next releases and the month's spend. `score`, `segments`,
  `meta` and `thumbs` print an estimate with `--dry-run` (the prompts they would send, four
  characters a token, times `[llm] prices`) and call nothing; Costs has the measured per-stream
  figures.
- 11-08: every flag and positional has help (`clipper <command> -h`; a test keeps it so), `run -h`
  explains the fast lane's twin stages, the wiki stops calling `--limit` and `--from` universal,
  and `clipper doctor` warns on `[youtube] auto_upload_minutes = 0` (serve) and on a bot machine
  with `[discord] enabled = false`.
- 11-08: the defaults and `clipper.example.toml` describe the product, not this install:
  `[receipts]` and `[weekly] masthead`/`byline` are empty until set (`clipper receipts` and
  `clipper weekly` refuse with the fix; `clipper doctor` warns), the example's show is
  `[shows.myshow]`, and a test keeps this install's names out of `config.py`, the example and
  `clipper setup`.
- 11-08: `studio/` holds the mockups, the research notes and the media-heavy reports; `[paths]
  streamers` now defaults to `studio/research_notes/streamers.json` (set it if you keep that
  file elsewhere; it is read with a default when absent).
- 11-08: hand-over audit (`reports/code-audit-2026-11-08.md`) and its tier 0: the test suite green
  on Windows and run by CI on Linux and Windows; ruff rules widened (import order, bugbear,
  pyupgrade, blind excepts, stale noqa, McCabe over 15); `clipper --version`; stale docs retired;
  tests filed by subject; this file.
- 11-07: a Mac as a `control` role (`clipper setup`); Linux bootstrap installs the fonts; Shorts
  panels can focus on a face.
- 11-05: serve, the bot and the worker as containers on the NAS (`deploy/hub`), with the config
  and all state on the NAS share; Claude Code in a container on the NAS; scoring prompt v7 with
  `[scoring] gambling`; narrated story videos.
- 11-04: `clipper story` plan and cut; `clipper people`; `clipper compare` and OpenRouter models;
  ChudLogic as a show; YouTube cookies from a browser profile; the wiki as the docs.
- 11-03: `clipper serve` split out of the bot; `clipper investigate` (#108) and the search swarm
  (#109); the 11-03 audit's fixes (quiet failures, cost guards, scheduler consistency).
- 11-02: cast library (`clipper cast`) and the `cast` worker stage; news watcher; weekly pages
  in Discord.
- 11-01: first X post (`clipper x`) and auto-posting; TikTok parked; faces on screen
  (`clipper faces`).

### 2026-10

- 10-31: `clipper x` (X API).
- 10-28 to 10-30: the maintainability audit: tests and CI, `ClipperError`, `jsonstate`
  everywhere, the stages table, `clipper services`, the record and thumbs packages;
  `clipper setup` phase 1; analytics; the cover editor.
- 10-25 to 10-27: `clipper receipts`; speaker labels; cut on the NAS; the cost ledger
  (`clipper costs`); the LLM layer.
- 10-23 to 10-24: war room on the phone (Tailscale); Discord DMs; source links in descriptions;
  `clipper weekly`; Modal monitoring.
- 10-20 to 10-22: automatic uploads; Shorts with scene-cut layouts; the worker's fast lane;
  TikTok drafts; hand picks (`clipper pick`).
- 10-18 to 10-19: multi-show; pre-shows; Channel 404 News; the backlog push.
