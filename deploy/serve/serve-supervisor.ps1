# Keeps `clipper serve` running: starts it, and restarts it whenever it exits (crash, killed
# process, lost share), waiting 10 s and doubling up to -MaxDelay while it keeps failing
# fast. Run by the "clipper-serve" scheduled task (serve-task.ps1 -Install); you can also
# run it by hand. serve logs to [paths] serve_log (logs/serve.log); this script adds
# logs/serve-supervisor.log (starts and exits) and logs/serve.console.log (the last run's
# console, which catches errors from before serve's own logging starts).
# Only one serve runs at a time (it locks streams/.serve.lock): a second copy exits at once
# and is retried here, so this also takes over if a serve started elsewhere stops.
param([int]$MaxDelay = 300)

. "$PSScriptRoot\..\common.ps1"
Start-ClipperSupervisor -Command serve -ConsoleLog serve.console.log -MaxDelay $MaxDelay -RecorderWebhook
