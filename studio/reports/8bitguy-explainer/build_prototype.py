# Prototype: 8-Bit Guy explainer Short, one picture edit, two mixes (flat vs natural).
# usage: python build.py <workdir>
import json, subprocess, sys, tomllib, wave
from pathlib import Path
import numpy as np

sys.path.insert(0, "C:/clipper")
from clipper import shorts

D = Path(sys.argv[1])
OFF = {k: v["wav_t"] for k, v in json.load(open(D / "offsets.json")).items()}
LAY = tomllib.load(open("C:/clipper/clipper.toml", "rb"))["shows"]["chud"]["shorts"]["layouts"]["watch"]
CROPS = shorts.panel_crops(LAY, 1920, 1080, 1080, 1920)
VIDEO_CROPS = shorts.panel_crops({"panels": [{"src": [0.1563, 0.1259, 0.7167, 0.7407], "h": 1920, "focus": [0.42, 0.5]}]},
                                 1920, 1080, 1080, 1920)
SR = 48000
W = D / "work"; W.mkdir(exist_ok=True)
OUT = D / "out"; OUT.mkdir(exist_ok=True)


def run(cmd):
    subprocess.run(cmd, check=True)


def chunk_t(name, wav_t):
    return wav_t - OFF[name]


def read_audio(path, ss=None, t=None, af=None):
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{ss:.3f}"] if ss is not None else []) + ["-i", str(path)]
    cmd += (["-t", f"{t:.3f}"] if t is not None else []) + ["-vn", "-ac", "2", "-ar", str(SR)]
    cmd += (["-af", af] if af else []) + ["-f", "f32le", "-"]
    a = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype=np.float32)
    return a.reshape(-1, 2).copy()


