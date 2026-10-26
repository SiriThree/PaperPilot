$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
try {
    & npm.cmd run dev
    if ($LASTEXITCODE -ne 0) { throw 'Frontend failed to start' }
} finally { Pop-Location }
