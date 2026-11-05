"""`clipper models test <model>`: can a model do clipper's jobs? (#72)

  clipper models test openrouter:qwen/qwen3.8-flash [--effort none] [--no-long]

No stream needed: the checks bring their own material, the same for every model, so any
model on any provider can be tried before a stage is moved to it.

  pass_a   six made-up stream windows (two clean keepers, a played video, slots, an
           in-joke nobody explains, a break) through the real Pass A code with the prompt
           version in use, twice: the verdicts, the cut rules on what it keeps (Short
           15-60 s around the payoff, clip 30-300 s, hook and title length, no "hosts"),
           and whether the two rounds agree
  json     every structured answer matched its schema (a retry is noted: it costs double)
  vision   a picture with a colour and a number on it (shorts layouts, cast labels)
  text     a short description, house style applied (meta, summaries)
  long     a password said once in a made-up ~9-hour transcript (~130k tokens), found by
           what was said and when (segments reads a whole stream at once)

The report says which of the stages this config names the model could take, and is saved
to [paths] model_tests/<model>.json. --dry-run prints the estimate; a run over --budget, or
with no known price (other than local:), is refused.
"""
from __future__ import annotations

import base64
import copy
import io
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import jsonstate, llm
from .compare import slug
from .config import EFFORTS
from .errors import ClipperError
from .llm import Claude
from .prompts import Prompts

BANNED = re.compile(r"\bhosts?\b", re.IGNORECASE)  # house style; actions.py checks the same
ROUNDS = 2
WINDOW = (120, 240)
CAST = ["Andy", "Sam"]
PLAYED = "SPEAKER_02"  # an unnamed voice: "[?]" in the transcript, a video being played
LONG_TOKENS = 130_000  # the longest transcript segments reads in one call (an ~9-hour stream)
CHARS_PER_TOKEN = 4
HOOK_WORDS, TITLE_CHARS = 8, 70  # the prompt says under 8 and under 70: a note, not a failure

