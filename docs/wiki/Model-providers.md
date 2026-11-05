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
which reports what each call cost. `clipper doctor` checks the key or URL for every provider in
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

It prints the estimate first and refuses a run over `--budget` ($2 by default); `--dry-run` only
prints it. An OpenRouter model with no `[llm.prices]` entry is estimated from OpenRouter's own
price list. The report goes to `<stream>/compare/score_<model>_<pass>.md` (and `.json`):

- Pass A: how many windows got the same verdict (kept, no clip, rejected, refused, error), and
  for windows both kept, the score difference and how much the clips overlap.
- Picks: each moment either side picked, with both titles and the review decision.
- Against review: how many of the approved, sent-back and rejected moments each side picked.

Older streams were scored with older prompts, and the analytics feedback Pass B reads has moved
on since, so compare a few recent reviewed streams before moving a stage's default.
