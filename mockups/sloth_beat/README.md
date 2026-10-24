# Sloth Beat: cover mockups (issue #46, 2026-10-24)

The second YouTube channel's look: a 1970s-2000s teen fan magazine (Tiger Beat, Bop, 16),
the opposite register from Lolcow Cinema's Enquirer covers. The subject is adored, not
caught. Name chosen by the user on 2026-10-24: **Sloth Beat** (favourite animals: bears,
sloths, dogs, otters, monkeys; the sloth won because "Beat" says tempo and a sloth has none,
which also describes the channel: long stories, slow comedy, no drama cycle).

Three 1280x720 covers, one per planned subject, plus `contact_sheet.jpg`:

| File | Subject | Palette |
|---|---|---|
| `01_papa_meat` | Papa Meat story supercuts (#45) | pink |
| `02_ben_avery` | Ben Avery's The Show (#47) | cyan |
| `03_on_cinema` | On Cinema at the Cinema (#46) | yellow |

**These are layout mockups.** The faces are placeholder cutouts (an egg with a name tag);
real covers cut the face from the video's frames the way the enquirer style does. The copy
is mockup copy. The sloth is a placeholder drawn in SVG; the real mascot is a Runway job
(flat sticker style, transparent background, a few poses) like Mother Moo was.

## Anatomy
- Masthead: SLOTH (white) BEAT (yellow, pink or whichever accent the palette gives) in
  Luckiest Guy, black stroke, black + accent offset shadow. Tagline "THE SLOWEST FAN MAG ON
  YOUTUBE" and an issue line (No. 1 · 75¢ · date). The sloth hangs off the end of BEAT.
- Big cutout face(s) right, white sticker border, hard drop shadow, blobby border radius so
  no two look the same. Two faces overlap for a duo (On Cinema).
- Kicker (Bangers on black, tilted) and a two-line Anton headline left, one accent word.
- Stickers: starbursts (Bangers), marker pills (Permanent Marker), hearts. They may overlap
  the face border but never the face itself or the headline.
- Bottom bar "ALSO INSIDE" plugging the other subjects, the Tiger Beat "also in this issue"
  strip. Same job as the enquirer teaser band.
- Dotted halftone over a radial gradient; three palettes (pink, cyan, yellow) via a class on
  `<body>`.

## How they are made
Each cover is an HTML file sharing `cover.css`, rendered by headless Chromium (Playwright)
at 1280x720. Open any `.html` in a browser to push things around; fonts load from Google
Fonts (Bangers, Luckiest Guy, Anton, Lilita One, Permanent Marker, Bowlby One; OFL) with
local copies in `fonts/` for offline rendering. This is the renderer proposed for the
channel's thumbs style: HTML template + Chromium instead of Pillow, so the design can be
drawn in Illustrator, exported as SVG and filled by the pipeline. Nothing in `clipper/` uses
it yet.
