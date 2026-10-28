# First run on a new Windows machine (#78). From the clipper folder (a clone or a copy):
#
#   powershell -ExecutionPolicy Bypass -File deploy\bootstrap.ps1
#
# Installs what's missing (uv, which brings Python 3.12; ffmpeg), the Python packages, then
# runs `clipper setup`, which asks the rest. When setup picks roles that need more packages
# (the GPU stack, Modal, discord.py) it exits with code 3; this script syncs them and starts
# setup again, so the whole walkthrough is one command. Run it again any time to carry on.
# Arguments go to clipper setup, e.g. deploy\bootstrap.ps1 --answers answers.toml
#
# On a machine where clipper already runs, don't: it is safe (an existing .venv is never
# re-synced bare), but setup's own sync would replace clipper.exe under a running bot.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
$clipper = Join-Path $root '.venv\Scripts\clipper.exe'

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Test-Command($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Add-UserPath($dir) {
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($user -split ';') -notcontains $dir) {
        [Environment]::SetEnvironmentVariable('Path', ($user.TrimEnd(';') + ";$dir").TrimStart(';'), 'User')
    }
    $env:Path += ";$dir"
}
$winget = Test-Command winget   # not in Windows Sandbox or some LTSC installs

# Windows' 260-character path limit: the anthropic package alone has files ~160 characters
# deep inside .venv, so a long folder breaks `import anthropic` (unless long paths are on).
$longPaths = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -ErrorAction SilentlyContinue).LongPathsEnabled
if ($longPaths -ne 1 -and $root.Length -gt 80) {
    Write-Host "This folder's path is $($root.Length) characters: $root"
    Write-Host 'With Windows long paths off, some Python packages fail to load from a folder that deep.'
    Write-Host 'Move clipper to a short folder (e.g. C:\clipper) and run this again, or turn long paths on'
    Write-Host '(Group Policy, or LongPathsEnabled = 1 under HKLM\SYSTEM\CurrentControlSet\Control\FileSystem; admin).'
    exit 1
}

if (-not (Test-Command uv)) {
    Write-Host '== Installing uv (it brings Python 3.12)'
    if ($winget) {
        winget install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements
        Update-SessionPath
    }
    if (-not (Test-Command uv)) {
        powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
        Add-UserPath "$env:USERPROFILE\.local\bin"
    }
}

if (-not (Test-Command ffmpeg)) {
    Write-Host '== Installing ffmpeg'
    if ($winget) {
        winget install --id Gyan.FFmpeg -e --source winget --accept-package-agreements --accept-source-agreements
        Update-SessionPath
    }
    if (-not (Test-Command ffmpeg)) {
        # No winget: BtbN's GPL build (libass captions, loudnorm, NVENC and QSV), per user.
        $tools = Join-Path $env:LOCALAPPDATA 'clipper-tools'
        New-Item -ItemType Directory -Force $tools | Out-Null
        $zip = Join-Path $tools 'ffmpeg.zip'
        $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the bar
        Invoke-WebRequest 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile $zip
        Expand-Archive $zip -DestinationPath $tools -Force
        Remove-Item $zip
        $bin = (Get-ChildItem $tools -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName
        Add-UserPath $bin
    }
}

if (-not (Test-Path $clipper)) {
    Write-Host '== Python packages (first sync; setup adds the ones its roles need)'
    uv sync
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
}

$env:CLIPPER_BOOTSTRAP = '1'
for ($i = 0; $i -lt 3; $i++) {
    & $clipper setup @args
    if ($LASTEXITCODE -ne 3) { exit $LASTEXITCODE }
    $extras = @((& $clipper setup --sync-args) -split ' ' | Where-Object { $_ })
    Write-Host "== uv sync $($extras -join ' ')"
    uv sync @extras
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
}
Write-Host 'setup still finds packages missing after syncing them; see the lines above'
exit 1
