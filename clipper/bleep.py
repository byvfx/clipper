"""Slur bleeping: find listed words in the WhisperX word timings, cover them in the audio
with a tone (or silence), and mask them in captions and text.

Patterns in [bleep] patterns are regexes matched against whole words, lowercased with
punctuation stripped ("Retarded," -> "retarded"), so a stem pattern like
"retard(s|ed)?" catches its forms without hitting longer, innocent words.
"""

from __future__ import annotations

import re
from pathlib import Path

PUNCT = re.compile(r"[^\w'*@$-]+")


class Bleeper:
    def __init__(self, bc: dict):
        self.mode = bc["mode"]
        self.pad = bc["pad_s"]
        self.freq = bc["tone_hz"]
        self.level = bc["tone_level"]
        # Slurs always; swearing too when [bleep] profanity is on.
        pats = list(bc["patterns"]) + (list(bc.get("profanity_patterns") or [])
                                       if bc.get("profanity") else [])
        self.rx = re.compile("|".join(f"(?:{p})" for p in pats), re.I) if pats else None

    @property
    def on(self) -> bool:
        return self.mode in ("bleep", "mute") and self.rx is not None

    def hit(self, word: str) -> bool:
        token = re.sub(r"['’]s$", "", PUNCT.sub("", word).strip("'-").lower())  # retard's
        return bool(self.rx and token and self.rx.fullmatch(token))

    def mask(self, text: str) -> str:
        """Mask listed words in free text: first letter kept, rest starred."""
        if not self.rx:
            return text

        def sub(m: re.Match) -> str:
            w = m.group(0)
            return w[0] + "*" * (len(w) - 1) if self.hit(w) else w

        return re.sub(r"[\w'@$*-]+", sub, text)

    def intervals(self, words: list[dict], a: float, b: float, t0: float) -> list[tuple]:
        """Spans to cover for source range [a, b), on a timeline where source time t0 is 0
        (the point ffmpeg seeks to, which is what its filters see as t=0)."""
        spans = [(max(w["start"] - self.pad, t0) - t0, w["end"] + self.pad - t0)
                 for w in words if a - 1 <= w["start"] < b and self.hit(w["word"])]
        spans = [(s, e) for s, e in spans if e > s]  # a word ending before the cut point
        merged: list[list[float]] = []
        for s, e in sorted(spans):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        return [tuple(x) for x in merged]

    def filter_script(self, spans: list[tuple], duration: float, path: Path) -> Path:
        """ffmpeg filtergraph: input audio 0:a -> [aout] with the spans bleeped or muted.
        The spans go in a command file (<path>.cmd) that asendcmd plays: at each span it
        mutes the speech (volume@dry) and switches the tone on (volume@tone). One big
        between()+... expression instead breaks ffmpeg's parser past a few dozen spans."""
        cmds = []
        for s, e in spans:
            acts = ["[enter] volume@dry volume 0", "[leave] volume@dry volume 1"]
            if self.mode == "bleep":
                acts += [f"[enter] volume@tone volume {self.level}", "[leave] volume@tone volume 0"]
            cmds.append(f"{s:.3f}-{e:.3f} " + ", ".join(acts) + ";")
        cmd = path.with_suffix(".cmd")
        cmd.write_text("\n".join(cmds) + "\n", encoding="utf-8")
        esc = cmd.resolve().as_posix().replace(":", "\\:")  # filtergraph-escaped (C\:/...)
        graph = f"[0:a]asendcmd=f='{esc}',volume@dry=1"
        if self.mode == "bleep":
            graph += (f"[dry];sine=frequency={self.freq}:sample_rate=48000:duration={duration + 1:.3f},"
                      f"aformat=channel_layouts=stereo,volume@tone=0[tone];"
                      f"[dry][tone]amix=inputs=2:normalize=0:duration=first[aout]")
        else:
            graph += "[aout]"
        path.write_text(graph, encoding="utf-8")
        return path
