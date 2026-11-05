# Fresh takes of one Moo line (eleven_v4, same text and stitching as v4.py), different seeds.
import json, re, sys, base64, requests
d, n, seeds = sys.argv[1], int(sys.argv[2]), [int(s) for s in sys.argv[3].split(",")]
lines = json.load(open(f"{d}/vo4/lines.json", encoding="utf-8"))
plain = [re.sub(r"\[[^\]]*\]\s*", "", l) for l in lines]
from clipper import config, narrate
key = narrate.key(narrate.settings(config.load()))
voice = "aAsWcN5jdLdiYG7Hq0YL"
i = n - 1
for sd in seeds:
    body = {"text": lines[i], "model_id": "eleven_v4", "seed": sd,
            "voice_settings": {"stability": 0.5, "style": 0.0, "speed": 1.05},
            "previous_text": " ".join(plain[:i]) or None, "next_text": plain[i + 1] if i + 1 < len(lines) else None}
    body = {k: v for k, v in body.items() if v is not None}
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
                      params={"output_format": "mp3_44100_128"}, headers={"xi-api-key": key}, json=body, timeout=300)
    r.raise_for_status(); j = r.json()
    open(f"{d}/vo4/takes/n{n}_s{sd}.mp3", "wb").write(base64.b64decode(j["audio_base64"]))
    json.dump(j.get("alignment"), open(f"{d}/vo4/takes/n{n}_s{sd}.align.json", "w"))
    print(f"n{n} seed {sd}: ok, {len(lines[i])} chars")