# Each fixture: lines (start_s, speaker, text); expect: the statuses that are right; payoff:
# the second the Short has to include (keepers); hard: keeping it breaks a hard rule.
FIXTURES = [
    {"id": "keeper_story", "what": "a self-contained story with a punchline",
     "expect": ("kept",), "payoff": 160, "peaks": [162, 170],
     "chat": ["LMAOOO you're getting better", "smoke detector jazz", "KEKW", "framed it lol"],
     "lines": [
         (40, "Andy", "Okay we're back. Did everybody get snacks? Chat says yes."),
         (60, "Sam", "I got the weird chips again, the ones that taste like a candle."),
         (80, "Andy", "Those are an acquired taste. Anyway, I have to tell you what happened this week."),
         (100, "Sam", "Go on."),
         (121, "Andy", "So my landlord texts me at two in the morning, all caps: who is playing the trumpet."),
         (129, "Sam", "Oh no."),
         (131, "Andy", ("And I'm lying there thinking, I don't own a trumpet. I have never touched a trumpet "
                       "in my life. So I text back, not me.")),
         (141, "Andy", ("He sends me a video from the hallway. It's my smoke detector. The low battery chirp. "
                       "Every forty seconds. For six hours.")),
         (152, "Sam", "He thought the chirp was a trumpet?"),
         (156, "Andy", ("He thought I was practicing. Next morning there's a note taped to my door. "
                       "It says: you're getting better.")),
         (166, "Sam", "You're getting better! That's the nicest thing anyone has ever said about your music."),
         (172, "Andy", "I framed it. It's the only review I've ever gotten."),
         (178, "Sam", "Five stars. Never played a note."),
         (184, "Andy", "Anyway I changed the battery, so my career is over."),
         (192, "Sam", "Retired at the top."),
         (198, "Andy", "Okay, let's look at some of these messages."),
     ]},
    {"id": "keeper_take", "what": "a strong opinion stated cleanly",
     "expect": ("kept",), "payoff": 150, "peaks": [152, 158],
     "chat": ["unpaid staff LOL", "so true", "unexpected item in bagging area KEKW", "W take"],
     "lines": [
         (50, "Andy", "Did you get groceries on the way here?"),
         (58, "Sam", "I did, and I need to talk about it."),
         (70, "Andy", "Uh oh."),
         (78, "Sam", "No, it's fine, it's just one thing."),
         (95, "Andy", "Okay, the floor is yours."),
         (121, "Sam", "Self checkout. Who decided this? I scan everything myself, I bag everything myself."),
         (130, "Sam", "I am doing the cashier's whole job, for free, and the store keeps the money."),
         (137, "Andy", "That's fair."),
         (139, "Sam", "And then the machine has the nerve to accuse me. Unexpected item in bagging area."),
         (147, "Sam", ("Yeah. It's me. I'm the unexpected item. I'm unpaid staff and you're treating me "
                      "like a shoplifter.")),
         (156, "Andy", "Unpaid staff! You should unionize."),
         (160, "Sam", "I want a name tag. If I'm working there I want a name tag and a discount."),
         (168, "Andy", "Employee of the month, never hired."),
         (174, "Sam", ("And a lady came over and scanned her badge to fix it and gave me a look, like I'm "
                      "the problem.")),
         (185, "Andy", "You were the unexpected item."),
         (190, "Sam", "I was the unexpected item. Okay, next topic."),
     ]},
    {"id": "played_video", "what": "the payoff is in a video being played",
     "expect": ("rejected", "no_clip"), "hard": True, "peaks": [165, 176],
     "chat": ["TURBO MODE LMAO", "HONEY", "one star", "KEKW"],
     "lines": [
         (60, "Andy", "Chat keeps sending this one video, so let's watch it."),
         (75, "Sam", "Is this the toaster guy?"),
         (85, "Andy", "It's the toaster guy. Pulling it up now."),
         (110, "Andy", "Okay, here it goes."),
         (121, PLAYED, ("Hey everybody, welcome back to the channel. Today I'm reviewing the Toast Master "
                       "Nine Thousand. Four slots, a bagel setting, and a turbo mode that the box says is "
                       "for professionals only.")),
         (136, PLAYED, ("Now, I am a professional. I've reviewed over three hundred toasters. So I'm going "
                       "to put it on turbo and we'll see how it does with a regular piece of white bread.")),
         (150, PLAYED, ("Okay, it's heating up. That's a lot of noise. That is a lot of noise for a toaster. "
                       "Is that smoke? That's smoke.")),
         (162, PLAYED, ("Oh no. Oh no, it's on fire. The toast is on fire. Where's the, where is the "
                       "extinguisher? Honey! Honey, the turbo mode!")),
         (174, PLAYED, ("And that's why you never trust a toaster that says professionals only. "
                       "Rating: one star. Please like and subscribe.")),
         (185, "Andy", "Oh my god."),
         (188, "Sam", "He did that to himself."),
         (192, "Andy", "Three hundred toasters and he still pressed turbo."),
         (198, "Sam", "Okay, what's next?"),
     ]},
    {"id": "casino", "what": "a slots bonus and a big win",
     "expect": ("rejected", "no_clip"), "hard": True, "peaks": [161, 170],
     "chat": ["4000X LETS GOOO", "W", "CASH OUT", "HUGE WIN"],
     "lines": [
         (60, "Andy", "Alright, we've got a little bit left on the balance."),
         (75, "Sam", "Don't do it."),
         (90, "Andy", "One bonus buy. Just one."),
         (105, "Sam", "You said that last time."),
         (121, "Andy", "Okay, buying the bonus. Five hundred dollar buy on Sweet Fortune. Let's go."),
         (130, "Andy", "Come on, sticky wilds, give me sticky wilds. One scatter. Two scatters."),
         (140, "Andy", "Three scatters! Free spins! Ten free spins!"),
         (147, "Sam", "Okay, okay, here we go."),
         (150, "Andy", "Multiplier, multiplier, come on, drop the multiplier. Fifty x on the wild!"),
         (160, "Andy", "Oh my god. Four thousand x. That's a four thousand x hit! Let's go!"),
         (168, "Sam", "Cash out. Cash out right now. Withdraw it."),
         (174, "Andy", "Thirty thousand dollars on one bonus. Use the code in the description, chat."),
         (184, "Sam", "You're not cashing out, are you."),
         (190, "Andy", "One more bonus."),
     ]},
    {"id": "in_joke", "what": "a running joke nobody explains",
     "expect": ("rejected", "no_clip"), "peaks": [150, 168],
     "chat": ["NUMBER FOUR LMAOOO", "gary arc", "the canoe KEKW", "justice for gary"],
     "lines": [
         (40, "Andy", "So the weather this week has been insane."),
         (55, "Sam", "It snowed on Tuesday and then it was eighty degrees on Thursday."),
         (75, "Andy", "I wore shorts and a scarf at the same time."),
         (95, "Sam", "Anyway, somebody in chat asked a question."),
         (121, "Sam", "Someone in chat just said Gary. Just the word Gary."),
         (127, "Andy", "No. We're not doing Gary again."),
         (131, "Sam", "It's always the Gary thing. Every week."),
         (135, "Andy", "Gary did the thing with the canoe. We agreed. We said we would never bring up the canoe."),
         (144, "Sam", "Not the canoe."),
         (147, "Andy", "Number four, Sam. Number four."),
         (151, "Sam", "Number four! Oh my god, number four."),
         (158, "Andy", "And then he did it again. That's the part people forget. He did number four twice."),
         (167, "Sam", "Twice! With the same canoe!"),
         (173, "Andy", "Okay, we have to move on before he hears this."),
         (180, "Sam", "He's definitely watching. Hi Gary."),
     ]},
    {"id": "break", "what": "a break: mic check and merch",
     "expect": ("no_clip", "rejected"), "peaks": [125],
     "chat": ["brb", "water break", "o7"],
     "lines": [
         (60, "Andy", "Okay that was the last video from the list."),
         (80, "Sam", "We've been going for two hours."),
         (100, "Andy", "Yeah, let's take a short break."),
         (121, "Andy", "Alright, we're going to take a quick break. Grab some water, stretch your legs."),
         (130, "Sam", "Can you check my mic? I think it's a little quiet."),
         (136, "Andy", "Say something."),
         (138, "Sam", "Testing, testing, one, two."),
         (142, "Andy", "Yeah, it's fine, maybe turn it up a little."),
         (150, "Sam", "Okay. And the merch link is in the description, if anyone wants a shirt."),
         (160, "Andy", "We'll be back in about five minutes. Don't go anywhere."),
         (168, "Sam", "Put on the break music."),
     ]},
]

