# Mother Moo: voice and delivery guide (#57, #58)

Voice: ElevenLabs library "Harriet - British Actress" (`[narrate] voice_id`), read with
eleven_v3 at stability 0.5, style 0, speed 1.05 (`[narrate] tts_settings`). Picked by the user
2026-10-23 as read "C"; reference script: `scripts/test_take_read_v3.txt`.

**Register:** a stern, punchy British newsreader. The comedy is in what she says, delivered
straight (Brass Eye). A little camp, rationed: two or three moments a segment, never the whole
read.

## Writing for her

- **Short sentences, full stops.** Punch comes from clipped lines: "For money. For tickets.
  For seats." Avoid ellipses except one deliberate beat ("For... the experience"); they make
  her drawl.
- **British phrasing:** "We go now, live, to our correspondent", "to establish whether",
  "Goodnight". No Americanisms ("gotten", "folks").
- **Cues** (eleven_v3 tags, before the line they colour):
  - the default register: `[crisp]`, `[stern]`, `[grave]`, `[clipped, urgent]`, `[flat]`, `[flat, deadpan]`, `[dry]`
  - the camp, sparingly: `[arch, one eyebrow raised]`, `[with withering disdain]`, `[a touch of mischief]`
  - timing: `[pause]` before a punchline or a reveal ("[pause] [flat] It does not.")
- **Camp moments** that worked: the raised eyebrow on a one-word reveal ("A fundraiser."),
  disdain on a quoted phrase ("the experience"), "darlings" once, in the sign-off.
- **Sign-off:** "That's all for tonight. Pour yourself something brown, darlings. And stay messy."

## Reading and performing

1. `clipper narrate speak script.txt` → `script.read.mp3` in her voice and settings.
2. Fix a line by changing its cue or punctuation and re-reading just that line (cheaper than the
   whole script); stitch the reads.
3. Perform to playback: lip-sync to the read on camera, then WAN 3 Prime makes it Mother Moo
   (#58). The read is the final audio.
