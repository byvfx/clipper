# Runs `clipper bot` unattended on this Windows desktop as the scheduled task
# "clipper-bot": it starts at logon (no window), bot-supervisor.ps1 restarts the bot when
# it exits, and the task re-checks every 5 minutes, so a killed supervisor comes back too.
# Runs as you, only while you're logged on, with no admin rights needed.
#
#   deploy\discord\bot-task.ps1 -Install     register (or update) the task and start it
#   deploy\discord\bot-task.ps1 -Status      task state, bot processes, recent log lines
#   deploy\discord\bot-task.ps1 -Restart     stop the bot and supervisor, start the task again
#   deploy\discord\bot-task.ps1 -Stop        stop the bot and disable the task (-Restart undoes it)
#   deploy\discord\bot-task.ps1 -Uninstall   stop everything and remove the task
#
# Logs: logs\bot.log (the bot), logs\bot-supervisor.log (restarts), logs\bot.console.log.
# A bot you start by hand (run-bot.ps1) while the task's bot runs exits at once: only one
# bot runs at a time. -Install refuses while a hand-started bot is running unless you pass
# -StopExisting.
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Install')][switch]$Install,
    [Parameter(ParameterSetName = 'Install')][switch]$StopExisting,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(ParameterSetName = 'Restart')][switch]$Restart,
    [Parameter(ParameterSetName = 'Stop')][switch]$Stop,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [string]$TaskName = 'clipper-bot'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\common.ps1"
$task = @{
    Name = $TaskName; Command = 'bot'; Extra = 'discord'
    Supervisor = Join-Path $PSScriptRoot 'bot-supervisor.ps1'; Script = $PSCommandPath
    Description = 'clipper Discord review bot'; Logs = 'bot-supervisor.log', 'bot.log'
}
Invoke-ClipperTask $task $PSCmdlet.ParameterSetName -StopExisting:$StopExisting
