# site-automation - Windows check: stig (roles/win_check_stig, run by playbooks/win_stig_drift_report.yml).
# The STIG scan with Evaluate-STIG: runs it on the server - as SYSTEM, in a one-time scheduled task (deleted after) -
# or reads the checklists it already wrote, and keeps each checklist's result on the server, one folder per checklist
# (a STIG, or a STIG of one web site or database instance) in the folder dir: its summary (JSON) and the checklist.
# Prints every checklist's rules with their result, the result of the scan before and when the rule last passed; the
# comparison with STIG Manager and the report are done on the AAP side (roles/check_stig, plugins/filter/stig_filters.py).
# Changes nothing on the server but its own folder and the scheduled task.
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

# Settings (roles/win_check_stig/defaults/main.yml):
#   evaluate   Evaluate-STIG's folder or Evaluate-STIG.ps1; '' = found: a scheduled task that runs it, else a folder named
#              Evaluate-STIG* on the local drives (search_depth levels down)
#   read       '' = run it; 'auto' = read the checklists it already wrote (the folder its scheduled task writes to, else
#              its default, C:\Users\Public\Documents\STIG_Compliance); or that folder
#   select, exclude, args, output, rejected, run_as (task or direct), powershell, timeout, dir, keep, save,
#   details_max, search (the folders to search; [] = the local drives), search_depth, host ('' = this computer's name)
$onWindows = ($env:OS -eq 'Windows_NT')
$me = [string](Get-Setting 'host' '')
if (-not $me) { $me = [string]$env:COMPUTERNAME }
$dir = [string](Get-Setting 'dir' '')
if (-not $dir) { $dir = Join-Path ([string]$env:ProgramData) 'site-automation\stig' }
$keep = [int](Get-Setting 'keep' 10)
if ($keep -lt 1) { $keep = 1 }
$save = [bool](Get-Setting 'save' $true)
$timeout = [int](Get-Setting 'timeout' 3600)
$runAs = [string](Get-Setting 'run_as' 'task')
$readFrom = [string](Get-Setting 'read' '')
$output = [string](Get-Setting 'output' 'CKLB')
$takeRejected = [bool](Get-Setting 'rejected' $true)
$detailsMax = [int](Get-Setting 'details_max' 4000)
$select = @(Get-Setting 'select' @() | Where-Object { $_ })
$exclude = @(Get-Setting 'exclude' @() | Where-Object { $_ })
$extra = @(Get-Setting 'args' @() | Where-Object { $null -ne $_ })
$searchDepth = [int](Get-Setting 'search_depth' 4)
$searchIn = @(Get-Setting 'search' @() | Where-Object { $_ })
$shellSet = [string](Get-Setting 'powershell' '')
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd\THHmmss\Z')
$nowText = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd\THH:mm:ss\Z')
$facts.scanned = $false
$facts.why = ''
$facts.evaluate = ''
$facts.found_by = ''
$facts.read = ''
$facts.scans = @()
$facts.os = ''
if ($onWindows) {
    try { $facts.os = [string](Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop).Caption } catch { }
}
$CKL_RESULT = @{ notafinding = 'pass'; open = 'fail'; notapplicable = 'notapplicable'; notreviewed = 'notchecked' }
$CKL_SEV = @{ high = 'high'; medium = 'medium'; low = 'low'; cati = 'high'; catii = 'medium'; catiii = 'low' }

function Get-Plain($Text) { return ([string]$Text).ToLower() -replace '[^a-z]', '' }
function Get-Quoted($Text) { return "'" + ([string]$Text).Replace("'", "''") + "'" }
# a time read back from JSON: PowerShell 7 makes ISO times DateTime objects
function Get-TimeText($Value) {
    if ($Value -is [datetime]) { return $Value.ToUniversalTime().ToString('yyyy-MM-dd\THH:mm:ss\Z') }
    return [string]$Value
}
# an XML element's text ('' when it is empty or not there)
function Get-XText($Node) {
    if ($null -eq $Node) { return '' }
    if ($Node -is [string]) { return $Node }
    return [string]$Node.InnerText
}
function Get-Short([string] $Name) { return ($Name.Split('.')[0]).ToLower() }

