"""`clipper narrate`: the user's performance in a character's voice (issue #57).

Usage:
    clipper narrate speak <script.txt> [--voice-id ID] [-o OUT]
    clipper narrate voice <take> [--voice-id ID] [-o OUT]
    clipper narrate voices [--search TEXT] [--accent british] [--gender female] [--age old]

`voice` sends a take's audio (a video or an audio file) through the ElevenLabs Voice Changer
(speech-to-speech): the words, timing and delivery stay the performer's, the voice becomes
[narrate] voice_id. A video comes back as <take>.voiced.mp4 with the picture untouched, so
lip sync made from the original take (e.g. by the Mother Moo video models, #58) still lines
up; an audio file comes back as <take>.voiced.wav (the [narrate] output_format's type). The performer's accent carries through:
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

import io
import os
import subprocess
import tempfile
import wave
from pathlib import Path

import requests

from . import media, wiki
from .errors import ClipperError

API = "https://api.elevenlabs.io"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
FALLBACK_FORMAT = "mp3_44100_128"   # every plan has it; used when the plan refuses [narrate] output_format


def ext(fmt: str) -> str:
    """File extension for an ElevenLabs output_format: raw pcm_<rate> is saved as .wav."""
    return "wav" if fmt.startswith("pcm_") else fmt.split("_")[0]


def wav_from_pcm(data: bytes, fmt: str) -> bytes:
    """ElevenLabs pcm_<rate> is headerless 16-bit little-endian mono; wrap it as a WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(fmt.split("_")[1]))
        w.writeframes(data[: len(data) // 2 * 2])
    return buf.getvalue()


def refused_format(status: int, body: str) -> bool:
    """The account's plan doesn't allow the requested output_format."""
    return status == 403 and "output_format_not_allowed" in body


def post_audio(url: str, nc: dict, what: str, **kw) -> tuple[bytes, str]:
    """POST for audio in [narrate] output_format; if the plan refuses that format, once more in
    FALLBACK_FORMAT. Returns (audio, the format it came in)."""
    fmt = nc["output_format"]
    r = requests.post(url, params={"output_format": fmt}, headers={"xi-api-key": key(nc)}, **kw)
    if not r.ok and fmt != FALLBACK_FORMAT and refused_format(r.status_code, r.text):
        print(f"ElevenLabs: this plan doesn't allow output_format {fmt}; using {FALLBACK_FORMAT} "
              f"(set [narrate] output_format to skip the retry)")
        fmt = FALLBACK_FORMAT
        for f in (kw.get("files") or {}).values():
            f[1].seek(0)
        r = requests.post(url, params={"output_format": fmt}, headers={"xi-api-key": key(nc)}, **kw)
    if not r.ok:
        raise ClipperError(f"ElevenLabs {what}: HTTP {r.status_code}: {r.text[:300]}")
    return r.content, fmt


def save(data: bytes, fmt: str, out: Path) -> Path:
    """Write audio that came in `fmt` to `out`: PCM wrapped as WAV, converted by ffmpeg when the
    format doesn't match out's extension (after a fallback, or an -o of another type)."""
    if fmt.startswith("pcm_"):
        data = wav_from_pcm(data, fmt)
    if out.suffix.lstrip(".").lower() == ext(fmt):
        out.write_bytes(data)
        return out
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / f"audio.{ext(fmt)}"
        tmp.write_bytes(data)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), str(out)], check=True)
    return out


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
        raise ClipperError(f"no ElevenLabs API key: set ${nc['key_env']} or put it in {path} "
                         f"({wiki.page('Character-pieces', 'narration-voice-elevenlabs')})")
    return k


def convert(audio: Path, nc: dict, voice_id: str) -> tuple[bytes, str]:
    """One speech-to-speech call; returns (audio, its format), normally [narrate] output_format."""
    with open(audio, "rb") as f:
        return post_audio(f"{API}/v1/voice-changer/{voice_id}", nc, "voice changer",
                          data={"model_id": nc["model_id"],
                                "remove_background_noise": str(nc["remove_background_noise"]).lower()},
                          files={"audio": (audio.name, f, "audio/wav")}, timeout=600)


