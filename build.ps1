$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project .venv is missing. Create it and install .[dev] first.'
}

Push-Location $projectRoot
try {
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; refusing to build.' }
    & $python -m PyInstaller --clean --noconfirm BoxyPrintAgent.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    Write-Host 'Built dist\BoxyPrintAgent.exe'
}
finally {
    Pop-Location
}
