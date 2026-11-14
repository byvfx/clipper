"""`clipper perform`: a lip-sync take to a script read, cut for the character model (#58).

Usage:
    clipper perform prepare <recording> --read <read.wav> [--take N] [--out DIR]
    clipper perform status <dir>
    clipper perform stitch <dir>
    clipper perform lipsync <dir> [--engine latentsync|sync|both]   (--deploy: the Modal app)

"Perform to playback" (#57): the performer plays the character's read (`clipper narrate
speak`) out loud and lip-syncs to it on camera, one or more takes in one recording.

prepare  finds every take in the recording by cross-correlating its audio with the read (the
         phone hears the playback), keeps take N (default 1), finds the read's pauses, and cuts
         the take into pieces of at most [perform] max_piece_s (WAN 3 Prime takes 15 s of
         reference video) at those pauses. Writes DIR/pieceN.mp4 (30 fps, under the upload
         size) and DIR/manifest.json.
render   is not a clipper step: the pieces go through the character model (Runway WAN 3 Prime
         with Mother Moo's hero image, via the Runway connection in Claude Code or the Runway
         web app; the Max plan's credits aren't reachable from an API key). Save each result
         as DIR/renderN.mp4.
stitch   places each render at its exact read time (the model rounds lengths down to whole
         seconds, so a short render holds its last frame over the gap, inside a pause), lays
         the read under it and writes DIR/final.mp4 plus DIR/final_phone.mp4 (small enough to
         send to a phone).
lipsync  redraws the mouth of final.mp4 to match the read exactly (the performer's own lip
         sync is rarely perfect): LatentSync 1.6 on Modal (clipper/modal_lipsync.py, ~9 min and
         ~$0.30 for 50 s on an L40S) or the sync.so API ([perform] sync_model; key in
         $SYNC_API_KEY or secrets/sync_api_key.txt). Writes final.lipsync-<engine>.mp4.
"""

from __future__ import annotations

import json
import re
import subprocess
from itertools import pairwise
from pathlib import Path

import numpy as np

from . import jsonstate, media
from .errors import ClipperError

SR = 8000  # analysis sample rate for take detection


def pcm(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32)
    return x - x.mean()


def find_takes(rec: np.ndarray, read: np.ndarray) -> list[tuple[float, float]]:
    """(start seconds, score) of each place the read plays in the recording, in time order."""
    n = 1 << int(np.ceil(np.log2(len(rec) + len(read))))
    c = np.abs(np.fft.irfft(np.fft.rfft(rec, n) * np.conj(np.fft.rfft(read, n)), n)[:len(rec)])
    c /= c.max() or 1.0
    takes, gap = [], int(len(read) * 0.8)
    while True:
        i = int(np.argmax(c))
        if c[i] < 0.5:  # well below the strongest match: no more takes
            break
        takes.append((i / SR, float(c[i])))
        c[max(0, i - gap):i + gap] = 0
    return sorted(takes)


def pauses(read: Path, pc: dict) -> list[tuple[float, float]]:
    """(start, end) of the silences in the read (a TTS read has clean ones)."""
    log = subprocess.run(["ffmpeg", "-v", "info", "-i", str(read), "-af",
                          f"silencedetect=noise={pc['silence_db']}dB:d={pc['silence_min_s']}", "-f", "null", "-"],
                         check=True, capture_output=True, text=True).stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", log)]
    return list(zip(starts, ends))


def cut_points(sil: list[tuple[float, float]], end: float, pc: dict) -> list[float]:
    """Greedy: each piece runs as long as it can (max_piece_s) and ends inside a pause, at
    least 0.05 s after the last word; a stretch with no pause gets a hard cut."""
    cuts, start = [0.0], 0.0
    while end - start > pc["max_piece_s"]:
        limit = start + pc["max_piece_s"]
        ok = [min(b - 0.05, limit) for a, b in sil
              if start + pc["min_piece_s"] <= a + 0.05 <= limit and b - a > 0.15
              and a + 0.05 < end - pc["min_piece_s"]]
        start = round(max(ok) if ok else limit, 3)
        cuts.append(start)
    return cuts + [round(end, 3)]


