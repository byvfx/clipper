"""`clipper people`: one record per person, for story, investigate and the vault (#138, part 1).

    clipper people list                       everyone, and which lists each record comes from
    clipper people show <name>                one record: names, channels, archive subject, cast folder
    clipper people set <name> [--name N] [--alias A]... [--unalias A]... [--channel PLATFORM=ID]...
                              [--unchannel PLATFORM=ID]... [--subject S] [--cast NAME] [--notes TEXT]
    clipper people import [--dry-run]         write the merged view of everyone into people.json

A record is {name, aliases, channels: {platform: [id, ...]}, subject, cast, notes}, keyed by a
slug. The platforms are PLATFORMS ("youtube_live" and "youtube_2" in streamers.json are YouTube).
Everything in channels is confirmed: typed by hand or from a hand-kept list, never guessed by a
model (a channel the swarm finds waits for approval, #138 part 3).

[paths] people (people.json) is the record. load() also folds in what the older lists know, so
nothing has to move at once: research_notes/streamers.json ([paths] streamers: streamers and
uploads_only), [story] people and the cast library's folders ([thumbnail] photos: aliases,
channels, the folder as `cast`). Entries are one person when any of their names match (names():
the name, the words in its brackets, the slug and the aliases, compared by investigate's
canon()); a field people.json sets wins, and lists are joined. An archive subject named like
one of a person's names is theirs unless a record's `subject` says otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import jsonstate
from .errors import ClipperError

PLATFORMS = ("youtube", "kick", "twitch", "rumble", "odysee", "x")
FIELDS = ("name", "aliases", "channels", "subject", "cast", "notes")
HOSTS = {"youtube.com": "youtube", "youtu.be": "youtube", "kick.com": "kick", "twitch.tv": "twitch",
         "rumble.com": "rumble", "odysee.com": "odysee", "x.com": "x", "twitter.com": "x"}


def canon(name: str) -> str:
    from .investigate import canon as key

    return key(name)


def path(cfg: dict) -> Path:
    return Path(cfg["paths"]["people"])


def stored(cfg: dict) -> dict[str, dict]:
    """people.json's own records, {id: record}."""
    return (jsonstate.read(path(cfg), {}) or {}).get("people", {})


# ---------- names and channels ----------

def names(rec: dict, pid: str = "") -> set[str]:
    """Every name a record answers to, as canon() keys: "The Quartering (Jer)" -> quartering jer,
    quartering, jer; "Chris Bores (Irate Gamer / Ghost Doctor)" -> ..., irate gamer, ghost doctor;
    the id's words too. Under three letters is too loose to match on."""
    out = set()
    for n in [rec.get("name") or "", *rec.get("aliases", [])]:
        out |= {canon(n), canon(re.sub(r"\(.*?\)", "", n))}
        for inner in re.findall(r"\((.*?)\)", n):
            out |= {canon(x) for x in re.split(r"[/,]", inner)}
    if pid:
        out.add(canon(pid.replace("-", " ")))
    return {n for n in out if len(n) >= 3}


def short_name(rec: dict) -> str:
    """The name without its brackets, as a canon() key: "The Quartering (Jer)" -> quartering."""
    return canon(re.sub(r"\(.*?\)", "", rec["name"])) or canon(rec["name"])


def platform_of(key: str) -> str | None:
    """A streamers.json channel key as a platform: youtube_live, youtube_2 -> youtube."""
    base = re.sub(r"(_live|_\d+)$", "", key.lower().strip())
    base = {"twitter": "x"}.get(base, base)
    return base if base in PLATFORMS else None


def platform_of_url(url: str) -> str | None:
    m = re.match(r"(?:https?://)?(?:www\.|m\.)?([^/]+)", url.strip(), re.IGNORECASE)
    return HOSTS.get(m.group(1).lower()) if m else None


