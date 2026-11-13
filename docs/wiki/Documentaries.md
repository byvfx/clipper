`clipper doc` builds a narrated mini-documentary from a plan you write by hand: your script,
the subject's own clips, cover shots cut to the words. It buys the narration, builds the
timeline in DaVinci Resolve, masters the render to YouTube's loudness, checks the result for
anything that must not be in it, and writes the publish kit.

It differs from a [story cut](Story-dossiers): a story is planned by a model and rendered
by ffmpeg as cards and clips; a documentary is your script and your edit, finished in Resolve.
Research for one still comes from `clipper story run`, [archives](Archives) and
[b-roll](B-roll).

## Order of work

1. `clipper doc new <slug>`: the piece's folder.
2. Fill in `banned.txt` and write `plan.toml`. Lock the script before buying narration: most
   rebuilds are narration changes.
3. `clipper doc voice <slug> --dry-run`, then without `--dry-run`. Listen to every take.
4. `clipper doc build <slug>`: a new timeline version in Resolve. Watch it, change the plan,
   build again.
5. `clipper doc master <slug>`: the render, the loudness, the leak check.
6. `clipper doc kit <slug>`: chapters, description, credits. You upload in Studio by hand.

## The piece's folder

A piece is `[paths] documentaries/<slug>/`, beside `clipper.toml` and never in the repo:

- `plan.toml`: the script and the edit.
- `banned.txt`: what must never be heard in the render or written in the kit.
- `reels/`: the subject's own video. `overlays/`: cards, labels, text. `music/`, `sfx/`.
  A file is named in the plan by its name without the extension. A file kept elsewhere
  goes in the plan's `[media]` table (`name = "path"`).
- `vo/` (the narration), `work/` (the build, caches), `renders/`, `kit.md`: written by the commands.

## The plan

`plan.toml` starts with the piece's own settings, then `[[seq]]` entries in the order they
play.

```toml
title = "The Lighthouse"
project = "Lighthouse"        # the Resolve project that must be open for build (optional)
voice_id = "..."              # this piece's narrator; without it, [doc] voice_id
description = "..."           # for the kit
credits = ["Room tone: Freesound 123456 by Someone (CC0)"]
pinned = "The pinned comment."

[clips.wave]                  # a named stretch of the subject's video
reel = "reel_a"
from = 10.0
to = 14.0

[[music]]                     # a cue from a section on
file = "cue1"
at = "COLD OPEN"

[room_tone]
file = "roomtone"

[[seq]]
section = "COLD OPEN"

[[seq]]
card = "card_open"
seconds = 5

[[seq]]
say = "He climbed the stairs every night for forty years."
take = "open"
shots = [["reel_a", 1.0], ["reel_b", 5, "every night"]]

[[seq]]
clip = "wave"
```

Each entry is one of four kinds:

- `section = "NAME"`: a red marker on the timeline and a YouTube chapter. `chapter = "Title"`
  gives the chapter its own wording; `chapter = false` keeps it out of the kit.
- `card = "overlay"` with `seconds`: a full-screen card. `silent = true` stops the music
  under it, and the room tone too unless the card has a `sound`. `sound = "thump"` plays an
  effect from the card's start; `sound_before = "swell"` ends one there.
- `clip = "name"`: the subject's own picture and sound, from `[clips.name]` or from `reel`,
  `from` and `to` on the entry. `mute = [[9.07, 9.5]]` cuts those seconds of the reel out of
  the sound and leaves the picture. `over = "overlay"` puts text over it; `note = "..."`
  leaves a yellow marker; `hard_out = true` stops its sound from running on under what follows.
- `say = "..."`: a narration line. `shots` are its cover shots: a reel, where the shot starts
  in the reel, and (from the second shot on) the words of this line it cuts in on. A fourth
  item is an overlay for that shot. `silent`, `over` and `label = false` work as on a clip.

A cue is checked against the line's own words when the plan is read, so a cue that isn't
there fails before anything is bought. A mistake is reported with its line in `plan.toml`.

A still image (a label, a headline) is turned into a short movie in `work/stills/` for the
build, because Resolve gives a still its own length on a timeline whatever it is asked for.

A reel gets a label on the second video track when `overlays/label_<reel>` exists, or when
`[labels]` names another overlay for it.

### Takes

`say` lines that share a `take` are voiced in one read, in script order, so they sound like
one performance even with clips between them. The read is cut in the pause before each
line. Two lines that follow each other directly are cut in the middle of that pause, with
no fade. A line with no `take` is a read of its own.