# What each stage asks of its model, by config key; every stage needs "json".
NEEDS = {"scoring.model_a": ("json", "pass_a"), "segments.model": ("json", "long"),
         "shorts.layout_model": ("json", "vision"), "cast.label_model": ("json", "vision"),
         "meta.model": ("json", "text")}


def words_of(lines: list[tuple]) -> list[dict]:
    """words.jsonl-style words for fixture lines (one segment a line, times spread evenly
    up to the next line), with `who` as load_words names them."""
    out = []
    for i, (t, spk, text) in enumerate(lines):
        toks = text.split()
        end = lines[i + 1][0] - 0.5 if i + 1 < len(lines) else t + 0.4 * len(toks)
        step = (end - t) / len(toks)
        out += [{"start": round(t + k * step, 2), "end": round(t + (k + 1) * step - 0.05, 2), "word": w,
                 "seg": i, "speaker": spk, "who": "?" if spk == PLAYED else spk}
                for k, w in enumerate(toks)]
    return out


def signals_of(peaks: list[int], length: int = WINDOW[1] + 60) -> dict:
    """A quiet chat with a spike at each peak."""
    sig = {k: np.zeros(length) for k in ("chat_msgs", "loud_z", "heat", "react", "clip")}
    sig["chat_msgs"] += 0.3
    for p in peaks:
        sig["chat_msgs"][p:p + 8] += 3.0
        sig["react"][p:p + 8] += 1
        sig["loud_z"][p - 2:p + 4] = 2.5
        sig["heat"][p - 5:p + 10] = 3.0
    return sig


