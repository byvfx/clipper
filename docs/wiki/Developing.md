Working on clipper itself. `CLAUDE.md` in the repo has the full set of rules for code sessions;
this page is the short version.

## Layout

```
clipper/              the package, one module per stage, cli.py wires them
prompts/              versioned prompt files; the version in use is set in clipper.toml
deploy/               bootstrap (new machine), NAS recorder (Docker), Kitsu (Docker), Windows worker, serve and bot tasks
streams/<id>/         one folder per stream: source, transcript, cuts, metadata, review state
clipper.example.toml  every setting, documented (a test checks it against config.py)
tests/                offline tests (pytest); CI runs them and ruff on every push
docs/wiki/            these pages; pushed to the wiki on every push to main
HANDOFF.md            current state between work sessions (short); docs/log/ has the dated diary
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

## Checks

```bash
uvx ruff@0.17.3 check clipper tests
.venv/Scripts/python.exe -m pytest -q
```

Tests are offline and take under a second; new pure logic gets a test. CI runs both on every
push (`.github/workflows/check.yml`). Try risky changes on the test stream `kick-131151926`
first. Before merging a PR, wait for Copilot's automatic review and handle each comment.

## Editing these docs

The wiki is built from `docs/wiki/` in the repo: edit the pages there, in the same PR as the
code they describe, and the `wiki` workflow (`.github/workflows/wiki.yml`) pushes them to the
wiki when the change lands on main. Edits made in the wiki's web editor are overwritten by the
next push.

- One file per page, flat: `Install-Windows.md` is the page "Install Windows". `_Sidebar.md`
  and `_Footer.md` are on every page.
- Link pages wiki-style, without `.md`: `[Faces](Faces)`, `[a section](War-room#faces)`. Link
  repo files with their full GitHub URL.
- `tests/test_wiki.py` checks that every page link resolves and that the sidebar lists every
  page.
