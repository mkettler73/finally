<#
.SYNOPSIS
    Start FinAlly (Windows / PowerShell).

.DESCRIPTION
    Builds the image if it is missing, then runs the container with the data
    volume mounted and the port published. Idempotent: running it again replaces
    the container and leaves the data volume - and the portfolio - untouched.

.PARAMETER Build
    Force a rebuild of the image before starting.

.PARAMETER Open
    Open the browser once the container answers its health check.

.EXAMPLE
    .\scripts\start_windows.ps1 -Build -Open
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Open
)

# Deliberately NOT 'Stop'. Windows PowerShell 5.1 wraps a native executable's
# stderr in an ErrorRecord, so under 'Stop' a perfectly normal `docker container
# inspect` miss ("No such container") aborts the script. Every docker call below
# is checked via $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'

$ImageName     = 'finally:latest'
$ContainerName = 'finally'
$VolumeName    = 'finally-data'
$Port          = if ($env:FINALLY_PORT) { $env:FINALLY_PORT } else { '8000' }

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Runs docker with all output discarded and returns its exit code, for the
# "does this object exist?" probes where a non-zero result is expected and fine.
function Invoke-DockerQuiet {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
    & docker @DockerArgs 2>&1 | Out-Null
    return $LASTEXITCODE
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'ERROR: Docker is not installed or not on PATH. See https://docs.docker.com/get-docker/'
    exit 1
}

if ((Invoke-DockerQuiet info) -ne 0) {
    Write-Host 'ERROR: Docker is installed but the daemon is not running. Start Docker Desktop and retry.'
    exit 1
}

if (-not (Test-Path '.env')) {
    Write-Host 'WARNING: no .env found. Copy .env.example to .env and add OPENROUTER_API_KEY'
    Write-Host '         if you want the AI chat panel to work. Everything else runs without it.'
}

$imageExists = ((Invoke-DockerQuiet image inspect $ImageName) -eq 0)

if ($Build -or (-not $imageExists)) {
    Write-Host "==> Building $ImageName"
    & docker build -t $ImageName .
    if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: image build failed.'; exit 1 }
} else {
    Write-Host "==> Using existing image $ImageName (pass -Build to rebuild)"
}

# Replace any previous container. The volume is never touched, so data persists.
if ((Invoke-DockerQuiet container inspect $ContainerName) -eq 0) {
    Write-Host '==> Removing previous container'
    Invoke-DockerQuiet rm -f $ContainerName | Out-Null
}

Invoke-DockerQuiet volume create $VolumeName | Out-Null

$runArgs = @(
    'run', '-d',
    '--name', $ContainerName,
    '-p', "${Port}:8000",
    '-v', "${VolumeName}:/app/db",
    '--restart', 'unless-stopped'
)
if (Test-Path '.env') { $runArgs += @('--env-file', '.env') }
$runArgs += $ImageName

Write-Host '==> Starting container'
$runOutput = & docker @runArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host 'ERROR: failed to start the container:'
    $runOutput | ForEach-Object { Write-Host "    $_" }
    Write-Host "    If port $Port is already in use, set FINALLY_PORT to another port and retry."
    # A container that fails at the networking stage is still created, stopped.
    # Clear it so the next run does not report "removing previous container".
    Invoke-DockerQuiet rm -f $ContainerName | Out-Null
    exit 1
}

$url = "http://localhost:$Port"
# Probe 127.0.0.1 explicitly: on hosts where "localhost" resolves to ::1 first,
# Docker's published port can refuse the IPv6 connection and the wait never ends.
$probe = "http://127.0.0.1:$Port/api/health"
Write-Host "==> Waiting for $url " -NoNewline

foreach ($attempt in 1..60) {
    try {
        $resp = Invoke-WebRequest -Uri $probe -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            Write-Host ''
            Write-Host "==> FinAlly is up:  $url"
            if ($Open) { Start-Process $url }
            exit 0
        }
    } catch {
        # Not listening yet - keep waiting.
    }

    $state = (& docker container inspect -f '{{.State.Running}}' $ContainerName 2>&1 | Select-Object -First 1)
    if ("$state" -ne 'true') {
        Write-Host ''
        Write-Host '==> Container exited during startup. Logs:'
        & docker logs $ContainerName
        exit 1
    }

    Write-Host '.' -NoNewline
    Start-Sleep -Seconds 2
}

Write-Host ''
Write-Host '==> Timed out waiting for health. Recent logs:'
& docker logs --tail 50 $ContainerName
exit 1
