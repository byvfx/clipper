# Keeps `clipper worker` running: starts it, and restarts it whenever it exits, waiting
# 10 s and doubling up to -MaxDelay while it keeps failing fast. Run by the
# "clipper-worker" scheduled task (worker-task.ps1 -Install); you can also run it by hand.
# Logs: logs\worker.log (the worker's console), logs\worker-supervisor.log (restarts).
# Each stage of a run also logs into its stream folder (run_<stage>.log, run.json).
param([int]$MaxDelay = 300)

$root = (Resolve-Path "$PSScriptRoot\..\..").Path
# Scheduled tasks don't always see User env vars set after logon; copy the keys in.
foreach ($name in 'ANTHROPIC_API_KEY', 'ANTHROPIC_WORKSPACE_ID', 'KITSU_TOKEN', 'DISCORD_BOT_TOKEN',
                  'CLIPPER_ALERT_WEBHOOK') {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        $v = [Environment]::GetEnvironmentVariable($name, 'User')
        if ($v) { [Environment]::SetEnvironmentVariable($name, $v) }
    }
}
# Failure alerts use the recorder's Discord webhook unless one is set here.
$recEnv = Join-Path $root 'deploy\record\env.local'
if (-not $env:CLIPPER_ALERT_WEBHOOK -and (Test-Path $recEnv)) {
    $line = Select-String -Path $recEnv -Pattern '^CLIPPER_ALERT_WEBHOOK=(.+)$' | Select-Object -First 1
    if ($line) { $env:CLIPPER_ALERT_WEBHOOK = $line.Matches[0].Groups[1].Value.Trim() }
}
Set-Location $root
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$supLog = Join-Path $logDir 'worker-supervisor.log'

function Say([string]$msg) {
    Add-Content -Path $supLog -Encoding utf8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [pid $PID] $msg"
}

$exe = Join-Path $root '.venv\Scripts\clipper.exe'
$delay = 5
Say "supervisor started"
while ($true) {
    $started = Get-Date
    try {
        $p = Start-Process -FilePath $exe -ArgumentList 'worker' -WorkingDirectory $root -NoNewWindow -PassThru `
            -RedirectStandardOutput (Join-Path $logDir 'worker.stdout.log') `
            -RedirectStandardError (Join-Path $logDir 'worker.log')
        $null = $p.Handle  # keeps ExitCode readable after exit
        Say "clipper worker started (pid $($p.Id))"
        $p.WaitForExit()
        $code = $p.ExitCode
    } catch {
        $code = "?"
        Say "could not start clipper worker: $_"
    }
    $ran = [int]((Get-Date) - $started).TotalSeconds
    if ($ran -ge 600) { $delay = 10 } else { $delay = [Math]::Min($delay * 2, $MaxDelay) }
    $last = Get-Content (Join-Path $logDir 'worker.log') -Tail 1 -ErrorAction SilentlyContinue
    Say "clipper worker exited with code $code after $ran s; restarting in $delay s. Last line: $last"
    Start-Sleep -Seconds $delay
}
