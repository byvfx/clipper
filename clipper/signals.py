"""Stage 3: per-second signals -> heat curve -> top N candidate windows.

Reads meta.json, chat.jsonl (or chat_parts/ while a chat pull is still
running), audio.wav, and words.jsonl (for previews only). Writes:
    signals.parquet  one row per second of stream
    windows.json     top windows by heat, with a transcript/chat preview

Chat is shifted earlier by chat_lag_s so a reaction lines up with the moment
that caused it. Each signal is turned into an excess over a rolling median
baseline, divided by a stream-wide robust scale, and clipped; heat is the
weighted sum of the positive parts.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import find_peaks

from .kick import CHAT_CHUNK_S

EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")


# ---------- inputs ----------

def load_chat(stream_dir: Path, meta: dict, n: int) -> tuple[list[dict], np.ndarray]:
    """Return messages (with t in stream seconds) and a per-second coverage mask."""
    full = stream_dir / "chat.jsonl"
    covered = np.zeros(n, bool)
    if full.exists():
        files = [full]
        covered[:] = True
    else:
        files = sorted((stream_dir / "chat_parts").glob("*.jsonl"))
    t0 = datetime.fromisoformat(meta["started_at"]).timestamp()
    msgs = []
    for f in files:
        if f is not full:
            a = int(f.stem) - t0
            covered[max(0, int(a)):min(n, int(math.ceil(a + CHAT_CHUNK_S)))] = True
        for line in f.read_text(encoding="utf-8").splitlines():
            m = json.loads(line)
            if f is not full or "t" not in m:
                m["t"] = m["ts"] - t0   # live recordings with gaps map t part by part
            if 0 <= m["t"] < n:
                msgs.append(m)
    msgs.sort(key=lambda m: m["t"])
    return msgs, covered


def wav_int16(path: Path) -> tuple[np.ndarray, int]:
    """Memory-map a 16-bit mono PCM wav (as written by the transcribe stage)."""
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"{path} is not a wav file")
        sr = channels = bits = 0
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise ValueError(f"{path}: no data chunk")
            cid, size = hdr[:4], int.from_bytes(hdr[4:], "little")
            if cid == b"fmt ":
                fmt = f.read(size)
                channels = int.from_bytes(fmt[2:4], "little")
                sr = int.from_bytes(fmt[4:8], "little")
                bits = int.from_bytes(fmt[14:16], "little")
                f.seek(size & 1, 1)
            elif cid == b"data":
                offset = f.tell()
                break
            else:
                f.seek(size + (size & 1), 1)
    if channels != 1 or bits != 16:
        raise ValueError(f"{path}: need 16-bit mono, got {channels} ch {bits} bit")
    count = (os.path.getsize(path) - offset) // 2
    return np.memmap(path, np.int16, "r", offset, (count,)), sr


def loudness_db(path: Path, n: int, block_s: int = 600) -> np.ndarray:
    pcm, sr = wav_int16(path)
    out = np.full(n, -90.0)
    for s in range(0, n, block_s):
        e = min(n, s + block_s, len(pcm) // sr)
        if e <= s:
            break
        x = np.asarray(pcm[s * sr:e * sr], np.float32).reshape(e - s, sr) / 32768.0
        out[s:e] = 10 * np.log10(np.mean(x * x, axis=1) + 1e-9)
    return out


# ---------- signal math ----------

def shift_earlier(x: np.ndarray, lag: int) -> np.ndarray:
    if lag <= 0:
        return x
    y = np.zeros_like(x)
    y[:-lag] = x[lag:]
    return y


def rolling_baseline(x: np.ndarray, covered: np.ndarray, baseline_s: int) -> np.ndarray:
    """Rolling median, computed separately over each covered run."""
    base = np.full(len(x), np.nan)
    edges = np.flatnonzero(np.diff(np.r_[0, covered.astype(np.int8), 0]))
    for a, b in zip(edges[::2], edges[1::2]):
        base[a:b] = median_filter(x[a:b], size=min(baseline_s, b - a), mode="nearest")
    return base


def soft_cap(z: np.ndarray, cap: float) -> np.ndarray:
    """Compress large z toward cap without flattening their order."""
    return cap * np.tanh(z / cap)


def mad(x: np.ndarray) -> float:
    return 1.4826 * float(np.median(np.abs(x - np.median(x))))


def level_z(x: np.ndarray, covered: np.ndarray, baseline_s: int, cap: float) -> np.ndarray:
    """Continuous signal (dB): excess over rolling median / stream-wide MAD."""
    excess = x - rolling_baseline(x, covered, baseline_s)
    ok = ~np.isnan(excess)
    z = np.full(len(x), np.nan)
    if ok.any():
        z[ok] = soft_cap(excess[ok] / max(mad(excess[ok]), 1e-6), cap)
    return z


def count_z(per_s: np.ndarray, covered: np.ndarray, baseline_s: int, smooth_s: int,
            cap: float) -> np.ndarray:
    """Count signal: Poisson z of the smooth_s-second count against its rolling
    median, divided by the stream's overdispersion (MAD of z, if above 1).
    Sparse signals ("clip it") need several messages to register; dense,
    bursty ones (chat rate) are scaled down by how bursty they normally are."""
    w = uniform_filter1d(per_s, size=smooth_s, mode="nearest") * smooth_s
    base = rolling_baseline(w, covered, baseline_s)
    zr = (w - base) / np.sqrt(base + 1.0)
    ok = ~np.isnan(zr)
    z = np.full(len(w), np.nan)
    if ok.any():
        z[ok] = soft_cap(zr[ok] / max(mad(zr[ok]), 1.0), cap)
    return z


def per_second_counts(msgs: list[dict], n: int, react: re.Pattern, clip: re.Pattern):
    total = np.zeros(n)
    react_n = np.zeros(n)
    clip_n = np.zeros(n)
    users: list[set] = [set() for _ in range(n)]
    for m in msgs:
        s = int(m["t"])
        text = EMOTE.sub(r" \1 ", m.get("content") or "")
        total[s] += 1
        users[s].add(m.get("user"))
        if react.search(text):
            react_n[s] += 1
        if clip.search(text):
            clip_n[s] += 1
    return total, np.array([len(u) for u in users], float), react_n, clip_n


# ---------- windows ----------

def pick_windows(heat: np.ndarray, sc: dict) -> list[dict]:
    h = np.nan_to_num(heat, nan=-1.0)
    peaks, props = find_peaks(h, height=sc["min_heat"], distance=sc["min_gap_s"])
    order = peaks[np.argsort(-props["peak_heights"])]
    n = len(h)
    windows: list[dict] = []
    for p in order:
        if len(windows) >= sc["top_n"]:
            break
        a = max(0, int(p) - sc["window_pre_s"])
        b = min(n, int(p) + sc["window_post_s"])
        for w in windows:
            if a < w["end_s"] and b > w["start_s"]:
                lo, hi = min(a, w["start_s"]), max(b, w["end_s"])
                if hi - lo <= sc["max_window_s"]:
                    w["start_s"], w["end_s"] = lo, hi
                    w["peaks"].append(int(p))
                    break
        else:
            windows.append({"start_s": a, "end_s": b, "peak_s": int(p),
                            "heat": float(h[p]), "peaks": [int(p)]})
    return windows


def hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def run(stream_dir: Path, sc: dict) -> list[dict]:
    meta = json.loads((stream_dir / "meta.json").read_text(encoding="utf-8"))
    n = int(math.ceil(meta["duration_s"]))
    lag = sc["chat_lag_s"]

    msgs, covered = load_chat(stream_dir, meta, n)
    react = re.compile(sc["react_regex"])
    clip = re.compile(sc["clip_regex"])
    total, users, react_n, clip_n = per_second_counts(msgs, n, react, clip)
    total, users, react_n, clip_n = (shift_earlier(x, lag) for x in (total, users, react_n, clip_n))
    covered = shift_earlier(covered.astype(np.int8), lag).astype(bool) | covered & (np.arange(n) >= n - lag)

    sm, bs, cap = sc["smooth_s"], sc["baseline_s"], sc["z_cap"]
    from .transcribe import extract_audio  # fetched VODs have no recorder audio.wav yet
    loud = loudness_db(extract_audio(stream_dir), n)
    chat_z = count_z(users, covered, bs, sm, cap)
    react_z = count_z(react_n, covered, bs, sm, cap)
    clip_z = count_z(clip_n, covered, bs, sm, cap)
    loud_z = level_z(uniform_filter1d(loud, size=3, mode="nearest"), np.ones(n, bool), bs, cap)

    pos = lambda z: np.clip(np.nan_to_num(z), 0, None)  # noqa: E731
    heat = (sc["w_chat"] * pos(chat_z) + sc["w_react"] * pos(react_z)
            + sc["w_clip"] * pos(clip_z) + sc["w_loud"] * pos(loud_z))
    heat[~covered] = np.nan  # no chat data -> don't pick from loudness alone

    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({
        "t": np.arange(n, dtype=np.int32), "covered": covered,
        "chat_msgs": total, "chat_users": users, "react": react_n, "clip": clip_n,
        "loud_db": loud, "chat_z": chat_z, "react_z": react_z, "clip_z": clip_z,
        "loud_z": loud_z, "heat": heat,
    }), stream_dir / "signals.parquet")

    windows = pick_windows(heat, sc)
    words = []
    wpath = stream_dir / "words.jsonl"
    if wpath.exists():
        words = [json.loads(line) for line in wpath.read_text(encoding="utf-8").splitlines()]
    wstarts = np.array([w["start"] for w in words])
    mt = np.array([m["t"] for m in msgs])
    for i, w in enumerate(windows, 1):
        p = w["peak_s"]
        w["rank"] = i
        w["chat_z"], w["react_z"], w["clip_z"], w["loud_z"] = (
            None if np.isnan(z[p]) else round(float(z[p]), 2) for z in (chat_z, react_z, clip_z, loud_z))
        w["chat_per_min"] = int(total[max(0, p - 30):p + 30].sum())
        lo, hi = np.searchsorted(wstarts, [p - 15, p + 5]) if len(words) else (0, 0)
        w["transcript_peak"] = " ".join(x["word"] for x in words[lo:hi])
        a, b = np.searchsorted(mt, [p + lag - 5, p + lag + 15])
        w["chat_sample"] = [" ".join(EMOTE.sub(r" \1 ", m["content"]).split()) for m in msgs[a:b]][:10]
    (stream_dir / "windows.json").write_text(
        json.dumps({"stream_id": stream_dir.name, "chat_coverage": round(float(covered.mean()), 3),
                    "params": sc, "windows": windows}, indent=2, ensure_ascii=False),
        encoding="utf-8")

    print(f"{len(msgs)} chat msgs, coverage {covered.mean():.0%}; {len(windows)} windows")
    for w in windows:
        print(f"#{w['rank']:>2} {hms(w['start_s'])}-{hms(w['end_s'])} peak {hms(w['peak_s'])} "
              f"heat {w['heat']:5.1f} chat {w['chat_z']} react {w['react_z']} clip {w['clip_z']} "
              f"loud {w['loud_z']} ({w['chat_per_min']}/min)")
    return windows
