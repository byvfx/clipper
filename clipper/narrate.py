"""`clipper narrate`: the user's performance in a character's voice (issue #57).

Usage:
    clipper narrate speak <script.txt> [--voice-id ID] [-o OUT]
    clipper narrate voice <take> [--voice-id ID] [-o OUT]
    clipper narrate voices [--search TEXT] [--accent british] [--gender female] [--age old]

`voice` sends a take's audio (a video or an audio file) through the ElevenLabs Voice Changer
(speech-to-speech): the words, timing and delivery stay the performer's, the voice becomes
[narrate] voice_id. A video comes back as <take>.voiced.mp4 with the picture untouched, so
lip sync made from the original take (e.g. by the Mother Moo video models, #58) still lines
up; an audio file comes back as <take>.voiced.mp3. The performer's accent carries through:
for a British character, perform it British.

`speak` reads a script in the character's voice (text to speech, [narrate] tts_model, with
inline delivery tags like [sighs]). The performer then lip-syncs to it on camera ("perform to
playback"), so the face is theirs, the voice and accent are the read's, and the mouth matches
because the take mimes that exact audio (user's preferred route, 2026-10-23).

`voices` lists the account's own voices, then ElevenLabs' shared library filtered by the
options, to pick a voice_id. The key is the ELEVENLABS_API_KEY user environment variable
([narrate] key_env); never put it in clipper.toml.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import requests

API = "https://api.elevenlabs.io"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}


def settings(cfg: dict) -> dict:
    """[narrate] plus the key file's path, for key()."""
    return dict(cfg["narrate"], key_file=cfg["paths"]["elevenlabs_key"])


def find_key(nc: dict) -> str:
    """$ELEVENLABS_API_KEY, else the paths.elevenlabs_key file; "" if neither."""
    k = os.environ.get(nc["key_env"], "").strip()
    path: Path = nc["key_file"]
    if not k and path.exists():
        k = path.read_text(encoding="utf-8-sig").strip()
    return k


def key(nc: dict) -> str:
    k = find_key(nc)
    if not k:
        path = nc["key_file"]
        raise SystemExit(f"no ElevenLabs API key: set ${nc['key_env']} or put it in {path} "
                         "(SETUP.md, Narration)")
    return k


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip() or 0)


def convert(audio: Path, nc: dict, voice_id: str) -> bytes:
    """One speech-to-speech call; returns the encoded audio in nc['output_format']."""
    with open(audio, "rb") as f:
        r = requests.post(f"{API}/v1/voice-changer/{voice_id}",
                          params={"output_format": nc["output_format"]},
                          headers={"xi-api-key": key(nc)},
                          data={"model_id": nc["model_id"],
                                "remove_background_noise": str(nc["remove_background_noise"]).lower()},
                          files={"audio": (audio.name, f, "audio/wav")}, timeout=600)
    if not r.ok:
        raise SystemExit(f"ElevenLabs voice changer: HTTP {r.status_code}: {r.text[:300]}")
    return r.content


def voice(take: Path, nc: dict, voice_id: str = "", out: Path | None = None) -> Path:
    voice_id = voice_id or nc["voice_id"]
    if not voice_id:
        raise SystemExit("no voice: set [narrate] voice_id, or pass --voice-id (clipper narrate voices)")
    dur = duration(take)
    if dur > nc["max_s"]:
        raise SystemExit(f"{take.name} is {dur:.0f} s; split takes longer than [narrate] max_s "
                         f"({nc['max_s']} s) at pauses first")
    is_video = take.suffix.lower() in VIDEO_EXT
    ext = nc["output_format"].split("_")[0]
    out = out or take.with_name(f"{take.stem}.voiced{'.mp4' if is_video else '.' + ext}")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "take.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(take), "-vn", "-ac", "1", "-ar", "44100",
                        str(src)], check=True)
        voiced = Path(td) / f"voiced.{ext}"
        voiced.write_bytes(convert(src, nc, voice_id))
        if is_video:
            # Picture copied untouched; the new voice replaces the take's audio, same start.
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(take), "-i", str(voiced),
                            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                            "-shortest", "-movflags", "+faststart", str(out)], check=True)
        else:
            out.write_bytes(voiced.read_bytes())
    print(f"{out}  ({dur:.1f} s, voice {voice_id})")
    return out


def speak(text: str, nc: dict, out: Path, voice_id: str = "") -> Path:
    """Text to speech in the character's voice: the read to perform to (lip-sync on headphones).
    eleven_v3 takes inline delivery tags such as [sighs], [whispers], [pause]."""
    voice_id = voice_id or nc["voice_id"]
    if not voice_id:
        raise SystemExit("no voice: set [narrate] voice_id, or pass --voice-id")
    if len(text) > nc["tts_max_chars"]:
        raise SystemExit(f"script is {len(text)} characters; split it (max {nc['tts_max_chars']} per read)")
    r = requests.post(f"{API}/v1/text-to-speech/{voice_id}", params={"output_format": nc["output_format"]},
                      headers={"xi-api-key": key(nc)}, json={"text": text, "model_id": nc["tts_model"]},
                      timeout=600)
    if not r.ok:
        raise SystemExit(f"ElevenLabs text to speech: HTTP {r.status_code}: {r.text[:300]}")
    out.write_bytes(r.content)
    print(f"{out}  ({duration(out):.1f} s, voice {voice_id}, {len(text)} characters)")
    return out


def voices(nc: dict, search: str = "", accent: str = "", gender: str = "", age: str = "") -> None:
    h = {"xi-api-key": key(nc)}
    mine = requests.get(f"{API}/v1/voices", headers=h, timeout=30)
    mine.raise_for_status()
    print("your voices:")
    for v in mine.json().get("voices", []):
        labels = v.get("labels") or {}
        print(f"  {v['voice_id']}  {v['name']:<24} {labels.get('accent', '')} {labels.get('gender', '')} "
              f"{labels.get('age', '')} {labels.get('description', '')}".rstrip())
    params = {k: v for k, v in {"search": search, "accent": accent, "gender": gender, "age": age,
                                "page_size": 30}.items() if v}
    lib = requests.get(f"{API}/v1/shared-voices", headers=h, params=params, timeout=30)
    lib.raise_for_status()
    print("library (add one before using it: clipper narrate voices --add OWNER:VOICE_ID):")
    for v in lib.json().get("voices", []):
        print(f"  {v['public_owner_id']}:{v['voice_id']}  {v['name'][:24]:<24} {v.get('accent', '')} "
              f"{v.get('gender', '')} {v.get('age', '')}  {(v.get('description') or '')[:70]}")


def add_voice(nc: dict, ref: str, name: str) -> str:
    """Copy a shared-library voice (OWNER:VOICE_ID) into the account so the voice changer can use it."""
    owner, vid = ref.split(":", 1)
    r = requests.post(f"{API}/v1/voices/add/{owner}/{vid}", headers={"xi-api-key": key(nc)},
                      json={"new_name": name}, timeout=30)
    if not r.ok:
        raise SystemExit(f"ElevenLabs add voice: HTTP {r.status_code}: {r.text[:300]}")
    new_id = r.json()["voice_id"]
    print(f"added {name!r}: voice_id {new_id} (set [narrate] voice_id = \"{new_id}\")")
    return new_id