def write_wav(path, a):
    a = np.clip(a, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((a * 32767).astype(np.int16).tobytes())


def tighten(a, cap=0.36, floor_db=-40):
    """Shorten silences in a read to at most `cap` s; trim its ends to 40 ms."""
    hop = int(SR * 0.01)
    rms = np.sqrt(np.mean(a[: len(a) // hop * hop].reshape(-1, hop, 2) ** 2, axis=(1, 2)) + 1e-12)
    quiet = 20 * np.log10(rms) < floor_db
    loud = np.where(~quiet)[0]
    a = a[max(0, loud[0] * hop - int(SR * .04)): (loud[-1] + 1) * hop + int(SR * .04)]
    quiet = quiet[loud[0]: loud[-1] + 1]
    keep, i, n = [], 0, len(quiet)
    pieces, start = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * 0.01 > cap:
                cut0 = i + int(cap / 2 / 0.01); cut1 = j - int(cap / 2 / 0.01)
                pieces.append((start, cut0)); start = cut1
            i = j
        else:
            i += 1
    pieces.append((start, n + 8))
    return np.concatenate([a[s * hop: e * hop] for s, e in pieces])


def fade(a, fin=0.0, fout=0.0):
    a = a.copy()
    if fin: k = min(len(a), int(SR * fin)); a[:k] *= np.linspace(0, 1, k)[:, None]
    if fout: k = min(len(a), int(SR * fout)); a[-k:] *= np.linspace(1, 0, k)[:, None]
    return a


def place(bus, a, t):
    i = int(round(t * SR))
    if i < 0: a, i = a[-i:], 0
    bus[i: i + len(a)] += a[: max(0, len(bus) - i)]


def lufs(path_or_a):
    """Integrated loudness via ffmpeg ebur128."""
    if not isinstance(path_or_a, (str, Path)):
        tmp = W / "_l.wav"; write_wav(tmp, path_or_a); path_or_a = tmp
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path_or_a), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    return float(r.rsplit("I:", 1)[1].split("LUFS")[0])


def gain_to(a, target):
    return a * 10 ** ((target - lufs(a)) / 20)


# --- the edit ---------------------------------------------------------------
# bite: ("bite", chunk, [(wav_in, wav_out), ...]); vo: ("vo", n, chunk, picture wav_in)
EDIT = [
    ("bite", "nt", [(28743.30, 28745.45)]),                              # film: "I know how to install Windows NT."
    ("vo", 1, "murray", 27070.5),                                        # Murray at his desk
    ("bite", "murray", [(27066.00, 27069.90)]),                          # "...quite unusual for my channel."
    ("vo", 2, "film", 28189.5),                                          # the film's talk-show opening
    ("vo", 3, "fb", 27964.0),                                            # Facebook: stuck in traffic
    ("bite", "traffic", [(27998.65, 28003.10)]),                         # "stuck in this traffic jam for... over an hour"
    ("vo", 4, "present", 28104.5),                                       # Chud's notes on the film
    ("bite", "present", [(28125.30, 28126.40), (28127.85, 28130.20)]),   # "I present to you the / tech support guy,"
    ("bite", "viper", [(31408.15, 31413.20), (31421.33, 31424.75)]),     # film + Chud: "no one looked ... the past month"
    ("bite", "end", [(33415.05, 33418.05), (33423.82, 33426.05)]),       # "his channel is restoring old tech ... worst possible audience."
]
VO_TAIL = 0.20   # picture held after a read ends
VO_LEAD = 0.15   # natural: the read starts this much before its shot (L-cut)
J_LEAD = 0.25    # natural: a bite's sound starts this much before its picture (J-cut)
XF = 0.04        # natural: crossfade at every audio join

reads = {n: tighten(read_audio(D / "vo4" / f"n{n}.mp3")) for n in (1, 2, 3, 4)}

shots = []   # (chunk, src_in, dur, kind, extra)
t = 0.0
for item in EDIT:
    if item[0] == "bite":
        _, ch, parts = item
        for k, (a, b) in enumerate(parts):
            shots.append(dict(kind="bite", chunk=ch, wav_in=a, dur=b - a, start=t, first=k == 0, last=k == len(parts) - 1))
            t += b - a
    else:
        _, n, ch, pin = item
        d = len(reads[n]) / SR + VO_TAIL
        shots.append(dict(kind="vo", n=n, chunk=ch, wav_in=pin, dur=d, start=t))
        t += d
TOTAL = t
print(f"total {TOTAL:.2f} s, {len(shots)} shots")

# --- picture (shared) -----------------------------------------------------------
parts_txt = []
for i, s in enumerate(shots):
    p = W / f"shot{i:02d}.mp4"
    # Chud's cam is off while Murray's video plays (he stepped away): Murray's player, full height.
    crops = VIDEO_CROPS if s["chunk"] == "murray" else CROPS
    g = []; lab = shorts.stack(g, "0:v", crops, 1080, "c")
    # Output-side seek: Kick's open GOPs drop frames after an input seek (decode from the chunk start).
    t0 = chunk_t(s['chunk'], s['wav_in'])
    pre = max(0.0, t0 - 12.0)   # end.ts is long: a coarse input seek to 12 s before, then exact
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{pre:.3f}", "-i", str(D / 'src' / f"{s['chunk']}.ts"), "-ss", f"{t0 - pre:.3f}",
         "-t", f"{s['dur']:.3f}", "-filter_complex", ";".join(g) + f";[{lab}]fps=30,format=yuv420p[v]", "-map", "[v]",
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", str(p)])
    parts_txt.append(f"file '{p.as_posix()}'")
(W / "shots.txt").write_text("\n".join(parts_txt))
run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(W / "shots.txt"), "-c", "copy", str(W / "picture.mp4")])

# --- audio ----------------------------------------------------------------------
N = int((TOTAL + 1) * SR)


def src(chunk, wav_t, dur, af=None):
    return read_audio(D / "src" / f"{chunk}.ts", chunk_t(chunk, wav_t), dur, af)


# room tone: the quietest 1.5 s of Chud's room chunk, looped with crossfades
room_raw = read_audio(D / "src" / "room.ts")
hop = SR // 10
r = np.sqrt(np.mean(room_raw[: len(room_raw) // hop * hop].reshape(-1, hop, 2) ** 2, axis=(1, 2)))
win = 15
k = int(np.argmin(np.convolve(r, np.ones(win), "valid")))
tone = room_raw[k * hop:(k + win) * hop]
print(f"room tone at {k / 10:.1f} s of room.ts, {20 * np.log10(np.sqrt(np.mean(tone ** 2)) + 1e-9):.1f} dBFS")


def tone_for(d):
    out = np.zeros((int(d * SR), 2), np.float32); x = int(0.3 * SR); pos = 0
    while pos < len(out):
        seg = fade(tone, 0.3, 0.3); place(out, seg, pos / SR); pos += len(tone) - x
    return out


# FLAT: hard cuts, bites at their own level, reads on silence, nothing under them.
flat = np.zeros((N, 2), np.float32)
for s in shots:
    if s["kind"] == "bite":
        place(flat, src(s["chunk"], s["wav_in"], s["dur"]), s["start"])
    else:
        place(flat, reads[s["n"]], s["start"])

# NATURAL: matched levels, room for the voice, ducked beds, J/L cuts, crossfades.
VO_AF = ("highpass=f=90,lowpass=f=13000,equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1.2:g=2,"
         "acompressor=threshold=-22dB:ratio=2.5:attack=8:release=120:makeup=1.5,"
         "aecho=in_gain=1:out_gain=0.92:delays=13|27:decays=0.16|0.09")
bites = np.zeros((N, 2), np.float32); bed = np.zeros((N, 2), np.float32)
vo = np.zeros((N, 2), np.float32); room = np.zeros((N, 2), np.float32)
TARGET = -18.0
bite_gain = {}
for s in shots:
    if s["kind"] == "bite":
        key = (s["chunk"], round(s["start"] - (0 if s["first"] else 0), 1))
        lead = J_LEAD if s["first"] else XF
        tail = XF if not s["last"] else 0.12
        a = src(s["chunk"], s["wav_in"] - lead, s["dur"] + lead + tail)
        g = bite_gain.setdefault(s["chunk"], 10 ** ((TARGET - lufs(src(s["chunk"], min(p["wav_in"] for p in shots if p["chunk"] == s["chunk"] and p["kind"] == "bite"), 6))) / 20))
        a = fade(a * g, lead if s["first"] else XF, tail)
        place(bites, a, s["start"] - lead)
    else:
        tmp = W / f"vo{s['n']}.wav"; write_wav(tmp, reads[s["n"]])
        v = read_audio(tmp, af=VO_AF)
        v = gain_to(v, TARGET + 1.0)                 # the voice sits a touch above the bites
        place(vo, v, s["start"] - VO_LEAD)
        b = src(s["chunk"], s["wav_in"] - 0.2, s["dur"] + 0.45)
        b = fade(gain_to(b, TARGET) * 10 ** (-3 / 20), 0.2, 0.25)
        place(bed, b, s["start"] - 0.2)

write_wav(W / "bites.wav", bites); write_wav(W / "bed.wav", bed); write_wav(W / "vo.wav", vo); write_wav(W / "room.wav", room)
write_wav(W / "flat_raw.wav", flat)
run(["ffmpeg", "-v", "error", "-y", "-i", str(W / "bed.wav"), "-i", str(W / "vo.wav"), "-i", str(W / "bites.wav"),
     "-filter_complex",
     "[1:a]asplit=2[vo][key];"
     "[0:a][key]sidechaincompress=threshold=0.012:ratio=10:attack=15:release=380:makeup=1[duck];"
     "[duck][vo][2:a]amix=inputs=3:normalize=0[m]", "-map", "[m]", str(W / "natural_raw.wav")])

for name in ("flat", "natural"):
    run(["ffmpeg", "-v", "error", "-y", "-i", str(W / "picture.mp4"), "-i", str(W / f"{name}_raw.wav"),
         "-filter_complex", "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]", "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart",
         str(OUT / f"8bitguy_{name}.mp4")])
    print(OUT / f"8bitguy_{name}.mp4")

# the two voices side by side: v3 then v4, the same four lines
for v in ("vo", "vo4"):
    a = np.concatenate([np.concatenate([read_audio(D / v / f"n{n}.mp3"), np.zeros((int(SR * .6), 2), np.float32)]) for n in (1, 2, 3, 4)])
    write_wav(W / f"{v}_all.wav", a)
run(["ffmpeg", "-v", "error", "-y", "-i", str(W / "vo_all.wav"), "-f", "lavfi", "-t", "1.5", "-i", f"anullsrc=r={SR}:cl=stereo",
     "-i", str(W / "vo4_all.wav"), "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]", "-map", "[a]",
     "-b:a", "192k", str(OUT / "mother_moo_v3_then_v4.mp3")])
print(OUT / "mother_moo_v3_then_v4.mp3")
