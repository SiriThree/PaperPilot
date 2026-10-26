param([string]$DockerHostUri = '')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    New-Item -ItemType Directory -Path '.runtime' -Force | Out-Null
    if (-not (Test-Path -LiteralPath '.runtime/day0.env')) {
        $taskPassword = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
        @("PAPERPILOT_DAY0_PASSWORD=$taskPassword", 'PAPERPILOT_DAY0_PORT=54326') |
            Set-Content -LiteralPath '.runtime/day0.env' -Encoding ascii
    }
    $taskDockerArgs = @()
    if ($DockerHostUri) { $taskDockerArgs += @('--host', $DockerHostUri) }
    & docker @taskDockerArgs compose --env-file '.runtime/day0.env' -f compose.day0.yaml up -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed; inspect Docker status.' }
} finally { Pop-Location }
