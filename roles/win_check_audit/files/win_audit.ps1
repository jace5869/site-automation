# site-automation - Windows check: audit (roles/win_check_audit, run by playbooks/win_health_check.yml).
# Advanced audit policy (by GUID), event log sizes, log forwarding agents.
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

# Audit policy (auditpol, by GUID so it works in any language), event log sizes, log forwarding.
$required = @(Get-Setting 'subcategories' @())
$rows = @(& auditpol.exe /get /category:* /r 2>&1 | ForEach-Object { [string]$_ })
if ($LASTEXITCODE -ne 0) {
    Add-Finding 'audit:policy' 'warning' ('could not read the audit policy: ' + (($rows -join ' ').Trim())) 'auditpol /get /category:*'
}
else {
    $have = @{}
    foreach ($r in @($rows | Where-Object { $_ -match '\{[0-9A-Fa-f-]{36}\}' } | ConvertFrom-Csv -Header 'Machine', 'Target', 'Subcategory', 'Guid', 'Inclusion', 'Exclusion')) {
        $have[([string]$r.Guid).Trim('{', '}').ToUpper()] = @{ name = [string]$r.Subcategory; setting = [string]$r.Inclusion }
    }
    $gaps = @()
    foreach ($q in $required) {
        $g = ([string]$q.guid).Trim('{', '}').ToUpper()
        $needS = ([string]$q.need) -match 'Success'
        $needF = ([string]$q.need) -match 'Failure'
        $cur = 'not found'
        if ($have.ContainsKey($g)) { $cur = $have[$g].setting }
        $ok = (-not $needS -or $cur -match 'Success') -and (-not $needF -or $cur -match 'Failure')
        if (-not $ok) { $gaps += @("$($q.name): $cur (needs $($q.need))") }
    }
    $facts.audit_gaps = $gaps
    if ($gaps.Count -gt 0) {
        Add-Finding 'audit:policy' 'warning' ("audit policy is missing $($gaps.Count) setting(s): " + ($gaps -join '; ')) 'auditpol /get /category:*  (set them by GPO: Advanced Audit Policy Configuration)'
    }
}
# Event log sizes (STIG: Security >= 196608 KB, Application and System >= 32768 KB)
$sizes = @{ Security = [int](Get-Setting 'security_log_min_kb' 196608); Application = [int](Get-Setting 'application_log_min_kb' 32768); System = [int](Get-Setting 'system_log_min_kb' 32768) }
foreach ($name in @($sizes.Keys)) {
    try {
        $log = Get-WinEvent -ListLog $name -ErrorAction Stop
        $kb = [int]([double]$log.MaximumSizeInBytes / 1KB)
        if ($kb -lt $sizes[$name]) { Add-Finding "audit:log-size:$name" 'warning' "the $name event log may grow to $kb KB only (minimum $($sizes[$name]) KB)" "Get-WinEvent -ListLog $name | Format-List MaximumSizeInBytes, LogMode" }
    }
    catch { }
}
# Log forwarding agents that must run (e.g. SplunkForwarder, nxlog, winlogbeat)
foreach ($n in @(Get-Setting 'forwarder_services' @())) {
    $s = Get-CimInstance -ClassName Win32_Service -Filter ("Name='{0}'" -f ([string]$n -replace "'", ''))
    if ($null -eq $s -or $s.State -ne 'Running') {
        $state = 'not installed'
        if ($null -ne $s) { $state = [string]$s.State }
        Add-Finding "audit:forwarder:$n" 'critical' "log forwarding service $n is ${state}: events do not reach the SIEM" "Get-Service $n"
    }
}
Write-Result
