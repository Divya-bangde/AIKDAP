<#
.SYNOPSIS
    Start, stop, or restart the local AIKDAP dev stack with one command.

.DESCRIPTION
    start   : checks Docker and Ollama, runs `docker compose up -d`, starts
              the reranker (via scripts/reranker.ps1), and opens the Vite
              frontend on port 5199 in its own window.
    stop    : stops the frontend, the reranker, and `docker compose down`.
    restart : stop, then start.

    Local dev only. Seminar mode stays in scripts/seminar-start.ps1.

.EXAMPLE
    .\scripts\dev.ps1 start
#>
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('start', 'stop', 'restart')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$frontendPort = 5199

function Start-Stack {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker is not running. Start Docker Desktop first.' }

    try { $null = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 3 }
    catch { Write-Warning 'Ollama is not reachable on :11434. LLM calls will fail until it is running.' }

    docker compose -f "$root\docker-compose.yml" up -d
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed.' }

    & "$PSScriptRoot\reranker.ps1" start

    if (Get-NetTCPConnection -LocalPort $frontendPort -State Listen -ErrorAction SilentlyContinue) {
        Write-Output "frontend already running on :$frontendPort"
    } else {
        Start-Process powershell -WorkingDirectory "$root\frontend" -ArgumentList `
            '-Command', "`$Host.UI.RawUI.WindowTitle='AIKDAP frontend'; npm run dev -- --port $frontendPort --strictPort"
        Write-Output "frontend starting on http://localhost:$frontendPort"
    }
}

function Stop-Stack {
    # Killing the Vite process ends npm, which closes the frontend window.
    Get-NetTCPConnection -LocalPort $frontendPort -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { taskkill /PID $_.OwningProcess /T /F *> $null }

    & "$PSScriptRoot\reranker.ps1" stop
    docker compose -f "$root\docker-compose.yml" down
}

switch ($Action) {
    'start'   { Start-Stack }
    'stop'    { Stop-Stack }
    'restart' { Stop-Stack; Start-Stack }
}
