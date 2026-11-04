"""Config loading: built-in defaults < clipper.toml < clipper.local.toml < CLIPPER_<SECTION>_<KEY> env vars.

The file is found via --config, then $CLIPPER_CONFIG, then ./clipper.toml.
Relative paths in [paths] resolve against the config file's directory.

clipper.local.toml (beside clipper.toml, or $CLIPPER_LOCAL_CONFIG) holds this machine's own
values (streams path, codec, transcribe backend; `clipper setup` writes it), so clipper.toml
can stay identical on every machine. Like env vars, it wins over show and stream overrides.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from . import jsonstate
from .errors import ClipperError

DEFAULTS: dict[str, dict[str, Any]] = {
    "paths": {
        "streams": "streams",
        "prompts": "prompts",
        "schedule": "schedule.json",
        "youtube_client_secret": "secrets/youtube_client_secret.json",  # OAuth Desktop client
        "youtube_token": "secrets/youtube_token.json",                  # written by --sign-in
        "youtube_quota": "logs/youtube_quota.json",  # Data API units spent per Pacific day
        "discord_token": "secrets/discord_token.txt",  # bot token, if not in [discord] token_env
        "elevenlabs_key": "secrets/elevenlabs_api_key.txt",  # if not in [narrate] key_env
        "sync_key": "secrets/sync_api_key.txt",  # sync.so, if not in [perform] sync_key_env
        "tiktok_client": "secrets/tiktok_client.json",  # {"client_key", "client_secret"}
        "tiktok_token": "secrets/tiktok_token.json",    # written by `clipper tiktok --sign-in`
        "x_client": "secrets/x_client.json",            # {"client_id"} (+ "client_secret" for a Web App)
        "x_token": "secrets/x_token.json",              # written by `clipper x --sign-in`
        "warroom_token": "secrets/warroom_token.txt",   # war room token, if not [warroom] token
        "brave_key": "secrets/brave_secret.txt",        # Brave Search API key, if not $BRAVE_API_KEY
        "rumble": "rumble",       # `clipper rumble` ready-to-upload folders
        "bot_log": "logs/bot.log",  # `clipper bot` log (rotated); "" = console only
        "serve_log": "logs/serve.log",  # `clipper serve` log (rotated); "" = console only
        "layouts": "layouts",     # reference screenshots: <show>/<layout>.jpg (`clipper shorts --save-example`)
        "archive": "archive",     # `clipper archive` subjects: <subject>/catalog.json + one dir per item
        "youtube_cookies": "secrets/youtube_cookies.txt",  # optional, Netscape cookies.txt: `archive` age-restricted items, pre-shows on a bot check
        "modal_runs": "logs/modal_runs.jsonl",  # every Modal call's start and end (`clipper modal`)
        "costs": "logs/costs.jsonl",  # every model call's cost (`clipper costs`, the war room)
        "warroom_undo": "logs/warroom_undo.json",  # the war room's last 20 actions, for Undo
        "warroom_layouts": "logs/warroom_layouts.json",  # the war room's saved panel layouts
        "news": "news",           # `clipper news`: news.sqlite (headlines, stories), context.json
        "weekly": "weekly",       # `clipper weekly` issues: <saturday>/01_cover.jpg ... issue.json
        "voices": "voices",       # enrolled voices per show: <show>.json (`clipper speakers --enroll`)
        "faces": "faces",         # known faces, every show: known.json (`clipper faces --enroll`) + models/
        "streamers": "research_notes/streamers.json",  # people and their channels (`clipper story`)
    },
    "modal": {
        # `clipper modal` and the war room's Modal panel (#49): spend is Modal's own billing.
        "monthly_budget_usd": 0,  # your ceiling for a month's metered cost, shown against it; 0 = none
        "alert_every_usd": 25,    # the bot DMs (kind "spend") each time the month passes a multiple; 0 = off
    },
    "narrate": {
        # `clipper narrate`: a performance re-voiced by the ElevenLabs Voice Changer (#57).
        "key_env": "ELEVENLABS_API_KEY",  # env var with the API key (never in clipper.toml)
        "voice_id": "",           # the character's voice (clipper narrate voices); "" = not set up
        "model_id": "eleven_multilingual_sts_v2",  # voice changer (`narrate voice`)
        "tts_model": "eleven_v3",  # script reads (`narrate speak`); takes [sighs]-style tags
        "tts_max_chars": 3000,    # per read; split longer scripts at scene breaks
        "tts_settings": {},       # voice_settings for reads, e.g. {stability = 0.5, style = 0, speed = 1.05};
                                  # {} = the voice's own; eleven_v3 stability is 0, 0.5 or 1
        "output_format": "pcm_48000",  # lossless 48 kHz, saved as .wav (Creator plan and up; plans
                                       # without it fall back to mp3_44100_128); pcm_44100 needs Pro
        "remove_background_noise": True,   # phone takes in a room
        "max_s": 300,             # longest take per call; split longer ones at pauses
    },
    "perform": {
        # `clipper perform`: a lip-sync take to a read, cut for the character model (#58).
        "max_piece_s": 15,        # WAN 3 Prime takes at most 15 s of reference video
        "min_piece_s": 3,
        "tail_s": 1.5,            # keep this much of the take after the read ends (the final hold)
        "silence_db": -35,        # pause detection in the read
        "silence_min_s": 0.25,
        "fps": 30,
        "crf": 21,                # pieces stay under the 30 MB single upload
        # lip-sync pass (`clipper perform lipsync`): redraw the mouth to match the read
        "lipsync_engine": "latentsync",  # latentsync (Modal, no account) | sync (sync.so, needs a key)
        "latentsync_app": "clipper-lipsync",  # Modal app (`clipper perform lipsync --deploy`)
        "latentsync_steps": 20,   # 20-50: higher is better and slower
        "latentsync_guidance": 1.5,  # 1.0-3.0: higher syncs harder, may jitter
        "sync_model": "lipsync-2",   # sync.so: lipsync-2 | lipsync-2-pro
        "sync_key_env": "SYNC_API_KEY",  # else paths.sync_key
    },
    "archive": {
        "gpu": "L4",              # Modal GPU for bulk runs: cheapest per hour of audio (#11)
        "parallel": 8,            # items at once (the Modal app allows 10 containers)
        "min_duration_s": 60,     # skip shorter items (Shorts)
        "youtube_format": "bestaudio[abr<=96]/bestaudio",  # ~40 MB per hour of audio
        "stop_after_failures": 20,  # in a row (e.g. YouTube's bot check); 0 = never stop
        "diarize": False,         # speaker turns per item too (#52): diarization.json, words named against
                                  # [paths] voices/<subject>.json; ~+80% GPU time (jer all: ~$70 more on L4)
        "link_parallel": 4,       # `archive check`: oEmbed requests at once
        "link_recheck_days": 7,   # `archive check` skips links checked more recently
        "index_window_s": 30,     # `archive index`: seconds of transcript per searchable window
        # `archive ask`: a cheap model writes the searches, then picks the windows that fit
        "ask_model": "claude-haiku-5-5",
        "ask_effort": "low",
        "ask_prompt_version": "v1",  # prompts/archive_expand_<v>.md, archive_judge_<v>.md
        "ask_max_queries": 8,
        "ask_per_query": 25,      # best-ranked windows kept per query
        "ask_candidates": 80,     # pooled windows the model reads (~25k tokens)
        "kick_format": "worst",   # a Kick VOD (`clipper story`): HLS variants are muxed, so the smallest one
    },
    "receipts": {
        # `clipper receipts`: a stream's stories about an archive subject -> old clips of him
        # saying it (#48), cut into one video with source and date on screen, uploaded private.
        "subject": "jer",         # the archive subject (paths.archive/<subject>)
        "topics": ["The Quartering"],  # segments with one of these topics...
        "figures": ["Jeremy Hambly", "Jer", "The Quartering"],  # ...or one of these figures
        # These two go with the subject: the video title's start ("" = "<subject> receipts")
        # and its YouTube tags (the show's name is added).
        "title": "The Quartering Receipts",
        "tags": ["The Quartering", "Jeremy Hambly", "receipts"],
        "model": "claude-sonnet-5-5",  # reads the segments, writes the stories and questions
        "effort": "medium",
        "prompt_version": "v2",   # prompts/receipts_stories_<v>.md
        # `receipts script`: Mother Moo's voice-over (open, a line per story card, a lead-in per
        # clip, close), voiced by `cut` with [narrate] voice_id and tts_settings (ElevenLabs).
        "script_model": "claude-opus-5-5",
        "script_effort": "medium",
        "script_prompt_version": "v2",  # prompts/receipts_script_<v>.md (v2: no "host")
        "vo_pad_s": 0.6,          # card time after a read ends
        "lufs": -16,              # every card and clip normalized to this (ffmpeg loudnorm)
        "max_stories": 6,
        "per_story": 3,           # receipts kept (use = true) per story; the rest stay listed
        "maybe": False,           # also use "maybe" fits (they need a look in context)
        "lead_s": 6,              # clip starts this much before the quote (at a sentence start)
        "tail_s": 5,              # and ends this much after it
        "max_clip_s": 60,
        "card_s": 3.5,            # story card before each story's receipts
        # yt-dlp, per clip: HLS first, where a section is a few segments; a section of a DASH
        # format had ffmpeg read from YouTube for 10+ minutes for a 20 s clip (2026-10-25)
        "format": "bv*[height<=1080][protocol^=m3u8]+ba[protocol^=m3u8]/b[height<=1080][protocol^=m3u8]"
                  "/bv*[height<=1080]+ba/b",
        "codec": "libx264",       # like [cut] codec; every part is re-encoded to one format
        "crf": 20,
        "bug": "",                # corner text on every clip; "" = [scoring] channel_name
        "fonts": ["C:/Windows/Fonts/impact.ttf", "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
                  "/Library/Fonts/Impact.ttf", "/System/Library/Fonts/Supplemental/Impact.ttf"],
        "text_fonts": ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
                       "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    },
    "investigate": {
        # `clipper investigate <subject>` (#108): read an archive subject's own videos for
        # checkable facts, find threads across them in code, write leads, check coverage.
        # Research files under paths.archive/<subject>/investigate/; nothing is uploaded or posted.
        "facts_model": "claude-haiku-5-5",  # reads every chunk of every item (the bulk of the cost)
        "facts_effort": "low",
        "leads_model": "claude-sonnet-5-5",  # reads one candidate thread at a time, writes the lead
        "leads_effort": "medium",
        "check_model": "claude-haiku-5-5",  # coverage labels and --confirm verdicts
        "check_effort": "low",
        "prompt_version": "v1",   # prompts/investigate_{facts,leads,check,confirm}_<v>.md
        "parallel": 8,            # model calls at once
        "chunk_s": 1200,          # transcript seconds per facts call
        "since_days": 0,          # facts: items from the last N days when --since isn't given; 0 = all
        "max_leads": 10,          # leads kept per `leads` run (--max)
        "max_candidates": 30,     # candidate threads the leads model reads (one call each)
        "min_facts_per_thread": 3,  # a thread needs this many facts from different videos
        "trend_weeks": 4,         # trend: these recent weeks against the 26 before them
        "dropped_weeks": 8,       # dropped thread: silent this long after being talked about
        "window_days": 60,        # convergence: personal facts from several videos within this
        "check_results": 10,      # check: his own video titles that match, at most
        "check_findings": 40,     # check: the swarm's findings the check model reads, best first
        # check: [swarm] settings for a lead's search (#109), small: a lead is one question
        "swarm": {"first_tasks": 8, "max_tasks": 20, "max_rounds": 2, "budget_usd": 0.1, "max_wall_s": 300},
        "budget_usd": 10.0,       # a run stops before it would spend more and says what's left
        "discord": False,         # leads, check and confirm post to discord_channel when done
        "discord_channel": "investigations",  # by name, made if missing (a forum works too): a thread per leads run
    },
    "swarm": {
        # The search swarm (#109, clipper/swarm.py) that `story` (#84) and `investigate` (#108)
        # use: a planner writes the first searches, workers run them in parallel and follow up
        # on what they find. Finds and ranks only; nothing is downloaded.
        "planner_model": "claude-sonnet-5-5",  # one call: the first tasks
        "planner_effort": "low",
        "worker_model": "claude-haiku-5-5",    # one call per task: findings and follow-ups
        "worker_effort": "low",
        "prompt_version": "v1",   # prompts/swarm_{plan,worker}_<v>.md
        "workers": 8,             # tasks at once (the sources' rates bound it too)
        "first_tasks": 16,        # the planner's tasks, at most
        "max_rounds": 3,          # follow-up rounds per run
        "max_tasks": 60,          # tasks per run
        "followups_per_task": 3,
        "results_per_task": 20,   # search results / channel videos a worker reads
        "channel_depth": 200,     # a channel task lists this many, keeps the run's dates, ranks by the goal's words
        "budget_usd": 1.0,        # per run; it stops before a call would go over
        "max_wall_s": 600,        # per run; no new task starts after this
        "rates": {"youtube": 0.5, "kick": 2.0, "odysee": 2.0, "reddit": 0.5, "web": 1.0},  # requests/s per source
    },
    "story": {
        # `clipper story` (#84), phase 1: a brief -> the swarm -> the videos worth transcribing ->
        # the moments that tell it -> a dossier. Files in paths.archive/story-<slug>/.
        "people": {},             # {name = {youtube = "@x", kick = "y", odysee = "@z"}}, beside streamers.json
        "days": 7,                # the window when --since isn't given
        "max_videos": 12,         # triage: videos to transcribe, at most (our own streams don't count)
        "max_hours": 6.0,         # ...and hours of audio (a video of unknown length counts as one)
        "min_duration_s": 30,     # shorter videos are skipped
        "gpu": "L4",              # Modal GPU for the transcripts
        "parallel": 2,            # containers at once: each cold start costs ~10 min of GPU, a story has few items
        "youtube_bot_retry": True,  # YouTube's bot check: retry signed in ([paths] youtube_cookies), as pre-shows do (user 11-03)
        "triage_model": "claude-haiku-5-5",
        "triage_effort": "low",
        "find_model": "claude-haiku-5-5",  # writes the searches, then picks the moments
        "find_effort": "low",
        "max_queries": 10,
        "per_query": 20,          # best-ranked windows kept per search and subject
        "candidates": 120,        # pooled windows the find model reads
        "dossier_model": "claude-sonnet-5-5",
        "dossier_effort": "medium",
        "dossier_sources": 80,    # swarm findings the dossier may cite, best first
        "prompt_version": "v1",   # prompts/story_{triage,expand,moments,dossier}_<v>.md
        "budget_usd": 2.0,        # triage, find and dossier together (the swarm has its own)
        "swarm": {},              # [swarm] settings for a story's searches, e.g. {max_tasks = 40}
        # Phase 2: `story plan` (the edit, from the moments) and `story cut` (the video, then meta,
        # thumbs and the Kitsu card), in paths.streams/story-<slug>/. Rendering is [receipts]'s:
        # fonts, bug, codec, crf, lufs, and its YouTube format.
        "show": "",               # the show a story goes out under; "" = `clipper --show <key>`, or the only show
        "edit_model": "claude-sonnet-5-5",  # orders the moments into the edit, writes the title
        "edit_effort": "medium",
        "max_minutes": 12.0,      # the edit stops here; later parts stay in story.json with use = false
        "lead_s": 6,              # a part starts this much before its quote (at a sentence start)
        "tail_s": 20,             # and ends this much after it: the rest of the thought
        "max_clip_s": 90,
        "kick_format": "best[height<=1080]/best",  # a Kick VOD's section (yt-dlp)
        "odysee_format": "best[height<=1080]/best",
        "publish": "asap",        # asap: out as soon as it is approved and uploaded | slots
        "playlist": "",           # YouTube playlist for every story ({show}); "" = the model's pick
        "credit": "Sources, in the order they appear:",  # the description's last block, a line per part after it
    },
    "rumble": {
        # No upload API, so `clipper rumble` prepares folders for the web form.
        "upload_url": "https://rumble.com/upload.php",
        "kinds": ["segment", "compilation", "clip"],  # which schedule.json kinds to prepare
        "license": "Rumble Only", # non-exclusive; Video Management claims the YouTube copy
        "category": "Entertainment",
        "visibility": "public",   # public | unlisted
        "schedule": "youtube",    # youtube: same slot (+ offset_minutes) | now
        "offset_minutes": 0,
        "footer": "",             # appended to descriptions; {youtube_url} once on YouTube
        "max_tags": 0,            # 0 = all of the draft's tags
        "max_title_chars": 100,
        "max_description_chars": 5000,
        "link": "hardlink",       # hardlink (no extra disk, copy across drives) | copy
        "kitsu": True,            # Rumble / Rumble URL columns
    },
    "weekly": {
        # `clipper weekly`: the week's streams -> a ten-page tabloid issue (1080x1440 pages
        # for a TikTok carousel or two YouTube Posts). The model drafts issue.json from
        # prompts/weekly_<v>.md; edit it and `clipper weekly --render`.
        "model": "claude-opus-5-5",
        "effort": "high",
        "prompt_version": "v3",   # v3: people only talked about from the cast library, photo by
                                  # label (#41); v2: quotes credited to who said them (#52)
        "write_articles": True,   # after a draft, write the three articles again, one call each...
        "article_prompt_version": "v2",  # ...with prompts/weekly_article_<v>.md...
        "article_example": "weekly_article_example.md",  # ...and this piece (in prompts/) as the voice to match
        "quote_match": 0.75,      # a hook this close to word for word in the Short is credited to its speaker
        "face_frames": 3,         # each person named in a stream's faces.json: their best faces, frames for the pages; 0 = off
        "first_issue": 1,         # number of the first issue this install makes
        "formats": ["tiktok", "youtube"],  # tiktok: 1080x1440 pages; youtube: the same pages square (youtube/)
        "masthead": ["LOLCOW", "CINEMA"],  # red word, chrome word (then "WEEKLY" inside)
        "byline": "BY THE LOLCOW DESK",
        "price": "$1.39 US",      # on the cover's barcode block
        # Words counted in the week's transcripts for the numbers page (the model picks from them).
        "words": ["grift", "coffee", "cameo", "sub", "clip", "lawsuit", "divorce"],
        # Fonts, first that exists: the cover headline (Impact), a condensed family with
        # named styles (Bahnschrift), a serif italic and bold italic (Georgia), a black and
        # a bold sans for the masthead and barcode.
        "headline_fonts": ["C:/Windows/Fonts/impact.ttf", "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
                           "/Library/Fonts/Impact.ttf", "/System/Library/Fonts/Supplemental/Impact.ttf"],
        "condensed_fonts": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/roboto/unhinted/RobotoCondensed-Bold.ttf",
                            "/usr/share/fonts/truetype/roboto/hinted/RobotoCondensed-Bold.ttf", "/Library/Fonts/RobotoCondensed-Bold.ttf"],
        "serif_italic_fonts": ["C:/Windows/Fonts/georgiai.ttf", "/usr/share/fonts/truetype/msttcorefonts/Georgia_Italic.ttf",
                               "/Library/Fonts/Georgia Italic.ttf", "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"],
        "serif_bold_italic_fonts": ["C:/Windows/Fonts/georgiaz.ttf", "/usr/share/fonts/truetype/msttcorefonts/Georgia_Bold_Italic.ttf",
                                    "/Library/Fonts/Georgia Bold Italic.ttf", "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf"],
        "black_fonts": ["C:/Windows/Fonts/ariblk.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Black.ttf",
                        "/Library/Fonts/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf"],
        "bold_fonts": ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
                       "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    },
    "tiktok": {
        # `clipper tiktok`: Shorts to the TikTok app's drafts; you post from the phone.
        "kinds": ["short"],       # schedule.json kinds allowed
        "max_per_24h": 3,         # drafts sent in any 24 h; TikTok allows at most 5 pending
        "auto_send_minutes": 0,   # `clipper bot` runs `clipper tiktok --auto` this often; 0 = only by hand
        "hashtags": [],           # added to every caption, after the Short's own
        "max_caption_chars": 2200,
        "redirect_port": 3455,    # sign-in redirect http://localhost:<port>/callback/
        "caption_channel_id": "", # Discord channel for captions; empty = [record] alert channel
    },
    "x": {
        # `clipper x`: Shorts posted to X as native video (API v2, pay-per-use).
        "kinds": ["short"],       # schedule.json kinds allowed
        "max_per_24h": 6,         # posts in any 24 h
        "auto_post_minutes": 0,   # `clipper bot` runs `clipper x --auto` this often; 0 = only by hand
        "after_youtube": False,   # true: --auto waits until a Short is public on YouTube
        "max_age_hours": 72,      # --auto skips Shorts from streams older than this (--items still can); 0 = any
        "hashtags": [],           # added after the Short's own
        "max_hashtags": 3,        # the Short's subject tags, then the show's
        "show_hashtag": True,     # end the hashtags with the show's name (#KinoCasino), never dropped first
        "hook_first": True,       # drop [meta] title_format's show prefix: the feed shows the first words
        "mentions": [],           # @handles of whose footage it is (per show: [shows.<key>.x]); each is notified
        "credit": "From {show}: {mentions}",  # last line, when the show has mentions
        "max_chars": 280,         # X Premium accounts may post longer
        "max_height": 1280,       # taller videos are re-encoded to this (X recommends 720x1280); 0 = as is
        "post_usd": 0.015,        # X's price per post without a link, logged to paths.costs
        "redirect_port": 3456,    # sign-in callback http://127.0.0.1:<port>/callback
    },
    "youtube": {
        "auto_upload_minutes": 0, # `clipper bot` runs `clipper upload` this often; 0 = only by hand
        "upload_ahead_hours": 48, # upload entries whose slot is within this window
        "min_lead_minutes": 30,   # publishAt must still be this far in the future
        "manual_publish": False,  # true: upload private, no publishAt; make it public in Studio by hand
        "max_per_run": 6,         # quota guard per run
        "max_per_24h": 6,         # uploads in any 24 h, all runs together: YouTube caps a channel's
                                  # daily uploads (uploadLimitExceeded); new channels hit it near 8
        "category_id": "24",      # Entertainment
        "language": "en",
        "cookies_from_browser": "",  # yt-dlp's signed-in retries read this browser profile live
                                     # ("firefox:clipper") instead of [paths] youtube_cookies
        "made_for_kids": False,
        "notify_subscribers": True,
        "captions": True,         # upload the .srt beside each video
        "thumbnails": True,       # set the .jpg beside each video (`clipper thumbs`)
        "short_thumbnails": False,  # ...Shorts too; off until thumbnails.set is verified on a Short
        "add_to_playlist": True,  # the metadata draft's playlist, created on first use
        "show_playlist": "{show}",  # ...and every upload in its show's playlist too; "" = off
        "playlist_privacy": "public",
        # A line in the description linking each video to the longer one it comes from: a clip
        # to its segment (at the clip's offset), a segment to its compilation. Only once that
        # video is public (or will be by the time this one is); every `clipper upload` run also
        # adds, fixes or drops the line on videos already up. {url}, {title}: the longer video's.
        "source_links": True,
        "clip_source": "Full segment: {url}",
        "segment_source": "The whole story in one video: {url}",
        "part_links": False,      # segments also link the stream's previous and next segment
        "previous_part": "Previous part: {url}",
        "next_part": "Next part: {url}",
        "link_updates_per_run": 20,  # description updates per run (~51 quota units each: a read and a write)
        "daily_quota": 10000,     # the Cloud project's Data API units a day (uploads apart)
        "upload_units": 600,      # kept back per upload still allowed today (thumbnail 50,
                                  # captions 400, playlists 50 each); backfills get the rest
    },
    "kick": {
        "channel": "",
        "max_height": 1080,
        "download_fragments": 8,
        "chat_workers": 4,
        "chat_rps": 3.0,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/141.0 Safari/537.36",
    },
    "worker": {
        # `clipper worker` (GPU machine) runs `clipper run` on each recording finished under
        # paths.streams; `clipper run <stream>` resumes at the first stage not done.
        "poll_s": 300,
        "stages": ["signals", "transcribe", "score", "segments", "faces", "cast", "cut", "meta", "thumbs",
                   "shorts", "review"],  # faces and cast need the faces extra (OpenCV); take them out where
                                         # that isn't installed
        # Clips first: cut, draft, cover and publish the picks and their Shorts, then cut
        # the long segments and compilations (hours of cutting and previews).
        "fast_lane": True,
        "notify": True,          # review stage posts Discord cards ([discord] enabled too)
        "channels": [],           # only recordings of these Kick slugs; [] = all
        "max_attempts": 3,        # failed runs are retried this many times in all...
        "retry_minutes": 60,      # ...this long apart
        "stale_minutes": 10,      # a run with no heartbeat this long is taken over
    },
    "preshow": {
        # `clipper preshow`: whole videos a show posts on its own YouTube channel (Kino
        # Casino's pre-recorded pre-show). Set youtube_channel per show, in [shows.<key>.preshow].
        "youtube_channel": "",    # channel id (UC...) whose uploads feed is watched; "" = off
        "title_pattern": r"(?i)pre.?show",  # uploads whose title matches are grabbed
        "max_age_hours": 36,      # feed entries older than this are left alone (fetch by hand)
        "poll_minutes": 5,        # `clipper bot` checks the feeds this often, then runs new ones; 0 = off
        "format": "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "stages": ["transcribe", "segments", "cut", "meta", "thumbs", "review"],  # no clips/Shorts
        "model": "claude-sonnet-5-5",  # writes the one segment's title/description
        "effort": "medium",
        "prompt_version": "v1",   # prompts/preshow_<v>.md
        "oneoff_prompt_version": "v1",  # prompts/oneoff_<v>.md: `fetch --from/--to/--about`, one stretch
        "trim_end_s": 3.0,        # drop this much off the end (2026-10-23, user); 0 = whole video
        "publish": "asap",        # asap: out as soon as it is approved and uploaded | slots
        "band": "Pre-show {date}!",  # cover's green band ({date} = M/D aired); "" = the model's line
        "teasers": None,          # cover's side boxes; false = one story fills it; None = as [thumbnail]
        "playlist": "{show} Pre-shows",  # YouTube playlist for every pre-show; "" = the model's pick
        "credit": "The {show} pre-show from {date}, first posted on their YouTube channel: {url}",
    },
    "record": {
        # `clipper record watch`: Kick slugs, or tables {slug = "...", <key below> = override}
        "channels": [],
        "poll_s": 60,             # live-status check per channel
        "max_height": 1080,       # best variant at or below this height; 0 = best
        "end_grace_s": 300,       # after it goes offline, wait this long for a restart
        "stall_alert_s": 60,      # alert when video stops arriving this long while live
        "segment_retry_s": 20,    # keep retrying a failed segment this long before a gap
        "container": "mkv",       # final source.<container>: mkv (remux) or ts
        "keep_parts": False,      # keep live/partNNN.ts after joining them
        "extract_audio": True,    # write audio.wav so `clipper signals` runs before transcribe
        "backfill_chat": True,    # fill chat-socket outages from the chat-history API
        "backfill_min_s": 5,      # ignore outages shorter than this
        # `clipper services stop|restart recorder` (and deploy/record/deploy.sh) reach the NAS by ssh:
        "ssh_host": "",           # ssh host alias; "" = $RECORD_SSH_HOST, else "nas"
        "compose_dir": "",        # the compose folder there, relative to the ssh user's home; "" = $RECORD_DIR, else "clipper-record"
        "sudo": "",               # prefix for docker there if the ssh user needs it ("sudo"); "" = $RECORD_SUDO
        "chat_tail_s": 15,        # keep chat this long past a part's end (chat trails video)
        "on_finish": "",          # command after a recording is finalized (no shell;
                                  # split like one): {stream_id} {stream_dir} {channel}
        "alert_webhook_env": "CLIPPER_ALERT_WEBHOOK",  # env var with a Discord webhook URL
        "alert_channel_id": "",   # or post alerts with the review bot's token here
        "alert_mention": "",      # prepended to warnings, e.g. "<@123456789012345678>"
    },
    "transcribe": {
        "backend": "whisperx",    # whisperx (CUDA) | mlx (Apple Silicon, `uv sync --extra mac`)
                                  # | modal (cloud GPU, `uv sync --extra modal`, issue #11)
        "modal_app": "clipper-whisperx",  # deployed app name (`clipper transcribe --deploy`)
        "modal_gpu": "L40S",      # T4 | L4 | A10 | L40S | A100 | H100; "" = the app's default
                                  # (8 h stream, 2026-10-23: L40S 10 min ~$0.34, L4 19 min ~$0.26,
                                  # H100 8 min ~$0.59; L4 is cheapest for bulk archive runs)
        "modal_audio": "flac",    # flac (lossless, ~60 MB/h) | opus (48 kbps, smaller upload)
        "mlx_model": "mlx-community/whisper-large-v3-turbo",  # Hugging Face repo for mlx
        "mlx_align": True,        # refine mlx word times with WhisperX alignment if installed
        "model": "large-v3",
        "language": "en",
        "compute_type": "float16",
        "batch_size": 8,
        "device": "cuda",
        "diarize": False,         # speaker turns too (pyannote, issue #52): whisperx or modal backend,
                                  # needs a Hugging Face token (wiki: Speakers); `clipper speakers` names them
        "diarize_model": "pyannote/speaker-diarization-community-1",
        "min_speakers": 0,        # 0 = let pyannote decide
        "max_speakers": 0,
    },
    "speakers": {
        # `clipper speakers`: SPEAKER_xx -> a name, by voice against the show's enrolled voices.
        "match": 0.6,             # lowest cosine similarity to name a speaker (kick-131151926: same
                                  # voice 0.88, different people <= 0.46; music beds near the co-hosts)
        "min_talk_s": 20,         # speakers with less talk time are listed but never named
        "max_refs": 10,           # embeddings kept per enrolled voice (newest win)
    },
    "faces": {
        # `clipper faces`: who is on screen, by face, over the stream's keyframes (#54). CPU only.
        "workers": 0,             # processes for detection; 0 = half the CPU threads
        "min_px": 40,             # faces shorter than this (source pixels) are too small to name
        "cluster": 0.4,           # lowest average cosine similarity inside one group of faces
        "match": 0.7,             # lowest similarity of a group to a known face to name it
                                  # (kick-131151926 vs kick-135522987: same person 0.93, lookalikes <= 0.55)
        "min_faces": 5,           # smaller groups are dropped
        "sheets": 60,             # contact sheets for the biggest groups (faces/F01.jpg ...); named ones always
        "max_refs": 10,           # group averages kept per known face (newest win)
    },
    "cast": {
        # `clipper cast` (#41): head-and-shoulders photos of each regular in [thumbnail]
        # photos/<Person>/, from streams and the web. Image search also needs $BRAVE_API_KEY.
        "min_px": 100,            # faces shorter than this (pixels of the picture) aren't taken
        "match": 0.5,             # an enrolled person's photo: lowest similarity of its face to
                                  # theirs (one face, not a group's average, so below [faces] match)
        "max_px": 1600,           # the longest side a photo is kept at
        "per_source": 6,          # most new photos one find adds from each source
        "videos": 12,             # recent videos whose thumbnails a YouTube channel offers
        "search_count": 40,       # image search results looked at
        "min_photos": 3,          # doctor flags anyone with fewer kept
        "known_figures": True,    # everyone with a kept photo is added to [scoring] known_figures
        # Automatic: the worker's `cast` stage grabs each new stream's named faces, and the bot
        # runs `clipper cast auto` (a find from every source) for anyone short of photos.
        "target": 6,              # photos (kept + new) the automation gathers per person
        "auto_keep": 0.65,        # keep a photo without asking when an enrolled face matches this
                                  # well and the label has no doubt (0 = everything waits for review)
        "auto_keep_sources": ["stream", "commons", "channel"],  # never "web" (image search, a pasted
                                  # address): its licence is unknown, so you keep those yourself
        "auto_hours": 24,         # serve's `cast auto` this often; 0 = off
        "retry_days": 7,          # someone a find added nothing for is tried again after this
        "label_model": "claude-haiku-5-5",  # labels each new photo ("" = no labels)
        "label_effort": "low",
        "prompt_version": "v1",   # prompts/cast_label_<v>.md
        "labels": ["neutral", "smiling", "laughing", "angry", "shocked", "smug", "sad", "serious"],
        # Never taken from image search (sites and the pages they're on): photo agencies, whose
        # photographers file copyright claims against thumbnails and posts.
        "blocked_domains": ["gettyimages.com", "gettyimages.co.uk", "istockphoto.com", "apimages.com",
                            "ap.org", "alamy.com", "shutterstock.com", "reuters.com", "wireimage.com",
                            "zumapress.com", "dreamstime.com", "depositphotos.com", "123rf.com",
                            "stock.adobe.com", "imago-images.com", "newscom.com", "agefotostock.com",
                            "pond5.com", "eyevine.com", "rexfeatures.com", "photoshelter.com"],
    },
    "news": {
        # `clipper news` (#110): headlines about the shows' people from feeds, subreddits and
        # Google News; the bot checks every check_minutes and posts what the judge rates.
        "check_minutes": 0,       # the bot's `news check` this often; 0 = off
        "channel": "news",        # Discord text channel for news posts, by name: the bot makes it if missing
        "channel_id": "",         # or this channel by id (wins over channel); both "" = the review channel
        "feeds": [{"name": "TMZ", "url": "https://www.tmz.com/rss.xml"},
                  {"name": "Dexerto", "url": "https://www.dexerto.com/feed/"}],
        "reddit": ["LivestreamFail"],  # subreddits whose new posts are read (their RSS)
        "google_news": True,      # one Google News search per watched person with a full name
        # The terms: who to watch, { "Ethan Klein" = ["H3 Podcast"] } (other names in the list;
        # always searched). [shows.<key>.news] watch ties terms to that show. With none (and
        # auto_watch off) nothing is fetched or searched at all.
        "watch": {},
        "auto_watch": False,      # also watch each show's cast, known figures and name, and the cast library
        "ignore": [],             # names never watched (a cast name that is also a common word)
        "min_fetch_minutes": 20,  # a feed or subreddit at most this often
        "search_minutes": 120,    # each Google News search at most this often
        "max_age_hours": 48,      # older items are skipped
        "keep_days": 30,          # items and stories are forgotten after this
        "judge_model": "claude-haiku-5-5",  # rates each matched headline ("" = --no-judge)
        "judge_effort": "low",
        "prompt_version": "v1",   # prompts/news_judge_<v>.md
        "max_judge": 60,          # most headlines judged per check (newest first)
        "post_maybe": True,       # post "maybe" stories too, marked as such
        "max_posts": 5,           # most stories posted per check (strong ones first)
        "context_days": 7,        # a queued context note no stream of its show took by then is dropped
        "preshow_minutes": 60,    # suggest the day's stories this long before a show's usual start
                                  # (learned from the last three weeks of streams); 0 = off
    },
    "segments": {
        "model": "claude-opus-5-5",   # one call over the whole transcript
        "effort": "medium",
        "prompt_version": "v3",   # prompts/segments_<v>.md, compilation_<v>.md (v3: speaker tags, #52)
        "min_aside_s": 180,       # fold shorter asides into the surrounding segment
        "min_s": 180,             # only cut segments between min_s and max_s long
        "max_s": 3600,
        "skip_kinds": ["intro", "break", "ad_read", "outro", "gambling"],
        "source_share": ["none", "some", "most"],  # played-video share allowed in a cut
        "compile_min_segments": 2,  # topics with this many segments also get one long video
    },
    "cut": {
        # Segments and compilations are always stream-copied (lossless, keyframe starts).
        "clip_codec": "libx264",  # picks: libx264 | h264_qsv | h264_nvenc | h264_vaapi
        "crf": 20,                # quality for re-encodes (global_quality / cq / qp)
        "parallel": 3,            # segments cut at once (each an ffmpeg: copy + AAC of the bleeped audio)
        # "nas": segments and compilations are cut on the NAS beside the source (#51), in a
        # one-off container of the recorder image (deploy/record) over ssh; picks stay here.
        "backend": "local",       # local | nas
        "nas_host": "",           # ssh host (alias) of the NAS, key login (BatchMode)
        "nas_data": "",           # the NAS folder mounted as /data, holding streams/ (the recorder's CLIPPER_DATA)
        "nas_user": "",           # uid:gid to write files as (the recorder's PUID:PGID); "" = the image's
        "nas_image": "clipper-record:latest",
    },
    "bleep": {
        "mode": "off",            # off | bleep (1 kHz tone) | mute; applied by `clipper cut`
        "pad_s": 0.08,            # cover this much either side of the word timing
        "tone_hz": 1000,
        "tone_level": 0.25,
        # Regexes matched against whole lowercased words (punctuation stripped).
        "patterns": [r"n[i1]gg(?:a|as|az|er|ers|uh|uhs)", r"f[a@]g(?:s|got|gots|gy)?",
                     r"retard(?:s|ed)?", r"tr[a@]nn(?:y|ies)", r"k[i1]kes?", r"spics?",
                     r"chinks?", r"wetbacks?"],
        "profanity": False,       # also cover swearing (profanity_patterns), not just slurs
        "profanity_patterns": [r"(?:mother)?f+u+c+k+\w*"],  # fuck, fucking, fuckin', motherfucker
    },
    "meta": {
        "model": "claude-opus-5-5",
        "effort": "medium",
        "prompt_version": "v3",   # prompts/meta_<v>.md, chapters_<v>.md (v3: speaker tags, #52)
        "concurrency": 4,
        "show_name": "",
        "cast": [],               # per show: its own people by name, e.g. ["Andy", "PPP"]; prompts name them, never "host"
        # Upload titles: {title}, {show} or {SHOW} (upper case). Not applied to a title
        # it would push past 100 characters, or one already starting with the show name.
        "title_format": "{title}",
        "credit": "Clipped from {show}, streamed live on Kick {date}: https://kick.com/{channel}",
        "chapters_min_s": 900,    # segments at least this long get transcript chapters
        "min_chapters": 3,
        "max_chapters": 8,
        "chapter_min_gap_s": 60,
        "srt_line_chars": 42,
        "srt_max_s": 6,
    },
    "thumbnail": {
        # `clipper thumbs`: candidate frames -> Claude picks one and writes the text ->
        # <video>.jpg beside each video (uploaded by `clipper upload`, shown in Kitsu).
        "model": "claude-opus-5-5",
        "effort": "medium",
        "prompt_version": "v2",   # prompts/thumbnail_<v>.md (v2: names [meta] cast, not "the hosts")
        "concurrency": 4,
        "clip_frames": 8,         # candidates per clip, around its hook and payoff
        "even_frames": 5,         # segments/compilations: evenly spaced candidates...
        "heat_frames": 5,         # ...plus this many chat-heat peaks
        "min_gap_s": 20,          # between candidate frames of one asset
        "min_brightness": 28,     # mean luma 0-255; darker frames are skipped
        "min_sharpness": 150.0,   # variance of the Laplacian; blurrier frames are skipped
        "min_crop": 0.4,          # the model may zoom in to a 16:9 box this wide (share of the frame)
        "max_words": 4,
        # Asking again for unusable text, and a reviewer's note on a retake:
        # prompts/thumbnail_retry_<v>.md, thumbnail_enquirer_retry_<v>.md, thumbnail_feedback_<v>.md
        "retry_prompt_version": "v1",
        "font": "",             # TTF/OTF path; empty = first of fonts that exists
        "fonts": ["C:/Windows/Fonts/impact.ttf", "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
                  "/Library/Fonts/Impact.ttf", "/System/Library/Fonts/Supplemental/Impact.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
        "uppercase": True,
        "text_color": "#ffffff",
        "accent_color": "#ffd400",  # the model's highlight word, badge and stripe
        "outline_color": "#000000",
        "outline_px": 10,
        "shadow_px": 8,           # drop shadow offset; 0 = none
        "text_height": 0.17,      # cap height of one line, as a share of the frame height
        "max_lines": 2,
        "margin_px": 48,
        "shade": 0.55,            # darken behind the text: 0 = none, 1 = black
        "saturation": 1.15,       # frame grading
        "contrast": 1.08,
        "badge_text": "",         # small fixed label top-left, e.g. the show name; empty = none
        "badge_font": "",         # empty = font
        "logo": "",               # PNG drawn top-left instead of badge_text; empty = none
        "stripe_px": 10,          # accent stripe along the bottom edge; 0 = none
        "max_bytes": 2_000_000,   # YouTube's limit
        "kitsu": True,            # attach to the published Kitsu revision, and set it as the thumbnail
        # style = "enquirer": a 1990s supermarket-tabloid cover (masthead, teaser band and
        # boxes plugging the episode's other videos, big face, yellow headline, inset).
        "style": "classic",       # classic | enquirer
        "enquirer_prompt_version": "v6",  # prompts/thumbnail_enquirer_<v>.md (v6: picks a cast photo by
                                          # label, #41; v5: frames say whose face `clipper faces` found;
                                          # v4: names [meta] cast, not "a host")
        "face_frames": 2,         # enquirer: with faces.json, each named person's best frames added...
        "face_people": 4,         # ...for this many people, the ones seen most in the video
        "masthead": "",           # empty = meta.show_name
        "masthead_tagline": "LARGEST CIRCULATION OF ANY STREAM ON KICK",
        "masthead_price": "$1.39",
        "badge_kicker": "AS SEEN ON",
        "badge_channel": "",      # empty = scoring.channel_name; the small badge on the photo
        "teasers": True,          # the three side boxes; false = the photo fills the left (one big moment)
        "condensed_fonts": ["C:/Windows/Fonts/bahnschrift.ttf",
                            "/usr/share/fonts/truetype/roboto/unhinted/RobotoCondensed-Bold.ttf",
                            "/usr/share/fonts/truetype/roboto/hinted/RobotoCondensed-Bold.ttf",
                            "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf"],
        "condensed_variation": "Bold Condensed",  # named instance for variable fonts
        # Enquirer covers: a folder of head-and-shoulders photos named "<Person>.jpg" (or
        # .png/.webp), for people talked about but not on screen. The model may use one for
        # a cover's face or its teaser box; `clipper thumbs --photo/--teaser-photo` sets one
        # by hand. Crop each to head and shoulders: the whole picture is the face. "" = none.
        "photos": "",
    },
    "shorts": {
        # `clipper shorts`: each pick's short window as a vertical video in shorts/.
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "codec": "",              # final encode; empty = cut.clip_codec
        "crf": 20,
        "cut_codec": "",          # the trimmed, bleeped intermediate; empty = cut.clip_codec
        "cut_crf": 16,
        "keep_work": False,       # keep shorts/work/ (intermediate cut, .ass captions)
        "pad_s": 0.15,            # room before the first and after the last word
        "window": "short",        # short: scoring's 15-60 s short window | clip: the pick's in/out
        "min_s": 15,              # widen shorter windows by whole transcript lines to this
        "max_s": 59,              # longest Short: over 60 s, any Content ID claim blocks it worldwide
        "end_pause_s": 0.7,       # window "short": end on a sentence end or a pause this long; 0 = off
        "fade_in_s": 0.05,
        "fade_out_s": 0.25,
        "lufs": -14.0,            # loudness target (two-pass loudnorm)
        "true_peak": -1.5,
        "lra": 11,
        # Layouts: name -> {description (for the picker), panels, caption_y}. Each panel
        # is src = [x, y, w, h] as fractions of the source frame, cropped to its slot's
        # aspect around focus = [fx, fy] (default the middle) and scaled to the full width;
        # h = its height in px (panels without h share the rest). Panels stack top to
        # bottom. caption_y: the captions' centre, as a share of the height. "center" (a
        # 9:16 crop of the middle) is always there.
        "layouts": {},
        "default_layout": "center",
        "layout_model": "claude-haiku-5-5",  # labels sampled frames with a layout
        "layout_effort": "low",
        "layout_prompt_version": "v1",  # prompts/shorts_layout_<v>.md
        # Scene cuts: a frame every scene_step_s gets a layout; a run shorter than
        # min_scene_s joins its neighbour; each change is placed on the first frame that
        # looks more like the new scene than the old, and every stretch uses its own layout.
        "scene_step_s": 4,
        "scene_batch": 8,         # frames per labelling call (more per call drifted)
        "min_scene_s": 6,
        "captions": True,
        "caption_font": "Impact",  # a font family the system (libass) can find
        "caption_fontsdir": "",    # or a folder of font files to look in
        "caption_size": 96,
        "caption_color": "#ffffff",
        "caption_highlight": "#ffd400",  # the word being spoken
        "caption_pop": 115,        # its scale, in percent
        "caption_outline": "#000000",
        "caption_outline_px": 7,
        "caption_shadow_px": 3,
        "caption_uppercase": True,
        "caption_max_words": 3,    # words on screen at once
        "caption_max_chars": 18,
        "caption_gap_s": 0.6,      # a pause this long starts a new group
        "hashtag": "#Shorts",      # first hashtag of each Short's description
    },
    "analytics": {
        # `clipper analytics`: YouTube stats of uploads -> analytics.json -> a hint in Pass B.
        "auto_hours": 24,         # `clipper serve` refreshes it this often (once there are uploads); 0 = off
        "min_videos": 5,          # a show needs this many uploads with 7-day numbers first
        "examples": 4,            # best and worst shown to Pass B
    },
    "notify": {
        # #22: the bot DMs you when something needs you (clipper/alerts.py). `clipper alert test`.
        "enabled": True,
        "user_id": "",            # who gets the DMs; "" = the first [discord] allowed_user_ids
        "kinds": ["ready", "upload", "failure", "recorder", "disk", "digest", "spend"],
        "quiet_hours": "00:00-08:00",  # [schedule] timezone; held, then sent together; "" = never quiet
        "cooldown_hours": 6,      # the same problem at most once per this long
        "digest_time": "09:00",   # daily digest (out today, tomorrow, waiting); "" = none
        "disk_free_gb": 50,       # warn when the streams share has less free than this; 0 = off
    },
    "serve": {
        # `clipper serve`: the always-on process (the war room, hourly uploads, pre-show grabs,
        # retakes, the daily jobs). The bot and `clipper doctor` reach it here.
        "url": "",                # "" = this machine, http://127.0.0.1:<[warroom] port>/; else e.g. "http://nas:8765/" (needs [warroom] token on both)
    },
    "warroom": {
        # The dashboard (schedule, pipeline, review queue, retakes): `clipper serve` runs it
        # with the jobs, `clipper warroom` the page alone.
        "host": "127.0.0.1",      # 0.0.0.0 to open it to the LAN (then set token)
        "port": 8765,
        "token": "",              # required off localhost: open it as http://host:port/?key=<token>
        "lowres_height": 360,     # phones play a copy this tall (short side), in lowres/ beside the preview
        "lowres_prewarm": True,   # make those copies ahead for items waiting for review
        "tailnet": False,         # served on this machine's tailnet (clipper warroom --tailnet on); doctor checks it
    },
    "retake": {
        # `clipper retake`: a "retake" note -> edits (trim, layout, title, thumbnail) ->
        # a new revision waiting for approval in Kitsu and a fresh Discord card.
        "auto": True,             # `clipper bot` runs it after card refreshes and Changes buttons
        "model": "claude-sonnet-5-5",
        "effort": "medium",
        "prompt_version": "v1",   # prompts/retake_<v>.md
        "context_s": 90,          # transcript this far around the cut points
        "max_per_run": 10,
    },
    "pick": {
        # `clipper pick`: Shorts from a stretch of a stream the user names (hand picks).
        "model": "claude-sonnet-5-5",
        "effort": "high",
        "prompt_version": "v1",   # prompts/pick_<v>.md, pick_locate_<v>.md
        "max_clips": 3,           # moments per stretch
        "span_s": 600,            # --at: this long a stretch, centred on the time
        "max_span_s": 2700,       # longest stretch read in one call
        "max_overlap": 0.5,       # skip a moment whose Short overlaps an existing pick this much
        "locate_model": "claude-haiku-5-5",  # --about without a time: search the summary timeline
        "locate_effort": "medium",
        "max_stretches": 2,       # summary blocks read after locating
        "locate_pad_s": 120,      # around each located 15-minute block
    },
    "review": {
        "host": "",               # Kitsu API URL + /api, e.g. http://nas:8090/api (required for review)
        "token_env": "KITSU_TOKEN",   # env var holding a Kitsu bot token
        "project": "",            # Kitsu project; empty = meta.show_name
        "episode_name": "{date}", # per stream; {date} {stream_id} {title}
        "include": ["segments", "picks", "shorts"],  # + "compilations" as items; else playlists
        "preview_height": 720,
        "preview_fps": 30,
        "preview_codec": "",      # empty = cut.clip_codec
        "preview_quality": 28,
        "max_upload_mb": 480,     # Kitsu's proxy rejects bigger uploads; longer previews are re-encoded to fit
        "normalize": False,       # let Kitsu re-encode uploads (slow on the NAS); ours are h264 mp4 already
    },
    "discord": {
        "enabled": False,         # post review cards when `clipper review publish` finishes
        "token_env": "DISCORD_BOT_TOKEN",  # env var with the bot token; else paths.discord_token
        "guild_id": "",           # your server's id; slash commands are registered there only
        "review_channel_id": "",  # text or forum channel; one thread (or post) per stream
        "allowed_user_ids": [],   # Discord user ids allowed to press buttons and run commands
        "thread_name": "{show} {date}",  # {show} {date} {stream_id} {title}
        "max_upload_mb": 10,      # this server's upload limit (10; 50 or 100 with boosts)
        "preview_height": 480,
        "max_video_kbps": 1500,   # bitrate cap for previews that fit easily
        "min_video_kbps": 350,    # below this an item only gets its first excerpt_s seconds
        "excerpt_s": 60,
        "replan": True,           # rerun `clipper schedule` after each decision from Discord
        "refresh_minutes": 10,    # bot re-reads Kitsu and updates cards changed elsewhere; 0 = off
        "refresh_days": 30,       # ...for streams started within this many days (0 = all)
        "weekly_channel": "weekly",  # `clipper weekly --discord` posts the pages here (made if missing)
    },
    "schedule": {
        "timezone": "America/Los_Angeles",
        "start": "",              # earliest date to fill (YYYY-MM-DD); empty = tomorrow
        "horizon_days": 21,       # how far ahead to fill slots
        "clip_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "clip_times": ["12:00", "18:00"],
        "segment_days": ["mon", "tue", "wed", "thu", "fri"],
        "segment_times": ["16:00"],
        "compilation_days": ["sat"],
        "compilation_times": ["14:00"],
        "short_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "short_times": ["10:00"],
        "max_per_day": 6,         # YouTube upload quota guard, across all kinds
        "min_gap_minutes": 90,    # between any two releases
        "kick_first_hours": 24,   # nothing goes out until this long after the stream ended
        "stream_budget": {},      # most releases per stream by kind, e.g. {segment = 10, short = 8}:
                                  # the best go (Shorts by score, segments by chat heat); {} = all;
                                  # 0 = none of that kind unless pinned
        "budget_hours": 8.0,      # a stream this long gets the whole budget; others scale with length
        "stale_days": 0,          # an item not out this many days after its first allowed slot (a pinned Publish at never goes stale)
                                  # never goes (keeps releases current); 0 = off
        "clip_order": "score",    # score (best first) or stream (oldest stream first)
        "mix_shows": True,        # shows take turns in each kind's queue; false = one queue
        "priority": False,        # set in a stream's config.toml: its items go ahead of every
                                  # other stream's (their automatic, not-uploaded slots are freed)
    },
    "signals": {
        "chat_lag_s": 6,          # stream delay + typing time
        "baseline_s": 300,        # rolling median window
        "smooth_s": 10,
        "z_cap": 8.0,
        "w_chat": 1.0,            # unique chatters/s
        "w_react": 0.8,           # laugh/shock messages
        "w_clip": 1.0,            # "clip it" messages
        "w_loud": 0.4,            # audio RMS
        "react_regex": r"(?i)lol|lmao|lmfao|kek|lul|haha|rofl|laugh|dead|omg|wtf|💀|😂|🤣",
        "clip_regex": r"(?i)\bclip",
        "min_heat": 1.0,
        "min_gap_s": 45,
        "window_pre_s": 120,
        "window_post_s": 30,
        "max_window_s": 240,
        "top_n": 30,
    },
    "costs": {
        # `clipper costs` and the war room's Costs panel (#50). Model calls and Modal runs are
        # logged as they happen; these are what the logs can't know.
        "monthly": {},   # fixed costs per month, {"Runway Max" = 95, "domain" = 2}
        "credits": {},   # a provider's monthly credit against its spend: {"modal" = 30}
    },
    "llm": {
        # Which provider answers is the model string's prefix (clipper/llm.py): bare or
        # "anthropic:" = Claude, "openai:<model>" (OPENAI_API_KEY), "gemini:<model>"
        # (GEMINI_API_KEY), "local:<model>" = an OpenAI-compatible server at local_base_url.
        "local_base_url": "",     # e.g. http://<host>:11434/v1 for Ollama; set in clipper.toml
        "timeout_s": 600,         # per OpenAI-compatible request
        # $ per 1M input/output tokens, by model string, for cost lines only (a model not
        # listed costs $0). A [llm.prices] table in clipper.toml adds to these.
        "prices": {
            "claude-haiku-5-5": [0.10, 0.50],
            "claude-sonnet-5-5": [2.00, 10.00],
            "claude-opus-5-5": [4.00, 20.00],
        },
    },
    "scoring": {
        "workspace_id": "",       # or $ANTHROPIC_WORKSPACE_ID; needed for unscoped keys
        "model_a": "claude-haiku-5-5",
        "model_b": "claude-sonnet-5-5",
        "effort_a": "medium",
        "effort_b": "high",
        "prompt_version": "v5",   # v5: speaker tags in the transcript (#52)
        "concurrency": 4,
        "lead_in_s": 120,
        "max_picks": 10,
        "channel_name": "a clips channel",
        "audience": "people who enjoy streamer commentary and drama",
        "tone": "unfiltered, chaotic, funny",
        "stream_context": "",     # show name, regulars, channel emotes; v2+ prompts
        # Style rules appended to every prompt that writes viewer-facing text (titles,
        # descriptions, hooks, chapters, thumbnails). Add your own notes here.
        "house_style": [("Never use the word \"hosts\", or \"host\" for a co-host; it "
                         'reads as machine-written. Name the person (Andy, PPP, Jer...) or make '
                         'the subject the actor '
                         '("Nobody buys it").')],
        "third_party_audio": "reject",  # or "allow_reactions"; v3+ prompts
        "max_source_audio_s": 5,  # allow_reactions: most source audio a Short may carry
        "audience_knowledge": "strangers",  # or "fans" (know known_figures); v4+ prompts
        "known_figures": "",      # regulars fans are assumed to know, comma-separated
        "transcript_scan": True,  # also find own-voice moments from the transcript (scan prompt)
        "scan_max_per_block": 3,  # per 15-minute block
        "scan_pad_s": 30,         # window padding around a scanned moment
        "scan_max_overlap": 0.5,  # drop scanned windows overlapping a chat window this much
    },
}


def _coerce(raw: str, like: Any, name: str = "") -> Any:
    """An env var's text as the default's type. Lists take "a,b" or "a b" (or JSON); tables
    take JSON (#77 L9)."""
    try:
        if isinstance(like, bool):
            return raw.lower() in ("1", "true", "yes", "on")
        if isinstance(like, int):
            return int(raw)
        if isinstance(like, float):
            return float(raw)
        if isinstance(like, list):
            raw = raw.strip()
            return json.loads(raw) if raw.startswith("[") else raw.replace(",", " ").split()
        if isinstance(like, dict):
            return json.loads(raw)
        if like is None and raw.strip().lower() in ("true", "false"):  # e.g. [preshow] teasers (unset = inherit)
            return raw.strip().lower() == "true"
    except ValueError as e:
        raise ClipperError(f"{name or 'environment variable'}={raw!r}: not a {type(like).__name__} ({e})") from None
    return raw


def stream_dir(cfg: dict, ref: str) -> Path:
    """A stream id under paths.streams, or a stream directory. One that doesn't exist is an
    error here, not a traceback three calls later (or a base config labelled as the stream)."""
    p = Path(ref)
    if p.is_dir():
        return p
    d = cfg["paths"]["streams"] / ref
    if not d.is_dir():
        raise ClipperError(f"no stream {ref!r} in {cfg['paths']['streams']} (clipper worker --status lists them)")
    return d


# Settings that take one of a fixed set of values. Anything else would quietly do the wrong
# thing (B7: [bleep] mode = "Bleep" meant no bleeping), so loading refuses it.
CHOICES = {
    ("bleep", "mode"): ("off", "bleep", "mute"),
    ("cut", "backend"): ("local", "nas"),
    ("transcribe", "backend"): ("whisperx", "mlx", "modal"),
    ("transcribe", "modal_audio"): ("flac", "opus"),
    ("thumbnail", "style"): ("classic", "enquirer"),
    ("shorts", "window"): ("short", "clip"),
    ("schedule", "clip_order"): ("score", "stream"),
    ("preshow", "publish"): ("asap", "slots"),
    ("record", "container"): ("mkv", "ts"),
    ("rumble", "visibility"): ("public", "unlisted"),
    ("rumble", "schedule"): ("youtube", "now"),
    ("rumble", "link"): ("hardlink", "copy"),
    ("perform", "lipsync_engine"): ("latentsync", "sync"),
    ("scoring", "third_party_audio"): ("reject", "allow_reactions"),
    ("scoring", "audience_knowledge"): ("strangers", "fans"),
}
# CLIPPER_* variables that aren't settings.
SPECIAL_ENV = {"CLIPPER_CONFIG", "CLIPPER_LOCAL_CONFIG", "CLIPPER_BOOTSTRAP", "CLIPPER_ALERT_WEBHOOK",
               "CLIPPER_DATA", "CLIPPER_TOML"}  # the last two: the NAS recorder's deploy (deploy/record)
SHOW_KEYS = ("channel", "channels")  # a [shows.<key>] table's own settings; the rest are sections


EFFORTS = ("low", "medium", "high", "xhigh", "max")  # every *effort* key (clipper/llm.py maps them per provider)


def check_choices(cfg: dict, where: str = "") -> None:
    bad = [f"[{s}] {k} = {cfg[s][k]!r} (one of: {', '.join(c)})"
           for (s, k), c in CHOICES.items() if s in cfg and cfg[s].get(k) not in c]
    bad += [f"[{s}] {k} = {v!r} (one of: {', '.join(EFFORTS)})"
            for s, sec in _sections(cfg) for k, v in sec.items()
            if "effort" in k and v not in EFFORTS]  # any type: false or 3 is as wrong as "med"
    if bad:
        raise ClipperError(f"{where}not a valid choice: " + "; ".join(bad))


def problems(cfg: dict) -> list[str]:
    """What load() warns about: settings clipper.toml or [shows.*] name that clipper doesn't
    have (a typo, or a key since renamed), and CLIPPER_* variables that match no setting."""
    root = _root(cfg)
    out = list(root.get("_unknown", []))
    names = {f"CLIPPER_{s}_{k}".upper() for s, v in _sections(root) for k in v}
    out += [f"environment variable {n} matches no setting" for n in sorted(os.environ)
            if n.startswith("CLIPPER_") and n not in names and n not in SPECIAL_ENV]
    # [notify] times: a typo here used to kill the bot's DM loop quietly, every minute.
    for key, pat, want in (("quiet_hours", _HM + "-" + _HM, "HH:MM-HH:MM"), ("digest_time", _HM, "HH:MM")):
        v = str((root.get("notify") or {}).get(key, "") or "").strip()
        if v and not re.fullmatch(pat, v):
            out.append(f"[notify] {key} = {v!r}: {want} (or empty)")
    # [schedule] budget_hours divides in every replan: the base value and each show's override.
    budgets = [("[schedule]", (root.get("schedule") or {}).get("budget_hours", 1))]
    budgets += [(f"[shows.{k}.schedule]", s["overrides"]["schedule"]["budget_hours"])
                for k, s in (root.get("shows") or {}).items()
                if "budget_hours" in (s.get("overrides") or {}).get("schedule", {})]
    out += [f"{where} budget_hours = {v!r} must be a positive number (it divides in every replan)"
            for where, v in budgets if not (isinstance(v, int | float) and v > 0)]
    return out


_HM = r"(?:[01]?\d|2[0-3]):[0-5]\d"


# Keys of a loaded config that aren't [section] tables: "shows" (the [shows.<key>]
# profiles), "show" (which one this config is resolved for) and private bookkeeping.
NOT_SECTIONS = ("shows", "show")


def _sections(cfg: dict):
    return ((s, v) for s, v in cfg.items() if s not in NOT_SECTIONS and not s.startswith("_"))


def _apply_env(cfg: dict, only: set[str] | None = None, local: dict | None = None) -> None:
    """This machine's values over cfg: clipper.local.toml (`local`, as loaded), then env vars."""
    for section, values in _sections(cfg):
        if only is not None and section not in only:
            continue
        values.update(copy.deepcopy((local or {}).get(section, {})))
        for key, value in values.items():
            name = f"CLIPPER_{section}_{key}".upper()
            env = os.environ.get(name)
            if env is not None:
                values[key] = _coerce(env, value, name)


def _resolve_paths(paths: dict, base: Path, keys=None) -> None:
    for key in keys if keys is not None else list(paths):
        p = Path(paths[key]).expanduser()
        paths[key] = p if p.is_absolute() else base / p


LOCAL_CONFIG = "clipper.local.toml"


def local_path(cfg_path: str | Path | None = None) -> Path:
    """This machine's clipper.local.toml: $CLIPPER_LOCAL_CONFIG, else beside the config file."""
    if os.environ.get("CLIPPER_LOCAL_CONFIG"):
        return Path(os.environ["CLIPPER_LOCAL_CONFIG"])
    path = Path(cfg_path or os.environ.get("CLIPPER_CONFIG") or "clipper.toml")
    return path.resolve().parent / LOCAL_CONFIG


def _load_local(path: Path, cfg: dict, base: Path) -> dict:
    """clipper.local.toml as {section: {key: value}}, [paths] resolved; unknown keys refused."""
    if not path.exists():
        return {}
    local = tomllib.loads(path.read_text(encoding="utf-8"))
    for section, values in local.items():
        if section in NOT_SECTIONS or not isinstance(values, dict):
            raise ClipperError(f"{path}: only this machine's [section] settings go here, not [{section}]")
        unknown = [k for k in values if k not in cfg.get(section, {})]
        if unknown:
            raise ClipperError(f"{path}: unknown setting [{section}] {', '.join(unknown)}")
    if "paths" in local:
        _resolve_paths(local["paths"], base)
    return local


def load(path: str | Path | None = None, use_local: bool = True) -> dict[str, dict[str, Any]]:
    cfg = copy.deepcopy(DEFAULTS)
    unknown: list[str] = []
    path = Path(path or os.environ.get("CLIPPER_CONFIG") or "clipper.toml")
    base = Path.cwd()
    shows: dict[str, dict] = {}
    if path.exists():
        base = path.resolve().parent
        with open(path, "rb") as f:
            data = tomllib.load(f)
        shows = data.pop("shows", {})
        loose = [k for k, v in data.items() if not isinstance(v, dict)]  # a key above any [section]
        unknown += [f"{path.name}: {k} = {data[k]!r} is outside any [section]" for k in loose]
        for k in loose:
            data.pop(k)
        if not isinstance(shows, dict) or any(not isinstance(s, dict) for s in shows.values()):
            raise ClipperError(f"{path.name}: [shows] holds [shows.<key>] tables only")
        unknown += _unknown(data, f"{path.name}: ")
        for key, show in shows.items():
            unknown += _unknown({s: v for s, v in show.items() if isinstance(v, dict)},
                                f"{path.name} [shows.{key}]: ")
            unknown += [f"{path.name} [shows.{key}]: unknown setting {s} (a show has {' and '.join(SHOW_KEYS)})"
                        for s, v in show.items() if not isinstance(v, dict) and s not in SHOW_KEYS]
        for section, values in data.items():
            cfg.setdefault(section, {}).update(values)
    lpath = local_path(path)
    local = _load_local(lpath, cfg, base) if use_local else {}
    _apply_env(cfg, local=local)
    _resolve_paths(cfg["paths"], base)

    cfg["shows"] = {}
    for key, show in shows.items():
        chans = show.get("channels") or ([show["channel"]] if show.get("channel") else [])
        cfg["shows"][key] = {"channels": [c.lower() for c in chans],
                             "overrides": {s: v for s, v in show.items()
                                           if isinstance(v, dict)}}
    cfg["show"] = {"key": "", "channel": cfg["kick"]["channel"]}
    cfg["_dir"] = base
    cfg["_file"] = path.resolve() if path.exists() else None
    cfg["_path"] = path.resolve()  # where it is, or would be (`clipper setup` writes it)
    cfg["_local"] = local
    cfg["_local_file"] = lpath if local else None
    cfg["_resolved"] = {}
    cfg["_unknown"] = unknown
    for msg in problems(cfg):  # a typo shouldn't stop the bot; `clipper doctor` fails on these
        print(f"warning: {msg}", file=sys.stderr)
    check_choices(cfg, f"{path.name}: ")
    return cfg


def _unknown(data: dict, where: str) -> list[str]:
    out = []
    for section, values in data.items():
        if section not in DEFAULTS:
            out.append(f"{where}unknown section [{section}]")
        elif isinstance(values, dict):
            out += [f"{where}unknown setting [{section}] {k}" for k in values if k not in DEFAULTS[section]]
    return out


# ---------- shows ----------
# One clipper.toml serves several shows. Each [shows.<key>] table names the show's Kick
# channels and holds sub-tables that override the same sections for that show's streams,
# e.g. [shows.kino.meta] show_name = "Kino Casino". A stream belongs to the show whose
# channels include the "channel" in its meta.json; with no match it keeps the base settings.

def show_of_channel(cfg: dict, channel: str) -> str:
    channel = (channel or "").lower()
    return next((k for k, s in _root(cfg)["shows"].items() if channel in s["channels"]), "")


_CHANNELS: dict[str, str] = {}   # a stream's channel never changes; meta.json may be on the NAS
STREAM_CONFIG = "config.toml"    # streams/<id>/config.toml: that stream's own overrides


def stream_channel(sdir: Path) -> str:
    key = str(sdir)
    if key in _CHANNELS:
        return _CHANNELS[key]
    channel = ""
    # live/state.json: a stream the NAS recorder is still on has no meta.json yet
    for p in (sdir / "meta.json", sdir / "live" / "state.json"):
        try:
            channel = jsonstate.load(p).get("channel") or ""
        except (OSError, ValueError):
            continue
        break
    if channel:
        _CHANNELS[key] = channel
    return channel


def _root(cfg: dict) -> dict:
    return cfg.get("_root") or cfg


def for_show(cfg: dict, key: str, channel: str = "") -> dict:
    """The config with show `key`'s overrides applied (env vars still win). Cached and
    safe to call on an already resolved config; "" gives the base config."""
    root = _root(cfg)
    if not key:
        if not channel or channel == root["show"]["channel"]:
            return root
    elif key not in root["shows"]:
        raise ClipperError(f"no [shows.{key}] in the config; shows: {', '.join(root['shows'])}")
    cache_key = (key, channel)
    if cache_key in root["_resolved"]:
        return root["_resolved"][cache_key]
    out = {k: copy.deepcopy(v) for k, v in root.items() if not k.startswith("_")}
    overrides = root["shows"][key]["overrides"] if key else {}
    for section, values in overrides.items():
        out.setdefault(section, {}).update(copy.deepcopy(values))
    _apply_env(out, set(overrides), root["_local"])
    if "paths" in overrides:
        _resolve_paths(out["paths"], root["_dir"], list(overrides["paths"]))
    if key and not channel:
        channel = (root["shows"][key]["channels"] or [""])[0]
    out["kick"]["channel"] = channel or out["kick"]["channel"]
    out["show"] = {"key": key, "channel": out["kick"]["channel"]}
    out["_root"] = root
    check_choices(out, f"[shows.{key}]: " if key else "")
    root["_resolved"][cache_key] = out
    return out


def for_stream(cfg: dict, sdir: Path) -> dict:
    """The config for this stream's show, picked by the Kick channel in its meta.json, then
    the stream's own streams/<id>/config.toml if it has one (a one-off night: more picks, a
    big story in stream_context, schedule priority). Env vars still win. A config from
    pin_show (`clipper --show <key>`) keeps that show instead of the channel's (B4)."""
    res = _for_stream(cfg, Path(sdir))
    return {**res, "_pinned": True} if cfg.get("_pinned") else res


def explain(cfg: dict, name: str, sdir: Path | None = None) -> dict:
    """Where "section.key" comes from: every layer that sets it, in the order they apply
    (default < clipper.toml < show < stream config.toml < clipper.local.toml < env), and the
    value that wins, for this stream (or the config's show, or the base config)."""
    section, _, key = name.partition(".")
    if section not in DEFAULTS or key not in DEFAULTS[section]:
        import difflib
        near = difflib.get_close_matches(name, [f"{s}.{k}" for s, v in DEFAULTS.items() for k in v], 4, 0.75)
        raise ClipperError(f"no setting {name!r}" + (f"; did you mean {', '.join(near)}?" if near else ""))
    root = _root(cfg)
    resolved = for_stream(cfg, sdir) if sdir else cfg
    show = resolved["show"]["key"]
    layers = [("default", DEFAULTS[section][key])]
    if root.get("_file"):
        data = tomllib.loads(Path(root["_file"]).read_text(encoding="utf-8"))
        if key in data.get(section, {}):
            layers.append((root["_file"].name, data[section][key]))
    if show and key in root["shows"][show]["overrides"].get(section, {}):
        layers.append((f"[shows.{show}]", root["shows"][show]["overrides"][section][key]))
    if sdir and (Path(sdir) / STREAM_CONFIG).exists():
        data = tomllib.loads((Path(sdir) / STREAM_CONFIG).read_text(encoding="utf-8"))
        if key in data.get(section, {}):
            layers.append((str(Path(sdir) / STREAM_CONFIG), data[section][key]))
    if key in root["_local"].get(section, {}):
        layers.append((LOCAL_CONFIG, root["_local"][section][key]))
    env = f"CLIPPER_{section}_{key}".upper()
    if env in os.environ:
        layers.append((f"${env}", os.environ[env]))
    return {"name": name, "value": resolved[section][key], "show": show, "layers": layers}


def show_args(cfg: dict) -> list[str]:
    """`--show <key>` for a clipper child process when this config is pinned to a show."""
    return ["--show", cfg["show"]["key"]] if cfg.get("_pinned") else []


def pin_show(cfg: dict, key: str) -> dict:
    """Show `key`'s config, kept by for_stream for any stream (`clipper --show <key>`)."""
    return {**for_show(cfg, key), "_pinned": True}


def _for_stream(cfg: dict, sdir: Path) -> dict:
    channel = stream_channel(sdir)
    key = cfg["show"]["key"] if cfg.get("_pinned") else show_of_channel(cfg, channel)
    out = for_show(cfg, key, channel)
    path = sdir / STREAM_CONFIG
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return out
    root = _root(cfg)
    cache_key = ("stream", str(sdir), key, channel, mtime)
    if cache_key in root["_resolved"]:
        return root["_resolved"][cache_key]
    overrides = tomllib.loads(path.read_text(encoding="utf-8"))
    res = {k: copy.deepcopy(v) for k, v in out.items() if not k.startswith("_")}
    for section, values in overrides.items():
        unknown = [k for k in values if k not in res.get(section, {})] if isinstance(values, dict) else [""]
        if unknown:
            raise ClipperError(f"{path}: unknown setting [{section}] {', '.join(unknown)}")
        res[section].update(copy.deepcopy(values))
    _apply_env(res, set(overrides), root["_local"])
    res["_root"] = root
    check_choices(res, f"{path}: ")
    root["_resolved"][cache_key] = res
    return res
