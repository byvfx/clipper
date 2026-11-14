# Lolcow Cinema check-in: 2026-10

Written 2026-11-01 by the monthly check-in task (#42) from `2026-10.md` / `2026-10.csv` (refreshed
2026-11-01 17:02 UTC; YouTube Analytics runs about two days behind) and `clipper costs --month 2026-10`.
Nothing was changed: these are suggestions for the user to accept or skip.

## The month

- **First clipper month.** Uploads ran 10-19 to 10-31 (13 days, 171 videos: 40 Shorts, 117 segments,
  9 compilations, 5 clips). There is no September report or earlier check-in, so there is nothing to
  compare against yet; November's check-in will be the first month-over-month.
- **Views 65,891 · watch time 1,177.7 h · subscribers +190.** Lifetime views on October uploads add
  up to 69.5k, so practically all of the channel's views came from this pipeline.
- **Where views came from:** Subscriptions 53% (35.1k), Shorts feed 32% (21.2k), YouTube search 7%
  (4.8k), everything else under 2% each. (HANDOFF on 10-28 had the Shorts feed at 80% of 12.7k views;
  the H3 segments shifted the mix toward subscription traffic in the last week.)
- **The H3 story was the month.** 51 videos about Ethan Klein / Hila / H3 (including the Just Trish
  one-off) took **49.7k views, 72% of the total**. The whale-fall handling on 10-23 (priority, more
  Shorts, reshuffle) is what put them out while hot: H3 Shorts median 1,198 views vs 494 for the
  other Shorts; H3 segments median 77 vs 18.
- **Shorts carry reach, segments carry watch time.** In the first 7 days, 13 Shorts got 7,049 views
  (median 475, 54% watched) vs 49 segments 1,595 views (median 9, 15% watched), but segments still
  gave more watch minutes (2,205 vs 1,756) and as many new subs (4 vs 3). Of 84 segments at least
  four days old, 37 have under 20 views and 55 under 50.
- **Kino vs Channel 404:** Kino Shorts median 1,112 (n=18) vs Don's 484 (n=5); Kino segments median
  28 (n=57) vs Don's 11.5 (n=26), both counting items published by 10-28 so they've had 4+ days.
- **Cost: $210.88**, but $183.58 of that is the one-off Jer archive transcription on Modal L4. The
  running pipeline cost about $27 (Kino $16.36, Channel 404 $9.93, mostly Opus for `segments`
  $13.85), roughly $0.40 per thousand views.

## Suggestions

### 1. Move two segment slots a day to Shorts
**Change:** in the schedule slots, go from 6 Shorts + 8 segments to 8 Shorts + 6 segments (for example
turn the 16:00 and 19:00 segment slots into Shorts; keep `max_per_24h` 15), and raise the per-stream
`stream_budget` short count to match (8 → 10 per 8 h) so there are Shorts to fill them.
**Numbers:** Shorts 7-day median 475 vs segments 9 (13 vs 49 videos); Shorts lifetime 28.7k views from
40 videos vs segments 33.2k from 117, and 32% of all channel views came from the Shorts feed.
Segments aren't dropped entirely because they bring more watch time (2,205 vs 1,756 7-day minutes).
**Confidence:** fairly high for "Shorts out-reach segments"; the size of the gap is inflated by H3.
**Check in November:** Shorts-feed views and total views per day vs October's last two weeks, and
whether total watch hours hold up with fewer segments.

### 2. Make the "whale-fall" playbook a standing per-stream override
**Change:** keep a ready-made `streams/<id>/config.toml` snippet for a big-story night: `[schedule]
priority = true`, `[scoring] max_picks` 20+, a higher Shorts budget, and one compilation for the
story. Start it the same night the story breaks, as on 10-23.
**Numbers:** H3 = 72% of October's views from 51 of 171 videos. H3 Shorts median 1,198 vs 494; the
H3 compilation got 2,029 views while the other eight compilations got 7 to 161. Top segment
"H3 Crew Turns on Ethan Klein" has 6,030 views.
**Confidence:** high that big stories dominate; one story is one data point on what exact budget is right.
**Check:** next big story's share of the month's views and how fast its first Short went out after
the stream ended.

