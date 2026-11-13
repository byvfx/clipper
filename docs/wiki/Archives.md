`clipper archive` builds searchable transcripts of other channels' back catalogs (for
receipts and supercuts), kept apart from the streams folder so nothing in them is uploaded:

```bash
clipper archive add jer https://www.youtube.com/@TheQuartering   # list long-form videos, with approximate dates
clipper archive transcribe jer --limit 100 --dry-run              # hours and estimated cost
clipper archive transcribe jer --limit 100                        # WhisperX on Modal, 8 at once; log in archive/jer/transcribe.log
clipper archive transcribe jer --diarize                          # speaker turns too (~+80% GPU), named against voices/jer.json
clipper archive status
clipper modal                                                     # GPUs up, runs, spend, archive ETA
clipper costs [--month 2026-10]                                   # month's spend: models + Modal + fixed, by stage/show/stream
clipper analytics --report [--month 2026-10] [--refresh]          # YouTube month: views, sources, 7-day numbers by show/kind/day/hour
clipper archive check jer                                         # which source links still play
clipper archive links jer                                         # the dead and restricted ones
clipper archive index jer                                         # keyword index, index.sqlite (free)
clipper archive search jer '"costco dog"' --by oldest             # hits: date, title, link at the moment
clipper archive search jer 'huck' --years                         # how often, per year
clipper archive ask jer "Jer saying he'd never take sponsor money"  # Haiku writes the searches, picks the fits (~1c)
```

`search` takes FTS5 syntax (`a b` both words, `"a b"` the phrase, `OR`, `NOT`, `pre*`) with
stemming, over ~30 s windows; each hit links to the moment, a few seconds early. It matches
words, not meaning: "the warehouse store" won't find "Costco" (embeddings are #53). `ask`
gets most of the way there cheaply: Haiku turns the question into up to 8 searches (synonyms,
names, how Whisper misspells them), then reads the ~80 pooled windows and returns the ones
that fit, strong or maybe, each with a quote, a reason and the link.

`check` downloads nothing and costs nothing: YouTube's oEmbed endpoint and Odysee's resolve say
whether each item is alive, restricted (private, embedding off, blocked) or dead, kept in
`links.json` with when it changed, and reruns report what died since. A dead link with a
transcript is a receipt whose source is gone; `transcribe` skips dead links.
`clipper archive check jer --items yt-<id> ...` checks just the items you are about to link to.