def prepare(recording: Path, read: Path, pc: dict, take: int = 1, out: Path | None = None) -> Path:
    out = out or recording.with_name(f"{recording.stem}.perform")
    out.mkdir(parents=True, exist_ok=True)
    rd = pcm(read)
    takes = find_takes(pcm(recording), rd)
    if not takes:
        raise ClipperError(f"the read isn't audible in {recording.name}: play it out loud while filming")
    for k, (t, s) in enumerate(takes, 1):
        print(f"take {k}: starts at {t:.2f} s (match {s:.2f})")
    if not 1 <= take <= len(takes):
        raise ClipperError(f"--take {take}: the recording has {len(takes)} take(s)")
    t0 = takes[take - 1][0]
    end = min(len(rd) / SR + pc["tail_s"], media.duration(recording) - t0)
    cuts = cut_points(pauses(read, pc), end, pc)
    pieces = []
    for i, (a, b) in enumerate(pairwise(cuts), 1):
        f = out / f"piece{i}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0 + a:.3f}", "-t", f"{b - a:.3f}",
                        "-i", str(recording), "-vf", f"fps={pc['fps']},format=yuv420p", "-c:v", "libx264",
                        "-crf", str(pc["crf"]), "-preset", "slow", "-c:a", "aac", "-b:a", "128k", "-ac", "1",
                        "-movflags", "+faststart", str(f)], check=True)
        pieces.append({"piece": f.name, "render": f"render{i}.mp4", "start": a, "end": b})
        mb = f.stat().st_size / 2**20
        print(f"  {f.name}: read {a:6.2f}-{b:6.2f} s ({b - a:.2f} s, {mb:.0f} MB)"
              + ("  <- over the 30 MB single upload" if mb > 30 else ""))
    manifest = {"recording": str(recording.resolve()), "read": str(read.resolve()), "take": take,
                "take_start": round(t0, 3), "end": round(end, 3), "pieces": pieces}
    jsonstate.write(out / "manifest.json", manifest)
    print(f"{out}: {len(pieces)} pieces; render each with the character model and save it as renderN.mp4,"
          f" then: clipper perform stitch {out}")
    return out


def status(d: Path) -> dict:
    m = jsonstate.load(d / "manifest.json")
    for p in m["pieces"]:
        r = d / p["render"]
        got = f"{media.duration(r):.2f} s rendered" if r.exists() else "not rendered yet"
        print(f"{p['piece']}: read {p['start']:.2f}-{p['end']:.2f} s -> {p['render']}: {got}")
    return m


def stitch(d: Path, pc: dict) -> Path:
    m = status(d)
    missing = [p["render"] for p in m["pieces"] if not (d / p["render"]).exists()]
    if missing:
        raise ClipperError(f"not rendered yet: {', '.join(missing)}")
    inputs, chains = [], []
    for i, p in enumerate(m["pieces"]):
        slot = p["end"] - p["start"]
        inputs += ["-i", str(d / p["render"])]
        chains.append(f"[{i}:v]fps={pc['fps']},scale=1920:1080,setsar=1,trim=0:{slot:.3f},setpts=PTS-STARTPTS,"
                      f"tpad=stop_mode=clone:stop_duration={slot:.3f},trim=0:{slot:.3f},setpts=PTS-STARTPTS[v{i}]")
    n = len(m["pieces"])
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v];" \
        f"[{n}:a]aresample=48000,apad=whole_dur={m['end']:.3f}[a]"
    final = d / "final.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-i", m["read"], "-filter_complex", graph,
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", "-t", f"{m['end']:.3f}", "-movflags", "+faststart", str(final)],
                   check=True)
    phone = d / "final_phone.mp4"
    # 720p keeps a minute under the 30 MB a phone message takes.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(final), "-vf", "scale=-2:720", "-c:v", "libx264",
                    "-crf", "24", "-preset", "slow", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                    str(phone)], check=True)
    print(f"{final} ({media.duration(final):.1f} s), {phone.name} ({phone.stat().st_size / 2**20:.0f} MB)")
    return final


# ---------- lip-sync pass ----------

