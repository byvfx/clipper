# National Enquirer covers: what makes them work (2026-10-22)

Research for the Lolcow Cinema Weekly cover (#35). The user wants the magazine cover to go full
tabloid ("we can get crazy"); YouTube thumbnails stay as they are for now. About 20 covers
looked at, 1970-2025; sources at the end. Notes describe devices, not the covers' copy.

## The four eras

**1970s, newsprint.** Black ink on grey paper, halftone photos, one spot colour at most.
Small "NATIONAL" over a heavy geometric-sans "ENQUIRER" (Futura Extra Bold-like), a price
("35c") and "LARGEST CIRCULATION OF ANY PAPER IN AMERICA" under it. One teaser box beside the
masthead. A two-line all-caps headline across the full width, a kicker line above it in a
black bar ("EXCLUSIVE - The strange ... "), and a story index down the left: short lines,
each ending in an italic *page 30*, separated by three stars. Photos carry a diagonal caption
across a corner and a reversed black bar under them. Reads as a newspaper, not a magazine.

**1990s, colour tabloid.** Flat red "ENQUIRER", still the geometric sans, on white; "THE NEW"
roundel in 1999. Coloured teaser boxes above the masthead (contest: "WIN $100,000", "Book
Bonus"), one big face cut off by the page edges, yellow headline in a rounder bold sans with
a drop shadow, white subheads, yellow **"Only in The ENQUIRER"** tags between sub-stories,
yellow asterisk bullets. Our current Enquirer thumbnails are this era.

**2000s, glossy.** Same red masthead, now partly covered by heads. Split covers: two stories
side by side with a jagged **torn-paper rip** between them. Mixed-case headlines appear. Red
"ENQUIRER EXCLUSIVE" tags, small pink/red bullet bars, a giveaway box ("WIN FREE GROCERIES
FOR A YEAR!") with a cut-out person in a corner.

**2020s, the current template (most consistent, best model for us).** Every cover is the
same grid:

1. **Top strip**: three teaser panels, each a solid colour (blue, green, purple, red; centre
   one framed in thick yellow and dropping over the masthead). Each: a small red or yellow
   kicker ("Exclusive!", "Nightmare!"), a 3-5 line condensed headline in white or yellow,
   a photo of the person, sometimes a name strip or THEN/NOW tags.
2. **Masthead**: small red italic "NATIONAL" over a huge white/chrome bevelled "ENQUIRER".
   The cover subject's head always overlaps it (masthead behind the cut-out head).
3. **Kicker bar**: a red rectangle, white bold type, 1-2 lines, one word picked out in yellow
   or caps ("Chilling conspiracy EXPLODES!", "It's NOT what you think!"). On special issues
   it's gold with stars.
4. **Headline**: 3-4 lines of ultra-condensed caps (Impact/Compacta/Anton class), yellow with
   a dark outline and shadow, left-aligned, set over the photo; ends in "!". White instead of
   yellow when the background is warm.
5. **Insets**: rectangular photos with a white border and a black name strip under them
   ("NAME / would-be role"), or a column of three framed insets with names on one side, or a
   **circle** inset. A yellow hand-drawn **curved arrow** points from text to inset.
6. **Bullets**: 1-4 red (sometimes blue) bars along the bottom, each with a **yellow square**
   bullet, white bold type, one word in yellow.
7. **Starburst**: a red circle with a white rim, rotated, white caps ("HISTORIC KEEPSAKE
   ISSUE!", "MORE EXCLUSIVES THAN ANY OTHER MAGAZINE!").
8. **Barcode block**: bottom right, issue date above, price line, barcode, publisher strip
   below. Sometimes turned 90 degrees up the right edge.
9. **Variants**: a right-hand yellow "PLUS:" column of 4-5 stories, each a red kicker, black
   condensed headline and *Page 8*; a thin gold frame round the whole cover on double issues;
   a photo caption stamp ("JULY 13, 2024") on the main image; RIP years under a face.

## Copy formula

Kicker (who says / how shocking) -> headline (subject + verb + "!") -> 1-3 bullets that
promise specifics. Kicker words: Revealed, Exclusive, Shocking, Untold, Finally, Busted,
Chilling, It's over. Headline verbs: exposed, snubbed, frozen out, crash & burn, cover-up,
betrayed. One word per line of copy gets the yellow highlight. Every story points to a page.

## For Lolcow Cinema Weekly

- **Cover = the 2020s template**, all of it: top strip of three teasers (the week's #2-#4
  stories), LOLCOW CINEMA masthead with the subject's head in front of it, red kicker,
  yellow 3-4 line headline, a circle or framed inset of the second person with a name strip
  and a yellow arrow, 2-3 red bullet bars, a starburst, and a barcode block (issue date,
  price, barcode, "LOLCOW CINEMA" where A360 MEDIA goes). Bullet and teaser lines can end in
  "Page 3" style refs that match the carousel slide numbers.
- **Inside pages**: keep the clean editorial mockups (mockups/weekly), borrowing the red
  kicker bars, yellow square bullets and the 1970s story index with *page n* refs for the
  contents slide.
- **Special issues**: gold frame + gold starred kicker (anniversaries, a big week), or a
  1970s newsprint "throwback issue" (black and white, story index, diagonal caption).
- **Tech**: the head-over-masthead look needs a person cut-out (background removal, e.g.
  rembg or MediaPipe segmentation) on the GPU machine. The chrome masthead can be a fixed
  pre-rendered asset. Everything else is layout and type; no generative AI needed.
- Fonts: an Impact/Anton-class condensed for headlines (Anton is already in the mockup spec),
  a heavy grotesque for kicker/bullet bars, the masthead as an image.

## Sources looked at

Current covers (magazine-services CDN, via Bing image search):
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/32221e0e-a8ac-44b2-818e-c6c6f981f8ea.jpg
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/7ee2a066-0469-4e8f-964d-08512953fbfe.jpg
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/522d1c1a-b0a5-414d-98c8-56380f474a0a.jpg
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/ff591381-a93d-44b8-8483-377554197ec7.jpg
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/a3bb17ca-ded6-4d90-8af0-925cf7cd25f0.jpg
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/65a877a8-63d4-4a51-80eb-4084bad71747.jpg
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/9f19efd9-e721-43bd-a0e2-1b14b61527be.jpg (gold frame, PLUS column)
- https://imageworx-cdn.magazine-services.net/s3/useast.contrix.prod.images/covers/all/03f1703a-52ae-433f-9ab8-b1a532748e83.jpg

Older (listings and archives):
- 2008 split cover: https://i.ebayimg.com/images/g/0rYAAOSwBRNbyOd1/s-l1600.jpg
- 1999: https://i.ebayimg.com/images/g/iSsAAOSw0qFjj8CQ/s-l1600.jpg
- 1996: https://i.ebayimg.com/images/g/L94AAOSwLwBjjWEx/s-l1600.jpg
- 1995: https://www.originalmagazines.com/cdn/shop/files/photo-jun-16-1-59-23-am.jpg
- 1990: https://i.pinimg.com/736x/b9/b8/37/b9b83787bd4b5a6875dd5b2965b27b12.jpg
- 1979: https://c8.alamy.com/comp/3CB002N/1979-january-14-national-enquirer-cover-adam-eve-were-astronauts-3CB002N.jpg
- 1977: https://i.ebayimg.com/images/g/dyYAAOSwuYxj-l8A/s-l1600.jpg
- 1970: https://pictures.abebooks.com/inventory/16660558497.jpg
