"""HLS for the recorder: pick a variant from the master playlist, parse the live media playlist,
and place each segment against what's already written (repeat, next, gap, new broadcast)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

from .. import kick

log = logging.getLogger("clipper.record")


def pick_variant(master: str, base: str, max_height: int) -> str:
    """Highest variant at or below max_height (0 = best), by height then bandwidth."""
    best, best_key = None, None
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        url = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
        res = re.search(r"RESOLUTION=\d+x(\d+)", line)
        bw = re.search(r"BANDWIDTH=(\d+)", line)
        if not url or not res:
            continue
        h = int(res.group(1))
        key = (h, int(bw.group(1)) if bw else 0)
        if (max_height <= 0 or h <= max_height) and (best_key is None or key > best_key):
            best, best_key = requests.compat.urljoin(base, url), key
    if not best:
        raise LookupError("no video variant in master playlist")
    log.info("variant %dp, %.1f Mb/s", best_key[0], best_key[1] / 1e6)
    return best


@dataclass
class Segment:
    seq: int        # media sequence number: the segment's identity
    pdt: float      # program date-time, epoch s (IVS ingest clock: jitters +-2 s
                    # against the media durations, so it only anchors parts)
    dur: float
    url: str
    title: str
    disc: bool      # EXT-X-DISCONTINUITY before it


def parse_media(text: str, base: str) -> tuple[list[Segment], bool, float]:
    segs, pdt, dur, title, disc = [], None, None, "", False
    target, seq = 2.0, 0
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pdt = kick._parse_ts(line.split(":", 1)[1].strip()).timestamp()
        elif line.startswith("#EXTINF:"):
            d, _, title = line[8:].partition(",")
            dur = float(d)
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            disc = True
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = float(line.split(":", 1)[1])
        elif line and not line.startswith("#") and dur is not None:
            if pdt is not None:
                segs.append(Segment(seq, pdt, dur, requests.compat.urljoin(base, line), title,
                                    disc))
                pdt += dur  # until the next PROGRAM-DATE-TIME tag says otherwise
            seq += 1
            dur, title, disc = None, "", False
    return segs, "#EXT-X-ENDLIST" in text, target


def have(s: Segment, last_seq: int, last_end: float) -> bool:
    """Already written? By sequence number; program date-times only decide whether
    a lower sequence is a new broadcast (sequence reset) rather than a repeat."""
    if last_seq >= 0:
        return s.seq <= last_seq and s.pdt < last_end + 30
    return bool(last_end) and s.pdt + s.dur <= last_end + 0.5   # state from before last_seq


def place(s: Segment, last_seq: int, last_end: float) -> tuple[int, float]:
    """(missing segments, seconds of video missing) before a segment that isn't written yet.
    missing -1: the sequence went back, a new broadcast (the gap is by program date-time)."""
    if last_seq < 0 and not last_end:
        return 0, 0.0                                  # first segment of the recording
    if last_seq >= 0 and last_seq < s.seq:
        missing = s.seq - last_seq - 1                 # same broadcast
        return missing, max(0.0, s.pdt - last_end) if missing else 0.0
    return -1, max(0.0, s.pdt - last_end)              # sequence reset: new broadcast
