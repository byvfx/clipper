"""Config loading: built-in defaults < clipper.toml < CLIPPER_<SECTION>_<KEY> env vars.

The file is found via --config, then $CLIPPER_CONFIG, then ./clipper.toml.
Relative paths in [paths] resolve against the config file's directory.
"""

from __future__ import annotations

import copy
import json
import os
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, dict[str, Any]] = {
    "paths": {
        "streams": "streams",
        "prompts": "prompts",
        "schedule": "schedule.json",
        "youtube_client_secret": "secrets/youtube_client_secret.json",  # OAuth Desktop client
        "youtube_token": "secrets/youtube_token.json",                  # written by --sign-in
        "discord_token": "secrets/discord_token.txt",  # bot token, if not in [discord] token_env
        "elevenlabs_key": "secrets/elevenlabs_api_key.txt",  # if not in [narrate] key_env
        "sync_key": "secrets/sync_api_key.txt",  # sync.so, if not in [perform] sync_key_env
        "tiktok_client": "secrets/tiktok_client.json",  # {"client_key", "client_secret"}
        "tiktok_token": "secrets/tiktok_token.json",    # written by `clipper tiktok --sign-in`
        "warroom_token": "secrets/warroom_token.txt",   # war room token, if not [warroom] token
        "rumble": "rumble",       # `clipper rumble` ready-to-upload folders
        "bot_log": "logs/bot.log",  # `clipper bot` log (rotated); "" = console only
        "layouts": "layouts",     # reference screenshots: <show>/<layout>.jpg (`clipper shorts --save-example`)
        "archive": "archive",     # `clipper archive` subjects: <subject>/catalog.json + one dir per item
        "youtube_cookies": "secrets/youtube_cookies.txt",  # optional, Netscape cookies.txt: `archive` age-restricted items
        "modal_runs": "logs/modal_runs.jsonl",  # every Modal call's start and end (`clipper modal`)
        "weekly": "weekly",       # `clipper weekly` issues: <saturday>/01_cover.jpg ... issue.json
        "voices": "voices",       # enrolled voices per show: <show>.json (`clipper speakers --enroll`)
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
        "output_format": "mp3_44100_128",  # wav_44100 / pcm_44100 need the Pro tier
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
        "prompt_version": "v2",   # v2: quotes credited to who said them (speaker names, #52)
        "write_articles": True,   # after a draft, write the three articles again, one call each...
        "article_prompt_version": "v2",  # ...with prompts/weekly_article_<v>.md...
        "article_example": "weekly_article_example.md",  # ...and this piece (in prompts/) as the voice to match
        "quote_match": 0.75,      # a hook this close to word for word in the Short is credited to its speaker
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
        "condensed_fonts": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/roboto/RobotoCondensed-Bold.ttf",
                            "/Library/Fonts/RobotoCondensed-Bold.ttf"],
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
        "link_updates_per_run": 20,  # description updates per run (50 quota units each)
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
        "stages": ["signals", "transcribe", "score", "segments", "cut", "meta", "thumbs", "shorts",
                   "review"],
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
        "trim_end_s": 3.0,        # drop this much off the end (2026-10-23, user); 0 = whole video
        "publish": "asap",        # asap: out as soon as it is approved and uploaded | slots
        "band": "Pre-show {date}!",  # cover's green band ({date} = M/D aired); "" = the model's line
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
        "chat_tail_s": 15,        # keep chat this long past a part's end (chat trails video)
        "on_finish": "",          # shell command after a recording is finalized;
                                  # {stream_id} {stream_dir} {channel}
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
                                  # needs a Hugging Face token (SETUP); `clipper speakers` names them
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
        "prompt_version": "v1",   # prompts/thumbnail_<v>.md
        "concurrency": 4,
        "clip_frames": 8,         # candidates per clip, around its hook and payoff
        "even_frames": 5,         # segments/compilations: evenly spaced candidates...
        "heat_frames": 5,         # ...plus this many chat-heat peaks
        "min_gap_s": 20,          # between candidate frames of one asset
        "min_brightness": 28,     # mean luma 0-255; darker frames are skipped
        "min_sharpness": 150.0,   # variance of the Laplacian; blurrier frames are skipped
        "min_crop": 0.4,          # the model may zoom in to a 16:9 box this wide (share of the frame)
        "max_words": 4,
        "font": "",               # TTF/OTF path; empty = first of fonts that exists
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
        "enquirer_prompt_version": "v3",  # prompts/thumbnail_enquirer_<v>.md
        "masthead": "",           # empty = meta.show_name
        "masthead_tagline": "LARGEST CIRCULATION OF ANY STREAM ON KICK",
        "masthead_price": "$1.39",
        "badge_kicker": "AS SEEN ON",
        "badge_channel": "",      # empty = scoring.channel_name; the small badge on the photo
        "condensed_fonts": ["C:/Windows/Fonts/bahnschrift.ttf",
                            "/usr/share/fonts/truetype/roboto/unhinted/RobotoCondensed-Bold.ttf",
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
        "window": "clip",         # clip: the pick's in/out | short: scoring's 15-60 s short window
        "min_s": 90,              # widen shorter windows by whole transcript lines to this
        "max_s": 179,             # longest Short (YouTube files vertical videos up to 3:00 as Shorts)
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
        "auto_hours": 24,         # `clipper bot` refreshes it this often (once there are uploads); 0 = off
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
    "warroom": {
        # `clipper warroom`: the local dashboard (schedule, pipeline, review queue, retakes).
        "host": "127.0.0.1",      # 0.0.0.0 to open it to the LAN (then set token)
        "port": 8765,
        "token": "",              # required off localhost: open it as http://host:port/?key=<token>
        "with_bot": True,         # `clipper bot` serves it too, so it's up whenever the bot runs
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


def _coerce(raw: str, like: Any) -> Any:
    if isinstance(like, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(raw)
    if isinstance(like, float):
        return float(raw)
    return raw


def stream_dir(cfg: dict, ref: str) -> Path:
    """A stream id under paths.streams, or a stream directory."""
    p = Path(ref)
    return p if p.is_dir() else cfg["paths"]["streams"] / ref


# Keys of a loaded config that aren't [section] tables: "shows" (the [shows.<key>]
# profiles), "show" (which one this config is resolved for) and private bookkeeping.
NOT_SECTIONS = ("shows", "show")


def _sections(cfg: dict):
    return ((s, v) for s, v in cfg.items() if s not in NOT_SECTIONS and not s.startswith("_"))


def _apply_env(cfg: dict, only: set[str] | None = None) -> None:
    for section, values in _sections(cfg):
        if only is not None and section not in only:
            continue
        for key, value in values.items():
            env = os.environ.get(f"CLIPPER_{section}_{key}".upper())
            if env is not None:
                values[key] = _coerce(env, value)


def _resolve_paths(paths: dict, base: Path, keys=None) -> None:
    for key in keys if keys is not None else list(paths):
        p = Path(paths[key]).expanduser()
        paths[key] = p if p.is_absolute() else base / p


def load(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path or os.environ.get("CLIPPER_CONFIG") or "clipper.toml")
    base = Path.cwd()
    shows: dict[str, dict] = {}
    if path.exists():
        base = path.resolve().parent
        with open(path, "rb") as f:
            data = tomllib.load(f)
        shows = data.pop("shows", {})
        for section, values in data.items():
            cfg.setdefault(section, {}).update(values)
    _apply_env(cfg)
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
    cfg["_resolved"] = {}
    return cfg


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
    try:
        channel = json.loads((sdir / "meta.json").read_text(encoding="utf-8")).get("channel") or ""
    except (OSError, ValueError):
        return ""
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
        raise SystemExit(f"no [shows.{key}] in the config; shows: {', '.join(root['shows'])}")
    cache_key = (key, channel)
    if cache_key in root["_resolved"]:
        return root["_resolved"][cache_key]
    out = {k: copy.deepcopy(v) for k, v in root.items() if not k.startswith("_")}
    overrides = root["shows"][key]["overrides"] if key else {}
    for section, values in overrides.items():
        out.setdefault(section, {}).update(copy.deepcopy(values))
    _apply_env(out, set(overrides))
    if "paths" in overrides:
        _resolve_paths(out["paths"], root["_dir"], list(overrides["paths"]))
    if key and not channel:
        channel = (root["shows"][key]["channels"] or [""])[0]
    out["kick"]["channel"] = channel or out["kick"]["channel"]
    out["show"] = {"key": key, "channel": out["kick"]["channel"]}
    out["_root"] = root
    root["_resolved"][cache_key] = out
    return out


def for_stream(cfg: dict, sdir: Path) -> dict:
    """The config for this stream's show, picked by the Kick channel in its meta.json, then
    the stream's own streams/<id>/config.toml if it has one (a one-off night: more picks, a
    big story in stream_context, schedule priority). Env vars still win."""
    sdir = Path(sdir)
    channel = stream_channel(sdir)
    out = for_show(cfg, show_of_channel(cfg, channel), channel)
    path = sdir / STREAM_CONFIG
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return out
    root = _root(cfg)
    cache_key = ("stream", str(sdir), channel, mtime)
    if cache_key in root["_resolved"]:
        return root["_resolved"][cache_key]
    overrides = tomllib.loads(path.read_text(encoding="utf-8"))
    res = {k: copy.deepcopy(v) for k, v in out.items() if not k.startswith("_")}
    for section, values in overrides.items():
        unknown = [k for k in values if k not in res.get(section, {})] if isinstance(values, dict) else [""]
        if unknown:
            raise SystemExit(f"{path}: unknown setting [{section}] {', '.join(unknown)}")
        res[section].update(copy.deepcopy(values))
    _apply_env(res, set(overrides))
    res["_root"] = root
    root["_resolved"][cache_key] = res
    return res
