"""The week: which streams started in it -> week.json (segments, hooks with who said them,
faces) and stats.json (hours, chat, emotes, screen time, word counts); next week's shows."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import jsonstate
from ..errors import ClipperError
from ..signals import EMOTE

LAUGH = re.compile(r"\b(lol|lmao|lmfao|kek|rofl|haha+|omegalul)\b|😂|🤣", re.IGNORECASE)
FWORD = re.compile(r"\bf+u+c+k", re.IGNORECASE)
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


# ---------- week ----------

def week_of(cfg: dict, day: str | None) -> tuple[datetime, datetime]:
    """[start, end) of the Sun-Sat week holding `day` (default today), in [schedule] timezone."""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz) if day else datetime.now(tz)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    start = d - timedelta(days=(d.weekday() + 1) % 7)   # back to Sunday
    return start, start + timedelta(days=7)


def issue_dir(cfg: dict, start: datetime) -> Path:
    return Path(cfg["paths"]["weekly"]) / (start + timedelta(days=6)).strftime("%Y-%m-%d")


def week_label(start: datetime) -> str:
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b').upper()} {start.day}-{end.day}, {end.year}"
    return f"{start.strftime('%b').upper()} {start.day} - {end.strftime('%b').upper()} {end.day}, {end.year}"


def next_issue_number(cfg: dict, here: Path) -> int:
    nums = []
    for p in Path(cfg["paths"]["weekly"]).glob("*/issue.json"):
        if p.parent != here:
            try:
                nums.append(int(jsonstate.load(p).get("issue") or 0))
            except (OSError, ValueError):
                pass
    return max(nums + [cfg["weekly"]["first_issue"] - 1]) + 1


# ---------- gather ----------

def _load(p: Path, default=None):
    try:
        return jsonstate.load(p)
    except (OSError, ValueError):
        return default


def chat_stats(sdir: Path, meta: dict, hours: float) -> dict:
    p = sdir / "chat.jsonl"
    if not p.exists():
        return {}
    t0 = datetime.fromisoformat(meta["started_at"]).timestamp()
    n, per_min, emotes, laughs, users = 0, Counter(), Counter(), 0, set()
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("type", "message") != "message":
                continue
            t = m.get("t", m.get("ts", t0) - t0)
            if not 0 <= t <= hours * 3600 + 600:
                continue
            n += 1
            per_min[int(t // 60)] += 1
            users.add(m.get("user"))
            c = m.get("content") or ""
            emotes.update(EMOTE.findall(c))
            if LAUGH.search(EMOTE.sub("", c)):
                laughs += 1
    peak_min, peak = max(per_min.items(), key=lambda kv: kv[1]) if per_min else (0, 0)
    return {"messages": n, "chatters": len(users), "laughs": laughs, "peak_per_min": peak,
            "peak_at_s": peak_min * 60, "emotes": dict(emotes.most_common(8))}


def load_words(sdir: Path) -> list[dict]:
    p = sdir / "words.jsonl"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                w = json.loads(line)
            except ValueError:
                continue
            if "word" in w:
                out.append(w)
    return out


def word_counts(spoken: list[dict], words: list[str]) -> tuple[dict, int]:
    if not spoken:
        return {}, 0
    joined = " ".join(w["word"] for w in spoken).lower()
    counts = {w: len(re.findall(r"\b" + re.escape(w.lower()) + r"\b", joined)) for w in words}
    counts["_fuck"] = len(FWORD.findall(joined))
    return counts, len(spoken)


TOKEN = re.compile(r"[a-z0-9']+")
UNNAMED = re.compile(r"SPEAKER_\d+")


def said_by(spoken: list[dict], hook: str, a: float, b: float, min_match: float) -> str:
    """Who said the hook, when it is (nearly) word for word in [a, b) and the stream has
    speaker names (#52): the name, "?" for a voice nobody named; "" when it is not there."""
    want = Counter(TOKEN.findall(hook.lower()))
    n = sum(want.values())
    toks = [(t, w.get("speaker")) for w in spoken if a <= w["start"] < b
            for t in TOKEN.findall(w["word"].lower())]
    if n < 3 or not any(s and not UNNAMED.fullmatch(s) and s != "music" for _, s in toks):
        return ""
    best, at = 0.0, 0
    for i in range(len(toks)):
        got = sum((Counter(t for t, _ in toks[i:i + n]) & want).values()) / n
        if got > best:
            best, at = got, i
    who = Counter(s for _, s in toks[at:at + n] if s).most_common(1)
    if best < min_match or not who or who[0][0] == "music":
        return ""
    return "?" if UNNAMED.fullmatch(who[0][0]) else who[0][0]


def faces_of(sdir: Path) -> list[dict]:
    """Every (person, frame, face box) the thumbnail stage recorded, largest faces first."""
    t = _load(sdir / "thumbnails.json", {}) or {}
    out = []
    for key, e in t.items():
        person, face = e.get("main_person"), e.get("face")
        frames = e.get("frames") or []
        if not (person and face and frames) or e.get("frame") is None:
            continue
        try:
            path = sdir / "thumbs" / key / frames[e["frame"]]["path"]
        except (IndexError, KeyError, TypeError):
            continue
        if face["width"] > 0.36 or face["height"] > 0.55:
            continue  # a whole video region, not a face
        out.append({"person": person, "key": key, "frame": str(path), "face": face,
                    "size": round(face["height"], 3), "photo": e.get("photo") or ""})
    out.sort(key=lambda f: -f["size"])
    return out


def found_faces(sdir: Path, here: Path, n: int) -> list[dict]:
    """faces.json (`clipper faces`): each named person's n best faces in the stream, the frame
    grabbed from the source into <issue>/faces/ once. Marked "found": the face model named
    them, so the pages take them before the thumbnail stage's guesses."""
    from ..faces import best_faces, grab, head_box
    from ..media import source_file

    data = _load(sdir / "faces.json") if n else None
    if not data:
        return []
    people = sorted({g["name"] for g in data["groups"].values() if g.get("name")})
    src: Path | None = None
    out = []
    for person in people:
        for f in best_faces(data, person, n=n):
            dst = here / "faces" / f"{sdir.name}_{f['t']}.jpg"  # the stored time as is: keyframes can be 0.03 s apart
            if not dst.exists():
                try:
                    src = src or source_file(sdir)  # only when a frame isn't kept yet: the source may be gone
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    grab(src, f["t"], dst)
                except ClipperError as e:
                    print(f"  {sdir.name}: {e}")
                    continue
            face = head_box(f["box"])  # the whole frame is kept, so no 16:9 crop to allow for
            # Two people can share a frame: the key names the group too, so a pin finds the right face.
            out.append({"person": person, "key": f"faces-{f['group']}@{f['t']}", "frame": str(dst), "face": face,
                        "size": round(face["height"], 3), "photo": "", "found": True})
    return out


def gather(cfg: dict, start: datetime, end: datetime, here: Path) -> tuple[dict, dict]:
    root = Path(cfg["paths"]["streams"])
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    wc = cfg["weekly"]
    streams: dict[str, dict] = {}
    for sdir in sorted(root.iterdir()):
        meta = _load(sdir / "meta.json")
        if not meta or not meta.get("started_at"):
            continue
        at = datetime.fromisoformat(meta["started_at"]).astimezone(tz)
        if not start <= at < end:
            continue
        seg = _load(sdir / "segments.json", {}) or {}
        review = _load(sdir / "review.json", {}) or {}
        cands = _load(sdir / "candidates.json", {}) or {}
        hours = (meta.get("duration_s") or 0) / 3600
        preshow = sdir.name.startswith("yt-")
        show = next((k for k, s in cfg["shows"].items() if (meta.get("channel") or "").lower() in s["channels"]), "")
        show_name = cfg["shows"][show]["overrides"].get("meta", {}).get("show_name", show) if show else meta.get("channel", "")
        segments = []
        for x in seg.get("segments", []):
            if preshow and len(seg.get("segments", [])) == 1:
                x = {**x, "topic": "Pre-show"}
            segments.append({"id": x["id"], "topic": x.get("topic") or "", "title": x.get("title", ""),
                             "summary": x.get("summary", ""), "figures": x.get("figures", []),
                             "kind": x.get("kind", ""), "duration_s": round(x.get("duration_s", 0), 1),
                             "cut": x.get("cut", True)})
        uploads = {k: v["fields"].get("YouTube URL") for k, v in review.items() if v.get("fields", {}).get("YouTube URL")}
        spoken = load_words(sdir)
        found = {c["id"]: c for c in cands.get("pass_a") or [] if "id" in c}
        hooks = []
        for p in (cands.get("pass_b") or {}).get("picks", []):
            if not p.get("hook"):
                continue
            c = found.get(p["id"], {})
            ts = [c[k] for k in ("in_s", "out_s", "short_in_s", "short_out_s") if c.get(k)]
            by = said_by(spoken, p["hook"], min(ts) - 5, max(ts) + 5, wc["quote_match"]) if ts else ""
            hooks.append({"id": p["id"], "hook": p["hook"], "title": p.get("title", ""), "said_by": by})
        shorts = [k for k, v in review.items() if v.get("kind") == "short"]
        counts, n_words = word_counts(spoken, wc["words"])
        streams[sdir.name] = {
            "show": show, "show_name": show_name, "channel": meta.get("channel"),
            "preshow": preshow, "started_at": at.isoformat(), "day": DAYS[at.weekday()],
            "date": at.strftime("%b %d"), "hours": round(hours, 2), "title": meta.get("title", ""),
            "segments": segments, "compilations": [{"topic": c.get("topic"), "title": c.get("title")}
                                                    for c in seg.get("compilations", [])],
            "shorts": len(shorts), "uploaded": len(uploads), "hooks": hooks,
            "chat": chat_stats(sdir, meta, hours) if not preshow else {},
            "words": counts, "spoken_words": n_words,
            "faces": found_faces(sdir, here, wc["face_frames"]) + faces_of(sdir),
        }
        print(f"  {sdir.name}: {show_name} {at:%a %b %d}, {hours:.1f} h, {len(segments)} segments, "
              f"{len(hooks)} hooks, {len(streams[sdir.name]['faces'])} faces")
    if not streams:
        raise ClipperError(f"no streams started {start:%Y-%m-%d} to {end:%Y-%m-%d} under {root}")
    week = {"start": start.strftime("%Y-%m-%d"), "end": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
            "label": week_label(start), "streams": streams}
    return week, stats_of(week)


def stats_of(week: dict) -> dict:
    live = {k: s for k, s in week["streams"].items() if not s["preshow"]}
    pre = {k: s for k, s in week["streams"].items() if s["preshow"]}
    minutes: Counter = Counter()
    for s in live.values():
        for x in s["segments"]:
            if x["kind"] != "intro" and x["topic"]:
                minutes[x["topic"]] += x["duration_s"] / 60
    words: Counter = Counter()
    for s in week["streams"].values():
        words.update(s["words"])
    emotes: Counter = Counter()
    chat_n = laughs = chatters = 0
    peak = (0, "", 0)
    for k, s in live.items():
        c = s.get("chat") or {}
        chat_n += c.get("messages", 0)
        laughs += c.get("laughs", 0)
        chatters += c.get("chatters", 0)
        emotes.update(c.get("emotes", {}))
        if c.get("peak_per_min", 0) > peak[0]:
            peak = (c["peak_per_min"], k, c.get("peak_at_s", 0))
    hours = sum(s["hours"] for s in live.values())
    fucks = words.pop("_fuck", 0)
    spoken = sum(s["spoken_words"] for s in week["streams"].values())
    return {
        "shows": len(live), "preshows": len(pre), "hours": round(hours, 2),
        "hours_with_preshows": round(hours + sum(s["hours"] for s in pre.values()), 2),
        "segments": sum(len(s["segments"]) for s in live.values()),
        "shorts": sum(s["shorts"] for s in week["streams"].values()),
        "chat_messages": chat_n, "chatters": chatters, "laughs": laughs,
        "peak_per_min": peak[0], "peak_stream": peak[1], "peak_at_s": peak[2],
        "top_emotes": emotes.most_common(5),
        "screen_minutes": {t: round(m, 1) for t, m in minutes.most_common()},
        "spoken_words": spoken, "fucks": fucks,
        "fuck_every_s": round(hours * 3600 / fucks) if fucks else 0,
        "said": {w: n for w, n in words.most_common()},
    }


def hm(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def next_week(cfg: dict, week: dict) -> list[tuple[str, str, str, str]]:
    """Next week's shows: this week's live streams' weekdays and start times, a week later."""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    rows = []
    for s in week["streams"].values():
        if s["preshow"]:
            continue
        at = datetime.fromisoformat(s["started_at"]).astimezone(tz) + timedelta(days=7)
        minute = 30 if at.minute >= 15 else 0  # shows start "at" a round time
        if at.minute >= 45:
            at += timedelta(hours=1)
            minute = 0
        at = at.replace(minute=minute)
        zone = at.strftime("%Z").replace("PDT", "PT").replace("PST", "PT")
        rows.append((at, DAYS[at.weekday()], at.strftime("%b %d").replace(" 0", " "), s["show_name"],
                     f"{at.strftime('%I:%M %p').lstrip('0')} {zone}"))
    rows.sort()
    return [r[1:] for r in rows]

