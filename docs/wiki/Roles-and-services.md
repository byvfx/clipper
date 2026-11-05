clipper runs in four roles, plus an optional operator. A machine can do more than one.

| Role | What runs there | Needs |
|---|---|---|
| **GPU worker** | `clipper worker`: transcribe, score, segments, then Shorts (and clips, if `[review] include` has `"picks"`) to review, then long segments to review | an NVIDIA GPU (8 GB+) or Apple Silicon, or none with `[transcribe] backend = "modal"` ([Transcribe on Modal](Transcribe-on-Modal)); the NAS share, ffmpeg |
| **Serve** | `clipper serve`: the [war room](War-room), hourly replan and upload, pre-show grabs, retakes, daily jobs | always on, the NAS share (or local streams), ffmpeg; the GPU extra, Modal or a CPU transcribe for [pre-shows](Pre-shows) |
| **Bot** | `clipper bot`: Discord review cards, buttons, DMs ([Review](Review)) | always on (usually the serve machine), the NAS share |
| **Recorder** | `clipper record watch` in Docker on the NAS | Docker; see [NAS recorder](NAS-recorder) |
| **Claude** (optional) | Claude Code in its own clone of the repo, driven from the Claude app on the phone | Docker on the NAS; see [Claude on the NAS](Claude-on-the-NAS) |

All roles share one streams folder (a NAS share). Only one machine runs serve and only one
runs the bot. Here serve, the bot and the worker run as containers on the NAS since
2026-11-05, with the config and state there too: see [NAS hub](NAS-hub).

## Services

On Windows, serve, the bot and the worker run as scheduled tasks (at logon, restarted if they
exit, no admin needed), installed once per machine ([Install on Windows](Install-Windows), step 8):

```powershell
deploy\worker\worker-task.ps1 -Install     # GPU worker
deploy\serve\serve-task.ps1 -Install       # clipper serve
deploy\discord\bot-task.ps1 -Install       # Discord bot
```

`-Status`, `-Restart`, `-Stop`, `-Uninstall` work on all three. `clipper services` shows them
plus the recorder and any cut running on the NAS, with serve's and the bot's heartbeats from the
share; `clipper services status|stop|restart serve|bot|worker|all` runs the same scripts, which
also stop the supervisor and stage processes (stopping the task alone doesn't).
`clipper services restart recorder` restarts the recorder over ssh, refused while it's
recording a show.

Restart serve after scheduler or war-room changes, the bot after Discord changes. With
`[serve] hub` set ([NAS hub](NAS-hub)), `clipper services` runs `docker compose` on the NAS over
ssh instead, and code changes go out with `bash deploy/hub/deploy.sh`.

## Serve and bot on different machines

The bot reaches serve at `[serve] url` (empty = this machine). On different machines set it,
give both the same `[warroom] token` (or `secrets/warroom_token.txt`), and put `[paths]
schedule` and `[paths] news` under the streams share on both (the bot reads what serve writes
there; `clipper doctor --role bot` checks).

## Logs and alerts

Logs go to `logs\` (`serve.log`, `bot.log`, `worker.log`). The bot DMs you when something needs
you ([Review](Review), "Alerts"). `clipper doctor --role gpu|serve|bot|nas|claude` checks a machine
and prints a fix for each problem.
