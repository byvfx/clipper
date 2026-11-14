"""ElevenLabs output formats: lossless PCM saved as WAV, and the fallback for plans without it."""
from __future__ import annotations

import io
import wave

from clipper import narrate, receipts


class Resp:
    def __init__(self, status: int, content: bytes = b"", text: str = ""):
        self.status_code, self.content, self.text = status, content, text
        self.ok = status < 400


def nc(fmt: str = "pcm_48000") -> dict:
    return {"output_format": fmt, "key_env": "ELEVENLABS_API_KEY", "key_file": None}


def test_ext():
    assert narrate.ext("pcm_48000") == "wav"
    assert narrate.ext("mp3_44100_192") == "mp3"
    assert narrate.ext("wav_44100") == "wav"


def test_default_is_lossless_48k(defaults):
    assert defaults["narrate"]["output_format"] == "pcm_48000"


def test_wav_from_pcm():
    pcm = (b"\x01\x00\xff\x7f" * 4800) + b"\x00"   # 9600 samples and a stray byte
    with wave.open(io.BytesIO(narrate.wav_from_pcm(pcm, "pcm_48000"))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 48000, 9600)
        assert w.readframes(2) == b"\x01\x00\xff\x7f"


def test_refused_format():
    body = '{"detail":{"code":"subscription_required","status":"output_format_not_allowed"}}'
    assert narrate.refused_format(403, body)
    assert not narrate.refused_format(401, body)
    assert not narrate.refused_format(403, '{"detail":{"status":"quota_exceeded"}}')


def test_post_audio_falls_back_once(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    asked = []

    def post(url, params, headers, **kw):
        asked.append(params["output_format"])
        if params["output_format"] == "pcm_48000":
            return Resp(403, text='{"detail":{"status":"output_format_not_allowed"}}')
        return Resp(200, b"mp3 bytes")

    monkeypatch.setattr(narrate.requests, "post", post)
    assert narrate.post_audio("u", nc(), "test") == (b"mp3 bytes", "mp3_44100_128")
    assert asked == ["pcm_48000", "mp3_44100_128"]


def test_post_audio_other_errors_raise(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(narrate.requests, "post", lambda *a, **k: Resp(401, text="bad key"))
    try:
        narrate.post_audio("u", nc(), "test")
    except narrate.ClipperError as e:
        assert "HTTP 401" in str(e)
    else:
        raise AssertionError("no error")


def test_save_pcm_as_wav(tmp_path):
    out = narrate.save(b"\x00\x00" * 480, "pcm_48000", tmp_path / "read.wav")
    with wave.open(str(out)) as w:
        assert w.getframerate() == 48000 and w.getnframes() == 480


def test_receipts_reuse_a_read_voiced_as_mp3(defaults, tmp_path, monkeypatch):
    sdir = tmp_path / "kick-1"
    receipts.out_dir(sdir).mkdir(parents=True)
    spoken = []
    monkeypatch.setattr(narrate, "speak", lambda text, nc, out: spoken.append(out) or out.write_bytes(b"x"))
    first = receipts.voiced(defaults, sdir, "Then he vanished.")
    assert first.suffix == ".wav" and len(spoken) == 1
    mp3 = first.with_suffix(".mp3")
    first.rename(mp3)                    # as if voiced before the default became pcm_48000
    assert receipts.voiced(defaults, sdir, "Then he vanished.") == mp3
    assert len(spoken) == 1
