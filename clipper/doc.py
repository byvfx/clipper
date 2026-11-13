"""`clipper doc`: a narrated mini-documentary, from a hand-written plan to a Resolve timeline,
a mastered render, a leak check and a publish kit.

Usage:
    clipper doc new <slug> [--title T]       the piece's folder, a starter plan.toml and banned.txt
    clipper doc voice <slug> [--dry-run]     the narration: bought once per take, compressed, word-timed
    clipper doc build <slug> [--dry-run]     the plan in frames (work/build.json), then a new timeline in Resolve
    clipper doc master <slug> [--file F] [--timeline NAME] [--no-leak]
                                             render, bring it to [doc] lufs, then the leak check
    clipper doc leak <slug> [--file F] [--dry-run]   the leak check alone, on any video
    clipper doc kit <slug>                   chapters, description, credits, disclosures -> kit.md

A piece is [paths] documentaries/<slug>/, outside the repo: plan.toml (the script and the edit),
banned.txt (what must never be heard or written), and its media in reels/ (the subject's own
video), overlays/ (cards, labels), music/ and sfx/, each file named by its stem.

plan.toml is `[[seq]]` entries in order, one of:
    section = "NAME"                     a red marker; a YouTube chapter (chapter = "Title" or false)
    card = "overlay", seconds = 5        a full-screen card (silent = true: no music under it)
    clip = "name"                        the subject's own picture and sound: [clips.name] reel/from/to,
                                         or reel/from/to on the entry; mute = [[9.07, 9.5]] cuts sound
    say = "One narration line."          cover shots under it: shots = [["reel", 17.8], ["reel2", 5, "the words"]]
                                         (a reel, where it starts, the words of this line it cuts in on)
`say` lines that share a `take` are voiced in one read, in script order, and cut apart in the
pause before each line: one performance, even with clips between its parts.

Narration costs ElevenLabs characters: `voice --dry-run` says how many, and a take is bought
again only when its words, voice or settings change. The leak check transcribes on Modal (a
few cents; `leak --dry-run` prices it). Resolve work (`build`, `master` without --file) needs
Resolve Studio open on this machine: clipper/doc_resolve.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
import wave
from difflib import SequenceMatcher
from pathlib import Path

from . import jsonstate, wiki
from .errors import ClipperError

PLAN, BANNED = "plan.toml", "banned.txt"
FOLDERS = ("reels", "overlays", "music", "sfx")
STILLS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
STILL_S = 3600.0          # a still is as long as the shot needs
V_PICTURE, V_LABELS, V_TEXT = 1, 2, 3
A_CLIP, A_VO, A_FX, A_UNDER, A_SCORE, A_TONE = 1, 2, 3, 4, 5, 7   # the score alternates on 5 and 6
VIDEO, AUDIO = 1, 2       # Resolve's mediaType
RATE = 48000
LIMIT_MARGIN = 0.8        # dB the master's limiter sits under [doc] true_peak

STARTER = '''\
# The plan of one piece: the script and the edit. `clipper doc` reads it; nothing here is
# sent anywhere but the narration lines (to ElevenLabs, by `clipper doc voice`).
title = "{title}"
# project = "{title}"       # the Resolve project that must be open for `build`
# voice_id = ""             # this piece's narrator; "" = [doc] voice_id
description = """
What the piece is, for the YouTube description.
"""
credits = []                # one line each, e.g. "Room tone: Freesound 123456 by Someone (CC0)"
# disclosures = []          # replaces [doc] disclosures for this piece
pinned = ""                 # the pinned comment

# [clips.name]              # a named stretch of the subject's own video (picture and sound)
# reel = "file-stem-in-reels"
# from = 27.07
# to = 32.85

# [labels]                  # reel -> the overlay shown over it on V2 (default: overlays/label_<reel>)

# [[music]]                 # a cue from a section on, crossfaded into the next cue
# file = "cue1"
# at = "COLD OPEN"

# [room_tone]
# file = "roomtone"

[[seq]]
section = "COLD OPEN"

# [[seq]]
# card = "card_open"
# seconds = 5

# [[seq]]
# say = "The first line of narration."
# take = "open"             # lines sharing a take are one read
# shots = [["reel-a", 17.8], ["reel-b", 5, "line of"]]

# [[seq]]
# clip = "name"
'''


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def tokens(text: str) -> list[str]:
    """A line's words as they are matched: lower case, letters and digits; delivery tags
    such as [sighs] aren't words."""
    return [t for t in (norm(w) for w in re.sub(r"\[[^\]]*\]", " ", text).split()) if t]


