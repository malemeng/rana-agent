[CmdletBinding()]
param(
    [string]$BackupDirectory,
    [string]$BackupImage = "alpine:3.21",
    [string]$Docker = "docker"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $BackupDirectory) {
    $BackupDirectory = Join-Path $ProjectRoot "backups"
}
$null = New-Item -ItemType Directory -Force -Path $BackupDirectory
$BackupDirectory = (Resolve-Path $BackupDirectory).Path

function Invoke-Docker {
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments

    & $script:Docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-DataVolume {
    param([Parameter(Mandatory = $true)][string]$Service)

    $containerIds = @(Invoke-Docker compose ps -aq $Service)
    if ($containerIds.Count -eq 0 -or -not $containerIds[0]) {
        throw "No container found for service: $Service"
    }
    $format = '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}'
    $volume = (Invoke-Docker inspect --format $format $containerIds[0] | Select-Object -First 1)
    if (-not $volume) {
        throw "No /data volume found for service: $Service"
    }
    return $volume.Trim()
}

Push-Location $ProjectRoot
try {
    Invoke-Docker compose config --quiet | Out-Null
    Invoke-Docker compose create server executor | Out-Null
    $serverVolume = Get-DataVolume -Service "server"
    $executorVolume = Get-DataVolume -Service "executor"
    $timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $archiveName = "rana-agent-$timestamp.tar.gz"

    try {
        # Stop writers so the SQLite database, WAL and artifacts are consistent.
        Invoke-Docker compose stop server executor | Out-Null
        Invoke-Docker run --rm `
            -v "${serverVolume}:/source/server:ro" `
            -v "${executorVolume}:/source/executor:ro" `
            -v "${BackupDirectory}:/backup" `
            $BackupImage tar -czf "/backup/$archiveName" -C /source server executor
        Write-Host "Backup created: $(Join-Path $BackupDirectory $archiveName)"
    }
    finally {
        Invoke-Docker compose up -d server executor | Out-Null
    }
}
finally {
    Pop-Location
}
