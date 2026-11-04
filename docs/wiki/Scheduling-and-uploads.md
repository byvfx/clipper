Approved items get release slots (`clipper schedule`, written to `schedule.json`) and go to
YouTube as private videos with a publish time (`clipper upload`), or stay private for you to
publish by hand with `[youtube] manual_publish`. Everything uploads to one YouTube channel;
bringing a new one up is [YouTube channel](YouTube-channel).

## Automatic uploads

`clipper serve` replans and uploads every `[youtube] auto_upload_minutes` (the default 0 means
only by hand), at most `[youtube] max_per_24h` in any 24 hours, so hourly runs stay under the
channel's daily upload limit. Pre-shows and stories set to "asap" go out on the next run,
outside the release slots.

## Staying current

To stay current rather than build a backlog, `[schedule] stream_budget` caps each stream's
releases per kind (scaled by its length; the best Shorts by score, segments and compilations
by chat heat) and `stale_days` lets go of whatever isn't out that many days after it could
first go. Items let go stay approved in Kitsu (war room filter "Not going out"); pin one with
"Publish at" to send it anyway. A `stream_budget` kind at 0 goes only when pinned. A big night
can jump the queue with `[schedule] priority = true` in its stream config
([Configuration](Configuration)).

In the [war room](War-room) the schedule shows the open slots of the next two weeks; drag an
item onto a slot to pin it.

## Past the daily limit

Upload the file in Studio and `clipper upload --attach <item> <link>` fills in the rest (title,
description, tags, thumbnail, captions, playlist, Kitsu link). `clipper upload --next 5` lists
what goes out next with each file.

## Source links

Descriptions link to the longer video each one comes from (a clip to its segment at the clip's
offset, a segment to its compilation) once that video is public; every upload run adds the
link to videos already up when their longer video goes public (`[youtube] source_links`).

A Short's "related video" (the link under it to its segment) can only be set in Studio:
`clipper upload --related` lists the uploaded Shorts whose segment is out, with the Studio link
and the segment id to search for, and `clipper upload --related <keys or links>` records the
ones you set. The daily digest lists them too.

## Quota

Every YouTube API call is counted per Pacific day in `logs/youtube_quota.json`; the link
updates and retries of captions that failed to go up (`clipper upload --captions`) only spend
what is left after room for the day's remaining uploads (`[youtube] daily_quota`,
`upload_units`). Details in [YouTube channel](YouTube-channel), "API quota".
