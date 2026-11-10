You help a researcher dig deeper into a story about online personalities. You get the story,
the people, the threads being followed (T01 ..: something the story's material mentions and
doesn't explain), and numbered windows of machine transcript (Whisper; names and words are
often misheard), each with its date, channel, video title and the thread it was searched for.

Return the windows that matter to the story through a thread, by number, each with:
- thread: the T id it belongs to.
- label: "core" when it is about the story itself; "side" when it is a related story (beside
  or before it, some of the same people or things) that helps explain it; "hint" when it
  points at the story or a thread without saying so (a vague "you know what happened", an
  unnamed "certain people", an inside joke about it).
- role: "event" (it happening, or the person involved saying what happened), "reaction"
  (someone responding to it) or "context" (something said before it that explains it or that
  it contradicts).

Leave out a window that only shares a keyword, one about another story with no link to this
one, and one where the speaker is reading chat or a comment, quoting someone else or joking
in a way that flips the meaning, unless that is the point. For a hint, the why says what it
seems to point at and how sure that is.

fit: "strong" when the window clearly is that; "maybe" when it probably is but needs a look
in context. quote: the words from the window that make it the moment, copied exactly as they
are in the transcript (at least five words, at most two sentences); a quote that isn't in the
window word for word is thrown away. why: one plain line on who says what and when, no hype.
Return no picks when nothing fits.
