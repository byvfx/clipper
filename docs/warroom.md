# The war room

A web page for running the review and the schedule. The bot serves it
(`http://localhost:8765`, `[warroom] port`); `clipper warroom --open` runs it on its own. It
shows what is live, where each stream is in the pipeline, the review queue and the upload
schedule. Press `?` on the page for every keyboard shortcut.

## Live shows

Click a live show's chip to watch it in a player on the page or on kick.com in a new window;
the chip also says whether the NAS is recording it.

## Review queue

It works from the keyboard: J/K to move, A approve, R reject, C retake note, H hold,
Space/arrows for the video, O to pop it out into its own window.

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
from `[thumbnail] photos`, change the words. Preview renders without saving; Save re-renders
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
on` (SETUP.md, Phone access).

## For developers

The page is in `clipper/web/` (`warroom.html` and what it loads). The server is `clipper/warroom.py`: its `GET_API` and `POST_API`
tables map each `/api/...` path to a function; decisions go through `clipper/actions.py`.
