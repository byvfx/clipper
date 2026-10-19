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
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$supervisor = Join-Path $PSScriptRoot 'bot-supervisor.ps1'
$logDir = Join-Path $root 'logs'

function Get-BotProcesses {
    # The supervisor, clipper.exe bot and its python children, for this checkout only.
    $all = Get-CimInstance Win32_Process
    $sup = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$supervisor*" })
    $bots = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root\.venv\Scripts\clipper.exe*" -and
                                    $_.CommandLine -match '\bbot\s*$' })
    [pscustomobject]@{ Supervisors = $sup; Bots = $bots }
}

function Stop-Bot {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    $p = Get-BotProcesses
    foreach ($proc in @($p.Supervisors) + @($p.Bots)) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($p.Supervisors.Count + $p.Bots.Count) {
        Write-Host "Stopped $($p.Supervisors.Count) supervisor and $($p.Bots.Count) bot process(es)."
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
    $p = Get-BotProcesses
    Write-Host "Supervisor pids: $(($p.Supervisors.ProcessId) -join ', ')"
    Write-Host "Bot pids: $(($p.Bots.ProcessId) -join ', ')"
    foreach ($f in 'bot-supervisor.log', 'bot.log') {
        $path = Join-Path $logDir $f
        if (Test-Path $path) {
            Write-Host "`n--- $path (last 8 lines)"
            Get-Content $path -Tail 8 | ForEach-Object { Write-Host $_ }
        }
    }
}

if ($Install) {
    if (-not (Test-Path (Join-Path $root '.venv\Scripts\clipper.exe'))) {
        throw "no $root\.venv\Scripts\clipper.exe; install clipper with the discord extra first"
    }
    $p = Get-BotProcesses
    if ($p.Bots.Count -and -not $p.Supervisors.Count) {
        if (-not $StopExisting) {
            throw ("a clipper bot started by hand is running (pids $(($p.Bots.ProcessId) -join ', ')). " +
                   "Stop it (Ctrl+C in its terminal), or rerun with -StopExisting.")
        }
    }
    Stop-Bot

    $user = "$env:USERDOMAIN\$env:USERNAME"
    # conhost --headless: no console window (powershell -WindowStyle Hidden still flashes
    # one, or opens Windows Terminal where that's the default terminal).
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" `
        -Argument "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$supervisor`"" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    # Re-check every 5 minutes forever; with IgnoreNew this only starts the task when it's not running.
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force `
        -Description "clipper Discord review bot (C:\clipper\deploy\discord\bot-task.ps1). Logs in $logDir." | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Registered and started $TaskName. Logs: $logDir"
    Start-Sleep -Seconds 15
    Show-Status
}
elseif ($Uninstall) {
    Stop-Bot
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
    Stop-Bot
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Restarted $TaskName."
    Start-Sleep -Seconds 15
    Show-Status
}
elseif ($Stop) {
    Stop-Bot
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    }
    Write-Host "Stopped, and task $TaskName disabled (it would restart the bot within 5 minutes). -Restart turns it back on."
}
else {
    Show-Status
}
