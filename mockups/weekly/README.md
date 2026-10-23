# Lolcow Cinema Weekly: mockups (issue #35)

The weekly YouTube Post, picked by the user on 2026-10-22: all eight slides, one issue for the
whole channel (every show), a reader poll every week. These are mockups: the photos are crops of
the old cover mockups, so some chins carry a soft blur where the old reaction inset was painted
out, and the copy and numbers are placeholders. Real issues take faces from the videos' frames.

**tiktok/** (2026-10-22): the whole issue again at 1080x1440 for a TikTok photo carousel, with
real data from Oct 18-24 (kick-133095550, -133260144, -133423456, -133597196 and two pre-shows).
Inside pages 02-08 keep the eight-slide plan above in a modern style with the cover's tabloid
cues (red kicker bars, yellow square bullets, yellow highlighter, page refs), type Impact /
Bahnschrift / Georgia italic. Quotes are verbatim from the transcripts (Whisper's "When is
being called a grifter..." kept as heard); the transcripts have no speaker labels, so Kino
lines are credited to the show. Numbers are computed (hours from meta.json, chat from
chat.jsonl, segment counts by topic). Page 06 (user, round 2: "get more funny"): a screen-time
chart of each regular's segment hours (Jer red, and the "more than the rest put together" line
is asserted against the data), then four joke cards: F-bomb rate, the top chat emote, "coffee"
and "Huck" counts from the transcripts. The top row gets a crown and "LOLCOW OF THE WEEK"
(user liked it). Round 3: the issue is 10 pages, with two real articles written from the
segment summaries: 04 "DSP's Worst Week" (Cameo, banana song, SF6 quit, $2 Kick night, USPS)
and 05 "Bearing Breaks His Silence" (the deleted Tism podcast and the takedown story). Pull
quotes are verbatim from the transcripts. Bearing's swinger/wife claims about private people
were left out on purpose; the hot tub clip is only mentioned. All pages keep the cover's 3:4. Luther Morgan's avatar is the user's photo in [thumbnail]
photos (`luther morgan.jpg`); Mel Mac, Jerry Banfield and Ethan Ralph show initials (no face
frame this week). contact_sheet.jpg shows all ten.

**tiktok/01_cover.jpg** (after the user chose a full-tabloid cover for TikTok):
1080x1440 (3:4), the 2020s Enquirer template from research_notes/enquirer-covers.md, built
from kick-133597196's real frames: top strip of three teasers (DSP, Blade, Ethan Ralph's
tacos), small red LOLCOW over a chrome CINEMA, Jer cut out with torchvision DeepLabV3
(person class) so his cap sits in front of the masthead, red kicker, Impact headline,
red bullet bars, Bearing's bear in a circle inset beside them, a starburst on the masthead's
C and a barcode block. User's notes (round 2): one kicker size and one headline size across
the top strip (as big as all three allow), kicker underlines stay, nothing over the cover
face (starburst, date stamp and inset moved off it), no arrow line, no platform name on the
barcode (the cover is reused beyond TikTok). Fonts: Impact, Bahnschrift Bold Condensed, Arial Black (Windows). Copy is
mockup copy written from the stream's segment titles.

| Slide | What it shows | Data it needs |
|---|---|---|
| 01 cover | Masthead, cover photo + story, "also this week" (4 avatars), cover line | Top story of the week; next four |
| 02 contents | Seven numbered stories: avatar, title, one line, day | The week's top videos, ranked |
| 03 feature | Cover story: portrait photo, headline, subtitle, two short columns | Top video's transcript summary |
| 04 brief | Six equal cards: avatar, category, headline, two lines; one is a parody ad | Smaller moments of the week |
| 05 quote | Quote of the week, who and when, two runners-up | Quotes said on stream |
| 06 numbers | Six stat cards | Hours live, clips, peak chat, running jokes |
| 07 market | One big parody ad and four small ones, all riffing on the week's bits | Running jokes from the streams |
| 08 back | Next week's schedule, reader poll ("Who had the worst week?") | `streamers.ics`; three people from the issue |

## Design system

- Square 1080 x 1080, 56 px margins, 6 columns, 24 px gutters.
- Type: Anton (headlines, caps), Instrument Serif italic (subtitles, quotes), Inter 400/600/800
  (labels as tracked caps, body, captions). All three are free Google Fonts; they would need
  installing on the GPU machine and a check in `clipper doctor`. Body text is 22-24 px or larger
  so it reads on a phone.
- Colour: paper `#F6F3ED`, ink `#121212`, muted `#706C64`, card `#ECE8E0`, red `#E42428` for
  labels, rules and page numbers only, yellow `#FFDE00` only as a highlighter behind one
  headline word (and as the accent on dark slides).
- Shapes: 20 px radius on every photo and card; people in lists are round avatars.
- Chrome: every inside slide has the same top bar (LOLCOW in red + CINEMA WEEKLY, section, issue)
  and footer (page n/8, week, "Swipe →").
- Ads are cards in the same grid, inverted or labelled "Ad · Parody"; every ad is marked as a parody.
- Faces: crop on the detected face so the whole head (cap to chin) fits; when the frame is wider
  than the source allows, show the whole head on a blurred copy of the photo instead of cutting it.
