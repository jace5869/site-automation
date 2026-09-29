# site-automation - Windows check: eventlog (roles/win_check_eventlog, run by playbooks/win_health_check.yml).
# Unexpected shutdowns, crashes, disk errors, a cleared Security log, error volume, full logs.
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

$hours = [int](Get-Setting 'hours' 24)
$errorWarn = [int](Get-Setting 'error_warn' 50)
$since = (Get-Date).AddHours(-$hours)
function Get-Events($Filter) {
    try { return @(Get-WinEvent -FilterHashtable $Filter -ErrorAction Stop) } catch { return @() }
}
function Get-Line($Event) {
    $m = [string]$Event.Message
    if ($m.Length -gt 160) { $m = $m.Substring(0, 160) + '...' }
    return ('{0} {1}: {2}' -f $Event.TimeCreated.ToString('yyyy-MM-dd HH:mm'), $Event.ProviderName, ($m -replace '\s+', ' '))
}
$hintSys = "Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddHours(-$hours)} | Select-Object -First 30 | Format-List TimeCreated, ProviderName, Id, Message"
# Unexpected shutdowns (41 Kernel-Power, 6008) and crashes (1001 BugCheck)
$down = @(Get-Events @{ LogName = 'System'; Id = 41, 6008; StartTime = $since })
if ($down.Count -gt 0) { Add-Finding 'eventlog:unexpected-shutdown' 'critical' ("unexpected shutdown or power loss in the last $hours hours: " + (Get-Line $down[0])) $hintSys }
$bsod = @(Get-Events @{ LogName = 'System'; Id = 1001; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; StartTime = $since })
if ($bsod.Count -gt 0) { Add-Finding 'eventlog:bugcheck' 'critical' ("the server crashed (bugcheck / blue screen): " + (Get-Line $bsod[0])) 'Get-ChildItem C:\Windows\Minidump; Get-WinEvent -FilterHashtable @{LogName=''System''; Id=1001} -MaxEvents 5 | Format-List' }
# Disk and file system errors
$disk = @(Get-Events @{ LogName = 'System'; ProviderName = 'disk', 'Ntfs', 'volmgr', 'stornvme', 'storahci', 'iScsiPrt', 'mpio'; Level = 1, 2; StartTime = $since })
if ($disk.Count -gt 0) { Add-Finding 'eventlog:disk-errors' 'critical' ("$($disk.Count) disk / file system error(s) in the last $hours hours; latest: " + (Get-Line $disk[0])) $hintSys }
# The Security log was cleared (1102)
$cleared = @(Get-Events @{ LogName = 'Security'; Id = 1102; StartTime = $since })
if ($cleared.Count -gt 0) { Add-Finding 'eventlog:security-log-cleared' 'critical' ("the Security log was cleared: " + (Get-Line $cleared[0])) "Get-WinEvent -FilterHashtable @{LogName='Security'; Id=1102} -MaxEvents 5 | Format-List" }
# How many errors, and from which sources
$errs = @(@(Get-Events @{ LogName = 'System'; Level = 1, 2; StartTime = $since }) + @(Get-Events @{ LogName = 'Application'; Level = 1, 2; StartTime = $since }))
$facts.errors = $errs.Count
if ($errs.Count -ge $errorWarn) {
    $top = @($errs | Group-Object -Property ProviderName | Sort-Object -Property Count -Descending | Select-Object -First 4 | ForEach-Object { '{0} {1}' -f $_.Name, $_.Count }) -join ', '
    Add-Finding 'eventlog:errors' 'warning' "$($errs.Count) errors in the System and Application logs in the last $hours hours (warning at $errorWarn); most from: $top" $hintSys
}
# Logs that are full and not overwriting: new events are lost
foreach ($name in @('System', 'Application', 'Security')) {
    try {
        $log = Get-WinEvent -ListLog $name -ErrorAction Stop
        if ($log.IsLogFull) { Add-Finding "eventlog:full:$name" 'critical' "the $name event log is full (mode $($log.LogMode)): new events are being lost" "Get-WinEvent -ListLog $name | Format-List *" }
    }
    catch { }
}
Write-Result
