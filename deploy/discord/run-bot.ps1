# Run the Discord review bot on Windows (`clipper bot`). Picks up the keys from your user
# environment when this shell started before they were set (deploy\common.ps1 lists them).
# To keep it running unattended (at logon, restarted when it dies) use bot-task.ps1 -Install;
# while that task's bot runs, this one exits at once (only one bot runs at a time).
. "$PSScriptRoot\..\common.ps1"
Import-ClipperKeys
Set-Location $ClipperRoot
& $ClipperExe bot
