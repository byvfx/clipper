# Lolcow Cinema Weekly: mockups (issue #35)

The weekly YouTube Post, picked by the user on 2026-10-22: all eight slides, one issue for the
whole channel (every show), a reader poll every week. These are mockups: the photos are crops of
the old cover mockups, so some chins carry a soft blur where the old reaction inset was painted
out, and the copy and numbers are placeholders. Real issues take faces from the videos' frames.

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
