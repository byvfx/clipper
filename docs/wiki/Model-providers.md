Every model key (`[scoring] model_a`, `[segments] model`, `[shorts] layout_model`, ...) picks
its provider by prefix; a bare name like `claude-sonnet-5-5` is Claude, as before.

| Model string | Provider | Needs |
|---|---|---|
| `openai:<model>` | OpenAI | `OPENAI_API_KEY` (user env var), or `secrets/openai_secret.txt` |
| `gemini:<model>` | Google Gemini (its OpenAI-compatible endpoint) | `GEMINI_API_KEY`, or `secrets/gemini_secret.txt` |
| `openrouter:<vendor>/<model>` | OpenRouter: one key for most vendors' models, e.g. `openrouter:openai/gpt-5-mini` | `OPENROUTER_API_KEY`, or the key in `secrets/openrouter_secret.txt` (`[paths] openrouter_key`) |
| `local:<model>` | Ollama, vLLM, LM Studio or any OpenAI-compatible server | `[llm] local_base_url` in clipper.toml (e.g. `http://<host>:11434/v1`); `LOCAL_LLM_API_KEY` if it wants one |

Add prices under `[llm.prices]` (`"openai:gpt-5-mini" = [0.25, 2.00]`, $ per 1M input/output
tokens) so the cost lines stay right; an unlisted model counts as $0, except on OpenRouter,
which reports what each call cost ([its own count](Costs#the-providers-own-numbers) is in the war room). `clipper doctor` checks the key or URL for every provider in
use and, online, that the server lists the model; `clipper setup --step keys` asks for the key of
any provider a stage uses. `ANTHROPIC_API_KEY` is only required while some stage is on Claude.

> [!NOTE]
> The prompts are tuned on Claude. Before moving a stage, compare the two (below) on a
> few reviewed streams; if the other model needs different wording, add a new prompt
> version for it. Claude's server-side refusal fallback has no equivalent elsewhere, and the
> content is crude, so expect more refusals (a refused call is skipped the same way).

## Trying a model on a stream

`clipper compare score <stream> --model <model>` scores a stream again with another model and
puts it side by side with what the stream already has, and with the review decisions synced
from Kitsu. It never touches `candidates.json`; it uses the prompt version the stream was scored
with, the same windows (chat and transcript scan) and the cached summary, so only the model differs.

| `--pass` | What the candidate does | Cost on an 8 h stream |
|---|---|---|
| `a` (default) | judges every window (Pass A); Pass B stays on the stream's model | the most: one call per window |
| `b` | ranks the stream's own Pass A results (Pass B) | one call |
| `ab` | both (`--model-b` gives Pass B its own candidate) | both |

`--prompt-version v6` tries another prompt version on the same windows and summary (the
stream's own caches); with the stream's own model it shows what the prompt alone changes.

`--repeat` allows the stream's own model and prompt, to see how much a plain rerun changes:
the noise floor under every other comparison.

`--effort` sets the candidate's effort instead of `[scoring] effort_a`/`effort_b`. `none` turns a
reasoning model's thinking off on the other providers (and is `low` on Claude): DeepSeek V4 Flash
thinks by default, about 2,200 output tokens a window, which cost more than Haiku on 11-04, and
V4 Pro at `high` spent all 16,000 of Pass B thinking. Any `*effort` key in the config takes
`none` too.

It prints the estimate first and refuses a run over `--budget` ($2 by default); `--dry-run` only
prints it. An OpenRouter model with no `[llm.prices]` entry is estimated from OpenRouter's own
price list; any other model with no price is refused, since the budget couldn't hold. A stream
with no cached summary gets one made with its own Pass A model, counted in the estimate. Hand
picks (`clipper pick`) are left out on both sides. The report goes to `<stream>/compare/score_<model>_<pass>.md` (and `.json`):

- Pass A: how many windows got the same verdict (kept, no clip, rejected, refused, error), and
  for windows both kept, the score difference and how much the clips overlap.
- Picks: each moment either side picked, with both titles and the review decision.
- Against review: how many of the approved, sent-back and rejected moments each side picked.

`clipper compare list <stream>` puts every report on a stream in one table, best first: approved
moments picked, verdicts shared, windows kept, windows the stream's model rejected that the
candidate kept (the guard against other creators' audio), errors, cost and time.

Older streams were scored with older prompts, and the analytics feedback Pass B reads has moved
on since, so compare a few recent reviewed streams before moving a stage's default.

The approved-moments count is noisy: on 11-05 Haiku scoring the same three streams again with
its own prompt found only 7 of the 18 moments it had found the first time. Use the two checks
below to choose a model, and `compare` to see what a change does on a real stream.

## Checking a model

`clipper models test <model>` needs no stream: it brings its own material, the same for every
model and provider, so a model can be tried before any stage moves to it.

| Check | What it asks | Stages that need it |
|---|---|---|
| `pass_a` | six made-up windows (two clean keepers, a played video, slots, a running joke nobody explains, a break) through the real Pass A code with the prompt version in use, twice: the verdicts, the cut rules on what it keeps (Short 15-60 s around the payoff, clip 30-300 s, no "hosts") and whether both rounds agree | `scoring.model_a` |
| `json` | every structured answer matched its schema (an answer fixed on the retry is counted: it costs double) | all |
| `vision` | a picture with a colour and a number on it | `shorts.layout_model`, `cast.label_model` |
| `text` | a short description with the house style applied | `meta.model` |
| `long` | a password said once in a made-up ~9-hour transcript (~130k tokens) | `segments.model` |

The report ends with which of the stages your config names a model for it could take. A hook a
word over the limit is a note, not a failure. `--effort none` turns reasoning off (other
providers); `--no-long` skips the long transcript, which is most of the cost (a few cents in all
for the models tried on 11-05). Results go to `[paths] model_tests` (`logs/model_tests/`);
`clipper models list` shows every saved test. The made-up windows are easy on purpose: they
catch a model that can't follow the format or the hard rules, not one with poor taste.

## Grading a model against your own calls

`clipper models gold` grades Pass A against windows a person has labelled: the answer key.

1. `clipper models gold propose` gathers windows from the 12 newest reviewed streams into
   `[paths] model_gold` (`logs/model_tests/gold.json`): moments approved in review come in as
   `keep`, already confirmed; windows the stream's model rejected or passed on come with its
   reason as the proposed label (`third_party`, `needs_context`, `casino`, `no_clip`); windows
   it kept that were never approved come unlabelled. Run it again later to add more; labels
   already confirmed are never changed.
2. Label them: set `"label"` and `"confirmed": true` in the file, or put the labels in a file
   shaped `{"<stream>_<id>": {"label": "keep", "note": "why"}}` and run
   `clipper models gold confirm <file>`.
3. `clipper models gold run <model> [--effort none] [--prompt-version v6]` scores the confirmed
   windows, each stream under its own show's rules, and reports how many verdicts match, keep or
   not, how many played clips or gambling moments it kept, and recall and precision per label.
   It shows the stream's own model next to it, which is flattering: only what it kept could be
   approved, and its reasons seeded the proposals. `--dry-run` prints the estimate (about two
   cents for 24 windows on a cheap model); over `--budget` ($2) is refused.