def voice(take: Path, nc: dict, voice_id: str = "", out: Path | None = None) -> Path:
    voice_id = voice_id or nc["voice_id"]
    if not voice_id:
        raise ClipperError("no voice: set [narrate] voice_id, or pass --voice-id (clipper narrate voices)")
    dur = media.duration(take)
    if dur > nc["max_s"]:
        raise ClipperError(f"{take.name} is {dur:.0f} s; split takes longer than [narrate] max_s "
                         f"({nc['max_s']} s) at pauses first")
    is_video = take.suffix.lower() in VIDEO_EXT
    out = out or take.with_name(f"{take.stem}.voiced{'.mp4' if is_video else '.' + ext(nc['output_format'])}")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "take.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(take), "-vn", "-ac", "1", "-ar", "44100",
                        str(src)], check=True)
        data, fmt = convert(src, nc, voice_id)
        voiced = save(data, fmt, Path(td) / f"voiced.{ext(fmt)}")
        if is_video:
            # Picture copied untouched; the new voice replaces the take's audio, same start.
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(take), "-i", str(voiced),
                            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                            "-shortest", "-movflags", "+faststart", str(out)], check=True)
        else:
            save(data, fmt, out)
    print(f"{out}  ({dur:.1f} s, voice {voice_id})")
    return out


def speak(text: str, nc: dict, out: Path, voice_id: str = "", overrides: dict | None = None) -> Path:
    """Text to speech in the character's voice: the read to perform to (lip-sync on headphones).
    eleven_v3 takes inline delivery tags such as [sighs], [whispers], [pause].
    [narrate] tts_settings (and `overrides`) go in as voice_settings: stability (eleven_v3: 0
    creative, 0.5 natural, 1 robust/steadiest), style (exaggeration), speed (0.7-1.2)."""
    voice_id = voice_id or nc["voice_id"]
    if not voice_id:
        raise ClipperError("no voice: set [narrate] voice_id, or pass --voice-id")
    if len(text) > nc["tts_max_chars"]:
        raise ClipperError(f"script is {len(text)} characters; split it (max {nc['tts_max_chars']} per read)")
    body = {"text": text, "model_id": nc["tts_model"]}
    if settings_ := {**nc["tts_settings"], **(overrides or {})}:
        body["voice_settings"] = settings_
    save(*post_audio(f"{API}/v1/text-to-speech/{voice_id}", nc, "text to speech", json=body, timeout=600), out)
    print(f"{out}  ({media.duration(out):.1f} s, voice {voice_id}, {len(text)} characters, "
          f"settings {body.get('voice_settings', 'voice default')})")
    return out


def read(nc: dict, text: str, folder: Path) -> Path | None:
    """One line read once per text, voice and settings, kept in folder (receipts' and stories'
    voice-overs); None for an empty line."""
    import hashlib
    import json

    if not text.strip():
        return None
    sig = json.dumps([text, nc["voice_id"], nc["tts_model"], nc["tts_settings"]], sort_keys=True)
    name = hashlib.sha1(sig.encode()).hexdigest()[:12]
    # a read voiced before the format changed is still that read: reuse it rather than pay again
    old = sorted(folder.glob(f"{name}.*"))
    out = old[0] if old else folder / f"{name}.{ext(nc['output_format'])}"
    if not out.exists():
        folder.mkdir(parents=True, exist_ok=True)
        speak(text, nc, out)
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
        raise ClipperError(f"ElevenLabs add voice: HTTP {r.status_code}: {r.text[:300]}")
    new_id = r.json()["voice_id"]
    print(f"added {name!r}: voice_id {new_id} (set [narrate] voice_id = \"{new_id}\")")
    return new_id
