"""`clipper speakers`: who is talking, by name (issue #52).

Usage:
    clipper speakers <stream>                    name the diarized speakers, list them with lines
    clipper speakers <stream> --diarize          diarize first: a stream transcribed without
                                                 [transcribe] diarize (one Modal call, no new transcript)
    clipper speakers <stream> --name SPEAKER_01=Andy [--enroll]
                                                 name one by hand ("SPEAKER_01=" clears it);
                                                 --enroll keeps that voice for the show

diarization.json (from `clipper transcribe` with [transcribe] diarize, or --diarize) holds
pyannote's speaker turns and one voice embedding per speaker. Each speaker with at least
[speakers] min_talk_s of talk is matched against the show's enrolled voices ([paths] voices,
<show>.json) by cosine similarity, named when the best is at least [speakers] match; names set
by hand win. words.jsonl gets `speaker` = that name, or the SPEAKER_xx label when nobody
matched; speakers.json keeps the mapping, scores and talk time. Turns are audio times, so they
stay valid when the stream is transcribed again. `clipper transcribe` runs this after it writes
words.jsonl when diarization.json exists.
"""

from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict
from pathlib import Path

from . import jsonstate
from .errors import ClipperError


def voices_path(cfg: dict) -> Path:
    return Path(cfg["paths"]["voices"]) / f"{cfg['show']['key'] or 'default'}.json"


def load_voices(cfg: dict) -> dict:
    try:
        return json.loads(voices_path(cfg).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"model": "", "voices": {}}


def cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def label_words(words: list[dict], turns: list[list]) -> list[str | None]:
    """The SPEAKER_xx of each word: the turn it overlaps most (turns can overlap: crosstalk)."""
    turns = sorted(turns)
    out: list[str | None] = [None] * len(words)
    active: list[list] = []
    j = 0
    for i in sorted(range(len(words)), key=lambda k: words[k].get("start", 0.0)):
        w = words[i]
        if "start" not in w:
            continue
        start, end = w["start"], max(w["end"], w["start"])
        while j < len(turns) and turns[j][0] < end:
            active.append(turns[j])
            j += 1
        active = [t for t in active if t[1] > start]
        best, best_s = None, 0.0
        for a, b, spk in active:
            s = min(b, end) - max(a, start)
            if s > best_s or (best is None and a <= start <= b):
                best, best_s = spk, s
        out[i] = best
    return out


def apply(sdir: Path, cfg: dict) -> dict:
    """Name each speaker and write speakers.json and the `speaker` field of words.jsonl."""
    d = json.loads((sdir / "diarization.json").read_text(encoding="utf-8"))
    sc = cfg["speakers"]
    bank = load_voices(cfg)
    if bank["voices"] and bank["model"] != d["model"]:
        print(f"enrolled voices are from {bank['model']}, this stream from {d['model']}: not matching")
        bank = {"model": "", "voices": {}}
    try:
        old = json.loads((sdir / "speakers.json").read_text(encoding="utf-8"))["speakers"]
    except FileNotFoundError:
        old = {}
    talk: dict[str, float] = defaultdict(float)
    for a, b, spk in d["turns"]:
        talk[spk] += b - a

    speakers = {}
    for label in sorted(set(talk) | set(d["embeddings"])):
        emb = d["embeddings"].get(label)
        closest, score = "", 0.0
        if emb and talk[label] >= sc["min_talk_s"]:
            for name, refs in bank["voices"].items():
                s = max(cosine(emb, r) for r in refs)
                if s > score:
                    closest, score = name, s
        entry = {"talk_s": round(talk[label], 1), "closest": closest, "match": round(score, 3)}
        if old.get(label, {}).get("by") == "hand":
            entry.update(name=old[label]["name"], by="hand")
        elif closest and score >= sc["match"]:
            entry.update(name=closest, by="voice")
        speakers[label] = entry
    (sdir / "speakers.json").write_text(
        json.dumps({"model": d["model"], "voices": str(voices_path(cfg)), "speakers": speakers}, indent=1),
        encoding="utf-8")

    wpath = sdir / "words.jsonl"
    if wpath.exists():
        words = [json.loads(line) for line in wpath.read_text(encoding="utf-8").splitlines() if line]
        for w, label in zip(words, label_words(words, d["turns"])):
            w["speaker"] = speakers.get(label, {}).get("name", label) if label else None
        tmp = wpath.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(w, ensure_ascii=False) + "\n" for w in words), encoding="utf-8")
        tmp.replace(wpath)
    return speakers


