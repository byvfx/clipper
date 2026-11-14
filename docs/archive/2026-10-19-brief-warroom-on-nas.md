# Brief: run the war room on the NAS

Written 2026-10-19, after all stream folders moved to the NAS share. Read CLAUDE.md and
HANDOFF.md first. Another session may be editing `warroom.py`, `bot.py` and `config.py`:
check `git status` / `git diff` before starting, and build on what is there.

## Goal

Serve `clipper warroom` from a Docker container on the NAS, next to the recorder, so the
dashboard reads stream files from local disk instead of over SMB. It should also stay up
while the laptop sleeps, and be reachable from a phone on the LAN. The Discord bot and the
GPU worker stay on the laptop.

## Why (measured 2026-10-19, NAS busy with pipeline runs)

- Each file operation over the share costs 20-30 ms. Before the caching added that day,
  `/api/overview` took over 120 s.
- With the per-stream cache (`warroom.py`: `_streams`, `_cached`, `review.Listing`), warm
  loads take 0.2-0.7 s and `/api/items` about 20 ms. A cold load still takes about 30 s,
  and a stream rebuilds in a few seconds after any write in its folder (approve, reject,
  heartbeat).
- Media previews travel NAS -> laptop -> browser. On the NAS they would be read straight
  from disk.

What's left to gain is cold loads, post-action rebuilds and video seeking, plus uptime.

## How it works today

- `clipper serve` (clipper/serve.py, since 2026-11-03; before that the bot) serves the war
  room in a thread. Defaults: host 127.0.0.1, port 8765. Off localhost,
  `warroom.server()` refuses to start without `[warroom] token`.
- Actions (`/api/action`) go through `clipper/actions.py` (Kitsu is the record). With
  `[discord] replan`, approve/reject/changes/reopen/hold/pin also run
  `schedule.run(cfg, sync_first=False)`, which rewrites `schedule.json`. `/api/replan` does
  the same, and `/api/sync` syncs Kitsu into each stream's `review.json`.
- Retakes: `start_retake()` spawns `clipper retake` as a subprocess on the same machine.
  The bot also finds retakes on its own: `retake.pending()` looks for
  `changes_requested` entries in `review.json` after each card refresh
  (`[discord] refresh_minutes = 10`) when `[retake] auto`.
- The recorder image (`deploy/record/Dockerfile`: python 3.12 slim + ffmpeg, `uv sync
  --frozen`, ENTRYPOINT `clipper`) can run `clipper warroom` as it is. It sets
  `CLIPPER_CONFIG=/data/clipper.toml` and `CLIPPER_PATHS_STREAMS=/data/streams`.
  `deploy/record/deploy.sh` copies the laptop's `clipper.toml` to `/data/clipper.toml`.

## What has to change

1. **Container.** Add a `warroom` service, either in `deploy/record/compose.yaml` or its
   own compose file. It reuses the image with `command: ["warroom"]`, port 8765, and
   `CLIPPER_WARROOM_HOST=0.0.0.0`, and takes `KITSU_TOKEN` and the war room token from the
   gitignored env file (`deploy/record/env.local`). Deploying or updating it must not
   restart the recorder: CLAUDE.md says to deploy the recorder only when no show is live,
   because a restart starts a new recording part. Use `docker compose up -d warroom`, or
   give the war room its own deploy script.
2. **Only one war room.** Set `CLIPPER_WARROOM_WITH_BOT=false` for the laptop bot (via
   `deploy/discord`), or make it the default once the NAS war room is the norm.
3. **Retakes stay on the laptop.** They need the Anthropic key and the laptop's encoder
   (h264_qsv); the NAS has neither. On the NAS, "changes" should only record the decision
   through `actions.changes`, which already leaves the `changes_requested` entry that
   `retake.pending()` finds, and should not spawn anything. So that retakes don't wait up
   to 10 minutes for the bot's refresh, add a small request file on the share (for example
   `streams/.retake.request`). The NAS war room writes it for "changes" and for the Retake
   button, and the bot polls it every ~30 s and runs `start_retake()`. The war room should
   show "queued for the laptop" rather than "working on it".
