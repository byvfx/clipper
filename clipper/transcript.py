"""The transcript as the stages read it: words.jsonl (with speaker names once
`clipper speakers` has named voices) and the "[start_s] [who] text" lines prompts get."""
from __future__ import annotations

import json
import re
from pathlib import Path

UNNAMED = re.compile(r"SPEAKER_\d+")


def load_words(stream_dir: Path) -> list[dict]:
    """words.jsonl. When `clipper speakers` named at least one voice (other than music),
    each word also gets `who`: the name, "?" for a voice nobody matched, None for no turn."""
    lines = (stream_dir / "words.jsonl").read_text(encoding="utf-8").splitlines()
    words = [json.loads(line) for line in lines]
    names = {w.get("speaker") for w in words} - {None, "music"}
    if any(not UNNAMED.fullmatch(n) for n in names):
        for w in words:
            spk = w.get("speaker")
            w["who"] = "?" if spk and UNNAMED.fullmatch(spk) else spk
    return words


TURN_MIN_WORDS = 3  # a shorter change of voice inside a line is a turn edge a word off


def transcript_lines(words: list[dict], a: float, b: float, speakers: bool = True) -> list[str]:
    """One line per WhisperX segment overlapping [a, b): "[start_s] text". With speaker names
    (load_words `who`), a line also splits where the voice changes, and the first line of each
    turn is tagged: "[start_s] [PPP] text", "[?]" for an unnamed voice, "[music]"."""
    tags = speakers and bool(words) and "who" in words[0]
    out, cur, seg = [], [], None
    for w in words:
        if w["end"] < a or w["start"] >= b:
            continue
        if w["seg"] != seg and cur:
            out.append(cur)
            cur = []
        seg = w["seg"]
        cur.append(w)
    if cur:
        out.append(cur)
    if not tags:
        return [_line(line) for line in out]
    lines, said = [], None
    for line in out:
        for spk, run in turns(line):
            tag = ("?" if UNNAMED.fullmatch(spk) else spk) if spk and spk != said else ""
            lines.append(_line(run, tag))
            said = spk or said
    return lines


def turns(line: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """A line's words split by speaker. Words with no turn stay with the run they are in;
    runs under TURN_MIN_WORDS words join their longer neighbour (pyannote's turns and
    WhisperX's word times disagree by a word or so at each change)."""
    runs: list[list] = []
    for w in line:
        s = w.get("speaker")
        if runs and (s is None or s == runs[-1][0] or runs[-1][0] is None):
            runs[-1][0] = runs[-1][0] or s
            runs[-1][1].append(w)
        else:
            runs.append([s, [w]])
    while len(runs) > 1:
        i = min(range(len(runs)), key=lambda k: len(runs[k][1]))
        if len(runs[i][1]) >= TURN_MIN_WORDS:
            break
        j = i - 1 if i == len(runs) - 1 or (i and len(runs[i - 1][1]) >= len(runs[i + 1][1])) else i + 1
        lo = min(i, j)
        runs[lo:lo + 2] = [[runs[j][0], runs[lo][1] + runs[lo + 1][1]]]
        if lo and runs[lo - 1][0] == runs[lo][0]:  # the merge can make two neighbours one voice
            runs[lo - 1:lo + 1] = [[runs[lo][0], runs[lo - 1][1] + runs[lo][1]]]
        if lo + 1 < len(runs) and runs[lo + 1][0] == runs[lo][0]:
            runs[lo:lo + 2] = [[runs[lo][0], runs[lo][1] + runs[lo + 1][1]]]
    return [(s, ws) for s, ws in runs]


def _line(line: list[dict], tag: str = "") -> str:
    return f"[{line[0]['start']:.1f}] " + (f"[{tag}] " if tag else "") + " ".join(x["word"] for x in line)


def snap(words: list[dict], t: float, key: str) -> float:
    i = min(range(len(words)), key=lambda k: abs(words[k][key] - t))
    return words[i][key]
