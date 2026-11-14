Kitsu (CGWire's review tool) is the record of every decision: one production per show, an
episode per stream, a sequence per topic segment, a shot per clip and per Short. clipper
creates and fills all of it ([Review](Review)); you provide a running Kitsu, a bot account for
clipper and a login per reviewer.

## Running Kitsu

Kitsu is a Docker compose stack (Kitsu, the Zou API, Postgres, Redis, Meilisearch), the
[mathbou/docker-cgwire](https://github.com/mathbou/docker-cgwire) one, pinned in
`deploy/kitsu`. Two scripts, the same stack:

- **On this machine** (Docker Desktop or Docker Engine, no ssh): `bash deploy/kitsu/local.sh`
  puts the stack's files in `~/kitsu` (`KITSU_DIR`) and starts it on port 8090 (`PORT` in
  `deploy/kitsu/env.local`); `KITSU_SUDO=sudo` if your user can't talk to Docker.
- **On a NAS or another host over ssh**: `bash deploy/kitsu/deploy.sh`, with `KITSU_SSH_HOST`
  (default `nas`), `KITSU_DIR` and `KITSU_SUDO`.

The first run writes `deploy/kitsu/env.local` with random secrets (gitignored; keep it, it is
the only copy of the database password). Rerunning either script updates the stack and runs
Zou's database upgrade; the data lives in named Docker volumes and survives.

Open `http://<host>:8090`. Sign in as the stack's default admin (`admin@example.com` /
`mysecretpassword`, from the docker-cgwire README), change that password straight away (your
avatar → Profile) or make your own admin account and remove the default one.

## clipper's account

clipper signs in as a bot. In Kitsu: Settings (the cog) → People → **Bots** → New bot, named
`clipper`, role Admin (it creates productions, task types and statuses); copy the token it
shows. Save it as the `KITSU_TOKEN` environment variable (`[review] token_env`) on every
machine that publishes or syncs; `clipper setup` asks for the address and the token and
checks them. In clipper.toml:

```toml
[review]
host = "http://<host>:8090/api"   # the API URL, with /api
```

`clipper doctor` (online) signs in and reports the bot's name. The first `clipper review
publish` creates the "Clipping" project template and the show's production (named after
`[meta] show_name`, or `[review] project`) with the task types, statuses and columns clipper
uses; later publishes keep them up to date.

## Reviewers

Each reviewer is a Kitsu person (People → New person) with their own login, added to the show's
production (the production's Team). A Manager can set statuses and edit the columns; an Artist
can comment. What they see and do is on [Reviewing](Reviewing).

## What clipper writes and reads

Statuses: **approved** (or **done**) approves, **retake** with a comment asks for a change (the
item is re-cut or re-covered and comes back), **rejected** drops it; anything else stays
pending. Columns on a segment (sequence) or clip (shot): YouTube title, YouTube description,
Hashtags, Tags and Playlist (what `clipper upload` sends), Hook, Category, Score and Found by
(how the clip was chosen), Layout (a Short's crop), Other voices (seconds of voices that aren't
the show's own), Tightened (the pauses cut out of a Short, [Pipeline](Pipeline#tightening)),
Publish at (your pin), Hold, Scheduled (the slot clipper gave it) and
Thumbnail text (the cover's words; a sync re-renders the cover). clipper fills a column when it
is empty or still shows what clipper wrote last, never over an edit of yours, and `clipper
review sync` reads them back.
