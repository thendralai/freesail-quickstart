# Run the complete Freesail stack: Gateway + Agent + UI (Windows PowerShell)
# Usage: .\run-all.ps1
#
# Configure via .env in this directory (copy .env.example to .env).
#
# The gateway, agent, and UI all run as independent processes:
#   - Gateway: MCP Streamable HTTP (port 3000, localhost only) + A2UI HTTP/SSE (port 3001, all interfaces)
#   - Agent:   Connects to gateway MCP
#   - UI:      Vite dev server (port 5173, all interfaces)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Uncomment to see the catalog prompt being sent to agent.
# $env:CATALOG_LOG_DIR = if ($env:CATALOG_LOG_DIR) { $env:CATALOG_LOG_DIR } else { "$ScriptDir\.freesail_logs" }
# New-Item -ItemType Directory -Force -Path $env:CATALOG_LOG_DIR | Out-Null

# Load .env if present
$envFile = Join-Path $ScriptDir ".env"
if (Test-Path $envFile) {
    Write-Host "Loading environment variables from .env..." -ForegroundColor Blue
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line -split "=", 2
            if ($parts.Count -eq 2) {
                $key = $parts[0].Trim()
                $value = $parts[1].Trim()
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
            }
        }
    }
}

# Process tracking for cleanup
$processes = @()

function Cleanup {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor Yellow
    foreach ($p in $script:processes) {
        if ($p -and -not $p.HasExited) {
            try {
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped $($p.ProcessName) (PID $($p.Id))"
            } catch {}
        }
    }
}

# Register cleanup on Ctrl+C
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Cleanup }

# Verify the required API key
$LLM_PROVIDER = if ($env:LLM_PROVIDER) { $env:LLM_PROVIDER } else { "gemini" }
switch ($LLM_PROVIDER) {
    "gemini" {
        if (-not $env:GOOGLE_API_KEY) {
            Write-Host "Error: GOOGLE_API_KEY is required (LLM_PROVIDER=gemini)" -ForegroundColor Red
            Write-Host "Get an API key from: https://aistudio.google.com/app/apikey"
            exit 1
        }
    }
    "openai" {
        if (-not $env:OPENAI_API_KEY) {
            Write-Host "Error: OPENAI_API_KEY is required (LLM_PROVIDER=openai)" -ForegroundColor Red
            Write-Host "Get an API key from: https://platform.openai.com/account/api-keys"
            exit 1
        }
    }
    "claude" {
        if (-not $env:ANTHROPIC_API_KEY) {
            Write-Host "Error: ANTHROPIC_API_KEY is required (LLM_PROVIDER=claude)" -ForegroundColor Red
            Write-Host "Get an API key from: https://console.anthropic.com/"
            exit 1
        }
    }
    default {
        Write-Host "Error: Unknown LLM_PROVIDER '$LLM_PROVIDER'. Must be gemini, openai, or claude." -ForegroundColor Red
        exit 1
    }
}

# Detect LAN IP
$LAN_IP = "<your-ip>"
try {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi", "Ethernet" -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -ne "127.0.0.1" } |
           Select-Object -First 1).IPAddress
    if ($ip) { $LAN_IP = $ip }
} catch {}

Write-Host ""
Write-Host "Starting Freesail stack..." -ForegroundColor Green
Write-Host ""

# Port configuration
$GATEWAY_HTTP_PORT = if ($env:GATEWAY_PORT) { $env:GATEWAY_PORT } else { "3001" }
$GATEWAY_MCP_PORT = if ($env:MCP_PORT) { $env:MCP_PORT } else { "3000" }

# Build gateway args — config file provides defaults; CLI flags override if set in .env
$configFile = Join-Path $ScriptDir "freesail-gateway.config.json"
$gatewayArgs = @("freesail", "run", "gateway", "--config", $configFile)
if ($env:GATEWAY_PORT) { $gatewayArgs += @("--http-port", $GATEWAY_HTTP_PORT) }
if ($env:MCP_PORT)     { $gatewayArgs += @("--mcp-port", $GATEWAY_MCP_PORT) }
if ($env:LOG_LEVEL)    { $gatewayArgs += @("--log-level", $env:LOG_LEVEL) }
if ($env:LOG_FILE)     { $gatewayArgs += @("--log-file", $env:LOG_FILE) }
if ($env:LOG_FILTER) {
    $env:LOG_FILTER -split " " | ForEach-Object {
        if ($_) { $gatewayArgs += @("--log-filter", $_) }
    }
}

# 1. Start Gateway
Write-Host "[Gateway] Starting on HTTP port $GATEWAY_HTTP_PORT, MCP port $GATEWAY_MCP_PORT" -ForegroundColor Blue
$gateway = Start-Process -FilePath "npx" -ArgumentList $gatewayArgs -PassThru -NoNewWindow
$processes += $gateway

Write-Host "[Gateway] Waiting for gateway to start..." -ForegroundColor Blue
Start-Sleep -Seconds 3

# 2. Start Agent
Write-Host "[Agent] Starting" -ForegroundColor Blue
$agentDir = Join-Path $ScriptDir "agent"
$agent = Start-Process -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory $agentDir -PassThru -NoNewWindow
$processes += $agent

Start-Sleep -Seconds 3

# 3. Start UI
$UI_PORT = if ($env:UI_PORT) { $env:UI_PORT } else { "5173" }
Write-Host "[UI] Starting on http://localhost:$UI_PORT" -ForegroundColor Blue
$uiDir = Join-Path $ScriptDir "react-app"
$viteCache = Join-Path $uiDir "node_modules\.vite"
if (Test-Path $viteCache) { Remove-Item -Recurse -Force $viteCache }
$ui = Start-Process -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory $uiDir -PassThru -NoNewWindow
$processes += $ui

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "All services running:" -ForegroundColor Green
Write-Host "  Gateway  (localhost):  http://localhost:$GATEWAY_HTTP_PORT"
Write-Host "  Gateway  (network):    http://${LAN_IP}:$GATEWAY_HTTP_PORT"
Write-Host "  MCP                    http://127.0.0.1:$GATEWAY_MCP_PORT  (agent only, localhost)"
Write-Host "  UI       (localhost):  http://localhost:$UI_PORT"
Write-Host "  UI       (network):    http://${LAN_IP}:$UI_PORT"
Write-Host ""
Write-Host "Press Ctrl+C to stop all services" -ForegroundColor Yellow
Write-Host ""

# Wait for any process to exit
try {
    while ($true) {
        $exited = $processes | Where-Object { $_.HasExited }
        if ($exited) { break }
        Start-Sleep -Seconds 1
    }
} finally {
    Cleanup
}
