# jev-desktop installer:
#   irm https://raw.githubusercontent.com/ehtan-smaltai/jev-desktop/main/install.ps1 | iex
# Installs uv if needed, installs the `jevd` command, then asks for your two API keys.
$ErrorActionPreference = 'Stop'
$source = 'https://github.com/ehtan-smaltai/jev-desktop/archive/refs/heads/main.zip'

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host 'Installing uv (Python package manager from astral.sh)...'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $uv = Find-Uv
    if (-not $uv) { throw 'uv installed but was not found. Open a new terminal and run this installer again.' }
}

Write-Host 'Installing jev-desktop...'
& $uv tool install --force --python 3.12 $source
if ($LASTEXITCODE -ne 0) { throw 'jev-desktop install failed.' }
& $uv tool update-shell | Out-Null

$bin = (& $uv tool dir --bin).Trim()
$env:Path = "$bin;$env:Path"
& (Join-Path $bin 'jevd.exe') setup
Write-Host ''
Write-Host 'Done. Open a new terminal if `jevd` is not found, then try:'
Write-Host '  jevd "Open Notepad and type: hello from jev"'
