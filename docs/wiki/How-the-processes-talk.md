clipper runs as several processes that almost never call each other: `clipper serve`, the
Discord bot, the worker and the recorder as [NAS containers](NAS-hub), `clipper <command>` by
hand, and sometimes [Claude's container](Claude-on-the-NAS). They share one **streams folder**
(`[paths] streams`, the NAS share) and talk through five mechanisms. When something didn't
happen, this page says which file or log answers why.

## The streams folder is the bus

```
streams/<id>/                a stream: source, transcript, cuts, metadata, review.json, run.json, ...
streams/<id>/.run.lock       the worker holding this stream (its name inside)
streams/.serve.lock          one serve, one bot: OS locks held while the process lives
streams/.bot.lock
streams/.retake.lock         one retake run at a time
streams/.serve/status.json   heartbeats: serve, bot, recorder
streams/.bot/status.json
streams/.record/status.json
streams/.notify/outbox/      DMs waiting for the bot, one JSON file each
```

## 1. State files and their locks (`clipper/jsonstate.py`)

Every shared JSON file (`schedule.json`, `review.json`, `review_state.json`, `run.json`,
`thumbnails.json`, `discord.json`, `retakes.json`, ...) is written whole, through a temp file
and `os.replace`, so a reader sees the old file or the new one. Readers use `jsonstate.load`
or `jsonstate.read`, which retry a file another process is replacing (Windows and the NAS share
refuse the open for a moment); a raw `json.loads(path.read_text())` fails a test.

A change happens under `<file>.lock`, a file created exclusively beside the state file with
the holder's name inside (host, pid, time, a uuid). A writer waits up to **60 s** for it; a lock
older than **120 s** is taken for abandoned and removed. Keep the locked part short: no Kitsu or
YouTube calls inside. The error "`<file>.lock is held by another process for over 60 s (delete it
if nothing is running)`" means a holder died inside the window; it clears itself after 120 s.

## 2. One instance of each service

- **serve and the bot** hold `streams/.serve.lock` and `.bot.lock` open with an OS lock
  (`msvcrt` / `fcntl`) for their whole life (`daemon.single_instance`). A second copy refuses to
  start: "another clipper serve is already running". The file stays behind after a crash, the
  lock does not, so a leftover file means nothing; Health reads heartbeats, never this file.
- **retake** takes `streams/.retake.lock` (exclusive create, its token inside) and touches it
  while it works; another `clipper retake` says "another clipper retake is running". A lock
  untouched for **an hour** is taken over.
- **the worker, per stream,** takes `streams/<id>/.run.lock` with its name inside and writes a
  heartbeat into `run.json` every 60 s. Another worker takes the stream over only when that
  heartbeat is older than `[worker] stale_minutes` (10); the takeover itself runs under a
  `jsonstate` lock so two takers can't both win. A hand `clipper run` on a stream the worker
  holds says "`<stream>: running on <host>`".
- **the recorder** keeps each live recording's state in `streams/<id>/live/state.json` (parts,
  gaps, chat coverage) and finalizes from it.

## 3. Heartbeats (`clipper/daemon.py`)

serve and the bot write `streams/.serve/status.json` and `.bot/status.json` every **60 s**:
`{at, host, pid, started_at, ...}`. Without one for **180 s** a service counts as down. serve's
also carries the war room address (`warroom`, `tailnet`) and each background job's last run,
exit code, last line and next due time. The recorder writes `streams/.record/status.json` on
every pass (a few seconds) with each channel's state and who is live.

Readers: `clipper services status`, the [war room](War-room)'s Health panel and
`clipper doctor`, from any machine that sees the share. "Down" therefore means one of two
things: the process died, or it can't reach the share any more.

## 4. The DM outbox (`clipper/alerts.py`)

Any process calls `alerts.send(cfg, kind, text, key)`: one small JSON file
(`{kind, text, key, at, host}`) lands in `streams/.notify/outbox/`, named so it sorts by time,
written whole. It never raises; a lost alert mustn't stop the caller.

The bot drains the folder every minute: only the kinds in `[notify] kinds`, the same `key` at
most once per `cooldown_hours`, nothing during `quiet_hours` (held, then sent together), to
`[notify] user_id`. After the DMs go out it deletes the files and records what it sent in its own
state file under `.notify/`. The recorder's warnings also go straight to the webhook channel
(`[record] alert_webhook_env`), which is why they arrive twice, on purpose.

No DM arrived? Files piling up in the outbox mean the bot isn't draining (its heartbeat says
whether it is up). An empty outbox means the alert was filtered: the kind, the cooldown or the
quiet hours.

## 5. HTTP to serve, and one in-process hook

serve owns `schedule.json`: every replan runs inside its process. The bot (and doctor) reach it
over HTTP at `[serve] url` (empty = `http://127.0.0.1:<[warroom] port>/`) with the war room
token in an `X-Key` header (`[warroom] token` or `secrets/warroom_token.txt`, the same on both
machines): `POST /api/replan` after a decision, `POST /api/retake` after "Changes", `GET
/api/health` for the tailnet link. When serve is down the decision is still in Kitsu and
`review.json`; the bot says "not replanned" and the next hourly run places the item. A token
mismatch is an HTTP 403 in `bot.log`.

