# Run every Windows check script for REAL on this Windows machine (no stand-ins), normally and in
# Constrained Language Mode, and check the result keeps the contract: one ###SITE-JSON### line,
# findings with id / severity / summary / hint. Also checks that every audit subcategory GUID in
# roles/win_check_audit/defaults/main.yml exists on this Windows. For CI on Windows runners:
#   powershell -File tests\windows\test_win_checks_real.ps1      pwsh -File tests\windows\test_win_checks_real.ps1
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$shell = (Get-Process -Id $PID).Path
$fail = 0
$count = 0
$wrapper = Join-Path ([System.IO.Path]::GetTempPath()) ('sa-clm-' + (Get-Random) + '.ps1')
Set-Content -LiteralPath $wrapper -Value @'
param([string] $Script)
$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
& $Script
'@
# settings like the role defaults, so the checks do real work
$configs = @{
    services = '{"required": ["EventLog", "RpcSs", "Dnscache", "W32Time"], "ignore": ["gupdate*", "edgeupdate*", "CDPUserSvc_*", "OneSyncSvc_*", "sppsvc", "MapsBroker"]}'
    network  = '{"dns_names": ["localhost"], "tcp": [{"name": "WinRM", "host": "127.0.0.1", "port": 5985}], "secure_channel": true}'
    certs    = '{"stores": ["Cert:\\LocalMachine\\My", "Cert:\\LocalMachine\\Root"], "warn_days": 30, "crit_days": 7}'
    patching = '{"search_pending": false}'
}
foreach ($dir in Get-ChildItem -Path (Join-Path $root 'roles') -Directory -Filter 'win_check_*') {
    $check = $dir.Name.Substring(10)
    $script = Join-Path $dir.FullName "files\win_$check.ps1"
    $env:SITE_CHECK_CONFIG = '{}'
    if ($configs.ContainsKey($check)) { $env:SITE_CHECK_CONFIG = $configs[$check] }
    foreach ($mode in 'full', 'constrained') {
        $count++
        $t0 = Get-Date
        if ($mode -eq 'full') { $out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File $script 2>&1 | ForEach-Object { [string]$_ }) }
        else { $out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File $wrapper -Script $script 2>&1 | ForEach-Object { [string]$_ }) }
        $secs = [int]((Get-Date) - $t0).TotalSeconds
        $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
        if (-not $line) { $fail++; Write-Host "FAIL - $check ($mode): no result line"; $out | Select-Object -Last 10 | ForEach-Object { Write-Host "       $_" }; continue }
        try { $r = ConvertFrom-Json -InputObject $line.Substring(16) } catch { $fail++; Write-Host "FAIL - $check ($mode): not JSON: $line"; continue }
        $bad = @()
        foreach ($f in @($r.findings)) {
            if ($null -eq $f) { continue }
            foreach ($k in 'id', 'severity', 'summary', 'hint') { if ($null -eq $f.$k) { $bad += "finding without $k" } }
            if (@('warning', 'critical') -notcontains [string]$f.severity) { $bad += "bad severity $($f.severity)" }
        }
        if ($bad.Count) { $fail++; Write-Host "FAIL - $check ($mode): $($bad -join '; ')"; continue }
        Write-Host ("ok   - {0} ({1}, {2} s): {3} finding(s)" -f $check, $mode, $secs, @($r.findings | Where-Object { $_ }).Count)
        foreach ($f in @($r.findings)) { if ($f) { Write-Host "         [$($f.severity)] $($f.id): $($f.summary)" } }
    }
}
Remove-Item -LiteralPath $wrapper -Force
# the audit subcategory GUIDs in the defaults exist on this Windows
$count++
$defaults = Get-Content -Raw -LiteralPath (Join-Path $root 'roles\win_check_audit\defaults\main.yml')
$entries = @([regex]::Matches($defaults, 'name: ([^,]+), guid: ([0-9A-F]{8}-69AE-11D9-BED3-505054503030)') | ForEach-Object { @{ name = $_.Groups[1].Value.Trim(); guid = $_.Groups[2].Value } })
$list = (& auditpol.exe /list /subcategory:* /v 2>&1 | Out-String).ToUpper()
# auditpol /list /v prints columns: "  Credential Validation      {0CCE923F-...}"
$missing = @($entries | Where-Object { $list -notmatch ('(?m)^\s*' + [regex]::Escape($_.name.ToUpper()) + '\s+\{' + $_.guid + '\}') } | ForEach-Object { $_.name + ' ' + $_.guid })
if ($entries.Count -eq 0 -or $missing.Count) { $fail++; Write-Host "FAIL - audit subcategories not known to this Windows (name + GUID): $($missing -join '; ') (checked $($entries.Count))" }
else { Write-Host "ok   - all $($entries.Count) audit subcategories (name + GUID) exist on this Windows" }
if ($fail) { Write-Host "$fail of $count test(s) failed"; exit 1 }
Write-Host "all $count tests passed"
