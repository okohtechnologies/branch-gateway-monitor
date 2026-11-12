<#
.SYNOPSIS
  Pre-flight check for the machine that will run Branch Monitor (Windows 10/11).

.DESCRIPTION
  Read-only: it changes nothing. It reports PASS / WARN / FAIL for everything the monitor depends on
  and says how to fix each problem. Run it before installing, and again after you finish.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\host_check.ps1 -Gateway 192.168.1.1,10.0.0.1

.PARAMETER Gateway
  One or more branch gateway IPs/hostnames to ping, to prove this machine can reach them.
#>
param(
    [string[]]$Gateway = @()
)

$ErrorActionPreference = "Stop"
$script:rows = @()

# With  powershell -File script.ps1 -Gateway a,b  Windows hands over ONE string "a,b", not two values.
# Accept commas, semicolons and spaces so it works however it is started.
$Gateway = @($Gateway | ForEach-Object { $_ -split '[,; ]+' } | Where-Object { $_ })

function Add-Result([string]$Status, [string]$Name, [string]$Detail, [string]$Fix = "") {
    $script:rows += [pscustomobject]@{ Status = $Status; Name = $Name; Detail = $Detail; Fix = $Fix }
}

function Test-Safe([scriptblock]$Block) {
    try { & $Block } catch { Add-Result "WARN" "Check could not run" $_.Exception.Message }
}

# --- Windows version -----------------------------------------------------------------------
Test-Safe {
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    $label = "$($os.Caption) (build $build)"
    if ($build -ge 22000) { Add-Result "PASS" "Windows version" $label }
    elseif ($build -ge 19045) { Add-Result "PASS" "Windows version" "$label, Windows 10 22H2" }
    else { Add-Result "FAIL" "Windows version" $label "Update to Windows 10 22H2 or Windows 11: Docker Desktop needs a recent build." }
}

# --- Virtualisation ----------------------------------------------------------------------
Test-Safe {
    $cs = Get-CimInstance Win32_ComputerSystem
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    if ($cs.HypervisorPresent -or $cpu.VirtualizationFirmwareEnabled) {
        Add-Result "PASS" "Virtualisation" "Enabled"
    } else {
        Add-Result "FAIL" "Virtualisation" "Off in the BIOS/UEFI" "Restart, open the BIOS/UEFI setup, and enable Intel VT-x / AMD-V (sometimes called SVM Mode)."
    }
}

# --- WSL 2 ---------------------------------------------------------------------------------
Test-Safe {
    $null = & wsl.exe --status 2>&1
    if ($LASTEXITCODE -eq 0) { Add-Result "PASS" "WSL 2" "Installed" }
    else { Add-Result "WARN" "WSL 2" "Not installed yet" "Docker Desktop's installer sets it up. Or run:  wsl --install  (then restart)." }
}

# --- Docker ----------------------------------------------------------------------------------
Test-Safe {
    $cli = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $cli) {
        Add-Result "FAIL" "Docker" "Not installed" "Install Docker Desktop:  winget install -e --id Docker.DockerDesktop   (then restart and start it)."
    } else {
        $null = & docker info 2>&1
        if ($LASTEXITCODE -eq 0) {
            $v = (& docker --version) -join " "
            $c = (& docker compose version) -join " "
            Add-Result "PASS" "Docker" "$v; $c"
        } else {
            Add-Result "FAIL" "Docker" "Installed, but the engine is not running" "Start Docker Desktop and wait until it says 'Engine running'."
        }
    }
}

# --- Sleep: a monitor that sleeps monitors nothing ---------------------------------------------
Test-Safe {
    $out = (& powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE) -join "`n"
    if ($out -match "Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)") {
        $seconds = [Convert]::ToInt32($Matches[1], 16)
        if ($seconds -eq 0) { Add-Result "PASS" "Sleep (plugged in)" "Never" }
        else { Add-Result "FAIL" "Sleep (plugged in)" "Sleeps after $([math]::Round($seconds / 60)) minutes" "Run (as Administrator):  powercfg /change standby-timeout-ac 0" }
    } else {
        Add-Result "WARN" "Sleep (plugged in)" "Could not read the setting" "Check Settings > System > Power: 'Sleep' must be 'Never' when plugged in."
    }
}

# --- Wired or Wi-Fi: Wi-Fi jitter shows up in every graph ---------------------------------------
Test-Safe {
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop | Sort-Object RouteMetric | Select-Object -First 1
    $nic = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex
    $isWifi = ($nic.PhysicalMediaType -match "802\.11") -or ($nic.InterfaceDescription -match "Wi-?Fi|Wireless")
    if ($isWifi) {
        Add-Result "WARN" "Network connection" "Connected over a wireless adapter ($($nic.Name))" "Use wired Ethernet if you can. Wi-Fi jitter adds noise to every branch's latency graph and can cause false alerts."
    } else {
        Add-Result "PASS" "Network connection" "$($nic.Name) (wired), $($nic.LinkSpeed)"
    }
}

