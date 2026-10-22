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
# A program (auditpol, net, w32tm, netsh, certreq ...): its output lines and exit code. Windows PowerShell 5.1
# makes each line a program writes to stderr an error when 2>&1 is used, and with 'Stop' above that ends the
# script - here they are lines like the others, and the exit code says whether it worked (-1: it did not start).
function Invoke-Native([string] $Exe, [string[]] $Arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    try { $out = @(& $Exe @Arguments 2>&1 | ForEach-Object { [string]$_ }) }
    catch { $out = @([string]$_) }
    finally { $ErrorActionPreference = $old }
    return New-Object -TypeName PSObject -Property @{ Out = $out; Code = $global:LASTEXITCODE }
}
# ---- end of the common part --------------------------------------------------------------------

# Audit policy (auditpol, by GUID so it works in any language), event log sizes, log forwarding.
$required = @(Get-Setting 'subcategories' @())
# auditpol /backup: each subcategory's setting as a number (1 Success, 2 Failure, 3 both) - the same on a server in
# any language. /get /r says it in words of the server's language: only the fallback.
$words = @{ 0 = 'No Auditing'; 1 = 'Success'; 2 = 'Failure'; 3 = 'Success and Failure' }
$have = @{}
$tmpDir = $env:TEMP
if (-not $tmpDir) { $tmpDir = $env:TMPDIR }
if (-not $tmpDir) { $tmpDir = '/tmp' }
$bk = Join-Path -Path $tmpDir -ChildPath ('site-auditpol-' + $PID + '.csv')
$ap = Invoke-Native 'auditpol.exe' @('/backup', ('/file:' + $bk))
if ($ap.Code -eq 0 -and (Test-Path -LiteralPath $bk)) {
    $lines = @(Get-Content -LiteralPath $bk | Where-Object { $_ -match '\{[0-9A-Fa-f-]{36}\}' })
    foreach ($r in @($lines | ConvertFrom-Csv -Header 'Machine', 'Target', 'Subcategory', 'Guid', 'Inclusion', 'Exclusion', 'Value')) {
        $v = 0
        if ([string]$r.Value -match '^\s*([0-9]+)\s*$') { $v = [int]$Matches[1] }
        $have[([string]$r.Guid).Trim('{', '}').ToUpper()] = @{ name = [string]$r.Subcategory; bits = $v; setting = $words[$v % 4] }
    }
}
if (Test-Path -LiteralPath $bk) { Remove-Item -LiteralPath $bk -Force -ErrorAction SilentlyContinue }
$failed = ''
if ($have.Count -eq 0) {
    $ap = Invoke-Native 'auditpol.exe' @('/get', '/category:*', '/r')
    $rows = @($ap.Out)
    if ($ap.Code -ne 0) { $failed = (($rows -join ' ').Trim()) }
    else {
        foreach ($r in @($rows | Where-Object { $_ -match '\{[0-9A-Fa-f-]{36}\}' } | ConvertFrom-Csv -Header 'Machine', 'Target', 'Subcategory', 'Guid', 'Inclusion', 'Exclusion')) {
            $set = [string]$r.Inclusion
            $bits = 0
            if ($set -match 'Success') { $bits += 1 }
            if ($set -match 'Failure') { $bits += 2 }
            $have[([string]$r.Guid).Trim('{', '}').ToUpper()] = @{ name = [string]$r.Subcategory; bits = $bits; setting = $set }
        }
    }
}
if ($failed) {
    Add-Finding 'audit:policy' 'warning' ('could not read the audit policy: ' + $failed) 'auditpol /get /category:*'
}
else {
    $gaps = @()
    foreach ($q in $required) {
        $g = ([string]$q.guid).Trim('{', '}').ToUpper()
        $needS = ([string]$q.need) -match 'Success'
        $needF = ([string]$q.need) -match 'Failure'
        $cur = 'not found'
        $bits = 0
        if ($have.ContainsKey($g)) { $cur = $have[$g].setting; $bits = [int]$have[$g].bits }
        $ok = (-not $needS -or ($bits -band 1)) -and (-not $needF -or ($bits -band 2))
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
