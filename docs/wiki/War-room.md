A web page for running the review and the schedule. `clipper serve` serves it together
with the background jobs (`http://localhost:8765`, `[warroom] port`); `clipper warroom
--open` runs the page on its own. It
shows what is live, where each stream is in the pipeline, the review queue and the upload
schedule. Press <kbd>?</kbd> on the page for every keyboard shortcut.

## Live shows

Click a live show's chip to watch it in a player on the page or on kick.com in a new window;
the chip also says whether the NAS is recording it.

## Review queue

It works from the keyboard:

| Key | Does |
|---|---|
| <kbd>J</kbd> / <kbd>K</kbd> | next / previous item |
| <kbd>A</kbd> | approve |
| <kbd>R</kbd> | reject |
| <kbd>C</kbd> | retake note |
| <kbd>H</kbd> | hold |
| <kbd>Space</kbd>, arrows | play, seek the video |
| <kbd>O</kbd> | pop the video out into its own window |
| <kbd>B</kbd> | Who's this? (a face on the paused video) |

The search box looks through every stream's titles, descriptions, topics and hashtags, and
what is said in each video (its captions). Status, date and show filters combine with it, and
the view is in the page's URL, so it can be bookmarked.

Tick items (shift-click for a range, or "Select shown") to approve, hold, release, pin,
reopen or send the same retake note to all of them, or to find and replace in their titles,
descriptions, hashtags, tags or playlists: the dry run lists every line that would change
before anything is written.

Every action, single or bulk, can be undone from its toast for 10 s or from "Undo…" (the
last 20). Undo goes through Kitsu too, and an item already on YouTube keeps its live text
(change it in Studio).