def channel_id(platform: str, value: str) -> str:
    """A channel in one spelling, so two lists naming it agree: YouTube as "@Handle" (its url
    when it has none), Kick, Twitch and X as the bare lower-case name, the rest as given."""
    v = value.strip().rstrip("/")
    if platform == "youtube":
        m = re.search(r"youtube\.com/(@[\w.-]+)", v, re.IGNORECASE)
        return m.group(1) if m else v
    if platform in ("kick", "twitch", "x"):
        m = re.search(r"(?:kick\.com|twitch\.tv|x\.com|twitter\.com)/@?([\w-]+)", v, re.IGNORECASE)
        return (m.group(1) if m else v.removeprefix("@")).lower()
    return v


def add_channels(rec: dict, chans: dict) -> None:
    """Join channels in ({platform or streamers.json key: id or [ids]}) into rec, each once."""
    for key, val in chans.items():
        p = platform_of(key)
        if not p:
            continue
        have = rec["channels"].setdefault(p, [])
        for v in [val] if isinstance(val, str) else val:
            cid = channel_id(p, v)
            if cid and cid.lower() not in {x.lower() for x in have}:
                have.append(cid)


def add_aliases(rec: dict, more: list[str]) -> None:
    """Join names into rec's aliases, skipping any it already answers to by that exact key."""
    have = {canon(rec["name"]), *map(canon, rec["aliases"])}
    for a in more:
        a = (a or "").strip()
        if a and canon(a) and canon(a) not in have:
            rec["aliases"].append(a)
            have.add(canon(a))


# ---------- the merged view ----------

def blank(name: str) -> dict:
    return {"name": name, "aliases": [], "channels": {}, "subject": "", "cast": "", "notes": "", "from": []}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", canon(re.sub(r"\(.*?\)", "", name)) or canon(name)).strip("-")
    return s[:40].strip("-") or "person"


def free_id(pid: str, taken) -> str:
    out, n = pid, 2
    while out in taken:
        out, n = f"{pid}-{n}", n + 1
    return out


def others(cfg: dict) -> list[tuple[str, str, dict]]:
    """(list, slug, entry) for everyone the older lists know, in the order they win a name."""
    out = []
    sp = cfg["paths"].get("streamers")
    data = (jsonstate.read(Path(sp), {}) or {}) if sp else {}
    for s in [*(data.get("streamers") or []), *(data.get("uploads_only") or [])]:
        out.append(("streamers.json", s.get("slug") or "",
                    {"name": s["name"], "aliases": [], "channels": s.get("channels") or {}}))
    for name, ch in (cfg["story"].get("people") or {}).items():
        out.append(("[story] people", "", {"name": name, "aliases": [], "channels": dict(ch)}))
    for name, idx in cast_people(cfg).items():
        chans: dict[str, list[str]] = {}
        for url in idx.get("channels") or []:
            if p := platform_of_url(url):
                chans.setdefault(p, []).append(url)
        out.append(("cast", "", {"name": name, "aliases": idx.get("aliases") or [], "channels": chans,
                                 "cast": name}))
    return out


def cast_people(cfg: dict) -> dict[str, dict]:
    """The cast library's folders, or {} on a machine without them ([thumbnail] photos unset
    or the share not there)."""
    from . import cast

    try:
        return cast.people(cfg)
    except (ClipperError, OSError):
        return {}


def subjects(cfg: dict) -> list[str]:
    """Archive subjects (not stories: those are `story-<slug>`)."""
    d = Path(cfg["paths"]["archive"])
    try:
        return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith(("story-", ".")))
    except OSError:
        return []


