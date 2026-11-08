# Storyboard: test throw, "The Fundraiser" (#58)

Frames for `../../test_throw_script.md`, numbered by its shots. `contact_sheet.jpg` shows them all.
The PNG and MP4 files are gitignored; the JPGs are 1600 px previews. Clips are 1080p24 with the
audio stripped (the script uses no model audio).

- **2026-10-23 pass** (shots 2-5, 7, 7b, 10): gpt-image-2 stills and Seedance 2.5 clips, the same
  clips as `../throw_v1/`, renumbered for the longer script.
- **2026-10-24 pass** (shots 6, 8, 9, 11): Nano Banana 2.1 stills, Seedance 2.5 clips, built on the
  reference set in `../../cast/` (see `cast.md`). Shot 6 was remade because the first Rupert was
  off-model; the old one is in `superseded/`. Later the same day the shot 6 and 8 stills were
  redone with his one-hand grip (mic and umbrella handle in one fist; v2 stills in `superseded/`).
  The 6, 8 and 9 **clips** were rendered from the earlier stills, so in them the umbrella isn't
  clearly held; they stay as animatics, and the real Rupert take sets the grip. These clips are 1926×1076 (the stills' aspect ratio),
  not 1920×1080; scale on import.

| Shot | Still | Clip | Runway task (still / clip) |
|---|---|---|---|
| 1 logo sting | none, built in Houdini/Fusion | | |
| 2 desk, wide | 02_desk_wide | 8 s | 1da93ff6 / 3b541e55 |
| 3 desk, MCU, grave | 03_desk_mcu_grave | 11 s | 4e8a5541 / fe3b91f0 |
| 4 medium, framed left | 04_desk_alarm_left | 11 s | 9632d928 / b487a373 |
| 5 turn to monitor | 05_desk_turn_monitor | 12 s | a921ee56 / 15c50222 |
| 6 Rupert, medium | 06_rupert_medium | 15 s | cfbbc78d / 01577397 |
| 7 café window | 07_cafe_window | 6 s | a4276161 / e3025312 |
| 7b barista head-shake | 07b_barista_headshake.jpg (frame at 3 s) | 6 s, 720p | edit of 7: 584b73f8 |
| 8 Rupert close-up | 08_rupert_cu | 15 s | 31c3772a / fdff2562 |
| 9 umbrella flip | 09_umbrella_flip.jpg (frame at 1.5 s) | 4 s | from 8's still: 7eebd8a5 |
| 10 desk, deadpan | 10_desk_deadpan | 8 s | a1b41ae2 / 2340a235 |
| 11 sign-off wink | 11_desk_signoff | 7 s | cf75f837 / 09213fba |

## Checked against the script and `cast.md`

Mother Moo matches in every desk shot (face, bouffant, cat-eye glasses, red jacket, cow-print scarf,
pearls), and Rupert now matches in 6, 8 and 9 (wire-rims, late 50s, thinning grey-brown hair, beige
trench, maroon tie, foam mic, tartan umbrella). No frame has readable text, so lettering still goes
on in Fusion.

- **2:** looks down at her notes, as written. The video wall is black, room for a Fusion fill. Very
  little head movement in the clip.
- **3:** good. Slow push-in, grave, the coffee cup under a spotlight on the monitor.
- **4:** good. Framed left with empty blue on the right for the graphic, hands at chest height,
  building to open-mouthed alarm.
- **5:** the still is square to the lens, not three-quarter. The clip turns her toward screen right,
  which is **her left** (the script says her right), and so far that her face is gone by 8 s; it
  also starts calm, with no snap from alarm. Fix the script wording rather than mirror the clip; in
  the real take, stop the turn at three-quarters.
- **6:** good. The café door with its brass handle is beside him; about 2 s of stare with the
  finger to the earpiece, then he talks; a car passes behind.
- **7:** good: steamed window, cup and blue check towel, flat stare. **7b** (the head-shake) reads
  better as the joke, but is 720p.
- **8:** framing, glasses with rain on them, the earpiece listen and the stoic hold all work, but the
  **door gag is missing**: the door never opens and he doesn't step aside. From about 6 s to 9 s he
  also holds the mic up to his ear like a phone. The door can come from the real take, or an edit.
- **9:** good. The umbrella snaps inside out and he clings on. The camera reframes in the first
  second, so cut in after it.
- **10:** deadpan to the lens, as written, but no turn back from the monitor at the start and no tiny
  smile at the end. The background is the CRT bank, not shot 5's big monitor.
- **11:** good. Arms open, big warm smile, a clean one-eye wink from about 2 s to 5 s.

**Lens:** the location shots read wide-angle (the street recedes behind Rupert). A 90s news crew
would stand back on a long zoom (roughly 60-100mm full-frame equivalent on a 2/3-inch Betacam), so
the background is compressed and mostly in focus. A long-lens edit of shot 6 (deep focus a8ad1301,
shallow f1d70d76) barely changed the perspective; the user is keeping the current stills for the
pilot and handling the look in post.

**Look:** the 10-24 shots (6, 8, 9, 11) are sharper and warmer than the 10-23 ones, closer to modern
photography than Betacam. Grade them down together in Resolve so the location shots match.
