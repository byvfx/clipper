# Runs `clipper worker` unattended on this GPU machine as the scheduled task
# "clipper-worker": it starts at logon (no window), worker-supervisor.ps1 restarts the worker
# when it exits, and the task re-checks every 5 minutes. Runs as you, only while you're
# logged on (it needs your NAS share login and GPU), with no admin rights needed.
# The worker keeps Windows awake while a run is going.
#
#   deploy\worker\worker-task.ps1 -Install     register (or update) the task and start it
#   deploy\worker\worker-task.ps1 -Status      task state, processes, recent log lines, runs
#   deploy\worker\worker-task.ps1 -Restart     stop the worker, start the task again
#   deploy\worker\worker-task.ps1 -Stop        stop the worker and disable the task
#   deploy\worker\worker-task.ps1 -Uninstall   stop everything and remove the task
#
# -Install refuses while a worker started by hand is running unless you pass -StopExisting.
# Stopping mid-run is safe: the next start resumes the stream at the stage that didn't
# finish (run.json in the stream folder).
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Install')][switch]$Install,
    [Parameter(ParameterSetName = 'Install')][switch]$StopExisting,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(ParameterSetName = 'Restart')][switch]$Restart,
    [Parameter(ParameterSetName = 'Stop')][switch]$Stop,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [string]$TaskName = 'clipper-worker'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\common.ps1"
$task = @{
    Name = $TaskName; Command = 'worker'; Extra = 'gpu'
    Supervisor = Join-Path $PSScriptRoot 'worker-supervisor.ps1'; Script = $PSCommandPath
    Description = 'clipper GPU worker'; Logs = 'worker-supervisor.log', 'worker.log'
    Status = { Write-Host ""; & $ClipperExe worker --status }
}
Invoke-ClipperTask $task $PSCmdlet.ParameterSetName -StopExisting:$StopExisting