### 3. Compilations only when a topic is big, not every night
**Change:** drop the daily 21:00 compilation slot to "only when the topic has N+ approved segments
or is flagged as a big story" (or set `stream_budget` compilation = 0 by default and turn it on per
stream), and give the 21:00 slot to a Short.
**Numbers:** 9 compilations: median lifetime 19 views (7-day median 6, 8% watched). Only the H3 one
(2,029) and DSP Pokemon box (161) cleared 100. Compilation joins also needed a fix this month.
**Confidence:** medium (9 videos, one outlier).
**Check:** that the compilations still made go over 100 views, and the 21:00 Short against the
other Short slots.

### 4. Lighter on Channel 404 segments, keep Don's Shorts
**Change:** cut Don's per-stream segment budget (about 7 → 4 per 8 h) and keep or raise his Shorts.
Leave Kino as is.
**Numbers:** Channel 404 segments median 11.5 (n=26) vs Kino 28 (n=57); 43 Channel 404 segments
took 2,196 views in total. Don's Shorts do fine when the topic is fresh: "dad's hat" 1,333, H3
Instagram 1,069, Wisconsin vote 951. His late-October Huck/coffee Shorts are at 27-185, but they are
1-3 days old, so don't judge them yet.
**Confidence:** medium for segments, low for anything about topics (fewer than 3 per bucket).
**Check:** Channel 404 Shorts medians next month, and whether the slots freed go to Kino items.

### 5. Test Shorts titles without the "KINO CASINO |" / "CHANNEL 404 NEWS |" prefix
**Change:** an experiment only: a new meta prompt version (or a Shorts-only title rule) that leads
with the hook and names the person, like the X text since 11-01, keeping the show in the
description and playlist. Run it on half the Shorts for two weeks if the user wants a clean test.
**Numbers:** none yet. Every October Short had the prefix, so the report can't show its effect. The
best titles all start the real hook late ("KINO CASINO | PPP explains why DSP is...") and the Shorts
feed shows short titles cut off.
**Confidence:** low; this is a hunch worth testing, not a finding.
**Check:** median 7-day views and % watched of prefixed vs hook-first Shorts in November (need 10+ each).

## Not worth acting on yet

- **Posting hour / weekday:** the hour table mostly reflects which kind went out at that hour (10:00,
  15:00 and 20:00 were Shorts slots), and weekdays have 1-17 videos with H3 landing on Sat-Sun. Revisit
  once slots are mixed and there's a month without one huge story.
- **Kino Jer-topic Shorts** median 326 (n=4) vs other Kino Shorts 1,165 (n=3): too few to say.
- **Pre-shows:** 6 published, 3 to 1,213 views; the two with H3 news did best, which is the same story effect.

## Last month's suggestions

None: this is the first check-in.

## Decisions (2026-11-02, the user)

- **1. Taken.** Segment slots 16:00 and 19:00 became Shorts; the 21:00 compilation slot too (3.):
  Shorts 09/11/13/15/16/18/19/20/21, segments 08/10/12/14/17/22, `max_per_24h` still 15.
  `stream_budget` short 8 -> 10 per 8 h.
- **2. Taken.** `docs/big-story.toml` (PR #120): copy to `streams/<id>/config.toml` the night a story breaks.
- **3. Taken.** `stream_budget` compilation = 0 (PR #120 makes 0 mean none unless pinned; it used to
  round up to 1). Compilations move to 23:00, since Shorts fill slots first.
- **4. Taken.** `[shows.c404.schedule] stream_budget` segment 6 per 8 h (about 4 for Don's ~5.5 h), Shorts kept.
- **5. Skipped** for now: titles keep the show prefix; revisit in November.
