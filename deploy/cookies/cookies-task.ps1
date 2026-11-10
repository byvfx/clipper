# Keeps the NAS hub's YouTube cookies fresh: the scheduled task "clipper-cookies" runs
# `clipper cookies export` at logon and every 4 hours, copying the Firefox profile in
# [youtube] cookies_from_browser ("firefox:clipper") into [paths] youtube_cookies on the share
# (secrets/youtube_cookies.txt), which the containers' signed-in yt-dlp retries read. Runs as
# you, only while you're logged on, no admin rights needed.
#
# NOTE: this makes the NAS depend on the laptop. While the laptop is off or logged out the
# file isn't refreshed, and once YouTube rotates the session, pre-show grabs (and story/archive
# signed-in retries) on the NAS fail the bot check until the laptop is back. Fully offline,
# laptop-free running needs another source of cookies (wiki Archives#keeping-the-nas-hubs-cookies-fresh).
#
#   deploy\cookies\cookies-task.ps1 -Install     register (or update) the task and run it once
#   deploy\cookies\cookies-task.ps1 -Status      task state, last result, recent log lines
#   deploy\cookies\cookies-task.ps1 -Run         export now (in this window)
#   deploy\cookies\cookies-task.ps1 -Uninstall   remove the task
#
# Log: logs\cookies.log in the checkout. An export from a signed-out profile is refused and
# leaves the file as it was.
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Install')][switch]$Install,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [Parameter(ParameterSetName = 'Run')][switch]$Run,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [string]$TaskName = 'clipper-cookies',
    [int]$Hours = 4
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\common.ps1"
$log = Join-Path $ClipperLogs 'cookies.log'

function Invoke-Export {
    New-Item -ItemType Directory -Force $ClipperLogs | Out-Null
    Limit-Log $log 1
    Set-Location $ClipperRoot
    # 5.1 turns a native command's redirected stderr into a terminating error under 'Stop'.
    $ErrorActionPreference = 'Continue'
    $out = & $ClipperExe cookies export 2>&1 | ForEach-Object { "$_" } | Out-String  # an ErrorRecord's message only
    $code = $LASTEXITCODE
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') exit $code $($out.Trim())" | Add-Content -Encoding utf8 $log
    Write-Host $out.Trim()
    exit $code
}

switch ($PSCmdlet.ParameterSetName) {
    'Run' { Invoke-Export }
    'Install' {
        if (-not (Test-Path $ClipperExe)) { throw "no $ClipperExe; install clipper first" }
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" `
            -Argument "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Run" `
            -WorkingDirectory $ClipperRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
            -RepetitionInterval (New-TimeSpan -Hours $Hours)).Repetition
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Force `
            -Description "clipper cookies export every $Hours h: YouTube cookies for the NAS hub ($PSCommandPath). Log: $log" | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Registered and started $TaskName (every $Hours h). Log: $log"
        Start-Sleep -Seconds 20
        Get-Content $log -Tail 3 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    }
    'Uninstall' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Removed task $TaskName."
        } else { Write-Host "Task $TaskName was not installed." }
    }
    default {
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $t) { Write-Host "Task $TaskName is not installed (-Install)." }
        else {
            $i = Get-ScheduledTaskInfo -TaskName $TaskName
            Write-Host "Task $TaskName`: $($t.State), last run $($i.LastRunTime) (result $($i.LastTaskResult)), next $($i.NextRunTime)"
        }
        if (Test-Path $log) { Write-Host "`n--- $log (last 5 lines)"; Get-Content $log -Tail 5 | ForEach-Object { Write-Host $_ } }
    }
}
