Every model call (#50) appends its cost to `[paths] costs` (`logs/costs.jsonl`) with the
command and stream it ran for; `clipper costs` and the [war room](War-room#modal-costs-and-analytics)'s
Costs panel add that to the Modal run log and your fixed costs: put plans and subscriptions in
`[costs] monthly` (`{"Runway Max" = 95}`) and a provider's monthly credit in `[costs] credits`
(`{"modal" = 30}`, shown as credit, not hidden). `clipper costs --backfill` once reads back
model spend earlier runs wrote into their output files (score, segments, receipts, weekly).

```bash
clipper costs [--month 2026-10]     # month's spend: models + Modal + fixed, by stage/show/stream
clipper modal                       # GPUs up, runs, spend, archive ETA
```

Modal's own billing is the real number for transcription ([Transcribe on Modal](Transcribe-on-Modal#watching-modal));
the bot DMs each time the month passes a multiple of `[modal] alert_every_usd`.

## What things cost

Rough figures from the other pages, to check before a big run:

| Job | Cost |
|---|---|
| Transcribe an 8 h stream on Modal | ~$0.34 (L40S), ~$0.26 (L4) |
| Weekly issue draft + three articles | ~$0.35 + ~$0.10 each |
| `archive ask` | ~1c |
| `receipts find` / `script` | ~$0.20 / ~$0.02 |
| `investigate facts` | ~$0.20 for 43 h; the whole Jer catalog ~$14 once |
| Search swarm, per lead | ~$0.01-0.03 |
| News check (Haiku) | well under a cent |
| Cast photo label | ~$0.0002 a photo; Brave image search ~$5 per 1,000 |
| X post | ~$0.015 (a link post $0.20, so clipper never adds one) |

A rerun of `score`, `segments`, `meta` or `thumbs` on a long stream pays again; `score` refuses
without `--force`. Use `--dry-run` where a command offers it for the estimate.
