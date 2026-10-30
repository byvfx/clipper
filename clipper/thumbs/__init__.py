"""Thumbnails: one 1280x720 JPEG per cut asset (clips/, segments/, compilations/).

  frames    ffmpeg grabs candidate frames: clips around their hook (short_in_s) and payoff
            (short_out_s) plus chat-heat peaks inside the clip; segments and compilations
            evenly spaced plus their hottest chat moments (signals.parquet). Black or
            blurry frames are nudged a little, then skipped.
  pick      one vision call per asset: the frame with the strongest readable expression or
            moment, 2-4 words of text, the word to highlight, and top/bottom placement
  classic   Pillow: the frame graded and shaded, the text in the [thumbnail] brand style
            (font, colors, outline, shadow, badge or logo, stripe), under max_bytes
  enquirer  the tabloid cover ([thumbnail] style = "enquirer"), its layout in data that a
            show can change in <paths.layouts>/<show>/enquirer.toml
  stage     `clipper thumbs`, and reviewers' text and photo changes
  editor    the war room's cover editor
  (the Pillow helpers they share are clipper/draw.py)

Writes <video>.jpg beside each video, and thumbnails.json in the stream dir with every
choice (frame time, text, why). To change one by hand, edit its "text", "highlight",
"position" or "frame" there and rerun with --compose-only (no model calls); the same
flag re-renders everything after changing the brand settings. Text is checked against
[bleep] patterns and never printed with a listed word. Reviewers change the text without
touching files through the Kitsu "Thumbnail text" column or Discord's Thumbnail text button;
`clipper review sync` writes it here and re-renders (set_texts).

Enquirer covers can use [thumbnail] photos ("<Person>.jpg" portraits) for people talked
about but not on screen: the model is offered the names and may set "photo" (the main
face) or "teaser_photo" (shown beside this video's teaser on the other covers);
`--photo` / `--teaser-photo` set them by hand (set_photos).

Shorts get a 9:16 version of their clip's enquirer cover beside them (shorts/<name>.jpg):
compose_vertical, from the clip's thumbnails.json choice, no model calls. `clipper thumbs`
draws them with the clip covers, `clipper shorts` right after rendering a Short, and
`clipper thumbs <stream> --shorts` draws just those (e.g. for streams made before this).

`clipper upload` sets the .jpg as the YouTube thumbnail; with [thumbnail] kitsu on, it is
attached to each item's latest Kitsu revision and becomes the item's Kitsu thumbnail.
"""

from .editor import cover_info, edit_cover
from .frames import photos
from .stage import check_text, run, set_photos, set_texts, shorts_only, video_key

__all__ = ["check_text", "cover_info", "edit_cover", "photos", "run", "set_photos", "set_texts",
           "shorts_only", "video_key"]
