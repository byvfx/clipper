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

## 2026-11-09: DeepSeek V4.1 Flash, the 10-17 snapshot

OpenRouter's plain `deepseek/deepseek-v4.1-flash` still points at the 2026-09-10 snapshot;
the newer one is only reached as `deepseek/deepseek-v4.1-flash-1017`, at $0.15 / $0.60 per 1M
tokens (the 09-10 snapshot is $0.045 / $0.50). Pinning a dated slug is the only way to know
which one ran.

Same three streams as the 11-05 runs, prompt v6, reasoning off (`--effort none`), Sonnet Pass B:

| Run | approved (of 18) | same (of 194) | kept | rej>kept | err | Cost, 3 streams |
|---|---|---|---|---|---|---|
| Haiku v5, a plain rerun | 7 | 142 | 25 | 7 | 0 | $0.34 |
| Haiku v6 | 7 | 116 | 47 | 15 | 0 | $0.42 |
| **V4.1 Flash 10-17 v6** | **7** (3+2+2) | 69 | 125 | 57 | 0 | $0.43 |
| V4.1 Flash 09-10 v6 | 4 (1+2+1) | 66 | 102 | 42 | 7 | $0.41 |

The 10-17 snapshot fixes the 09-10 one's errors and ties Haiku on approved picks, but it keeps
five times as many windows and 57 that Haiku threw out, so Pass B and review would carry the
filtering Haiku does now. Not adopted. Reports, on kick-135553454, kick-134467434 and
kick-134812118:
`<stream>/compare/score_openrouter-deepseek-deepseek-v4.1-flash-1017-none-v6_a.md`.

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
