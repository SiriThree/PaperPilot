param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.runtime\venv312\Scripts\pythonw.exe'
$state = Join-Path $repo '.runtime\local-deployment'
if (!(Test-Path -LiteralPath (Join-Path $state 'deployment.json')) -or !(Test-Path -LiteralPath $python)) {
    throw 'Initialize the owned local deployment and Python 3.12 environment first.'
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$description = "PaperPilot owned loopback deployment: $repo"
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$tasks = @(
    @{ Name='PaperPilot Local Start'; Script='local_deployment.py'; Command='start'; Trigger=(New-ScheduledTaskTrigger -AtLogOn -User $identity) },
    @{ Name='PaperPilot Local Backup'; Script='local_operations.py'; Command='backup'; Trigger=(New-ScheduledTaskTrigger -Daily -At '03:00') },
    @{ Name='PaperPilot Local Monitor'; Script='local_operations.py'; Command='monitor'; Trigger=(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 15)) },
    @{ Name='PaperPilot Local Auth Cleanup'; Script='local_operations.py'; Command='prune-auth'; Trigger=(New-ScheduledTaskTrigger -Daily -At '03:30') }
)
foreach ($item in $tasks) {
    $existing = Get-ScheduledTask -TaskName $item.Name -ErrorAction SilentlyContinue
    if ($existing -and $existing.Description -ne $description) { throw "Unknown task name refused: $($item.Name)" }
}
foreach ($item in $tasks) {
    if ($Remove) {
        Unregister-ScheduledTask -TaskName $item.Name -Confirm:$false -ErrorAction SilentlyContinue
    } else {
        $scriptPath = Join-Path $PSScriptRoot $item.Script
        $action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $scriptPath + '" ' + $item.Command) -WorkingDirectory $repo
        Register-ScheduledTask -TaskName $item.Name -Action $action -Trigger $item.Trigger -Settings $settings -Principal $principal -Description $description -Force | Out-Null
    }
}
Write-Output 'Owned current-user tasks updated. They run only while this Windows user is logged in; Docker Desktop must be available.'
