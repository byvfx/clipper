"""The news panel (#110): stories about the shows' people, search, dismiss, context for a stream."""
from __future__ import annotations

from ..errors import ClipperError


def news_get(cfg: dict, days: str | float = "") -> dict:
    """The last few days' stories (dismissed ones too, marked), the shows, each source's state."""
    from .. import news

    try:
        d = min(max(float(days or 3), 0.5), 30)
    except ValueError:
        d = 3
    return {"stories": news.stories(cfg, d, everything=True), "shows": news.show_names(cfg),
            "feeds": news.feeds(cfg), "check_minutes": cfg["news"]["check_minutes"],
            "watching": sorted(w.name for w in news.watchlist(cfg).values())}

def news_search(cfg: dict, q: str) -> dict:
    from .. import news

    if not q.strip():
        return {"hits": []}
    return {"hits": news.search(cfg, q.strip()[:200])[:60]}

def _news_ids(body: dict) -> list[int]:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise ClipperError("news: ids is a list of story numbers")
    return ids

def news_dismiss(cfg: dict, body: dict) -> dict:
    from .. import news

    news.dismiss(cfg, _news_ids(body))
    return news_get(cfg, body.get("days") or "")

def news_context(cfg: dict, body: dict) -> dict:
    """{ids, show?, preview?, days?}: preview returns the text, the stories it covers and the show
    without queueing it."""
    from .. import news

    ids = _news_ids(body)
    if body.get("preview"):
        found = news.stories(cfg, ids=ids)
        text, used = news.context_block(found)
        return {"show": str(body.get("show") or (news.story_show(cfg, found[0]) if found else "")),
                "text": text, "ids": used}
    e = news.set_context(cfg, ids, str(body.get("show") or ""), who="war room")
    return {**news_get(cfg, body.get("days") or ""), "added": e}