Titles, descriptions, hashtags and cover words are checked as you type ("hosts", unmasked
swearing, YouTube's length limits with the show prefix), and cards with a problem carry a ⚠
badge.

## Cover editor

"Edit cover" in an item's window opens it: pick the main frame (shift-click for the inset)
from the candidate frames, drag and resize the face boxes, choose a photo or teaser photo
from `[thumbnail] photos` (a cast library person by label too: "Jer · angry"), change the words. Preview renders without saving; Save re-renders
with no model calls (every cover of the stream when the teaser changes) and re-attaches it in
Kitsu.

## Faces

Who is who on screen (#54), kept in `[paths] faces/known.json` for every show.

- **A stream's groups:** "Faces" in an item's window, or "👤 faces" on a scanned stream in
  the pipeline panel. Each group of faces `clipper faces` found has its contact sheet (first
  row its clearest faces, second a spread over the stream), its name, and whether that came by
  hand or by face (with the match, 0.7+ names a group). Type a name and press Name (this stream
  only) or Name + enroll (every stream; the stream's look-alike groups are named at once).
  "✓ Right" keeps a by-face name as a hand one; Clear removes a name. Filter by named/unnamed.
- **Who's this? (B):** pause the video on a face, press B or the button, and drag a box round
  one face. clipper takes that frame at full size, finds the face in the box (small ones too)
  and says who it looks like; Keep this face adds it to the known faces and names the stream's
  look-alike groups. For a face too rare or small to make a group: a guest, a costume, a
  picture-in-picture. Not on a phone (no inline video there).
- The × on a known face forgets that person (every sample). Hand names stay.

The command line does the same: `clipper faces <stream> --name F03=Jer --enroll`,
`clipper faces <stream> --at 1:23:45 --box x,y,w,h --person Jer`, `clipper faces --forget Jer`.

## News

Headlines about the shows' people (`clipper news`, #110). **News** in the header opens it.

- **Stories** from the last 1, 3, 7 or 30 days, newest first: the headline the judge wrote, strong
  or maybe, who and which show, why it matters, and the outlets' links. "posted" means the bot
  put it in Discord (`clipper serve` finds them, the bot posts them); "in context" that it was
  added to a show's next stream.
- **Tonight's context…** shows the text the stream would get and the show it goes to (change it
  in the menu); **Add to its next stream** queues it. **Dismiss** hides a story and the bot never
  posts it.
- **Search** asks Google News now (the last 7 days) and adds the stored headlines that match.
- The header line says how often serve checks and how many sources are failing (hover for why).

## Cast

The cast library (#41): a few head-and-shoulders photos of each regular in `[thumbnail]
photos/<Person>/`, for covers and the weekly. **Cast** in the header opens it.

- **The list** is everyone with photos or an enrolled face, with how many are kept and new;
  "!" marks fewer than `[cast] min_photos`. "Someone new" adds a person.
- **A person:** other names and channels (YouTube `@name` or Kick addresses), saved with Save;
  the channels are what Find > Channels reads.
- **Find:** Commons (Wikimedia, freely licensed), Channels (their avatar and recent video
  thumbnails), Image search (Brave; needs `$BRAVE_API_KEY`), a photo's web address, or a stream
  id (that stream's sharpest faces of the person, named in its Faces window). One runs at a
  time in the background; the window shows its progress and fills in. Each photo is checked
  against the person's known face, so other people are left out, and labelled by a small model.
- **New photos** come first, blue-edged; ⚠ is the labeller's doubt (a designed thumbnail with
  text, a hidden face). Keep or Drop each; fix a label with its menu. A dropped photo is deleted
  and its source never fetched again. Kept photos are what covers use: the cover model picks
  the label that fits the headline (angry for a meltdown, laughing for a gloat), else neutral;
  a stream, Commons or channel photo before an image search one. The weekly draft does the
  same for people it pictures from a photo, and everyone with a kept photo joins the prompts'
  known figures.

Photos also arrive by themselves (the worker after each stream, serve's daily `cast auto`);
the sure ones are already kept (not from image search, an enrolled face matched well, a label
with no doubt), so
look over Kept too and drop what's wrong.

The command line: `clipper cast list|grab|find|add|keep|drop|label|set|auto` ([Cast library](Cast-library)).

## Schedule and uploads

The schedule shows the open slots of the next two weeks and the approved items with no slot.
Drag an item or a scheduled slot onto an open slot (or a day) to pin it, or onto "unpin"; the
page warns before a drop breaks the spacing rules.

Above it, the upload panel shows uploads in the last 24 h against the cap, the last automatic
run and its errors, and what goes next. Uploaded slots show views and a YouTube link.

## Modal, costs and analytics

- **Modal** (under Health): the month's Modal bill, the spend rate, GPUs up, transcriptions
  running and failed, and each archive's progress. `clipper modal` prints the same.
- **Costs** (below it): the month added up. Every model call (logged as it is made, with its
  stage and stream), Modal runs, and `[costs] monthly` fixed costs, by stage and show.
  `clipper costs` prints the same; revenue joins it once the channel is monetized.
- **Analytics** (full width, under the queue): a month of YouTube. Views, watch time and
  subscribers against the month before, views per day, where views came from (Shorts feed,
  search, ...), and the first-7-day numbers of that month's uploads by show, kind, weekday and
  hour, with the best and weakest. `clipper analytics --report` writes the same as
  `reports/analytics/<month>.md` plus a .csv of every upload.

## Layouts

On a desktop the panels can be moved: Layout > Arrange panels, then drag a panel by its title
into the top, left, middle, right or bottom zone, or into the hidden tray. Without a mouse,
focus a panel's title: ↑/↓ move it within its zone, ←/→ to the next zone, H hides it. Each
side column has a width. The browser keeps the arrangement; Layout > Save as names it in
`[paths] warroom_layouts`, where every machine can pick it.

## On a phone

It switches to one view at a time (Review, Schedule, Pipeline and Stats tabs at the bottom).
Items open full screen with the decisions at the bottom and swipe to the next one, and videos
play from a 360p copy (`lowres/` beside each preview, about a quarter of the data). It can be
added to the home screen. Away from home it goes through Tailscale: `clipper warroom --tailnet
on` ([Phone access](Phone-access)).

## For developers

The page is in `clipper/web/` (`warroom.html` and what it loads). The server is `clipper/warroom.py`: its `GET_API` and `POST_API`
tables map each `/api/...` path to a function; decisions go through `clipper/actions.py`.
