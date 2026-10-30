# Keeps `clipper bot` running: starts it, and restarts it whenever it exits (crash, killed
# process, lost network), waiting 10 s and doubling up to -MaxDelay while it keeps failing
# fast. Run by the "clipper-bot" scheduled task (bot-task.ps1 -Install); you can also run
# it by hand. The bot itself logs to [paths] bot_log (logs/bot.log); this script adds
# logs/bot-supervisor.log (starts and exits) and logs/bot.console.log (the last run's
# console, which catches errors from before the bot's own logging starts).
# Only one bot runs at a time (it locks streams/.bot.lock): a second copy exits at once
# and is retried here, so this also takes over if a bot started elsewhere stops.
param([int]$MaxDelay = 300)

. "$PSScriptRoot\..\common.ps1"
Start-ClipperSupervisor -Command bot -ConsoleLog bot.console.log -MaxDelay $MaxDelay
