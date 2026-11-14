The clip art library keeps pictures that are free to use: old engravings and prints, drawings,
emoji and icons. They're for [covers](Covers), the [weekly issue](Weekly-issue), story cuts and
explainers. It is the still-picture partner of the [b-roll library](B-roll). Each picture keeps
where it came from, its credit line and its licence.

## Sources

- `commons`: Wikimedia Commons. Drawings (SVG files: clip art, icons, diagrams) come first.
  Only when a find still wants more does it search pictures (photos and scans), which are
  mostly photos and often need a credit; `[clipart] commons_pictures = false` keeps it to
  drawings. Add "engraving" to a search for old prints. An SVG, or a picture wider than
  `[clipart] max_px`, comes as Commons' own render at about that width (Commons rounds it to
  one of its standard sizes; SVGs come as PNG, with their transparent background). No key.
- `met`: The Metropolitan Museum of Art's open access: prints, trade cards, ads and drawings
  the Met marks public domain, which it releases as CC0. No key.
- `cleveland`: the Cleveland Museum of Art's open access, only works it marks CC0, at 3400
  pixels. No key.
- `iconify`: emoji and icons through Iconify (api.iconify.design). By default the sets are
  `[clipart] iconify_sets`: Fluent Emoji (Microsoft's 3D-look emoji, and their flat version),
  Noto Emoji (Google), Tabler and Phosphor (arrows, shapes, symbols). Files are SVG. Each set's
  licence comes from its own record. A logo or brand set is never used, whatever its licence
  says, because logos are trademarks. With `iconify_sets = []` every set whose licence is in
  `[clipart] licences` is searched. Each SVG is sized `[clipart] max_px` tall, so a renderer
  doesn't draw it 16 pixels high. No key.
- `pixabay`: Pixabay's vectors (`[clipart] pixabay_type`, or `illustration` or `all`) under
  the Pixabay Content License. It needs a free key: sign up at pixabay.com, copy your key from
  pixabay.com/api/docs into `secrets/pixabay_secret.txt` (`[paths] pixabay_key`), or set
  `PIXABAY_API_KEY` ([b-roll](B-roll) uses the same key for Pixabay's video). The key is
  never written to `clipart.json` or printed. Without a key,
  Pixabay is skipped. Pictures come at most 1280 pixels wide, unless Pixabay grants your key
  full access. `pixabay_transparent = true` keeps only pictures with a transparent background.

The Art Institute of Chicago's image server refuses scripts, so it isn't a source. Openclipart
needs an account's key for its API and may come later. Until then, Openclipart's art is on
Pixabay under the user OpenClipart-Vectors.

## Commands

- `clipper clipart find "cow engraving"`: searches every source in `[clipart] sources` and adds
  up to `[clipart] per_source` new pictures from each, tagged with the search. `--source met`
  searches one source; `-n 10` takes more.
- `clipper clipart list [--tag cow] [--source iconify] [--status new|kept] [--json]`: the
  library, newest first, with each picture's size, credit and licence. `--json` adds each
  file's path.
- `clipper clipart add <address|set:icon|file|folder> [--tag X]`: one picture. It can be:
  - a Commons `File:` page, a metmuseum.org/art/collection/search, clevelandart.org/art or
    pixabay.com page;
  - an Iconify name such as `fluent-emoji:cow-face` (from icon-sets.iconify.design, whose
    addresses work too);
  - your own picture file, or a folder of them with all its subfolders. That needs
    `--credit` and usually `--licence`.

  A downloaded pack goes in whole, for example the CC0 packs from kenney.nl (speech bubbles,
  emotes):
  `clipper clipart add Downloads/kenney_emotes-pack --credit "Kenney (kenney.nl)" --licence CC0 --tag emote`.
- `clipper clipart keep|drop <id> ...`: a dropped picture's file is deleted, and the next find
  doesn't bring its source back. Adding it by hand brings it back.

The files and `clipart.json` are in `[paths] clipart`. A file is `<id>.<ext>`: `.png`, `.jpg`,
`.gif`, `.webp` or `.svg`. A museum original over `[clipart] max_mb` falls back to the
museum's web-size copy.

## Rights

A picture is kept only if all three of these hold, and otherwise it's skipped with the reason
printed:

- Its licence is in `[clipart] licences`. By default that is `pd` (public domain), `cc0`,
  `permissive` (MIT, Apache, ISC, BSD and OFL) and `pixabay`.
- Nothing in its title, description or credit names an outside owner (©, "copyright", "all
  rights reserved", Getty ...).
- Commons doesn't mark it trademarked or as a person's likeness.

Noncommercial, no-derivatives and GPL licences are never kept.

- **Credits.** None of the default licences needs a credit when you use a picture. The credit
  line is kept anyway; it's good manners in a description. Adding `cc-by` to `licences` keeps
  attribution-licensed art too, such as Twemoji, game-icons.net and many Commons files. Then
  every video or post that uses one must credit its author. `cc-by-sa` (OpenMoji, a lot of
  Commons) also makes what you build from it share-alike, a cover included. Leave it out.
- **People and brands.** Pixabay's licence bans using a picture that shows a brand or a logo
  commercially, and showing a recognisable person in a bad light. Use Pixabay for objects and
  drawings, never as the face of a cover.
- **Old characters.** A public-domain comic or ad can show a character someone still owns as a
  trademark. Don't put a recognisable mascot on a cover.
- **Permissive licences** (Fluent Emoji MIT, Noto Apache): the licence notice belongs beside
  copies you hand on. The library keeps the set's name and licence in `clipart.json`, and a
  video needs no credit.

## Choosing by hand

Some sources are good but have no API, or don't want scripts; for these, download what you
pick and `clipper clipart add` the file:

- [Old Book Illustrations](https://www.oldbookillustrations.com), engravings on white,
  ideal for cut-outs.
- The Public Domain Review's image archive.
- The Library of Congress's "Free to use and reuse" sets (old ads, WPA posters). Its API
  allows only 20 requests a minute.
- LottieFiles, for free animations.
- Kenney's CC0 packs.

Avoid "free PNG" sites (PNGWing, StickPNG, CleanPNG) and GIF sites (Giphy): they hold no rights
to what they serve.

## Settings

`[clipart]` in `clipper/defaults.toml`: `sources`, `per_source`, `licences`, `commons_pictures`, `max_px`,
`max_mb`, `iconify_sets`, `pixabay_type`, `pixabay_transparent`, `timeout_s`. `clipper doctor`
checks the source and licence names, warns when Pixabay has no key, and counts the library.
