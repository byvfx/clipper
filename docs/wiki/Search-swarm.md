`clipper/swarm.py` is the search `clipper story` (#84) and `investigate check` use: many
small searches at once, each free to follow up on what it finds. It is library code, not a command.
A planner (Sonnet) turns the goal (a story brief, a lead) into up to 16 first tasks; workers (Haiku,
8 at a time) each run one tool in code and read its numbered results: YouTube search (bounded to
the run's week, month or year, approximate dates), a YouTube channel's uploads and streams, a Kick
channel's VODs, an Odysee channel, Reddit and the web (Brave), an archive subject's transcripts and
our own streams' segments. Each worker marks the results that fit (strong or maybe) and proposes up to 3 follow-ups (a clip channel
that keeps covering it, another person, a quote to search). The coordinator merges findings by
platform id, queues follow-ups it hasn't seen as the next round, and stops at `[swarm]
max_rounds`, `max_tasks`, `budget_usd` or `max_wall_s`. A channel task lists up to `channel_depth`
videos, keeps the run's dates and passes the 20 whose titles fit the goal best. Model output never
becomes a fetch: a finding is one of the tool's own results, a follow-up must parse as its kind
(a query, a supported platform's channel, an archive on file), and targets asking for anyone's
whereabouts or private details are dropped. The trail is the caller's `swarm.json` (every task,
its parent, results, findings, cost); a rerun resumes it. A 30-task run on Jer's Huck story found
109 results (44 strong) across Kick, YouTube clip channels, Odysee, Reddit and the web for ~$0.03.

## Setup

The swarm needs `ANTHROPIC_API_KEY`
and yt-dlp (with deno, as for pre-shows). Its web and Reddit searches use the same Brave key; Kick,
Odysee and YouTube listings need no key. YouTube searches run from this machine's IP, so a bot
check here affects them too. Per-source request rates are `[swarm] rates`.
