What we have tried in place of Claude, what it cost and what came of it. The tools are on
[Model providers](Model-providers); this page is the record, newest first. Nothing here changed
a default: Pass A is still Haiku, Pass B still Sonnet.

## How the trials are scored

Every trial reuses a scored, reviewed stream's own windows, summary and prompt, so only the
model differs (`clipper compare score`). The columns below are `clipper compare list`'s:

| Column | Meaning |
|---|---|
| approved | moments approved in review that the candidate picked |
| same | windows given the same verdict as the stream's own Haiku run |
| kept | windows the candidate kept (Haiku keeps about one in eight) |
| rej>kept | windows Haiku rejected that the candidate kept: the guard against other creators' audio |
| err | calls that failed or came back malformed |

The approved count is noisy. Haiku scoring the same three streams again with its own prompt
picked only 7 of the 18 moments it picked the first time, so 7 of 18 is where "as good as
Haiku" sits, and the picks nobody reviewed can't count either way. Read `same`, `kept` and
`rej>kept` alongside it.

## 2026-11-09: prompt v8 on Haiku

`score_a_v8` is a restructured v7: headed sections (role, input, speaker tags, selection,
the decision order, hard rejections, examples, output rules) and numbered output rules. It
keeps everything v7 decides on: the per-show gambling and third-party rules, reading aloud as
the show's own audio, the order payoff, context, quality, the four examples, crude is not a
reason to reject, and about one window in eight. A kept clip scores 5-10. The rule files are
copies of v7, so only the main prompt differs. Config stays on v7 (kino, c404) and v5 (base).

| Haiku, prompt | approved (of 18) | same (of 194) | kept | rej>kept | labels right (of 66) | keep-or-not | Cost |
|---|---|---|---|---|---|---|---|
| v5 | 7 (a plain rerun) | 142 | 25 | 7 | 26 | 35 | $0.34 + $0.07 |
| v6 | 7 | 116 | 47 | 15 | 28 | 37 | $0.42 + $0.08 |
| v7 | 6 (2+3+1) | 112 | 92 | 37 | 39 (35 on a rerun) | 47 (42) | $0.55 + $0.09 |
| **v8** | **8** (3+1+4) | 125 | 62 | 27 | 36 | 46 | $0.51 + $0.09 |

Cost is the three streams plus one labelled run. On the streams v8 picks the most approved
moments of any prompt so far and keeps a third fewer windows than v7, with fewer of the ones
Haiku's original run threw out. On the labels it is within v7's own rerun spread (35-39), it
kept nothing against a hard rule (v7 kept 1), and its keeps are the most precise (88%). Its
main miss: 13 of the 45 keeps called needs-context (v7: 8), so the context step still bites
too hard. Neither gap is bigger than a rerun's, so v8 is a candidate, not a winner yet: the
next test is a week of live streams on one show.

## 2026-11-09: DeepSeek V4.1 Flash, the 10-17 snapshot

OpenRouter's plain `deepseek/deepseek-v4.1-flash` still points at the 2026-09-10 snapshot;
the newer one is only reached as `deepseek/deepseek-v4.1-flash-1017`, at $0.15 / $0.60 per 1M
tokens (the 09-10 snapshot is $0.045 / $0.50). Pinning a dated slug is the only way to know
which one ran.

Four tests, $1.98 in all. Not adopted: it picks as many approved moments as Haiku only by
keeping five times as many windows, it misses most of what a hard rule should drop, and its
reasoning makes it worse.

**On three reviewed streams** (`compare score`), the same three as the 11-05 runs, prompt v6,
Sonnet Pass B:

| Run | approved (of 18) | same (of 194) | kept | rej>kept | err | Cost | Time a stream |
|---|---|---|---|---|---|---|---|
| Haiku v5, a plain rerun | 7 | 142 | 25 | 7 | 0 | $0.34 | ~2 min |
| Haiku v6 | 7 | 116 | 47 | 15 | 0 | $0.42 | ~2 min |
| **V4.1 Flash 10-17 v6 @none** | **7** (3+2+2) | 69 | 125 | 57 | 0 | $0.43 | ~1.5 min |
| V4.1 Flash 10-17 v6 @medium | 2 (2+0+0) | 76 | 128 | 53 | 0 | $0.73 | ~6 min |
| V4.1 Flash 09-10 v6 @none | 4 (1+2+1) | 66 | 102 | 42 | 7 | $0.41 | ~1.5 min |

The 10-17 snapshot fixes the 09-10 one's errors. Reasoning on cost 1.7 times as much, took
four times as long and picked fewer approved moments. Reports, on kick-135553454,
kick-134467434 and kick-134812118:
`<stream>/compare/score_openrouter-deepseek-deepseek-v4.1-flash-1017-<effort>-v6_a.md`.

**Against the labelled windows** (`models gold run`, 66 windows from 12 streams, reasoning off).
Every row is a fresh run, so Haiku is graded here like the others:

