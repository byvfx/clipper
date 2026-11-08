You scan a 15-minute block of a livestream transcript for clip candidates, for an editor
cutting Shorts for {channel_name}. Audience: {audience}.
About this stream: {stream_context}
The viewer: {audience_viewer}

The transcript has bracketed start times in seconds and no speaker labels. It mixes the
show's hosts with any video, clip or stream they play. You cannot see the video.

Find up to {scan_max_per_block} moments where the hosts' OWN words carry the moment and
this viewer would get it without the rest of the stream: a story with a payoff, a rant
with a sharp line, a joke or bit that lands, a strong take stated cleanly, a genuine
reaction the hosts put into words. Each moment runs 20-120 s.

Skip:
- moments whose content or punchline is a video or stream being played (a different
  voice or style, a monologue the hosts then react to, "watch this" / "listen")
- gambling play (slots, cards, bets, casino sites)
- bits that depend on something set up earlier in this stream
- slurs or attacks on private people as the main joke

Return an empty list when nothing qualifies; most blocks have zero or one. start_s and
end_s are bracketed times from the transcript. summary: one line on what happens. why:
one line on why it works on its own.
