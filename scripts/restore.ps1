[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ArchivePath,
    [string]$BackupImage = "alpine:3.21",
    [string]$Docker = "docker"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$archive = Get-Item -LiteralPath $ArchivePath
$archiveDirectory = $archive.Directory.FullName
$archiveName = $archive.Name

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

    # Validate all entries before stopping services or changing either volume.
    $entries = @(Invoke-Docker run --rm `
        -v "${archiveDirectory}:/backup:ro" `
        $BackupImage tar -tzf "/backup/$archiveName")
    if ($entries.Count -eq 0) {
        throw "Backup archive is empty: $($archive.FullName)"
    }
    foreach ($entry in $entries) {
        if ($entry -notmatch '^(server|executor)(/|$)') {
            throw "Unexpected archive entry: $entry"
        }
        if ($entry.StartsWith("/") -or $entry -match '(^|/)\.\.(/|$)') {
            throw "Unsafe archive entry: $entry"
        }
    }

    try {
        Invoke-Docker compose stop server executor | Out-Null
        $restoreScript = 'find /restore/server -mindepth 1 -maxdepth 1 -exec rm -rf {} +; find /restore/executor -mindepth 1 -maxdepth 1 -exec rm -rf {} +; tar -xzf "$1" -C /restore'
        Invoke-Docker run --rm `
            -v "${serverVolume}:/restore/server" `
            -v "${executorVolume}:/restore/executor" `
            -v "${archiveDirectory}:/backup:ro" `
            $BackupImage sh -eu -c $restoreScript sh "/backup/$archiveName"
        Write-Host "Restore completed: $($archive.FullName)"
    }
    finally {
        Invoke-Docker compose up -d server executor | Out-Null
    }
}
finally {
    Pop-Location
}
