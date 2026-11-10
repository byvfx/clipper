Working on clipper itself. `CLAUDE.md` in the repo has the full set of rules for code sessions;
this page is the short version.

## Layout

```
clipper/              the package, one module per stage, cli.py wires them
clipper/prompts/      the prompt files, versioned, shipped inside the package; the version in use is set in clipper.toml
deploy/               bootstrap (new machine); compose stacks for the NAS hub (serve, bot, worker), the recorder, Kitsu and Claude; Windows task scripts for a GPU worker
streams/<id>/         one folder per stream: source, transcript, cuts, metadata, review state
clipper/defaults.toml every setting with its default, documented; config.py loads it (clipper.example.toml is a starter clipper.toml)
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
  env ([Configuration](Configuration)). Use `config.for_stream(cfg, sdir)` whenever a stream is known; `config.base(cfg)` is the config
  before any override. A two-letter section alias (`sc`, `rc`, `wc`) only in a module that reads
  one section; elsewhere spell the section out.
- Model calls go through `clipper/llm.py`; the provider is the model string's prefix
  ([Model providers](Model-providers)).
- Library code raises `ClipperError` (`clipper/errors.py`); only `cli.main` exits.
- Prompts are `clipper/prompts/<name>_<version>.md`, inside the package (`[paths] prompts` adds a
  folder of your own, read first); `prompts.VERSIONS` names the config key that holds each one's
  version (doctor and a test read it). Add a new version rather than editing one that produced
  reviewed output.
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
2. Its settings in `clipper/defaults.toml`, a comment on each (that file is the documentation;
   `config.DEFAULTS` is it loaded; a None default is a commented line there plus
   `config.NONE_DEFAULTS`). Nothing machine- or show-specific in a default (test).
3. A pipeline stage is also a row in `clipper/stages.py` (test).
4. A `clipper doctor` check when it needs a key, a service or a file, with the fix in the message.
5. A wiki page or section, in the sidebar (test), and `wiki.page(...)` links in its messages (test).
6. Tests for its pure logic in `tests/`, and the cost printed before any model call it makes. A
   new module needs a test that names it (`tests/test_modules_have_tests.py` fails otherwise).

## Checks

```bash
uvx ruff@0.17.3 check clipper tests
.venv/Scripts/python.exe -m pytest -q
```

Tests are offline and take about 25 s; new pure logic gets a test. CI runs both on every
push, on Linux and Windows (`.github/workflows/check.yml`), except a push or PR that changes only
notes no test reads: the root `*.md` files, `LICENSE`, `docs/log/`, `docs/archive/`, `reports/` and
`studio/`. Such a PR shows no checks at all; that is expected, not a stuck run. A change under
`docs/wiki/` still runs them, since a test checks the links and the sidebar. The ruff rules are the defaults plus
the few selected in `pyproject.toml` (`[tool.ruff.lint]`). Try risky changes on a short test stream
first. Before merging a PR that changes code, wait for the review below and handle each finding
(Copilot's automatic review is paused since 2026-11-10).

## Code review on PRs

Every PR that changes code (`clipper/`, `tests/`, `deploy/`, `.github/`, `pyproject.toml`) also
gets a second review from `clipper codereview` (the `review` workflow,
`.github/workflows/review.yml`). It sends the diff against main and the changed files in full to
`[codereview] reviewer` in one model call at high effort: Grok 4.7 through OpenRouter, about $1
and 25 minutes for a PR that changes 9 files. It posts the correctness bugs it finds as one
comment on the PR, and the same review on every issue the PR links (`Closes #70`, or `#70` in the PR's title or body). Each comment is edited in
place on later pushes, never added again. A push that leaves the PR's own code changes as they were
(a docs-only push, or merging main into the branch) isn't reviewed again: the comment carries a
digest of the PR's added and removed lines (a binary file's new contents, a file's mode), without
the context and positions a merge moves. Such
a push still copies the review to a linked issue that doesn't have it yet (a failed post, a
`Closes #N` added later), and a push that reverts all the PR's code changes replaces the findings
with a note that none stand. A notes-only PR, a draft or a
fork's PR gets no review. A failed review (no key, the model down, an answer with neither findings nor "No correctness
bugs found", a 30-minute timeout, a dropped
connection or a gateway's 408/502/504, never retried since each try may be paid for) says so in the comment, keeps the last good review below
it, and never fails the PR's checks. The review is printed in the workflow's log before it is
posted, so a failed post loses nothing.

It needs the repo secret `OPENROUTER_API_KEY`, set once from the laptop (set 2026-11-10), in Git Bash:

```bash
gh secret set OPENROUTER_API_KEY --repo byvfx/clipper < //192.168.0.155/docker/clipper/secrets/openrouter_secret.txt
```

or in PowerShell, which has no `<`:

```powershell
Get-Content \\192.168.0.155\docker\clipper\secrets\openrouter_secret.txt -Raw | gh secret set OPENROUTER_API_KEY --repo byvfx/clipper
```

To try another model, set `CLIPPER_CODEREVIEW_REVIEWER` in the workflow's env (e.g.
`openrouter:openai/gpt-5.6-sol-pro`). Run it locally before pushing with `clipper codereview`
(prints the review, nothing posted) or `clipper codereview --dry-run` (what would be sent and its
cost). Treat it as a second opinion: check each finding against the code.

Why Grok: on 2026-11-09, 13 models reviewed the same commit of PR #191 (about 88k tokens in).
The real findings were minor ones (loudness measured before the pauses were cut, a raw
subprocess error, a flag dropped for uploaded Shorts):

| Model (OpenRouter) | Effort | Real findings | False ones | Cost | Time |
|---|---|---|---|---|---|
| `openai/gpt-5.6-sol-pro` | high | 2 | 0 | $3.12 | 3.5 min |
| `x-ai/grok-4.7` | high | 2 | 0 | $0.87 | 25 min |
| `google/gemini-4-argon`, `moonshotai/kimi-k3.1-pro`, `z-ai/glm-5.3-prime` | high | 0 | 0 | $0.38–0.79 | 6–7 min |
| `deepseek/deepseek-v4-pro` | high | 0 (one guess) | 0 | $0.18 | 20 min |
| `z-ai/glm-5.3`, `moonshotai/kimi-k2.7-code`, `deepseek/deepseek-v4-flash` | medium | 0 | 0–1 | $0.02–0.15 | 3–20 min |
| `qwen/qwen3.8-max-0902`, `qwen/qwen3.8-flash`, `google/gemini-3.8-flash` | medium | 0–1 | 1 | $0.03–0.32 | 4–7 min |

The false one, from all three in the last row, was that `asetpts=N/SR/TB` collapses the audio:
for audio ffmpeg's `N` counts samples, so it is right. `qwen/qwen3.8-max-prime` used its whole
output on thinking and wrote nothing, as `deepseek/deepseek-v4-pro` did at medium effort before
`[codereview] thinking_tokens` gave it room.

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
- Keep the look plain: no emoji in headings or the sidebar, and no colours of our own in the
  Mermaid diagrams (GitHub's theme handles light and dark). The tabloid style belongs to the
  covers and the weekly, not to these pages.
