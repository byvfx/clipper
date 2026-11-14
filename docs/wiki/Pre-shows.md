A show can also post a pre-recorded pre-show on its own YouTube channel (Kino Casino does, on
show days around midday PT). With `[shows.<key>.preshow] youtube_channel` set
([Adding a show](Adding-a-show#pre-show-channel)), `clipper serve` checks that channel's feed
every few minutes, grabs each new pre-show into `streams/yt-<video id>/` and runs it as **one
whole video** (less `[preshow] trim_end_s`, 3 s, off the end): transcribe, one segment, cut,
meta, thumbs, review. No clips and no Shorts. Once approved, `clipper schedule` places it
"asap", outside the release slots, and the next `clipper upload` publishes it at once.
`clipper preshow list` shows the feed; `clipper preshow fetch <url>` grabs an older one.
When the channel's RSS feed fails (it can 404 for hours), the check lists the channel's videos
page instead, which has no dates: a pre-show listed below one already grabbed counts as older
and is left alone, so after a channel switch grab the latest by hand.

When YouTube's bot check stops a grab, `clipper preshow` retries it signed in with the YouTube
cookies file, or with a browser profile's cookies when `[youtube] cookies_from_browser` is set
([Archives](Archives#signed-in-through-a-browser-profile)).

## One-offs

The same path takes a **one-off** from any YouTube video: `clipper --show <key> preshow fetch
<url> --from 1:30:00 [--to 2:10:00] --about "what the stretch is"` grabs it and the segments
stage asks the model for the exact start and end inside that window (`clipper/prompts/oneoff_<v>.md`),
so one long segment comes out, not the whole video. A show that is not on Kick gets a
placeholder channel in its `[shows.<key>]` table.

`[preshow] teasers = false` drops the cover's three side boxes for every pre-show and one-off
cover ([Covers](Covers)).
