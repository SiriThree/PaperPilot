param([switch]$ScopedOnly, [switch]$BusinessRls, [switch]$BusinessOnly, [string]$ContainerImage = "")
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPassword = [guid]::NewGuid().ToString('N')
$env:POSTGRES_PASSWORD = $taskPassword
# A fresh disposable container is the only database this script can target.
$taskContainer = docker run --rm -d -e POSTGRES_PASSWORD -p '127.0.0.1::5432' -p '127.0.0.1::8000' pgvector/pgvector:pg16
if ($LASTEXITCODE -ne 0) { throw 'Dedicated PostgreSQL test container failed to start' }
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $taskContainer pg_isready -U postgres 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Dedicated PostgreSQL test server was not ready' }
    docker exec $taskContainer postgres --version
    $env:PAPERPILOT_TEST_DATA_HOST = '127.0.0.1'
    $env:PAPERPILOT_TEST_DATA_PORT = ((docker port $taskContainer 5432/tcp) -split ':')[-1]
    $env:PAPERPILOT_TEST_DATA_USER = 'postgres'
    $env:PAPERPILOT_TEST_DATA_PASSWORD = $taskPassword
    $env:PAPERPILOT_PHASE5A_ISOLATED = '1'
    if ($ContainerImage) {
        docker image inspect $ContainerImage *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Explicit local test image is unavailable' }
        $env:PAPERPILOT_ISOLATED_CONTAINER = $taskContainer
        $env:PAPERPILOT_CONTAINER_IMAGE = $ContainerImage
        $env:PAPERPILOT_CONTAINER_API_PORT = ((docker port $taskContainer 8000/tcp) -split ':')[-1]
    }
    # Avoid provider fallback to a workspace-local configuration file.
    Set-Location ([IO.Path]::GetTempPath())
    $python = Join-Path $taskRoot '.venv/Scripts/python.exe'
    $testFiles = @(
        (Join-Path $taskRoot 'backend/tests/test_scoped_audit_postgres.py'),
        (Join-Path $taskRoot 'backend/tests/test_authentication_boundary_postgres.py'),
        ((Join-Path $taskRoot 'backend/tests/test_restricted_executor_postgres.py') + '::test_two_project_readers_and_duplicate_binding')
    )
    if (-not $ScopedOnly) {
        $testFiles += (Join-Path $taskRoot 'backend/tests/test_candidate_project_audit_postgres.py')
        $testFiles += (Join-Path $taskRoot 'backend/tests/test_audit_role_postgres.py')
    }
    if ($BusinessOnly) {
        $testFiles = @((Join-Path $taskRoot 'backend/tests/test_scoped_business_postgres.py'),
            (Join-Path $taskRoot 'backend/tests/test_project_bootstrap_postgres.py'))
    } elseif ($BusinessRls) {
        $testFiles += (Join-Path $taskRoot 'backend/tests/test_scoped_business_postgres.py')
        $testFiles += (Join-Path $taskRoot 'backend/tests/test_project_bootstrap_postgres.py')
    }
    & $python -m pytest @testFiles -q -rs --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Audit boundary tests failed' }
} finally {
    Set-Location $taskRoot
    # Only the container created above is stopped; no other Docker resources are touched.
    docker stop $taskContainer 2>$null | Out-Null
}
