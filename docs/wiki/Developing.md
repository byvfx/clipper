Working on clipper itself. `CLAUDE.md` in the repo has the full set of rules for code sessions;
this page is the short version.

## Layout

```
clipper/              the package, one module per stage, cli.py wires them
prompts/              versioned prompt files; the version in use is set in clipper.toml
deploy/               bootstrap (new machine); compose stacks for the NAS hub (serve, bot, worker), the recorder, Kitsu and Claude; Windows task scripts for a GPU worker
streams/<id>/         one folder per stream: source, transcript, cuts, metadata, review state
clipper.example.toml  every setting, documented (a test checks it against config.py)
tests/                offline tests (pytest); CI runs them and ruff on every push
docs/wiki/            these pages; pushed to the wiki on every push to main
HANDOFF.md            current state between work sessions (short); docs/log/ has the dated diary
studio/               the studio's working files: mockups, research notes, media-heavy reports (not the package)
reports/              the audits and the monthly analytics reports
```

## Shape

- Every pipeline stage is a `clipper <stage>` subcommand, wired in `clipper/cli.py`, one module
  per stage. A new pipeline stage is also a row in `clipper/stages.py`, which the worker and
  `clipper run` read.
- Stages read and write files in `streams/<id>/` and are idempotent. JSON state goes through
  `clipper/jsonstate.py`.
- Config: defaults in `clipper/config.py` < `clipper.toml` < `clipper.local.toml` < `CLIPPER_*`
  env ([Configuration](Configuration)). Use `config.for_stream(cfg, sdir)` whenever a stream is known.
- Model calls go through `clipper/llm.py`; the provider is the model string's prefix
  ([Model providers](Model-providers)).
- Library code raises `ClipperError` (`clipper/errors.py`); only `cli.main` exits.
- Prompts are `prompts/<name>_<version>.md`; add a new version rather than editing one that
  produced reviewed output.
- Kitsu is the record for review decisions; the bot and the war room act through
  `clipper/actions.py`.
- Stages print, services log. A `clipper <stage>` prints its progress (the worker and serve copy
  a child's output into `run_<stage>.log` and `serve.log`); serve, the bot, the worker and the
  recorder log through `logging`, never `print`. The processes share state through the streams
  folder: [How the processes talk](How-the-processes-talk) lists the locks, heartbeats, the DM
  outbox and serve's API, and which log answers which question.

## Adding a command or a stage

Each step is checked by a test where it can be, so a missing one fails `pytest`, not a user:

1. The parser and `cmd_<name>` in `clipper/cli.py`, in its group's `add_<group>`, and the name in
   `COMMAND_GROUPS` (test).
2. Its settings in `config.DEFAULTS`, a comment on each, and the same keys in
   `clipper.example.toml` (test). Nothing machine- or show-specific in a default.
3. A pipeline stage is also a row in `clipper/stages.py` (test).
4. A `clipper doctor` check when it needs a key, a service or a file, with the fix in the message.
5. A wiki page or section, in the sidebar (test), and `wiki.page(...)` links in its messages (test).
6. Tests for its pure logic in `tests/`, and the cost printed before any model call it makes.

## Checks

```bash
uvx ruff@0.17.3 check clipper tests
.venv/Scripts/python.exe -m pytest -q
```

Tests are offline and take about 25 s; new pure logic gets a test. CI runs both on every
push, on Linux and Windows (`.github/workflows/check.yml`). The ruff rules are the defaults plus
the few selected in `pyproject.toml` (`[tool.ruff.lint]`). Try risky changes on the test stream `kick-131151926`
first. Before merging a PR, wait for Copilot's automatic review and handle each comment.

## Editing these docs

> [!IMPORTANT]
> The wiki is built from `docs/wiki/` in the repo: edit the pages there, in the same PR as the
> code they describe. Edits made in the wiki's web editor are overwritten by the next push.

The `wiki` workflow (`.github/workflows/wiki.yml`) pushes the pages to the wiki when a change
lands on main. Images go in `docs/wiki/images/` and are linked by their full wiki address
(`https://github.com/byvfx/clipper/wiki/images/<file>`). Callouts (`> [!TIP]`, `> [!WARNING]`),
Mermaid diagrams and `<kbd>` keys all render.

- One file per page, flat: `Install-Windows.md` is the page "Install Windows". `_Sidebar.md`
  and `_Footer.md` are on every page.
- Link pages wiki-style, without `.md`: `[Faces](Faces)`, `[a section](War-room#faces)`. Link
  repo files with their full GitHub URL.
- `tests/test_wiki.py` checks that every page link resolves and that the sidebar lists every
  page.
