# Runs `clipper serve` unattended on this Windows machine as the scheduled task
# "clipper-serve": it starts at logon (no window), serve-supervisor.ps1 restarts it when it
# exits, and the task re-checks every 5 minutes, so a killed supervisor comes back too.
# Runs as you, only while you're logged on, with no admin rights needed. serve is the war
# room, the hourly replan and upload, pre-show grabs, retakes and the daily jobs; the
# Discord bot (deploy\discord) is only Discord and asks serve to replan.
#
#   deploy\serve\serve-task.ps1 -Install     register (or update) the task and start it
#   deploy\serve\serve-task.ps1 -Status      task state, processes, recent log lines
#   deploy\serve\serve-task.ps1 -Restart     stop serve and the supervisor, start the task again
#   deploy\serve\serve-task.ps1 -Stop        stop serve and disable the task
#   deploy\serve\serve-task.ps1 -Uninstall   stop everything and remove the task
#
# Logs: logs\serve.log (serve and its jobs' output), logs\serve-supervisor.log (restarts),
# logs\serve.console.log. Only one serve runs per streams folder (streams\.serve.lock): a
# second copy exits at once. -Install refuses while a serve started by hand is running
# unless you pass -StopExisting. A restart kills a running upload: `clipper services
# status serve` shows the jobs; wait until none is running.
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Install')][switch]$Install,
    [Parameter(ParameterSetName = 'Install')][switch]$StopExisting,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(ParameterSetName = 'Restart')][switch]$Restart,
    [Parameter(ParameterSetName = 'Stop')][switch]$Stop,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [string]$TaskName = 'clipper-serve'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\common.ps1"
$task = @{
    Name = $TaskName; Command = 'serve'; Extra = 'faces'
    Supervisor = Join-Path $PSScriptRoot 'serve-supervisor.ps1'; Script = $PSCommandPath
    Description = 'clipper serve: war room, uploads, pre-shows, retakes, daily jobs'
    Logs = 'serve-supervisor.log', 'serve.log'
}
Invoke-ClipperTask $task $PSCmdlet.ParameterSetName -StopExisting:$StopExisting
