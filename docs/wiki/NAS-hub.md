The NAS can run everything that has to stay up: `clipper serve` (the [war room](War-room) and
its jobs), the Discord bot and the worker, beside the [recorder](NAS-recorder), with
transcription on [Modal](Transcribe-on-Modal). The laptop can then sleep; it is only for
working on clipper. This has been the setup here since 2026-11-05.

## What runs where

| Container (`deploy/hub`) | Runs | Notes |
|---|---|---|
| `serve` | `clipper serve` | war room on 127.0.0.1:8765, hourly replan and upload, retakes, daily jobs |
| `bot` | `clipper bot` | Discord; reaches serve on 127.0.0.1 |
| `worker` | `clipper worker` | every stage of each finished recording; cuts locally (`CLIPPER_CUT_BACKEND=local`) |
| `tailscale` | tailscaled, userspace | serves the war room on the tailnet from `tailscale-serve.json` ([Phone access](Phone-access)) |

All four use host networking and their own compose project (`clipper-hub`), so a deploy never
restarts the recorder. The image is Debian 13 (ffmpeg 7.1) with Intel's media driver:
`/dev/dri` is passed in and `h264_qsv` encodes on the NAS iGPU, as on the laptop.

Claude Code can run beside them too, in its own container, so clipper can be driven and
changed from the Claude app on the phone: [Claude on the NAS](Claude-on-the-NAS).

## The config and state live on the NAS

The NAS folder `/volume1/docker/clipper` (`/data` in the containers, the `docker/clipper` share
from the laptop) holds the one `clipper.toml` and everything `[paths]` points at, all relative
to it: `streams/`, `schedule.json`, `secrets/`, `logs/`, `faces/`, `voices/`, `news/`,
`weekly/`, `photos/`, `archive/`, `layouts/`, `rumble/`, `people.json`. Edit `clipper.toml` there
(back it up to `backups/` first). Neither deploy script copies a config over it any more.

On the laptop, the user environment variable `CLIPPER_CONFIG` points at it
(`//192.168.0.155/docker/clipper/clipper.toml`); clipper also reads it from the registry when a
shell didn't inherit it, and `./clipper.toml` still wins if there is one. Prompts come with the
code (`clipper/prompts/`, inside the package), the same on every machine.

`schedule.json` stores each video as `<stream>/<folder>/<name>.mp4`, relative to `[paths]
streams`, so it means the same file on every machine. Older entries with a full path are mapped by
the part from the stream's folder on.

## Fonts

Covers, the weekly issue and Shorts captions want Impact, Bahnschrift, Georgia and Arial. Put
the `.ttf` files in the NAS folder's `fonts/` (copied from `C:\Windows\Fonts`): the containers
mount it as `/usr/local/share/fonts/clipper`, where libass finds them by family and clipper
by file name (a font list's `C:/Windows/Fonts/impact.ttf` finds `impact.ttf` there).
`clipper doctor` names the font each check found.

## Deploy

`bash deploy/hub/deploy.sh` from the repo root (Git Bash on Windows; ssh alias `nas`) builds
the image from the checkout (code and prompts) and starts or updates all four. Deploy after
code changes: this replaces restarting serve or the bot. A stage or upload running at that
moment stops; the worker resumes the stream at its first unfinished stage, and an upload that
didn't finish goes again on the next hourly run. `bash deploy/hub/deploy.sh bot` updates one.

The first run copies `deploy/hub/env.template` to `deploy/hub/env.local` (gitignored) and
stops: fill in the keys (`ANTHROPIC_API_KEY`, `KITSU_TOKEN`, `MODAL_TOKEN_ID` /
`MODAL_TOKEN_SECRET` from `~/.modal.toml`, the alert webhook). The other keys are files in
`secrets/`. There is no browser in the containers, so pre-shows use
`secrets/youtube_cookies.txt` (`CLIPPER_YOUTUBE_COOKIES_FROM_BROWSER` is empty there), which
the laptop's `clipper-cookies` task refreshes from its Firefox profile every 4 hours
([Archives](Archives#keeping-the-nas-hubs-cookies-fresh)). That one job ties the NAS to the
laptop: with the laptop off for days, signed-in pre-show grabs stop once the cookies rotate.

## Day to day

With `[serve] hub = "clipper-hub"`, `clipper services status|stop|restart serve|bot|worker`
on the laptop runs `docker compose` on the NAS over ssh. Logs: `logs/serve.log` and
`logs/bot.log` in the NAS folder as before, plus `ssh nas 'cd clipper-hub && docker compose
logs -f serve'`. Check a container with `ssh nas 'cd clipper-hub && docker compose exec serve
clipper doctor --role serve'`.

> [!WARNING]
> Only one serve and one bot may run. A laptop task and a NAS container don't see each
> other's lock (Windows over SMB vs. the NAS's own disk), so keep the laptop's tasks disabled
> (`clipper-serve`, `clipper-bot`, `clipper-worker` in Task Scheduler).

## Back to the laptop

Stop the containers (`ssh nas 'cd clipper-hub && docker compose stop serve bot worker'`), set
`[serve] hub = ""`, and enable the three scheduled tasks again ([Roles and
services](Roles-and-services)). The config and state stay on the NAS; the laptop reads them over
the share.