# --- Internet: what the monitor, the build and Telegram need -------------------------------------
$needs = @(
    @{ Host = "api.telegram.org"; Why = "Telegram alerts" },
    @{ Host = "registry-1.docker.io"; Why = "downloading Docker images" },
    @{ Host = "pypi.org"; Why = "building the app image" },
    @{ Host = "hc-ping.com"; Why = "the dead-man's-switch heartbeat (optional)"; Optional = $true }
)
foreach ($n in $needs) {
    Test-Safe {
        $r = Test-NetConnection -ComputerName $n.Host -Port 443 -WarningAction SilentlyContinue
        if ($r.TcpTestSucceeded) { Add-Result "PASS" "Internet: $($n.Host)" "Reachable ($($n.Why))" }
        elseif ($n.Optional) { Add-Result "WARN" "Internet: $($n.Host)" "Not reachable ($($n.Why))" "Needed only if you use the heartbeat. Check the firewall/proxy." }
        else { Add-Result "FAIL" "Internet: $($n.Host)" "Not reachable ($($n.Why))" "Check this machine's internet, DNS and any company firewall or proxy." }
    }
}

# --- Branch gateways ---------------------------------------------------------------------------
if ($Gateway.Count -eq 0) {
    Add-Result "WARN" "Branch gateways" "None given" "Re-run with  -Gateway <ip>,<ip>  to prove this machine can reach your branches."
}
foreach ($g in $Gateway) {
    Test-Safe {
        $replies = @(Test-Connection -ComputerName $g -Count 4 -ErrorAction SilentlyContinue)
        if ($replies.Count -gt 0) {
            $avg = [math]::Round(($replies | Measure-Object -Property ResponseTime -Average).Average)
            Add-Result "PASS" "Gateway $g" "$($replies.Count)/4 replies, about $avg ms"
        } else {
            Add-Result "FAIL" "Gateway $g" "No reply to ping" "Connect this machine to the network/VPN that reaches the branch. If the gateway ignores ping, add it later as a TCP branch."
        }
    }
}

# --- Port and disk ---------------------------------------------------------------------------------
Test-Safe {
    $busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($busy) { Add-Result "FAIL" "Port 8000" "Already in use (process $($busy[0].OwningProcess))" "Stop whatever uses port 8000, or the dashboard cannot start." }
    else { Add-Result "PASS" "Port 8000" "Free" }
}
Test-Safe {
    $sys = (Get-CimInstance Win32_OperatingSystem).SystemDrive
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$sys'"
    $free = [math]::Round($disk.FreeSpace / 1GB, 1)
    if ($free -ge 10) { Add-Result "PASS" "Disk space" "$free GB free on $sys" }
    else { Add-Result "FAIL" "Disk space" "Only $free GB free on $sys" "Free at least 10 GB: Docker images and the database live here." }
}

# --- Tools to fetch the code ---------------------------------------------------------------------
foreach ($tool in @(@{ Cmd = "git"; Id = "Git.Git" }, @{ Cmd = "gh"; Id = "GitHub.cli" })) {
    Test-Safe {
        if (Get-Command $tool.Cmd -ErrorAction SilentlyContinue) { Add-Result "PASS" "Tool: $($tool.Cmd)" "Installed" }
        else { Add-Result "WARN" "Tool: $($tool.Cmd)" "Not installed" "Install:  winget install -e --id $($tool.Id)   (then open a NEW PowerShell window)." }
    }
}

# --- Report ---------------------------------------------------------------------------------------------
Write-Host ""
foreach ($r in $script:rows) {
    $colour = switch ($r.Status) { "PASS" { "Green" } "WARN" { "Yellow" } default { "Red" } }
    Write-Host ("[{0}] " -f $r.Status) -ForegroundColor $colour -NoNewline
    Write-Host ("{0}: {1}" -f $r.Name, $r.Detail)
    if ($r.Status -ne "PASS" -and $r.Fix) { Write-Host ("       -> {0}" -f $r.Fix) -ForegroundColor DarkGray }
}
$fail = @($script:rows | Where-Object Status -eq "FAIL").Count
$warn = @($script:rows | Where-Object Status -eq "WARN").Count
Write-Host ""
if ($fail -gt 0) { Write-Host "$fail problem(s) must be fixed before you continue. $warn warning(s)." -ForegroundColor Red }
elseif ($warn -gt 0) { Write-Host "Ready to continue. $warn warning(s) are worth reading above." -ForegroundColor Yellow }
else { Write-Host "All checks passed. This machine is ready." -ForegroundColor Green }
exit ([int]($fail -gt 0))