def piece_dir(cfg: dict, slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", slug):
        raise ClipperError(f"piece {slug!r}: lower-case letters, digits and hyphens, at most 40")
    return Path(cfg["paths"]["documentaries"]) / slug


def new(cfg: dict, slug: str, title: str = "") -> Path:
    d = piece_dir(cfg, slug)
    if (d / PLAN).exists():
        raise ClipperError(f"{d / PLAN} already exists")
    for f in (*FOLDERS, "work", "vo", "renders"):
        (d / f).mkdir(parents=True, exist_ok=True)
    (d / PLAN).write_text(STARTER.format(title=(title or slug).replace('"', "'")), encoding="utf-8")
    if not (d / BANNED).exists():
        (d / BANNED).write_text("# One term per line that must never be heard in the render or written in the kit:\n"
                                "# names, places, employers; add the spellings a transcript might give.\n"
                                "# !a phrase   = heard words that look like a term but are fine\n", encoding="utf-8")
    print(f"{d}: edit {PLAN} and {BANNED}, put media in {', '.join(FOLDERS)} ({wiki.page('Documentaries')})")
    return d


# ---------- the plan ----------

def _where(lines: list[int], n: int) -> str:
    return f"{PLAN} line {lines[n]}" if n < len(lines) else f"{PLAN} seq {n + 1}"


def _shot(s, where: str) -> dict:
    if isinstance(s, dict):
        s = [s.get("reel"), s.get("at", 0), s.get("on", ""), s.get("over", "")]
    if not isinstance(s, list) or not 2 <= len(s) <= 4 or not isinstance(s[0], str) or isinstance(s[1], (str, bool)):
        raise ClipperError(f'{where}: a shot is ["reel", start seconds, "words it cuts in on", "overlay"]: {s!r}')
    return {"reel": s[0], "at": float(s[1]), "on": (s[2] if len(s) > 2 else "") or "", "over": (s[3] if len(s) > 3 else "") or ""}


def cue_index(words: list[str], phrase: str, after: int) -> int | None:
    """Where a cue's words start in a line's words, at or after `after`."""
    want = tokens(phrase)
    for i in range(after, len(words) - len(want) + 1):
        if words[i:i + len(want)] == want:
            return i
    return None


def _say(e: dict, where: str, n: int) -> dict:
    text = " ".join(str(e["say"]).split())
    words = tokens(text)
    if not words:
        raise ClipperError(f"{where}: an empty narration line")
    shots = [_shot(s, where) for s in e.get("shots", [])]
    if not shots or shots[0]["on"]:
        raise ClipperError(f"{where}: the first shot of a line has no cue words (it starts with the line)")
    at = 0
    for s in shots[1:]:
        i = cue_index(words, s["on"], at) if s["on"] else None
        if i is None:
            raise ClipperError(f"{where}: cue {s['on']!r} isn't in the line after the cue before it")
        s["word"], at = i, i + 1
    return {"kind": "say", "text": text, "take": str(e["take"]) if e.get("take") else f"#{n + 1}", "shots": shots,
            "silent": bool(e.get("silent")), "label": e.get("label", True) is not False, "over": e.get("over") or "",
            "max_pause": e.get("max_pause")}


def _clip(e: dict, clips: dict, where: str) -> dict:
    name = e["clip"]
    c = {**clips.get(name, {}), **{k: e[k] for k in ("reel", "from", "to") if k in e}}
    if not {"reel", "from", "to"} <= set(c):
        raise ClipperError(f"{where}: clip {name!r} needs reel, from and to (here or in [clips.{name}])")
    if not float(c["to"]) > float(c["from"]) >= 0:
        raise ClipperError(f"{where}: clip {name!r} ends before it starts")
    mute = [[float(a), float(b)] for a, b in e.get("mute", c.get("mute", []))]
    return {"kind": "clip", "name": name, "reel": c["reel"], "start": float(c["from"]), "end": float(c["to"]),
            "over": e.get("over") or "", "label": e.get("label", True) is not False, "mute": sorted(mute),
            "note": e.get("note") or "", "hard_out": bool(e.get("hard_out", c.get("hard_out")))}


def parse_plan(text: str) -> dict:
    """plan.toml as the plan every step reads; a mistake is one ClipperError naming its line."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ClipperError(f"{PLAN}: {e}") from None
    lines = [n for n, ln in enumerate(text.splitlines(), 1) if re.match(r"\s*\[\[seq\]\]", ln)]
    seq = []
    for n, e in enumerate(raw.get("seq", [])):
        where = _where(lines, n)
        kinds = [k for k in ("section", "card", "clip", "say") if k in e]
        if len(kinds) != 1:
            raise ClipperError(f"{where}: an entry is one of section, card, clip or say")
        if kinds[0] == "section":
            seq.append({"kind": "section", "name": str(e["section"]), "chapter": e.get("chapter", True)})
        elif kinds[0] == "card":
            if not float(e.get("seconds", 0)) > 0:
                raise ClipperError(f"{where}: card {e['card']!r} needs seconds")
            seq.append({"kind": "card", "card": e["card"], "seconds": float(e["seconds"]), "silent": bool(e.get("silent")),
                        "sound": e.get("sound") or "", "sound_before": e.get("sound_before") or ""})
        elif kinds[0] == "clip":
            seq.append(_clip(e, raw.get("clips", {}), where))
        else:
            seq.append(_say(e, where, n))
    if not any(e["kind"] != "section" for e in seq):
        raise ClipperError(f"{PLAN}: no card, clip or say entries yet")
    names = [e["name"] for e in seq if e["kind"] == "section"]
    music = []
    for m in raw.get("music", []):
        if "file" not in m or m.get("at") not in (None, *names):
            raise ClipperError(f"{PLAN}: a [[music]] row needs file, and at = one of the section names: {m!r}")
        music.append({"file": m["file"], "at": m.get("at"), "at_s": float(m.get("at_s", 0)), "tail": bool(m.get("tail")),
                      "credit": m.get("credit") or ""})
    tone = raw.get("room_tone")
    return {"title": str(raw.get("title") or "Untitled"), "project": raw.get("project") or "", "fps": raw.get("fps"),
            "voice_id": raw.get("voice_id") or "", "tts_settings": raw.get("tts_settings"),
            "description": str(raw.get("description") or "").strip(), "pinned": str(raw.get("pinned") or "").strip(),
            "credits": list(raw.get("credits", [])), "disclosures": raw.get("disclosures"),
            "labels": dict(raw.get("labels", {})), "media": dict(raw.get("media", {})), "music": music,
            "room_tone": {"file": tone["file"], "credit": tone.get("credit") or ""} if tone else None, "seq": seq}


def load_plan(cfg: dict, slug: str) -> tuple[Path, dict]:
    d = piece_dir(cfg, slug)
    if not (d / PLAN).exists():
        raise ClipperError(f"no piece {slug!r} in {d.parent} (clipper doc new {slug})")
    plan = parse_plan((d / PLAN).read_text(encoding="utf-8-sig"))
    plan["fps"] = int(plan["fps"] or cfg["doc"]["fps"])
    return d, plan


def takes(plan: dict) -> list[dict]:
    """The reads to buy, in script order: {id, says: [seq indexes], text, joined, max_pause}.
    joined[k] says whether part k+1 follows part k with nothing between them."""
    out: dict[str, dict] = {}
    flat = [n for n, e in enumerate(plan["seq"]) if e["kind"] != "section"]
    for pos, n in enumerate(flat):
        e = plan["seq"][n]
        if e["kind"] != "say":
            continue
        t = out.setdefault(e["take"], {"id": e["take"], "says": [], "texts": [], "joined": [], "pos": []})
        if t["says"]:
            t["joined"].append(t["pos"][-1] == pos - 1)
        t["says"].append(n)
        t["texts"].append(e["text"])
        t["pos"].append(pos)
    for t in out.values():
        t["text"] = " ".join(t["texts"])
        caps = [plan["seq"][n]["max_pause"] for n in t["says"] if plan["seq"][n]["max_pause"] is not None]
        t["max_pause"] = max(caps) if caps else None
        del t["pos"]
    return list(out.values())


# ---------- narration: one take, cut at the pauses ----------

def align(script: list[str], heard: list[list]) -> tuple[list[tuple[float, float]], float]:
    """When each script word is said, from the words a transcript heard ([word, start, end]):
    matched words take the heard word's times, the ones between are spread evenly over the
    gap. Also the share of script words that matched (a low one: the read isn't the script)."""
    if not script:
        return [], 1.0
    if not heard:
        raise ClipperError("no words heard in the read")
    h = [norm(w[0]) for w in heard]
    times: list[tuple[float, float] | None] = [None] * len(script)
    for a, b, size in SequenceMatcher(None, script, h, autojunk=False).get_matching_blocks():
        for k in range(size):
            times[a + k] = (float(heard[b + k][1]), float(heard[b + k][2]))
    matched = sum(t is not None for t in times)
    i = 0
    while i < len(script):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(script) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i else float(heard[0][1])
        hi = times[j][0] if j < len(script) else float(heard[-1][2])
        hi = max(hi, lo)
        step = (hi - lo) / (j - i)
        for k in range(i, j):
            times[k] = (lo + step * (k - i), lo + step * (k - i + 1))
        i = j
    return times, matched / len(script)   # type: ignore[return-value]


def spans(counts: list[int], times: list[tuple[float, float]], length: float, joined: list[bool],
          out_pad: float = 0.25, in_pad: float = 0.15) -> list[tuple[float, float]]:
    """Where each line of a take starts and ends in its file. Two lines that run straight on are
    cut in the middle of the pause between them; with something between them, each keeps a
    little air (out_pad after its last word, in_pad before its first), never past that middle."""
    out, start, at = [], 0.0, 0
    for k, n in enumerate(counts):
        at += n
        if k == len(counts) - 1:
            out.append((start, length))
            break
        e, s = times[at - 1][1], times[at][0]
        mid = (e + s) / 2
        if joined[k]:
            out.append((start, mid))
            start = mid
        else:
            out.append((start, min(e + out_pad, mid)))
            start = max(s - in_pad, mid)
    return [(round(a, 3), round(b, 3)) for a, b in out]


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """ffmpeg silencedetect's log as (start, end) pairs."""
    return list(zip(map(float, re.findall(r"silence_start: (-?[0-9.]+)", stderr)),
                    map(float, re.findall(r"silence_end: ([0-9.]+)", stderr))))


def pause_cuts(silences: list[tuple[float, float]], total: float, keep: float, head: float = 0.05,
               tail: float = 0.12) -> list[tuple[float, float]]:
    """What to take out of a read so no pause inside it is longer than `keep` (a voice clone
    leaves 1-2.5 s between sentences), with `head` of silence before it and `tail` after."""
    cuts = []
    for s, t in silences:
        s = max(s, 0.0)
        first, last = s < 0.05, t > total - 0.05
        lim = head if first else tail if last else keep
        if t - s > lim:
            cuts.append((s, t - lim) if first else (s + lim, t) if last else (s + lim / 2, t - lim / 2))
    return cuts


def stamp(p: Path) -> list:
    """What says a media file is still the same file: its path, size and time of change."""
    st = p.stat()
    return [str(p), st.st_size, st.st_mtime_ns]


def key_of(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


def narrator(cfg: dict, plan: dict) -> dict:
    """[narrate]'s key, model and format with this piece's voice and voice settings."""
    from . import narrate

    dc = cfg["doc"]
    return {**narrate.settings(cfg), "voice_id": plan["voice_id"] or dc["voice_id"],
            "tts_settings": dc["tts_settings"] if plan["tts_settings"] is None else plan["tts_settings"]}


def take_keys(t: dict, nc: dict, dc: dict) -> tuple[str, str]:
    """(the bought read's name, the processed take's): the first changes with the words, the
    voice or its settings; the second also with the compressor and the pause cap."""
    raw = key_of(t["text"], nc["voice_id"], nc["tts_model"], nc["tts_settings"])
    keep = dc["max_pause_s"] if t["max_pause"] is None else t["max_pause"]
    return raw, key_of(raw, dc["vo_filter"], keep, dc["silence_db"], dc["silence_min_s"])


def take_files(d: Path, t: dict, nc: dict, dc: dict) -> tuple[Path, Path, Path]:
    raw, done = take_keys(t, nc, dc)
    return d / "vo" / "raw" / f"{raw}.wav", d / "vo" / f"vo_{done}.wav", d / "vo" / f"vo_{done}.words.json"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise ClipperError(f"ffmpeg: {r.stderr.strip()[-600:]}")
    return r


def process_take(raw: Path, dst: Path, dc: dict, keep: float) -> float:
    """The bought read -> the take the timeline uses: the compressor, pauses capped, 48 kHz
    mono. Returns its length."""
    import numpy as np

    tmp = dst.with_suffix(".tmp.wav")
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), *(["-af", dc["vo_filter"]] if dc["vo_filter"] else []),
          "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(tmp)])
    log = _run(["ffmpeg", "-hide_banner", "-i", str(tmp), "-af",
                f"silencedetect=noise={dc['silence_db']}dB:d={dc['silence_min_s']}", "-f", "null", "-"]).stderr
    with wave.open(str(tmp)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    tmp.unlink()
    parts, at = [], 0
    for a, b in pause_cuts(parse_silences(log), len(x) / RATE, keep):
        parts.append(x[at:int(a * RATE)])
        at = int(b * RATE)
    y = np.concatenate([*parts, x[at:]])
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(y.tobytes())
    return len(y) / RATE


def hear(wav: Path, model: str) -> list[list]:
    """A take's words as heard, [word, start, end], by a small local Whisper (free, CPU)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ClipperError("word times need faster-whisper (uv sync --extra gpu, or "
                           "uv pip install faster-whisper)") from None
    import numpy as np

    # decoded by ffmpeg, not by faster-whisper's own reader (PyAV versions disagree about it)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-ar", "16000", "-ac", "1", "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    segs, _ = WhisperModel(model, device="cpu", compute_type="int8").transcribe(np.frombuffer(raw, np.float32),
                                                                                word_timestamps=True)
    return [[w.word.strip(), round(w.start, 2), round(w.end, 2)] for s in segs for w in s.words]


def wav_length(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def banned_in_script(d: Path, plan: dict, fuzzy: float) -> list[dict]:
    terms, allowed = read_banned(d / BANNED)
    words = [(w, None) for e in plan["seq"] if e["kind"] == "say" for w in e["text"].split()]
    return leaks(words, terms, allowed, fuzzy)


def voice(cfg: dict, slug: str, dry_run: bool = False) -> None:
    from . import narrate

    d, plan = load_plan(cfg, slug)
    dc, nc = cfg["doc"], narrator(cfg, plan)
    if hits := banned_in_script(d, plan, dc["leak_fuzzy"]):
        raise ClipperError(f"{PLAN}: the narration has what {BANNED} lists: "
                           + "; ".join(f"{h['heard']!r} ({h['term']})" for h in hits[:8]))
    ts = takes(plan)
    todo = [t for t in ts if not take_files(d, t, nc, dc)[0].exists()]
    if long := [t["id"] for t in todo if len(t["text"]) > nc["tts_max_chars"]]:
        raise ClipperError(f"takes over [narrate] tts_max_chars ({nc['tts_max_chars']}): {', '.join(long)}: "
                           "split them into two takes")
    print(f"{len(ts)} takes, {len(todo)} to buy: {sum(len(t['text']) for t in todo)} characters from ElevenLabs "
          f"(voice {nc['voice_id'] or 'not set'}, {nc['tts_model']})")
    if dry_run:
        for t in todo:
            print(f"  {t['id']}: {len(t['text'])} characters, {len(t['says'])} line(s)")
        return
    if todo and not nc["voice_id"]:
        raise ClipperError("no narrator: set voice_id in plan.toml or [doc] voice_id (clipper narrate voices)")
    for t in ts:
        raw, done, words = take_files(d, t, nc, dc)
        raw.parent.mkdir(parents=True, exist_ok=True)
        if not raw.exists():
            narrate.speak(t["text"], nc, raw, nc["voice_id"])
        if not done.exists():
            keep = dc["max_pause_s"] if t["max_pause"] is None else t["max_pause"]
            print(f"  {t['id']}: {wav_length(raw):.1f} -> {process_take(raw, done, dc, keep):.1f} s")
        if not words.exists():
            heard = hear(done, dc["words_model"])
            jsonstate.write(words, heard)
            _, share = align(tokens(t["text"]), heard)
            if share < 0.8:
                print(f"  {t['id']}: only {share:.0%} of the script's words were heard; listen to {done.name}: "
                      + " ".join(w[0] for w in heard)[:300])
    print(f"narration ready in {d / 'vo'}: listen to each take before `clipper doc build {slug}`")


def narration(d: Path, plan: dict, nc: dict, dc: dict) -> dict[int, dict]:
    """For each say entry (by its seq index): its take's file, where it starts and ends in it,
    and when each of its words is said."""
    out = {}
    for t in takes(plan):
        _, done, words = take_files(d, t, nc, dc)
        heard = jsonstate.read(words, None)
        if not done.exists() or heard is None:
            raise ClipperError(f"take {t['id']} isn't voiced yet (clipper doc voice {d.name})")
        times, _ = align(tokens(t["text"]), heard)
        counts = [len(tokens(x)) for x in t["texts"]]
        at = 0
        for n, (a, b), c in zip(t["says"], spans(counts, times, wav_length(done), t["joined"]), counts):
            out[n] = {"file": done.stem, "start": a, "end": b, "words": times[at:at + c]}
            at += c
    return out


# ---------- the layout: the plan in frames ----------

def kept(a: int, b: int, cuts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """What is left of a..b once the cuts are out: they may overlap, nest, or reach outside it."""
    out, at = [], a
    for c0, c1 in sorted(cuts):
        c0, c1 = max(c0, a), min(c1, b)
        if c1 <= at:
            continue
        if c0 > at:
            out.append((at, c0))
        at = c1
    if at < b:
        out.append((at, b))
    return out


def _over(clip: str, f: int, n: int, track: int) -> dict:
    return {"clip": clip, "start": 0, "end": n, "record": f, "track": track, "type": VIDEO}


def layout(plan: dict, vo: dict[int, dict], dur: dict[str, float], dc: dict, labels: dict[str, str],  # noqa: C901
           stamps: dict[str, list] | None = None) -> dict:
    """Every clip, shot, card, label and narration piece on its track at its frame. `vo` is
    narration(); `dur` each reel's and sound's length in seconds; `labels` reel -> overlay;
    `stamps` each reel's file (stamp()), so a fade cut from a reel is named for that file."""
    stamps = stamps or {}
    fps = plan["fps"]

    def fr(s: float) -> int:
        return round(s * fps)

    pad, lead, tail_s, leadin = dc["pad_s"], dc["cue_lead_s"], dc["tail_s"], dc["lead_in_s"]
    sync, v1, v2, v3, audio = [], [], [], [], []
    markers, sections, blend, quiet, dead, fixed = [], [], [], [], [], []
    flat = [(n, e) for n, e in enumerate(plan["seq"]) if e["kind"] != "section"]
    f, sec = 0, None

    def label(reel: str, at: int, n: int) -> None:
        key = labels.get(reel)
        if not key:
            return
        if v2 and v2[-1]["clip"] == key and v2[-1]["record"] + v2[-1]["end"] == at and v2[-1]["end"] + n <= fr(dc["label_max_s"]):
            v2[-1]["end"] += n      # the same reel straight on: one label, no blink
        else:
            v2.append(_over(key, at, n, V_LABELS))

    def under(kind: str, name: str, reel: str, start: float, length: float, record: int) -> None:
        b = {"name": f"{kind}_{name}_{key_of(reel, stamps.get(reel), round(start, 3), length)[:8]}", "reel": reel,
             "start": round(start, 4),
             "dur": length, "kind": "out" if kind in ("out", "xo") else "in", "record": record}
        blend.append(b)

    for pos, (n, e) in enumerate(flat):
        while sec is None or sec < n:      # sections before this entry start at its frame
            sec = 0 if sec is None else sec + 1
            if sec < n and plan["seq"][sec]["kind"] == "section":
                s = plan["seq"][sec]
                markers.append({"frame": f, "color": "Red", "name": s["name"], "note": "Section start", "duration": 1})
                sections.append({"name": s["name"], "frame": f, "chapter": s["chapter"]})
        before = flat[pos - 1][1] if pos else None
        nxt = flat[pos + 1][1] if pos + 1 < len(flat) else None
        if e["kind"] == "card":
            k = fr(e["seconds"])
            v1.append(_over(e["card"], f, k, V_PICTURE))
            if e["silent"]:
                quiet.append([f, f + k])
                if not e["sound"]:
                    dead.append([f, f + k])
            for snd, at in ((e["sound"], f), (e["sound_before"], max(0, f - fr(dur.get(e["sound_before"], 0))))):
                if snd:
                    audio.append({"clip": snd, "start": 0, "end": fr(dur[snd]), "record": at, "track": A_FX, "type": AUDIO})
            f += k
        elif e["kind"] == "clip":
            a, b = fr(e["start"]), fr(e["end"])
            if e["end"] > dur[e["reel"]]:
                raise ClipperError(f"{PLAN}: clip {e['name']!r} runs to {e['end']} s; {e['reel']} is {dur[e['reel']]:.1f} s long")
            if before and before["kind"] == "clip" and not before["hard_out"]:   # clip into clip: the sounds overlap a moment
                pb = fr(before["end"]) / fps
                if pb + 0.5 < dur[before["reel"]] - 0.2:
                    under("xo", before["name"], before["reel"], pb, 0.5, f)
                if e["start"] >= 0.4:
                    under("xi", e["name"], e["reel"], a / fps - 0.3, 0.3, f - fr(0.3))
            row = {"clip": e["reel"], "start": a, "end": b, "record": f, "track": V_PICTURE}
            if e["mute"]:                   # picture whole; the sound in pieces around what is cut
                row["type"] = VIDEO
                pieces = kept(a, b, [(fr(m0), fr(m1)) for m0, m1 in e["mute"]])
                for k, (p0, p1) in enumerate(pieces):
                    audio.append({"clip": e["reel"], "start": p0, "end": p1, "record": f + p0 - a, "track": A_CLIP,
                                  "type": AUDIO, "fade_in": 2 if k else 0, "fade_out": 2 if k + 1 < len(pieces) else 0})
            sync.append(row)
            if e["over"]:
                v3.append(_over(e["over"], f, b - a, V_TEXT))
            elif e["label"]:
                label(e["reel"], f, b - a)
            if e["note"]:
                markers.append({"frame": f + 2, "color": "Yellow", "name": "NOTE", "note": e["note"],
                                "duration": max(1, b - a - 4)})
            f += b - a
        else:
            v = vo[n]
            s0, s1 = v["start"], v["end"]
            cont = bool(before and before["kind"] == "say" and before["take"] == e["take"])
            next_cont = bool(nxt and nxt["kind"] == "say" and nxt["take"] == e["take"])
            gap = 0.0 if next_cont else pad
            lead_in = bool(nxt and nxt["kind"] == "clip" and not e["silent"] and nxt["start"] >= leadin + 0.05)
            if lead_in:
                gap = 0.1                   # the clip's sound comes up under the last words
            tail = None
            if before and before["kind"] == "clip" and not e["silent"] and not before["hard_out"]:
                bf = fr(before["end"])
                if bf / fps + tail_s < dur[before["reel"]] - 0.2:
                    tail = (before["reel"], bf)
            vo_at = 0 if cont else fr(dc["vo_after_clip_s"]) if tail else fr(dc["vo_lead_s"])
            # the block is as long as its words, in whole frames of the take: a take cut in two meets itself exactly
            k = fr(s1) - fr(s0) + round(gap * fps) + vo_at
            if lead_in:
                under("in", nxt["name"], nxt["reel"], fr(nxt["start"]) / fps - leadin, leadin, f + k - fr(leadin))
            shots = list(e["shots"])
            starts = [0] + [max(0, round((v["words"][s["word"]][0] - s0 - lead) * fps) + vo_at) for s in shots[1:]]
            if tail:
                reel, bf = tail
                tf = fr(tail_s)
                under("out", before["name"], reel, bf / fps, tail_s, f)
                if shots[0]["reel"] != reel and (len(starts) == 1 or starts[1] >= tf + 24) and k >= tf + 30:
                    # the clip's picture runs on under the first words
                    shots = [{"reel": reel, "at": bf / fps, "over": before["over"]}, *shots]
                    starts = [0, tf, *starts[1:]]
            if starts != sorted(set(starts)) or starts[-1] >= k:
                raise ClipperError(f"{PLAN}: the line {e['text'][:40]!r}: two cues land on one frame, or one "
                                   f"after the line ends ({starts} of {k} frames)")
            for i, s in enumerate(shots):
                at, length = starts[i], (starts[i + 1] if i + 1 < len(shots) else k) - starts[i]
                start = s["at"]
                if length / fps > dur[s["reel"]] - 0.3:
                    raise ClipperError(f"{PLAN}: the line {e['text'][:40]!r}: shot {i + 1} needs {length / fps:.1f} s "
                                       f"of {s['reel']}, which is {dur[s['reel']]:.1f} s long")
                if start + length / fps > dur[s["reel"]] - 0.3:   # too near the reel's end: slide the shot back
                    start = max(0.0, dur[s["reel"]] - 0.3 - length / fps)
                    fixed.append([e["text"][:30], s["reel"], round(start, 1)])
                a = fr(start)
                v1.append({"clip": s["reel"], "start": a, "end": a + length, "record": f + at, "track": V_PICTURE, "type": VIDEO})
                if e["label"]:
                    label(s["reel"], f + at, length)
                if s.get("over"):
                    v3.append(_over(s["over"], f + at, length, V_TEXT))
            audio.append({"clip": v["file"], "start": fr(s0), "end": fr(s1), "record": f + vo_at, "track": A_VO, "type": AUDIO,
                          "fade_in": 0 if cont else 2, "fade_out": 0 if next_cont else 6})
            if e["over"]:
                v3.append(_over(e["over"], f, k, V_TEXT))
            if e["silent"]:
                quiet.append([f, f + k])
            f += k
    for b in blend:
        audio.append({"clip": b["name"], "start": 0, "end": fr(b["dur"]), "record": b["record"], "track": A_UNDER,
                      "type": AUDIO, "reel": b["reel"]})
    return {"fps": fps, "total": f, "sync": sync, "v1": v1, "v2": v2, "v3": v3, "audio": audio, "markers": markers,
            "sections": sections, "blend": blend, "quiet": sorted(quiet), "dead": sorted(dead), "fixed": fixed}


def picture_gaps(b: dict) -> list[list[int]]:
    """Frames where V1 has a hole or two items overlap: an empty list on a sound layout."""
    v = sorted(b["sync"] + b["v1"], key=lambda r: r["record"])
    return [[x["record"], y["record"]] for x, y in zip(v, v[1:]) if x["record"] + x["end"] - x["start"] != y["record"]]


def add_score(b: dict, plan: dict, dur: dict[str, float], dc: dict) -> None:
    """The music cues, each from its section to a crossfade into the next on two alternating
    tracks, stopping at a silent stretch; and the room tone, out only over a dead-silent card."""
    fps, total = b["fps"], b["total"]
    xf = round(dc["crossfade_s"] * fps)
    at = {s["name"]: s["frame"] for s in b["sections"]}
    starts = [(at[m["at"]] if m["at"] else 0) + round(m["at_s"] * fps) for m in plan["music"]]
    for k, m in enumerate(plan["music"]):
        a = starts[k]
        last = k + 1 == len(starts)
        end = total if last else min(total, starts[k + 1] + xf)
        a = next((q1 for q0, q1 in b["quiet"] if q0 <= a < q1), a)     # starting in a silent stretch: after it
        cut = next((q0 for q0, _ in b["quiet"] if a < q0 < end), None)
        end = cut if cut is not None else end
        have = round(dur[m["file"]] * fps)
        n = min(end - a, have)
        if n <= 0:
            continue
        off = max(0, have - n) if m["tail"] else 0     # tail: the cue's own ending lands on the end
        b["audio"].append({"clip": m["file"], "start": off, "end": off + n, "record": a, "track": A_SCORE + k % 2,
                           "type": AUDIO, "music": True, "fade_in": xf if k else round(0.5 * fps),
                           "fade_out": round(0.7 * fps) if last and cut is None else min(xf, n // 2)})
    if plan["room_tone"]:
        edges = [0, *(x for q in b["dead"] for x in q), total]
        for k in range(0, len(edges), 2):
            if edges[k + 1] > edges[k]:
                b["audio"].append({"clip": "roomtone_bed", "start": edges[k], "end": edges[k + 1], "record": edges[k],
                                   "track": A_TONE, "type": AUDIO, "tone": True, "fade_in": round(fps / 3), "fade_out": fps})


def gain(lufs: float, peak: float, target: float, ceiling: float) -> float:
    """Clip gain in dB to reach target, held back where the peak would pass the ceiling."""
    if lufs < -50:          # too short or too quiet to measure: leave it
        return 0.0
    return round(max(-12.0, min(target - lufs, ceiling - peak, 14.0)), 1)


def set_levels(b: dict, measure, dc: dict) -> None:
    """A gain on every sound row: each clip of the subject and each narration take at [doc]
    dialogue_lufs, the fades under the narration at their clip's gain, music and room tone
    under the dialogue. measure(clip, start_s, length_s) -> (LUFS, peak dBFS); None = the file."""
    fps, target, ceiling = b["fps"], dc["dialogue_lufs"], dc["peak_ceiling"]
    whole: dict[str, float] = {}
    clips = [r for r in b["sync"] if r.get("type") != VIDEO] + [r for r in b["audio"] if r["track"] == A_CLIP]
    for r in clips:
        r["gain"] = gain(*measure(r["clip"], r["start"] / fps, (r["end"] - r["start"]) / fps), target, ceiling)
    for r in b["audio"]:
        if r["track"] == A_VO:      # one gain a take, so a take cut in two doesn't step in level
            if r["clip"] not in whole:
                whole[r["clip"]] = gain(*measure(r["clip"], None, None), target, ceiling)
            r["gain"] = whole[r["clip"]]
        elif r["track"] == A_UNDER:
            near = [c for c in clips if c["clip"] == r["reel"]]
            end = r["record"] + r["end"]
            r["gain"] = min(near, key=lambda c: min(abs(c["record"] - end), abs(c["record"] + c["end"] - c["start"]
                                                                                 - r["record"])))["gain"] if near else 0.0
        elif r.get("music") or r.get("tone"):
            under = dc["music_under"] if r.get("music") else dc["tone_under"]
            if r["clip"] not in whole:
                whole[r["clip"]] = round(target - under - measure(r["clip"], None, None)[0], 1)
            r["gain"] = whole[r["clip"]]


# ---------- media ----------

def find_media(d: Path, plan: dict) -> dict[str, Path]:
    """Every file in the piece's media folders by its stem, plus plan.toml's [media] table
    (name = "a path", for files kept elsewhere)."""
    out: dict[str, Path] = {}
    for folder in FOLDERS:
        for p in sorted((d / folder).rglob("*")) if (d / folder).is_dir() else []:
            if p.is_file() and not p.name.startswith("."):
                if p.stem in out:
                    raise ClipperError(f"two files named {p.stem!r}: {out[p.stem]} and {p}")
                out[p.stem] = p
    for name, path in plan["media"].items():
        p = Path(path).expanduser()
        out[name] = p if p.is_absolute() else d / p
    return out


def needed(plan: dict) -> set[str]:
    """Every media name the plan uses."""
    out = {m["file"] for m in plan["music"]} | ({plan["room_tone"]["file"]} if plan["room_tone"] else set())
    for e in plan["seq"]:
        if e["kind"] == "card":
            out |= {e["card"], e["sound"], e["sound_before"]}
        elif e["kind"] == "clip":
            out |= {e["reel"], e["over"]}
        elif e["kind"] == "say":
            out |= {e["over"]} | {s["reel"] for s in e["shots"]} | {s["over"] for s in e["shots"]}
    return out - {""}


def lengths(d: Path, media: dict[str, Path], names: set[str]) -> dict[str, float]:
    """Each named file's length in seconds (a still: as long as needed), probed once a file."""
    from .media import duration

    cache = jsonstate.read(d / "work" / "durations.json", {}) or {}
    out = {}
    for name in sorted(names):
        p = media[name]
        if p.suffix.lower() in STILLS:
            out[name] = STILL_S
            continue
        sig = stamp(p)
        if cache.get(name, {}).get("sig") != sig:
            cache[name] = {"sig": sig, "s": duration(p)}
        out[name] = cache[name]["s"]
    (d / "work").mkdir(exist_ok=True)
    jsonstate.write(d / "work" / "durations.json", cache)
    return out


def loudness(path: Path, start: float | None = None, length: float | None = None) -> tuple[float, float]:
    """(integrated LUFS, peak dBFS) of a file or a stretch of it; -70 when nothing measures."""
    seek = [] if start is None else ["-ss", f"{start:.3f}", "-t", f"{length:.3f}"]
    e = _run(["ffmpeg", "-hide_banner", *seek, "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
    return parse_loudness(e)


def parse_loudness(stderr: str) -> tuple[float, float]:
    i = re.findall(r"I:\s+(-?[0-9.]+) LUFS", stderr)
    p = re.findall(r"Peak:\s+(-?[0-9.]+) dBFS", stderr)
    return (float(i[-1]) if i else -70.0, float(p[-1]) if p else 0.0)


def make_blends(d: Path, b: dict, media: dict[str, Path]) -> None:
    """The short pieces of a clip's own sound that run under the narration at each join."""
    out = d / "work" / "blend"
    out.mkdir(parents=True, exist_ok=True)
    for x in b["blend"]:
        dst = out / f"{x['name']}.wav"
        if not dst.exists():
            fade = f"afade=t=out:st=0:d={x['dur']}" if x["kind"] == "out" else f"afade=t=in:st=0:d={x['dur']}:curve=qua"
            _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, x['start']):.4f}", "-t", str(x["dur"]), "-i",
                  str(media[x["reel"]]), "-vn", "-af", fade, "-ar", str(RATE), "-ac", "2", "-c:a", "pcm_s16le", str(dst)])
        media[x["name"]] = dst


def make_tone(d: Path, src: Path, seconds: float) -> Path:
    """One continuous room tone as long as the piece: the source looped with 1 s crossfades."""
    import numpy as np

    dst = d / "work" / f"roomtone_bed_{key_of(stamp(src))[:8]}_{int(seconds) + 30}.wav"
    if dst.exists():
        return dst
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-f", "s16le", "-ac", "2", "-ar", str(RATE), "-"],
                         check=True, capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32)
    if len(x) < 4 * RATE:
        raise ClipperError(f"{src.name}: a room tone needs at least 4 s")
    x = x[RATE:-RATE]                                    # drop a second at each end
    ramp = np.linspace(0, 1, RATE, dtype=np.float32)[:, None]
    want = (int(seconds) + 30) * RATE
    out = x.copy()
    while len(out) < want:
        out = np.concatenate([out[:-RATE], out[-RATE:] * (1 - ramp) + x[:RATE] * ramp, x[RATE:]])
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(out[:want].astype(np.int16).tobytes())
    return dst


def as_wav(d: Path, src: Path) -> Path:
    """A music cue as 48 kHz stereo WAV (Resolve reads mp3 lengths loosely)."""
    if src.suffix.lower() == ".wav":
        return src
    dst = d / "work" / "music" / f"{src.stem}_{key_of(stamp(src))[:8]}.wav"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", str(RATE), "-ac", "2", "-c:a", "pcm_s24le", str(dst)])
    return dst


def still_movies(d: Path, b: dict, media: dict[str, Path]) -> None:
    """Each still the build uses as a movie long enough for its longest use: Resolve gives a
    still its own default length on a timeline, whatever range it is asked for (tried on 21.2)."""
    need: dict[str, int] = {}
    for r in (r for k in ("sync", "v1", "v2", "v3") for r in b[k]):
        if media[r["clip"]].suffix.lower() in STILLS:
            need[r["clip"]] = max(need.get(r["clip"], 0), r["end"])
    for name, frames in need.items():
        src = media[name]
        secs = frames // b["fps"] + 2
        dst = d / "work" / "stills" / f"{src.stem}_{key_of(stamp(src))[:8]}_{secs}.mov"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            _run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(src), "-t", str(secs), "-r", str(b["fps"]),
                  "-c:v", "qtrle", "-pix_fmt", "argb", str(dst)])
        media[name] = dst


# ---------- build ----------

def build(cfg: dict, slug: str, dry_run: bool = False) -> dict:
    d, plan = load_plan(cfg, slug)
    dc, nc = cfg["doc"], narrator(cfg, plan)
    if hits := banned_in_script(d, plan, dc["leak_fuzzy"]):
        raise ClipperError(f"{PLAN}: the narration has what {BANNED} lists: "
                           + "; ".join(f"{h['heard']!r} ({h['term']})" for h in hits[:8]))
    media = find_media(d, plan)
    labels = {**{r: f"label_{r}" for r in media if f"label_{r}" in media}, **plan["labels"]}
    names = needed(plan) | set(labels.values())
    if missing := sorted(names - set(media)):
        raise ClipperError(f"{PLAN} names media that isn't in {', '.join(FOLDERS)}: {', '.join(missing)}")
    for m in plan["music"]:
        media[m["file"]] = as_wav(d, media[m["file"]])
    dur = lengths(d, media, names)
    vo = narration(d, plan, nc, dc)
    b = layout(plan, vo, dur, dc, labels, {n: stamp(media[n]) for n in names})
    add_score(b, plan, dur, dc)
    for t in takes(plan):
        f = take_files(d, t, nc, dc)[1]
        media[f.stem] = f
    make_blends(d, b, media)
    still_movies(d, b, media)
    if plan["room_tone"]:
        media["roomtone_bed"] = make_tone(d, media[plan["room_tone"]["file"]], b["total"] / b["fps"])
    set_levels(b, lambda clip, s, n: loudness(media[clip], s, n), dc)
    b["title"] = plan["title"]
    b["media"] = {k: str(v) for k, v in media.items()}
    jsonstate.write(d / "work" / "build.json", b)
    secs = b["total"] / b["fps"]
    print(f"{plan['title']}: {secs / 60:.2f} min; {len(b['sync'])} clips, {len(b['v1'])} shots and cards, "
          f"{len(b['v2'])} labels, {len(b['audio'])} sound pieces, {len(b['sections'])} sections")
    if gaps := picture_gaps(b):
        raise ClipperError(f"the picture track has holes or overlaps at frames {gaps[:8]}")
    if b["fixed"]:
        print("shots slid back from their reel's end: " + "; ".join(f"{t!r} {r} -> {s} s" for t, r, s in b["fixed"]))
    if dry_run:
        print(f"dry run: {d / 'work' / 'build.json'} written, Resolve not touched")
        return b
    from . import doc_resolve

    name = doc_resolve.build(doc_resolve.connect(dc), b, plan["project"])
    jsonstate.merge(d / "work" / "state.json", {"timeline": name}, ["timeline"])
    print(f"timeline {name!r} built and saved: watch it, then `clipper doc master {slug}`")
    return b


# ---------- master ----------

def master_filter(lufs: float, target: float, true_peak: float) -> tuple[float, str]:
    """(the gain in dB, the ffmpeg filter) that brings a measured mix to the target: gain, then
    a limiter at the true-peak ceiling for what the gain pushes over it."""
    g = round(target - lufs, 2)
    # the limiter works on samples; true peaks and the AAC encode overshoot it by up to ~0.8 dB
    return g, f"volume={g}dB,alimiter=limit={10 ** ((true_peak - LIMIT_MARGIN) / 20):.4f}:level=false:attack=5:release=50"


def master_file(src: Path, dc: dict) -> Path:
    """`src` with its sound at [doc] lufs (video copied) beside it as <name>.master.mp4."""
    dst = src.with_name(f"{src.stem}.master.mp4")
    lufs, peak = loudness(src)
    if lufs <= -70:
        raise ClipperError(f"{src.name}: no sound to measure")
    g, filt = master_filter(lufs, dc["lufs"], dc["true_peak"])
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "copy", "-af", filt, "-ar", str(RATE), "-c:a", "aac",
          "-b:a", "320k", "-movflags", "+faststart", str(dst)])
    after, peak2 = loudness(dst)
    print(f"{src.name}: {lufs:.1f} LUFS, peak {peak:.1f} -> gain {g:+.1f} dB -> {after:.1f} LUFS, peak {peak2:.1f} "
          f"(target {dc['lufs']}, ceiling {dc['true_peak']}): {dst.name}")
    if peak2 > dc["true_peak"] + 0.1:
        print(f"  the peak is {peak2 - dc['true_peak']:.1f} dB over the ceiling after encoding: lower [doc] true_peak")
    if abs(after - dc["lufs"]) > 1.0:
        print(f"  {abs(after - dc['lufs']):.1f} LU off the target: the limiter is working hard; raise "
              "[doc] dialogue_lufs and build again rather than push the master")
    return dst


