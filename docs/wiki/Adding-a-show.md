Add a `[shows.<key>]` table to clipper.toml (see the end of clipper.example.toml):
`channels = ["<kick slug>"]`, `[shows.<key>.meta] show_name`, and
`[shows.<key>.scoring] stream_context` / `known_figures`. For Shorts, measure the show's
screen layouts into `[shows.<key>.shorts.layouts.<name>]` (panels of the frame, as fractions), one per
scene the show switches between: a Short that crosses a scene cut changes layout there.
Save a reference screenshot of each one from a recorded stream (`clipper shorts <stream>
--save-example <layout> --at <seconds>`); `clipper doctor` warns while any is missing.
A layout that frames an existing scene another way (say, only the webcams of a watch scene)
gets `pick = false`: scene detection never chooses it and it needs no screenshot; pick it by
hand with `clipper shorts <stream> --items <vNN> --layout <name>`, the Kitsu Layout column or a retake.
Add the slug to `[record] channels` to record it live. `clipper shows` lists every show and
its streams; the show's Kitsu production is created on its first publish.

## Pre-show channel

If the show posts a [pre-show](Pre-shows) on YouTube, add `[shows.<key>.preshow] youtube_channel =
"UC..."` (the channel id: the `channel_id` in `yt-dlp -J <any video url>`, or the
`channelId` in the channel page's source). `clipper preshow list` should then list its
recent pre-shows, and `clipper doctor` checks the feed. `clipper serve` polls it ([preshow]
poll_minutes) and runs new pre-shows on its machine, transcribe included, so serve's
machine needs the GPU extra, Modal, or `[transcribe] device = "cpu"`; set poll_minutes = 0
on a serve that shouldn't. yt-dlp needs deno for YouTube; `uv sync` installs it (yt-dlp's
`deno` extra). Without it YouTube answers downloads with HTTP 403 partway through.
