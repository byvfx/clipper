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

## v4: busier still (user, 2026-11-14: wire sloth, "I would add more stuff, I want it to feel really busy")
`*_v4.html/jpg` + `contact_sheet_v4.jpg`, `cover_v4.css` = v3 plus, on every cover: an
EXCLUSIVE! scalloped seal, a WIN! burst with a prize line, a PLUS! roundel, a white TOP 5 list
card, "pg. N" page-reference tags on four items, XOXO in red marker, a lipstick kiss, a
"zzz..." speech bubble from the sloth, a dashed "100% UNOFFICIAL FAN MAG" seal by the barcode,
a PULL-OUT POSTER edge tab, and 7-8 sparkles and 7 hearts scattered in the gaps. The wire sloth
(`sloth_sticker.png`) is the mascot; the branch variant is kept for reference only.
**The sloth is the H** (user, same day): the masthead renders SLOT·BEAT with the H's advance kept
blank (`.mast .h{visibility:hidden}`) and the sticker hung in the gap at 110% (114 px wide).

## v7: the mix (user, 2026-11-14: "I like v6, let's do a mix of v5 and v6, adding some gradients
to the existing shapes with some that have that mosaic print look")
`*_v7.html/jpg` + `contact_sheet_v7.jpg`, `cover_v7.css`. The v6 layout untouched; the panels get
v5's finishes: the ground is a light-to-dark gradient of its colour, the left column a gradient
with the mosaic print over it at 55%, the bottom-right wedge a two-colour gradient, the top-right
wedge a gradient with a finer mosaic at 60%, and one wedge stays flat so the eye has a rest. The
mosaic is an SVG pattern of rounded tiles in the panel's own colours (two patterns, 7x7 and 8x8
blocks, seeded per cover). Text boxes stay flat for legibility. **Current direction.**

## v6: the 'Teen grammar (user sent three 'Teen covers, 1986-1989; see `refs/README.md`)
`*_v6.html/jpg` + `contact_sheet_v6.jpg`, `cover_v6.css`, built with the same stand-in photos.
Different rules from Bop: flat colour panels cut on diagonals (one ground, one left column, two
wedges, four colours per issue, no gradients, no outlines on text); a black top strip with
coloured bullet dots; the striped Sloth Beat masthead kept as the brand with the sloth as the H;
a tiny date/price line under it; one dominant photo right (two on the duo cover) with no sticker
border; secondary photos in thick white frames with a black keyline and their names in condensed
caps underneath; coverlines in Oswald condensed caps, white or black, each with a bullet-dot
sub-line in Archivo Narrow; one script line in Lobster ("stories you'll lose sleep over!"); a
"'SLOTH BEAT INQUIRER..." box of small photos; a cyan "INSIDER'S REPORT" tab; a vertical coverline;
and the mailing label bottom-right as the period artifact. Cleaner than v4/v5 and reads better
small; the elements are fewer but each is bigger.

## Photo test (user, 2026-11-14: "run a test with real pictures on both versions")
`*_v4p.*` and `*_v5p.*` + `contact_sheet_v4p.jpg` / `contact_sheet_v5p.jpg`: v4 and v5 with
photographs in every face slot instead of silhouettes. The cloud session cannot fetch frames of
the real subjects (network policy), so the photos are **Runway stand-ins, generic people, not
likenesses**: `photo_a.png` (bearded, the main face), `photo_b.png` (glasses), `photo_c.png`
(grey hair, suit), `photo_d.png` (moustache), `photo_owl.png` (plush owl). Cut off their white
studio background by edge flood-fill (`cutphoto.py` in the session scratchpad; a clean matting
tool was not installable here). Photos keep the sticker outline (stacked drop-shadows) and the red
name bars. For the real thing: frames from the videos, cut out with the thumbs stage's person
matte (DeepLabV3, as the TikTok cover did).

