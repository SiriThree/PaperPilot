param([int]$ApiPort = 61085, [int]$WebPort = 61084)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPassword = [guid]::NewGuid().ToString('N')
$env:POSTGRES_PASSWORD = $taskPassword
$taskContainer = docker run --rm -d -e POSTGRES_PASSWORD -p '127.0.0.1::5432' pgvector/pgvector:pg16
if ($LASTEXITCODE -ne 0) { throw 'Isolated PostgreSQL container failed to start' }
try {
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
        docker exec $taskContainer pg_isready -U postgres 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $taskReady = $true; break }
        Start-Sleep -Milliseconds 1000
    }
    if (-not $taskReady) { throw 'Isolated PostgreSQL did not become ready' }
    $taskPort = ((docker port $taskContainer 5432/tcp) -split ':')[-1]
    $env:PAPERPILOT_TEST_DATA_HOST = '127.0.0.1'
    $env:PAPERPILOT_TEST_DATA_PORT = $taskPort
    $env:PAPERPILOT_TEST_DATA_USER = 'postgres'
    $env:PAPERPILOT_TEST_DATA_PASSWORD = $taskPassword
    $env:PAPERPILOT_PHASE5A_ISOLATED = '1'
    Set-Location ([IO.Path]::GetTempPath())
    & (Join-Path $taskRoot '.venv/Scripts/python.exe') -m pytest (Join-Path $taskRoot 'backend/tests/test_phase5a_persistent.py') (Join-Path $taskRoot 'backend/tests/test_persistent_integration.py') (Join-Path $taskRoot 'backend/tests/test_phase5d_policy.py') (Join-Path $taskRoot 'backend/tests/test_restricted_executor_postgres.py') -q -rs --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Persistent backend tests failed' }
    & (Join-Path $taskRoot '.venv/Scripts/python.exe') -m pytest (Join-Path $taskRoot 'backend/tests/data_contracts') --data-contract-provider app.data.provider:build_harness -q -rs --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL shared contract tests failed' }
    Set-Location $taskRoot
    docker exec $taskContainer createdb -U postgres phase5a_browser
    if ($LASTEXITCODE -ne 0) { throw 'Isolated browser database creation failed' }
    $env:PAPERPILOT_DATA_HOST = '127.0.0.1'
    $env:PAPERPILOT_DATA_PORT = $taskPort
    $env:PAPERPILOT_DATA_USER = 'postgres'
    $env:PAPERPILOT_DATA_PASSWORD = $taskPassword
    $env:PAPERPILOT_DATA_DBNAME = 'phase5a_browser'
    $env:PAPERPILOT_ENV = 'test'
    $env:PAPERPILOT_AUTH_MODE = 'development'
    $env:PAPERPILOT_A_MODE = 'persistent'
    $env:PAPERPILOT_C_MODE = 'mock'
    $env:PAPERPILOT_D_MODE = 'mock'
    $env:PAPERPILOT_PHASE5A_API_PORT = [string]$ApiPort
    $env:PAPERPILOT_PHASE5A_WEB_PORT = [string]$WebPort
    $env:PAPERPILOT_ALLOWED_ORIGINS = "http://127.0.0.1:$WebPort"
    $taskDir = Join-Path ([IO.Path]::GetTempPath()) ('paperpilot-phase5a-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $taskDir | Out-Null
    $env:PAPERPILOT_STORAGE_DIR = Join-Path $taskDir 'uploads'
    $env:PAPERPILOT_PHASE5A_PDF = Join-Path $taskDir 'text.pdf'
    & (Join-Path $taskRoot '.venv/Scripts/python.exe') -c 'import sys; sys.path.insert(0, sys.argv[1]); from ai_contracts.support import pdf_bytes; open(sys.argv[2], chr(119)+chr(98)).write(pdf_bytes([str(2026)]))' (Join-Path $taskRoot 'backend/tests') $env:PAPERPILOT_PHASE5A_PDF
    if ($LASTEXITCODE -ne 0) { throw 'Fixture creation failed' }
    & (Join-Path $taskRoot '.venv/Scripts/python.exe') scripts/manage_postgres.py migrate
    if ($LASTEXITCODE -ne 0) { throw 'Isolated migration failed' }
    & (Join-Path $taskRoot '.venv/Scripts/python.exe') scripts/manage_postgres.py seed-demo
    if ($LASTEXITCODE -ne 0) { throw 'Isolated seed failed' }
    npm.cmd --prefix frontend run test:e2e -- --config=playwright.phase5a.config.ts
    if ($LASTEXITCODE -ne 0) { throw 'Persistent browser test failed' }
} finally {
    Set-Location $taskRoot
    docker stop $taskContainer 2>$null | Out-Null
}