## Narration

`clipper doc voice` buys each take from ElevenLabs once (`[narrate]`'s key and `tts_model`,
the plan's `voice_id` or `[doc] voice_id`). `--dry-run` checks the plan and prints how many
characters it would buy. A take is bought again only when its words, the voice or the voice
settings change.

Each bought read then goes through `[doc] vo_filter` (a light 2:1 compressor), has its
pauses cut down to `[doc] max_pause_s` (`max_pause = 0.9` on a line keeps longer ones in its
take), and gets its word times from a small local Whisper (free; it needs `faster-whisper`,
which the `gpu` extra installs). Changing the compressor or the pause cap processes the takes
again without buying them.

The narration is refused when a line contains a term from `banned.txt`.

## Build

`clipper doc build` turns the plan into frames (`work/build.json`) and builds a new timeline
named `<title> vNN` in the open Resolve project. An earlier version is never changed.
`--dry-run` stops before Resolve.

The timeline has three video tracks (picture, labels, text and cards) and seven audio tracks:
clip sound, narration, effects, clip sound under the narration, two score tracks and room
tone.

- Sound at the joins: a clip's sound fades out for `[doc] tail_s` under the next line, and
  the next clip's sound fades in for `[doc] lead_in_s` under a line's last words.
- Levels: every clip and every narration take gets a clip gain that brings it to
  `[doc] dialogue_lufs`, held back where its peak would pass `[doc] peak_ceiling`.
- Music: each `[[music]]` cue runs from its section (`at`, plus `at_s` seconds) into a
  `[doc] crossfade_s` crossover with the next cue, on alternating tracks, `[doc] music_under`
  LU under the dialogue. A cue stops at a silent stretch, and one whose section opens on a
  silent card starts after it. `tail = true` places a cue so its
  own ending lands on its last frame.
- Room tone: the `[room_tone]` file looped with one-second crossfades for the whole piece,
  `[doc] tone_under` LU under the dialogue.

The project's timeline frame rate must be the plan's (`fps` in `plan.toml`, else `[doc] fps`);
a build into a project at another rate is refused before anything is placed.

After building, every item is read back from Resolve; an item that isn't where the plan put
it, or a gain or fade Resolve refused, is an error.

### Resolve

Resolve Studio must be running on the same machine, with Preferences > System > General >
"External scripting using" set to Local. clipper finds the scripting module where Resolve
installs it; `[doc] resolve_api` and `resolve_lib` (or `RESOLVE_SCRIPT_API` and
`RESOLVE_SCRIPT_LIB`) name another place. `clipper doctor` reports whether it is found once a
piece exists.

## Master

`clipper doc master` renders the last built timeline to `renders/` (`--timeline NAME` for
another, `--file` for a render you made yourself), measures it, and writes
`<name>.master.mp4` with the sound brought to `[doc] lufs` (-14 for YouTube) by gain and a
limiter at `[doc] true_peak`. The picture is copied, not re-encoded. It prints the loudness
before and after. When the result is more than 1 LU off, raise `[doc] dialogue_lufs` and
build again rather than push the master harder.

Then it runs the leak check; `--no-leak` skips it.

## Leak check

`clipper doc leak` transcribes the mastered render on [Modal](Transcribe-on-Modal) (a few
cents, mostly the container's start; `--dry-run` prints the estimate) and fails when a term
from `banned.txt` is heard, printing the time and the words. It also reads `kit.md`. A
transcript is kept per file, so a second run on the same render costs nothing.

`banned.txt` is one term per line; `#` starts a comment. A term matches the same words (and
their possessive: `Ann` also finds `Ann's`, not `Anna`), the same letters split or joined differently, and, for terms of five letters or more, letters
close enough that a transcript's spelling of a name still counts (`[doc] leak_fuzzy`). Add the
spellings you expect anyway. A line starting with `!` marks heard words that look like a
term but are fine.

The check hears only the sound. On-screen text, and the channel's older titles and replies,
are yours to read.

## Kit

`clipper doc kit` writes `kit.md` from the plan and the last build: the title, the
description with chapters (from the sections: the first at 0:00, each at least ten seconds
after the one before and before the end, none when there are fewer than three), the credits (the plan's
`credits` and each `credit` on a music cue or the room tone), the disclosure lines
(`[doc] disclosures`, or the plan's own `disclosures`), the pinned comment and a checklist.
It fails when the text contains a banned term.
