> [!TIP]
> Try anything risky on the test stream `kick-131151926` before a real one.

## Install a machine

From the clipper folder on a new machine (keep the folder path short on Windows, e.g. `C:\clipper`):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\bootstrap.ps1   # macOS/Linux: bash deploy/bootstrap.sh
```

It installs uv, Python 3.12 and ffmpeg if they're missing, then runs `clipper setup`, a
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

Each stage skips what's done; `clipper run <id> --from cut` redoes from a stage on
([Pipeline](Pipeline)). Review the items in the [war room](War-room) or on the Discord cards
([Review](Review)); `clipper serve` schedules approved ones and uploads every `[youtube]
auto_upload_minutes` (`clipper schedule`, `clipper upload` by hand when that's 0; see
[Scheduling and uploads](Scheduling-and-uploads)).

## Check

`clipper doctor --role gpu|serve|bot|nas` (this machine), `clipper worker --status` (each
recording's stages), `clipper review status` (decisions and what's scheduled), `clipper costs`
(the month's spend, [Costs](Costs)).

## Stop and restart

`clipper serve`, the bot and the GPU worker are Windows scheduled tasks:
`clipper services status|stop|restart serve|bot|worker` ([Roles and services](Roles-and-services)).
The recorder is a container on the NAS: `bash deploy/record/deploy.sh`, only when no show is
live (a restart starts a new recording part).

## When something breaks

The bot DMs you (`[notify]`, see [Review](Review)); logs are in `logs/` (`serve.log`,
`bot.log`, `worker.log`); `clipper doctor` prints a fix for each problem it finds.

## Commands and flags

`clipper -h` lists the commands by group; `clipper <command> -h` gives each one's flags. The
same flag means the same thing everywhere: `--items s02,c03` picks items (the old `--ids`,
`--keys`, `--item` and `--segments` still work), times are `H:MM:SS`, `H:MM` or seconds,
`--limit N` caps a run, `--dry-run` changes nothing, and `--no-sync` skips the Kitsu read.
Flags for different jobs on one command (`upload --links` and `--next`, say) are refused together.
