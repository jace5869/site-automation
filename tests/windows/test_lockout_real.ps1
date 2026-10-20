# The account lockout scripts (roles/ad_lockout/files) for real, on a Windows machine that is not a domain
# controller (the CI runner): the Security log query of lockout_dc.ps1 is one Windows takes (its XPath -
# the time window, the Status condition - is checked by Windows itself, not by a stand-in), and
# lockout_source.ps1 reads the services, scheduled tasks, sessions, processes and mapped drives of the
# account running it without an error. Needs an elevated prompt (the Security log, other users' processes).
#   powershell -File tests\windows\test_lockout_real.ps1        pwsh -File tests\windows\test_lockout_real.ps1
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$shell = (Get-Process -Id $PID).Path
$fail = 0
function Run([string] $Name, $Config) {
    $env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject $Config -Depth 5 -Compress
    $out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "roles/ad_lockout/files/$Name.ps1") 2>&1 | ForEach-Object { [string]$_ })
    $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
    if (-not $line) { $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }; return $null }
    return (ConvertFrom-Json -InputObject $line.Substring(16))
}
function Check([string] $What, [bool] $Ok, $Detail) {
    if ($Ok) { Write-Host "ok   - $What" } else { $script:fail++; Write-Host "FAIL - $What"; Write-Host "       $Detail" }
}
$me = [string]$env:USERNAME
$r = Run 'lockout_dc' @{ users = @(@{ sam = $me; upn = '' }, @{ sam = "o'brien"; upn = 'x&y@example.mil' }); hours = 2; max_events = 50 }
Check 'lockout_dc.ps1 answers' ($null -ne $r) 'no result line'
if ($r) {
    $errs = @($r.dc.errors | Where-Object { $_ -notmatch 'ActiveDirectory' })
    Check 'the Security log queries are taken (only the missing ActiveDirectory module is noted)' ($errs.Count -eq 0) ($errs -join ' | ')
    Check 'who it is' ([string]$r.dc.name -ne '' -and $r.dc.role -ne '') ($r.dc | ConvertTo-Json -Compress)
}
$r = Run 'lockout_source' @{ users = @(@{ sam = $me; domain = [string]$env:USERDNSDOMAIN; netbios = [string]$env:USERDOMAIN; upn = ''; sid = '' }) }
Check 'lockout_source.ps1 answers' ($null -ne $r) 'no result line'
if ($r) {
    Check 'services, tasks, sessions, processes read without an error' (@($r.source.errors).Count -eq 0) (@($r.source.errors) -join ' | ')
    Check 'its own processes are found' (@($r.source.found | Where-Object { $_.kind -eq 'process' }).Count -gt 0) ($r.source | ConvertTo-Json -Compress -Depth 4)
}
if ($fail) { Write-Host "$fail test(s) failed"; exit 1 }
Write-Host 'all real lockout script tests passed'
