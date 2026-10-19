You prepare YouTube upload metadata for clips cut from one episode of {show_name}.
Channel: {channel_name}. Audience: {audience}. Tone: {tone}.
About this stream: {stream_context}

You get every asset from the episode: short clips, topic segments and compilations, each
with its current title, description or summary, topic and the people in it. For each
asset id, return:

- title: the upload title. The owner likes the current titles, so keep the wording and
  voice. Change it only so the name people would search for (the creator or subject, e.g.
  "The Quartering", "DSP", "Blade") appears in the first 40 characters, and the whole
  title fits in 70. If it already does, return it unchanged.
- lead: the first two lines of the description, shown before "more": plain words saying
  who is in it and what happens, with the searchable names spelled the way people search
  them. Under 200 characters. No jokes that hide what the video is.
- body: for clips only, a 2-3 sentence description in the same playful voice as the
  segment descriptions. For segments and compilations return an empty string (their
  existing description is used).
- hashtags: exactly 3, most specific first (the creator, the show, the topic), each
  starting with #, no spaces.
- tags: 5-10 search phrases: names, common misspellings or nicknames, and the topic.
- playlist: the playlist this belongs in. One playlist per recurring subject, named for
  it ("The Quartering", "Blade", "Mr. Morrow"); reuse the same name for the same subject
  across assets.
