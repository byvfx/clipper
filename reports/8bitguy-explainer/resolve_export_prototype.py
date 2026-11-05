# Prototype: the 8-Bit Guy explainer as a Resolve project: vertical shots with handles, Moo's reads and
# the ducked beds as media, and a Resolve-shaped OTIO timeline that places them like the natural mix.
# usage: python resolve_export_prototype.py <workdir> <outdir> [VERSION]
import copy, json, subprocess, sys, tomllib, wave
from pathlib import Path
import numpy as np

sys.path.insert(0, "C:/clipper")
from clipper import shorts

D = Path(sys.argv[1]); OUTD = Path(sys.argv[2]); MED = OUTD / "media"; MED.mkdir(parents=True, exist_ok=True)
W = D / "work"; W.mkdir(exist_ok=True)
OFF = {k: v["wav_t"] for k, v in json.load(open(D / "offsets.json")).items()}
LAY = tomllib.load(open("C:/clipper/clipper.toml", "rb"))["shows"]["chud"]["shorts"]["layouts"]["watch"]
CROPS = shorts.panel_crops(LAY, 1920, 1080, 1080, 1920)
VIDEO_CROPS = shorts.panel_crops({"panels": [{"src": [0.1563, 0.1259, 0.7167, 0.7407], "h": 1920, "focus": [0.42, 0.5]}]},
                                 1920, 1080, 1080, 1920)