# Evaluate-STIG.ps1 in a folder (or its Src\Evaluate-STIG, or an Evaluate-STIG folder in it), or the script itself
function Find-EvaluateIn([string] $Path) {
    if (-not $Path) { return '' }
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return $Path }
    foreach ($sub in @('', 'Evaluate-STIG', (Join-Path 'Src' 'Evaluate-STIG'))) {
        $p = Join-Path $Path 'Evaluate-STIG.ps1'
        if ($sub) { $p = Join-Path (Join-Path $Path $sub) 'Evaluate-STIG.ps1' }
        if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
    }
    return ''
}

# The scheduled tasks that run Evaluate-STIG.ps1: the script and the folder it writes to (-OutputPath)
function Get-EvaluateTasks {
    $out = @()
    if (-not (Get-Command -Name Get-ScheduledTask -ErrorAction SilentlyContinue)) { return $out }
    foreach ($t in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        foreach ($a in @($t.Actions)) {
            $text = ([string]$a.Execute) + ' ' + ([string]$a.Arguments)
            if ($text -notmatch '(?i)evaluate-stig\.ps1') { continue }
            $script = ''
            $m = [regex]::Match($text, '(?i)((?:[a-z]:\\|\\\\)[^"'']*?evaluate-stig\.ps1)')
            if ($m.Success) { $script = $m.Groups[1].Value }
            $outPath = ''
            $o = [regex]::Match($text, '(?i)-OutputPath\s+(?:"([^"]+)"|''([^'']+)''|(\S+))')
            if ($o.Success) { $outPath = ($o.Groups[1].Value + $o.Groups[2].Value + $o.Groups[3].Value) }
            $out += @(@{ task = ([string]$t.TaskPath + [string]$t.TaskName); script = $script; output = $outPath })
        }
    }
    return $out
}

# A folder named Evaluate-STIG* with the script, at most $Depth levels down the local drives (or the folders given)
function Search-Evaluate([int] $Depth) {
    $skip = @('Windows', '$Recycle.Bin', 'System Volume Information', 'Recovery', 'PerfLogs', 'AppData', 'WinSxS', 'Config.Msi', '.git', 'node_modules')
    $level = @($searchIn)
    if (-not $level.Count -and $onWindows) {
        $level = @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { [string]$_.Root -match '^[A-Za-z]:\\$' } | ForEach-Object { [string]$_.Root })
    }
    $found = @()
    $seen = 0
    for ($d = 0; $d -lt $Depth -and $level.Count -and $seen -lt 50000; $d++) {
        $next = @()
        foreach ($r in $level) {
            foreach ($c in @(Get-ChildItem -LiteralPath $r -Directory -Force -ErrorAction SilentlyContinue)) {
                $seen++
                if ($skip -contains $c.Name -or [string]$c.Attributes -match 'ReparsePoint') { continue }
                if ($c.Name -like 'Evaluate-STIG*') {
                    $s = Find-EvaluateIn $c.FullName
                    if ($s) { $found += @($s); continue }
                }
                $next += @($c.FullName)
            }
        }
        $level = $next
    }
    return $found
}

# The checklists (.cklb, .ckl) under a folder, newest first: this server's own folder when there is one per server
# (Evaluate-STIG writes <OutputPath>\<computer>\Checklist), never its Previous folders
function Get-Checklists([string] $Root) {
    $base = $Root
    if ($me) {
        $mine = Join-Path $Root $me
        if (Test-Path -LiteralPath $mine -PathType Container) { $base = $mine }
    }
    return @(Get-ChildItem -LiteralPath $base -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { @('.cklb', '.ckl') -contains ([string]$_.Extension).ToLower() -and $_.FullName -notmatch '[\\/]Previous[\\/]' } |
        Sort-Object -Property LastWriteTimeUtc -Descending)
}