It needs the Modal backend set up ([Transcribe on Modal](Transcribe-on-Modal)). YouTube audio is
downloaded on this machine and uploaded; Odysee is meant to be fetched in the cloud (not
working yet, issue #44).

## HEI Network (On Cinema)

heinetwork.tv, On Cinema's own site, has the whole show for members: every season, the Oscar
specials, Decker, the podcast and the extras (352 videos, 102 hours on 2026-11-11). `archive add`
lists it with a member's cookies; the videos are plain HLS from Mux (no DRM). Most need no
cookies to fetch, but about a quarter (seasons 12-14, some specials and extras) are signed links
whose token runs out in about two days: `video` and `transcribe` fetch such an item's page again
for a fresh one, so keep the cookies file signed in while they run. Tokens never appear in
messages or error files.

```bash
clipper archive add oncinema https://www.heinetwork.tv           # ~350 pages at 1 s apart, ~10 min
clipper archive add oncinema https://www.youtube.com/@adultswim --match 'on cinema|decker'
clipper archive add oncinema https://www.youtube.com/@HEINetwork https://www.youtube.com/@timheidecker
clipper archive video oncinema --dry-run                         # hours and roughly how many GB
clipper archive video oncinema                                   # source.mp4 + captions per item
clipper archive transcribe oncinema --diarize --dry-run          # Tim and Gregg apart; the cost first
clipper archive posts oncinema <sheet link>                      # every post, as posts.json
```

The cookies: sign in at heinetwork.tv with *Remember me* ticked, export the site's cookies with a
browser extension (Netscape cookies.txt or Cookie-Editor's JSON) to `[paths] hei_cookies`
(`secrets/hei_cookies.txt`). `clipper doctor` warns when the file holds no live sign-in, and a
listing that meets a members-only page stops and says to export them again. Rerun `add` each week
for the new episodes: items already in the catalog are kept as they are, but gain a date or a
length the first listing lacked (a stream's length probe can fail; one that turns out shorter
than `min_duration_s` is dropped unless it's already transcribed). A catalog whose items all have dates is kept newest first across its sources, so
`video` and `transcribe` with `--limit` or `--oldest` follow the air date; YouTube's dates are
approximate ("3 years ago").

Items are `hei-<section>-<slug>` (`hei-oc-s16-hamnet-and-ella-mccay`, `hei-oscar-...`), with the
series, the title and the air date the site shows. Never upload them: the archive is for
research and for cutting short clips, never whole episodes.

### Naming the speakers

`transcribe --diarize` splits each item into SPEAKER_00, SPEAKER_01, ... and names them against
the subject's own enrolled voices (`[paths] voices`/`<subject>.json`, never a show's). To teach it a
voice, name a speaker in one item where they talk a lot and keep the voice:

```bash
clipper speakers <archive>/oncinema/hei-oc-s16-hamnet-and-ella-mccay              # who talks, their longest lines
clipper speakers <archive>/oncinema/hei-oc-s16-hamnet-and-ella-mccay --name SPEAKER_00=Tim --enroll
clipper archive speakers oncinema                                             # every item named again; hours per name
```

`clipper speakers` takes an archive item's folder as well as a stream; it uses the item's subject
voices, so enrolling there never touches a show's. Two or three samples from different years
help a voice that changed (`[speakers] max_refs` keeps the last few).

### Keeping videos

`clipper archive video <subject>` keeps each YouTube or HEI item's best video as `source.mp4` in
its folder, with the caption tracks as `captions.<lang>.vtt` ([archive] `video_format`,
`video_parallel`). A download is written under another name and renamed when complete, so an
interrupted one is simply fetched again; a failure leaves `video_error.txt` and the log is
`<subject>/video.log`. Once an HEI item's video is kept, `transcribe` takes the audio from it
instead of fetching the stream again. No model cost; the space is about 1.4 GB an hour at 1080p.

### Posts

`clipper archive posts <subject> <Google Sheet link>` saves a public sheet of posts as
`posts.csv` (as exported) and `posts.json` (one row per post, keyed by the sheet's header). For
On Cinema that is the fans' complete post archive linked from the On Cinema Wiki's X page: every
tweet and Bluesky post by Tim, Gregg and the show since 2011, with the episodes marked between
them. Tim's X account has been private since it was hacked in December 2024, so the X API can't
read it; the sheet is the record. Rerun to refresh.

## Age-restricted videos and YouTube cookies

`clipper archive transcribe` downloads anonymously; a video
YouTube won't serve without sign-in ("Sign in to confirm your age") is retried with `[paths]
youtube_cookies` (`secrets/youtube_cookies.txt`) when that file exists. Export it as Netscape
cookies.txt or as JSON (Cookie-Editor's default; converted on the fly). Each signed-in run hands
yt-dlp a private copy, because yt-dlp writes its cookies back to the file when it exits with a browser extension (e.g. "Get cookies.txt LOCALLY") from a private
window signed in to an adult Google account (not the upload channel's, if you can avoid it),
then close the window so the session isn't rotated. Rerun `transcribe` to pick up the failures.
A JSON export left in the same folder under the extension's own name (`www.youtube.com_<date>.json`)
is used without renaming when it is newer than `youtube_cookies.txt`.
Pre-shows use the same file more freely: when YouTube's bot check ("Sign in to confirm you're
not a bot") stops a pre-show grab, `clipper preshow` retries it signed in. The archive never
does that, so a bulk run doesn't put the account on a blocked IP. Browsers rotate these
cookies within days; when a signed-in retry still hits the bot check, the bot DMs ([notify]
kind `failure`, once per video per `cooldown_hours`) to export fresh ones. The block is on the
IP: a PO-token provider (bgutil-ytdlp-pot-provider, tried 2026-10-31) doesn't get past it.

### Signed in through a browser profile

An exported file goes stale when the browser it came from rotates the session (four exports in
eight days, 2026-10-27 to 11-04). Set `[youtube] cookies_from_browser` (in `clipper.local.toml`)
and every signed-in retry above reads a browser profile's cookies live instead of the file, so a
rotation never leaves them behind. Use Firefox: Chrome and Edge on Windows encrypt their cookies
so that yt-dlp can't read them. Set it up once, as the Windows user that runs `clipper serve`:

1. Install Firefox, then make a profile just for clipper:
   `& "C:\Program Files\Mozilla Firefox\firefox.exe" -CreateProfile "clipper $env:APPDATA\Mozilla\Firefox\Profiles\clipper"`
2. Open it with `& "C:\Program Files\Mozilla Firefox\firefox.exe" -P clipper -no-remote`, sign in
   to YouTube (a spare Google account, not the upload channel's), play any video and close it.
3. `cookies_from_browser = "firefox:clipper"` under `[youtube]` (yt-dlp's
   `BROWSER[:PROFILE]`; a profile folder's full path works too, written out: yt-dlp doesn't expand
   `~` or `%APPDATA%` in it).

`clipper doctor` reads the profile's cookie database (no network; youtube.com's cookies only, since
google.com's carry the same names) and fails when it has no YouTube
sign-in. Don't browse with the profile. If YouTube signs it out, open it, sign in again and close
it; the pre-show DM says the same.

### Keeping the NAS hub's cookies fresh

The [NAS hub](NAS-hub)'s containers have no browser, so their signed-in retries copy the file
`secrets/youtube_cookies.txt`, which goes stale the same way an export does. The laptop copies
the Firefox profile above into it: `clipper cookies export` writes the profile's live youtube.com
cookies to `[paths] youtube_cookies` (Netscape, through a temp file; `--browser` and `--to`, the
latter relative to the config file's folder, override the two settings). It refuses, leaving the
file alone, when the profile isn't signed in or the share can't be reached. Only the export writes
that file (yt-dlp gets a private copy), so it holds exactly what the profile held at the last run.
The task doesn't refresh the sign-in itself: it copies the profile, so when YouTube signs the
profile out, sign in again there. Close the profile before an export: yt-dlp reads only the saved
cookie database, not what an open Firefox hasn't written yet.
Install the task once, on the laptop (the profile and `cookies_from_browser = "firefox:clipper"`
set up as above):

    deploy\cookies\cookies-task.ps1 -Install    # "clipper-cookies": at logon and every 4 hours

`-Status` shows the last run and `logs\cookies.log`, `-Run` exports now, `-Uninstall` removes it.
Where there's no browser profile set, `clipper doctor` checks the file instead: it fails when the
file has no live YouTube sign-in and warns when the last export is over 24 hours old.

This makes the NAS depend on the laptop: while the laptop is off or logged out the file isn't
refreshed, and once YouTube rotates the session, pre-show grabs (and story and archive signed-in
retries) on the NAS fail the bot check and DM until the laptop is back. Running fully on the NAS
with the laptop off would need another source of cookies, such as a browser in a container.
