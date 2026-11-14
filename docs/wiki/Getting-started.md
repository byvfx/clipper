> [!TIP]
> Try anything risky on a short test stream first: fetch any short VOD and run it end to end.

## Install a machine

From the clipper folder on a new machine (keep the folder path short on Windows, e.g. `C:\clipper`):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\bootstrap.ps1   # macOS/Linux: bash deploy/bootstrap.sh
```

It installs uv, Python 3.12 and ffmpeg if they're missing, puts a `clipper` command on PATH
(open a new terminal to use it), then runs `clipper setup`, a
walkthrough that asks whether this machine joins an existing install or starts a new one,
what it runs, where it transcribes, its streams folder and encoder, and the keys (checked as
you enter them), then runs `clipper doctor`. Run it again to carry on; `clipper setup --step
keys` redoes one step and `clipper setup --check` shows where each step stands.

[Install](Install) has the full per-platform steps, including what setup doesn't do yet
(Discord ids, YouTube sign-in, [adding a show](Adding-a-show)), the [NAS recorder](NAS-recorder)
and running the worker and bot unattended.

## One stream end to end

```bash
clipper fetch <kick vod url>
clipper run <stream id>
```

`fetch` keeps going past a video segment Kick no longer has (a gap beats no video) but says how
many it skipped and stores the count as `skipped_fragments` in the stream's `meta.json`: clips
over a gap jump there. A download that fails part way resumes on the next `fetch` and keeps the
count of the segments it skipped before (in `source.ts.skipped` until it finishes).

Each stage skips what's done; `clipper run <id> --from cut` redoes from a stage on
([Pipeline](Pipeline)). Review the items in the [war room](War-room) or on the Discord cards
([Review](Review)); `clipper serve` schedules approved ones and uploads every `[youtube]
auto_upload_minutes` (`clipper schedule`, `clipper upload` by hand when that's 0; see
[Scheduling and uploads](Scheduling-and-uploads)).

## Check

`clipper status` is the one screen: the services' heartbeats and the recorder, each
recording's stages, review counts, the next releases and the month's spend. Behind it:
`clipper doctor --role gpu|serve|bot|nas` (this machine's setup), `clipper worker --status`,
`clipper review status` (decisions and what's scheduled) and `clipper costs` ([Costs](Costs)).

## Stop and restart

`clipper serve`, the bot and the worker run as containers on the NAS:
`clipper services status|stop|restart serve|bot|worker` runs `docker compose` there, and code
changes go out with `bash deploy/hub/deploy.sh` ([Roles and services](Roles-and-services#services),
[NAS hub](NAS-hub)). On a single Windows machine without Docker they are scheduled tasks instead,
driven by the same command. The recorder is its own container on the NAS:
`bash deploy/record/deploy.sh`, only when no show is live (a restart starts a new recording part).

## When something breaks

The bot DMs you (`[notify]`, see [Review](Review)); logs are in `logs/` (`serve.log`,
`bot.log`, `worker.log`); `clipper doctor` prints a fix for each problem it finds. `clipper
checkup` finds the quiet ones: a stuck stage, a service that stopped, a late upload
([Checkup](Checkup)).

## Commands and flags

`clipper -h` lists the commands by group; `clipper <command> -h` gives each one's flags, every
one explained. A flag means the same thing wherever it appears: `--items s02,c03` picks items
(the old `--ids`, `--keys`, `--item` and `--segments` still work), times are `H:MM:SS`, `H:MM`
or seconds, `--dry-run` changes nothing and says what would happen, `--force` redoes what is
already done, and `--no-sync` skips the Kitsu read. Two are command-specific: `--from` is a
stage on `run` and `story run` but a time on `pick` and `preshow fetch`, and `--limit N` caps
only `upload` and the `archive` and `investigate` commands.
Flags for different jobs on one command (`upload --links` and `--next`, say) are refused together.

A stream argument takes the id (`kick-137022368`), a stream folder, or a shortcut: `latest` is
the stream that started last (a recording still going has no start yet and is skipped),
`latest:kino` that show's, and any unique start of an id works (`kick-13702`); a mistyped id
names the nearest ones. The read commands take `--json` for a script or the Claude container:
`status`, `review list`, `schedule --list`, `shows`, `costs`, `config diff`, `checkup`.

Tab completion: `clipper completion powershell` (or `bash`) prints a script for the shell,
made from this version's commands, actions and flags; stream arguments complete to `latest`
and `latest:<show>`. Write it to a file once and load that from your profile, and make it
again after an update that adds commands:

```powershell
clipper completion powershell > ~/clipper-completion.ps1
Add-Content $PROFILE '. ~/clipper-completion.ps1'
```

(bash: `clipper completion bash > ~/.clipper-completion.bash` and source it from `~/.bashrc`.)
A long `clipper run` logs "stage 4 of 15" as each stage starts.
