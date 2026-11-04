The cast library (#41) keeps a few head-and-shoulders photos of each regular for
[covers](Covers) and the [weekly issue](Weekly-issue): from their streams' named
[faces](Faces), Wikimedia Commons, their own channels and image search, each checked against
their known face and labelled (neutral, laughing, angry...). The [war room](War-room)'s
**Cast** window keeps or drops them.

## Setup

It needs the `faces` extra (`uv sync --extra faces`) and `[thumbnail] photos` set
([Covers](Covers), Photos). `clipper serve` runs the daily find and the Cast window, so it
needs the `faces` extra too (`clipper setup` adds it for the serve role; `clipper doctor --role
serve` checks).

Image search is Brave's Search API (about $5 per 1,000 searches): make a key at
api-dashboard.search.brave.com and put it in `secrets/brave_secret.txt` (`[paths] brave_key`)
or the Windows user environment variable `BRAVE_API_KEY`; without it the other sources still
work. The same key serves the web and Reddit searches of the [search swarm](Search-swarm).

## Commands

The library is `photos/<Person>/` folders of several photos each, kept by `clipper cast`
beside the single files (a person's best kept photo wins over their `<Person>.jpg`).

- `clipper cast grab <stream>`: each named person's sharpest faces in a stream
- `clipper cast set Jer --alias "The Quartering" --channel https://www.youtube.com/@TheQuartering`
- `clipper cast find Jer`: Wikimedia Commons, the channels' avatars and video thumbnails, image search
- `clipper cast add Jer <file|url>`
- `clipper cast keep|drop|label ...` and `clipper cast list`

Every photo is checked for the person's face first (only theirs if they're enrolled in
`faces/known.json`), cropped 4:5 and labelled by a Haiku call (`[cast] label_model`, about
$0.0002 a photo; `--no-label` or `""` skips it). It never takes photo agencies' pictures
(`[cast] blocked_domains`), but image search photos have no known licence, so prefer stream and
Commons ones for covers (covers do, given the choice). `clipper doctor` flags anyone with fewer
than `[cast] min_photos` (3) kept.

## It fills itself

The worker's `cast` stage (after `faces` in `[worker] stages`) grabs each new stream's named
faces for anyone with fewer than `[cast] target` (6) photos, and serve runs `clipper cast auto`
every `[cast] auto_hours` (24), a find from every source for the same people (someone it found
nothing for waits `[cast] retry_days`). A photo is kept without asking when it comes from a
stream, Commons or a channel (`[cast] auto_keep_sources`; never image search, whose licence is
unknown), the person is enrolled, their face matches at `[cast] auto_keep` (0.65) or better and
the label has no doubt; the rest wait as New in the Cast window. Costs: a Haiku label per photo
and a Brave search per person tried, cents a day.

## How covers use it

Covers (`[thumbnail] enquirer_prompt_version` v6) and the weekly (`[weekly] prompt_version` v3)
are told each person's labels and pick the photo that fits the story; the war room's cover
editor offers them too ("Jer · angry"). Everyone with a kept photo is added to `[scoring]
known_figures` (with their aliases) unless `[cast] known_figures = false`.
