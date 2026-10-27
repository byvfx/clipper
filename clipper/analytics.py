"""YouTube performance of uploaded videos, fed back into scoring.

Usage:
    clipper analytics             refresh analytics.json for every uploaded schedule entry
    clipper analytics --sign-in   once: grant read-only YouTube Analytics (adds 7-day views,
                                  average view percentage, watch time, subscribers gained)
    clipper analytics --list      print what's in analytics.json, best first

Views, likes and comments come from the YouTube Data API with the upload sign-in. Once a
video is 7 days old, its first-7-days numbers come from the YouTube Analytics API (needs
--sign-in once) and stay fixed. analytics.json sits next to schedule.json.

Scoring (Pass B) gets a short "how past uploads did" block per show once at least
[analytics] min_videos of that show's uploads have 7-day numbers: the best and worst by
7-day views, with their category and score, as a hint about what this audience watches.
`clipper bot` refreshes it every [analytics] auto_hours.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import jsonstate
from .config import _root, for_stream

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


def path_of(cfg: dict) -> Path:
    return Path(cfg["paths"]["schedule"]).with_name("analytics.json")


def load(cfg: dict) -> dict:
    p = path_of(cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def credentials(cfg: dict, sign_in: bool = False):
    from google.oauth2.credentials import Credentials

    from . import youtube

    paths = cfg["paths"]
    if sign_in:  # the upload scopes plus analytics, so one token serves both
        scopes = youtube.SCOPES
        youtube.SCOPES = [*scopes, ANALYTICS_SCOPE]
        try:
            return youtube.credentials(paths, sign_in=True)
        finally:
            youtube.SCOPES = scopes
    creds = youtube.credentials(paths)
    granted = json.loads(Path(paths["youtube_token"]).read_text(encoding="utf-8")).get("scopes") or []
    if ANALYTICS_SCOPE in granted:
        creds = Credentials.from_authorized_user_file(str(paths["youtube_token"]))
    return creds, ANALYTICS_SCOPE in granted


def entry_details(cfg: dict, e: dict) -> dict:
    sdir = cfg["paths"]["streams"] / e["stream"]
    key = e["key"].split("/", 1)[1]
    fields = {}
    rp = sdir / "review.json"
    if rp.exists():
        fields = (json.loads(rp.read_text(encoding="utf-8")).get(key) or {}).get("fields") or {}
    sc = for_stream(cfg, sdir) if sdir.exists() else cfg
    return {"show": sc["show"]["key"], "show_name": sc["meta"]["show_name"],
            "category": fields.get("Category") or "", "score": fields.get("Score") or "",
            "topic": fields.get("Topic") or ""}


def run(cfg: dict, sign_in: bool = False) -> dict:
    from googleapiclient.discovery import build

    cfg = _root(cfg)
    if sign_in:
        credentials(cfg, sign_in=True)
        print("signed in with YouTube Analytics access")
    creds, has_analytics = credentials(cfg)
    sched = json.loads(Path(cfg["paths"]["schedule"]).read_text(encoding="utf-8")) \
        if Path(cfg["paths"]["schedule"]).exists() else {}
    up = [e for e in sched.get("entries", []) if e.get("status") == "uploaded" and e.get("video_id")]
    data = load(cfg)
    if not up:
        print("no uploaded videos yet")
        return data
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False) if has_analytics else None
    now = datetime.now(UTC)
    by_id = {e["video_id"]: e for e in up}
    ids = list(by_id)
    for i in range(0, len(ids), 50):
        res = yt.videos().list(part="statistics,snippet", id=",".join(ids[i:i + 50])).execute()
        for v in res.get("items", []):
            e = by_id[v["id"]]
            st = v.get("statistics", {})
            rec = data.setdefault(v["id"], {})
            rec.update({"key": e["key"], "stream": e["stream"], "kind": e["kind"],
                        "title": v["snippet"]["title"], "publish_at": e["publish_at"],
                        "url": e.get("url") or f"https://youtu.be/{v['id']}",
                        "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                        "comments": int(st.get("commentCount", 0)),
                        "checked_at": now.isoformat(timespec="seconds"), **entry_details(cfg, e)})
    gone = [vid for vid in ids if vid in data and data[vid].get("checked_at") != now.isoformat(timespec="seconds")]
    for vid in gone:
        data[vid]["missing"] = True  # deleted or private on YouTube
    if ya:
        for vid, rec in data.items():
            if rec.get("d7") or vid not in by_id:
                continue
            pub = datetime.fromisoformat(rec["publish_at"]).astimezone(UTC).date()
            if (now.date() - pub).days < 8:  # Analytics lags ~2 days; wait for a full week
                continue
            r = ya.reports().query(
                ids="channel==MINE", startDate=pub.isoformat(),
                endDate=(pub + timedelta(days=6)).isoformat(), filters=f"video=={vid}",
                metrics="views,estimatedMinutesWatched,averageViewDuration,"
                        "averageViewPercentage,subscribersGained,likes").execute()
            if r.get("rows"):
                cols = [h["name"] for h in r["columnHeaders"]]
                rec["d7"] = dict(zip(cols, r["rows"][0]))
    p = path_of(cfg)
    jsonstate.write(p, data)
    d7 = sum(1 for r in data.values() if r.get("d7"))
    print(f"{len(data)} videos in {p.name} ({d7} with 7-day numbers"
          + ("" if has_analytics else "; run `clipper analytics --sign-in` for those") + ")")
    return data


def show(cfg: dict) -> None:
    data = load(_root(cfg))
    rows = sorted(data.values(), key=lambda r: -((r.get("d7") or {}).get("views") or r.get("views", 0)))
    for r in rows:
        d7 = r.get("d7") or {}
        print(f"{(d7.get('views') or r['views']):>7} views{' (7 d)' if d7 else '      '}"
              f"  {d7.get('averageViewPercentage', 0):5.1f}%  {r['kind']:<11} {r.get('show', ''):<6} "
              f"{r['title'][:70]}")
    if not rows:
        print("analytics.json is empty; run clipper analytics")


def feedback_block(cfg: dict, show_key: str) -> str:
    """For Pass B: best and worst of this show's uploads by 7-day views, or "" when there
    are fewer than [analytics] min_videos with 7-day numbers."""
    ac = cfg["analytics"]
    rows = [r for r in load(_root(cfg)).values()
            if r.get("d7") and not r.get("missing") and r.get("show") == show_key
            and r["kind"] in ("clip", "short")]
    if len(rows) < ac["min_videos"]:
        return ""
    rows.sort(key=lambda r: -r["d7"].get("views", 0))
    n = ac["examples"]
    pick = rows[:n] + [r for r in rows[-n:] if r not in rows[:n]]

    def line(r: dict) -> str:
        d = r["d7"]
        return (f"- {d.get('views', 0)} views, {d.get('averageViewPercentage', 0):.0f}% watched "
                f"({r['kind']}, {r.get('category') or '?'}, score {r.get('score') or '?'}): {r['title']}")

    return ("<past_performance>\nHow this show's recent uploads did on YouTube in their first "
            "7 days (best first, then the weakest). A hint about what this audience watches, "
            "not a rule: still judge each candidate on its own.\n"
            + "\n".join(line(r) for r in pick) + "\n</past_performance>\n\n")
