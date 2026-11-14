`receipts` starts from a story and finds his old clips; `clipper investigate` starts from the
person and looks for the story. It reads an archive subject's own transcripts and comes back
with leads: claims that changed, numbers that moved, teases never paid off, topics that
suddenly took over. Leads are research for you, never uploaded or posted.

```bash
clipper investigate facts jer --since 2026-09-01 --dry-run   # items, hours, estimated cost
clipper investigate facts jer --since 2026-09-01             # Haiku reads each item for checkable facts (~$0.20 for 43 h)
clipper investigate leads jer [--about "lawsuit"] [--confirm] # threads found in code, Sonnet writes the leads (~$0.10)
clipper investigate check jer [--lead L03] [--trail]          # is anyone else on it? the search swarm, per lead
clipper investigate list jer                                  # kind, confidence, coverage, receipts
clipper investigate show jer L03                              # the story and the timeline, quote and link per receipt
clipper investigate confirm jer [--lead L03]                  # test leads against more of the archive (`archive ask`)
clipper investigate run jer                                   # facts --new -> leads -> check, total cost
clipper investigate post jer [--new-thread]                   # the kept leads to Discord #investigations
```

`facts` keeps only facts whose quote is found in the item's words (in the part it was read from,
with its numbers and any negation unchanged), and the receipt keeps his wording, so every receipt in a
lead is something he said, linked to the moment. Each item's facts are cached
(`archive/jer/investigate/facts/<item>.json`) until the prompt version or the transcript
changes, so a rerun reads only new uploads. The whole Jer catalog (2,731 h) is about $14 once;
quote the `--dry-run` figure before running it. It counts the prompt, the schema and the header that every call repeats (about 1,800 tokens), one call per `[investigate] chunk_s` of video and at least one an item, so a catalog of short clips costs much more per hour than long videos. `leads` builds `facts.sqlite` and finds
candidate threads with no model call: a trend, a contradiction (affirmed, then denied), a
shift (a number that changes), a dropped thread (checked against the full transcripts) and a
convergence (personal remarks from several videos close together); Sonnet reads each one and
writes the lead or rejects it, and the rejected ones and why are kept in `leads.json` too.
`check` runs the search swarm on each kept lead, seeded with the lead's search words on YouTube
(titles only), the web and Reddit (Brave, the `clipper cast` key; without one, Reddit's own feed,
which often refuses) and our own streams' segments; its planner adds searches and its workers
follow up, within `[investigate] swarm` (small: ~$0.01-0.03 a lead). With his own video titles,
Haiku labels each lead fresh, partly covered or covered. The results and verdict are in
`investigate/swarm/<lead>.json`, the task tree in `<lead>.trail.json` (`--trail` prints both). Edit `leads.json` to flip `keep`, change a headline or add
`notes`; `leads.md` is the readable dossier. Everyone's names in [`clipper people`](People) merge
their spellings, and the names of the person whose archive it is count as him. Put other
spellings Whisper gets wrong, or other names for him, in `investigate/aliases.json` (`{"Jeremy":
"self", "Hassan Piker": "Hasan Piker"}`), which wins over `clipper people`.
A run stops before it would spend more than `[investigate] budget_usd`. Whisper can mishear a
number, so a "shift" is worth a listen at the link before you use it.

With `[investigate] discord = true`, `leads`, `check` and `confirm` end by posting to
`[investigate] discord_channel` (`#investigations`, made if missing; a forum channel of that
name works too): one thread per investigation, that is per `leads` run, named
"jer · 2026-11-03" (with the `--about` focus when there is one), and one short message per kept
lead: headline, kind, confidence, coverage, how many receipts, the story's first sentence and a
link to the lead in the war room. A later `check` or `confirm` edits those messages rather than
posting again; a new `leads` run starts a new thread. The ids are in `investigate/discord.json`.
A post that fails says so and leaves leads.json as it is; `clipper investigate post jer` retries.
The war room's **Investigations** window shows each subject's dossier (leads.md, rendered),
newest first; a Discord link (`#inv=jer&run=<made_at digits>&lead=L03`) opens it at that lead
of that run (an earlier run is read from its `leads.<time>.json` backup). A lead that stops being
kept is marked withdrawn in Discord; a thread or message is only remade when Discord says it is gone.

## Setup

`clipper investigate` needs nothing new beyond a
transcribed archive and `ANTHROPIC_API_KEY`; `clipper archive index <subject>` (free) lets it
check dropped threads against the full transcripts and is needed for `--confirm`. Its coverage
check searches the web and Reddit through the Brave key `clipper cast` uses (see the cast
library above); without one it tries Reddit's own feed, which often turns unsigned clients away.
To get the leads in Discord, set `[investigate] discord = true`: the review bot's token and
`[discord] guild_id` are enough, and the bot makes `#investigations` if it has Manage Channels
(otherwise make the channel by hand; `clipper doctor` checks the settings).
