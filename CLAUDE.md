# clipper: notes for Claude Code

Read HANDOFF.md first: the current state between sessions (what runs where, what the user
decided, what is waiting on them, open work, recipes, gotchas), kept under ~300 lines. The
session diary is `docs/log/<YYYY-MM>.md`: grep it for history, don't read it whole. The user
docs are the GitHub wiki, built from `docs/wiki/` (one flat page per file, `Home.md` first,
`_Sidebar.md` lists every page); `.github/workflows/wiki.yml` pushes it on every push to main,
so edit the pages there, never in the wiki itself. Link pages wiki-style (`[Faces](Faces)`,
`[x](War-room#faces)`); `tests/test_wiki.py` checks the links and the sidebar. README.md is a
short pointer and SETUP.md maps its old sections to wiki pages (error messages still name
them). At the end of a session update HANDOFF.md in place (no diaries there) and add a dated
entry at the end of the month's log; keep the wiki pages true whenever behaviour changes, in
the same PR as the change.

## Shape

- Every pipeline stage is a `clipper <stage>` subcommand, wired in `clipper/cli.py`,
  one module per stage in `clipper/`. New functionality is a subcommand or a flag on one,
  never a loose script. A new command's parser goes in its group's `add_<group>` in cli.py
  and its name in `COMMAND_GROUPS` (a test checks); a new pipeline stage is also a row in
  `clipper/stages.py`, which the worker and `clipper run` read.
- A stream is `streams/<id>/`; stages read and write files there and are idempotent, so
  `clipper run` can skip what is done. Review state is `review.json`, retakes `retakes.json`.
  JSON files go through `clipper/jsonstate.py`: `load`/`read` (retry a file mid-replace),
  `write` (tmp + replace), `update`/`merge` (under a lock). A test fails on a raw
  `json.loads(path.read_text())`.
- Config: defaults in `clipper/config.py` < `clipper.toml` < `clipper.local.toml` (this machine's
  own values, written by `clipper setup`) < `CLIPPER_<SECTION>_<KEY>` env; the last two also win
  over show and stream overrides.
  `config.for_stream(cfg, sdir)` resolves the show from the stream's `meta.json` channel,
  then the stream's own `streams/<id>/config.toml` if any; use it rather than the base config
  whenever a stream is known.
- Shows: `[shows.<key>]` tables; a show's sub-tables override base sections. Each show has
  its own Kitsu production. Everything uploads to one YouTube channel.
- Model calls go through `clipper/llm.py` (`llm.Claude` is an alias of `LLM`); the provider is the
  model string's prefix (`openai:`, `gemini:`, `local:`, bare = Claude). Don't call an SDK directly.
- Errors: library code raises `ClipperError` (`clipper/errors.py`) with a one-line message, never
  `SystemExit`; only `cli.main` turns it into an exit. The bot, war room and worker catch it
  like any exception. A test fails on a new `raise SystemExit` outside cli.py.
- Prompts are `prompts/<name>_<version>.md` (loaded by `clipper/prompts.py`); the version in use is a config key. Add a new
  version rather than editing one that produced reviewed output.

## Rules

- No machine-specific values in `config.py` defaults: no IPs, hostnames, user ids, local
  paths. They go in `clipper.toml` (gitignored) or env vars, with a placeholder in
  `clipper.example.toml`. `clipper doctor` should fail with a fix when a required one is unset.
- Secrets are env vars (`ANTHROPIC_API_KEY`, `KITSU_TOKEN`, `DISCORD_BOT_TOKEN`,
  `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`, `ELEVENLABS_API_KEY`, `BRAVE_API_KEY` for cast image
  search, and `OPENAI_API_KEY` / `GEMINI_API_KEY` if a stage uses those providers) or files
  under `secrets/`. Never write them into tracked files, logs, or messages.
- Anything a new check or install step needs belongs in `clipper doctor` and its wiki page, not
  only in a chat answer (and in `clipper setup`, `clipper/setup.py`, when a new user would
  have to do it).
- Kitsu is the record for review decisions. Bot buttons and the war room act through
  `clipper/actions.py`; do not write decisions anywhere else.
- Uploads are automatic since 2026-10-20: `clipper serve` replans and runs `clipper upload` every
  `[youtube] auto_upload_minutes` (60 here; the default 0 means only by hand), private with publishAt, at most `max_per_24h`. Do not run
  `clipper upload` or `clipper review publish` by hand without being asked.
- Generated titles, descriptions and thumbnail text should avoid the word "hosts"; the
  user finds it reads as machine-written.

## Running things here

- Windows dev machine: the venv is `.venv` (uv). Run commands as `.venv/Scripts/clipper.exe ...`
  or `uv run clipper ...`. There is no `python` on PATH outside the venv.
- Keys are Windows *user* environment variables, which agent shells do not inherit. Load
  them first, for example in PowerShell:
  `$env:KITSU_TOKEN = [Environment]::GetEnvironmentVariable("KITSU_TOKEN","User")`.
- `clipper serve` (the war room and every background job), the Discord bot and the GPU worker
  run as scheduled tasks (`deploy/serve`, `deploy/discord`, `deploy/worker`).
  `clipper services status|stop|restart serve|bot|worker|all` runs their task scripts, which also
  stop the supervisor and stage processes (stopping the task alone doesn't). Restart serve after
  scheduler or war-room Python changes, the bot after Discord changes.
- The NAS recorder is deployed with `bash deploy/record/deploy.sh`, only when no show is live:
  the restart starts a new recording part. The script refuses while `clipper record status`
  shows a recording (exit 3); `--force` overrides it.
- Model calls cost money. Say what a rerun of `score`, `segments`, `meta` or `thumbs` will
  cost before doing it on a long stream. `score` refuses a rerun without `--force` (`clipper run
  --from` passes it), `cut` keeps picks whose in/out points are unchanged, `thumbs` keeps a cover
  whose cut is unchanged, and `weekly --render` never drafts.
- Before a hand edit of `schedule.json` or `clipper.toml`, copy it to
  `backups/<file>.before_<what>_<date>` (gitignored), not the repo root.

## Checks

- `clipper doctor --role gpu --offline` after changing config, ffmpeg, fonts or dependencies.
- Try risky changes on the test stream `kick-131151926` before a real one.
- Before committing: `uvx ruff@0.17.3 check clipper tests` and `.venv/Scripts/python.exe -m pytest -q`.
  The rules are ruff's defaults for the version pinned in pyproject (`required-version`); bump
  the pin there and in the dev group together. Tests are offline and take under a second; new
  pure logic gets a test in `tests/`. CI runs both on every push (`.github/workflows/check.yml`).
  Merging a PR: wait for Copilot's automatic review (a few minutes after the PR opens), handle
  each comment and show the user, then merge. Green CI alone isn't enough.
  Avoid `uv run`/`uv sync` while serve or the bot is up: they re-sync and cannot replace the running
  clipper.exe (`uv pip install --python .venv/Scripts/python.exe pytest` installs pytest alone).
