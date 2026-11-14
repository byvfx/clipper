The b-roll library (#229) keeps short clips that are free to use, for [story cuts](Story-dossiers),
explainers and the [weekly issue](Weekly-issue): footage made by the US government has no
copyright, and the Internet Archive and Wikimedia Commons state each item's licence. Each clip
keeps where it came from, its credit line and its licence. Still pictures (engravings,
drawings, emoji, icons) are in the [clip art library](Clip-art).

## Sources

- `nasa`: the NASA Image and Video Library (images.nasa.gov): space, Earth from orbit, storms,
  archive film. No key.
- `dvids`: the Defense Visual Information Distribution Service (dvidshub.net), the armed forces'
  own video: aircraft, ships, training, disaster relief. Only its B-Roll category is searched
  (footage without narration; `[broll] dvids_category`, `""` for all of it). It needs a free key:
  register at dvidshub.net and copy the API keys from your account into
  `secrets/dvids_secret.txt` (`[paths] dvids_key`), the public key on the first line and the
  private one on the second, or set `DVIDS_API_KEY` to the public key. Only the public key is
  used, and it is never written to `broll.json` or printed. Without a key, DVIDS is skipped.
- `prelinger`: the Internet Archive's Prelinger films: ads, industrial and educational films
  from the 1930s to the 1970s. No key.
- `commons`: Wikimedia Commons video, a lot of it government footage already. No key.

The National Archives needs a key from NARA and comes later (#229).

## Commands

- `clipper broll find "hurricane from orbit"`: searches every source in `[broll] sources` and
  adds up to `[broll] per_source` new clips from each, tagged with the search. `--source nasa`
  searches one source.
- `clipper broll list [--tag storm] [--status new|kept|long] [--json]`: the library, newest
  first. `--json` adds each file's path, which you can import into Resolve.
- `clipper broll add <address|id|file> [--from 0:01:20 --to 0:01:45] [--tag X]`: one clip, from
  an images.nasa.gov/details, dvidshub.net/video, archive.org/details or Commons `File:` page.
  With a library id it takes a part of that clip. A video file you downloaded yourself needs
  `--credit "U.S. Navy video by ..."`.
- `clipper broll keep|drop <id> ...`: a dropped clip's file is deleted, and the next find
  doesn't bring its source back.

Times read as everywhere else: `0:01:20` (H:MM:SS), `1m20s`, or seconds. `1:20` means one
hour and twenty minutes.

Clips up to `[broll] max_s` (3 minutes) are downloaded whole and re-encoded to at most
`[broll] max_height`. A longer one, like most Prelinger films, is listed as `long` with no
file; `add <id> --from --to` takes the part you want. Raise `max_s` and a long clip that now
fits is downloaded whole: by the next find that returns it, or by `add <id>` with no times.
The files and `broll.json` are in `[paths] broll`.

## Rights

A clip is kept only when its licence is in `[broll] licences` (`pd`, public domain, and
`cc0` by default) and nothing in its title, description or credit names an outside owner
(©, "copyright", "all rights reserved", Getty, AP, Reuters ...). Anything else is skipped,
and the reason is printed. Adding `cc-by` keeps attribution-licensed clips too, but then every
video that uses one must credit its author. A noncommercial or no-derivatives clause (NC, ND)
is never kept, whatever else the licence says, even "public domain, non-commercial use only".

- **Credits.** Put the clip's credit line in the description of any video that uses it.
- **Sound.** Clips have no sound unless `[broll] audio = true`. NASA videos often carry
  licensed music; with the sound on, a NASA clip whose credits mention music is skipped.
- **No endorsement.** Nothing may suggest an agency endorses us: no insignia or recognizable
  service member on a cover or as the subject of a title.

State and foreign government footage (ESA, the EU, UK Parliament) has its own licences and
isn't a source. Nor is C-SPAN, whose own camera footage is copyrighted.

## Settings

`[broll]` in `clipper/defaults.toml`: `sources`, `dvids_category`, `per_source`, `max_s`,
`max_height`, `licences`, `audio`, `codec` (empty uses `[cut] clip_codec`), `crf`, `timeout_s`.
`clipper doctor` checks the source and licence names, warns when DVIDS has no key, and counts
the library.
