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
