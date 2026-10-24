# site-automation - log analysis: the log lines of one Windows server (roles/log_analysis, run by
# playbooks/log_analysis.yml). The event logs' critical, error and warning events of the last hours, and
# the lines of text logs (e.g. a ServiceNow MID Server's agent logs) that match the words to look for.
# Read-only: it changes nothing on the server. The lines are in facts.entries, what was read in
# facts.sources; the AAP side groups them into patterns and has ACT explain them.
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

$now = Get-Date
$since = $now.AddMinutes(-[double](Get-Setting 'since_min' 1440))
$untilMin = [double](Get-Setting 'until_min' 0)
$until = $null
if ($untilMin -gt 0) { $until = $now.AddMinutes(-$untilMin) }
$level = [int](Get-Setting 'level' 3)
if ($level -lt 1) { $level = 1 }
if ($level -gt 3) { $level = 3 }
$maxLines = [int](Get-Setting 'max_lines' 500)
$maxChars = [int](Get-Setting 'max_line_chars' 400)
$match = [string](Get-Setting 'match' 'error|fail|fatal|crit|panic|denied|refused|timed out|timeout|unable to|cannot|exception|warn')
$providers = @(Get-Setting 'providers' @() | ForEach-Object { [string]$_ } | Where-Object { $_ })
$providersOnly = [bool](Get-Setting 'providers_only' $false)
$levelName = @{ 1 = 'critical'; 2 = 'error'; 3 = 'warning' }
$entries = @()
$sources = @()
$seenEvent = @{}

function Get-Clean([string] $Text) {
    $t = ($Text -replace '\s+', ' ').Trim()
    if ($t.Length -gt $maxChars) { $t = $t.Substring(0, $maxChars) }
    return $t
}
function Get-Utc($Time) {
    if ($null -eq $Time) { return '' }
    return $Time.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}
# One query of one event log: its events not taken yet become entries. -> the error ('' = none)
function Read-Events([string] $Log, [hashtable] $Filter) {
    $Filter.LogName = $Log
    $Filter.StartTime = $since
    if ($null -ne $until) { $Filter.EndTime = $until }
    $events = @()
    try { $events = @(Get-WinEvent -FilterHashtable $Filter -MaxEvents $maxLines -ErrorAction Stop) }
    catch {
        if ([string]$_.FullyQualifiedErrorId -notmatch 'NoMatchingEventsFound' -and [string]$_ -notmatch 'No events were found') { return (Get-Clean ([string]$_)) }
    }
    foreach ($e in $events) {
        $key = $Log + '/' + $e.RecordId
        if ($script:seenEvent.ContainsKey($key)) { continue }
        $script:seenEvent[$key] = $true
        $msg = [string]$e.Message
        if (-not $msg) { $msg = '(no message text)' }
        $lv = $levelName[[int]$e.Level]
        if (-not $lv) { $lv = 'warning' }            # information, verbose, audit (asked for by provider or ID)
        $script:entries += @(@{ source = $Log; time = (Get-Utc $e.TimeCreated); level = $lv; app = [string]$e.ProviderName
                                message = (Get-Clean ('Event ' + $e.Id + ': ' + $msg)) })
        $script:counts[$Log] = 1 + [int]$script:counts[$Log]
    }
    return ''
}

# ---- the event logs: by level, the listed providers at every level, the listed IDs whatever their level ----
$counts = @{}
$logs = @(Get-Setting 'logs' @('System', 'Application') | ForEach-Object { [string]$_ } | Where-Object { $_ })
$idsByLog = @{}
foreach ($x in @(Get-Setting 'event_ids' @())) {
    $targets = @([string]$x.log)
    if (-not $x.log) { $targets = @($logs) + @('Security') }
    foreach ($l in $targets) {
        if (-not $idsByLog.ContainsKey($l)) { $idsByLog[$l] = @() }
        $idsByLog[$l] += @([int]$x.id)
    }
}
$allLogs = @($logs)
foreach ($l in $idsByLog.Keys) { if ($allLogs -notcontains $l) { $allLogs += @($l) } }
foreach ($name in $allLogs) {
    $counts[$name] = 0
    $err = ''
    if ($logs -contains $name) {
        if ($providers.Count -gt 0) {
            $err = Read-Events $name @{ ProviderName = $providers; Level = @(1, 2, 3, 4) }
        }
        if (-not ($providersOnly -and $providers.Count -gt 0)) {
            $e2 = Read-Events $name @{ Level = @(1..$level) }
            if ($e2) { $err = $e2 }
        }
    }
    if ($idsByLog.ContainsKey($name)) {
        $e3 = Read-Events $name @{ Id = @($idsByLog[$name] | Select-Object -Unique) }
        if ($e3 -and -not $err) { $err = $e3 }
    }
    $sources += @(@{ name = $name; lines = [int]$counts[$name]; error = $err })
}

# ---- text logs ----
$seen = @{}
foreach ($pattern in @(Get-Setting 'files' @())) {
    $p = [string]$pattern
    $files = @(Get-ChildItem -Path $p -File -ErrorAction SilentlyContinue)
    if ($files.Count -eq 0) {
        $sources += @(@{ name = $p; lines = 0; error = ''; skipped = 'no such file' })
        continue
    }
    foreach ($f in $files) {
        if ($seen.ContainsKey($f.FullName)) { continue }
        $seen[$f.FullName] = $true
        if (@('.gz', '.zip', '.7z', '.bz2', '.xz') -contains $f.Extension.ToLower()) {
            $sources += @(@{ name = $f.FullName; lines = 0; error = ''; skipped = 'compressed' })
            continue
        }
        if ($f.LastWriteTime -lt $since) {
            $sources += @(@{ name = $f.FullName; lines = 0; error = ''; skipped = 'unchanged in the window' })
            continue
        }
        $src = @{ name = $f.FullName; lines = 0; error = '' }
        $hits = @()
        try { $hits = @(Select-String -LiteralPath $f.FullName -Pattern $match -ErrorAction Stop | Select-Object -Last $maxLines) }
        catch { $src.error = Get-Clean ([string]$_) }
        foreach ($h in $hits) {
            $line = [string]$h.Line
            $t = ''
            if ($line -match '^\s*\[?(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)') { $t = $Matches[1] + 'T' + $Matches[2] }   # as written: local time
            $entries += @(@{ source = $f.FullName; time = $t; level = ''; app = ''; message = (Get-Clean $line) })
        }
        $src.lines = $hits.Count
        $sources += @($src)
    }
}

$facts.entries = @($entries)
$facts.sources = @($sources)
$facts.read_at = Get-Utc (Get-Date)
Write-Result
