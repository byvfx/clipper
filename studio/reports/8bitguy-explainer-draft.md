# The 8-Bit Guy's AI movie: explainer draft (2026-11-04)

Source: ChudLogic, Kick, 2026-10-18 (`streams/kick-132975332`, 9.4 h, transcribed). The 8-Bit Guy
part runs 7:30-9:17: Chud plays David Murray's announcement video, reads a viewer's notes, watches
the whole 50-minute film, then reads the comments. Chat ran well above his average the whole time
(40/min avg; peaks 7:58, 8:39, 9:06-9:10 at up to 191/min).

## What happened (as the stream shows it)

- David Murray (The 8-Bit Guy, retro computers) posts that he was off for three months with health
  trouble, and made something "quite unusual for my channel": an AI-made film. He opens with a
  disclaimer for viewers who hate AI.
- The film is **The Tech Support Guy**, ~50 minutes, released free on YouTube after a cinema premiere.
  Chud says it was mostly prompted on a phone, with Grok video and Suno music (from the film's own
  credits, 9:10).
- The hero is David Larson, the powerless tech-support man of a superhero team, who catches the
  villain Viper, hides her in his bunker for a month without telling the team, and falls for her.
- Premiere: Murray posted that he was stuck in traffic and might miss it, with a video from his car
  (7:46). Chud quotes 16 people at the screening, then 60, so leave the number out.
- Comments under the film are brutal ("We're witnessing internet history. Not the good kind.").

**Leave out:** the "AI girlfriend Mika" Reddit posts (7:39-7:43). They come from a viewer's document
("according to Goy Walker") and aren't confirmed to be Murray's. Also Chud's rape/fetish lines and the
remarks about Murray's wife: his opinion, said as fact, about a real person.

## Script as rendered (the prototype mp4s, 58.8 s)

MOO = Mother Moo (ElevenLabs v4, Harriet). The words in [brackets] are v4's delivery directions;
she doesn't say them. Times are the stream's (transcript) clock; bites with two times are cut
inside, to drop a pause or a few words. Reads total 33.9 s, bites 27.7 s; the J/L-cut overlaps
bring it to 58.8 s.

| # | Sound | Picture | s |
|---|-------|---------|---|
| 1 | Bite, the film: "I know how to install Windows NT." | the film, 7:59:03 | 2.2 |
| 2 | MOO: "[stern British newsreader, crisp and measured] This is The 8-Bit Guy. For years, he has mended old computers on YouTube. [grave] Then he vanished... for three months. [flat, deadpan] He came back with THIS." | Murray's announcement video | 10.3 |
| 3 | Bite, Murray: "This video is going to be a little short and quite unusual for my channel." | 7:31:06 | 3.9 |
| 4 | MOO: "[stern] A fifty-minute superhero film. Made with A.I. [arch, one eyebrow raised] It is called... The Tech Support Guy. [dry, deadpan] The hero does tech support." | the film, ~7:52 | 10.4 |
| 5 | MOO: "[crisp] He booked a cinema for the premiere. [dry, a beat later] He did not make it." | Murray's premiere post, ~7:46 | 4.2 |
| 6 | Bite, Murray in his car: "I have been stuck in this traffic jam for... over an hour" | 7:46:38 | 4.5 |
| 7 | MOO: "[clipped, brisk] Kick streamer Chud Logic, whose own YouTube channel had been terminated days earlier, sat through all fifty minutes. [with withering disdain] On our behalf." | Chud, ~7:48 | 9.0 |
| 8 | Bite, Chud: "I present to you / the tech support guy, the full movie." | 7:48:45 / 7:48:47 | 3.5 |
| 9 | Bite, the film: "I also checked the security cameras. It's true. Viper has been here for almost a month, right under our noses." / Chud: "...but no one looked at any point in the past month?" | 8:43:28 / 8:43:41 | 8.5 |
| 10 | Bite, Chud: "...his channel is restoring old tech." / "...literally the worst possible audience." | 9:16:55 / 9:17:03 | 5.2 |

