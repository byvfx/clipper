`clipper checkup` looks for the problems the [alerts](Review#alerts) can't see, because nothing
ever raised an error: a stage that hangs instead of failing, a service that stops beating, a
recording the worker never starts, a release that misses its upload, a title that breaks the
house rules. It reads the files the services write, the same ones `clipper status` reads. It
makes no model call, touches no network and runs nothing. It prints what it found and exits 1
when it finds anything.

```bash
clipper checkup                  # every check
clipper checkup --only streams   # one check (repeatable)
clipper checkup --json           # the report as JSON, for a script or Claude
```

## What it checks

| Check | Finding | Setting |
|---|---|---|
| services | serve's, the bot's or the recorder's heartbeat is stale or missing. While the bot is down no DM arrives, so the report is the only place this shows up | |
| streams | a stage is still running after `stuck_hours`, but the worker is still beating | `stuck_hours` (6) |
| streams | run.json says running but the worker stopped beating: it died mid-run | `[worker] stale_minutes` × 2 |
| streams | a finished recording has waited `queue_hours` for the worker while nothing runs | `queue_hours` (3) |
| streams | a run failed with no retries left (also DM'd once, when it ran out) | `[worker] max_attempts` |
| schedule | a release isn't uploaded `late_hours` before its slot, or its slot has passed | `late_hours` (1) |
| schedule | nothing at all releases in the next `gap_hours` | `gap_hours` (24; 0 = off) |
| lint | a title, description or thumbnail text about to go out has one of `words` in it, is over YouTube's limit, or has no description. It checks what `clipper upload` would send (Kitsu's columns, then the draft) | `words` |

The settings are `[checkup]` in the config. A check that crashes is reported as a finding of
its own, so the other checks still run.

Each finding has a stable key (`stuck:kick-1:cut`, `late:kick-1/s04_x`). `--mark` saves the
keys in `streams/.checkup/state.json`, so the next run can mark which findings are new and
which it saw before ("since 11-08 08:30"). Only the scheduled run uses `--mark`. A run by hand
doesn't, so it never hides anything from the next scheduled run.

## The nightly report

The [claude container](Claude-on-the-NAS) runs the checkup every morning at `CHECKUP_AT`
(`deploy/claude/env.local`, e.g. `08:30`, after quiet hours; empty = off) and DMs you once:

- **Nothing found:** a one-line "Checkup 11-09: all good.", with no model call.
- **Findings:** a headless `claude -p` run (prompt: `deploy/claude/checkup.md`) reads the
  report, the stage logs, `logs/` and recent commits, and sends one DM with each problem, its
  likely cause and the next step. It runs on the Claude subscription, not the API key.
- **Claude can't run** (signed out, usage limit): the plain findings go out instead.

That run is read-only. It may use the read tools, `clipper checkup`, `status`,
`doctor --offline`, `record status`, `worker --status`, `alert send` and `git log/show/diff`,
and nothing else: no stage, no model call, no upload, no schedule, config or Kitsu change, no
commit, no PR. When a fix needs one of those, the DM says what to run.

It runs `main`'s code from its own worktree (`~/checkup-src` in the container, updated every
run), not the clone, which a phone session may have on any branch; `CHECKUP_REF` in
`env.local` picks another branch. Run it by hand with
`ssh nas 'cd clipper-claude && docker compose exec claude clipper-checkup --dry-run'`
(`--dry-run` prints the DM instead of sending it and doesn't `--mark`); the timer's output is in
`/tmp/checkup.log` in the container. The bot sends `checkup` DMs only once the hub runs code
that has the kind (deployed after 2026-11-09).

The DM is kind `checkup` in `[notify] kinds`, and `clipper alert send` is how any script queues
one: `clipper alert send --kind checkup "text"`, or `-` to read stdin.

Planned (#185): more checks (repeated errors in `logs/`, cookies and tokens about to expire,
Kitsu approvals that never reached the schedule), hourly runs once the nightly reports prove
right, and then PRs for clear code fixes.