def sync_key(pc: dict) -> str:
    import os

    k = os.environ.get(pc["sync_key_env"], "").strip()
    path: Path = pc["sync_key_file"]
    if not k and path.exists():
        k = path.read_text(encoding="utf-8-sig").strip()
    if not k:
        raise ClipperError(f"no Sync API key: set ${pc['sync_key_env']} or put it in {path}")
    return k


def lipsync_latentsync(video: Path, audio_wav: Path, pc: dict) -> bytes:
    import modal

    fn = modal.Function.from_name(pc["latentsync_app"], "lipsync")
    out = fn.remote(video.read_bytes(), audio_wav.read_bytes(), pc["latentsync_steps"], pc["latentsync_guidance"])
    print(f"LatentSync: {out['seconds']:.0f} s on Modal")
    return out["video"]


def lipsync_sync(video: Path, audio_wav: Path, pc: dict) -> bytes:
    import time

    import requests

    h = {"x-api-key": sync_key(pc)}
    with open(video, "rb") as v, open(audio_wav, "rb") as a:
        r = requests.post("https://api.sync.so/v2/generate", headers=h, timeout=600,
                          data={"model": pc["sync_model"], "options": json.dumps({"sync_mode": "cut_off"})},
                          files={"video": (video.name, v, "video/mp4"), "audio": (audio_wav.name, a, "audio/wav")})
    if not r.ok:
        raise ClipperError(f"Sync: HTTP {r.status_code}: {r.text[:300]}")
    gid = r.json()["id"]
    t0 = time.time()
    errors = 0
    while True:  # #77 M13: a network blip doesn't lose the paid job; it's never polled forever
        time.sleep(10)
        if time.time() - t0 > 3600:
            raise ClipperError(f"Sync job {gid} still not done after an hour; check sync.so")
        try:
            s = requests.get(f"https://api.sync.so/v2/generate/{gid}", headers=h, timeout=60).json()
        except (requests.RequestException, ValueError) as e:
            errors += 1
            if errors >= 10:
                raise ClipperError(f"Sync job {gid}: polling failed 10 times ({e}); check sync.so") from None
            continue
        errors = 0
        if s.get("status") == "COMPLETED":
            print(f"Sync {pc['sync_model']}: {time.time() - t0:.0f} s")
            return requests.get(s["outputUrl"], timeout=600).content
        if s.get("status") in ("FAILED", "REJECTED", "CANCELED", "CANCELLED"):
            raise ClipperError(f"Sync {s.get('status')}: {s.get('errorCode')} {s.get('error')}")


def lipsync(target: Path, pc: dict, engine: str, audio: Path | None = None) -> Path:
    """Redraw the mouth of `target` (a perform dir's final.mp4, or any video) to match the
    read (the manifest's, or `audio`), with LatentSync on Modal or the Sync API. The output keeps
    the read as its audio: <video>.lipsync-<engine>.mp4."""
    import tempfile

    video = target / "final.mp4" if target.is_dir() else target
    if audio is None:
        audio = Path(jsonstate.load(target / "manifest.json")["read"])
    out = video.with_name(f"{video.stem}.lipsync-{engine}.mp4")
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "read.wav"
        # Padded to the video's length: the models stop where the audio stops (the final hold).
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-af", f"apad=whole_dur={media.duration(video):.3f}",
                        "-ac", "1", "-ar", "16000", str(wav)], check=True)
        raw = Path(td) / "raw.mp4"
        raw.write_bytes((lipsync_latentsync if engine == "latentsync" else lipsync_sync)(video, wav, pc))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-i", str(audio), "-map", "0:v", "-map", "1:a",
                        "-af", f"apad=whole_dur={media.duration(raw):.3f}", "-c:v", "libx264", "-crf", "16", "-preset",
                        "medium", "-c:a", "aac", "-b:a", "192k", "-t", f"{media.duration(raw):.3f}", "-movflags", "+faststart",
                        str(out)], check=True)
    print(f"{out} ({media.duration(out):.1f} s)")
    return out


def deploy_lipsync() -> None:
    import sys

    from . import modal_lipsync

    subprocess.run([sys.executable, "-m", "modal", "deploy", modal_lipsync.__file__], check=True)
