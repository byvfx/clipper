# Jev lab: where does a decision model fit?

Side tests, 2026-11-04, on branch `jev-lab` (main + the 11-03 `jev-gate` experiment). Nothing
here changes the pipeline. Every test can be rerun on another model with `--model` (any clipper
model string; `lab/decider.py` asks a non-Jev model the same typed questions as one structured-
output call), so the same numbers can be produced for OpenRouter models once that provider exists.

## Setup

- Worktree: `C:\clipper-jevlab` (branch `jev-lab`). Run from `C:\clipper` (for clipper.toml and the
  NAS paths) with the lab's package first on the path:
  ```
  $env:PYTHONPATH = "C:\clipper-jevlab"
  $env:TYPESAFE_API_KEY = [Environment]::GetEnvironmentVariable("TYPESAFE_API_KEY","User")
  C:\clipper\.venv\Scripts\python.exe C:\clipper-jevlab\lab\run.py lean
  ```
  (In Git Bash: `PYTHONPATH=C:/clipper-jevlab`, one path only; `:` is not the Windows separator.)
- Data: `lab/data/` (gitignored: it holds stream transcripts). `windows.jsonl` is the dataset;
  `<variant>.jsonl` are Jev's answers; `<variant>__<model>.jsonl` another model's.
- Jev is pinned to `typesafe:jev-1.14.0` (the docs: aliases move; thresholds are per version).
- Cost: Jev is $0.042 per 1M input tokens, output free. The costs ledger line fails while serve
  holds its lock; the lab prints token counts instead.

## The dataset (T0)

`python lab/build.py` -> `lab/data/windows.jsonl`: every Pass A window of every scored stream
(21 streams, 12 on prompts v4 and 9 on v5, Kino and Channel 404): 1,341 windows, Haiku kept 276,
Pass B picked 110, 58 picks have 7-day YouTube views. Each row has the text Haiku read, the bare
transcript, Haiku's status / score / flags, the pick and its rank, views, and Haiku's cost.
Rebuild it when there are more streams or views (it reads candidates.json, scan_<v>.json,
windows.json and analytics.json; no model calls).

## Scoring tests (T1-T5)

`python lab/run.py VARIANT [--model M]`, then `python lab/analyze.py VARIANT ...`.
Variants are a state plus a question set (`lab/run.py`):

| variant | state Jev reads | questions | tokens/window | 1,341 windows |
|---|---|---|---|---|
| `brief_old` (T1) | rendered Pass A brief + Haiku's window text | prompts/gate_v5.md (11-03) | ~4,900 | ~$0.28 |
| `lean` (T2) | JSON: show line, cast, lead-in, window, chat sample (no brief, no signal table) | gate (self-contained, situational score levels) + 15 atomic nouls | ~3,000 | $0.17 |
| `body` (T3) | JSON: cast + window transcript only | gate + 13 atomic nouls (no chat ones) | ~2,000 | $0.11 |
| `brief` (T4) | as brief_old | as lean | ~4,900 | $0.28 |

Haiku's Pass A cost $1.28 for the same windows.

`analyze.py` columns: AUC against Haiku's keeps, against Pass B's picks, and against picks among
Haiku's keeps (could it stand in for Pass B); `passB_overlap` (top n of each stream's keeps vs its
n picks); Spearman with 7-day views (n=58); and "send the top X% of each stream's windows on":
share of picks / top-quartile-viewed picks that get through. Baselines: chat heat (free) and
Haiku's own score. `composite` = logistic regression over a variant's answers, fitted leave-one-
stream-out.

### Results (Jev 1.14.0)

| | vs Haiku keeps | vs picks | picks within keeps | views rho |
|---|---|---|---|---|
| chat heat (free) | 0.35 | 0.43 | 0.59 | 0.12 |
| Haiku score | (its own) | 0.94 | 0.67 | **0.57** |
| T2 lean has_clip / composite | 0.64 / 0.72 | 0.67 / 0.71 | 0.59 | ~0 |
| T3 body has_clip / composite | - / 0.74 | - / 0.75 | 0.59 | 0.15 |
| T4 brief has_clip / composite | 0.77 / **0.83** | 0.76 / 0.79 | 0.53-0.58 | 0.04-0.28 |
| T1 brief_old has_clip / composite | 0.77 / 0.80 | 0.77 / 0.77 | 0.58 | 0.28 |