Inside serve, the war room page starts a retake through `warroom.HOOKS["retake"]`, a callback
serve installs, not HTTP. The page run on its own (`clipper warroom`) detaches a `clipper retake`
process instead and logs it to `logs/retake.log`.

## Child processes

- **serve** runs each background job as a `clipper <args>` child (upload, preshow check, retake,
  tiktok, x, analytics, cast auto, news check) and copies its output into `logs/serve.log`
  (`[paths] serve_log`). A non-zero exit queues a DM (kind `upload` or `failure`) keyed on the
  command and its last line, so a different item failing the next hour isn't swallowed by the
  cooldown.
- **the worker** runs each stage as `python -m clipper.cli <stage> <stream>`, output to
  `streams/<id>/run_<stage>.log`, status in `run.json` (`stages.<name>.status`, and on a failure
  `error: "<stage> exited N: <last three lines>"`). It resumes at the first unfinished stage.
- **cut** with `[cut] backend = "nas"` sshes to `[cut] nas_host` and runs a container named
  `clipper-cut-<id>`; a cut killed half way can leave it running: `ssh nas docker rm -f
  clipper-cut-<id>`.

## When something didn't happen

| Symptom | Look at |
|---|---|
| No upload this hour | `clipper services status` (serve's `upload` job: last exit, next due), `logs/serve.log`, `clipper upload --next 5` |
| A stream is stuck | `streams/<id>/run.json` (`status`, `error`, the stage), `run_<stage>.log`, who holds `.run.lock` |
| A button press says "not replanned" | serve is down or unreachable: `.serve/status.json`, `[serve] url`, the token (403 in `bot.log`) |
| No DM | the outbox folder (piling up: bot down; empty: filtered by kind, cooldown or quiet hours), `.bot/status.json` |
| Health says down | the heartbeat is over 180 s old: the service died, or it lost the share |
| "held by another process for over 60 s" | a `<file>.lock` left by a killed process; gone by itself after 120 s |
| The war room page is unstyled after a pull | serve reads `warroom.css` and `js/*.js` from disk: restart serve |
| A cut never finishes on the NAS | `ssh nas docker ps`, then `docker rm -f clipper-cut-<id>` |

## Print or log?

A **stage** (`clipper <stage>`, `clipper run`) prints its progress to stdout: when the worker or
serve runs it as a child, that output is what lands in `run_<stage>.log` or `serve.log`. A
**service** (serve, bot, worker, recorder) logs through `logging` (`daemon.setup_logging`), never
`print`, so every line carries a time and a level and rotates with the file (`logs/serve.log`,
`logs/bot.log`, 5 MB x 5). In the containers the same lines are `docker compose logs`.
