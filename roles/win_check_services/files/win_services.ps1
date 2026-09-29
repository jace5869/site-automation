# site-automation - Windows check: services (roles/win_check_services, run by playbooks/win_health_check.yml).
# Required services running; automatic services that stopped with an error; services that keep crashing.
# Read-only: it changes nothing on the server.
# ---- common to every site-automation Windows check (keep identical in all of them) -----------
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG (the role's defaults and your inventory).
# The script prints ONE line: ###SITE-JSON### {"findings": [{id, severity, summary, hint}], "facts": {...}}
# Windows PowerShell 5.1 and PowerShell 7; also works in Constrained Language Mode (a few parts
# that need full .NET say so and are skipped there).
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
$findings = @()
$facts = @{}
$fullLanguage = ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage')
function Add-Finding([string] $Id, [string] $Severity, [string] $Summary, [string] $Hint) {
    $script:findings += @(@{ id = $Id; severity = $Severity; summary = $Summary; hint = $Hint })
}
function Get-Setting([string] $Name, $Default) {
    if ($null -ne $cfg -and $null -ne $cfg.$Name) { return $cfg.$Name }
    return $Default
}
function Write-Result {
    '###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ findings = @($script:findings); facts = $script:facts } -Depth 6 -Compress)
}
# ---- end of the common part --------------------------------------------------------------------

$required = @(@(@(Get-Setting 'required' @()) + @(Get-Setting 'required_extra' @())) | Where-Object { $_ } | ForEach-Object { [string]$_ })
$ignore = @(Get-Setting 'ignore' @())
$crashHours = [int](Get-Setting 'crash_hours' 24)
$crashWarn = [int](Get-Setting 'crash_warn' 3)
function Get-Hint([string] $Name) {
    return "Get-Service $Name; Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'} -MaxEvents 20 | Format-List TimeCreated, Message"
}
function Test-Ignored([string] $Name) {
    foreach ($p in $ignore) { if ($Name -like [string]$p) { return $true } }
    return $false
}
$all = @(Get-CimInstance -ClassName Win32_Service)
$byName = @{}
foreach ($s in $all) { $byName[([string]$s.Name).ToLower()] = $s }
$reqLower = @($required | ForEach-Object { $_.ToLower() })
foreach ($r in $required) {
    $s = $byName[$r.ToLower()]
    if ($null -eq $s) {
        Add-Finding "services:$r" 'critical' "required service $r is not installed" "Get-Service $r"
    }
    elseif ($s.State -ne 'Running') {
        Add-Finding "services:$($s.Name)" 'critical' ('required service {0} ({1}) is {2} (start type {3}, exit code {4})' -f $s.Name, $s.DisplayName, $s.State, $s.StartMode, $s.ExitCode) (Get-Hint $s.Name)
    }
}
# An automatic service that stopped with exit code 0, or never started (1077), is normal for
# trigger-start and delayed services. One that stopped with an error code is not.
$stopped = @()
foreach ($s in $all) {
    if ($s.StartMode -ne 'Auto' -or $s.State -eq 'Running') { continue }
    if ($reqLower -contains ([string]$s.Name).ToLower()) { continue }
    if (Test-Ignored ([string]$s.Name)) { continue }
    $stopped += @([string]$s.Name)
    if (@(0, 1077) -contains [int]$s.ExitCode) { continue }
    Add-Finding "services:$($s.Name)" 'warning' ('automatic service {0} ({1}) is {2} (exit code {3})' -f $s.Name, $s.DisplayName, $s.State, $s.ExitCode) (Get-Hint $s.Name)
}
$facts.stopped_automatic = $stopped
# Services that keep crashing: Service Control Manager events 7031 / 7034.
$events = @()
try {
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Service Control Manager'; Id = 7031, 7034; StartTime = (Get-Date).AddHours(-$crashHours) } -ErrorAction Stop)
}
catch { $events = @() }
$counts = @{}
foreach ($e in $events) {
    $n = [string]$e.Properties[0].Value
    if ($counts.ContainsKey($n)) { $counts[$n] = $counts[$n] + 1 } else { $counts[$n] = 1 }
}
foreach ($k in @($counts.Keys)) {
    if ($counts[$k] -ge $crashWarn) {
        Add-Finding "services:crash:$k" 'warning' "service '$k' stopped unexpectedly $($counts[$k]) times in the last $crashHours hours" "Get-WinEvent -FilterHashtable @{LogName='System'; Id=7031,7034} -MaxEvents 20 | Format-List TimeCreated, Message"
    }
}
Write-Result