function New-Rule($Rule, $V, $StigId, $Severity, $Title, $Status, $Details) {
    $res = $CKL_RESULT[(Get-Plain $Status)]
    if (-not $res) { $res = 'notchecked' }
    $sev = $CKL_SEV[(Get-Plain $Severity)]
    if (-not $sev) { $sev = '' }
    return @{ rule = [string]$Rule; v = [string]$V; stigid = [string]$StigId; severity = $sev; title = [string]$Title; result = $res; details = [string]$Details }
}

# A checklist -> its STIGs: @{stig_id, title, version, release, host, site, instance, rules}
function Read-Checklist([string] $Path) {
    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $out = @()
    if ($text.TrimStart().StartsWith('{')) {
        $d = ConvertFrom-Json -InputObject $text
        $hostName = ''; $site = ''; $inst = ''
        $t = $d.target_data
        if ($null -ne $t) {
            $hostName = [string]$t.host_name
            if ([string]$t.is_web_database -eq 'True' -or [string]$t.is_web_database -eq 'true') { $site = [string]$t.web_db_site; $inst = [string]$t.web_db_instance }
        }
        foreach ($s in @($d.stigs)) {
            if ($null -eq $s) { continue }
            $rules = @()
            foreach ($r in @($s.rules)) {
                if ($null -eq $r) { continue }
                $rules += @(New-Rule $r.rule_id $r.group_id $r.rule_version $r.severity $r.rule_title $r.status $r.finding_details)
            }
            $title = [string]$s.display_name
            if (-not $title) { $title = [string]$s.stig_name }
            $out += @(@{ stig_id = [string]$s.stig_id; title = $title; version = [string]$s.version; release = [string]$s.release_info
                    host = $hostName; site = $site; instance = $inst; rules = $rules })
        }
        return $out
    }
    $x = [xml]$text
    $hostName = ''; $site = ''; $inst = ''
    $asset = $x.CHECKLIST.ASSET
    if ($null -ne $asset) {
        $hostName = Get-XText $asset.HOST_NAME
        if ((Get-XText $asset.WEB_OR_DATABASE) -eq 'true') { $site = Get-XText $asset.WEB_DB_SITE; $inst = Get-XText $asset.WEB_DB_INSTANCE }
    }
    foreach ($ist in @($x.CHECKLIST.STIGS.iSTIG)) {
        if ($null -eq $ist) { continue }
        $info = @{}
        foreach ($sd in @($ist.STIG_INFO.SI_DATA)) { if ($null -ne $sd) { $info[(Get-XText $sd.SID_NAME)] = Get-XText $sd.SID_DATA } }
        $rules = @()
        foreach ($v in @($ist.VULN)) {
            if ($null -eq $v) { continue }
            $a = @{}
            foreach ($sd in @($v.STIG_DATA)) {
                $k = Get-XText $sd.VULN_ATTRIBUTE
                if (-not $a.ContainsKey($k)) { $a[$k] = Get-XText $sd.ATTRIBUTE_DATA }
            }
            $rules += @(New-Rule $a['Rule_ID'] $a['Vuln_Num'] $a['Rule_Ver'] $a['Severity'] $a['Rule_Title'] (Get-XText $v.STATUS) (Get-XText $v.FINDING_DETAILS))
        }
        $out += @(@{ stig_id = [string]$info['stigid']; title = [string]$info['title']; version = [string]$info['version']; release = [string]$info['releaseinfo']
                host = $hostName; site = $site; instance = $inst; rules = $rules })
    }
    return $out
}

# version 2, release_info 'Release: 4 Benchmark Date: ...' -> V2R4
function Get-StigVersion($S) {
    $v = ([string]$S.version) -replace '\D', ''
    $m = [regex]::Match([string]$S.release, 'Release:\s*(\d+)')
    if ($v -and $m.Success) { return 'V' + $v + 'R' + $m.Groups[1].Value }
    $m = [regex]::Match(([string]$S.title) + ' ' + ([string]$S.release), '\bV(\d+)R(\d+)\b')
    if ($m.Success) { return 'V' + $m.Groups[1].Value + 'R' + $m.Groups[2].Value }
    return ''
}

