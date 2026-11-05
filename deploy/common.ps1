# Shared by the scheduled-task scripts of clipper serve (deploy\serve), the Discord bot
# (deploy\discord) and the GPU worker (deploy\worker). Dot-source it, then call
# Invoke-ClipperTask (the *-task.ps1 scripts) or Start-ClipperSupervisor (the
# *-supervisor.ps1 scripts). Windows PowerShell 5.1 runs these.

$ClipperRoot = (Resolve-Path "$PSScriptRoot\..").Path
$ClipperLogs = Join-Path $ClipperRoot 'logs'
$ClipperExe = Join-Path $ClipperRoot '.venv\Scripts\clipper.exe'

# Scheduled tasks don't always see User env vars set after logon; these are copied in when
# the process doesn't have them (the secrets of CLAUDE.md, HF_TOKEN for whisperx, and the
# alert webhook). A new key a stage reads belongs here too.
$ClipperKeys = 'ANTHROPIC_API_KEY', 'ANTHROPIC_WORKSPACE_ID', 'KITSU_TOKEN', 'DISCORD_BOT_TOKEN',
               'CLIPPER_ALERT_WEBHOOK', 'MODAL_TOKEN_ID', 'MODAL_TOKEN_SECRET', 'ELEVENLABS_API_KEY',
               'OPENAI_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY', 'HF_TOKEN', 'BRAVE_API_KEY',
               'ANTHROPIC_ADMIN_KEY'

function Import-ClipperKeys {
    foreach ($name in $ClipperKeys) {
        if (-not [Environment]::GetEnvironmentVariable($name)) {
            $v = [Environment]::GetEnvironmentVariable($name, 'User')
            if ($v) { [Environment]::SetEnvironmentVariable($name, $v) }
        }
    }
}

function Import-RecorderWebhook {
    # With no CLIPPER_ALERT_WEBHOOK, borrow the recorder's (deploy\record\env.local). Without
    # either, alerts go through the bot's token into [record] alert_channel_id. Says which.
    if ($env:CLIPPER_ALERT_WEBHOOK) { return 'CLIPPER_ALERT_WEBHOOK' }
    $recEnv = Join-Path $ClipperRoot 'deploy\record\env.local'
    if (Test-Path $recEnv) {
        $line = Select-String -Path $recEnv -Pattern '^CLIPPER_ALERT_WEBHOOK=(.+)$' | Select-Object -First 1
        if ($line) {
            $env:CLIPPER_ALERT_WEBHOOK = $line.Matches[0].Groups[1].Value.Trim()
            return "the recorder's, from $recEnv"
        }
    }
    return 'none; alerts go to [record] alert_channel_id through the bot token, if set'
}

function Limit-Log([string]$Path, [int]$MaxMB = 5) {
    # One older copy (<log>.1) is kept once a log passes MaxMB.
    if ((Test-Path $Path) -and (Get-Item $Path).Length -gt $MaxMB * 1MB) {
        Move-Item -Force $Path "$Path.1"
    }
}

# ---------- supervisor ----------

function Start-ClipperSupervisor {
    # Runs `clipper <Command>` and restarts it whenever it exits, waiting 10 s and doubling up
    # to MaxDelay while it keeps failing fast. Logs starts and exits to logs\<Command>-supervisor.log;
    # the command's stderr goes to logs\<ConsoleLog>, its stdout to logs\<Command>.stdout.log.
    param([string]$Command, [string]$ConsoleLog, [int]$MaxDelay = 300, [switch]$RecorderWebhook)
    Import-ClipperKeys
    $webhook = if ($RecorderWebhook) { Import-RecorderWebhook } else { '' }
    Set-Location $ClipperRoot
    New-Item -ItemType Directory -Force $ClipperLogs | Out-Null
    $supLog = Join-Path $ClipperLogs "$Command-supervisor.log"
    $console = Join-Path $ClipperLogs $ConsoleLog
    function Say([string]$msg) {
        Limit-Log $supLog
        Add-Content -Path $supLog -Encoding utf8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [pid $PID] $msg"
    }

    $delay = 5
    Say "supervisor started"
    if ($webhook) { Say "alert webhook: $webhook" }
    while ($true) {
        $started = Get-Date
        try {
            $p = Start-Process -FilePath $ClipperExe -ArgumentList $Command -WorkingDirectory $ClipperRoot `
                -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $ClipperLogs "$Command.stdout.log") `
                -RedirectStandardError $console
            $null = $p.Handle  # keeps ExitCode readable after exit
            Say "clipper $Command started (pid $($p.Id))"
            $p.WaitForExit()
            $code = $p.ExitCode
        } catch {
            $code = "?"
            Say "could not start clipper ${Command}: $_"
        }
        $ran = [int]((Get-Date) - $started).TotalSeconds
        if ($ran -ge 600) { $delay = 10 } else { $delay = [Math]::Min($delay * 2, $MaxDelay) }
        $last = Get-Content $console -Tail 1 -ErrorAction SilentlyContinue
        Say "clipper $Command exited with code $code after $ran s; restarting in $delay s. Last line: $last"
        Start-Sleep -Seconds $delay
    }
}

# ---------- scheduled task ----------
# A task is a hashtable: Name (the scheduled task), Command (serve | bot | worker), Supervisor (its
# script), Script (the task script, for the description), Description, Extra (the uv extra
# clipper needs), Logs (names in logs\ to tail), Status (optional scriptblock run after).

