The Udder Truth's host is performed to playback: a script read in her voice, the performer
lip-syncing to it on camera, a video model turning the take into her.

```bash
clipper narrate speak script.txt                       # the read, in [narrate] voice_id
# film yourself lip-syncing while the read plays out loud (one or more takes)
clipper perform prepare take.mov --read script.read.wav --take 1   # cut at the read's pauses
# render each pieceN.mp4 with the character model -> renderN.mp4 (Runway, WAN 3 Prime)
clipper perform stitch take.perform                     # final.mp4 + final_phone.mp4
```

`clipper narrate voice <take>` re-voices a take in her voice when the performer's own delivery
is wanted instead. Setup is below.

## Narration voice (ElevenLabs)

`clipper narrate voice <take>` re-voices a performance (a phone take, a webcam clip, an audio
file) with the ElevenLabs Voice Changer: the words, timing and delivery stay the performer's,
the voice becomes the character's. A video comes back as `<take>.voiced.mp4` with the picture
untouched. The performer's accent carries through.

1. Make an ElevenLabs account (a paid tier for commercial use). Reads and voiced takes come
   back as lossless 48 kHz WAV (`[narrate] output_format = "pcm_48000"`, Creator plan and up);
   a plan without it gets a 128 kbps MP3 instead, with a note. Profile → API keys → create a key (it needs the Voice Changer and Voices permissions).
   Paste it, alone on one line, into `secrets/elevenlabs_api_key.txt` (gitignored), or set the
   Windows *user* environment variable `ELEVENLABS_API_KEY`, which wins if both are set.
2. Pick the voice: `clipper narrate voices --accent british --gender female --age old` lists your
   voices and matching library voices. Add a library voice with
   `clipper narrate voices --add OWNER:VOICE_ID --name "Mother Moo"`.
3. Put the printed id in clipper.toml:
   ```toml
   [narrate]
   voice_id = "..."
   ```
4. `clipper doctor` checks the key, the plan and the voice.

Takes longer than `[narrate] max_s` (300 s) are split at pauses first.

`clipper narrate speak script.txt` reads a script in the same voice (text to speech,
`[narrate] tts_model` = eleven_v3; inline cues like `[sighs]`, `[pause]`, `[whispers]` steer the
delivery) and writes `script.read.wav`. The performer can then lip-sync to it on camera, so the
face and timing are theirs and the voice and accent are the read's.