SR, FPS, HANDLE = 48000, 30, 1.0
TL_START = 108000                       # 01:00:00:00 at 30 fps, Resolve's default
LINES = json.load(open(D / "vo4" / "lines.json"))
REF = json.load(open(Path(__file__).parent / "resolve" / "resolve_reference.otio"))  # a timeline Resolve exported itself


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
    """Shorten silences in a read to at most `cap` s; trim its ends to 40 ms (as build.py)."""
    hop = int(SR * 0.01)
    rms = np.sqrt(np.mean(a[: len(a) // hop * hop].reshape(-1, hop, 2) ** 2, axis=(1, 2)) + 1e-12)
    quiet = 20 * np.log10(rms) < floor_db
    loud = np.where(~quiet)[0]
    a = a[max(0, loud[0] * hop - int(SR * .04)): (loud[-1] + 1) * hop + int(SR * .04)]
    quiet = quiet[loud[0]: loud[-1] + 1]
    i, n, pieces, start = 0, len(quiet), [], 0
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
    if not isinstance(path_or_a, (str, Path)):
        tmp = W / "_l.wav"; write_wav(tmp, path_or_a); path_or_a = tmp
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path_or_a), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    return float(r.rsplit("I:", 1)[1].split("LUFS")[0])


def db(x):
    return 10 ** (x / 20)


def fr(t):
    return int(round(t * FPS))


# --- the edit (as build.py) ------------------------------------------------------
EDIT = [
    ("bite", "nt", [(28743.30, 28745.45)], "Film: \"I know how to install Windows NT.\""),
    ("vo", 1, "murray", 27070.5, "Murray's announcement video"),
    ("bite", "murray", [(27066.00, 27069.90)], "Murray: \"...quite unusual for my channel.\""),
    ("vo", 2, "film", 28189.5, "The film's opening"),
    ("vo", 3, "fb", 27964.0, "Murray's premiere post"),
    ("bite", "traffic", [(27998.65, 28003.10)], "Murray: \"stuck in this traffic jam for... over an hour\""),
    ("vo", 4, "present", 28104.5, "Chud's notes on the film"),
    ("bite", "present", [(28125.30, 28126.40), (28127.85, 28130.20)], "Chud: \"I present to you / the tech support guy, the full movie.\""),
    ("bite", "viper", [(31408.15, 31413.20), (31421.33, 31424.75)], "Film: Viper \"right under our noses\" / Chud: \"no one looked...\""),
    ("bite", "end", [(33415.05, 33418.05), (33423.82, 33426.05)], "Chud: \"his channel is restoring old tech / ...worst possible audience.\""),
]
VO_TAIL, VO_LEAD, J_LEAD, BITE_TAIL = 0.20, 0.15, 0.25, 0.12
TARGET = -18.0
VERSION = sys.argv[3] if len(sys.argv) > 3 else "v1"
# reads whose shot holds its first frame through the read's first sentence, then plays (user, 11-04:
# "hold the first frame of the 8bit guy and then play it")
HOLD_FIRST_SENTENCE = {1} if VERSION == "v2" else set()
# reads whose picture is the next bite's first frame, held for the whole read; the bite then plays with no
# J-cut and no bed under the read (user, 11-04: "use the 03 bite murray first frame while moo is talking,
# then once moo stops 8bit can [talk]")
FREEZE_ON_NEXT = {1} if VERSION in ("v3", "v4", "v5", "v6") else set()
# v4: Moo's reads also go in dry (tightened and levelled only) on their own track, for a Fairlight chain;
# the baked reads stay on a disabled track to A/B against (user, 11-04: "un AI mother moos voice a bit")
DRY_MOO = VERSION in ("v4", "v5", "v6")
# v5: real room tone under each dry read, as her room's noise floor (user, 11-04: "find some room noise
# online we can use"): BigSoundBank 2458 "Sound Atmosphere in a House", CC0, its steadiest stretch
ROOM_TONE = OUTD / "roomtone" / "AMBRoom_Sound atmosphere in a house (ID 2458)_BigSoundBank.com.wav" if VERSION in ("v5", "v6") else None
# v6: no opening Windows NT shot (it shows Chud's cam); the edit opens on the 8-Bit Guy freeze (user, 11-04:
# "lose the bit of footage of chud at the beginning, just start with 8 bit like we have it now")
DROP = {"nt"} if VERSION == "v6" else set()
LEAD_IN, OPEN_READ_AT = 0.4, 0.25   # a read that opens the edit: frozen picture first, the read 0.25 s in
ROOM_FROM, ROOM_DBFS = 47.0, -62.0
VO_AF = ("highpass=f=90,lowpass=f=13000,equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1.2:g=2,"
         "acompressor=threshold=-22dB:ratio=2.5:attack=8:release=120:makeup=1.5,"
         "aecho=in_gain=1:out_gain=0.92:delays=13|27:decays=0.16|0.09")

reads = {n: tighten(read_audio(D / "vo4" / f"n{n}.mp3")) for n in (1, 2, 3, 4)}


def first_pause(a, min_gap=0.15, floor_db=-40):
    """Middle of the read's first pause of at least `min_gap` s (the end of its first sentence)."""
    hop = SR // 100
    rms = np.sqrt(np.mean(a[: len(a) // hop * hop].reshape(-1, hop, 2) ** 2, axis=(1, 2)) + 1e-12)
    q = 20 * np.log10(rms) < floor_db
    i = int(np.argmax(~q))
    while i < len(q):
        if q[i]:
            j = i
            while j < len(q) and q[j]:
                j += 1
            if (j - i) / 100 >= min_gap:
                return (i + j) / 200
            i = j
        else:
            i += 1
    return 0.0

# items: one media file each (a bite with two parts is one file, cut twice on the timeline)
items, t = [], 0.0
for idx, e in enumerate(EDIT):
    if (e[1] if e[0] == "bite" else e[2]) in DROP:
        continue
    if e[0] == "bite":
        _, ch, parts, note = e
        shots = []
        for a, b in parts:
            shots.append(dict(wav_in=a, dur=b - a, start=t)); t += b - a
        items.append(dict(kind="bite", chunk=ch, shots=shots, note=note, src0=parts[0][0], src1=parts[-1][1], idx=idx))
    else:
        _, n, ch, pin, note = e
        d = len(reads[n]) / SR + VO_TAIL + (LEAD_IN if not items else 0.0)
        read_at = t + OPEN_READ_AT if not items else t - VO_LEAD
        hold = first_pause(reads[n]) - VO_LEAD if n in HOLD_FIRST_SENTENCE else 0.0
        items.append(dict(kind="vo", n=n, chunk=ch, shots=[dict(wav_in=pin, dur=d, start=t)], note=note,
                          src0=pin, src1=pin + d, hold=hold, freeze_next=n in FREEZE_ON_NEXT, idx=idx, read_at=read_at)); t += d
TOTAL = t
for a, b in zip(items, items[1:]):
    if a.get("freeze_next"):
        assert b["kind"] == "bite"
        b["no_lead"] = True

# --- levels: per-source gain to -18 LUFS, a master gain toward -14 without clipping ----------
gain = {}
for it in items:
    if it["kind"] == "bite":
        gain[it["chunk"]] = db(TARGET - lufs(read_audio(D / "src" / f"{it['chunk']}.ts", chunk_t(it["chunk"], it["src0"]), 6)))

N = int((TOTAL + 1) * SR)
vo_bus, bed_bus, bite_bus = (np.zeros((N, 2), np.float32) for _ in range(3))
moo = {}
for it in items:
    s = it["shots"][0]
    if it["kind"] == "vo":
        tmp = W / f"vo{it['n']}.wav"; write_wav(tmp, reads[it["n"]])
        v = read_audio(tmp, af=VO_AF); v = v * db(TARGET + 1.0 - lufs(v))
        moo[it["n"]] = v; place(vo_bus, v, it["read_at"])
        dry = read_audio(tmp); it["dry"] = dry * db(TARGET + 1.0 - lufs(dry))
        if it["freeze_next"]:
            continue
        h = it["hold"]   # a held first frame is silent; the bed starts when the picture starts playing
        b = read_audio(D / "src" / f"{it['chunk']}.ts", chunk_t(it["chunk"], s["wav_in"] - 0.2), s["dur"] - h + 0.45)
        b = fade(b * db(TARGET - lufs(b) - 3), 0.2, 0.25)
        place(bed_bus, b, s["start"] + h - 0.2)
    else:
        for k, sh in enumerate(it["shots"]):
            lead = J_LEAD if k == 0 and not it.get("no_lead") else 0
            a = read_audio(D / "src" / f"{it['chunk']}.ts", chunk_t(it["chunk"], sh["wav_in"] - lead), sh["dur"] + lead)
            place(bite_bus, a * gain[it["chunk"]], sh["start"] - lead)
write_wav(W / "rx_bed.wav", bed_bus); write_wav(W / "rx_vo.wav", vo_bus); write_wav(W / "rx_bites.wav", bite_bus)
run(["ffmpeg", "-v", "error", "-y", "-i", str(W / "rx_bed.wav"), "-i", str(W / "rx_vo.wav"), "-filter_complex",
     "[0:a][1:a]sidechaincompress=threshold=0.012:ratio=10:attack=15:release=380:makeup=1[d]", "-map", "[d]",
     str(W / "rx_duck.wav")])
duck = read_audio(W / "rx_duck.wav")
mix = vo_bus + bite_bus; mix[: len(duck)] += duck[: len(mix)]
peak = 20 * np.log10(np.abs(mix).max())
M = min(-14.0 - lufs(mix), -1.0 - peak)
if VERSION != "v1":
    M = -1.9   # v1's trim: v1's media files are reused as they are, so every version keeps its level
print(f"mix {lufs(mix):.1f} LUFS, peak {peak:.1f} dBFS -> master gain {M:+.1f} dB baked into every file")
MG = db(M)

# --- media --------------------------------------------------------------------------------
media = {}   # name -> (path, frames, start wav time)
for i, it in enumerate(items):
    ch = it["chunk"]
    m0 = max(OFF[ch] + 0.05, it["src0"] - HANDLE)
    m1 = it["src1"] + HANDLE
    nfr = fr(m1 - m0)
    name = f"{it['idx'] + 1:02d}_{'moo' + str(it['n']) if it['kind'] == 'vo' else 'bite'}_{ch}.mov"
    crops = VIDEO_CROPS if ch == "murray" else CROPS
    g = []; lab = shorts.stack(g, "0:v", crops, 1080, "c")
    t0 = chunk_t(ch, m0); pre = max(0.0, t0 - 12.0)
    ag = gain[ch] if it["kind"] == "bite" else db(TARGET - 3 - lufs(read_audio(D / "src" / f"{ch}.ts", t0, m1 - m0)))
    if not (MED / name).exists(): run(["ffmpeg", "-v", "error", "-y", "-ss", f"{pre:.3f}", "-i", str(D / "src" / f"{ch}.ts"), "-ss", f"{t0 - pre:.3f}",
         "-filter_complex", ";".join(g) + f";[{lab}]fps={FPS},format=yuv420p[v];[0:a]aresample={SR},volume={ag * MG:.5f}[a]",
         "-map", "[v]", "-map", "[a]", "-frames:v", str(nfr), "-t", f"{nfr / FPS:.4f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-g", "15", "-c:a", "pcm_s16le", str(MED / name)])
    it.update(media=name, m0=m0, nfr=nfr)
    media[name] = (MED / name, nfr)
    if it["kind"] == "vo":
        n = it["n"]; s = it["shots"][0]
        mname = f"moo{n}.wav"
        if not (MED / mname).exists(): write_wav(MED / mname, moo[n] * MG)
        media[mname] = (MED / mname, fr(len(moo[n]) / SR))
        if DRY_MOO:
            dname = f"moo{n}_dry.wav"
            if not (MED / dname).exists(): write_wav(MED / dname, it["dry"] * MG)
            media[dname] = (MED / dname, fr(len(it["dry"]) / SR)); it["moo_dry"] = dname
        if ROOM_TONE:
            rname = f"room{n}.wav"; L = len(it["dry"])
            if not (MED / rname).exists():
                rt = read_audio(ROOM_TONE, ROOM_FROM + 7.0 * (n - 1), L / SR + 0.1)[:L]
                rt = fade(rt * db(ROOM_DBFS - 20 * np.log10(np.sqrt(np.mean(rt ** 2)))), 0.03, 0.03)
                write_wav(MED / rname, rt)
            media[rname] = (MED / rname, fr(L / SR)); it["room"] = rname
        it.update(moo=mname, bed_rec=None)
        if it["freeze_next"]:
            continue
        # the ducked bed under this read, cut from the ducked bus on frame boundaries
        b0, b1 = fr(s["start"] + it["hold"] - 0.2), fr(s["start"] + s["dur"] + 0.25)
        bname = f"bed{n}_hold.wav" if it["hold"] else f"bed{n}.wav" if VERSION < "v6" else f"bed{n}_{VERSION}.wav"
        write_wav(MED / bname, duck[int(b0 / FPS * SR): int(b1 / FPS * SR)] * MG); media[bname] = (MED / bname, b1 - b0)
        it.update(moo=mname, bed=bname, bed_rec=(b0, b1))

# --- OTIO, in the shape Resolve exports -------------------------------------------------------
ref_tracks = REF["tracks"]["children"]
VREF = next(c for c in ref_tracks[0]["children"] if c["OTIO_SCHEMA"] == "Clip.2")
AREF = next(c for c in ref_tracks[1]["children"] if c["OTIO_SCHEMA"] == "Clip.2")


def rt(v):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(FPS), "value": float(v)}


def tr(start, dur):
    return {"OTIO_SCHEMA": "TimeRange.1", "duration": rt(dur), "start_time": rt(start)}


def clip(name, src_start, dur, audio, frozen=False):
    c = copy.deepcopy(AREF if audio else VREF)
    path, nfr = media[name]
    c.update(name=name, source_range=tr(src_start, dur), markers=[])
    c["media_references"]["DEFAULT_MEDIA"].update(name=name, available_range=tr(0, nfr), target_url=str(path).replace("/", "\\"))
    # a freeze frame's duration is its record span (OTIO); it uses one source frame
    assert 0 <= src_start and (src_start < nfr if frozen else src_start + dur <= nfr), (name, src_start, dur, nfr)
    return c


def gap(d):
    return {"OTIO_SCHEMA": "Gap.1", "metadata": {}, "name": "", "source_range": tr(0, d), "effects": [], "markers": [], "enabled": True}


def track(name, kind, placed):
    """placed: [(record_frame, clip)] in order, no overlaps."""
    meta = copy.deepcopy((ref_tracks[0] if kind == "Video" else ref_tracks[1])["metadata"])
    kids, pos = [], 0
    for rec, c in sorted(placed, key=lambda x: x[0]):
        assert rec >= pos, (name, rec, pos)
        if rec > pos: kids.append(gap(rec - pos))
        kids.append(c); pos = rec + int(c["source_range"]["duration"]["value"])
    return {"OTIO_SCHEMA": "Track.1", "metadata": meta, "name": name, "source_range": None, "effects": [], "markers": [],
            "enabled": True, "children": kids, "kind": kind}


V1, BITE = [], {0: [], 1: []}
BED = {0: [], 1: []}; MOO, MOO_DRY, ROOM, MARK = [], [], [], []
nb = nv = 0
for idx, it in enumerate(items):
    shots = it["shots"]
    for k, s in enumerate(shots):
        rec0, rec1 = fr(s["start"]), fr(s["start"] + s["dur"])
        src = fr(s["wav_in"] - it["m0"])
        hold = fr(it.get("hold", 0.0))
        if it.get("freeze_next"):   # the next bite's first frame, frozen for the whole read
            nxt = items[idx + 1]
            frz = clip(nxt["media"], fr(nxt["shots"][0]["wav_in"] - nxt["m0"]), rec1 - rec0, False, frozen=True)
            frz["effects"].append({"OTIO_SCHEMA": "FreezeFrame.1", "metadata": {}, "name": "", "effect_name": "FreezeFrame",
                                   "time_scalar": 0.0})
            V1.append((rec0, frz))
            continue
        if hold:   # the first frame held (a Resolve freeze frame), then the shot plays from that frame
            frz = clip(it["media"], src, hold, False)
            frz["effects"].append({"OTIO_SCHEMA": "FreezeFrame.1", "metadata": {}, "name": "", "effect_name": "FreezeFrame",
                                   "time_scalar": 0.0})
            V1.append((rec0, frz))
        V1.append((rec0 + hold, clip(it["media"], src, rec1 - rec0 - hold, False)))
        if it["kind"] == "bite":
            lead = min(fr(J_LEAD), rec0) if k == 0 and not it.get("no_lead") else 0
            tail = fr(BITE_TAIL) if (k == len(shots) - 1 and it is not items[-1]) else 0
            BITE[nb % 2].append((rec0 - lead, clip(it["media"], src - lead, rec1 - rec0 + lead + tail, True)))
    rec = fr(shots[0]["start"])
    if it["kind"] == "bite":
        nb += 1
        MARK.append((rec, "Bite", "YELLOW", it["note"]))
    else:
        if it["bed_rec"]:
            b0, b1 = it["bed_rec"]
            BED[nv % 2].append((b0, clip(it["bed"], 0, b1 - b0, True))); nv += 1
        MOO.append((fr(it["read_at"]), clip(it["moo"], 0, media[it["moo"]][1], True)))
        if DRY_MOO:
            MOO_DRY.append((fr(it["read_at"]), clip(it["moo_dry"], 0, media[it["moo_dry"]][1], True)))
        if ROOM_TONE:
            ROOM.append((fr(it["read_at"]), clip(it["room"], 0, media[it["room"]][1], True)))
        MARK.append((rec, f"Moo {it['n']}", "BLUE", f"{LINES[it['n'] - 1]}  [picture: {'the next bite, first frame held' if it['freeze_next'] else it['note']}]"))

tracks = [track("Video 1", "Video", V1),
          track("Stream A", "Audio", BITE[0]), track("Stream B", "Audio", BITE[1]),
          track("Bed A (ducked)", "Audio", BED[0]), track("Bed B (ducked)", "Audio", BED[1]),
          track("Mother Moo", "Audio", MOO), track("Music", "Audio", [])]
if DRY_MOO:
    baked = track("Mother Moo (baked)", "Audio", MOO); baked["enabled"] = False
    tracks[5:6] = [track("Mother Moo (dry)", "Audio", MOO_DRY), baked]
if ROOM_TONE:
    tracks.insert(6, track("Moo room tone", "Audio", ROOM))
markers = [{"OTIO_SCHEMA": "Marker.2", "metadata": {"Resolve_OTIO": {"Keywords": [], "Note": note}}, "name": name,
            "color": color, "marked_range": tr(rec, 1)} for rec, name, color, note in MARK]
otio = copy.deepcopy(REF)
otio["name"] = f"8-Bit Guy explainer {VERSION}"
otio["global_start_time"] = {"OTIO_SCHEMA": "RationalTime.1", "rate": float(FPS), "value": float(TL_START)}
otio["tracks"].update(markers=markers, children=tracks)
(OUTD / f"8bitguy_explainer_{VERSION}.otio").write_text(json.dumps(otio, indent=2))
print(f"total {TOTAL:.2f} s = {fr(TOTAL)} frames; {len(media)} media files; wrote {OUTD / f'8bitguy_explainer_{VERSION}.otio'}")
print("holds:", {it["n"]: f"{it['hold']:.2f} s = {fr(it['hold'])} frames" for it in items if it.get("hold")})
for t_ in tracks:
    print(f"  {t_['name']}: {sum(c['OTIO_SCHEMA'] == 'Clip.2' for c in t_['children'])} clips")