def master(cfg: dict, slug: str, file: Path | None = None, timeline: str = "", leak_check: bool = True) -> Path:
    d, plan = load_plan(cfg, slug)
    dc = cfg["doc"]
    if file is None:
        from . import doc_resolve

        name = timeline or (jsonstate.read(d / "work" / "state.json", {}) or {}).get("timeline")
        if not name:
            raise ClipperError(f"no timeline built yet (clipper doc build {slug}), or pass --timeline / --file")
        (d / "renders").mkdir(exist_ok=True)
        file = doc_resolve.render(doc_resolve.connect(dc), name, d / "renders", dc, plan["project"])
    out = master_file(Path(file), dc)
    if leak_check:
        leak(cfg, slug, out)
    return out


# ---------- the leak check ----------

def read_banned(path: Path) -> tuple[list[str], set[str]]:
    """banned.txt: (the terms, the heard phrases marked fine with a leading !)."""
    terms, allowed = [], set()
    for ln in path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []:
        ln = ln.split("#")[0].strip()
        if ln.startswith("!"):
            allowed.add("".join(tokens(ln[1:])))
        elif tokens(ln):
            terms.append(ln)
    return terms, allowed


def leaks(words: list[tuple], terms: list[str], allowed: set[str], fuzzy: float) -> list[dict]:
    """Where a banned term is in (word, time) pairs: the same words (or their possessive), the same
    letters split or joined another way, or (five letters and up) letters close enough that a
    transcript's spelling of a name still counts. One hit a term and place, the closest wording there:
    {term, heard, t, exact}. Words inside a phrase banned.txt marks fine are never a hit."""
    toks = [(norm(w), w, t) for w, t in words if norm(w)]
    fine: set[int] = set()
    for i in range(len(toks)):
        for n in range(1, 7):
            if "".join(x[0] for x in toks[i:i + n]) in allowed:
                fine |= set(range(i, i + n))
    found = []
    for term in terms:
        tt = tokens(term)
        want = "".join(tt)
        for i in range(len(toks)):
            for n in range(1, len(tt) + 2):     # from one word (the term written as one) to one word more
                win = toks[i:i + n]
                if len(win) < n or set(range(i, i + n)) <= fine:
                    continue
                got = "".join(x[0] for x in win)
                exact = got == want or (got == want + "s" and bool(re.search(r"['\u2019]s\W*$", win[-1][1])))
                ratio = SequenceMatcher(None, want, got, autojunk=False).ratio()
                if exact or (len(want) >= 5 and ratio >= fuzzy):
                    found.append((not exact, -ratio, i, n, {"term": term, "heard": " ".join(x[1] for x in win),
                                                           "t": win[0][2], "exact": exact}))
    out: list[tuple] = []
    for _, _, i, n, h in sorted(found, key=lambda x: x[:4]):
        if not any(o[2]["term"] == h["term"] and i < o[0] + o[1] and o[0] < i + n for o in out):
            out.append((i, n, h))
    return [h for _, _, h in sorted(out, key=lambda x: x[:2])]


