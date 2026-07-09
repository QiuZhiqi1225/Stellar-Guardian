param(
    [int]$Port = 8000,
    [string]$BackendHost = "0.0.0.0",
    [string]$ExternalKey = "147852369",
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$envFile = Join-Path $repoRoot ".env"
$outputDir = Join-Path $repoRoot "outputs"
$runTag = Get-Date -Format "yyyyMMdd-HHmmss"
$tunnelLog = Join-Path $outputDir "public-webhook-tunnel-$runTag.log"
$resultFile = Join-Path $outputDir "public-webhook-config.json"

function Ensure-Venv {
    if (Test-Path $python) {
        return
    }
    Push-Location $repoRoot
    try {
        python -m venv .venv
        & $python -m pip install -r requirements.txt
    }
    finally {
        Pop-Location
    }
}

function Read-DotEnvLines {
    if (Test-Path $envFile) {
        return [System.Collections.Generic.List[string]](Get-Content $envFile)
    }
    return [System.Collections.Generic.List[string]]::new()
}

function Get-DotEnvValue {
    param([string]$Name)
    $escapedName = [regex]::Escape($Name)
    foreach ($line in Read-DotEnvLines) {
        if ($line -match "^$escapedName=(.*)$") {
            return $Matches[1]
        }
    }
    return ""
}

function Set-DotEnvValue {
    param(
        [string]$Name,
        [string]$Value
    )
    $escapedName = [regex]::Escape($Name)
    $lines = Read-DotEnvLines
    $updated = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$escapedName=") {
            $lines[$i] = "$Name=$Value"
            $updated = $true
            break
        }
    }
    if (-not $updated) {
        $lines.Add("$Name=$Value")
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($envFile, $lines, $utf8NoBom)
}

function Stop-BackendProcesses {
    $targets = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -match "uvicorn app\.main:app" -and
            $_.CommandLine -match "--port $Port(\s|$)"
        }
    foreach ($target in $targets) {
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-QuickTunnelProcesses {
    $targets = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "cloudflared.exe" -and
            $_.CommandLine -match "tunnel --url http://127\.0\.0\.1:$Port" -and
            $_.CommandLine -notmatch "--token"
        }
    foreach ($target in $targets) {
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Wait-HttpJson {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 40
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $content = curl.exe -sS --max-time 10 $Url
            if (-not $content) {
                throw "Empty response from $Url"
            }
            return $content | ConvertFrom-Json
        }
        catch {
            Start-Sleep -Milliseconds 800
        }
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Url"
}

function Wait-TunnelUrl {
    param([int]$TimeoutSeconds = 40)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-Path $tunnelLog) {
            $content = Get-Content $tunnelLog -Raw
            $match = [regex]::Match($content, "https://[a-z0-9-]+\.trycloudflare\.com")
            if ($match.Success) {
                return $match.Value
            }
        }
        Start-Sleep -Milliseconds 800
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for quick tunnel URL."
}

function Start-Backend {
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Path $outputDir | Out-Null
    }
    $startTag = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $backendOutLog = Join-Path $outputDir "public-webhook-backend-$startTag.out.log"
    $backendErrLog = Join-Path $outputDir "public-webhook-backend-$startTag.err.log"
    $proc = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", $BackendHost, "--port", "$Port") `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $backendOutLog `
        -RedirectStandardError $backendErrLog `
        -PassThru `
        -WindowStyle Hidden
    return $proc
}

function Start-QuickTunnel {
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Path $outputDir | Out-Null
    }
    if (Test-Path $tunnelLog) {
        Remove-Item $tunnelLog -Force
    }
    $proc = Start-Process -FilePath $cloudflared `
        -ArgumentList @("tunnel", "--url", "http://127.0.0.1:$Port", "--protocol", "http2", "--no-autoupdate", "--logfile", $tunnelLog) `
        -WorkingDirectory $repoRoot `
        -PassThru `
        -WindowStyle Hidden
    return $proc
}

Ensure-Venv

if (-not (Test-Path $cloudflared)) {
    throw "cloudflared is not installed at $cloudflared"
}

Stop-QuickTunnelProcesses
Stop-BackendProcesses

$backendProc1 = Start-Backend
$localHealth = Wait-HttpJson -Url "http://127.0.0.1:$Port/health"

$tunnelProc = Start-QuickTunnel
$publicBaseUrl = Wait-TunnelUrl

Set-DotEnvValue -Name "PUBLIC_BASE_URL" -Value $publicBaseUrl

Stop-BackendProcesses
$backendProc2 = Start-Backend
$localHealth = Wait-HttpJson -Url "http://127.0.0.1:$Port/health"
$publicHealth = Wait-HttpJson -Url "$publicBaseUrl/health"
$dashboard = Wait-HttpJson -Url "$publicBaseUrl/api/dashboard"

$ingestKey = Get-DotEnvValue -Name "INGEST_KEY"
if (-not $ingestKey) {
    throw "INGEST_KEY is missing in .env"
}
$webhookUrl = "$publicBaseUrl/webhooks/huawei/$ingestKey"

$smokeResult = $null
if (-not $SkipSmokeTest) {
    $messageId = "codex-public-smoke-" + [guid]::NewGuid().ToString("N")
    $body = @{
        message_id = $messageId
        subject = "Codex public webhook smoke test"
        message = @{
            severity = "critical"
            content = "Public webhook path verified through Cloudflare quick tunnel."
            external_key = $ExternalKey
        }
    } | ConvertTo-Json -Depth 6
    $payloadFile = Join-Path $outputDir "public-webhook-payload-$runTag.json"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($payloadFile, $body, $utf8NoBom)
    $smokeResponse = curl.exe -sS --max-time 15 -X POST -H "Content-Type: application/json" --data-binary "@$payloadFile" $webhookUrl | ConvertFrom-Json
    Start-Sleep -Seconds 2
    $recent = Wait-HttpJson -Url "http://127.0.0.1:$Port/api/local/debug/huawei-recent" -TimeoutSeconds 15
    $recentItem = $recent.items | Where-Object { $_.payload.message_id -eq $messageId } | Select-Object -First 1
    $smokeResult = @{
        request_message_id = $messageId
        webhook_response = $smokeResponse
        audit_found = [bool]$recentItem
        audit_status = if ($recentItem) { $recentItem.status } else { "" }
        audit_event_id = if ($recentItem -and $recentItem.result -and $recentItem.result.event) { $recentItem.result.event.event_id } else { "" }
    }
}

$result = [ordered]@{
    checked_at = (Get-Date).ToString("o")
    public_base_url = $publicBaseUrl
    webhook_url = $webhookUrl
    dashboard_public_base_url = $dashboard.public_base_url
    ingest_key = $ingestKey
    local_health = $localHealth
    public_health = $publicHealth
    backend_pid = $backendProc2.Id
    tunnel_pid = $tunnelProc.Id
    smoke_test = $smokeResult
}

$result | ConvertTo-Json -Depth 8 | Set-Content -Path $resultFile -Encoding UTF8
$result | ConvertTo-Json -Depth 8
