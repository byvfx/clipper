A show can also post a pre-recorded pre-show on its own YouTube channel (Kino Casino does, on
show days around midday PT). With `[shows.<key>.preshow] youtube_channel` set
([Adding a show](Adding-a-show#pre-show-channel)), `clipper serve` checks that channel's feed
every few minutes, grabs each new pre-show into `streams/yt-<video id>/` and runs it as **one
whole video** (less `[preshow] trim_end_s`, 3 s, off the end): transcribe, one segment, cut,
meta, thumbs, review. No clips and no Shorts. Once approved, `clipper schedule` places it
"asap", outside the release slots, and the next `clipper upload` publishes it at once.
`clipper preshow list` shows the feed; `clipper preshow fetch <url>` grabs an older one.

When YouTube's bot check stops a grab, `clipper preshow` retries it signed in with the YouTube
cookies file ([Archives](Archives#age-restricted-videos-and-youtube-cookies)).

## One-offs

The same path takes a **one-off** from any YouTube video: `clipper --show <key> preshow fetch
<url> --from 1:30:00 [--to 2:10:00] --about "what the stretch is"` grabs it and the segments
stage asks the model for the exact start and end inside that window (`prompts/oneoff_<v>.md`),
so one long segment comes out, not the whole video. A show that is not on Kick gets a
placeholder channel in its `[shows.<key>]` table.

`[preshow] teasers = false` drops the cover's three side boxes for every pre-show and one-off
cover ([Covers](Covers)).
