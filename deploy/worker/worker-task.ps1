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
# Stopping mid-run is safe: the next start resumes the stream at the stage that didn't
# finish (run.json in the stream folder).
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Install')][switch]$Install,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(ParameterSetName = 'Restart')][switch]$Restart,
    [Parameter(ParameterSetName = 'Stop')][switch]$Stop,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [string]$TaskName = 'clipper-worker'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$supervisor = Join-Path $PSScriptRoot 'worker-supervisor.ps1'
$logDir = Join-Path $root 'logs'

function Get-WorkerProcesses {
    # The supervisor, clipper.exe worker and the stage processes it started.
    $all = Get-CimInstance Win32_Process
    $sup = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$supervisor*" })
    $workers = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root\.venv\Scripts\clipper.exe*" -and
                                       $_.CommandLine -match '\bworker\s*$' })
    $ids = @($workers.ProcessId)
    $stages = @($all | Where-Object { $ids -contains $_.ParentProcessId })
    [pscustomobject]@{ Supervisors = $sup; Workers = $workers; Stages = $stages }
}

function Stop-Worker {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    $p = Get-WorkerProcesses
    foreach ($proc in @($p.Supervisors) + @($p.Workers) + @($p.Stages)) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($p.Supervisors.Count + $p.Workers.Count) {
        Write-Host "Stopped $($p.Supervisors.Count) supervisor, $($p.Workers.Count) worker and $($p.Stages.Count) stage process(es)."
    }
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "Task $TaskName : $($task.State); last run $($info.LastRunTime) (result $($info.LastTaskResult))"
    } else {
        Write-Host "Task $TaskName is not installed."
    }
    $p = Get-WorkerProcesses
    Write-Host "Supervisor pids: $(($p.Supervisors.ProcessId) -join ', ')"
    Write-Host "Worker pids: $(($p.Workers.ProcessId) -join ', ')"
    foreach ($f in 'worker-supervisor.log', 'worker.log') {
        $path = Join-Path $logDir $f
        if (Test-Path $path) {
            Write-Host "`n--- $path (last 8 lines)"
            Get-Content $path -Tail 8 | ForEach-Object { Write-Host $_ }
        }
    }
    Write-Host ""
    & (Join-Path $root '.venv\Scripts\clipper.exe') worker --status
}

if ($Install) {
    if (-not (Test-Path (Join-Path $root '.venv\Scripts\clipper.exe'))) {
        throw "no $root\.venv\Scripts\clipper.exe; install clipper with the gpu extra first"
    }
    Stop-Worker
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" `
        -Argument "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$supervisor`"" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force `
        -Description "clipper GPU worker (C:\clipper\deploy\worker\worker-task.ps1). Logs in $logDir." | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Registered and started $TaskName. Logs: $logDir"
    Start-Sleep -Seconds 15
    Show-Status
}
elseif ($Uninstall) {
    Stop-Worker
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed task $TaskName."
    } else {
        Write-Host "Task $TaskName was not installed."
    }
}
elseif ($Restart) {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        throw "task $TaskName is not installed; use -Install"
    }
    Stop-Worker
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Restarted $TaskName."
    Start-Sleep -Seconds 15
    Show-Status
}
elseif ($Stop) {
    Stop-Worker
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    }
    Write-Host "Stopped, and task $TaskName disabled. -Restart turns it back on."
}
else {
    Show-Status
}
