# clipper

Turns long Kick streams into YouTube uploads: full-length segments, topic compilations,
Shorts, titles, descriptions, chapters and thumbnails, with a human review step before
anything goes out. New here: [Start here](#start-here).

One stream in, a reviewed set of videos out:

```
fetch / record -> signals -> transcribe -> score -> segments -> cut -> meta -> thumbs -> shorts
                                                                                    |
                        upload <- schedule <- review (Kitsu, Discord, war room) <- review publish
```

- **fetch / record**: a finished Kick VOD, or the live stream as it happens (NAS container), with chat.
- **signals**: chat activity and loudness into a heat curve.
- **transcribe**: WhisperX (NVIDIA, or on Modal's cloud GPUs) or mlx-whisper (Apple Silicon) to word timestamps; with
  `[transcribe] diarize`, speaker turns too, named per show by `clipper speakers` (enrolled voices).
  Once a stream has names, the transcript the model stages read tags each turn (`[PPP] ...`,
  `[?]` for a voice nobody named, `[music]`), and the weekly credits a quote to whoever said it.
- **score**: Claude reads the transcript and heat curve and proposes moments and picks.
- **segments**: Claude splits the stream into topic segments and compilations.
- **cut**: lossless segment cuts on keyframes, re-encoded picks, bleeps; a few segments at once,
  on the NAS beside the source with `[cut] backend = "nas"`.
- **meta / thumbs / shorts**: upload metadata and SRT, tabloid-style thumbnails (a 9:16 one for each Short),
  vertical captioned Shorts. With `[shorts] window = "short"` each Short is the pick's scored hot-take
  moment (15-59 s here, under YouTube's 60 s Content ID block line), ending on a full sentence.
  Picks are also cut as horizontal clips; leave `"picks"` out of `[review] include` to post only their Shorts.
  Covers can use portraits from a photos folder for people talked about but not on screen.
- **faces**: who is on screen, by face, over every keyframe (CPU, `clipper faces`): groups named
  against known faces kept for every show; enquirer covers get each named person's best frames and use the found face box (#54).
  The war room names groups from their sheets and picks a face by hand from a paused video.
- **review**: every item goes to Kitsu and Discord. Approve, reject, hold, or leave a note asking for a change.
  Notes become retakes: the item is re-cut or re-thumbnailed and comes back for review.
- **schedule / upload**: approved items get release slots and go to YouTube as private videos with a publish time
  (or stay private for you to publish by hand: `[youtube] manual_publish`). To stay current rather
  than build a backlog, `[schedule] stream_budget` caps each stream's releases per kind (scaled by its
  length; the best Shorts by score, segments and compilations by chat heat) and `stale_days` lets go of
  whatever isn't out that many days after it could first go. Items let go stay approved in Kitsu
  (war room filter "Not going out"); pin one with "Publish at" to send it anyway. Past the channel's daily
  upload limit, upload the file in Studio and `clipper upload --attach <item> <link>` fills in the rest.
  Descriptions link to the longer video each one comes from (a clip to its segment at the clip's
  offset, a segment to its compilation) once that video is public; every upload run adds the link
  to videos already up when their longer video goes public (`[youtube] source_links`).
  Every YouTube API call is counted per Pacific day in `logs/youtube_quota.json`; the link updates
  and retries of captions that failed to go up (`clipper upload --captions`) only spend what is left
  after room for the day's remaining uploads (`[youtube] daily_quota`, `upload_units`).
  A Short's "related video" (the link under it to its segment) can only be set in Studio:
  `clipper upload --related` lists the uploaded Shorts whose segment is out, with the Studio link
  and the segment id to search for, and `clipper upload --related <keys or links>` records the
  ones you set. The daily digest lists them too.
- **pick**: Shorts from a stretch you name, when you watched the show and know where the good bits are:
  `clipper pick <stream> --from 4:05:00 --to 4:25:00 --about "the Quartering bit"` (or `--at 4:50:00`, or
  only `--about`, which searches the stream's summary first; `--find <word>` lists transcript lines for free).
  Times are the Kick VOD's clock. The moments found join the stream's picks (ids `h01`...) and go through
  cut, meta, thumbs, Shorts and review like scored ones; `--dry-run` shows them without saving.
- **tiktok / rumble**: `clipper tiktok --items ...` sends Shorts to the TikTok app's drafts (you post from the
  phone; the caption comes to Discord); `--auto`, which the bot runs every `[tiktok] auto_send_minutes`,
  sends the next ones in YouTube-schedule order up to `max_per_24h` (TikTok is parked: we post by hand,
  auto-send off, no direct posting; SETUP.md says why). `clipper rumble` prepares folders
  for Rumble's upload form.
- **x**: `clipper x --items ...` posts Shorts to X as native video (hook-first title + one hashtag, no
  link: X bills a link post at $0.20 against $0.015); `--auto`, which the bot runs every
  `[x] auto_post_minutes` (off by default), posts approved Shorts from streams of the last
  `max_age_hours`, newest stream first, without waiting for YouTube. `clipper x --issue [DAY]`
  posts a weekly issue's ten pages as a thread of 4 + 4 + 2 images (text from issue.json `post`,
  or `post.x` for a hand edit; `x.json` in the issue folder records it).

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
The same path takes a **one-off** from any YouTube video: `clipper --show <key> preshow fetch
<url> --from 1:30:00 [--to 2:10:00] --about "what the stretch is"` grabs it and the segments
stage asks the model for the exact start and end inside that window (prompts/oneoff_<v>.md), so one
long segment comes out, not the whole video. A show that is not on Kick gets a placeholder
channel in its `[shows.<key>]` table. `[thumbnail] teasers = false` (per show) drops the
cover's three side boxes so the photo fills the left: one big moment, no other stories to plug.
`[preshow] teasers = false` does the same for every pre-show and one-off cover only.
Every position, size, color and fixed word of the tabloid covers (16:9 and the Shorts' 9:16)
is data, `LAYOUT` in `clipper/thumbs/enquirer.py`; a show changes any of it in
`layouts/<show>/enquirer.toml` (`[paths] layouts`, beside its Shorts screenshots), e.g.
`[colors] green = "#0b3d91"` or `[cover.inset] size = [200, 180]`, and
`clipper thumbs <stream> --compose-only` redraws with no model calls. An unknown key is an error.

**Weekly issue.** `clipper weekly` turns the week's streams (Sun-Sat) into *Lolcow Cinema
Weekly*, a ten-page tabloid for a TikTok carousel or two YouTube Posts: a cover, contents,
the cover story and two features, news in brief, the quote of the week, the week in numbers,
a marketplace of parody ads, and next week's schedule with a readers' poll. It gathers the
week (segments, chat, transcripts, the thumbnail stage's faces) into `weekly/<saturday>/week.json`,
has the `[weekly]` model draft the copy into `issue.json` (about $0.35), writes the three articles
again one call each with a writer prompt and the owner's own piece as the voice to match (about
$0.10 each), and renders 1080x1440 pages plus a square 1080x1080 set in `youtube/` for YouTube
Posts, which show images square (`[weekly] formats`). `--headline "..."` tells the draft what the cover story is; edit `issue.json` (copy, or
`faces` to pin a frame for someone) and `clipper weekly --render`. Posting is by hand
(`post_text.md` has the captions).

## Start here

1. **Read**: this page for what clipper does, then [SETUP.md](SETUP.md) to install a machine
   (`deploy\bootstrap.ps1` runs `clipper setup`, then `clipper doctor`). HANDOFF.md is the
   current state between work sessions: what runs where, what's waiting, known gotchas.
2. **One stream end to end**: `clipper fetch <kick vod url>`, then `clipper run <stream id>`.
   Each stage skips what's done; `clipper run <id> --from cut` redoes from a stage on. Review the
   items in the war room ([docs/warroom.md](docs/warroom.md)) or on the Discord cards; the bot
   schedules approved ones and uploads every `[youtube] auto_upload_minutes`
   (`clipper schedule`, `clipper upload` by hand when that's 0).
3. **Check**: `clipper doctor --role gpu|bot|nas` (this machine), `clipper worker --status`
   (each recording's stages), `clipper review status` (decisions and what's scheduled),
   `clipper costs` (the month's spend).
4. **Stop and restart**: the bot and the GPU worker are Windows scheduled tasks:
   `deploy\discord\bot-task.ps1 -Status | -Stop | -Restart` and
   `deploy\worker\worker-task.ps1 -Status | -Stop | -Restart`. The recorder is a container on
   the NAS: `bash deploy/record/deploy.sh`, only when no show is live (a restart starts a new
   recording part).
5. **When something breaks**: the bot DMs you (`[notify]`); logs are in `logs/` (`bot.log`,
   `worker.log`); `clipper doctor` prints a fix for each problem it finds.

## Roles

| Role | Runs | Where |
|---|---|---|
| Recorder | `clipper record watch` | Docker on a NAS, see `deploy/record` |
| GPU worker | `clipper worker` | a machine with an NVIDIA GPU (8 GB+) or Apple Silicon, or any machine with `[transcribe] backend = "modal"` (cloud GPU) |
| Bot | `clipper bot` | any always-on machine: Discord review cards, retakes, the war room, pre-show grabs |

All roles share one streams folder (a NAS share). One machine can do more than one role.
`clipper services` shows them all (and any cut running on the NAS);
`clipper services restart bot|worker|all` restarts the bot and worker, `... recorder` the
recorder, refused while it's recording a show.

## Quick start

From the clipper folder on a new machine (keep the folder path short on Windows, e.g. `C:\clipper`):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\bootstrap.ps1   # macOS/Linux: bash deploy/bootstrap.sh
```

It installs uv, Python 3.12 and ffmpeg if they're missing, then runs `clipper setup`, a
walkthrough that asks whether this machine joins an existing install or starts a new one,
what it runs, where it transcribes, its streams folder and encoder, and the keys (checked as
you enter them), then runs `clipper doctor`. Run it again to carry on; `clipper setup --step
keys` redoes one step and `clipper setup --check` shows where each step stands. Then:

```bash
clipper fetch <kick vod url>
clipper run <stream id>
```

[SETUP.md](SETUP.md) has the full per-platform steps, including what setup doesn't do yet
(Discord ids, YouTube sign-in, adding a show), the NAS recorder and running the worker and bot
unattended.

`clipper -h` lists the commands by group; `clipper <command> -h` gives each one's flags. The
same flag means the same thing everywhere: `--items s02,c03` picks items (the old `--ids`,
`--keys`, `--item` and `--segments` still work), times are `H:MM:SS`, `H:MM` or seconds,
`--limit N` caps a run, `--dry-run` changes nothing, and `--no-sync` skips the Kitsu read.
Flags for different jobs on one command (`upload --links` and `--next`, say) are refused together.

## Configuration

Settings come in layers: built-in defaults, then `clipper.toml`, then this machine's own
`clipper.local.toml` (written by `clipper setup`: streams path, encoder, transcribe backend),
then environment variables named `CLIPPER_<SECTION>_<KEY>` (for example
`CLIPPER_CUT_CLIP_CODEC=libx264`). That lets one shared `clipper.toml` serve every machine.
`clipper.local.toml` and env vars win over show and stream overrides too.

Each Kick channel is a show: a `[shows.<key>]` table with its channels, show name, scoring
context and Shorts layouts. A show's sub-tables override the base sections for that show.
One stream can go further with its own `streams/<id>/config.toml` (same sections), for a big
night: more picks, the breaking story in `[scoring] stream_context`, and `[schedule]
priority = true` so its items go out ahead of the backlog. Unknown keys there are refused.
`clipper.example.toml` documents every key; `clipper.toml` holds only what differs (`clipper
setup` starts a new one that way). `clipper.toml` itself is gitignored because it holds your
Discord ids and Kitsu address.

`clipper config get meta.model` prints a setting's value and every layer that sets it
(add `--stream <id>` for one stream, or `--show <key>`). Settings with a fixed set of choices
(`[bleep] mode`, the backends, `[thumbnail] style`, ...) refuse anything else when the config
loads; an unknown setting or `CLIPPER_*` variable is a warning on every command, and
`clipper doctor` fails on it. `clipper --show <key>` applies that show to any stream.

Secrets never go in the config: API keys and tokens are environment variables, and the
YouTube OAuth files live in `secrets/` (gitignored).

## Archives

`clipper archive` builds searchable transcripts of other channels' back catalogs (for
receipts and supercuts), kept apart from the streams folder so nothing in them is uploaded:

```bash
clipper archive add jer https://www.youtube.com/@TheQuartering   # list long-form videos
clipper archive transcribe jer --limit 100 --dry-run              # hours and estimated cost
clipper archive transcribe jer --limit 100                        # WhisperX on Modal, 8 at once; log in archive/jer/transcribe.log
clipper archive transcribe jer --diarize                          # speaker turns too (~+80% GPU), named against voices/jer.json
clipper archive status
clipper modal                                                     # GPUs up, runs, spend, archive ETA
clipper costs [--month 2026-10]                                   # month's spend: models + Modal + fixed, by stage/show/stream
clipper analytics --report [--month 2026-10] [--refresh]          # YouTube month: views, sources, 7-day numbers by show/kind/day/hour
clipper archive check jer                                         # which source links still play
clipper archive links jer                                         # the dead and restricted ones
clipper archive index jer                                         # keyword index, index.sqlite (free)
clipper archive search jer '"costco dog"' --by oldest             # hits: date, title, link at the moment
clipper archive search jer 'huck' --years                         # how often, per year
clipper archive ask jer "Jer saying he'd never take sponsor money"  # Haiku writes the searches, picks the fits (~1c)
```

`search` takes FTS5 syntax (`a b` both words, `"a b"` the phrase, `OR`, `NOT`, `pre*`) with
stemming, over ~30 s windows; each hit links to the moment, a few seconds early. It matches
words, not meaning: "the warehouse store" won't find "Costco" (embeddings are #53). `ask`
gets most of the way there cheaply: Haiku turns the question into up to 8 searches (synonyms,
names, how Whisper misspells them), then reads the ~80 pooled windows and returns the ones
that fit, strong or maybe, each with a quote, a reason and the link.

`check` downloads nothing and costs nothing: YouTube's oEmbed endpoint and Odysee's resolve say
whether each item is alive, restricted (private, embedding off, blocked) or dead, kept in
`links.json` with when it changed, and reruns report what died since. A dead link with a
transcript is a receipt whose source is gone; `transcribe` skips dead links.
`clipper archive check jer --items yt-<id> ...` checks just the items you are about to link to.

It needs the Modal backend set up (SETUP.md, "Transcribe on Modal"). YouTube audio is
downloaded on this machine and uploaded; Odysee is meant to be fetched in the cloud (not
working yet, issue #44).

### Receipts for a stream (#48)

`clipper receipts` turns a stream's stories about the archive subject (`[receipts] subject`,
`jer`) into one video of his own old clips, to send to the show:

```bash
clipper receipts find kick-<id>     # Sonnet names the stories + a search each; `archive ask` finds the clips (~$0.20)
clipper receipts list kick-<id>     # stories, receipts ([x] = used), quotes, links
clipper receipts script kick-<id>   # Opus writes Mother Moo's voice-over for the used ones (~$0.02)
clipper receipts cut kick-<id>      # receipts/receipts.mp4 + description.txt; voices changed lines (ElevenLabs)
clipper receipts upload kick-<id>   # private, outside the schedule; the link goes in receipts.json
```

The edit is `streams/<id>/receipts/receipts.json`: flip `use`, reorder, trim `start`/`end`
(seconds in the source video), rewrite a story card (`story`, `now`) or the `title` /
`description`, then `cut` again. Each clip carries the channel and date on screen and its
source link at that moment in the description, with chapters per story. Clips are bleeped
from the archive's word timings like our own cuts. With a script, the open, each story card
and a "THEN / date" card before each clip carry the read (`[narrate] voice_id`, Mother Moo);
edit any line in receipts.json and `cut` re-voices only that one. Every part is normalized to
`[receipts] lufs`. It needs `ELEVENLABS_API_KEY` (SETUP.md, Narration) only when there is a
script. Nothing is scheduled or made public: the
upload stays private until someone changes it in Studio, and sending it on is up to you.

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
as a card with buttons, and the war room (a web page the bot serves) shows what is live,
where each stream is in the pipeline, the review queue and the upload schedule, with bulk
edits, undo, a cover editor and a drag-and-drop schedule. Its manual is [docs/warroom.md](docs/warroom.md).

The bot also DMs you when something needs you (`[notify]`): a stream ready for review, an
upload failing, a stage failing after its retries, recorder trouble, the streams share
filling up, and a daily digest at 09:00. Quiet hours (00:00-08:00) hold them until morning.
`clipper alert test` sends a test DM; `clipper alert digest` prints the digest.

## Layout

```
clipper/              the package, one module per stage, cli.py wires them
prompts/              versioned prompt files; the version in use is set in clipper.toml
deploy/               bootstrap (new machine), NAS recorder (Docker), Kitsu (Docker), Windows worker and bot tasks
streams/<id>/         one folder per stream: source, transcript, cuts, metadata, review state
clipper.example.toml  every setting, documented (a test checks it against config.py)
tests/                offline tests (pytest); CI runs them and ruff on every push
SETUP.md              installing on a new machine
HANDOFF.md            current state between work sessions (short); docs/log/ has the dated diary
docs/warroom.md       the war room's manual
```

## Requirements

Python 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg with libass (the "full" builds), an
Anthropic API key, a Kitsu instance, and a Discord bot if you want cards in Discord. Any stage
can run on another model provider instead (OpenAI, Gemini, or a local OpenAI-compatible server
such as Ollama) by prefixing its model key: SETUP "Other model providers".
YouTube downloads (pre-shows) need a JavaScript runtime; `uv sync` installs deno for yt-dlp.
