`clipper news` (#110) watches the headlines for the terms you give it (`[news] watch`,
or per show): TMZ, Dexerto, r/LivestreamFail and a Google News search for each term. With no
terms it fetches nothing; `[news] auto_watch` also watches each show's cast, known figures and
name and the cast library. A cheap
model (Haiku, well under a cent a check) rates each headline that names someone: about them and
not a namesake, new, public conduct (never their children or private people), and groups five
outlets on one event into one story. The bot posts new stories to a #news channel (it makes it if missing) with **Dismiss** and
**Tonight's context**, which adds the story to that show's next stream's `stream_context`
before scoring; an hour before a show's usual start it suggests that day's stories. The war
room's News window lists them and searches Google News by hand.

## Setup

It reads headlines about the shows' people and posts the ones that matter
to Discord. Only headlines, a short snippet and the link are stored (`[paths] news`,
`news.sqlite`); articles are never fetched.

1. **Who**: only the terms you give: `[news] watch = { "Ethan Klein" = ["H3 Podcast"] }` (other
   names in the list), or `[shows.<key>.news] watch` to tie them to a show (Tonight's context then
   goes to it). Every term gets a Google News search. With no terms nothing is fetched or
   searched at all. `[news] auto_watch = true` also watches each show's `[meta] cast`, `[scoring]
   known_figures` ("Jer (The Quartering)" adds an alias) and show name, and the cast library; there
   one-word names ("Jer", "Don") only match as written, and a name that's a common word goes in
   `[news] ignore`. `clipper news watch` lists what's watched.
2. **Where**: the bot posts to the text channel `[news] channel` ("news"), and makes it if the server
   has none (it needs Manage Channels for that; otherwise make #news by hand). `channel_id` picks a
   channel by id instead; with both empty, news goes to the review channel (in a forum, one "News" post).
3. **Try it**: `clipper news check --no-judge` fetches and matches with no model call;
   `clipper news check` also rates the matches (`[news] judge_model`, Haiku: about $0.004 for
   30 headlines) and `clipper news list` shows the stories. `clipper news feeds` shows each
   source's last fetch and error.
4. **Automatic**: set `[news] check_minutes = 30` and restart serve (it checks; the bot posts). It posts at most
   `max_posts` (5) new stories a check, the shows' own people first; "maybe" ones too while
   `post_maybe` is on. **Tonight's context** shows the text, then **Add it** queues it in
   `news/context.json` (whole stories, at most 1,500 characters: the preview is all of it); the
   worker writes it into that show's next stream's `streams/<id>/config.toml` `[scoring]
   stream_context` (a backup beside it) before scoring, however many days later; a note no stream
   took in `context_days` (7) is dropped. A `stream_context` in clipper.local.toml or
   `$CLIPPER_SCORING_STREAM_CONTEXT` would hide it, so the worker leaves the note queued and logs
   why. `[news] preshow_minutes` (60) before a show's usual start (learned from the last three
   weeks of streams; once a day per show) the bot lists that day's stories for it with a
   Context button that previews and adds them.
5. **Command line**: `clipper news search "Ethan Klein"`, `clipper news dismiss 12`,
   `clipper news context 12 --show kino`.

The bot and the worker must see the same `[paths] news` folder; on two machines, put it on the
streams share. `clipper doctor` (bot role) fails when news is on with nowhere to post and, online,
checks the feeds answer.
