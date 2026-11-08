# Changelog

Notable changes, newest first. The dated diary is `docs/log/<YYYY-MM>.md`; `HANDOFF.md` is the
current state; the version is `clipper --version` (set in `clipper/__init__.py` and
`pyproject.toml`, which a test keeps equal). Nothing has been released yet.

## Unreleased (0.1.0)

### 2026-11

- 11-08: a heartbeat read that lands while its writer replaces the file no longer counts as "no
  heartbeat": `daemon.read_heartbeat` reads a missing or unreadable heartbeat again (three reads,
  0.3 s apart) when its folder exists. `clipper record status` (exit 3 while a show is recorded,
  which `deploy/record/deploy.sh` trusts), `clipper status`, doctor and the war room read through it.
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