def modal_estimate(seconds: float, gpu: str) -> float:
    """Dollars for one short transcription: mostly the container's start, not the audio."""
    try:
        from .modal_app import GPU_PRICES
    except ImportError:
        raise ClipperError("the leak check transcribes on Modal: uv sync --extra modal "
                           f"({wiki.page('Transcribe-on-Modal')})") from None
    return round((180 + seconds / 30) / 3600 * GPU_PRICES.get(gpu or "L4", 0.0), 2)


def heard_words(cfg: dict, d: Path, video: Path, dry_run: bool = False) -> list[tuple[str, float]] | None:
    """The render's words with their times, transcribed on Modal once per file."""
    from . import media, transcribe

    work = d / "work" / "leak"
    work.mkdir(parents=True, exist_ok=True)
    sig = [video.name, video.stat().st_size, int(video.stat().st_mtime)]
    cache = work / f"{video.stem}.words.json"
    old = jsonstate.read(cache, None)
    if old and old["sig"] == sig:
        return [tuple(w) for w in old["words"]]
    tc = {**cfg["transcribe"], "diarize": False}
    tc["modal_gpu"] = cfg["doc"]["leak_gpu"] or tc["modal_gpu"]
    secs = media.duration(video)
    print(f"transcribing {video.name} ({secs / 60:.1f} min) on Modal {tc['modal_gpu'] or 'default GPU'}: "
          f"about ${modal_estimate(secs, tc['modal_gpu']):.2f}", flush=True)
    if dry_run:
        return None
    wav = work / "audio.wav"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    try:
        out, _ = transcribe.transcribe_modal(wav, tc, cfg["paths"]["modal_runs"])
    finally:
        wav.unlink(missing_ok=True)
    words = [(w["word"].strip(), round(float(w.get("start", s["start"])), 2)) for s in out["segments"]
             for w in s.get("words") or [{"word": x, "start": s["start"]} for x in s["text"].split()]]
    jsonstate.write(cache, {"sig": sig, "words": words})
    return words


