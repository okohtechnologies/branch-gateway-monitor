<#
.SYNOPSIS
  Creates the .env file for Branch Monitor, with generated secrets and checked input.

.DESCRIPTION
  Why a script instead of editing .env by hand: it generates the two secrets for you, it only ever
  writes characters that cannot break the database connection URL, it checks what you type, it writes
  the file the way Docker needs it (UTF-8 WITHOUT a byte-order mark), and it never prints your bot token.
  It refuses to overwrite an existing .env unless you pass -Force.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\init_env.ps1

.PARAMETER Path
  Where to write the file. Defaults to .env in the project folder.
#>
param(
    [string]$Path = (Join-Path (Split-Path $PSScriptRoot -Parent) ".env"),
    [switch]$Force,
    # The parameters below exist so the script can be tested or automated; normally you are prompted.
    [string]$BotToken,
    [string]$ChatId,
    [string]$HeartbeatUrl,
    [string]$Timezone,
    [switch]$Fast,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function New-HexSecret([int]$Bytes) {
    $b = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($b) } finally { $rng.Dispose() }
    -join ($b | ForEach-Object { $_.ToString("x2") })
}

function Read-Plain([System.Security.SecureString]$s) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Ask([string]$Prompt, [string]$Default = "") {
    if ($NonInteractive) { return $Default }
    $suffix = if ($Default) { " [$Default]" } else { "" }
    $answer = Read-Host "$Prompt$suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { $Default } else { $answer.Trim() }
}

if ((Test-Path $Path) -and -not $Force) {
    Write-Host "A file already exists at $Path. Nothing was changed." -ForegroundColor Yellow
    Write-Host "To replace it, run this again with -Force (the old file is kept as .env.bak)."
    exit 1
}

# --- Telegram ------------------------------------------------------------------------------------------
if (-not $BotToken) {
    if ($NonInteractive) { Write-Host "BotToken is required." -ForegroundColor Red; exit 1 }
    Write-Host "Telegram bot token (from @BotFather). Nothing is shown as you type or paste." -ForegroundColor Cyan
    $BotToken = (Read-Plain (Read-Host "Bot token" -AsSecureString)).Trim()
}
if ($BotToken -notmatch '^\d{6,12}:[A-Za-z0-9_-]{30,}$') {
    Write-Host "That does not look like a bot token (expected digits, a colon, then about 35 letters and digits)." -ForegroundColor Red
    exit 1
}
if (-not $ChatId) { $ChatId = Ask "Your Telegram chat ID (digits; message your bot, then see the README)" }
if ($ChatId -notmatch '^-?\d{5,15}$') {
    Write-Host "The chat ID must be a number (a group's ID starts with a minus sign)." -ForegroundColor Red
    exit 1
}

# --- Heartbeat (optional) -------------------------------------------------------------------------------
if (-not $PSBoundParameters.ContainsKey("HeartbeatUrl")) {
    $HeartbeatUrl = Ask "healthchecks.io ping URL (leave empty to skip; you can add it later)"
}
if ($HeartbeatUrl -and $HeartbeatUrl -notmatch '^https?://[^\s]+$') {
    Write-Host "The heartbeat URL must start with https:// and contain no spaces." -ForegroundColor Red
    exit 1
}

# --- Time zone ------------------------------------------------------------------------------------------
if (-not $Timezone) { $Timezone = Ask "Time zone for alert times, as Area/City (for example Africa/Lagos, Europe/London) or UTC" "UTC" }
if ($Timezone -ne "UTC" -and $Timezone -notmatch '^[A-Za-z]+/[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)?$') {
    Write-Host "Use a time zone like Africa/Lagos or Europe/London (or UTC)." -ForegroundColor Red
    exit 1
}

# --- Speed ----------------------------------------------------------------------------------------------
if (-not $Fast -and -not $NonInteractive) {
    $a = Ask "Use fast checking (every 5 seconds, outage alert in about 20 seconds)? Otherwise every 30 seconds, about 90 seconds (y/n)" "y"
    $Fast = $a -match '^(y|yes)$'
}

# --- Build the file -----------------------------------------------------------------------------------
$lines = @(
    "# Created by scripts\init_env.ps1 on $(Get-Date -Format 'yyyy-MM-dd HH:mm'). Never commit or share this file.",
    "# Most monitoring values can be changed later in the dashboard (Settings).",
    "",
    "# Generated: letters and digits only, so they cannot break the database connection URL.",
    "POSTGRES_PASSWORD=$(New-HexSecret 24)",
    "SECRET_KEY=$(New-HexSecret 32)",
    "",
    "TELEGRAM_BOT_TOKEN=$BotToken",
    "TELEGRAM_CHAT_ID=$ChatId",
    "TZ=$Timezone"
)
if ($HeartbeatUrl) { $lines += "HEARTBEAT_URL=$HeartbeatUrl" }
if ($Fast) {
    $lines += @("", "# Fast checking", "PROBE_INTERVAL_S=5", "PROBE_COUNT=3", "PROBE_TIMEOUT_S=1", "CONFIRM=4", "RETENTION_DAYS=14")
}

if (Test-Path $Path) { Copy-Item $Path "$Path.bak" -Force }
# UTF-8 without a byte-order mark: a BOM makes Docker read the first variable name wrongly.
[System.IO.File]::WriteAllText($Path, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Created $Path" -ForegroundColor Green
Write-Host "  generated : POSTGRES_PASSWORD, SECRET_KEY"
Write-Host "  you gave  : TELEGRAM_BOT_TOKEN (hidden), TELEGRAM_CHAT_ID=$ChatId, TZ=$Timezone$(if ($HeartbeatUrl) { ', HEARTBEAT_URL (hidden)' })"
Write-Host "  checking  : $(if ($Fast) { 'every 5 seconds, confirmed after 4 checks' } else { 'every 30 seconds, confirmed after 3 checks (defaults)' })"
Write-Host ""
Write-Host "Keep this file private. It is excluded from git." -ForegroundColor Yellow
