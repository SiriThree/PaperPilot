$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & '.\.venv\Scripts\python.exe' -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
    if ($LASTEXITCODE -ne 0) { throw 'Backend failed to start' }
} finally { Pop-Location }
