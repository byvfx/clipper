`clipper receipts` turns a stream's stories about the archive subject (`[receipts] subject`,
`jer`) into one video of his own old clips, to send to the show:

```bash
clipper receipts find kick-<id>     # Sonnet names the stories + a search each; `archive ask` finds the clips (~$0.20)
clipper receipts list kick-<id>     # stories, receipts ([x] = used), quotes, links
clipper receipts script kick-<id>   # Opus writes Mother Moo's voice-over for the used ones (~$0.02)
clipper receipts cut kick-<id>      # receipts/receipts.mp4 + description.txt; voices changed lines (ElevenLabs)
clipper receipts upload kick-<id>   # private, outside the schedule; the link goes in receipts.json
```

The edit is `streams/<id>/receipts/receipts.json`: flip `use`, reorder, trim `start`/`end`
(seconds in the source video), rewrite a story card (`story`, `now`) or the `title` /
`description`, then `cut` again. Each clip carries the channel and date on screen and its
source link at that moment in the description, with chapters per story. Clips are bleeped
from the archive's word timings like our own cuts. With a script, the open, each story card
and a "THEN / date" card before each clip carry the read (`[narrate] voice_id`, Mother Moo);
edit any line in receipts.json and `cut` re-voices only that one. Every part is normalized to
`[receipts] lufs`. It needs `ELEVENLABS_API_KEY` ([Character pieces](Character-pieces#narration-voice-elevenlabs)) only when there is a
script. Nothing is scheduled or made public: the
upload stays private until someone changes it in Studio, and sending it on is up to you.
