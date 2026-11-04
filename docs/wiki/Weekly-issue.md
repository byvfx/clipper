`clipper weekly` turns the week's streams (Sun-Sat) into *Lolcow Cinema
Weekly*, a ten-page tabloid for a TikTok carousel or two YouTube Posts: a cover, contents,
the cover story and two features, news in brief, the quote of the week, the week in numbers,
a marketplace of parody ads, and next week's schedule with a readers' poll. It gathers the
week (segments, chat, transcripts, faces named by `clipper faces` and the thumbnail stage's) into `weekly/<saturday>/week.json`,
has the `[weekly]` model draft the copy into `issue.json` (about $0.35), writes the three articles
again one call each with a writer prompt and the owner's own piece as the voice to match (about
$0.10 each), and renders 1080x1440 pages plus a square 1080x1080 set in `youtube/` for YouTube
Posts, which show images square (`[weekly] formats`). `--headline "..."` tells the draft what the cover story is; edit `issue.json` (copy, or
`faces` to pin a frame for someone) and `clipper weekly --render`. Colours, the cover's
geometry and the fixed copy can be changed in `weekly/style.toml` (Style, below). Posting is by hand
(`post_text.md` has the captions); `clipper weekly --discord` drops the pages and captions in a
`#weekly` Discord channel to save them on a phone.

`clipper weekly` builds the week's ten-page tabloid issue into `weekly/<saturday>/` (the
folder is `[paths] weekly`). It needs Impact, Bahnschrift, Georgia (italic and bold italic)
and Arial (bold and black), all standard on Windows; `clipper doctor` warns when any is
missing, and `[weekly] *_fonts` take other paths. The cover subject is cut out with
torchvision DeepLabV3 (the worker's torch install; the weights download once), otherwise the
cover shows a photo panel instead.

1. `clipper weekly --headline "Ethan Klein: ..."` on Saturday, after Friday's show is
   processed: gathers the week, drafts `issue.json` with `[weekly] model` and renders.
2. Look at `contact_sheet.jpg`. Fix copy in `issue.json`; pin a better frame for a face
   with `"faces": {"Name": "<stream>/segments/s16_x"}` (that stream's thumbnail frame) or
   `{"frame": "<stream>/thumbs/.../05_1.0.jpg", "face": {x, y, width, height}, "bottom": 0.73}`,
   or a portrait you drop in the issue folder: `{"photo": "hila_klein.png"}`. The cover can
   take `"background": "towers.png"` (a picture shown as a dark shadow behind the subject,
   `background_focus: [x, y]` picks the crop) and `subject_height` (pixels, default 940).
   Then `clipper weekly --render` (`--only 01,03` for a few pages). `--draft` writes a new
   draft and keeps the old file as a backup. `--write 04,05` has the writer prompt redo
   those articles (deck, columns, pull quote) from the week's summaries, matching the voice
   of `prompts/weekly_article_example.md`; a draft that would run off the page is sent back
   once to be cut. Replace the example with a piece of your own to change the voice.
3. Post by hand: TikTok takes all ten 3:4 pages as one carousel. YouTube Posts show images
   square and take five per post, so use `youtube/01-10` (the same pages on a square canvas) as
   two posts, 01-05 and 06-10, with the captions in `post_text.md`. `--format youtube` renders
   only the square set. `clipper weekly --discord` (with `--week` for an older issue) posts
   both sets and the captions to the `[discord] weekly_channel` text channel (`weekly`), to
   save on a phone; it makes the channel if the bot has Manage Channels, else make it by hand.
   A rerun posts only what is missing (`<issue>/discord.json`).

Issue numbers continue from `[weekly] first_issue` across the folders in `weekly/`.

The look is data: the canvas, the palette, the cover's geometry (teaser strip, kicker,
headline width, inset, starburst, barcode block, subject height) and the fixed copy
("LOLCOW OF THE WEEK", "READERS' POLL", the barcode digits, ...) have defaults in
`clipper/weekly/style.py`. To change any of them, put a `style.toml` next to the issues
(`weekly/style.toml`, i.e. in `[paths] weekly`) with the same tables and keys, only the ones
you change, and `clipper weekly --render`:

```toml
[palette]
red = [200, 20, 30]
[cover]
subject_height = 900          # an issue.json cover.subject_height still wins
[copy]
lolcow = "COW OF THE WEEK"
```

A colour is `[r, g, b]` or the name of a `[palette]` colour; an unknown key is refused.