Changes from the first draft below: Moo's lines are rewritten for her voice ("mended", "vanished",
"He did not make it", the "On our behalf" button); "watched all of it" became "sat through all
fifty minutes"; the YouTube termination is "days earlier" (it was ~10-16, the stream 10-18).

## First draft: a vertical Short, ~55 s

N = narration (ElevenLabs). Bites are from the stream at its own timestamps.

| # | What | Source | ~s |
|---|------|--------|----|
| 1 | Bite, the film: "I know how to install Windows NT." | 7:59:03 | 2 |
| 2 | N: "This is The 8-Bit Guy. For years, he's fixed old computers on YouTube. Then he went quiet for three months, and came back with this." | | 9 |
| 3 | Bite, Murray: "This video is going to be a little short and quite unusual for my channel." | 7:31:06 | 4 |
| 4 | N: "A fifty-minute superhero film, made with AI. It's called The Tech Support Guy. The hero, David, does tech support." | | 7 |
| 5 | N: "He booked a cinema for the premiere, and didn't make it." | | 3 |
| 6 | Bite, Murray: "I have been stuck in this traffic jam for over an hour." | 7:46:38 | 4 |
| 7 | N: "Kick streamer ChudLogic, whose own YouTube channel was just terminated, watched all of it." | | 5 |
| 8 | Bite, Chud: "I present to you the tech support guy, the full movie." | 7:48:45 | 4 |
| 9 | Bite, the film: "Viper has been here for almost a month, right under our noses." + Chud: "So there's security cameras... but no one looked at any point in the past month?" | 8:43:30-8:43:48 | 10 |
| 10 | Bite, Chud: "His channel is restoring old tech... It's like literally the worst possible audience." | 9:16:52-9:17:07 | 7 |

Narration is ~75 words. Source audio from Murray's film/videos stays at or under 5 s per bite
(`max_source_audio_s`). No "hosts". Swearing in bites goes through the bleep pass.

Alternatives for slots 9-10: the plates going from four to two (8:02:09), the hero picking up
Viper ("Well, I could carry you", 8:06:28), "Sir, we don't have a holodeck" (8:11:21), the cartoon
switch (8:29:47).

## Making the sound feel natural

What makes narration over stream footage sound pasted on, and what to do instead:

1. **Duck, don't cut.** Keep Chud's audio running under the narration, pulled down ~12-15 dB by a
   sidechain compressor keyed on the voice (fast attack ~20 ms, release ~400 ms). The room, his
   breathing and the chat sounds stay alive and swell back between lines.
2. **Put the voice in the same space.** TTS is dry and studio-clean; the stream is a compressed
   USB mic in a room. High-pass the narration (~90 Hz), a touch of presence, a very short room
   reverb, and a faint bed of Chud's own room tone (from his quiet moments) under it, so it never
   sits in dead digital silence.
3. **Land lines in his pauses.** The transcript has word times: place each narration line in a
   gap in Chud's speech, not over a sentence. Generate it line by line (ElevenLabs with
   previous/next text, so the delivery still flows) with timestamps, so lines can be nudged.
4. **Overlap the cuts.** 40-80 ms audio crossfades at every cut (no clicks); J-cuts (the next
   bite's sound starts ~250 ms before its picture) and L-cuts (the narration's tail runs over the
   next shot), the way a human editor hides joins.
5. **One loudness at the end.** Match narration and bites first (around the same short-term
   LUFS), then the Shorts loudnorm pass we already run.
6. **Music optional.** A low bed, ducked harder than the stream, glues the cuts; leave it out on a
   first pass and A/B.

Prototype: render the same cut twice, **flat** (hard cuts, voice on top, stream muted under it)
vs **natural** (1-5), and compare.

## Prototype, 2026-11-04 (8bitguy-explainer/, beside this file)

