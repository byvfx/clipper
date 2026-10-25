# The Udder Truth: cast

Every character is original: an invented face, name and voice, not recognisable as any real person
(#58 satire rules). One reference image per character and one ElevenLabs voice each, reused every
week so they stay the same. Make the reference images in Runway (Gen-4 Image with References, plan
credits), then save the chosen one under the character's name in the reference set. Location plates
are part of the cast too.

Every prompt ends with "not resembling any real person". Generate four to eight options and pick one.
Stills cost little next to video; the expensive part is animating (WAN 3 Prime, about 420 credits
per 15 s piece).

## Reference set (made 2026-10-24)

The chosen references are in `cast/` (1600 px JPG tracked; the full 2752×1536 PNGs are gitignored).
Pass them to Runway by task ID (`referenceImages[].taskId`) rather than re-uploading.

| Tag | File | Runway task | Made from |
|---|---|---|---|
| `@Moo` | `mother_moo/` (hero) | fe671c52-2ab8-4da9-9ef8-f89d39c02acc | the bake-off |
| `@Rupert` | `cast/rupert.jpg` | f4b762e7-979e-454b-a086-7997827f56e1 | sheet v1 (168e3c6f, in `cast/superseded/`) + the one-hand grip |
| `@Cafe` | `cast/cafe.jpg` | e6b8aa16-9bd1-4021-96ce-b66d69ec86bd | storyboard shot 6 (e2310855) |
| `@Desk` | `cast/desk.jpg` | 44903b21-5274-4b25-aa18-cb27a7e2bd0d | storyboard shot 2 (1da93ff6) |
| barista | `cast/barista.jpg` | d5b6ce85-6221-455f-900a-9db645aea5ab | storyboard shot 7 (a4276161) |

## For the test throw

### Mother Moo, the anchor (`@Moo`)
Already designed: the hero image from the bake-off (Runway task fe671c52-…) and Harriet's voice
(read C). A salacious, campy gossip dame, grand, grave and catty. Don't redesign her; reuse the hero
image as `@Moo` and its wide desk framing.

### The studio desk (`@Desk`)
**Set redesign pending (user, 2026-10-24).** The newsroom in the storyboard reads dead: the wide's
eight-screen video wall is black (shots 2, 11 and `cast/desk.jpg`), the CRT banks behind her show
flat blue with no picture (3, 4, 10), and only shot 5's big monitor carries an image. The layout
also changes from shot to shot (a video wall in the wides, scattered CRT banks and a big side monitor
in the closer shots), so it isn't one room. Brass Eye refs pulled 2026-10-24 (`refs/brass_eye/`, 20
frames, local only); findings in `brass_eye_formats.md` under "Studio look". What to change:

- **Light:** go dark. Black void, hard key on her, pools of blue and amber behind; lose the evenly
  lit bright-blue panels.
- **One layout for every shot:** her desk centre with THE UDDER TRUTH lit on its front panel (Fusion);
  a stack of CRTs either side of her; one big screen behind her right shoulder, the one she turns to
  in shot 5. The wide shows the desk as an island in a dark space over a glowing floor grid.
- **Screens alive:** every screen gets content in Fusion (show emblem loops, colour bars, a LIVE bug,
  the story's images). Generate them as glowing blanks, so their light spill is in the plate, then
  corner-pin the content (the desk shots are locked off).
- **Neon slogan boards** near the desk, our own lines, added in Fusion.
- **Our own signature:** keep the chrome, push the blue/amber, and add Mother Moo's crimson and a
  cow-print touch so it doesn't read as their set.

Order: new desk plate, then the desk storyboard stills (2, 3, 4, 5, 10, 11), then Mother Moo's WAN
renders, which use those stills as references. Never pass the Brass Eye frames to an image model;
describe the qualities instead.

Cut from the hero image's background if it's clean. Otherwise:
`Empty 1990s British current-affairs news studio, an overcooked curved chrome desk, deep blue and red
lighting, a large CRT monitor wall behind, glossy floor, dramatic key light, wide shot, 16:9, Betacam
video look. No people, no readable text.`

### Rupert Churn, Our Man Outside (`@Rupert`)
A late-50s British regional-news correspondent sent to stand outside places for far too long. Utterly
earnest, soaked, never acknowledges the absurdity. Voice: middle-aged, slightly hoarse, BBC-local
gravitas. **The user voices him** (2026-10-24). **Recurring** (user): always outside something, so
every episode reuses `cast/rupert.jpg` and the same look (wire-rims, beige trench, maroon tie, foam
mic, tartan umbrella with a broken spoke); only the location changes. **Grip** (user): the mic and
the umbrella handle are in the same fist, the shaft leaning on his shoulder, because his other hand
is always at his earpiece. Say so in every prompt: left to itself the model floats the umbrella or
drops the mic (both happened). The sheet and the shot 6 and 8 stills show the grip.
`Character reference sheet, front and three-quarter views, of an original fictional British TV news
correspondent in his late fifties: ruddy round face, thinning grey-brown hair plastered flat by rain,
steamed-up wire-rimmed glasses, beige trench coat soaked dark at the shoulders, loosened maroon tie,
holding a foam-covered handheld microphone. Earnest, slightly harried expression. 1990s British TV
news look, soft video lighting, plain grey background. Not resembling any real person.`

### The café, Spilt Bean (`@Cafe`)
Invented; not a real shop or street. Name and window lettering go on in Fusion.
`Night exterior of a small independent coffee shop on a wet British high street, 1990s, warm light
spilling from steamed-up windows, rain streaks in the street lights, wet pavement reflections, harsh
light from a news camera, 16:9, Betacam video look. No people, no readable signage.`

### The barista (extra, no lines)
As made for shot 7 she wears a black jumper and dries cups with a blue check tea towel, not the
brown apron below; the reference keeps that so shot 7 still matches.
`An unimpressed young barista in a brown apron behind a café counter at night, drying a cup and
staring flatly out of the window, warm interior light, seen through a steamed-up rainy window, 1990s
TV news look. Not resembling any real person.`

## The bench, for later episodes (not needed for the test)

### Dr Fenella Whey, the expert
"Head of Beverage Threat Assessment, Institute for Concerned Drinking." Brass Eye's fake expert:
absolute certainty, invented terms, a prop that explains nothing (a coffee bean the size of a
football, cut in half). Appears on the studio monitor or in a sit-down interview. Clipped, posh voice.
`Original fictional British scientist in her sixties, silver bob, oversized red spectacles, tweed
jacket with a lanyard of ID badges, holding a giant cross-section model of a coffee bean, book-lined
office, 1990s documentary interview lighting, 16:9. Not resembling any real person.`

### DS Colin Rennet, the police liaison
"Online Fundraising Unit." Explains internet things he has clearly never seen, with a flip chart.
Flat Midlands monotone.
`Original fictional British detective sergeant in his forties, grey moustache, ill-fitting grey suit,
pointing at a flip chart in a dim police briefing room, 1990s TV documentary look, 16:9. Not
resembling any real person.`

### Trudy Skimm, the showbiz correspondent
Red carpets and premieres nobody has heard of; delivers the week's gossip as breathless scoops.
Bright, breathless, mid-Atlantic.
`Original fictional 1990s showbiz TV reporter in her thirties, big blow-dried hair, sequinned
jacket, holding a microphone at a red carpet with flash photography behind her, 16:9. Not
resembling any real person.`
