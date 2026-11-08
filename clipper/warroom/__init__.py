"""The war room: a local web page with everything at once.

Usage:
    clipper warroom [--port 8765] [--open]

Serves http://127.0.0.1:<port>/ ([warroom] host/port): which shows are live on Kick, each
stream's pipeline progress, the review queue with previews and thumbnails (approve, ask for
changes, reject, hold, pin, edit title / description / hashtags / thumbnail words), the
release schedule, retakes, the services' health and who is who on screen (#54: a stream's face
groups to name, and a box to draw round a face on a paused video). Decisions go through clipper/actions.py,
so they land in Kitsu exactly like the CLI's and the Discord bot's (Kitsu stays the
record); a "changes" decision starts `clipper retake` in the background, like the bot.

It reads the files the pipeline writes (review.json is as fresh as the last sync: the bot's
card refresh and the hourly replan sync, and the page's Sync button syncs now). `clipper
serve` (clipper/serve.py) runs it together with the background jobs; `clipper warroom` runs
the page alone. Only listens on localhost unless [warroom] host is changed, which then
needs [warroom] token.

One package since 2026-11-08 (audit tier 1): the page's data (data), the decisions (review),
the panels (covers, faces, cast, search, news, investigations, saved_layouts), low-res copies
(lowres), the tailscale link and the HTTP server (httpd); `state` holds what they share. Every public name is re-exported here, so
`warroom.<name>` is the same whichever module defines it."""

from .cast import (
    CAST_JOB,
    CAST_LOCK,
    cast_get,
    cast_label,
    cast_mark,
    cast_run,
    cast_set,
)
from .covers import cover_get, cover_key, cover_post
from .data import (
    CACHE,
    CACHE_LOCK,
    CACHE_MAX_AGE_S,
    LIVE_CACHE,
    all_rows,
    analytics_status,
    costs_status,
    for_stream_show_name,
    health,
    kick_live,
    last_upload_run,
    media_url,
    modal_status,
    overview,
    retakes,
    row_json,
    shown_shows,
    stream_info,
    stream_infos,
    stream_rows,
    upload_status,
    usage_status,
    views_by_video,
)
from .faces import (
    GROUP_KEY,
    checked_box,
    faces_enroll,
    faces_forget,
    faces_get,
    faces_name,
    faces_pick,
    stream_path,
)
from .httpd import (
    CODE,
    GET_API,
    LOCAL_HOSTS,
    MAX_POST,
    PAGE,
    POST_API,
    STATIC,
    WEB,
    Handler,
    make_handler,
    run,
    server,
    static_file,
    url,
    watch_page,
)
from .investigations import investigations_get
from .lowres import (
    LOWRES,
    LOWRES_COND,
    lowres_path,
    lowres_ready,
    lowres_status,
    lowres_want,
    render_lowres,
)
from .news import news_context, news_dismiss, news_get, news_search
from .review import (
    BULK,
    COLUMN_OF,
    REF,
    REPLANS,
    ROW_OF,
    SLOT_KINDS,
    UNDO_MAX,
    checked_ref,
    do_action,
    do_bulk,
    do_replace,
    do_slot,
    do_undo,
    recent_actions,
    remember,
    replace_plan,
    slot_plan,
    slot_print,
    start_retake,
    sync_all,
    undo_path,
)
from .saved_layouts import LAYOUT_ZONES, layouts, save_layout
from .search import SRT_CACHE, search_transcripts, srt_text
from .state import HOOKS, LOCK, SYNC, TAILNET, WHO, token
from .tailscale import tailnet, tailnet_status, tailscale_exe

__all__ = ["HOOKS", "LOCK", "SYNC", "TAILNET", "WHO", "token", "CACHE", "CACHE_LOCK", "CACHE_MAX_AGE_S", "LIVE_CACHE", "all_rows", "analytics_status", "costs_status", "for_stream_show_name", "health", "kick_live", "last_upload_run", "media_url", "modal_status", "overview", "retakes", "row_json", "shown_shows", "stream_info", "stream_infos", "stream_rows", "upload_status", "usage_status", "views_by_video", "BULK", "COLUMN_OF", "REF", "REPLANS", "ROW_OF", "SLOT_KINDS", "UNDO_MAX", "checked_ref", "do_action", "do_bulk", "do_replace", "do_slot", "do_undo", "recent_actions", "remember", "replace_plan", "slot_plan", "slot_print", "start_retake", "sync_all", "undo_path", "LAYOUT_ZONES", "layouts", "save_layout", "cover_get", "cover_key", "cover_post", "GROUP_KEY", "checked_box", "faces_enroll", "faces_forget", "faces_get", "faces_name", "faces_pick", "stream_path", "CAST_JOB", "CAST_LOCK", "cast_get", "cast_label", "cast_mark", "cast_run", "cast_set", "SRT_CACHE", "search_transcripts", "srt_text", "news_context", "news_dismiss", "news_get", "news_search", "investigations_get", "LOWRES", "LOWRES_COND", "lowres_path", "lowres_ready", "lowres_status", "lowres_want", "render_lowres", "tailnet", "tailnet_status", "tailscale_exe", "CODE", "GET_API", "Handler", "LOCAL_HOSTS", "MAX_POST", "PAGE", "POST_API", "STATIC", "WEB", "make_handler", "run", "server", "static_file", "url", "watch_page"]