def load(cfg: dict) -> dict[str, dict]:
    """Everyone, {id: record + "from" (the lists it came from)}: people.json, then the older
    lists folded in by name, then archive subjects matched to the names."""
    out: dict[str, dict] = {}
    for pid, r in stored(cfg).items():
        rec = {**blank(r.get("name") or pid), **{k: r[k] for k in FIELDS if k in r}, "from": ["people.json"]}
        rec["aliases"], rec["channels"] = list(rec["aliases"]), {p: list(v) for p, v in rec["channels"].items()}
        out[pid] = rec
    index: dict[str, str] = {}

    def reindex(pid: str) -> None:
        for n in names(out[pid], pid):
            index.setdefault(n, pid)

    for pid in out:
        reindex(pid)
    for source, slug, e in others(cfg):
        hits = sorted({index[n] for n in names(e, slug) if n in index}, key=list(out).index)
        if hits:
            pid = hits[0]
        else:
            pid = free_id(slug or slugify(e["name"]), out)
            out[pid] = blank(e["name"])
        rec = out[pid]
        add_aliases(rec, [e["name"], *e["aliases"]])
        add_channels(rec, e["channels"])
        if e.get("cast") and not rec["cast"]:
            rec["cast"] = e["cast"]
        if source not in rec["from"]:
            rec["from"].append(source)
        reindex(pid)
    claimed = {r["subject"] for r in out.values() if r["subject"]}
    for s in subjects(cfg):
        if s in claimed:
            continue
        for pid, rec in out.items():
            if not rec["subject"] and canon(s) in names(rec, pid):
                rec["subject"] = s
                break
    return out


def conflicts(people: dict[str, dict]) -> dict[str, list[str]]:
    """{name: [ids]} for a name two records answer to: a lookup by it is ambiguous."""
    seen: dict[str, list[str]] = {}
    for pid, rec in people.items():
        for n in names(rec, pid):
            seen.setdefault(n, []).append(pid)
    return {n: ids for n, ids in sorted(seen.items()) if len(ids) > 1}


def find(cfg: dict, name: str, people: dict[str, dict] | None = None) -> tuple[str, dict] | None:
    """(id, record) for a name, nickname, alias or id; None when nobody answers to it."""
    people = load(cfg) if people is None else people
    if name in people:
        return name, people[name]
    want = names({"name": name})
    hits = [pid for pid, rec in people.items() if want & names(rec, pid)]
    if len(hits) > 1:
        exact = [pid for pid in hits if canon(name) in names(people[pid], pid)]
        hits = exact if len(exact) == 1 else hits
    if len(hits) > 1:
        raise ClipperError(f"{name!r} could be {', '.join(hits)}: use the id")
    return (hits[0], people[hits[0]]) if hits else None


def by_subject(cfg: dict, subject: str, people: dict[str, dict] | None = None) -> dict | None:
    people = load(cfg) if people is None else people
    return next((rec for rec in people.values() if rec["subject"] == subject), None)


def alias_map(cfg: dict, subject: str) -> dict[str, str]:
    """For investigate: {name: what it means} for everyone's names, the subject's own as "self"
    (him, not a thread) and anyone else's as their short name, so spellings merge."""
    people = load(cfg)
    bad = set(conflicts(people))
    out = {}
    for pid, rec in people.items():
        to = "self" if rec["subject"] == subject else short_name(rec)
        for n in names(rec, pid) - bad:
            if n != to:
                out[n] = to
    return out


def display_name(cfg: dict, subject: str) -> str:
    """The person an archive subject is of, by name; the subject itself when nobody is."""
    rec = by_subject(cfg, subject)
    return rec["name"] if rec else subject


# ---------- changes ----------

def clean(rec: dict) -> dict:
    out = {k: rec[k] for k in FIELDS if k in rec}
    out["channels"] = {p: v for p, v in rec.get("channels", {}).items() if v}
    return out


def parse_channel(text: str) -> tuple[str, str]:
    """"youtube=@x", "x=@handle" or a url -> (platform, id)."""
    key, sep, val = text.partition("=")
    p = platform_of(key) if sep else platform_of_url(text)
    if not p:
        raise ClipperError(f"{text!r}: PLATFORM=ID ({', '.join(PLATFORMS)}) or a channel url")
    return p, channel_id(p, val if sep else text)


