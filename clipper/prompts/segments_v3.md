You split a livestream into topic segments, so each segment can be uploaded on its own
as a long horizontal video (for example "the DSP segment" or "the Quartering segment").
Channel: {channel_name}. Audience: {audience}.
About this stream: {stream_context}

The transcript has bracketed start times in seconds. It mixes the show's own people
with any video, clip or stream they play.

Speaker tags: some transcripts name who is talking, matched from their voices. The
line that starts a new turn carries the name after its time ("[812.4] [PPP] ...");
lines without a tag continue the last one. The show's own people are named in "About
this stream"; any other name is almost always a clip of that person being played. "[?]"
is a voice nobody named: usually a video, clip or stream being played, sometimes a
caller or guest. "[music]" is a song, not talk.

Rules:
- Cover the whole stream in order: each segment starts where the previous one ends, the
  first starts at the first line and the last ends at the last line. No gaps, no overlaps.
- A segment is one continuous subject: one creator or story being covered, one video
  being watched and discussed, one call, one game. Start a new segment when the show's
  own people clearly move to a new subject. A subject they return to later is a separate segment.
- Fold asides shorter than about {min_aside_s} seconds into the segment around them.
  Most segments run 5-40 minutes.
- start_s: the bracketed time of the segment's first line. end_s: the next segment's start_s
  (for the last segment, the time of the last line).
- title: a YouTube title for the segment as its own upload, under 70 characters, honest
  about what happens. topic: a short label for the main subject ("DSP", "The Quartering",
  "Blade in Vegas"). figures: the creators or people it is about.
- topic names the creator or recurring story, not the angle: every segment about the same
  creator gets the exact same topic, spelled the same way, even when it covers a different
  incident ("The Quartering", never "The Quartering dog grift" or "The Quartering AI
  segment"). Segments sharing a topic are joined into one long video, so a new label
  splits them apart. Put the angle in the title instead.
- kind: intro, commentary (the show's own people talking), reaction (watching and
  discussing a video or stream), call (a guest or caller), gambling (casino or betting play), ad_read, break,
  outro, or other.
- source_share: how much of the segment is audio from a video or stream being played:
  none, some, or most. With tags, that is the lines from [?] and other names.
- standalone: true if a viewer who knows the show's regulars ({known_figures}) would follow
  it without the rest of this stream.
- summary: 1-2 sentences for the editor. With tags, say who said what; never credit a
  clip's words to the show's own people. description: 2-3 sentences for the YouTube
  description, written for viewers.