function Get-Label($S) {
    $t = ([string]$S.title) -replace '\s*Security Technical Implementation Guide\s*$', '' -replace '\s*STIG\s*$', ''
    if (-not $t) { $t = [string]$S.stig_id }
    if ($S.site -or $S.instance) { $t = $t + ' - ' + (@($S.site, $S.instance) | Where-Object { $_ }) -join ' / ' }
    return $t
}

# What Evaluate-STIG printed: the checklists its own schema check moved away, its checks that failed, its log
function Get-EvaluateSaid([string] $Text) {
    $where = ''
    $m = [regex]::Match($Text, '(?im)Moving to ((?:[a-z]:\\|\\\\|/)[^''\r\n]*?)\.?\s*$')
    if ($m.Success) { $where = $m.Groups[1].Value.TrimEnd('\', '/') }
    $rejected = @()
    if ($where) {
        foreach ($b in [regex]::Matches($Text, '(?i)''([^''\r\n]+\.cklb?)''\s*:\s*failed schema validation')) {
            $rejected += @(Join-Path $where (Split-Path -Leaf $b.Groups[1].Value))
        }
    }
    $failed = @([regex]::Matches($Text, '(?i)\(Get-V(\d+)\)\s*:\s*Failed') | ForEach-Object { 'V-' + $_.Groups[1].Value } | Sort-Object -Unique)
    $log = ''
    if ($where) {
        $l = Join-Path (Split-Path -Parent $where) 'Evaluate-STIG.log'
        if (Test-Path -LiteralPath $l -PathType Leaf) { $log = $l }
    }
    $lines = @(($Text -split "`r?`n") | Where-Object { $_ -match '(?i)error|failed|total checklists' } | ForEach-Object { $_.Trim() })
    return @{ rejected = $rejected; failed = $failed; log = $log; lines = $lines }
}

# Run Evaluate-STIG into $OutDir: a small script in $Work runs it with every line it prints going to a file, as
# SYSTEM in a one-time scheduled task (run_as: task - outside the WinRM session and its memory quota), or as a
# process of this session (run_as: direct) -> @{ code; said; timed_out; how }
function Invoke-Evaluate([string] $Script, [string] $OutDir, [string] $Work) {
    $log = Join-Path $Work 'evaluate.txt'
    $codeFile = Join-Path $Work 'exit.txt'
    $wrapper = Join-Path $Work 'run.ps1'
    $call = @('&', (Get-Quoted $Script), '-Output', (Get-Quoted $output), '-OutputPath', (Get-Quoted $OutDir))
    if ($select.Count) { $call += @('-SelectSTIG', ((@($select | ForEach-Object { Get-Quoted $_ })) -join ',')) }
    if ($exclude.Count) { $call += @('-ExcludeSTIG', ((@($exclude | ForEach-Object { Get-Quoted $_ })) -join ',')) }
    foreach ($a in $extra) {
        if ([string]$a -match '^-[A-Za-z]\w*$') { $call += @([string]$a) } else { $call += @(Get-Quoted $a) }
    }
    $body = @(
        '$ErrorActionPreference = ''Continue''',
        '$global:LASTEXITCODE = 0',
        ('Set-Location -LiteralPath ' + (Get-Quoted (Split-Path -Parent $Script))),
        ('try { ' + ($call -join ' ') + ' *> ' + (Get-Quoted $log) + '; $c = $LASTEXITCODE }'),
        ('catch { Add-Content -LiteralPath ' + (Get-Quoted $log) + ' -Value ([string]$_); $c = 1 }'),
        'if ($null -eq $c) { $c = 0 }',
        ('Set-Content -LiteralPath ' + (Get-Quoted $codeFile) + ' -Value $c')
    )
    Set-Content -LiteralPath $wrapper -Value $body -Encoding UTF8
    $shell = $shellSet
    if (-not $shell -and $onWindows) { $shell = Join-Path ([string]$env:SystemRoot) 'System32\WindowsPowerShell\v1.0\powershell.exe' }
    if (-not $shell) { $shell = (Get-Process -Id $PID).Path }
    $r = @{ code = -1; said = ''; timed_out = $false; how = '' }
    $t0 = Get-Date
    if ($runAs -eq 'task' -and $onWindows -and (Get-Command -Name Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        $r.how = 'a scheduled task as SYSTEM'
        $name = 'site-automation STIG scan ' + $stamp
        $action = New-ScheduledTaskAction -Execute $shell -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $wrapper + '"')
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds ($timeout + 120)) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $name -TaskPath '\' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        try {
            Start-ScheduledTask -TaskName $name -TaskPath '\'
            while (-not (Test-Path -LiteralPath $codeFile)) {
                Start-Sleep -Seconds 5
                $secs = ((Get-Date) - $t0).TotalSeconds
                if ($secs -gt $timeout) { $r.timed_out = $true; break }
                $state = [string](Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue).State
                if ($secs -gt 30 -and $state -ne 'Running' -and $state -ne 'Queued') { break }
            }
            if (Test-Path -LiteralPath $codeFile) { $r.code = [int]([string](Get-Content -LiteralPath $codeFile -Raw)).Trim() }
            else {
                $info = Get-ScheduledTaskInfo -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue
                if ($null -ne $info) { $r.code = [int64]$info.LastTaskResult }
                Stop-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue
            }
        }
        finally { Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue }
    }
    else {
        $r.how = 'this session'
        $p = Start-Process -FilePath $shell -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $wrapper + '"')) -NoNewWindow -PassThru
        Wait-Process -Id $p.Id -Timeout $timeout -ErrorAction SilentlyContinue
        if (Get-Process -Id $p.Id -ErrorAction SilentlyContinue) {
            $r.timed_out = $true
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $codeFile) { $r.code = [int]([string](Get-Content -LiteralPath $codeFile -Raw)).Trim() }
    }
    if (Test-Path -LiteralPath $log) { $r.said = [string](Get-Content -LiteralPath $log -Raw) }
    return $r
}

