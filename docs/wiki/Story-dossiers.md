```bash
clipper story new "Jer banned from Kick after the FDA call" --days 7 --dry-run   # swarm + triage, the cost
clipper story run jer-banned-kick-fda-call     # transcribe, find the moments, write the dossier
clipper story show jer-banned-kick-fda-call    # dossier.md again (keeps hand edits)
clipper story add <slug> <video url>...        # then: clipper story run <slug> --from triage
clipper story status | trail <slug>
clipper --show kino story plan <slug>          # the edit: streams/story-<slug>/story.json (~$0.03)
clipper story cut <slug>                       # render it, meta, thumbs, the Kitsu card
```

A brief in, a research dossier out, then a video of it for review. The people
the brief names (by name, nickname, alias or id, from [`clipper people`](People); `--person`
adds one) seed the swarm with their YouTube, Kick and Odysee channels. Triage
(Haiku) picks the videos worth transcribing within `--max-videos` / `--max-hours`, the people's own
uploads first, and prints the Modal estimate; our own recorded streams are linked, not transcribed
again. Ingest transcribes on Modal (YouTube audio, Kick VOD audio, Odysee) into the archive subject
`story-<slug>` and indexes it. Find (Haiku) searches that index and any archive subject the swarm
found something in, picks the moments (event, reaction, context) and keeps only quotes that are in
the transcript, in its own wording. The dossier (Sonnet) is a summary, a timeline marked shown /
reported / speculation, people, who said what, conflicts, coverage, gaps and angles, every line
citing moments (M01) or sources (S01); a cite that doesn't resolve is sent back once, then the step
fails. In `dossier.json`, `notes` and a source's `struck: true` survive a rerun. Each step is skipped
when done; `--from` reruns one and everything after it. Files are in `paths.archive/story-<slug>/`;
nothing is uploaded until a video of it is approved.

`story plan` (Sonnet) orders the moments into an edit under a show (`--show`, `[story] show`, or
the only one): a title and description, and the moments as parts with a chapter each. Code bounds
each part around its quote (`[story] lead_s` / `tail_s` / `max_clip_s`, at sentence edges), drops a
part that repeats an earlier one's words (a re-upload, our stream playing the video), merges
overlaps and stops at `[story] max_minutes`; what it left out stays in `story.json` with
`use: false` and a note. Edit `story.json` by hand (use, order, start/end, chapter, the lower
third's channel, title), then `story cut` renders it: each part from our own source video on the
share or a yt-dlp section of the original, re-encoded with the channel and date on a lower third
and bleeped (the `[receipts]` renderer), joined into one segment of the stream `story-<slug>`
(meta.json kind "story", on the show's Kick channel). Meta takes its chapters from the parts and
lists every source with its link in the description (`[story] credit`); then thumbs and the
Kitsu card, in an episode of its own. A segment retake can't trim a story: edit `story.json` and
cut again (unchanged parts aren't re-rendered). Approved, it goes out as soon as possible
(`[story] publish = "asap"`), with the hourly uploads.

## Setup

Story dossiers need the swarm's keys plus Modal (as for `clipper
archive`): the videos it picks are transcribed there, on `[story] gpu` (L4). Kick VODs are
downloaded here at their smallest variant (`[archive] kick_format`) for their audio; YouTube audio
uses the archive's cookies retry. People and their channels come from [`clipper people`](People) (`people.json`, with
`research_notes/streamers.json`, `[story] people` and the cast library folded in). `story cut` renders
with `[receipts]`'s fonts (Impact and Arial Bold, as receipts need) and reads our own streams'
source videos from `paths.streams`, so it runs on a machine that sees the share; with several
shows, set `[story] show` or pass `clipper --show <key>` to `story plan`.
