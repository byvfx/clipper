Nothing goes out before a person approves it. Review happens in
[Kitsu](https://www.cg-wire.com/kitsu), the record for every decision, with one production per
show and one episode per stream. `deploy/kitsu` runs it in Docker. The Discord bot mirrors each
item as a card with buttons, and the [war room](War-room) (a web page `clipper serve` serves)
shows what is live, where each stream is in the pipeline, the review queue and the upload
schedule, with bulk edits, undo, a cover editor and a drag-and-drop schedule.

## Decisions

Approve, reject, hold, or leave a note asking for a change. A note becomes a retake: the item
is re-cut or re-thumbnailed and comes back for review. Approved items are scheduled and
uploaded ([Scheduling and uploads](Scheduling-and-uploads)). `clipper review status` shows the
decisions and what's scheduled.

## Shorts held for other voices

A Short whose voices other than the show's own people (`[meta] cast`) run over `[scoring]
max_source_audio_s` (5 s) arrives held: the speaker tags count the seconds of `[?]` voices
(usually a video being played) and other names, and the Kitsu "Other voices" column and the
Discord card show them ("22 s of other voices (limit 5 s)"). Pass B is meant to drop those, but
some got through. Watch it: if the show's commentary carries the moment, release the hold and
it schedules as usual; otherwise reject it or ask for a trim. A hold you release isn't put back,
and a re-cut under the limit comes off hold by itself. It needs speaker names
([Speakers](Speakers)); a show without them is not checked. `[shorts] hold_source_audio = false`
turns it off.

## Alerts

The bot also DMs you when something needs you (`[notify]`): a stream ready for review, an
upload failing, a stage failing after its retries, recorder trouble, the streams share
filling up, and a daily digest at 09:00. Quiet hours (00:00-08:00) hold them until morning.
`clipper alert test` sends a test DM; `clipper alert digest` prints the digest.