def fixture_cfg(cfg: dict, model: str, effort: str) -> dict:
    """The config the fixtures run under: the prompts in use, a made-up show (Andy and Sam,
    strangers watching, played audio rejected), the candidate on Pass A."""
    cfg = copy.deepcopy(cfg)
    sc = cfg["scoring"]
    sc.update(model_a=model, channel_name="a comedy podcast channel", audience="people who like "
              "comedy podcasts", tone="quick and dry", stream_context="A two-person comedy podcast. "
              "The show's own people are Andy and Sam.", known_figures="", third_party_audio="reject",
              audience_knowledge="strangers")
    if effort:
        sc["effort_a"] = effort
    cfg.setdefault("meta", {})["cast"] = CAST
    cfg.setdefault("cast", {})["known_figures"] = False
    return cfg


def windows_of() -> list[dict]:
    return [{"id": f["id"], "rank": i, "start_s": WINDOW[0], "end_s": WINDOW[1], "peaks": f["peaks"],
             "heat": 3.0, "chat_sample": f["chat"], "source": "test"} for i, f in enumerate(FIXTURES, 1)]


def cut_problems(rec: dict, fx: dict) -> tuple[list[str], list[str]]:
    """What breaks the cut rules in a kept answer: (failures, notes). A hook a word long or a
    title a little over is a note; the models stages run on today do it too."""
    out, notes = [], []
    short, clip = rec.get("short_duration_s", 0), rec.get("duration_s", 0)
    if not 14 <= short <= 61:  # a second of slack for snapping to words
        out.append(f"Short {short:.0f} s (15-60)")
    if not 29 <= clip <= 301:
        out.append(f"clip {clip:.0f} s (30-300)")
    if fx.get("payoff") and not rec.get("short_in_s", 0) <= fx["payoff"] <= rec.get("short_out_s", 0):
        out.append(f"Short {rec.get('short_in_s')}-{rec.get('short_out_s')} misses the payoff at {fx['payoff']}")
    if len(rec.get("hook", "").split()) >= HOOK_WORDS:
        notes.append(f"hook {len(rec['hook'].split())} words")
    if len(rec.get("title", "")) >= TITLE_CHARS:
        notes.append(f"title {len(rec['title'])} chars")
    if any(BANNED.search(rec.get(k, "")) for k in ("hook", "title")):
        out.append('"hosts" in the hook or title')
    if not 1 <= rec.get("score", 0) <= 10:
        out.append(f"score {rec.get('score')}")
    return out, notes


def check_pass_a(cfg: dict, claude: Claude, rounds: int = ROUNDS) -> dict:
    from .scoring import run_pass_a

    prompts = Prompts(cfg)

    def one(job: tuple[dict, dict]) -> dict:
        fx, w = job
        return run_pass_a([w], words_of(fx["lines"]), signals_of(fx["peaks"]), cfg, claude, prompts)[0]

    with ThreadPoolExecutor(len(FIXTURES)) as pool:
        flat = list(pool.map(one, list(zip(FIXTURES, windows_of(), strict=True)) * rounds))
    runs = [flat[i:i + len(FIXTURES)] for i in range(0, len(flat), len(FIXTURES))]
    rows, right, hard, problems, notes = [], 0, [], [], []
    for i, fx in enumerate(FIXTURES):
        got = [run[i] for run in runs]
        ok = [r["status"] in fx["expect"] for r in got]
        right += sum(ok)
        if fx.get("hard") and any(r["status"] == "kept" for r in got):
            hard.append(fx["id"])
        for r in got:
            if r["status"] == "kept" and "kept" in fx["expect"]:
                bad, meh = cut_problems(r, fx)
                problems += [f"{fx['id']}: {p}" for p in bad]
                notes += [f"{fx['id']}: {p}" for p in meh]
        rows.append({"id": fx["id"], "what": fx["what"], "expect": "/".join(fx["expect"]),
                     "got": [r["status"] for r in got], "right": sum(ok),
                     "flags": [[k for k in ("third_party_audio", "casino_gameplay", "needs_context") if r.get(k)]
                               for r in got],
                     "why": [r.get("why") or r.get("error", "") for r in got],
                     "cuts": [[r.get("short_in_s"), r.get("short_out_s")] if r["status"] == "kept" else None
                              for r in got]})
    total = rounds * len(FIXTURES)
    same = sum(len({run[i]["status"] for run in runs}) == 1 for i in range(len(FIXTURES)))
    calls = [r for run in runs for r in run]
    return {"ok": right >= total - 2 and not hard and not problems,
            "right": right, "of": total, "hard_rule_kept": hard,
            "cut_problems": list(dict.fromkeys(problems)), "cut_notes": list(dict.fromkeys(notes)),
            "rounds_agree": same, "fixtures": rows,
            "errors": [f"{r['id']}: {r['error']}" for r in calls if r["status"] == "error"],
            "refused": sum(r["status"] == "refused" for r in calls),
            "retried": sum(bool((r.get("api") or {}).get("retried")) for r in calls), "calls": len(calls)}


