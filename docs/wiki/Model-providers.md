Every model key (`[scoring] model_a`, `[segments] model`, `[shorts] layout_model`, ...) picks
its provider by prefix; a bare name like `claude-sonnet-5-5` is Claude, as before.

| Model string | Provider | Needs |
|---|---|---|
| `openai:<model>` | OpenAI | `OPENAI_API_KEY` (user env var) |
| `gemini:<model>` | Google Gemini (its OpenAI-compatible endpoint) | `GEMINI_API_KEY` |
| `local:<model>` | Ollama, vLLM, LM Studio or any OpenAI-compatible server | `[llm] local_base_url` in clipper.toml (e.g. `http://<host>:11434/v1`); `LOCAL_LLM_API_KEY` if it wants one |

Add prices under `[llm.prices]` (`"openai:gpt-5-mini" = [0.25, 2.00]`, $ per 1M input/output
tokens) so the cost lines stay right; an unlisted model counts as $0. `clipper doctor` checks
the key or URL for every provider in use and, online, that the server lists the model.

The prompts are tuned on Claude. Before moving a stage, run both on the test stream
`kick-131151926` and compare; if the other model needs different wording, add a new prompt
version for it. Claude's server-side refusal fallback has no equivalent elsewhere, and the
content is crude, so expect more refusals (a refused call is skipped the same way).
