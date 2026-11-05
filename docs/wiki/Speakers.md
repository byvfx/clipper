Speaker labels (diarization, #52): `[transcribe] diarize = true` adds pyannote's speaker
turns to the same Modal call (or the local WhisperX run), so `words.jsonl` says who is talking.
The model is gated on Hugging Face:

1. On huggingface.co, sign in and accept the terms on
   `pyannote/speaker-diarization-community-1`. Then Settings → Access Tokens → a **Read** token.
2. Store it as a Modal secret (never in clipper.toml):
   `.venv/Scripts/python.exe -m modal secret create huggingface HF_TOKEN=hf_...`.
   For `backend = "whisperx"` set `HF_TOKEN` as a user environment variable instead.
3. `clipper transcribe --deploy`: the image now bakes the diarization model with that token, so
   **the deploy fails until the secret exists**.
4. Turn it on per show, e.g. `[shows.kino.transcribe] diarize = true` (or one stream's
   `streams/<id>/config.toml`). `clipper doctor --role gpu` checks the secret.
5. Name the regulars once: `clipper speakers <stream>` lists SPEAKER_00, SPEAKER_01... with
   their talk time and longest lines; `clipper speakers <stream> --name SPEAKER_01=Andy --name
   SPEAKER_00=PPP --enroll` names them and keeps their voices in `[paths] voices`
   (`voices/<show>.json`). Later streams of that show are named by voice (`[speakers] match`).
   A stream transcribed before: `clipper speakers <stream> --diarize` (diarization alone).
   Enroll only someone's normal voice: name an impression or a music bed by hand on that
   stream, without `--enroll` (pass the `--name` flags you want enrolled in a separate call).
6. Nothing else to set: once a stream has a named speaker, score, segments and meta read
   speaker tags in the transcript (the v5 scoring and v3 segments/meta prompts explain them;
   a `clipper.toml` that pins older `prompt_version`s gets tags the prompt doesn't expect, so
   keep the pins current). Streams without names give the same text as before.
7. Shorts with more than `[scoring] max_source_audio_s` of other voices arrive held in review
   ([Review](Review#shorts-held-for-other-voices)); a show without speaker names isn't checked.