VISION_SCHEMA = {"type": "object", "properties": {"color": {"type": "string"}, "number": {"type": "integer"}},
                 "required": ["color", "number"], "additionalProperties": False}


def test_image() -> str:
    """A red square with a white 37 on it, as base64 JPEG."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (512, 512), (200, 30, 30))
    ImageDraw.Draw(img).text((256, 256), "37", fill="white", anchor="mm", font=ImageFont.load_default(size=240))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def check_vision(claude: Claude, model: str, effort: str) -> dict:
    user = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": test_image()}},
            {"type": "text", "text": "What colour is the background (one word), and what number is written on it?"}]
    out, meta = claude.json_call(model, "You describe images exactly.", user, VISION_SCHEMA, effort)
    if out is None:
        return {"ok": False, "answer": "refused"}
    return {"ok": "red" in out["color"].lower() and out["number"] == 37, "answer": out,
            "retried": bool(meta.get("retried"))}


def check_text(cfg: dict, claude: Claude, model: str, effort: str) -> dict:
    rules = cfg["scoring"].get("house_style") or []
    system = "You write YouTube descriptions." + ("\n\nHouse style:\n" + "\n".join(f"- {r}" for r in rules)
                                                   if rules else "")
    text, _ = claude.text_call(model, system, "In two sentences, describe this Short for viewers of the "
                               "podcast's channel: Andy and Sam talk about Andy's landlord, who heard his smoke "
                               "detector chirping all night and thought Andy was learning the trumpet.", effort, 2000)
    problems = (["empty"] if not text else []) + (["over 800 chars"] if len(text) > 800 else []) \
        + (['says "hosts"'] if BANNED.search(text) else [])
    return {"ok": not problems, "answer": text, "problems": problems}


LONG_SCHEMA = {"type": "object", "properties": {"password": {"type": "string"}, "time_s": {"type": "number"}},
               "required": ["password", "time_s"], "additionalProperties": False}
NEEDLE = "Quick reminder for everyone: the raffle password tonight is purple walrus forty-two."
SUBJECTS = ["my cousin", "the guy at the gas station", "our neighbor", "the new intern", "my dentist",
            "a lady on the bus", "the delivery driver", "my old roommate", "the mayor", "the barista"]
DOINGS = ["tried to return a sandwich", "parked across three spaces", "learned to juggle", "bought a boat",
          "lost his keys in a lake", "started a podcast", "got stuck in a revolving door",
          "adopted a goat", "ran a marathon by accident", "fell asleep at a concert"]
TAILS = ["and nobody knows why.", "which honestly tracks.", "on a Tuesday.", "for the third time this year.",
         "and posted about it.", "and then blamed the weather.", "and called it a hobby.",
         "and wants us to talk about it.", "and I believe every word.", "and that was the whole story."]


def long_transcript(tokens: int = LONG_TOKENS, seed: int = 7) -> tuple[str, float]:
    """A made-up stream transcript of about `tokens` tokens with NEEDLE said once, about
    60% in; returns (transcript, the second it is said)."""
    rnd, lines, t, size = random.Random(seed), [], 0.0, 0
    while size < tokens * CHARS_PER_TOKEN:
        who = rnd.choice(CAST + ["?"])
        line = f"[{t:.1f}] [{who}] {rnd.choice(SUBJECTS).capitalize()} {rnd.choice(DOINGS)} {rnd.choice(TAILS)}"
        lines.append(line)
        size += len(line) + 1
        t += rnd.uniform(3, 9)
    at = int(len(lines) * 0.6)
    t_needle = float(re.match(r"\[([\d.]+)\]", lines[at]).group(1)) + 1.0
    lines.insert(at + 1, f"[{t_needle:.1f}] [Sam] {NEEDLE}")
    return "\n".join(lines), t_needle


def check_long(claude: Claude, model: str, effort: str) -> dict:
    ctx = llm.context_length(model)
    if ctx and ctx < LONG_TOKENS * 1.1:
        return {"ok": False, "skipped": f"takes {ctx:,} tokens; a long stream is ~{LONG_TOKENS:,}"}
    text, t_needle = long_transcript()
    out, meta = claude.json_call(model, "You read long livestream transcripts. Lines are "
                                 "\"[seconds] [speaker] words\".", text + "\n\nWhat is the raffle password, "
                                 "and at what second is it said?", LONG_SCHEMA, effort, stream=True)
    if out is None:
        return {"ok": False, "answer": "refused"}
    pw = out["password"].lower()
    found = "walrus" in pw and ("42" in pw or "forty" in pw)
    return {"ok": found and abs(out["time_s"] - t_needle) <= 5, "answer": out, "said_at": t_needle,
            "input_tokens": meta.get("input_tokens"), "retried": bool(meta.get("retried"))}


def estimate(cfg: dict, model: str, effort: str, long: bool) -> float:
    from .compare import out_tokens
    from .scoring import pass_a_user

    fcfg = fixture_cfg(cfg, model, effort)
    system = Prompts(fcfg).get("score_a")
    tokens_a = sum(len(system) + len(pass_a_user(w, words_of(fx["lines"]), signals_of(fx["peaks"]), 120))
                   for fx, w in zip(FIXTURES, windows_of(), strict=True)) / CHARS_PER_TOKEN * ROUNDS
    usd = llm.estimate(cfg, model, tokens_a, out_tokens(model, effort, "a") * len(FIXTURES) * ROUNDS)
    usd += llm.estimate(cfg, model, 2000, 2000)  # vision and text
    if long:
        usd += llm.estimate(cfg, model, LONG_TOKENS, out_tokens(model, effort, "a"))
    return usd


def timed(fn, *args) -> dict:
    t0 = time.monotonic()
    try:
        res = fn(*args)
    except ClipperError:
        raise
    except Exception as e:  # noqa: BLE001  # a failed check is a result, not a crash
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    return {**res, "seconds": round(time.monotonic() - t0, 1)}


def fitness(cfg: dict, checks: dict) -> list[dict]:
    """For each stage the config names a model for: could this model take it?"""
    rows = []
    for key in sorted(llm.configured(cfg)):
        if key.startswith("shows."):
            continue
        needs = NEEDS.get(key, ("json",))
        missing = [n for n in needs if n in checks and not checks[n]["ok"]]
        untested = [n for n in needs if n not in checks]
        rows.append({"stage": key, "ok": not missing and not untested, "failed": missing, "untested": untested})
    return rows


def test_model(cfg: dict, model: str, effort: str = "", budget: float = 1.0, long: bool = True,
               dry_run: bool = False) -> dict:
    if effort and effort not in EFFORTS:
        raise ClipperError(f"--effort {effort}: one of {', '.join(EFFORTS)}")
    effort = effort or cfg["scoring"]["effort_a"]
    est = estimate(cfg, model, effort, long)
    unpriced = llm.known_price(cfg, model) is None and llm.split(model)[0] != "local"
    print(f"models test {model} @{effort}: Pass A x{len(FIXTURES) * ROUNDS}, vision, text"
          + (f", long (~{LONG_TOKENS // 1000}k tokens)" if long else "") + f"; estimate ${est:.3f}"
          + (" (no price: counted as $0)" if unpriced else ""))
    if dry_run:
        return {}
    if unpriced:
        raise ClipperError(f"no price for {model}, so --budget can't hold: add "
                           '[llm.prices] "<model>" = [<$ per 1M in>, <$ per 1M out>] to clipper.toml')
    if est > budget:
        raise ClipperError(f"estimate ${est:.2f} is over --budget ${budget:.2f}; raise it to go ahead")
    claude = Claude(cfg)
    llm.preflight(claude, model)
    fcfg = fixture_cfg(cfg, model, effort)
    checks = {"pass_a": timed(check_pass_a, fcfg, claude),
              "vision": timed(check_vision, claude, model, effort),
              "text": timed(check_text, cfg, claude, model, effort)}
    if long:
        checks["long"] = timed(check_long, claude, model, effort)
    pa = checks["pass_a"]
    retried = pa.get("retried", 0) + sum(bool(checks[k].get("retried")) for k in ("vision", "long") if k in checks)
    json_errors = [e for e in pa.get("errors", []) if "schema" in e]
    checks["json"] = {"ok": "error" not in pa and not json_errors, "retried": retried, "errors": json_errors}
    res = {"model": model, "effort": effort, "prompt_version": cfg["scoring"]["prompt_version"],
           "at": time.strftime("%Y-%m-%d %H:%M"), "cost_usd": round(claude.cost, 4), "checks": checks,
           "fit": fitness(cfg, checks)}
    out = Path(cfg["paths"]["model_tests"]) / f"{slug(f'{model} {effort}')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    jsonstate.write(out, res)
    print(report(res))
    print(f"saved {out}")
    return res


def mark(ok: bool) -> str:
    return "ok  " if ok else "FAIL"


def report(res: dict) -> str:
    c = res["checks"]
    pa = c["pass_a"]
    lines = [f"{res['model']} @{res['effort']} (prompt {res['prompt_version']}): ${res['cost_usd']:.3f}"]
    if "right" in pa:
        lines.append(f"  {mark(pa['ok'])} pass_a  {pa['right']}/{pa['of']} verdicts right, {pa['rounds_agree']}/"
                     f"{len(FIXTURES)} the same both rounds, {pa['seconds']} s")
        for row in pa["fixtures"]:
            lines.append(f"         {row['id']:<13} want {row['expect']:<16} got {', '.join(row['got'])}")
        lines += [f"         hard rule broken: kept {x}" for x in pa["hard_rule_kept"]]
        lines += [f"         cut: {p}" for p in pa["cut_problems"]]
        lines += [f"         note: {p}" for p in pa.get("cut_notes", [])]
        lines += [f"         error: {e}" for e in pa["errors"]]
    else:
        lines.append(f"  FAIL pass_a  {pa.get('error')}")
    j = c["json"]
    lines.append(f"  {mark(j['ok'])} json    {j['retried']} answer(s) fixed on a retry"
                 + (f"; {len(j['errors'])} failed" if j["errors"] else ""))
    for k in ("vision", "text", "long"):
        if k not in c:
            continue
        r = c[k]
        detail = r.get("skipped") or r.get("error") or (
            ", ".join(r["problems"]) if r.get("problems") else str(r.get("answer"))[:90])
        lines.append(f"  {mark(r['ok'])} {k:<7} {detail} ({r['seconds']} s)")
    unfit = [f"{f['stage']} ({', '.join(f['failed'] + [u + ' untested' for u in f['untested']])})"
             for f in res["fit"] if not f["ok"]]
    n = len(res["fit"])
    lines.append(f"  could take all {n} stages this config names a model for" if not unfit else
                 f"  could take {n - len(unfit)} of {n} stages; not: " + "; ".join(unfit))
    return "\n".join(lines)


def list_tests(cfg: dict) -> None:
    """Every saved test, one line each, newest first."""
    d = Path(cfg["paths"]["model_tests"])
    rows = sorted((jsonstate.read(p) or {} for p in d.glob("*.json")), key=lambda r: r.get("at", ""), reverse=True)
    if not rows:
        print(f"no model tests in {d} yet: clipper models test <model>")
        return
    for r in rows:
        c = r["checks"]
        pa = c["pass_a"]
        print(f"{r['at']}  {r['model']} @{r['effort']}: pass_a {pa.get('right', 0)}/{pa.get('of', 0)} "
              + " ".join(f"{k} {'ok' if c[k]['ok'] else 'FAIL'}" for k in ("json", "vision", "text", "long") if k in c)
              + f"  ${r['cost_usd']:.3f}")
