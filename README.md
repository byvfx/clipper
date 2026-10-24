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
  vertical captioned Shorts. With `[shorts] window = "short"` each Short is the pick's scored hot-take
  moment (15-59 s here, under YouTube's 60 s Content ID block line), ending on a full sentence.
  Picks are also cut as horizontal clips; leave `"picks"` out of `[review] include` to post only their Shorts.
  Covers can use portraits from a photos folder for people talked about but not on screen.
- **review**: every item goes to Kitsu and Discord. Approve, reject, hold, or leave a note asking for a change.
  Notes become retakes: the item is re-cut or re-thumbnailed and comes back for review.
- **schedule / upload**: approved items get release slots and go to YouTube as private videos with a publish time
  (or stay private for you to publish by hand: `[youtube] manual_publish`). Past the channel's daily
  upload limit, upload the file in Studio and `clipper upload --attach <item> <link>` fills in the rest.
  Descriptions link to the longer video each one comes from (a clip to its segment at the clip's
  offset, a segment to its compilation) once that video is public; every upload run adds the link
  to videos already up when their longer video goes public (`[youtube] source_links`).
- **tiktok / rumble**: `clipper tiktok --items ...` sends Shorts to the TikTok app's drafts (you post from the
  phone; the caption comes to Discord); `--auto`, which the bot runs every `[tiktok] auto_send_minutes`,
  sends the next ones in YouTube-schedule order up to `max_per_24h`. `clipper rumble` prepares folders
  for Rumble's upload form.

Everything is a `clipper <stage>` subcommand. `clipper run` does every stage a stream still
needs, and `clipper worker` does that for each recording as it finishes. Clips and Shorts come first
(`[worker] fast_lane`): once a stream is transcribed and scored, its picks and their Shorts are
cut, written up and sent to review (roughly an hour after an 8-hour show ends), and only then
does the worker cut the long segments and compilations, which take hours. A newer stream's
clips go ahead of an older stream's segments.

**Pre-shows.** A show can also post a pre-recorded pre-show on its own YouTube channel (Kino
Casino does, on show days around midday PT). With `[shows.<key>.preshow] youtube_channel`
set, the bot checks that channel's feed every few minutes, grabs each new pre-show into
`streams/yt-<video id>/` and runs it as **one whole video** (less `[preshow] trim_end_s`, 3 s,
off the end): transcribe, one segment, cut, meta, thumbs, review. No clips and no Shorts. Once approved, `clipper schedule` places it
"asap", outside the release slots, and the next `clipper upload` publishes it at once.
`clipper preshow list` shows the feed; `clipper preshow fetch <url>` grabs an older one.

## Roles

| Role | Runs | Where |
|---|---|---|
| Recorder | `clipper record watch` | Docker on a NAS, see `deploy/record` |
| GPU worker | `clipper worker` | a machine with an NVIDIA GPU (8 GB+) or Apple Silicon, or any machine with `[transcribe] backend = "modal"` (cloud GPU) |
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
One stream can go further with its own `streams/<id>/config.toml` (same sections), for a big
night: more picks, the breaking story in `[scoring] stream_context`, and `[schedule]
priority = true` so its items go out ahead of the backlog. Unknown keys there are refused.
`clipper.example.toml` documents every key. `clipper.toml` itself is gitignored because it
holds your Discord ids and Kitsu address.

Secrets never go in the config: API keys and tokens are environment variables, and the
YouTube OAuth files live in `secrets/` (gitignored).

## Archives

`clipper archive` builds searchable transcripts of other channels' back catalogs (for
receipts and supercuts), kept apart from the streams folder so nothing in them is uploaded:

```bash
clipper archive add jer https://www.youtube.com/@TheQuartering   # list long-form videos
clipper archive transcribe jer --limit 100 --dry-run              # hours and estimated cost
clipper archive transcribe jer --limit 100                        # WhisperX on Modal, 8 at once
clipper archive status
```

It needs the Modal backend set up (SETUP.md, "Transcribe on Modal"). YouTube audio is
downloaded on this machine and uploaded; Odysee is meant to be fetched in the cloud (not
working yet, issue #44).

## Character pieces (Mother Moo)

The Udder Truth's host is performed to playback: a script read in her voice, the performer
lip-syncing to it on camera, a video model turning the take into her.

```bash
clipper narrate speak script.txt                       # the read, in [narrate] voice_id
# film yourself lip-syncing while the read plays out loud (one or more takes)
clipper perform prepare take.mov --read script.read.mp3 --take 1   # cut at the read's pauses
# render each pieceN.mp4 with the character model -> renderN.mp4 (Runway, WAN 3 Prime)
clipper perform stitch take.perform                     # final.mp4 + final_phone.mp4
```

`clipper narrate voice <take>` re-voices a take in her voice when the performer's own delivery
is wanted instead. Setup: SETUP.md, "Narration voice".

## Review

Review happens in [Kitsu](https://www.cg-wire.com/kitsu), with one production per show and
one episode per stream. `deploy/kitsu` runs it in Docker. The Discord bot mirrors each item
as a card with buttons, and the war room (`clipper warroom`, a local web page) shows what is
live, where each stream is in the pipeline, the review queue and the upload schedule.
Click a live show's chip to watch it in a player on the page or on kick.com in a new
window; the chip also says whether the NAS is recording it. The review queue works from the
keyboard (J/K to move, A approve, R reject, C retake note, H hold, Space/arrows for the
video, O to pop it out into its own window); press `?` in the war room for the full list.
Titles, descriptions, hashtags and cover words are checked as you type ("hosts", unmasked
swearing, YouTube's length limits with the show prefix), and cards with a problem carry a
⚠ badge. Above the schedule, the upload panel shows uploads in the last 24 h against the cap,
the last automatic run and its errors, and what goes next; uploaded slots show views and a
YouTube link. On a phone it switches to one view at a
time (Review, Schedule, Pipeline tabs at the bottom), items open full screen with the
decisions at the bottom and swipe to the next one, and videos play from a 360p copy
(`lowres/` beside each preview, about a quarter of the data). It can be added to the home
screen. From the phone away from home it goes through Tailscale: `clipper warroom --tailnet
on` (SETUP.md, Phone access).

The bot also DMs you when something needs you (`[notify]`): a stream ready for review, an
upload failing, a stage failing after its retries, recorder trouble, the streams share
filling up, and a daily digest at 09:00. Quiet hours (00:00-08:00) hold them until morning.
`clipper alert test` sends a test DM; `clipper alert digest` prints the digest.

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
