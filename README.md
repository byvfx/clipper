# clipper

Turns long Kick streams into YouTube uploads: full-length segments, topic compilations,
Shorts, titles, descriptions, chapters and thumbnails, with a human review step before
anything goes out.

One stream in, a reviewed set of videos out:

```
fetch / record -> transcribe -> signals -> score -> segments -> cut -> meta -> thumbs -> shorts
                                                                                    |
                        upload <- schedule <- review (Kitsu, Discord, war room) <- review publish
```

- **fetch / record**: a finished Kick VOD, or the live stream as it happens (NAS container), with chat.
- **transcribe**: WhisperX (NVIDIA) or mlx-whisper (Apple Silicon) to word timestamps.
- **signals**: chat activity and loudness into a heat curve.
- **score**: Claude reads the transcript and heat curve and proposes moments and picks.
- **segments**: Claude splits the stream into topic segments and compilations.
- **cut**: lossless segment cuts on keyframes, re-encoded picks, bleeps.
- **meta / thumbs / shorts**: upload metadata and SRT, tabloid-style thumbnails (a 9:16 one for each Short),
  vertical captioned Shorts.
  Covers can use portraits from a photos folder for people talked about but not on screen.
- **review**: every item goes to Kitsu and Discord. Approve, reject, hold, or leave a note asking for a change.
  Notes become retakes: the item is re-cut or re-thumbnailed and comes back for review.
- **schedule / upload**: approved items get release slots and go to YouTube as private videos with a publish time
  (or stay private for you to publish by hand: `[youtube] manual_publish`). Past the channel's daily
  upload limit, upload the file in Studio and `clipper upload --attach <item> <link>` fills in the rest.

Everything is a `clipper <stage>` subcommand. `clipper run` does every stage a stream still
needs, and `clipper worker` does that for each recording as it finishes.

**Pre-shows.** A show can also post a pre-recorded pre-show on its own YouTube channel (Kino
Casino does, on show days around midday PT). With `[shows.<key>.preshow] youtube_channel`
set, the bot checks that channel's feed every few minutes, grabs each new pre-show into
`streams/yt-<video id>/` and runs it as **one whole video**: transcribe, one segment, cut,
meta, thumbs, review. No clips and no Shorts. Once approved, `clipper schedule` places it
"asap", outside the release slots, and the next `clipper upload` publishes it at once.
`clipper preshow list` shows the feed; `clipper preshow fetch <url>` grabs an older one.

## Roles

| Role | Runs | Where |
|---|---|---|
| Recorder | `clipper record watch` | Docker on a NAS, see `deploy/record` |
| GPU worker | `clipper worker` | a machine with an NVIDIA GPU (8 GB+) or Apple Silicon |
| Bot | `clipper bot` | any always-on machine: Discord review cards, retakes, the war room, pre-show grabs |

All roles share one streams folder (a NAS share). One machine can do more than one role.

## Quick start

```bash
uv sync --extra gpu --extra discord      # GPU worker + bot; --extra mac on Apple Silicon
cp clipper.example.toml clipper.toml     # then edit: streams path, Kitsu URL, Discord ids, shows
export ANTHROPIC_API_KEY=... KITSU_TOKEN=...
uv run clipper doctor --role gpu         # checks ffmpeg, codecs, fonts, GPU, keys, Kitsu, Discord, Kick
uv run clipper fetch <kick vod url>
uv run clipper run <stream id>
```

[SETUP.md](SETUP.md) has the full per-platform steps, including the NAS recorder and running the
worker and bot unattended.

## Configuration

Settings come from three layers: built-in defaults, then `clipper.toml`, then environment
variables named `CLIPPER_<SECTION>_<KEY>` (for example `CLIPPER_CUT_CLIP_CODEC=libx264`).
That lets one shared `clipper.toml` serve every machine, with per-machine differences in env vars.

Each Kick channel is a show: a `[shows.<key>]` table with its channels, show name, scoring
context and Shorts layouts. A show's sub-tables override the base sections for that show.
`clipper.example.toml` documents every key. `clipper.toml` itself is gitignored because it
holds your Discord ids and Kitsu address.

Secrets never go in the config: API keys and tokens are environment variables, and the
YouTube OAuth files live in `secrets/` (gitignored).

## Review

Review happens in [Kitsu](https://www.cg-wire.com/kitsu), with one production per show and
one episode per stream. `deploy/kitsu` runs it in Docker. The Discord bot mirrors each item
as a card with buttons, and the war room (`clipper warroom`, a local web page) shows what is
live, where each stream is in the pipeline, the review queue and the upload schedule.

## Layout

```
clipper/              the package, one module per stage, cli.py wires them
prompts/              versioned prompt files; the version in use is set in clipper.toml
deploy/               NAS recorder (Docker), Kitsu (Docker), Windows worker and bot tasks
streams/<id>/         one folder per stream: source, transcript, cuts, metadata, review state
clipper.example.toml  every setting, documented
SETUP.md              installing on a new machine
HANDOFF.md            running notes between work sessions
```

## Requirements

Python 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg with libass (the "full" builds), an
Anthropic API key, a Kitsu instance, and a Discord bot if you want cards in Discord.
YouTube downloads (pre-shows) need a JavaScript runtime; `uv sync` installs deno for yt-dlp.