# A folder only SYSTEM and the Administrators can open
function New-PrivateFolder([string] $Path) {
    if (Test-Path -LiteralPath $Path -PathType Container) { return }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    if ($onWindows) { Invoke-Native 'icacls.exe' @($Path, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F') | Out-Null }
}

# One checklist (a STIG, or a STIG of one web site or database instance): its rules with the scan before and when
# each last passed, and - save - its summary and the checklist kept in its own folder, the last $keep runs
function Get-Scan($S, $File, [bool] $ReadMode) {
    $key = ((@($S.stig_id, $S.site, $S.instance) | Where-Object { $_ }) -join '_') -replace '[^A-Za-z0-9._-]+', '_'
    if (-not $key) { $key = 'checklist' }
    $hd = Join-Path $dir $key
    $prev = $null
    $sums = @(Get-ChildItem -LiteralPath $hd -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d{8}T\d{6}Z_summary\.json$' } | Sort-Object -Property Name)
    for ($i = $sums.Count - 1; $i -ge 0 -and $null -eq $prev; $i--) {
        try { $prev = ConvertFrom-Json -InputObject ([string](Get-Content -LiteralPath $sums[$i].FullName -Raw)) } catch { $prev = $null }
    }
    $prevItems = @{}
    $before = @{}
    $prevTime = ''
    if ($null -ne $prev) {
        $prevTime = Get-TimeText $prev.time
        if ($null -ne $prev.items) { foreach ($p in @($prev.items.PSObject.Properties)) { $prevItems[$p.Name] = [string]$p.Value } }
        if ($null -ne $prev.passed_before) { foreach ($p in @($prev.passed_before.PSObject.Properties)) { $before[$p.Name] = Get-TimeText $p.Value } }
    }
    $by = @{}
    foreach ($r in $S.rules) {
        $m = [regex]::Match($r.rule, 'SV-\d+')
        if (-not $m.Success) { continue }
        $k = $m.Value
        $v = $r.v
        if (-not $v) { $v = 'V-' + $k.Substring(3) }
        $it = @{ key = $k; v = $v; rule = $r.rule; stigid = $r.stigid; severity = $r.severity; result = $r.result }
        if ($r.result -eq 'fail') {
            $it.title = $r.title
            $d = $r.details.Trim()
            if ($d) { if ($d.Length -gt $detailsMax) { $d = $d.Substring(0, $detailsMax) }; $it.details = $d }
        }
        if ($prevItems.ContainsKey($k)) { $it.prev = $prevItems[$k] }
        if ($before.ContainsKey($k)) { $it.passed = $before[$k] }
        $by[$k] = $it
    }
    $items = @()
    $counts = @{}
    $results = @{}
    $passedNow = @{}
    foreach ($k in $before.Keys) { $passedNow[$k] = $before[$k] }
    foreach ($k in @($by.Keys | Sort-Object)) {
        $it = $by[$k]
        $items += @($it)
        $counts[$it.result] = 1 + [int]$counts[$it.result]
        $results[$k] = $it.result
        if ($it.result -eq 'pass') { $passedNow[$k] = $nowText }
    }
    $scan = @{ scanned = $true; why = ''; key = $key; benchmark = $S.stig_id; label = (Get-Label $S); profile_title = $S.title
        stig_version = (Get-StigVersion $S); site = $S.site; instance = $S.instance; scanner = 'Evaluate-STIG'; platform = 'windows'
        history = 'items'; os = $facts.os; counts = $counts; items = $items; previous = @{ time = $prevTime }; saved = @{}
        checklist = (Split-Path -Leaf $File.FullName) }
    if ($ReadMode) { $scan.checklist_time = $File.LastWriteTimeUtc.ToString('yyyy-MM-dd\THH:mm:ss\Z') }
    if ($save) {
        New-PrivateFolder $dir
        New-Item -ItemType Directory -Path $hd -Force | Out-Null
        $kept = Join-Path $hd ($stamp + '_checklist' + ([string]$File.Extension).ToLower())
        Copy-Item -LiteralPath $File.FullName -Destination $kept -Force
        $summary = @{ time = $nowText; stamp = $stamp; counts = $counts; scanner = 'Evaluate-STIG'; benchmark = $S.stig_id; label = $scan.label
            title = $S.title; stig_version = $scan.stig_version; site = $S.site; instance = $S.instance; checklist = $scan.checklist
            items = $results; passed_before = $passedNow }
        $sumPath = Join-Path $hd ($stamp + '_summary.json')
        Set-Content -LiteralPath $sumPath -Value (ConvertTo-Json -InputObject $summary -Depth 4 -Compress) -Encoding UTF8
        Copy-Item -LiteralPath $sumPath -Destination (Join-Path $hd 'latest-summary.json') -Force
        $stamps = @(@($sums | ForEach-Object { $_.Name.Substring(0, 16) }) + @($stamp))
        if ($stamps.Count -gt $keep) {
            foreach ($old in $stamps[0..($stamps.Count - $keep - 1)]) {
                Get-ChildItem -LiteralPath $hd -File | Where-Object { $_.Name.StartsWith($old + '_') } | Remove-Item -Force -ErrorAction SilentlyContinue
            }
        }
        $scan.saved = @{ dir = $hd; results = $kept; summary = $sumPath }
    }
    return $scan
}

try {
    $files = @()
    $readMode = [bool]$readFrom
    # where Evaluate-STIG is: the setting, a scheduled task that runs it, a folder named Evaluate-STIG* on the drives
    $tasks = @()
    if ($onWindows) { $tasks = @(Get-EvaluateTasks) }
    if ($readMode) {
        $where = $readFrom
        if ($readFrom -eq 'auto') {
            $where = ''
            foreach ($t in $tasks) { if ($t.output -and -not $where) { $where = $t.output; $facts.found_by = 'the scheduled task ' + $t.task } }
            if (-not $where -and $env:PUBLIC) { $where = Join-Path ([string]$env:PUBLIC) (Join-Path 'Documents' 'STIG_Compliance'); $facts.found_by = "Evaluate-STIG's default output folder" }
            if (-not $where) { throw "no folder to read Evaluate-STIG's checklists from: no scheduled task of it names one (-OutputPath), and no Public folder (win_check_stig_evaluate_read)" }
        }
        $facts.read = $where
        if (-not (Test-Path -LiteralPath $where -PathType Container)) {
            throw ('no folder ' + $where + " to read Evaluate-STIG's checklists from (win_check_stig_evaluate_read)")
        }
        $files = @(Get-Checklists $where)
        if (-not $files.Count) { throw ('no Evaluate-STIG checklist (.cklb or .ckl) in ' + $where + ' (win_check_stig_evaluate_read)') }
        $how = 'read from ' + $where
    }
    else {
        $script = ''
        $evaluate = [string](Get-Setting 'evaluate' '')
        if ($evaluate) {
            $script = Find-EvaluateIn $evaluate
            if (-not $script) { throw ('Evaluate-STIG is not at ' + $evaluate + ': no Evaluate-STIG.ps1 there (win_check_stig_evaluate)') }
            $facts.found_by = 'win_check_stig_evaluate'
        }
        else {
            foreach ($t in $tasks) {
                if (-not $script -and $t.script -and (Test-Path -LiteralPath $t.script -PathType Leaf)) { $script = $t.script; $facts.found_by = 'the scheduled task ' + $t.task }
            }
            if (-not $script) {
                $all = @(Search-Evaluate $searchDepth)
                if ($all.Count) {
                    $script = @($all | Sort-Object -Property @{ Expression = { (Get-Item -LiteralPath $_).LastWriteTimeUtc } } -Descending)[0]
                    $facts.found_by = 'a search of the local drives (' + $all.Count + ' found, the newest taken)'
                }
            }
            if (-not $script) {
                $why = 'Evaluate-STIG was not found on the server: no scheduled task runs Evaluate-STIG.ps1'
                $unreachable = @($tasks | Where-Object { $_.script } | ForEach-Object { $_.script })
                if ($unreachable.Count) { $why = 'Evaluate-STIG was not found on the server: its scheduled task runs ' + $unreachable[0] + ', which this job cannot open' }
                $why += (', and no folder named Evaluate-STIG* with Evaluate-STIG.ps1 in it, ' + $searchDepth + ' levels down the local drives. Set win_check_stig_evaluate to its folder')
                $def = ''
                if ($env:PUBLIC) { $def = Join-Path ([string]$env:PUBLIC) (Join-Path 'Documents' 'STIG_Compliance') }
                if ($def -and (Test-Path -LiteralPath $def -PathType Container)) {
                    $old = @(Get-Checklists $def)
                    if ($old.Count) { $why += ('; or read the checklists it wrote to ' + $def + ' (the newest ' + $old[0].LastWriteTimeUtc.ToString('yyyy-MM-dd') + ') with win_check_stig_evaluate_read: auto') }
                }
                throw $why
            }
        }
        $facts.evaluate = $script
        $workBase = [string]$env:TEMP
        if (-not $workBase) { $workBase = '/tmp' }
        if ($save) { New-PrivateFolder $dir; $workBase = $dir }
        $work = Join-Path $workBase ('run-' + $stamp)
        New-Item -ItemType Directory -Path (Join-Path $work 'out') -Force | Out-Null
        try {
            $ran = Invoke-Evaluate $script (Join-Path $work 'out') $work
            $said = Get-EvaluateSaid $ran.said
            $facts.evaluate_exit = $ran.code
            $facts.evaluate_failed = $said.failed
            $facts.evaluate_log = $said.log
            $facts.stderr = ''
            if ($ran.said) {
                $from = $ran.said.Length - 600      # no [Math] in Constrained Language Mode
                if ($from -lt 0) { $from = 0 }
                $facts.stderr = $ran.said.Substring($from).Trim()
            }
            if ($ran.timed_out) { throw ('Evaluate-STIG took longer than ' + $timeout + ' seconds (win_check_stig_timeout) - stopped') }
            $files = @(Get-Checklists (Join-Path $work 'out'))
            $rejected = @($said.rejected | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
            if (-not $files.Count -and $rejected.Count -and $takeRejected) {
                # its own schema check moved them away (a check of its that failed leaves a field it does not accept):
                # the statuses are still there - read, and said in the report
                $files = @($rejected | ForEach-Object { Get-Item -LiteralPath $_ } | Sort-Object -Property LastWriteTimeUtc -Descending)
                $facts.evaluate_rejected = @($files | ForEach-Object { $_.FullName })
            }
            if (-not $files.Count) {
                $why = 'Evaluate-STIG wrote no checklist (exit ' + $ran.code + ', run by ' + $ran.how + ')'
                if ($said.rejected.Count) {
                    $why += (': its schema check rejected ' + ((@($said.rejected | ForEach-Object { Split-Path -Leaf $_ })) -join ', ') + ' (moved to ' + (Split-Path -Parent $said.rejected[0]))
                    if (-not $takeRejected) { $why += '; win_check_stig_evaluate_rejected: true reads them)' } else { $why += ', not there any more)' }
                    if ($said.failed.Count) { $why += ('; its checks that failed: ' + ($said.failed -join ', ')) }
                    if ($said.log) { $why += (' - see ' + $said.log) }
                }
                else {
                    $short = ($said.lines -join ' | ')
                    if (-not $short) { $short = $facts.stderr }
                    if (-not $short) { $short = 'no message' }
                    if ($short.Length -gt 600) { $short = $short.Substring($short.Length - 600) }
                    $why += (': ' + $short)
                }
                throw $why
            }
            $how = 'run by ' + $ran.how
            # read before the run folder goes (Get-Scan copies each checklist it keeps)
            $seen = @{}
            foreach ($f in $files) {
                try { $stigs = @(Read-Checklist $f.FullName) } catch { continue }
                foreach ($s in $stigs) {
                    $sk = (@($s.stig_id, $s.site, $s.instance) -join '|')
                    if ($seen.ContainsKey($sk) -or -not $s.stig_id) { continue }
                    $seen[$sk] = $true
                    $facts.scans += @(Get-Scan $s $f $false)
                }
            }
        }
        finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if ($readMode) {
        $seen = @{}
        foreach ($f in $files) {
            try { $stigs = @(Read-Checklist $f.FullName) } catch { continue }
            foreach ($s in $stigs) {
                # a folder of several servers' checklists: only this one's
                if ($s.host -and $me -and (Get-Short $s.host) -ne (Get-Short $me)) { continue }
                $sk = (@($s.stig_id, $s.site, $s.instance) -join '|')
                if ($seen.ContainsKey($sk) -or -not $s.stig_id) { continue }
                $seen[$sk] = $true
                $facts.scans += @(Get-Scan $s $f $true)
            }
        }
    }
    if (-not $facts.scans.Count) { throw ('no STIG checklist of this server ' + $how + ' (' + $files.Count + ' checklist files)') }
    $facts.scans = @($facts.scans | Sort-Object -Property @{ Expression = { $_.label } })
    $facts.scanned = $true
}
catch {
    $facts.scanned = $false
    $facts.why = [string]$_.Exception.Message
    if (-not $facts.why) { $facts.why = [string]$_ }
}
Write-Result