`8bitguy_flat.mp4` and `8bitguy_natural.mp4`: the same 58.8 s vertical edit (13 shots, chud `watch`
layout; Murray's two shots use his player full height because Chud's cam is off then), two mixes.
`mother_moo_v3_then_v4.mp3`: the four lines read by eleven_v3 (Harriet, tags as in her guide) then
eleven_v4. Built by `build_prototype.py` (scratch, not a clipper stage yet).

- **v4 vs v3:** the same lines took 33.9 s in v4 against 38.5 s in v3, with 0.3-0.9 s pauses
  instead of 1-2 s; v4 also says "Chud Logic" cleanly (v3 came out "Chad Logic" to Whisper). v4
  takes direction as inline descriptive tags ("[stern British newsreader, crisp and measured]"),
  pacing by punctuation (ellipses, CAPS), no break tags; `previous_text`/`next_text` stitch the
  lines; `/with-timestamps` works (character times saved next to each read).
- **Natural mix:** each source normalised to -18 LUFS, the read +1 LU over that; voice chain
  high-pass 90 Hz, -2 dB at 250 Hz, +2 dB at 3.2 kHz, light compression, two short early
  reflections (13/27 ms); the B-roll's own sound runs under each read, sidechain-ducked ~17 dB
  (about 20 dB under her); reads start 0.15 s before their shot (L-cut), bites' sound 0.25 s before
  their picture (J-cut), 40 ms crossfades inside bites; final loudnorm -14 LUFS / -1.5 dBTP.
  Flat: hard cuts, raw levels, silence under the reads, same final loudnorm.
- **No room tone to borrow:** Chud's mic is gated (about 30% of his quiet moments are digital
  silence), so a room-tone layer has nothing to sample; the ducked bed carries continuity instead.
- **Fetched-VOD clock bug:** this VOD's timestamps jump back 821.7 s somewhere before 7:28, so
  transcript times after that are 821.7 s ahead of the video's own clock. Seeking the source at a
  transcript time lands 13.7 minutes off, so `clipper run` must not cut this stream until fetch
  is fixed. Also: Kick's open GOPs drop frames after an input seek, so cut with an output-side seek.

## In DaVinci Resolve, 2026-11-04 (8bitguy-explainer/resolve/)

Resolve Studio 21.1.1 on the laptop, driven by samuelgursky/davinci-resolve-mcp (registered for this
project) or straight through Resolve's Python API. The project **"8-Bit Guy explainer"** holds timeline
**"8-Bit Guy explainer v1"**, imported from `8bitguy_explainer_v1.otio`; every clip is linked, the timeline is
1080x1920 at 30 fps, 1760 frames (58.7 s). `resolve_check.mp4` is Resolve's own render of it.

Tracks:

| Track | What |
|-------|------|
| Video 1 | 13 shots, already laid out vertical (chud `watch` crop; Murray's player full height) |
| Stream A / B | the bites' own sound, alternating so a J-cut (sound 8 frames ahead of picture) can overlap the shot before |
| Bed A / B (ducked) | the stream under each read, already ducked against Moo (the sidechain from the natural mix, baked in) |
| Mother Moo | the four v4 reads, EQ/compression/reflections baked in, 4-5 frames ahead of their shot (L-cut) |
| Music | empty, for a bed |

Ten timeline markers carry the script: blue "Moo n" markers with the line (and its v4 tags) and the
picture under it, yellow "Bite" markers with the words.

How it's built (`resolve_export_prototype.py`, `resolve_import_prototype.py`, scratch copies):

- **Media with handles:** one .mov per item (H.264 CRF 16, short GOP, PCM audio), 1 s of spare footage each
  side, so cuts can be opened up; a two-part bite is one file cut twice. Picture and sound are cut together
  from the source, so they stay in sync.
- **Levels are baked into the files**, because Resolve's API cannot set clip volume, faders or automation
  (`SetProperty("Volume")` returns False; the MCP says so too). Each source is at -18 LUFS, Moo at -17,
  and the beds are already ducked, all with -1.9 dB master trim so nothing clips. The timeline therefore
  plays at -19.2 LUFS: on the Deliver page turn on audio normalisation to -14 LUFS / -1 dBTP (or put a
  limiter on the main bus and raise it). Sidechain ducking in Fairlight is not scriptable either, which is
  why the beds come pre-ducked; re-duck by hand if a read moves.
- **The OTIO file is in Resolve's own shape** (copied from `resolve_reference.otio`, a timeline Resolve
  exported): Clip.2 with `media_references`, bare Windows paths, `available_range` from frame 0 (the media
  has no timecode), the Resolve effect stubs, track names. A hand-written plain OTIO imports as nothing
  (the MCP's notes say so: Resolve returns None and gives no reason).
- **Check:** the render's sound matches the natural mix second by second within about 1 dB (the prototype
  had a final loudnorm) and within ~10 ms; the picture matches shot for shot.
