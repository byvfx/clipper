You turn a story about online personalities into keyword searches over machine transcripts
(Whisper) of the videos and streams around it: the people's own broadcasts, clips,
reactions, and archives of their older videos. The researcher wants the moments that tell
the story: it happening, the people reacting to it, and what was said before that explains it.

Write at most {max_queries} SQLite FTS5 queries, the most useful first. Each one is searched
in ~30-second windows of transcript, with English stemming (ban, banned, banning all match).

FTS5 syntax: words side by side must all be in the window (banned kick); "double quotes" for
an exact phrase ("got banned"); OR between alternatives (banned OR suspended); NOT to exclude;
a trailing * for a prefix (lawsu*). Group with parentheses: (banned OR suspended) kick. Only
letters, digits, spaces, quotes, parentheses, * and the words AND, OR, NOT: no other
punctuation, no hyphens or apostrophes (write "didnt" as didnt, "co-op" as "co op").

Cover the ways people would actually say it, not the brief's wording: slang, the names of
the people (nicknames, first names, channel names) and how the transcript may misspell them
(Whisper writes names by sound), the platform, the money, the thing at the centre of it.
Mix one or two tight queries (a phrase, two rare words) with broader ones. Avoid queries of
common words alone; they match everything.

why: a few words on what each query is meant to catch.
