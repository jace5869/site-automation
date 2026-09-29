# site-automation - Windows check: time (roles/win_check_time, run by playbooks/win_health_check.yml).
# Windows Time running, synchronized with a real source, recently, and the offset from that source.
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

$offWarn = [double](Get-Setting 'offset_warn_sec' 1)
$offCrit = [double](Get-Setting 'offset_crit_sec' 5)
$syncHours = [int](Get-Setting 'max_hours_since_sync' 24)
$hintStatus = 'w32tm /query /status /verbose; w32tm /query /peers; w32tm /query /configuration'
$svc = Get-CimInstance -ClassName Win32_Service -Filter "Name='W32Time'"
if ($null -eq $svc -or $svc.State -ne 'Running') {
    $state = 'not installed'
    if ($null -ne $svc) { $state = [string]$svc.State }
    Add-Finding 'time:service' 'critical' "the Windows Time service (W32Time) is $state, so the clock is not kept in sync" 'Get-Service W32Time; w32tm /query /status'
    Write-Result
    return
}
$status = @(& w32tm.exe /query /status 2>&1 | ForEach-Object { [string]$_ })
$src = ''
$lastSync = ''
foreach ($l in $status) {
    if ($l -match '^\s*Source:\s*(.+?)\s*$') { $src = $Matches[1] }
    elseif ($l -match '^\s*Last Successful Sync Time:\s*(.+?)\s*$') { $lastSync = $Matches[1] }
}
$facts.source = $src
$facts.last_sync = $lastSync
$cs = Get-CimInstance -ClassName Win32_ComputerSystem
if (-not $src) {
    Add-Finding 'time:status' 'warning' ('could not read the time status: ' + (($status -join ' ').Trim())) $hintStatus
}
elseif ($src -match 'Local CMOS Clock|Free-running System Clock') {
    Add-Finding 'time:source' 'critical' "the clock is not synchronized with anything: its time source is '$src'" ($hintStatus + '; w32tm /resync /rediscover')
}
else {
    if ($src -match 'VM IC Time Synchronization Provider' -and $cs.PartOfDomain -and [int]$cs.DomainRole -lt 4) {
        Add-Finding 'time:source' 'warning' 'the clock follows the virtualization host (VM IC provider), not the domain time hierarchy' ($hintStatus + '; disable the VM time sync integration service for domain members')
    }
    if ($lastSync -and $lastSync -notmatch 'unspecified') {
        try {
            $ls = [datetime]::Parse($lastSync)
            $age = [int]((Get-Date) - $ls).TotalHours
            $facts.hours_since_sync = $age
            if ($age -gt $syncHours) {
                Add-Finding 'time:last-sync' 'warning' "the last successful time sync was $age hours ago ($lastSync), from $src" ($hintStatus + '; w32tm /resync')
            }
        }
        catch { $facts.last_sync_unreadable = $true }
    }
    $peer = ($src -split ',')[0].Trim()
    if ($peer -and $peer -notmatch '\s') {
        $sc = @(& w32tm.exe /stripchart /computer:$peer /samples:1 /dataonly 2>&1 | ForEach-Object { [string]$_ })
        $off = $null
        foreach ($l in $sc) { if ($l -match ',\s*([+-]?\d+\.\d+)s\s*$') { $off = [double]$Matches[1] } }
        if ($null -eq $off) { $facts.offset_note = (($sc -join ' ').Trim()) }
        else {
            $facts.offset_sec = $off
            $abs = $off
            if ($abs -lt 0) { $abs = -$abs }
            if ($abs -ge $offCrit) { Add-Finding 'time:offset' 'critical' "the clock is off by $off s from $peer (critical at $offCrit s; Kerberos fails at 300 s)" ($hintStatus + '; w32tm /resync') }
            elseif ($abs -ge $offWarn) { Add-Finding 'time:offset' 'warning' "the clock is off by $off s from $peer (warning at $offWarn s)" ($hintStatus + '; w32tm /resync') }
        }
    }
}
Write-Result
