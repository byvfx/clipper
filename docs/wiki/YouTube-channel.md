What we learned bringing up a new channel (October 2026): it is held back in
ways the API reports badly, so do these before the first big upload run.

1. **Sign in** ([YouTube sign-in](YouTube-sign-in)): `clipper upload --sign-in` once, on the machine that uploads. The token
   lands in `secrets/youtube_token.json`. First set the Google Cloud project's OAuth app to
   **In production** (Google Auth Platform > Audience > Publish app; no verification
   needed, you click past the "unverified app" warning): while it is in **Testing**, Google
   expires the sign-in after 7 days and uploads stop with `invalid_grant`. Signing in again
   keeps the analytics scope, and `clipper doctor` (online) tests the sign-in by refreshing it.
2. **Verify the channel** at https://www.youtube.com/verify (phone). Without it custom
   thumbnails are refused (403); videos still go up, and `clipper upload --thumbnails` sets
   the covers afterwards. Verification can take more than one round: if Studio later
   refuses an upload, it may offer another verification link. Follow it. On our channel
   that second verification lifted the cap for Studio *and* the API: uploads went through
   again right away.
3. **Expect a small daily upload cap.** `uploadLimitExceeded` ("The user has exceeded the
   number of videos they may upload") is YouTube's limit on the *channel*, not the API
   quota (that one is `quotaExceeded`). Private uploads count too. The window looks
   rolling (about 24 h from each upload), and the API hit it at 8 uploads in 24 h on the
   new channel. It grows with the channel's history and verification; YouTube doesn't
   publish the number. Keep `[schedule] max_per_day`, `[youtube] max_per_run` and `max_per_24h` under it
   (`max_per_24h` counts every upload in the last 24 h, so hourly automatic runs stay under too).
   A refused upload leaves nothing behind on YouTube or in schedule.json, so it is safe to
   rerun later.
4. **Past the cap, upload in Studio** (it may still accept uploads when the API is
   refused): `clipper upload --next 5` lists what goes out next with each file; drag the
   file into Studio, set it Private, skip the details, then
   `clipper upload --attach <item> <link>` sets the title, description and tags, adds
   the thumbnail, captions, playlist and Kitsu link, and records the video as uploaded so
   clipper never sends it twice.
5. **Publishing through the API** (`videos.update` to public) worked on our Cloud project
   before any API audit, so `[youtube] manual_publish` can be turned off once you trust
   the uploads. Before that, check the first private uploads for Content ID claims in
   Studio. The Data API can't see claims; it only shows region blocks.
6. **Playlists** are created on first use and their ids cached in
   `secrets/youtube_playlists.json`. If you delete one in Studio, the next add finds it gone,
   looks the name up again and makes a new one if needed. A playlist renamed in Studio keeps
   its id, so videos keep going into it. Playlist names come from each item's Kitsu
   "Playlist" column.
7. **Source links** (`[youtube] source_links`, on by default) add a line such as "Full
   segment: <link>" to a clip's or segment's description once its longer video is public.
   They are not in Kitsu. Each upload run edits at most `link_updates_per_run` (20)
   descriptions of videos already up (about 51 quota units each).
   `clipper upload --links --dry-run` shows what it would change. Change the wording per show
   with `clip_source` / `segment_source` in `[shows.<key>.youtube]`.
8. **API quota.** A Cloud project gets 10,000 Data API units a day, reset at midnight
   Pacific; uploads themselves have their own bucket (100 a day), but each upload's
   thumbnail (50), captions (400) and playlist adds (50 each) come out of the 10,000.
   Every call is counted in `logs/youtube_quota.json` and each upload run ends with the
   day's total. Source-link updates and caption retries (a captions upload that failed is
   tried again hourly, 3 times; `clipper upload --captions` alone) only spend what's left
   after `upload_units` (600) for each upload `max_per_24h` still allows that day. If Google
   raises your project's quota, set `[youtube] daily_quota` to match.
