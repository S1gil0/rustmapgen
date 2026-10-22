# RustMapGen installer for Windows: a Python environment of its own, steamcmd, the Rust dedicated
# server (downloaded from Steam, several GB) and the generator's mod. Running it again updates the game.
# Start it with install.cmd (or: powershell -ExecutionPolicy Bypass -File install.ps1).
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Find-Python {
    # The "python" that Windows ships without Python only opens the Store: ask each candidate for its version.
    foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
        $command = Get-Command $candidate[0] -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $arguments = @($candidate | Select-Object -Skip 1) + @('-c', 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')
        try { $answer = & $command.Source @arguments 2>$null } catch { continue }
        if ("$answer".Trim() -eq '1') {
            $found = @($command.Source) + @($candidate | Select-Object -Skip 1)
            return ,$found
        }
    }
    return $null
}

if (-not [Environment]::Is64BitOperatingSystem) { throw 'The Rust dedicated server needs 64-bit Windows.' }
Write-Host 'Looking for Python 3.9 or newer...'
$python = Find-Python
if (-not $python) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'Python 3.9 or newer was not found: installing Python 3.12 with winget...'
        & $winget.Source install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        $python = Find-Python
    }
    if (-not $python) { throw 'Python 3.9 or newer is needed. Install it (tick "Add python.exe to PATH"), then run this again.' }
}
Write-Host "  found: $($python -join ' ')"

$venv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
# A run that stopped half way can leave an environment without pip: it is made again in place.
$working = $false
if (Test-Path -LiteralPath $venv) { & $venv -m pip --version *> $null; $working = ($LASTEXITCODE -eq 0) }
if (-not $working) {
    Write-Host 'Making a Python environment of its own in .venv (about half a minute)...'
    $rest = @($python | Select-Object -Skip 1)
    & $python[0] @rest -m venv --clear .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not make a Python environment.' }
}
Write-Host 'Installing the Python packages it needs (numpy, Pillow, lz4)...'
& $venv -m pip install --quiet --disable-pip-version-check --upgrade pip
& $venv -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Python packages (numpy, Pillow, lz4).' }
& $venv -m rustmapgen install @args
if ($LASTEXITCODE -ne 0) { throw 'The install did not finish: see the lines above.' }
Write-Host ''
Write-Host 'Installed. Start the panel with rustmapgen.cmd (double-click it).'
