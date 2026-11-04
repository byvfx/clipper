`bash deploy/record/deploy.sh` from the repo root (ssh alias `nas`). It copies clipper.toml
to the NAS; the recorder only reads `[record]` and `[kick]`. Re-run it after changing
`[record] channels`. It refuses while a show is being recorded (the restart would start a
new recording part): `clipper record status` shows what the recorder is doing, from the
heartbeat it writes to the share, and `deploy.sh --force` deploys anyway.
`clipper services stop|restart recorder` stops or restarts it over ssh without a new build,
with the same check.

## Cut on the NAS (#51)

The same image cuts segments and compilations beside the source,
instead of the laptop pulling and pushing every byte over the share (it writes at ~23 MB/s
on Wi-Fi). In clipper.toml: `[cut] backend = "nas"`, `nas_host` (the ssh alias, key login),
`nas_data` (the recorder's `CLIPPER_DATA`, e.g. `/volume1/docker/clipper`) and `nas_user`
(its `PUID:PGID`, e.g. `1000:10`). Each `clipper cut` then sends this checkout's `clipper/`
package and clipper.toml over ssh and runs in a one-off container (`clipper-cut-<stream>`),
so the recorder needs no redeploy for code changes, only for a new image. Picks still cut
here (they re-encode). `clipper doctor --role gpu` checks the ssh login and the image.
A cut stopped half way can leave its container running: `ssh nas docker rm -f clipper-cut-<id>`.
