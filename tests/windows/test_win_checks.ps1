# Tests for the Windows check scripts (roles/win_check_*/files/*.ps1), with made-up server data
# (run_scenario.ps1). Every scenario runs twice: normal (FullLanguage) and in Constrained Language
# Mode, as on servers with WDAC / AppLocker. Runs in Windows PowerShell 5.1 and PowerShell 7,
# on Windows and on Linux:
#   pwsh -File tests/windows/test_win_checks.ps1         powershell -File tests\windows\test_win_checks.ps1
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$runner = Join-Path $PSScriptRoot 'run_scenario.ps1'
$shell = (Get-Process -Id $PID).Path
$fail = 0
$count = 0
function Script([string] $Check) { return (Join-Path $root "roles/win_check_$Check/files/win_$Check.ps1") }
# check, scenario, the finding ids expected (exactly), the severity of each ('' = do not care)
$cases = @(
    @('disk', 'disk_full', @{ 'disk:C:' = 'warning'; 'disk:D:' = 'critical' }),
    @('services', 'services', @{ 'services:W32Time' = 'critical'; 'services:NoSuchSvc' = 'critical'; 'services:Spooler' = 'warning'; 'services:crash:Foo Service' = 'warning' }),
    @('performance', 'performance', @{ 'performance:cpu' = 'warning'; 'performance:memory' = 'critical'; 'performance:pagefile:C:\pagefile.sys' = 'warning' }),
    @('time', 'time_cmos', @{ 'time:source' = 'critical' }),
    @('time', 'time_offset', @{ 'time:last-sync' = 'warning'; 'time:offset' = 'critical' }),
    @('time', 'time_stopped', @{ 'time:service' = 'critical' }),
    @('network', 'network', @{ 'network:gateway' = 'warning'; 'network:dns:bad.test' = 'critical'; 'network:secure-channel' = 'critical'; 'network:tcp:127.0.0.1:1' = 'critical' }),
    @('network', 'network_ok', @{}),
    @('eventlog', 'eventlog', @{ 'eventlog:unexpected-shutdown' = 'critical'; 'eventlog:disk-errors' = 'critical'; 'eventlog:security-log-cleared' = 'critical'; 'eventlog:errors' = 'warning'; 'eventlog:full:Security' = 'critical' }),
    @('security', 'security', @{ 'security:defender-realtime' = 'critical'; 'security:defender-signatures' = 'warning'; 'security:av-service:masvc' = 'critical'; 'security:firewall:Public' = 'critical'; 'security:smb1' = 'critical'; 'security:rdp-nla' = 'warning'; 'security:TLS1.0' = 'warning'; 'security:TLS1.1' = 'warning' }),
    @('security', 'security_passive', @{}),
    @('audit', 'audit', @{ 'audit:policy' = 'warning'; 'audit:log-size:Security' = 'warning'; 'audit:forwarder:SplunkForwarder' = 'critical' }),
    @('accounts', 'accounts', @{ 'accounts:guest' = 'critical'; 'accounts:password-age:Administrator' = 'warning'; 'accounts:inactive:svc_old' = 'warning'; 'accounts:admin:CORP\jdoe' = 'warning' }),
    @('accounts', 'accounts_dc', @{}),
    @('certs', 'certs', @{ ('certs:' + ('A' * 40)) = 'critical'; ('certs:' + ('B' * 40)) = 'critical'; ('certs:' + ('C' * 40)) = 'warning' }),
    @('patching', 'patching', @{ 'patching:reboot' = 'warning'; 'patching:age' = 'critical' }),
    @('patching', 'patching_ok', @{})
)
foreach ($clm in @($false, $true)) {
    foreach ($c in $cases) {
        $check = $c[0]; $scenario = $c[1]; $want = $c[2]
        $label = "$check/$scenario" + $(if ($clm) { ' (constrained)' } else { '' })
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Script', (Script $check), '-Scenario', $scenario)
        if ($clm) { $a += '-Clm' }
        $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
        $count++
        $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
        if (-not $line) { $fail++; Write-Host "FAIL - $label : no result line"; $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }; continue }
        $r = ConvertFrom-Json -InputObject $line.Substring(16)
        $got = @{}
        $bad = @()
        foreach ($f in @($r.findings)) {
            if ($null -eq $f) { continue }
            $got[[string]$f.id] = [string]$f.severity
            foreach ($k in 'id', 'severity', 'summary', 'hint') { if ($null -eq $f.$k) { $bad += "finding without $k" } }
            if (@('warning', 'critical') -notcontains [string]$f.severity) { $bad += "bad severity $($f.severity)" }
        }
        foreach ($k in $want.Keys) {
            if (-not $got.ContainsKey($k)) { $bad += "missing $k" }
            elseif ($want[$k] -and $got[$k] -ne $want[$k]) { $bad += "$k is $($got[$k]), expected $($want[$k])" }
        }
        foreach ($k in $got.Keys) { if (-not $want.ContainsKey($k)) { $bad += "unexpected $k" } }
        if ($bad.Count) {
            $fail++
            Write-Host "FAIL - $label : $($bad -join '; ')"
            foreach ($f in @($r.findings)) { if ($f) { Write-Host "       [$($f.severity)] $($f.id): $($f.summary)" } }
        }
        else { Write-Host "ok   - $label ($($got.Count) finding(s))" }
    }
}
# Constrained Language Mode: TLS endpoints are reported as not checkable, not as an error
$out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File $runner -Script (Script 'certs') -Scenario 'certs_endpoints_clm' -Clm 2>&1 | ForEach-Object { [string]$_ })
$count++
if (($out -join "`n") -match '"id":"certs:endpoints"') { Write-Host 'ok   - certs/endpoints in constrained mode are reported, not an error' }
else { $fail++; Write-Host 'FAIL - certs/endpoints in constrained mode'; $out | Select-Object -Last 5 | ForEach-Object { Write-Host "       $_" } }
if ($fail) { Write-Host "$fail of $count test(s) failed"; exit 1 }
Write-Host "all $count tests passed"
