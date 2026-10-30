# Keeps `clipper worker` running: starts it, and restarts it whenever it exits, waiting
# 10 s and doubling up to -MaxDelay while it keeps failing fast. Run by the
# "clipper-worker" scheduled task (worker-task.ps1 -Install); you can also run it by hand.
# Logs: logs\worker.log (the worker's console), logs\worker-supervisor.log (restarts).
# Each stage of a run also logs into its stream folder (run_<stage>.log, run.json).
# Failure alerts use CLIPPER_ALERT_WEBHOOK, else the recorder's webhook from
# deploy\record\env.local; the supervisor log says which.
param([int]$MaxDelay = 300)

. "$PSScriptRoot\..\common.ps1"
Start-ClipperSupervisor -Command worker -ConsoleLog worker.log -MaxDelay $MaxDelay -RecorderWebhook
