`clipper audit` tells you how the week went, and once a month a deep audit reads the saved
weeks to find what keeps going wrong. Where [Checkup](Checkup) asks "what is wrong right now",
this one looks for patterns.

## The weekly digest

```bash
clipper audit         # the digest, at most three things to do
clipper audit --all   # every thing to do, and where to look
```

It reads files and makes no model call, so it costs nothing. It covers:

| Section | What it says |
|---|---|
| To do | At most three things, what is breaking now first |
| Ran | Streams done, partial and failed this week |
| Out | Uploads by kind, and any that missed their slot |
| Spend | The week's model and Modal spend, biggest stages first |
| Review | What you approved and rejected |
| Disk | Free space on the streams share |
| Repo | Open PRs and branches besides `main` |
| Best | The most-watched video published this week |

A section that can't be read becomes a thing to do, and the others still run. A stream that
failed and then finished counts as finished, because `run.json` keeps only the last result.

The [claude container](Claude-on-the-NAS) runs it after the morning checkup on `AUDIT_WEEKDAY`
(`deploy/claude/env.local`; 1 is Monday, 7 is Sunday, the default) and DMs it to you. Each week
is kept as `streams/.audit/weekly-<date>.json`, which is what the deep audit reads. A week it
misses (the container was down) is simply missing, and nothing catches up.

The settings are `[audit]` in the config.

### Backups

Set `backup_folder` to where your backups land. Until you do, the digest says every week that
nothing is backing up your files, and once it is set, it warns when the newest file there is
older than `backup_max_days`. It only looks at the date of the newest file, so restore from a
backup now and then to know it works.

### Reminders

For a decision you want to come back to, add a dated line to `reminders` in `clipper.toml`:

```toml
[audit]
reminders = ["2026-11-16 compare Kino v8 with v7"]
```

It shows from a week before the date until you take the line out.

## The monthly deep audit

On `AUDIT_MONTHDAY` (default the 1st) the container gives `clipper audit --brief`, the last
`deep_weeks` weeks side by side with repeat problems first, to a headless `claude -p` run
(prompt: `deploy/claude/audit-deep.md`). It follows the repeat problems into the stage logs and
the code, looks for drift in spend, failures, disk and uploads, reads why picks were rejected,
and checks what came of last month's findings.

You get one DM with the top three findings. The full report, with the evidence, is
`streams/.audit/deep-<YYYY-MM>.md`, and next month's run reads it. The deep audit needs at least one saved week.

It runs on the Claude subscription, not the API key, and it is read-only in the same way as
[the nightly report](Checkup#the-nightly-report): the read tools, a fixed list of commands, and
one DM. It can't change anything. When a fix needs a change, the report says what to do. If
Claude can't run (signed out, usage limit), you get the repeat problems alone.

To try either one by hand:

```bash
ssh nas 'cd clipper-claude && docker compose exec claude clipper-audit --weekly --dry-run'
```

```bash
ssh nas 'cd clipper-claude && docker compose exec claude clipper-audit --deep --dry-run'
```

`--dry-run` prints the DM instead of sending it and saves nothing. The timer's output is in
`/tmp/audit.log` in the container.

## Dependency updates

`.github/dependabot.yml` opens up to two grouped PRs a week, one for Python packages and one for
GitHub Actions. CI runs on them and nothing merges by itself. They get no code review comment,
since a Dependabot PR has no secrets, so merge on green CI and your own word.