def leak(cfg: dict, slug: str, file: Path | None = None, dry_run: bool = False) -> None:
    """Fail when the render says, or the kit writes, anything banned.txt lists."""
    from .timefmt import clock

    d, _ = load_plan(cfg, slug)
    terms, allowed = read_banned(d / BANNED)
    if not terms:
        raise ClipperError(f"{d / BANNED} lists nothing: a leak check with no terms proves nothing")
    if file is None:
        done = sorted((d / "renders").glob("*.master.mp4"), key=lambda p: p.stat().st_mtime)
        if not done:
            raise ClipperError(f"no mastered render in {d / 'renders'} (clipper doc master {slug}), or pass --file")
        file = done[-1]
    words = heard_words(cfg, d, Path(file), dry_run)
    if words is None:
        return
    fuzzy = cfg["doc"]["leak_fuzzy"]
    hits = leaks(words, terms, allowed, fuzzy)
    kit_hits = []
    if (d / "kit.md").exists():
        kit_hits = leaks([(w, None) for w in (d / "kit.md").read_text(encoding="utf-8").split()], terms, allowed, fuzzy)
    for h in hits:
        print(f"  {clock(h['t'])}  heard {h['heard']!r}: {'is' if h['exact'] else 'sounds like'} a banned term")
    for h in kit_hits:
        print(f"  kit.md  {h['heard']!r}: {'is' if h['exact'] else 'looks like'} a banned term")
    if hits or kit_hits:
        raise ClipperError(f"leak check failed: {len(hits)} in {Path(file).name}, {len(kit_hits)} in kit.md. Fix the "
                           f"cut (mute = [[from, to]] on the clip) or, for a false alarm, add `!the heard words` "
                           f"to {BANNED}")
    print(f"leak check passed: {len(words)} words heard in {Path(file).name}, {len(terms)} terms, none found. It "
          "hears only the sound: read the on-screen text, and the channel's older titles and replies, yourself.")