function Get-ClipperProcesses($t) {
    # The supervisor, `clipper.exe <Command>` of this checkout, and everything it started.
    $all = @(Get-CimInstance Win32_Process)
    # This script and the processes that started it are never the task's: `clipper.exe
    # services restart bot` also ends in "bot", and stopping it would end the restart before
    # the task starts again (2026-10-30).
    $byId = @{}
    foreach ($p in $all) { $byId[$p.ProcessId] = $p }
    $mine = @{}
    # [uint32]: CIM process ids are UInt32, and a hashtable keeps Int32 5 and UInt32 5 apart.
    for ($id = [uint32]$PID; $id -and $byId[$id] -and -not $mine[$id]; $id = $byId[$id].ParentProcessId) {
        $mine[$id] = $true
    }
    $sup = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$($t.Supervisor)*" })
    $main = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ClipperExe*" -and
                                    $_.CommandLine -match "\b$($t.Command)\s*$" -and
                                    -not $mine[$_.ProcessId] })
    $seen = @{}
    foreach ($p in $main) { $seen[$p.ProcessId] = $true }
    $children = @()
    # (Not @($next.ProcessId): on an empty list that is @($null), whose Count is 1.)
    $parents = @($main | ForEach-Object { $_.ProcessId })
    while ($parents.Count) {
        $next = @($all | Where-Object { $parents -contains $_.ParentProcessId -and -not $seen[$_.ProcessId] })
        foreach ($p in $next) { $seen[$p.ProcessId] = $true }
        $children += $next
        $parents = @($next | ForEach-Object { $_.ProcessId })
    }
    [pscustomobject]@{ Supervisors = $sup; Main = $main; Children = $children }
}

function Stop-ClipperTask($t) {
    if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    }
    $p = Get-ClipperProcesses $t
    foreach ($proc in @($p.Supervisors) + @($p.Main) + @($p.Children)) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($p.Supervisors.Count + $p.Main.Count) {
        Write-Host ("Stopped $($p.Supervisors.Count) supervisor, $($p.Main.Count) $($t.Command) " +
                    "and $($p.Children.Count) child process(es).")
    }
}

function Show-ClipperTask($t) {
    $task = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $t.Name
        Write-Host "Task $($t.Name) : $($task.State); last run $($info.LastRunTime) (result $($info.LastTaskResult))"
    } else {
        Write-Host "Task $($t.Name) is not installed."
    }
    $p = Get-ClipperProcesses $t
    Write-Host "Supervisor pids: $(($p.Supervisors.ProcessId) -join ', ')"
    Write-Host "$((Get-Culture).TextInfo.ToTitleCase($t.Command)) pids: $(($p.Main.ProcessId) -join ', ')"
    foreach ($f in $t.Logs) {
        $path = Join-Path $ClipperLogs $f
        if (Test-Path $path) {
            Write-Host "`n--- $path (last 8 lines)"
            Get-Content $path -Tail 8 | ForEach-Object { Write-Host $_ }
        }
    }
    if ($t.Status) { & $t.Status }
}

function Register-ClipperTask($t) {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    # conhost --headless: no console window (powershell -WindowStyle Hidden still flashes
    # one, or opens Windows Terminal where that's the default terminal).
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" `
        -Argument "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$($t.Supervisor)`"" `
        -WorkingDirectory $ClipperRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    # Re-check every 5 minutes forever; with IgnoreNew this only starts the task when it's not running.
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force `
        -Description "$($t.Description) ($($t.Script)). Logs in $ClipperLogs." | Out-Null
}

function Invoke-ClipperTask($t, [string]$Action, [switch]$StopExisting) {
    # Action: Install | Uninstall | Restart | Stop | Status (the task script's parameter set).
    switch ($Action) {
        'Install' {
            if (-not (Test-Path $ClipperExe)) {
                throw "no $ClipperExe; install clipper with the $($t.Extra) extra first"
            }
            $p = Get-ClipperProcesses $t
            if ($p.Main.Count -and -not $p.Supervisors.Count -and -not $StopExisting) {
                throw ("a clipper $($t.Command) started by hand is running (pids $(($p.Main.ProcessId) -join ', ')). " +
                       "Stop it (Ctrl+C in its terminal), or rerun with -StopExisting.")
            }
            Stop-ClipperTask $t
            Register-ClipperTask $t
            Start-ScheduledTask -TaskName $t.Name
            Write-Host "Registered and started $($t.Name). Logs: $ClipperLogs"
            Start-Sleep -Seconds 15
            Show-ClipperTask $t
        }
        'Uninstall' {
            Stop-ClipperTask $t
            if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
                Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
                Write-Host "Removed task $($t.Name)."
            } else {
                Write-Host "Task $($t.Name) was not installed."
            }
        }
        'Restart' {
            if (-not (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue)) {
                throw "task $($t.Name) is not installed; use -Install"
            }
            Stop-ClipperTask $t
            Enable-ScheduledTask -TaskName $t.Name | Out-Null
            Start-ScheduledTask -TaskName $t.Name
            Write-Host "Restarted $($t.Name)."
            Start-Sleep -Seconds 15
            Show-ClipperTask $t
        }
        'Stop' {
            Stop-ClipperTask $t
            if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
                Disable-ScheduledTask -TaskName $t.Name | Out-Null
            }
            Write-Host ("Stopped, and task $($t.Name) disabled (it would restart the $($t.Command) " +
                        "within 5 minutes). -Restart turns it back on.")
        }
        default { Show-ClipperTask $t }
    }
}