- **Found on the way:** the prototype mp4s' picture runs 1.5 s late against their sound. The re-cut .ts
  chunks start their video 1.5 s after their audio (cut at a keyframe), and `build_prototype.py` seeked the
  picture on its own. The Resolve media is right; the mp4s were not re-rendered.
- **Left in the project:** a "probe (delete me)" timeline and two media pool clips from the shape probe.

**v2 (user: "hold the first frame of the 8bit guy and then play it"):** timeline "8-Bit Guy explainer v2"
(`8bitguy_explainer_v2.otio`, render `resolve_check_v2.mp4`). Murray's first shot freezes on its first frame
while Moo says "This is The 8-Bit Guy." (46 frames, 1.54 s, up to the pause after that sentence, found from
the read's audio), then plays from that same frame on "For years...". The freeze is a real Resolve freeze
frame (OTIO `FreezeFrame.1` on a clip, which Resolve imports), so it can be lengthened in the timeline. The
frozen stretch is silent under Moo; the bed (`bed1_hold.wav`) fades in 0.2 s before the picture moves. v1 is
untouched (v2 reuses its media and adds only the new bed).

**v3 (user: "use the 03 bite murray first frame while moo is talking, then once moo stops 8bit can
[talk]"):** timeline "8-Bit Guy explainer v3" (`8bitguy_explainer_v3.otio`, render `resolve_check_v3.mp4`).
Under Moo's whole first line (287 frames) the picture is the first frame of Murray's bite ("This video is
going to be a little short..."), frozen; when she finishes, the same shot plays from that frame with his
own sound. No J-cut into that bite (his voice over his still face would look like bad lip sync) and no
stream bed under the line (a still picture with sound under it reads wrong). A freeze longer than the
media's handles imports fine: its OTIO duration is the record span. v1 and v2 are untouched.

**Line 1 takes (user: "it sounds like moo is saying crap in the first wav"):** Whisper hears every word of
the original read correctly; its weak spots are "8-Bit Guy" (heard as "8th", 0.59) and the long deadpan
"THIS" at the end (its tail alone reads as "Yes"). Three fresh v4 takes of the same text (seeds 11/22/33,
~620 characters, `moo_takes_prototype.py`) score about the same, so it needs ears: `moo_line1_takes.mp3`
plays the original, then seed 11, 22, 33, with 1.5 s gaps.

**v4 (user: "un AI mother moos voice a bit and get it feeling more real with fairlight"):** timeline
"8-Bit Guy explainer v4" = v3 plus Moo **dry** (`moo1-4_dry.wav`: tightened and levelled only, no EQ,
compression or reflections) on "Mother Moo (dry)", with the baked reads on "Mother Moo (baked)", disabled,
for A/B. The Fairlight chain goes on the dry track.

What scripts can and can't do with Resolve audio (21.1.1, checked live): no clip or track volume, pan, EQ,
automation or FairlightFX (`SetProperty("Volume")` returns False); a whole mix saved in the UI as a Fairlight
preset can be applied to a timeline (`ApplyFairlightPresetToCurrentTimeline`; none saved yet); timeline
`NormalizeAudioLevel` sets clip levels to a standard (YouTube, EBU R128, True Peak, ...), each clip on its
own or as a group; voice isolation on/off per clip or track; render audio format/codec. So: build Moo's chain
by hand once, save it as a preset, and the MCP reapplies it to every later timeline that keeps the same
track layout.

Fairlight recipe for the dry track (a starting point, by ear):

1. EQ: high-pass ~90 Hz; -2 to -4 dB around 3-5 kHz where TTS sounds glassy; +1 dB low shelf ~180 Hz for
   body; gentle roll-off above ~12 kHz (real mics and rooms soften the very clean top).
2. Dynamics: compressor ~3:1, 3-5 dB of gain reduction, medium attack; De-Esser if the S's spit.
3. A little grit: Soft Clipper or Distortion at a very low mix, just enough harmonics to break the
   too-clean digital edge.
4. Space, the biggest single tell: Reverb on a small room or booth, decay 0.3-0.5 s, pre-delay ~10 ms, wet
   well under the dry (start around -20 dB). If your build has AI Dialogue Matcher (Resolve 20+), match the
   dry read to a reference clip of a real VO in a room instead.
5. A noise floor: real room tone under the whole track at about -60 dBFS (Chud's mic is gated, so record
   10 s of your own room or use a library one).
6. Breaths: real VO breathes before sentences; TTS often doesn't. Add a few soft breaths from a library
   before the sentence starts, low.

Fairlight can't fix the read itself (rhythm, emphasis). For that: more v4 takes and direction tags, or
ElevenLabs speech-to-speech (perform the line yourself and convert it to Harriet), which keeps human timing
and breaths.

**v5 room tone (user: "find some room noise online we can use"):** BigSoundBank #2458
[Sound Atmosphere in a House](https://bigsoundbank.com/sound-atmosphere-in-a-house-s2458.html) by Joseph
Sardin, CC0 (commercial use, no credit required; the site says its free sounds aren't in Content ID). WAV
48 kHz/24-bit, 1:46, a steady -30 dBFS with three small bumps; saved in `resolve/roomtone/` (gitignored).
Timeline "8-Bit Guy explainer v5" = v4 plus "Moo room tone": one clip under each dry read, cut from the
steadiest stretch (0:47 on, 7 s apart per line), at -62 dBFS RMS with 30 ms fades. Raise or lower that
track's fader to taste; it's meant to be felt, not heard. (The site blocks scripted downloads; it came
through the app's browser.)

**v6 (user: "lose the bit of footage of chud at the beginning, just start with 8 bit like we have it
now"):** timeline "8-Bit Guy explainer v6", 56.9 s (`8bitguy_explainer_v6.otio`, render
`resolve_check_v6.mp4`). The Windows NT opener (the film over Chud's cam) is gone; the edit opens on the
frozen first frame of Murray's bite, Moo's first line comes in 0.25 s later, and when she finishes he plays.
Everything else as v5 (dry Moo, room tone, baked Moo disabled). Media names now follow the shot's place in
the full edit, so dropping a shot re-renders nothing; v6 has its own bed files (`bed2-4_v6.wav`).

Next, if this is the way: a `clipper explainer` stage (script → reads → edit → media + OTIO), with the MCP
used for the import, render and checks.

## Posted to X (2026-11-04)

https://x.com/lolcowcinema/status/2118120833046741152: v6 with burned-in Shorts-style captions
(Whisper words, "8-Bit", "A.I." and "fifty" fixed by hand), loudnorm -14 LUFS, 720x1280
(`8bitguy_explainer_v6_x.mp4`; the 1080x1920 master is `8bitguy_explainer_v6_captioned.mp4`).
Text in `x_post.txt` (version A, no Mother Moo, no links), crediting @IrrationalChad.
