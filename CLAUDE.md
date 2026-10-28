# clipper: notes for Claude Code

Read HANDOFF.md first: the current state between sessions (what runs where, what the user
decided, what is waiting on them, open work, recipes, gotchas), kept under ~300 lines. The
session diary is `docs/log/<YYYY-MM>.md`: grep it for history, don't read it whole. README.md
says what the project is, SETUP.md how to install it. At the end of a session update
HANDOFF.md in place (no diaries there) and add a dated entry at the end of the month's log;
keep README and SETUP true whenever behaviour changes.

## Shape

- Every pipeline stage is a `clipper <stage>` subcommand, wired in `clipper/cli.py`,
  one module per stage in `clipper/`. New functionality is a subcommand or a flag on one,
  never a loose script.
- A stream is `streams/<id>/`; stages read and write files there and are idempotent, so
  `clipper run` can skip what is done. Review state is `review.json`, retakes `retakes.json`.
- Config: defaults in `clipper/config.py` < `clipper.toml` < `CLIPPER_<SECTION>_<KEY>` env.
  `config.for_stream(cfg, sdir)` resolves the show from the stream's `meta.json` channel,
  then the stream's own `streams/<id>/config.toml` if any; use it rather than the base config
  whenever a stream is known.
- Shows: `[shows.<key>]` tables; a show's sub-tables override base sections. Each show has
  its own Kitsu production. Everything uploads to one YouTube channel.
- Model calls go through `clipper/llm.py` (`scoring.Claude` is an alias); the provider is the
  model string's prefix (`openai:`, `gemini:`, `local:`, bare = Claude). Don't call an SDK directly.
- Prompts are `prompts/<name>_<version>.md`; the version in use is a config key. Add a new
  version rather than editing one that produced reviewed output.

## Rules

- No machine-specific values in `config.py` defaults: no IPs, hostnames, user ids, local
  paths. They go in `clipper.toml` (gitignored) or env vars, with a placeholder in
  `clipper.example.toml`. `clipper doctor` should fail with a fix when a required one is unset.
- Secrets are env vars (`ANTHROPIC_API_KEY`, `KITSU_TOKEN`, `DISCORD_BOT_TOKEN`,
  `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`, `ELEVENLABS_API_KEY`, and `OPENAI_API_KEY` /
  `GEMINI_API_KEY` if a stage uses those providers) or files
  under `secrets/`. Never write them into tracked files, logs, or messages.
- Anything a new check or install step needs belongs in `clipper doctor` and SETUP.md, not
  only in a chat answer.
- Kitsu is the record for review decisions. Bot buttons and the war room act through
  `clipper/actions.py`; do not write decisions anywhere else.
- Uploads are automatic since 2026-10-20: the bot replans and runs `clipper upload` every
  `[youtube] auto_upload_minutes` (60), private with publishAt, at most `max_per_24h`. Do not run
  `clipper upload` or `clipper review publish` by hand without being asked.
- Generated titles, descriptions and thumbnail text should avoid the word "hosts"; the
  user finds it reads as machine-written.

## Running things here

- Windows dev machine: the venv is `.venv` (uv). Run commands as `.venv/Scripts/clipper.exe ...`
  or `uv run clipper ...`. There is no `python` on PATH outside the venv.
- Keys are Windows *user* environment variables, which agent shells do not inherit. Load
  them first, for example in PowerShell:
  `$env:KITSU_TOKEN = [Environment]::GetEnvironmentVariable("KITSU_TOKEN","User")`.
- The Discord bot and GPU worker run as scheduled tasks (`deploy/discord`, `deploy/worker`).
  Stopping the task does not stop the supervised process; see the gotchas in HANDOFF.md.
- The NAS recorder is deployed with `bash deploy/record/deploy.sh`, only when no show is live:
  the restart starts a new recording part.
- Model calls cost money. Say what a rerun of `score`, `segments`, `meta` or `thumbs` will
  cost before doing it on a long stream.

## Checks

- `clipper doctor --role gpu --offline` after changing config, ffmpeg, fonts or dependencies.
- Try risky changes on the test stream `kick-131151926` before a real one.
- `uvx ruff check clipper` before committing (ruff is not in the venv; the rule set is the user-level ruff config, not pyproject). The tree is clean as of 2026-10-19; keep it that way. Avoid `uv run` while the bot is up: it re-syncs and cannot replace the running clipper.exe.