On the 7 v5 streams (403 windows) the 11-03 run had has_clip AUC 0.81; T4's composite 0.885.
T1 reruns 11-03's exact setup a day later (pinned 1.14.0 vs the jev-latest alias): has_clip
0.811 vs 0.809 on those windows, so Jev is repeatable run to run.

- The brief matters: removing it (T2, T3) drops has_clip from 0.77 to 0.63, against the docs'
  advice to keep state short. Jev needs the show's rules, not just the words.
- Many atomic nouls combined in code (the docs' composite scoring) beat any single question.
- Jev can't stand in for Pass B (picks within keeps ~0.55 vs Haiku's 0.67) and nothing Jev says
  tracks views; Haiku's score does (rho 0.57 with 58 Shorts; it was 0.1 with 25 on 11-03).
- A gate in front of Haiku (T5, top X% of each stream by T4 composite): 90% -> loses nothing,
  80% -> 99% of picks and every top-viewed one, 70% -> 95% / 87%. Saves ~20% of Haiku Pass A
  (~$0.012 a stream) and costs about the same in Jev. Not worth wiring.

## Flags and routing (T6, T7)

T6, Jev's flags against Haiku's own flags (AUC, 1,341 windows; `brief` / `lean`):
casino_gameplay **0.975 / 0.978** (31 flagged), third_party_audio 0.86 / 0.83 (432),
needs_context 0.70 / 0.61 (878). Concrete, factual calls are where Jev is strong; taste is not.

T7, route by confidence (the docs' pattern): Jev answers when it is sure, Haiku otherwise
(`brief`; agreement with Haiku):
- keep/no: Jev rejects its lowest 20% (composite) alone: 99.6% right, 0 picks lost; 40%: 96.3%, 8 lost.
- casino: its surest 57-73%: 100%.
- third-party audio: surest 21%: 97.2%; 42%: 94.8%; 61%: 90.0%.

## Archive facts screen (T8)

`python lab/facts.py [--size 60] [--model M]`: Jer's 211 investigated items cut into 60 s pieces
(2,688; 46% hold one of Haiku's 1,680 verified facts). One call per piece, three nouls. $0.06.
Best (claim): AUC 0.72; keeping 95% of fact pieces still sends 89% of the words. Not worth it.
Length alone: AUC 0.60.

## Rerunning on OpenRouter

1. Add the provider to `clipper/llm.py` (an `openrouter:` prefix, OpenAI-compatible).
2. `run.py brief --model openrouter:<model>` etc. (and `facts.py --model ...`). An LLM answers
   yes/no as a stated probability, which is coarser than Jev's; compare AUC and the routing
   tables, not raw thresholds. Mind the cost: ~4,900 tokens x 1,341 windows = 6.6M input tokens
   per brief variant (Haiku would be ~$7; check the model's price first). `--limit` and
   `--streams` cut it down; the 7 v5 streams (`kick-1343*`..`kick-1355*`) are 403 windows.
3. `analyze.py brief brief__openrouter-<model>` puts them side by side.
4. Routing test (needs step 2 on two or more models): per window, which models agree with Haiku and
   with Pass B; then does Jev's confidence (|2p-1| per noul) predict where the cheap model is wrong
   (escalate only those)? That is T7 with a model ladder instead of Jev -> Haiku.

## Not tested yet

- Few-shot examples inside the instructions (docs: helps only when examples resemble inputs).
- Choice-option order bias on `category` (docs: leans to the first option).
- Images (Jev 1.14 takes PNG/JPEG, "Dreamer-ready"): cover frame choice in `thumbs`, which Opus does.
- A pre-upload guardrail (casino / third-party audio / needs-context on the final Short), the
  place T6 says Jev is good.