4. **`schedule.json` must be shared and portable.** It lives at `C:\clipper\schedule.json`,
   and its entries' `file` holds absolute Windows UNC paths
   (`\\192.168.0.155\docker\clipper\streams\...`), written at `schedule.py` ~61 and ~94.
   The NAS war room runs `schedule.run` on approve, which would write `/data/streams/...`
   paths that the laptop's upload code can't open (`youtube.py` ~80 and ~305, `rumble.py`
   ~102, ~171 and ~293).
   - Move `schedule.json` onto the share: `[paths] schedule` on the laptop,
     `CLIPPER_PATHS_SCHEDULE=/data/schedule.json` in the container.
   - Store `file` relative to the streams root (for example
     `kick-132380636/clips/01_x.mp4`) and resolve it against `paths.streams` wherever it's
     read. Keep reading old absolute entries, and rewrite them once.
   - The "uploaded" entries `schedule.py` keeps verbatim need the same handling.
5. **Writers on two machines.** The bot (laptop) and the war room (NAS) both change
   `review.json`, `review_state.json` and `schedule.json` through `actions.py` and
   `schedule.run`. Today the only locks are in-process: `warroom.LOCK` and the bot's
   `asyncio.Lock`. Those files are also written in place with `write_text`
   (`review.py` ~512-758, `schedule.py` ~246), so a reader can see half a file.
   - Add a lock file on the share around actions and `schedule.run`, shaped like
     `worker.Run.acquire`: create it exclusively, write the holder's name, treat it as
     stale after a minute or two.
   - Write those JSON files atomically: write a temp file, then replace, retrying on
     `PermissionError`. `worker.write_json` already does this; share it.
   - Over SMB, Windows refuses to rename over a file another process has open. That's the
     "Access is denied" seen on `run.json` on 2026-10-19.
6. **Doctor and docs.** Add a war room role to `clipper doctor`: token set when host isn't
   localhost, `KITSU_TOKEN` present, streams root readable, schedule path writable. Add a
   SETUP.md section for deploying it and opening it from a phone (`http://<nas>:8765/?key=<token>`),
   and log the change in HANDOFF.md.

## Out of scope

(2026-10-20: both now planned. Tailscale access is byvfx/clipper#10, the bot and worker
on the NAS #12, under the roadmap #14. This brief is issue #9.)

- Moving the Discord bot or the GPU worker to the NAS.
- Exposing the war room outside the LAN. No port forwarding; the token is LAN-grade only.

## Done when

- The NAS container serves the war room. A cold `/api/overview` takes a few seconds at
  most, and warm loads are under 0.5 s.
- From the NAS war room, approve/reject/hold/pin update Kitsu, `review.json` and the shared
  `schedule.json`. The laptop bot's Discord cards pick up the change on their next refresh.
- "Changes" from the NAS war room starts a retake on the laptop within about a minute.
- `clipper upload --dry-run` on the laptop resolves every `schedule.json` file path.
- `uvx ruff check clipper` passes. Test risky changes on `kick-131151926` first.
- The recorder never restarted during deploys (check `docker compose ps` uptime).

## Facts that matter

- NAS: SSH alias `nas`; compose dir `~/clipper-record`; data dir `/volume1/docker/clipper`
  (`/data` in containers); streams at `/volume1/docker/clipper/streams`. UGOS
  sftp/scp is chrooted, so copy files with `ssh nas "cat > path"`.
- Laptop: reaches the share through a saved Windows credential (`cmdkey`); `[paths] streams =
  "//192.168.0.155/docker/clipper/streams"` in the gitignored `clipper.toml`.
- Kitsu: `http://192.168.0.155:8090`, also on the NAS, so the container reaches it locally.
- Keys are Windows user env vars on the laptop. On the NAS they go only in the gitignored
  env file.
