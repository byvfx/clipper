The `thumbs` stage ([Pipeline](Pipeline)) makes tabloid-style covers: 16:9 for segments,
compilations and clips, and a 9:16 one for each Short. The model picks the frame, the face and
the words; [Faces](Faces) gives it each named person's best frames and the found face box, and
the [cast library](Cast-library) gives it photos of people talked about but not on screen.
The [war room](War-room)'s cover editor changes any of it by hand.

## Layout

Every position, size, color and fixed word of the tabloid covers is data, `LAYOUT` in
`clipper/thumbs/enquirer.py`; a show changes any of it in `layouts/<show>/enquirer.toml`
(`[paths] layouts`, beside its Shorts screenshots), e.g. `[colors] green = "#0b3d91"` or
`[cover.inset] size = [200, 180]`, and `clipper thumbs <stream> --compose-only` redraws with
no model calls. An unknown key is an error. Leave the file out for the default covers.

`[thumbnail] teasers = false` (per show) drops the cover's three side boxes so the photo fills
the left: one big moment, no other stories to plug. `[preshow] teasers = false` does the same
for every [pre-show](Pre-shows) and one-off cover only.

## Photos

Tabloid covers can use a portrait of someone the show talks about who is never on screen.
Make a folder every machine can reach (e.g. `photos` beside `streams` on the NAS), add one
head-and-shoulders crop per person named `<Person>.jpg` (the whole picture is treated as the
face, so crop out watermarks and logos), and set `[thumbnail] photos` to it (a relative
folder is beside `clipper.toml`, like `[paths]`). The model may then
use one for a cover's face or teaser box; `clipper thumbs <stream> --items s04 --teaser-photo
"Hasan Piker"` sets one by hand. The [cast library](Cast-library) keeps several labelled photos
per person in the same folder and fills itself.

Fonts: covers and Short captions use Impact and Bahnschrift, both standard on Windows (on
Linux, `ttf-mscorefonts-installer` and `fonts-roboto-unhinted`).

## Fan-magazine covers

`[thumbnail] style = "fanmag"` is a second cover look, built for the Sloth Beat channel (#46): a
late-80s teen fan magazine instead of the tabloid. It has the same pick as the tabloid (the
same thumbnails.json fields, so the war room's cover editor, `--photo` and `--teaser-photo` work
on it), with its own prompt (`clipper/prompts/thumbnail_fanmag_<v>.md`, `fanmag_prompt_version`)
in a fan's voice: adoring, never an exposé. The cover is the cut-out face on the right with a
hand-cut paper edge, the coverline and the episode's other videos as bullet coverlines in the
left column (so text never sits on a face), halftone panels, and the striped masthead with the
mascot hanging in place of one letter. The design and its references are in
`studio/mockups/sloth_beat/` (v7). Set it per show:

```toml
[shows.<key>.thumbnail]
style = "fanmag"
masthead = "SLOTH BEAT"
mascot = "sloth"          # the bundled sloth, a PNG path, or "" for none
mascot_letter = 4         # the letter it hangs in place of (0-based; the H)
```

Each cover is an HTML page, `thumbs/<item>/fanmag/cover.html` in the stream, screenshotted by a
Chromium-based browser in headless mode: Microsoft Edge or Google Chrome is found by itself,
otherwise set `[thumbnail] browser` to its executable. Open the page in a browser to try a change
in its CSS, then make it in `clipper/thumbs/fanmag.py`; `--compose-only` redraws without model
calls. The fonts and the mascot ship in `clipper/assets/fanmag/`. With the `gpu` extra
(torchvision) the cut-outs follow the person's outline; without it they are the face crop's
rectangle, still with the paper edge. `clipper doctor` checks the browser, the assets and
torchvision. Shorts get no 9:16 cover in this style yet, and the NAS hub image has no browser,
so a fanmag show's covers are drawn on a machine that has one: without one, `clipper thumbs`
stops before any pick is paid for. Cut-outs are kept in `thumbs/_fanmag/` in the stream, so a
redraw doesn't redo them.
