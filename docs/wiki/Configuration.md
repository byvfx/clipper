Settings come in layers: built-in defaults, then `clipper.toml`, then this machine's own
`clipper.local.toml` (written by `clipper setup`: streams path, encoder, transcribe backend),
then environment variables named `CLIPPER_<SECTION>_<KEY>` (for example
`CLIPPER_CUT_CLIP_CODEC=libx264`). That lets one shared `clipper.toml` serve every machine.
`clipper.local.toml` and env vars win over show and stream overrides too.

Each Kick channel is a show: a `[shows.<key>]` table with its channels, show name, scoring
context and Shorts layouts. A show's sub-tables override the base sections for that show.
One stream can go further with its own `streams/<id>/config.toml` (same sections), for a big
night: more picks, the breaking story in `[scoring] stream_context`, and `[schedule]
priority = true` so its items go out ahead of the backlog. Unknown keys there are refused.
[`docs/big-story.toml`](https://github.com/byvfx/clipper/blob/main/docs/big-story.toml) is the ready-made one to copy: more picks and Shorts, priority, and the
night's compilation. A `[schedule] stream_budget` kind at 0 goes only when pinned, so
`compilation = 0` there keeps compilations for big-story nights.
`clipper/defaults.toml` documents every key with its default; `clipper.toml` holds only what
differs (`clipper setup` starts a new one that way, and `clipper.example.toml` is a starter to
copy). `clipper.toml` itself is gitignored because it holds your
Discord ids and Kitsu address.

`clipper config get meta.model` prints a setting's value and every layer that sets it
(add `--stream <id>` for one stream, or `--show <key>`). Settings with a fixed set of choices
(`[bleep] mode`, the backends, `[thumbnail] style`, ...) refuse anything else when the config
loads; an unknown setting or `CLIPPER_*` variable is a warning on every command, and
`clipper doctor` fails on it. `clipper --show <key>` applies that show to any stream.

`clipper config diff` lists every setting this install changes from `clipper/defaults.toml`,
by layer: `clipper.toml`, each show's table, `clipper.local.toml`, then `CLIPPER_*` variables
(`clipper config diff kino` keeps one show's table; `--json` for a script). It answers "what
did I change" without opening the file on the share.

Secrets never go in the config: API keys and tokens are environment variables, and the
YouTube OAuth files live in `secrets/` (gitignored).
