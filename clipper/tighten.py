"""Tighten: cut the dead air out of a Short (#70, the first piece of the edits roadmap #66).

A pause between two words longer than [edit] max_gap_s is cut down to about keep_gap_s
(keep_turn_s where the voice changes, so a reply keeps its beat), but only where the audio
is quiet as well (ffmpeg silencedetect at silence_db): a gap in the words is often a laugh,
a video playing or a reaction, and those stay. No cut is shorter than min_cut_s, none
reaches into a word or its bleep, and the Short never drops under [shorts] min_s.

Times here are seconds into the Short's own cut (0 = its first frame). The cuts sit in the
gaps between words, so every word keeps its length and only moves earlier: a `Timeline`
maps a time in the cut to the tightened Short, for the captions and the scene changes.
"""
from __future__ import annotations

import math
import re
import subprocess
from itertools import pairwise
from pathlib import Path

# The [edit] keys that change what tightening cuts (a Short's signature has them).
KEYS = ("max_gap_s", "keep_gap_s", "keep_turn_s", "min_cut_s", "silence_db")


def quiet_spans(video: Path, ec: dict) -> list[tuple[float, float]]:
    """(start, end) of the stretches of `video` quieter than [edit] silence_db for at least
    min_cut_s, in seconds from its start. A silence still going at the end runs to inf."""
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
                          f"silencedetect=noise={ec['silence_db']}dB:d={ec['min_cut_s']}",
                          "-f", "null", "-"], check=True, capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    starts = [max(0.0, float(m)) for m in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", log)]
    return list(zip(starts, ends + [math.inf] * (len(starts) - len(ends))))


def cuts(words: list[dict], a: float, b: float, quiet: list[tuple[float, float]], ec: dict,
         bleep_pad: float = 0.0, max_removed: float = math.inf, fps: float = 30) -> list[dict]:
    """The pauses to cut from the Short a..b (stream seconds): {"from", "to"} in seconds
    into the Short, with the words either side ("before", "after") for the review card.
    quiet: the cut's silences (quiet_spans). max_removed: the most all cuts together may
    take out; the longest pauses go first. Each cut is moved in onto whole frames of fps,
    so the picture and the sound lose exactly the same time (Timeline.select)."""
    ws = [w for w in words if w["start"] >= a and w["end"] <= b]
    found = []
    for w1, w2 in pairwise(ws):
        if w2["start"] - w1["end"] <= ec["max_gap_s"]:
            continue
        s1, s2 = w1.get("speaker"), w2.get("speaker")
        keep = ec["keep_turn_s"] if s1 and s2 and s1 != s2 else ec["keep_gap_s"]
        edge = max(keep / 2, bleep_pad + 0.02)  # never into a word, nor its bleep's padding
        lo, hi = w1["end"] - a + edge, w2["start"] - a - edge
        for qa, qb in quiet:
            x = math.ceil(max(lo, qa) * fps - 1e-6) / fps
            y = math.floor(min(hi, qb) * fps + 1e-6) / fps
            if y - x >= ec["min_cut_s"] - 1e-9:
                found.append({"from": round(x, 3), "to": round(y, 3),
                              "before": w1["word"], "after": w2["word"]})
    chosen, total = [], 0.0
    for c in sorted(found, key=lambda c: c["from"] - c["to"]):
        if total + c["to"] - c["from"] <= max_removed + 1e-9:
            chosen.append(c)
            total += c["to"] - c["from"]
    return sorted(chosen, key=lambda c: c["from"])


class Timeline:
    """The Short with `cuts` taken out of its dur seconds: times in the cut -> the Short."""

    def __init__(self, cuts: list[dict], dur: float):
        self.cuts = [(c["from"], c["to"]) for c in cuts]
        self.dur = dur

    @property
    def removed(self) -> float:
        return round(sum(y - x for x, y in self.cuts), 3)

    @property
    def length(self) -> float:
        return round(self.dur - self.removed, 3)

    def __call__(self, t: float) -> float:
        """Where time t of the cut lands; a time inside a cut lands where the cut is."""
        out = t
        for x, y in self.cuts:
            if t >= y:
                out -= y - x
            elif t > x:
                out -= t - x
        return round(out, 3)

    def keeps(self) -> list[tuple[float, float]]:
        edges = [0.0, *(v for c in self.cuts for v in c), self.dur]
        return list(zip(edges[::2], edges[1::2]))

    def select(self, fps: float = 30) -> str:
        """The kept stretches as an ffmpeg select/aselect expression, by whole frames of fps:
        a frame (or an audio frame of the same length) is kept when it starts in a stretch.
        The edges sit half a frame early so a frame starting right on one is never in doubt."""
        def edge(t):
            return (round(t * fps) - 0.5) / fps
        return "+".join(f"gte(t,{edge(x):.4f})*lt(t,{edge(y):.4f})" for x, y in self.keeps())

    def scenes(self, scenes: list[list]) -> list[list]:
        """[start, end, layout] scenes of the cut, moved onto the Short. A scene that was all
        pause comes out empty (start == end), still in its place."""
        return [[self(s), self(e), n] for s, e, n in scenes]

    def words(self, words: list[dict], a: float) -> list[dict]:
        """Stream-time words with their times moved as the Short moves them (a = its start)."""
        return [{**w, "start": a + self(w["start"] - a), "end": a + self(w["end"] - a)} for w in words]


def label(cs: list[dict]) -> str:
    """The review card's line: "4 pauses, 3.2 s"."""
    if not cs:
        return ""
    n = len(cs)
    return f"{n} pause{'s' * (n != 1)}, {sum(c['to'] - c['from'] for c in cs):.1f} s"
