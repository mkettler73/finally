<#
.SYNOPSIS
    Stop FinAlly (Windows / PowerShell).

.DESCRIPTION
    Stops and removes the container. The data volume is kept by default, so the
    portfolio survives a restart. Idempotent: stopping something that is not
    running is a no-op, not an error.

.PARAMETER Purge
    Also delete the data volume. Irreversible - portfolio and trade history go.

.EXAMPLE
    .\scripts\stop_windows.ps1
#>
[CmdletBinding()]
param(
    [switch]$Purge
)

# See start_windows.ps1 for why this is 'Continue' and not 'Stop'.
$ErrorActionPreference = 'Continue'

$ContainerName = 'finally'
$VolumeName    = 'finally-data'

function Invoke-DockerQuiet {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
    & docker @DockerArgs 2>&1 | Out-Null
    return $LASTEXITCODE
}

if ((Invoke-DockerQuiet info) -ne 0) {
    Write-Host 'Docker daemon is not running - nothing to stop.'
    exit 0
}

if ((Invoke-DockerQuiet container inspect $ContainerName) -eq 0) {
    Write-Host "==> Stopping and removing container $ContainerName"
    Invoke-DockerQuiet rm -f $ContainerName | Out-Null
} else {
    Write-Host "==> No container named $ContainerName - nothing to stop."
}

if ($Purge) {
    Write-Host "==> Removing data volume $VolumeName (portfolio and trade history will be lost)"
    if ((Invoke-DockerQuiet volume rm $VolumeName) -ne 0) {
        Write-Host '    (volume did not exist, or is still in use)'
    }
} else {
    Write-Host "==> Data volume $VolumeName kept. Pass -Purge to delete it."
}

exit 0
