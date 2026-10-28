param(
    [switch]$Demo,
    [ValidateRange(1024, 65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $appEntry = if ($Demo) { 'app.demo:create_demo_app' } else { 'app.main:create_app' }
    & '.\.venv\Scripts\python.exe' -m uvicorn $appEntry --factory --app-dir backend --host 127.0.0.1 --port $Port --no-access-log
    if ($LASTEXITCODE -ne 0) { throw 'Backend failed to start' }
} finally { Pop-Location }
