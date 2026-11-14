# Sloth Beat mascot brief

The channel's logo is the word SLOTH BEAT in fat striped bubble letters, and the sloth *is the
H*: it hangs in the H's slot between SLOT and BEAT on every cover. Everything else the mascot
does (bursts, stickers, the channel avatar) is secondary to that one job. Chosen by the user
on 2026-11-14 from the v4 mockups in this folder; the stretched "wire sloth" there is the
placeholder this brief replaces.

## The character
- A sloth who is a fan. Sleepy, affectionate, in no hurry. Never sarcastic, never scared, never
  a "lolcow" register: Sloth Beat adores its subjects (the Tiger Beat / Bop tone), Lolcow Cinema
  is the other channel.
- Three long curved claws per hand and foot, round tan face with dark eye patches that run down
  past the eyes, small black nose, half-closed eyes and a small closed smile. No visible teeth.
- Reads at 40 px tall on a phone thumbnail. If a detail does not survive that, drop it.

## The look (1990s flat sticker, not kawaii)
- Flat solid colours, no gradients, no airbrush shading, no blush dots, no sparkle eyes.
- Thick black outline (about 2.5% of the figure's height), rounded joins.
- A thick white sticker border around the whole silhouette, then a hard offset drop shadow
  (that shadow is added by the cover, not drawn into the asset).
- Palette: fur `#8b6a4b`, lighter face and belly `#c9a97d`, eye patches and nose `#4a3324`,
  claws `#e8c9a0`, outline `#1a1a1a`, border `#ffffff`. Accents only from the cover palette:
  pink `#ff2d8a`, yellow `#ffe600`, cyan `#2fd3ff`, purple `#8b2be2`.
- No leaves, branches, trees or jungle. The sloth hangs from the magazine, not from nature.
- No text inside the art, ever. Words go on the cover in the cover's fonts.

## Pose 1, the H (required first)
- Canvas proportion 7 wide : 10 tall (the H slot in the masthead at 140 x 200 px, see
  `cover_v4.css`, `img.sloth`). The figure fills the canvas edge to edge.
- Both arms straight up and spread to the top corners, gripping a horizontal bar that runs the
  full width along the top edge. The bar is the crossbar of the H's neighbours and will be
  hidden behind the ribbon, so it can be plain.
- Body hangs straight down the middle, feet dangling at the bottom edge, slightly turned to
  the viewer, face at about 35% from the top.
- Silhouette must read as an H from across the room: two uprights (the arms, continuing into
  the body's edges) and a crossbar (the shoulders and head).

## Other poses (one asset each, same style, square canvas unless noted)
1. **Peeking**: head and claws over the top edge of a box, for the "wants a new look!" labels.
2. **Holding a heart** one-armed, for the hearts and the "fave" slots.
3. **Asleep**, curled, with room for a "zzz" bubble beside it, for the bottom corner seals.
4. **Reading Sloth Beat**: the sloth with a small copy of the magazine (cover left blank).
5. **Pointing** at something to its left, for the "that's him!" arrows and the pull-out tab.
6. **Avatar**: head and shoulders only, centred, for the YouTube channel icon (1:1, also works
   as a circle crop).

## Deliverables
- PNG with real alpha, at least 2000 px on the long side, one file per pose, named
  `sloth_<pose>.png` in `mockups/sloth_beat/` for now and in the channel's assets folder once
  the thumbs style exists.
- SVG as well if the generator can give one (Runway `recraft-v4.1-vector` does; the cover
  renderer is HTML, so an SVG scales to the Shorts covers for free). Vector output is the goal
  for the final set; raster is fine for the mockups.
- One contact sheet of all poses on the three cover palettes.

## How to generate (Runway, as used for Mother Moo and the v3/v4 sloth)
- Model: `seedream-5-pro` with a transparent background worked for the sticker look
  (tasks 1b5e5334-57ab-42f2-9026-c51ad30d8b1d and 4d759bc7-5165-4588-a932-9d3b4374752b are the
  wire and branch variants). For vector: `recraft-v4.1-vector` with the palette above as
  `colors`. `gpt-image-2` rejected the first prompt as invalid input; avoid clauses like
  "a badge that reads nothing".
- Prompt skeleton: *"Cartoon sloth mascot sticker in a flat 1990s teen-magazine style: [pose,
  one sentence, say where every limb is and what the figure fills], facing the viewer with a
  sleepy half-closed smile, warm brown fur, tan face with dark eye patches, thick black
  outlines, flat solid colours with no shading or gradients, no blush, no leaves, no branch, no
  text, a thick white sticker border around the whole figure, isolated on a transparent
  background."*
- Ratio 3:4 for the H pose, 1:1 for the rest. Generate 2 to 4, pick, then use the pick as a
  `referenceImages` entry for the other poses so the face stays the same sloth.
- The cloud session cannot download Runway's PNGs (network policy). Either run the
  generation from the desktop, or cut the sticker out of the tool's JPEG preview with the
  Pillow reconstruction in this session's build script (it removes the background and the
  enclosed white holes but keeps the white border).

## Acceptance
- Dropped into the H slot of `01_papa_meat_v4.html` at natural proportions with no stretch,
  the masthead still reads "SLOTH BEAT" at 320 px wide.
- The six poses next to each other look like one character drawn by one hand.
- Nothing in the set is cuter than the 1995 Bop cover is. Charm comes from the pose and the
  sleepy face, not from big eyes and blush.
