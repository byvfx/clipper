# Run the Discord review bot on Windows (`clipper bot`). Picks up KITSU_TOKEN and
# DISCORD_BOT_TOKEN from your user environment when this shell started before they were set.
# To keep it running unattended (at logon, restarted when it dies) use bot-task.ps1 -Install;
# while that task's bot runs, this one exits at once (only one bot runs at a time).
$root = Resolve-Path "$PSScriptRoot\..\.."
foreach ($name in 'KITSU_TOKEN', 'DISCORD_BOT_TOKEN') {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        $v = [Environment]::GetEnvironmentVariable($name, 'User')
        if ($v) { [Environment]::SetEnvironmentVariable($name, $v) }
    }
}
Set-Location $root
& "$root\.venv\Scripts\clipper.exe" bot
