# site-automation - ServiceNow MID Server host check (roles/servicenow/tasks/mid_host.yml, run by playbooks/servicenow_health.yml).
# Every MID Server service on this server: its state and start mode, its agent folder (from the service's
# command line), config.xml's name and instance URL, the agent log's age and SEVERE lines, the JVM restarts
# the wrapper log shows, the free space of the agent's drive. Read-only: it changes nothing on the server.
# The judging happens on the AAP side (plugins/filter/servicenow_mid_filters.py).
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

$match = [string](Get-Setting 'service_match' 'snc_mid*')
$hours = [double](Get-Setting 'hours' 24)
$tail = [int](Get-Setting 'tail_lines' 20000)
$keep = [int](Get-Setting 'keep_lines' 5)
$now = Get-Date
$since = $now.AddHours(-$hours)

# a line's own time: the agent log writes "10/23/26 07:01:02 (123) ...", the wrapper log "... | 2026/10/23 07:01:02 | ..."
function Get-LineTime([string] $Line) {
    $m = [regex]::Match($Line, '(\d{2})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})')
    if ($m.Success) {
        try { return Get-Date -Year (2000 + [int]$m.Groups[3].Value) -Month ([int]$m.Groups[1].Value) -Day ([int]$m.Groups[2].Value) -Hour ([int]$m.Groups[4].Value) -Minute ([int]$m.Groups[5].Value) -Second ([int]$m.Groups[6].Value) }
        catch { return $null }
    }
    $m = [regex]::Match($Line, '(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})')
    if ($m.Success) {
        try { return Get-Date -Year ([int]$m.Groups[1].Value) -Month ([int]$m.Groups[2].Value) -Day ([int]$m.Groups[3].Value) -Hour ([int]$m.Groups[4].Value) -Minute ([int]$m.Groups[5].Value) -Second ([int]$m.Groups[6].Value) }
        catch { return $null }
    }
    return $null
}
# one decimal, without [math] (Constrained Language Mode refuses its methods)
function Get-Round1([double] $Value) { return ([double][long]($Value * 10)) / 10 }
function Get-Short([string] $Line) {
    $s = ($Line -replace '\s+', ' ').Trim()
    if ($s.Length -gt 300) { $s = $s.Substring(0, 300) + '...' }
    return $s
}
# the lines of a log file in the window that match a pattern: how many, and the last few
function Get-LogLines([string] $Path, [string] $Pattern) {
    $out = @{ path = $Path; exists = $false; age_min = $null; count = 0; last = @() }
    if (-not (Test-Path -LiteralPath $Path)) { return $out }
    $out.exists = $true
    $item = Get-Item -LiteralPath $Path
    $out.age_min = Get-Round1 ($now - $item.LastWriteTime).TotalMinutes
    if ($item.LastWriteTime -lt $since) { return $out }
    $hits = @()
    foreach ($l in @(Get-Content -LiteralPath $Path -Tail $tail)) {
        if ([string]$l -notmatch $Pattern) { continue }
        $t = Get-LineTime ([string]$l)
        if ($null -ne $t -and $t -lt $since) { continue }
        $hits += @(Get-Short ([string]$l))
    }
    $out.count = $hits.Count
    if ($hits.Count -gt 0) { $out.last = @($hits | Select-Object -Last $keep) }
    return $out
}

$services = @()
try {
    $services = @(Get-CimInstance -ClassName Win32_Service | Where-Object { $_.Name -like $match -or $_.DisplayName -like 'ServiceNow MID Server*' })
}
catch { $facts.error = 'could not list the services: ' + [string]$_ }

$mids = @()
foreach ($s in $services) {
    $m = @{ service = [string]$s.Name; display = [string]$s.DisplayName; state = [string]$s.State; start_mode = [string]$s.StartMode
        agent_dir = ''; config = $false; name = ''; url = ''; log = $null; wrapper = $null; disk = $null; error = '' }
    $path = [string]$s.PathName
    # "D:\ServiceNow\mid01\agent\bin\wrapper-windows-x86-64.exe" -s "D:\ServiceNow\mid01\agent\conf\wrapper.conf"
    $d = [regex]::Match($path, '^"?([^"]+?[\\/]agent)[\\/]bin[\\/]')
    if ($d.Success) { $m.agent_dir = $d.Groups[1].Value }
    if ($m.agent_dir) { try {
        $conf = $m.agent_dir + '\config.xml'      # not Join-Path: it fails when the path's drive is gone
        if (Test-Path -LiteralPath $conf) {
            $m.config = $true
            $text = (Get-Content -LiteralPath $conf) -join "`n"
            $n = [regex]::Match($text, '<parameter\s+name="name"\s+value="([^"]*)"')
            if ($n.Success) { $m.name = $n.Groups[1].Value }
            $u = [regex]::Match($text, '<parameter\s+name="url"\s+value="([^"]*)"')
            if ($u.Success) { $m.url = $u.Groups[1].Value }
        }
        $logs = $m.agent_dir + '\logs'
        $m.log = Get-LogLines ($logs + '\agent0.log.0') '\bSEVERE\b'
        $m.wrapper = Get-LogLines ($logs + '\wrapper.log') 'JVM exited unexpectedly|JVM appears hung|JVM did not exit|OutOfMemoryError|JVM exited while loading'
        $drive = ''
        if ($m.agent_dir -match '^[A-Za-z]:') { $drive = $m.agent_dir.Substring(0, 2).ToUpper() }
        if ($drive) { try {
            $ld = Get-CimInstance -ClassName Win32_LogicalDisk -Filter ("DeviceID='" + $drive + "'")
            if ($null -ne $ld -and [double]$ld.Size -gt 0) {
                $m.disk = @{ drive = $drive; free_gb = (Get-Round1 ([double]$ld.FreeSpace / 1GB)); free_pct = (Get-Round1 (100 * [double]$ld.FreeSpace / [double]$ld.Size)) }
            }
        }
        catch { } }
    } catch { $m.error = 'could not read ' + $m.agent_dir + ': ' + [string]$_ } }
    $mids += @($m)
}
$facts.mids = @($mids)
$facts.hostname = [string]$env:COMPUTERNAME
Write-Result
