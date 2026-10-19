# Keeps `clipper bot` running: starts it, and restarts it whenever it exits (crash, killed
# process, lost network), waiting 10 s and doubling up to -MaxDelay while it keeps failing
# fast. Run by the "clipper-bot" scheduled task (bot-task.ps1 -Install); you can also run
# it by hand. The bot itself logs to [paths] bot_log (logs/bot.log); this script adds
# logs/bot-supervisor.log (starts and exits) and logs/bot.console.log (the last run's
# console, which catches errors from before the bot's own logging starts).
# Only one bot runs at a time (it locks streams/.bot.lock): a second copy exits at once
# and is retried here, so this also takes over if a bot started elsewhere stops.
param([int]$MaxDelay = 300)

$root = (Resolve-Path "$PSScriptRoot\..\..").Path
foreach ($name in 'KITSU_TOKEN', 'DISCORD_BOT_TOKEN') {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        $v = [Environment]::GetEnvironmentVariable($name, 'User')
        if ($v) { [Environment]::SetEnvironmentVariable($name, $v) }
    }
}
Set-Location $root
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$supLog = Join-Path $logDir 'bot-supervisor.log'

function Say([string]$msg) {
    Add-Content -Path $supLog -Encoding utf8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [pid $PID] $msg"
}

$exe = Join-Path $root '.venv\Scripts\clipper.exe'
$delay = 5
Say "supervisor started"
while ($true) {
    $started = Get-Date
    try {
        $p = Start-Process -FilePath $exe -ArgumentList 'bot' -WorkingDirectory $root -NoNewWindow -PassThru `
            -RedirectStandardOutput (Join-Path $logDir 'bot.stdout.log') `
            -RedirectStandardError (Join-Path $logDir 'bot.console.log')
        $null = $p.Handle  # keeps ExitCode readable after exit
        Say "clipper bot started (pid $($p.Id))"
        $p.WaitForExit()
        $code = $p.ExitCode
    } catch {
        $code = "?"
        Say "could not start clipper bot: $_"
    }
    $ran = [int]((Get-Date) - $started).TotalSeconds
    if ($ran -ge 600) { $delay = 10 } else { $delay = [Math]::Min($delay * 2, $MaxDelay) }
    $last = Get-Content (Join-Path $logDir 'bot.console.log') -Tail 1 -ErrorAction SilentlyContinue
    Say "clipper bot exited with code $code after $ran s; restarting in $delay s. Last line: $last"
    Start-Sleep -Seconds $delay
}