def set_person(cfg: dict, name: str, new_name: str | None = None, aliases: list[str] = (),
               unaliases: list[str] = (), channels: list[str] = (), unchannels: list[str] = (),
               subject: str | None = None, cast: str | None = None, notes: str | None = None) -> str:
    """Change (or add) a person in people.json; returns the id. A person known only from the
    older lists is written whole, so people.json holds the full record from then on."""
    people = load(cfg)
    hit = find(cfg, name, people)
    if subject and not (Path(cfg["paths"]["archive"]) / subject).is_dir():
        raise ClipperError(f"no archive subject {subject!r} (clipper archive add {subject} <url>)")
    with jsonstate.update(path(cfg), {"people": {}}) as data:
        own = data.setdefault("people", {})
        if hit:
            pid, rec = hit[0], clean(hit[1])
        else:
            pid = free_id(slugify(name), {*people, *own})
            rec = clean(blank(name))
        if new_name:
            old, rec["name"] = rec["name"], new_name.strip()
            add_aliases(rec, [old])
        drop = {canon(a) for a in unaliases}
        rec["aliases"] = [a for a in rec["aliases"] if canon(a) not in drop]
        add_aliases(rec, [a for a in aliases if canon(a) not in drop])
        for text in unchannels:
            p, cid = parse_channel(text)
            rec["channels"][p] = [c for c in rec["channels"].get(p, []) if c.lower() != cid.lower()]
        for text in channels:
            p, cid = parse_channel(text)
            add_channels(rec, {p: cid})
        for key, val in (("subject", subject), ("cast", cast), ("notes", notes)):
            if val is not None:
                rec[key] = val.strip()
        own[pid] = clean(rec)
    return pid


def import_all(cfg: dict, dry_run: bool = False) -> None:
    """Write everyone in the merged view into people.json (its own fields are kept)."""
    people = load(cfg)
    own = stored(cfg)
    new = [pid for pid in people if pid not in own]
    print(f"{len(people)} people: {len(own)} in people.json, {len(new)} only in the older lists"
          + (f" ({', '.join(new)})" if new else ""))
    for n, ids in conflicts(people).items():
        print(f"  {n!r} is a name of {', '.join(ids)}: an alias to fix")
    if dry_run or not people:
        return
    with jsonstate.update(path(cfg), {"people": {}}) as data:
        data["people"] = {pid: clean(rec) for pid, rec in people.items()}
    print(f"wrote {path(cfg)}")


# ---------- printing ----------

def channel_line(rec: dict) -> str:
    return "; ".join(f"{p} {', '.join(ids)}" for p, ids in rec["channels"].items() if ids) or "no channels"


def show_list(cfg: dict) -> None:
    people = load(cfg)
    for pid, rec in sorted(people.items(), key=lambda x: x[1]["name"].lower()):
        extra = [f"archive {rec['subject']}" if rec["subject"] else "", f"cast {rec['cast']}" if rec["cast"] else ""]
        print(f"{pid}: {rec['name']}" + (f" aka {', '.join(rec['aliases'])}" if rec["aliases"] else ""))
        print(f"    {channel_line(rec)}" + "".join(f"; {x}" for x in extra if x)
              + f"  (from {', '.join(rec['from'])})")
    for n, ids in conflicts(people).items():
        print(f"warning: {n!r} is a name of {', '.join(ids)} (clipper people set <id> --unalias ...)")


def show_person(cfg: dict, name: str) -> None:
    hit = find(cfg, name)
    if not hit:
        raise ClipperError(f"nobody called {name!r} (clipper people list)")
    pid, rec = hit
    print(f"{pid}: {rec['name']}")
    print(f"  aliases: {', '.join(rec['aliases']) or '-'}")
    for p in PLATFORMS:
        if rec["channels"].get(p):
            print(f"  {p}: {', '.join(rec['channels'][p])}")
    print(f"  archive subject: {rec['subject'] or '-'}")
    print(f"  cast folder: {rec['cast'] or '-'}")
    if rec["notes"]:
        print(f"  notes: {rec['notes']}")
    print(f"  from: {', '.join(rec['from'])}")
