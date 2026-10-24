# Test for the log analysis's Windows collector (roles/log_analysis/files/log_collect.ps1) with made-up
# event logs (a stand-in Get-WinEvent) and text logs in a temporary folder - normal and in Constrained
# Language Mode. Windows PowerShell 5.1 and PowerShell 7, on Windows and on Linux:
#   pwsh -File tests/windows/test_log_collect.ps1          powershell -File tests\windows\test_log_collect.ps1
param([switch] $Inner, [switch] $Clm, [string] $Dir)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$script = Join-Path $root 'roles/log_analysis/files/log_collect.ps1'

if ($Inner) {
    # ---- one run of the collector against the made-up data ----
    $now = Get-Date
    $script:rid = 0
    function Ev($log, $id, $provider, $level, $minutesAgo, $message) {
        $script:rid++
        return [pscustomobject]@{ LogName = $log; Id = $id; ProviderName = $provider; Level = $level; RecordId = $script:rid
            TimeCreated = $now.AddMinutes(-$minutesAgo); Message = $message }
    }
    $global:testEvents = @(
        (Ev 'System' 7031 'Service Control Manager' 2 60 'The snc_mid service terminated unexpectedly.'),
        (Ev 'System' 7036 'Service Control Manager' 4 50 'The snc_mid service entered the stopped state.'),
        (Ev 'System' 7036 'Other Provider' 4 50 'information nobody asked for'),
        (Ev 'System' 6008 'EventLog' 2 1800 'The previous system shutdown was unexpected (too old).'),
        (Ev 'Application' 1000 'Application Error' 3 30 'Faulting application name: java.exe'),
        (Ev 'Security' 4625 'Microsoft-Windows-Security-Auditing' 0 20 'An account failed to log on.'),
        (Ev 'Security' 4624 'Microsoft-Windows-Security-Auditing' 0 20 'An account was logged on (not asked for).'))
    function Get-WinEvent {
        param($FilterHashtable, $MaxEvents, $ErrorAction)
        $h = $FilterHashtable
        if (@('System', 'Application', 'Security') -notcontains $h.LogName) { throw "There is not an event log on the localhost computer that matches `"$($h.LogName)`"." }
        $r = @($global:testEvents | Where-Object {
                $_.LogName -eq $h.LogName -and
                (-not $h.Id -or @($h.Id) -contains $_.Id) -and
                (-not $h.ProviderName -or @($h.ProviderName) -contains $_.ProviderName) -and
                (-not $h.Level -or @($h.Level) -contains $_.Level) -and
                (-not $h.StartTime -or $_.TimeCreated -ge $h.StartTime) -and
                (-not $h.EndTime -or $_.TimeCreated -le $h.EndTime) })
        if ($r.Count -eq 0) { throw (New-Object System.Exception 'No events were found that match the specified selection criteria.') }
        $r | Select-Object -First $MaxEvents
    }
    $cfg = @{ since_min = 1440; until_min = 0; logs = @('System', 'Application', 'DNS Server'); level = 3
        providers = @('Service Control Manager'); providers_only = $false
        event_ids = @(@{ log = ''; id = 4625 }, @{ log = 'System'; id = 7031 })
        files = @((Join-Path $Dir 'agent\logs\agent0.log*'), (Join-Path $Dir 'old.log'), (Join-Path $Dir 'none*.log'), (Join-Path $Dir 'agent\logs\*.gz'))
        match = 'error|warn|SEVERE'; max_lines = 500; max_line_chars = 120 }
    $env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject $cfg -Depth 6 -Compress
    if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
    & $script
    return
}

# ---- the test: make the text logs, run the collector twice (normal, constrained), check what it read ----
$shell = (Get-Process -Id $PID).Path
$dir = Join-Path ([System.IO.Path]::GetTempPath()) ('sa-logs-' + (Get-Random))
New-Item -ItemType Directory -Path (Join-Path $dir 'agent\logs') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $dir 'agent\logs\agent0.log') -Value @(
    '10/23/26 07:01:02 (123) Worker-Standard SEVERE *** ERROR *** connection refused',
    '10/23/26 07:01:03 (124) Worker-Standard INFO all fine',
    ('2026-10-23 07:01:04 WARNING ' + ('x' * 300)))
Set-Content -LiteralPath (Join-Path $dir 'old.log') -Value 'ERROR from long ago'
(Get-Item -LiteralPath (Join-Path $dir 'old.log')).LastWriteTime = (Get-Date).AddDays(-3)
Set-Content -LiteralPath (Join-Path $dir 'agent\logs\agent0.log.1.gz') -Value 'ERROR compressed'
$fail = 0
foreach ($clm in @($false, $true)) {
    $label = 'log_collect' + $(if ($clm) { ' (constrained)' } else { '' })
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Inner', '-Dir', $dir)
    if ($clm) { $a += '-Clm' }
    $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
    $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
    if (-not $line) { $fail++; Write-Host "FAIL - $label : no result line"; $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }; continue }
    $r = (ConvertFrom-Json -InputObject $line.Substring(16)).facts
    $got = @($r.entries | ForEach-Object { '{0}|{1}|{2}' -f $_.source, $_.level, ($_.message -replace '\s+', ' ').Substring(0, [Math]::Min(40, $_.message.Length)) })
    $bad = @()
    $want = @(
        'System|error|Event 7031: The snc_mid service',
        'System|warning|Event 7036: The snc_mid service',
        'Application|warning|Event 1000: Faulting application',
        'Security|warning|Event 4625: An account failed')
    foreach ($w in $want) { if (-not @($got | Where-Object { $_.StartsWith($w) }).Count) { $bad += "missing $w" } }
    foreach ($s in @($r.sources | Where-Object { @('System', 'Application', 'Security') -contains $_.name -and $_.error })) { $bad += "$($s.name) has an error: $($s.error)" }
    foreach ($x in $got) { if ($x -match 'too old|nobody asked|not asked for|long ago|compressed|all fine') { $bad += "should not be there: $x" } }
    if (@($got | Where-Object { $_ -like 'System|error|Event 7031*' }).Count -ne 1) { $bad += '7031 read twice (by level and by ID)' }
    $agent = @($r.entries | Where-Object { $_.source -like '*agent0.log' })
    if ($agent.Count -ne 2) { $bad += "agent0.log: $($agent.Count) lines, expected 2" }
    if (@($agent | Where-Object { $_.message.Length -gt 120 }).Count) { $bad += 'a line longer than max_line_chars' }
    if (-not (@($agent | Where-Object { $_.time -eq '2026-10-23T07:01:04' }).Count)) { $bad += 'the ISO time of a text line (as written, no Z)' }
    $dns = @($r.sources | Where-Object { $_.name -eq 'DNS Server' })
    if (-not $dns.Count -or -not $dns[0].error) { $bad += 'the missing DNS Server log is not reported' }
    $skips = @($r.sources | Where-Object { $_.skipped } | ForEach-Object { $_.skipped })
    foreach ($s in 'unchanged in the window', 'no such file', 'compressed') { if ($skips -notcontains $s) { $bad += "no source skipped as $s" } }
    if ($bad.Count) {
        $fail++; Write-Host "FAIL - $label : $($bad -join '; ')"; $got | ForEach-Object { Write-Host "       $_" }
        $r.sources | ForEach-Object { Write-Host "       source $($_.name): $($_.lines) $($_.error) $($_.skipped)" }
    }
    else { Write-Host "ok   - $label : $($got.Count) lines, the providers, IDs, levels, files and the window as asked" }
}
Remove-Item -LiteralPath $dir -Recurse -Force
if ($fail) { Write-Host "$fail FAILED"; exit 1 }
Write-Host 'ALL PASSED'
