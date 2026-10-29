param([switch]$Browser)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPassword = [guid]::NewGuid().ToString('N')
$env:POSTGRES_PASSWORD = $taskPassword
$taskContainer = docker run --rm -d -e POSTGRES_PASSWORD -p '127.0.0.1::5432' pgvector/pgvector:pg16
if ($LASTEXITCODE -ne 0) { throw 'Dedicated test container failed to start' }
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $taskContainer pg_isready -U postgres 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Dedicated test server was not ready' }
    docker exec $taskContainer postgres --version
    $env:PAPERPILOT_TEST_DATA_HOST = '127.0.0.1'
    $env:PAPERPILOT_TEST_DATA_PORT = ((docker port $taskContainer 5432/tcp) -split ':')[-1]
    $env:PAPERPILOT_TEST_DATA_USER = 'postgres'
    $env:PAPERPILOT_TEST_DATA_PASSWORD = $taskPassword
    $env:PAPERPILOT_PHASE5A_ISOLATED = '1'
    Set-Location ([IO.Path]::GetTempPath())
    $python = Join-Path $taskRoot '.venv/Scripts/python.exe'
    & $python -m pytest (Join-Path $taskRoot 'backend/tests/test_authentication_postgres.py') (Join-Path $taskRoot 'backend/tests/test_authentication_http_postgres.py') -q -rs --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Credential database tests failed' }
    if ($Browser) {
        & $python -m pytest ((Join-Path $taskRoot 'backend/tests/phase6_browser_check.py') + '::test_phase65_credentials_browser') -q -rs --tb=short
        if ($LASTEXITCODE -ne 0) { throw 'Credential browser test failed' }
    }
} finally {
    Set-Location $taskRoot
    docker stop $taskContainer 2>$null | Out-Null
}