## v5: early-90s pop-art background (user, 2026-11-14: "more shapes in the bg, the pop art look
with the mosaic look and gradients, that early nineties look, along with our stuff here")
`*_v5.html/jpg` + `contact_sheet_v5.jpg`, `cover_v5.css`. Same elements as v4, the arms-up sloth
(`sloth_h.png`, the user's pick) in the H slot at natural size, and the background replaced by a
generated SVG (`nineties_bg()` in the build, seeded per cover so each is different):
- a three-stop diagonal gradient per palette (pink/purple/cyan, cyan/teal/yellow, yellow/orange/pink);
- a pop-art mosaic of rounded tiles over the whole page at low opacity, plus three bold round
  mosaic patches with outlined tiles;
- Memphis shapes with black outlines: triangles, rings, squiggles, three-dot rows, bowties,
  quarter circles, pills, confetti, eight-point stars, some gradient-filled;
- two halftone dot patches, a black-and-white checker strip at an angle, two zigzag bands.
The scribble and the sun rays are gone. Everything still sits under the faces and shapes.

## Arms-up sloth (the brief's pose 1), side by side
`*_v4_h.html/jpg` + `contact_sheet_v4_h.jpg`: the same v4 covers with `sloth_h.png` in the H slot at
its natural proportions (no stretch; `object-fit: contain` in a 140x200 box). `sloth_h.png` is
Runway task 71eaa329-dcb9-4a23-b363-de586fee48e1 (Seedream 5 Pro, 3:4, the first prompt in
`mascot_brief.md`); `sloth_h_b.png` is its sibling 7fd620ce-7354-40b3-9dc9-841874a40972 (has blush,
which the brief rules out). The user likes the stretched wire sloth in `*_v4.*` too, so both sets
stay. Still JPEG-preview cutouts, not the real transparent PNGs.

## v3: as dense as the references (user: "density can be even tighter", 2026-11-14)
`*_v3.html/jpg` + `contact_sheet_v3.jpg`, `cover_v3.css`. Every gap is filled and things overlap
the way the Bop covers do: the main face stands in front of the masthead's tail, labels sit on
shoulders, a starburst sits on a face's edge. New over v2:
- Wavy yellow ribbon across the top with a white "details (since you asked)" circle on it; on
  the flat cover "12 HUGE POSTERS!" in white bubble letters plus a "NEW!" burst.
- Masthead with a four-point sparkle; thinner stripe bands; the sloth is a real sticker when
  `SLOTH_IMG` is set in the build (see below).
- Red name bars on the small cutouts (Bop's "DYLAN" / "COLE"), 5-6 faces per cover including
  a two-face "sneak backstage" pair and the stuffed owl as a recurring bit.
- More shape slots: "ONLY IN SLOTH BEAT!" slug, QUIZ circle, "X wants a new look!" label,
  "FREE STICKERS", a white quote card in a slab serif, the posters list with a starburst behind
  the number, hearts, the barcode rotated onto the right edge on the flat cover.
- The tagline is gone from the face of the cover; it lives in tiny spine text on the left edge.
- Heavier crayon scribble (two turbulence layers, one light, one dark) on the two crayon covers.
**Mascot:** `sloth_sticker.png` is a Runway image (Seedream 5 Pro, prompt in HANDOFF, task
1b5e5334-57ab-42f2-9026-c51ad30d8b1d), cut out of the tool's JPEG preview with Pillow because the
cloud session could not fetch the transparent PNG; `sloth_sticker_b.png` is the second variant
(task 4d759bc7-5165-4588-a932-9d3b4374752b, on a branch with leaves). Both are cute-kawaii rather
than the 1990s flat-mascot look; a proper mascot pass (poses, a Sloth Beat badge, less kawaii) is
still to do. Fonts added: Titan One (masthead), Fredoka (name bars, slugs), Kalam (ribbon, quotes), Lobster
(window subtitles), Alfa Slab One (quote card), Bubblegum Sans, Chewy.

## v2: the Bop references (user sent two Bop covers, July 1995 and ~2007)
`*_v2.html/jpg` + `contact_sheet_v2.jpg`, built against the references rather than a tidy pastel
layout. What they add over v1:
- Stacked bubble masthead: Titan One, pink with white horizontal stripes, five extruded layers
  (white / pink alternating), black outline. The main face sits *in front of* the masthead's tail
  and bleeds off the bottom; small cutouts (white or yellow sticker outline) fill every corner.
- Coverlines in shapes, one fact or tease per person: top ribbon ("The truth about X's
  obsession" / "12 HUGE POSTERS!"), purple triangle ("2 X centerfolds plus pinup"), pink heart
  ("How to know if X is flirting with you"), yellow circle ("Who's X in love with?", "OMG did X
  get in a fight?"), red starburst with a big number + vertical stacked word ("22 PINUPS",
  "16 SEASONS"), cyan box ("What gets X's motor running?"), pink quote box ("X: 'I look for...'"),
  black window ("THE SHOW at play"), orange "POSTER SPECIAL" panel with a mini photo strip, a
  posters list. Price and barcode bottom-left.
- Two backgrounds: 1995 crayon scribble (multicolour gradient + SVG turbulence, `crayon` /
  `scribble2`) and 2007 flat cyan with sun rays (`flat`).
- Copy register: teases *about the person*, questions, numbers, "only in Sloth Beat".
The thumbs-stage version would pick 4-6 of these slots per cover from the stream's other items
(the teaser band's job on the enquirer style) and fill the names from meta, not hand-write them.

## Anatomy (v1)
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