# ---------- the publish kit ----------

def chapter_title(s: dict) -> str:
    if isinstance(s["chapter"], str):
        return s["chapter"]
    name = s["name"].strip().lstrip("0123456789. ").strip() or s["name"]
    return name.capitalize() if name.isupper() else name


def chapters(sections: list[dict], fps: int, total: int, min_s: float = 10.0) -> list[tuple[float, str]]:
    """YouTube chapters from the section markers: the first at 0:00, each at least min_s after
    the one before and before the end (`total` frames; a closer one is left out), and none at
    all under three."""
    out: list[tuple[float, str]] = []
    for s in sections:
        if s["chapter"] is False:
            continue
        t = 0.0 if not out else s["frame"] / fps
        if out and (t - out[-1][0] < min_s or total / fps - t < min_s):
            continue
        out.append((t, chapter_title(s)))
    return out if len(out) >= 3 else []


def kit_text(plan: dict, b: dict, disclosures: list[str]) -> str:
    from .timefmt import clock

    credits = list(dict.fromkeys([*plan["credits"], *(m["credit"] for m in plan["music"] if m["credit"]),
                                  *([plan["room_tone"]["credit"]] if plan["room_tone"] and plan["room_tone"]["credit"] else [])]))
    ch = chapters(b["sections"], b["fps"], b["total"])
    desc = [plan["description"], ""]
    if ch:
        desc += ["Chapters", *(f"{clock(t)} {title}" for t, title in ch), ""]
    if credits:
        desc += ["Credits", *credits, ""]
    if disclosures:
        desc += [*disclosures, ""]
    out = [f"# {plan['title']}", "", f"Length {clock(b['total'] / b['fps'])}.", "", "## Title", "", plan["title"], "",
           "## Description", "", "```", *desc[:-1], "```", ""]
    if not ch:
        out += ["No chapters: YouTube needs three or more sections, each ten seconds or longer.", ""]
    out += ["## Pinned comment", "", plan["pinned"] or "(none written: `pinned` in plan.toml)", "",
            "## Before publishing", "",
            "- `clipper doc leak` passed on the file you upload, and you watched it for on-screen text.",
            "- The channel's older uploads, titles and replies don't carry anything on the banned list.",
            "- Studio: the altered or synthetic content box, when the narration is an AI voice.",
            "- You listened to the whole master on speakers and on a phone.", ""]
    return "\n".join(out)


def kit(cfg: dict, slug: str) -> Path:
    d, plan = load_plan(cfg, slug)
    b = jsonstate.read(d / "work" / "build.json", None)
    if not b:
        raise ClipperError(f"no build yet: the chapters come from it (clipper doc build {slug} --dry-run)")
    dc = cfg["doc"]
    text = kit_text(plan, b, dc["disclosures"] if plan["disclosures"] is None else plan["disclosures"])
    terms, allowed = read_banned(d / BANNED)
    out = d / "kit.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    if hits := leaks([(w, None) for w in text.split()], terms, allowed, dc["leak_fuzzy"]):
        raise ClipperError(f"{out}: it has what {BANNED} lists: " + "; ".join(f"{h['heard']!r}" for h in hits[:8]))
    print(f"-> {out}")
    return out
