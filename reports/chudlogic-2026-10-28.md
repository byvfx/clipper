# ChudLogic: YouTube termination and Kick channel (2026-10-28)

## Summary

- **YouTube: terminated around 2026-10-15/16.** He announced it on stream (Kick title on 2026-10-16:
  "MY YOUTUBE CHANNEL HAS BEEN TERMINATED"). `youtube.com/@chudlogic` now returns 404 (a control
  channel returns 200), and an old video of his returns "Video unavailable". **No public reason
  found.** No YouTube statement, news story or Reddit thread came up. The 2026-10-18 stream title
  mentions "FAITHFUL SHERIFF YOUTUBE COPYRIGHT ABUSE" and 2026-10-23 is "ROBBED BY YOUTUBE", so
  copyright strikes are a plausible guess, but that is unconfirmed. Three copyright strikes in 90
  days is YouTube's standard path to termination.
- **Kick: yes, he streams there daily.** `kick.com/chudlogic`, verified affiliate, ~10.9k
  followers, subs enabled. Not a new move: the Kick channel was created 2023-08-17 and
  StreamRecorder has tracked 334 streams (~1,900 h) since 2025-10-31.

## Who he is

British drama-commentary streamer and YouTuber (X: @IrrationalChad; Kick bio: "Z-List Streamer,
Drama Analyser & Politics Enjoyer"). Started on Twitch, grew on YouTube with drama coverage and
Destiny-sphere debate panels (MrGirl a frequent collaborator). Cut ties with Destiny in mid-2023.

## Kick channel facts (Kick API, pulled today)

| Field | Value |
|---|---|
| Slug / channel id | `chudlogic` / 16999603 (user 17789526) |
| Followers | 10,904 |
| Status | verified, affiliate, subs on, VODs on, not banned |
| Socials on profile | X `irrationalchad`, YouTube `chudlogic` (dead), Discord `chudlogic` |
| Categories | Just Chatting (one Gears of War: E-Day stream) |
| VODs listed | 27 (2026-09-29 to 2026-10-27), 204.6 h total |

Note `kick.com/chud_logic` is a different, empty account (2 followers); the real one is `chudlogic`.

**Schedule:** starts around 08:00-10:00 UTC (morning UK), usually ~10 h, near daily. Shorter
(~5 h) "DAY OFF KICK STREAM"s on some days. Multiple VODs on one day are usually restarts.

**Content (recent titles):** a rolling drama docket: Ethan Klein / Hila divorce, Pokimane and
Atrioc, Russell Brand trial, Kai Cenat allegations, Andrew Tate, Moist Critical, DarkViperAu, bodycam
streams, UK politics (Farage). **Overlap with our lolcows:** he covers **OnlyUseMeBlade** often
(2026-10-27 "INSANE ONLY USE ME BLADE CONTENT", 2026-10-17, 2026-10-06) and **DSP** (2026-10-06).

## Ban history

- **YouTube:** channel terminated, ~2026-10-16 (above). Earlier, DarkViperAu had one of his
  reaction videos taken down via DMCA.
- **Twitch partner, six suspensions** per StreamerBans: 2022-05-04 (3 d), 2023-02-19 (1 d),
  2023-04-22 (3 d), 2023-11-04 (14 d), 2025-08-07 (1 d), 2025-09-06 (7 d). One was reported by
  Sportskeeda as "aiding account suspension evasion" (he said he thought it was for watching a banned
  streamer's content). Twitch is no longer his main platform.

## Controversies on record (allegations, attributed)

- **2021-01-13:** accused of sending flirtatious messages to someone later revealed to be a minor;
  Destiny reviewed it the same day and called the grooming claim unconvincing. (Destiny wiki)
- **2023 / 2026-05:** his opt-in "Spicy Chat" Discord channel has been accused of harassing and
  doxxing ElderDrazi; Stardust confronted him in 2023, ElderDrazi posted screenshots in May 2026.
  ChudLogic says it was an attempt to protect ElderDrazi's information. (Destiny wiki; unverified)

## If we ever clip him

- Recording works with what we have: `kick.py` handles `chudlogic` like any channel (VOD list,
  HLS, chat replay). It would be a new `[shows.<key>]` with its own Kitsu production.
- **Get permission first**, as with Don (404). Ask him directly; he has no YouTube presence now,
  so clips may matter more to him, but his termination also makes YouTube more sensitive to
  re-uploads of his content. Uploading his streams wholesale or as a mirror would read as
  circumvention on his behalf; short transformative clips with permission are the safer shape.
- Daily ~10 h streams = high transcription load (about 70 h/week on Modal).
- His Blade/DSP segments could feed existing lolcow content without a full show.

## Unknowns

- The termination reason, and whether he has appealed or started a new channel.
- Live viewer averages (the VOD `viewer_count` field here is 19-868, likely VOD views, not live
  concurrents; Streams Charts blocked our fetch).

## Sources

- Kick API: `kick.com/api/v2/channels/chudlogic` and `/videos` (2026-10-28)
- [ChudLogic on Kick](https://kick.com/chudlogic/videos)
- [StreamRecorder: ChudLogic](https://streamrecorder.io/kick/chudlogic)
- [StreamerBans: ChudLogic](https://streamerbans.com/user/chudlogic)
- [Sportskeeda: third Twitch ban](https://www.sportskeeda.com/esports/news-why-controversial-streamer-chudlogic-banned-third-time-twitch)
- [Destiny wiki: ChudLogic](https://wiki.destiny.gg/view/ChudLogic)
- [DarkViperAu Strikes Down ChudLogic Reaction Commentary](https://www.youtube.com/watch?v=Wx-ITakw5k8)
- [Wikipedia: YouTube copyright strike](https://en.wikipedia.org/wiki/YouTube_copyright_strike)