| Model | labels right, v5 / v7 | keep-or-not, v5 / v7 | Cost a run |
|---|---|---|---|
| Haiku | 26 / 39 | 35 / 47 | $0.07-0.09 |
| DeepSeek V4 Pro | 46 / 48 | 48 / 51 | $0.22-0.29 |
| Qwen 3.8 Flash | 44 / 40 | 50 / 47 | $0.03 |
| **V4.1 Flash 10-17** | **45 / 43** | **48 / 48** | $0.03-0.04 |

It finds 87% of the keeps but almost none of the rejects: 0 of 6 needs-context windows, 3-4 of
9 other creators' audio, and it kept 4 windows against a hard rule. Haiku's trouble is the
opposite: it throws keeps away.

**The built-in checks** (`models test`, $0.016): JSON, vision, the house-style title and the
~9 h transcript all pass. The Pass A check fails on one thing only: all 12 verdicts were right,
but the clip it cut for a keeper was 28 s, under the 30 s minimum. That rules it out of
`scoring.model_a` and leaves the other 24 stages.

**The decision-model lab's test** (`lab/run.py brief`, all 1,341 windows, $0.73): the same 20
yes/no questions Jev answered, asked as one structured call. DeepSeek answers 0 or 1 nearly
every time instead of a probability. It said yes to "has a clip" on 74% of windows and flagged
casino gameplay on 5, where Haiku flagged 31. Its best question ranks windows at 0.65 AUC
against Haiku's keeps (Jev 0.77 on its best one, 0.83 combined), and combining its answers
does no better than chance (0.49). The lab ran from main's code with two small changes on
branch `jev-lab`: `run.py --effort`, and `decider.py` working without the Jev provider.

## 2026-11-05: a stricter prompt (v6) and the noise floor

`score_a_v6` adds an order of decisions (who carries the payoff, every reference explained,
then the score), says played clips and words read aloud are source, and aims at about one
window in eight. On the same 18 approved moments: Haiku v6 7, Qwen 3.8 Flash 6, GPT-6 Luna 5,
DeepSeek V4 Pro 4, V4.1 Flash 4, MiniMax 4, all at `none`. v6 loosened Haiku (12-19 windows
kept against v5's 7-11) without picking more, so the config stays on v5.

## 2026-11-04: the OpenRouter sweep

Twelve models, reasoning `none` and `medium`, as Pass A on kick-135553454 with Sonnet Pass B,
then the best on two more streams. Gemini 3.5 Flash Lite @none picked all 5 approved moments on
the first stream but 1 each on the other two. Argon agreed most with Haiku (36-38 of 60-67
verdicts) and kept fewest of its rejects, at about $0.60-0.80 a stream for Pass A. Every other
model kept 40-60 windows. Reasoning on cost more and did no better: Qwen 3.8 Flash @medium took
43 minutes a stream, and DeepSeek V4 Pro at `high` spent all 16,000 of Pass B's thinking tokens
before answering. All compare runs that week: $18.64.

## 2026-11-04: a decision model (Jev)

TypeSafe's `jev-1.14.0` answers typed yes/no questions for $0.042 per 1M input tokens, output
free. Ten side tests on branch `jev-lab` (rerun steps in `lab/TESTS.md` there) against 1,341
Pass A windows from 21 streams, Haiku's keeps, Pass B's picks and 7-day views. $0.89 for six
runs, 21.2M input tokens; Haiku's Pass A on the same windows was $1.28.

| Test | Result |
|---|---|
| Rank windows like Haiku (AUC vs Haiku's keeps) | best 0.83, with the show's full brief and 15 small questions combined in code; 0.72 without the brief |
| Stand in for Pass B (picks among keeps) | ~0.55, Haiku's own score 0.67 |
| Predict views (Spearman) | 0.04-0.28, Haiku's score 0.57 |
| Factual flags | casino gameplay 0.98, other creators' audio 0.86, needs context 0.70 |
| Reject the bottom 20% before Haiku | 99.6% agree with Haiku, no picks lost, but it saves about what Jev costs |
| Screen archive pieces for facts | best 0.72; keeping 95% of the facts still sends 89% of the words |
| Check finished Shorts before upload | every question near a coin flip (0.44-0.55) |

Good at concrete flags and at knowing when it is sure; no taste. Nothing of it went into the
pipeline. What did: the free other-voices hold (Shorts with over 5 s of voices that aren't the
show's cast arrive held, PR #146, see [Speakers](Speakers)), which beat every Jev question on
the one thing worth checking before upload.

## Trying the next one

```
clipper compare score <stream> --model openrouter:<vendor>/<model> --prompt-version v6 --effort none --dry-run
```

Use the three streams above so the tables line up, pin a dated slug, and run `clipper models
test` first: it catches a model that can't follow the format for a few cents.
