## pass_a
- third_party_audio: reaction clips are allowed, but only when the streamer carries them.
  The streamer is often playing someone else's video, clip or stream (signs: lines tagged
  [?] or with a name other than the show's own people, a different
  voice or speaking style, a monologue the streamer then reacts to, "watch this" / "listen"
  / pausing a video). Keep a reaction moment when the streamer's own commentary or reaction
  is the payoff and the Short needs at most {max_source_audio_s} seconds of the source's
  audio to set it up. Set third_party_audio=true (reject) when the source is the payoff
  (the funny or shocking part is what the video says or does, and the streamer only
  laughs along), or when the Short would need more than {max_source_audio_s} seconds of it.
  A few seconds of source setup does not by itself make a moment needs_context: judge
  whether the viewer gets it from that setup plus the streamer's commentary.
## pass_b
- Reaction clips are allowed when the streamer's commentary carries the moment and the
  source video's audio is only brief setup (at most {max_source_audio_s} seconds in the
  Short). Drop candidates whose payoff is the source video itself, even if the first pass
  kept them.