def set_names(sdir: Path, names: dict[str, str]) -> None:
    """Hand names ({"SPEAKER_01": "Andy"}; "" clears one) kept in speakers.json for apply()."""
    path = sdir / "speakers.json"
    if not path.exists():
        raise ClipperError(f"no speakers.json in {sdir}: run `clipper speakers {sdir.name}` first")
    data = json.loads(path.read_text(encoding="utf-8"))
    for label, name in names.items():
        if label not in data["speakers"]:
            raise ClipperError(f"no {label} in this stream; speakers: {', '.join(data['speakers'])}")
        entry = data["speakers"][label]
        entry.pop("name", None)
        entry.pop("by", None)
        if name:
            entry.update(name=name, by="hand")
    jsonstate.write(path, data, indent=1)


def enroll(sdir: Path, cfg: dict, labels: list[str]) -> None:
    """Add these speakers' voices to the show's enrolled voices under their names."""
    d = json.loads((sdir / "diarization.json").read_text(encoding="utf-8"))
    names = json.loads((sdir / "speakers.json").read_text(encoding="utf-8"))["speakers"]
    bank = load_voices(cfg)
    if bank["voices"] and bank["model"] != d["model"]:
        raise ClipperError(f"{voices_path(cfg)} holds {bank['model']} voices; this stream is {d['model']}")
    bank["model"] = d["model"]
    for label in labels:
        name = names[label].get("name")
        if not name or label not in d["embeddings"]:
            raise ClipperError(f"{label}: needs a name and a voice embedding to enroll")
        refs = bank["voices"].setdefault(name, [])
        refs.append(d["embeddings"][label])
        del refs[:-cfg["speakers"]["max_refs"]]
        print(f"enrolled {name} from {sdir.name} {label} ({len(refs)} sample(s))")
    path = voices_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonstate.write(path, bank, indent=None)


def hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def show(sdir: Path, cfg: dict, speakers: dict, lines: int = 3) -> None:
    """Each speaker: name (or closest voice), talk time, and a few of their longest turns."""
    bank = load_voices(cfg)
    show_key = cfg["show"]["key"] or "default"
    if bank["voices"]:
        print(f"{show_key} voices: " + ", ".join(f"{n} ({len(r)})" for n, r in sorted(bank["voices"].items())))
    else:
        print(f"no enrolled voices for {show_key} yet: name speakers with --name SPEAKER_xx=Name --enroll")
    d = json.loads((sdir / "diarization.json").read_text(encoding="utf-8"))
    wpath = sdir / "words.jsonl"
    words = [json.loads(line) for line in wpath.read_text(encoding="utf-8").splitlines() if line] \
        if wpath.exists() else []
    starts = [w["start"] for w in words]
    for label, e in sorted(speakers.items(), key=lambda kv: -kv[1]["talk_s"]):
        if e.get("name"):
            score = f", {e['match']:.2f}" if e["by"] == "voice" else ""
            who = f"{e['name']} ({e['by']}{score})"
        else:
            who = f"? (closest {e['closest']} {e['match']:.2f})" if e["closest"] else "?"
        print(f"\n{label}  {who}  {e['talk_s'] / 60:.1f} min")
        turns = sorted((t for t in d["turns"] if t[2] == label), key=lambda t: t[0] - t[1])[:lines]
        for a, b, _ in sorted(turns):
            i, k = bisect.bisect_left(starts, a), bisect.bisect_left(starts, b)
            text = " ".join(w["word"] for w in words[i:k])
            print(f"  {hms(a)}  {text[:110]}{'...' if len(text) > 110 else ''}")
