The code can be cloned again; the months of editorial judgement can't. That lives in the config,
the hand-edit backups, the enrolled faces and voices, the cast photos, the layouts and each
stream's review state. `clipper backup` zips those. `clipper streams` shows what the stream
folders take on the share and frees the big files of streams that are done with.

## clipper backup

```bash
clipper backup --dry-run        # what would go in, by folder, with sizes
clipper backup                  # write backups/taste/taste-<date>-<time>.zip
clipper backup --with-secrets   # secrets/ too
clipper backup --to D:/offsite  # somewhere else
```

What goes in, relative to the config file's folder (`/data` on the NAS hub):

| What | Where |
|---|---|
| The config | `clipper.toml`, `clipper.local.toml` |
| Hand-edit copies | `backups/` (not the backup folder itself) |
| Schedule, people, spend | `schedule.json`, `people.json`, `logs/costs.jsonl` |
| Faces and voices | `faces/` (not the downloaded models), `voices/` |
| Shorts layouts | `layouts/` |
| Cast photos | `[thumbnail] photos`, with each person's `cast.json` |
| Your own prompts | `[paths] prompts`, if set |
| Each stream | `config.toml`, `meta.json`, `review.json`, `review_state.json`, `thumbnails.json`, `retakes.json` |
| Secrets | `secrets/`, only with `--with-secrets` or `[backup] with_secrets` |

These stay out: the recordings and cuts (the videos are on YouTube), the transcripts (Modal can
redo them), `deploy/*/env.local` on the machine you deploy from, and Kitsu's database. All of
this install's zips together come to about 30 MB.

serve makes one when the newest is `[backup] every_days` old (7; 0 = only by hand) and keeps the
newest `[backup] keep` (8). `clipper doctor` and the weekly [Audit](Audit#backups) say when the
newest is older than `[backup] max_age_days`.

A zip on the NAS doesn't survive the NAS. Copy `backups/taste/` to another disk now and then.
To restore, unzip it over the config folder (or pick out the files you need).

### Kitsu

Every review decision and note is in Kitsu's database, in its Docker volumes on the NAS. Stop
Kitsu, then tar each volume (`docker volume ls` lists them):

```bash
docker run --rm -v <volume>:/v -v "$PWD":/b alpine tar czf /b/kitsu-<volume>.tgz -C /v .
```

## Disk: clipper streams

```bash
clipper streams list                  # size per stream: source, audio, cuts, previews
clipper streams prune                 # what prune would remove (older than 30 days)
clipper streams prune --yes           # remove it
clipper streams prune --cuts --yes    # and the long cuts of uploaded or rejected items
clipper streams prune --stream kick-1 # one stream; says why when it's left alone
```

A Kino night takes 60-90 GB: the source recording is about 30 GB, the audio 1 GB, the segment
and compilation cuts 20-50 GB and the review previews 3-8 GB.

prune removes the source (`source.*`) and the audio (`audio.*`). It keeps every JSON file
(transcript, picks, review state, `config.toml`), so the cheap stages and archive lookups still
work. `--cuts` also removes the segment and compilation video and the review preview of each
item that is uploaded or rejected. An approved item the scheduler let go keeps its file, so a
pin can still send it. Clips and Shorts always stay, because X and TikTok post from them. What
was removed goes in the stream's `pruned.json`, and a stage that needs the source afterwards
says it was pruned.

A stream is left alone when:
- it is in `[streams] never_prune` (a test stream you re-cut)
- it is recording, or the worker is running it
- it isn't in review yet
- it is younger than `--older-than` (default `[streams] keep_source_days`, else 30 days)
- an item is still waiting on a decision, is scheduled and not uploaded, or is held while it
  could still go out (`[schedule] stale_days` after the stream)

With `[streams] keep_source_days` set, serve runs `clipper streams prune --yes` once a day. It
is 0 (only by hand) by default.
