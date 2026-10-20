# Tests for the account lockout scripts (roles/ad_lockout/files/lockout_*.ps1), with a made-up domain
# (run_lockout_scenario.ps1). Every scenario runs twice: normal (FullLanguage) and in Constrained
# Language Mode, as on DCs with WDAC / AppLocker. Runs in Windows PowerShell 5.1 and PowerShell 7, on
# Windows and on Linux:
#   pwsh -File tests/windows/test_lockout_collect.ps1      powershell -File tests\windows\test_lockout_collect.ps1
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$runner = Join-Path $PSScriptRoot 'run_lockout_scenario.ps1'
$shell = (Get-Process -Id $PID).Path
$fail = 0
$count = 0
function Script([string] $Name) { return (Join-Path $root "roles/ad_lockout/files/$Name.ps1") }
function One($list, $sb) { return @(@($list) | Where-Object $sb) }
# Each case: script, scenario, the top key of the result, and checks: a scriptblock per name that gets
# the result and returns $true when it is right.
$cases = @(
    @('lockout_find', 'find_one', 'find', [ordered]@{
            'no error, the PDC of corp.test'   = { param($r) @($r.errors).Count -eq 0 -and $r.role -eq 'pdc' -and $r.domain -eq 'corp.test' -and $r.netbios -eq 'CORP' }
            'CORP\jdoe found, locked out'      = { param($r) $u = $r.users[0]; $u.found -and $u.sam -eq 'jdoe' -and $u.locked -and $u.lockout_time -ne '' -and $u.dn -like 'CN=Jane Doe*' -and $u.sid -like 'S-1-5-21-*' }
            'the domain''s lockout policy'     = { param($r) $r.policy.threshold -eq 5 -and $r.policy.duration_min -eq 30 -and $r.policy.window_min -eq 30 }
            'no list of every locked account'  = { param($r) @($r.locked).Count -eq 0 }
        }),
    @('lockout_find', 'find_upn', 'find', [ordered]@{
            'a UPN in another case is found'   = { param($r) $u = $r.users[0]; $u.found -and $u.sam -eq 'jdoe' -and $u.input -eq 'JDOE@corp.test' }
            'an unknown name is not found'     = { param($r) $u = $r.users[1]; -not $u.found -and $u.here }
            'another domain is not this one''s' = { param($r) $u = $r.users[2]; -not $u.found -and -not $u.here }
            'an apostrophe in a name'          = { param($r) $u = $r.users[3]; $u.found -and $u.sam -eq "o'brien" -and $u.password_expired }
        }),
    @('lockout_find', 'find_all', 'find', [ordered]@{
            'locked out now: 2, the latest first (bob''s old lockoutTime left out)' = { param($r) $r.locked_total -eq 2 -and @($r.locked).Count -eq 2 -and $r.locked[0].sam -eq 'jdoe' -and $r.locked[1].sam -eq 'erin' }
            'erin''s fine-grained policy: 3, until unlocked' = { param($r) $e = $r.locked[1]; $e.policy -eq 'Admins-PSO' -and $e.threshold -eq 3 -and $e.duration_min -eq 0 }
        }),
    @('lockout_find', 'find_no_ad', 'find', [ordered]@{
            'still answers, says the module is missing' = { param($r) $r.ad_module -eq $false -and (@($r.errors) -join ' ') -match 'ActiveDirectory' -and $r.role -eq 'pdc' }
        }),
    @('lockout_dc', 'dc_pdc', 'dc', [ordered]@{
            'no error, who it is'              = { param($r) @($r.errors).Count -eq 0 -and $r.name -eq 'DC01' -and $r.role -eq 'pdc' }
            'its own count: 5 bad passwords, locked out, last logon' = { param($r) $u = $r.users[0]; $u.read -and $u.bad_pwd_count -eq 5 -and $u.locked -and $u.last_logon -ne '' }
            'erin: never logged on here'       = { param($r) $u = $r.users[1]; $u.read -and $u.last_logon -eq '' }
            '11 failures of jdoe (successes, older ones and other accounts left out)' = { param($r) @($r.events).Count -eq 11 -and @(One $r.events { $_.user -ne 'jdoe' }).Count -eq 0 }
            '4740: caller PC123'               = { param($r) $e = @(One $r.events { $_.id -eq 4740 }); $e.Count -eq 1 -and $e[0].computer -eq 'PC123' }
            '4767: unlocked by CORP\helpdesk1' = { param($r) @(One $r.events { $_.id -eq 4767 -and $_.by -eq 'CORP\helpdesk1' }).Count -eq 1 }
            '4771: status as 0x18 / 0x12, the address without ::ffff: (also typed JDOE)' = { param($r) @(One $r.events { $_.id -eq 4771 -and $_.status -eq '0x18' -and $_.ip -eq '192.0.2.50' }).Count -eq 4 -and @(One $r.events { $_.id -eq 4771 -and $_.status -eq '0x12' }).Count -eq 2 }
            '4768 refused after the lock (the success left out)' = { param($r) $e = @(One $r.events { $_.id -eq 4768 }); $e.Count -eq 1 -and $e[0].status -eq '0x12' }
            '4776: workstation without \\, bad password' = { param($r) $e = @(One $r.events { $_.id -eq 4776 }); $e.Count -eq 1 -and $e[0].computer -eq 'PC123' -and $e[0].status -eq '0xc000006a' }
            '4625: negative status read, sub status, type 3, WS9' = { param($r) $e = @(One $r.events { $_.id -eq 4625 })[0]; $e.status -eq '0xc000006d' -and $e.sub_status -eq '0xc000006a' -and $e.logon_type -eq 3 -and $e.computer -eq 'WS9' -and $e.ip -eq '192.0.2.60' }
            'addresses resolved (or empty)'    = { param($r) $r.names.'192.0.2.50' -eq 'pc123.corp.test' -and $r.names.'192.0.2.60' -eq '' }
            'not capped'                       = { param($r) @($r.capped).Count -eq 0 }
        }),
    @('lockout_dc', 'dc_bdc', 'dc', [ordered]@{
            'a DC, its own count 3'            = { param($r) $r.role -eq 'dc' -and $r.users[0].bad_pwd_count -eq 3 }
            'through the mail server, and an address with no name' = { param($r) $r.names.'192.0.2.25' -eq 'exch01.corp.test' -and @(One $r.events { $_.ip -eq '192.0.2.99' }).Count -eq 1 }
        }),
    @('lockout_dc', 'dc_capped', 'dc', [ordered]@{
            'only the newest 3, said so'       = { param($r) @($r.events).Count -eq 3 -and @($r.capped)[0] -eq 'jdoe' }
        }),
    @('lockout_dc', 'dc_quote', 'dc', [ordered]@{
            'an apostrophe in the name: asked for in double quotes, found' = { param($r) @($r.events).Count -eq 1 -and $r.events[0].computer -eq 'LAPTOP7' -and @($r.errors).Count -eq 0 }
        }),
    @('lockout_unlock', 'unlock_ok', 'unlock', [ordered]@{
            'unlocked, read back'              = { param($r) $r.unlocked[0].ok -and $r.unlocked[0].locked_after -eq $false }
        }),
    @('lockout_unlock', 'unlock_denied', 'unlock', [ordered]@{
            'refused: the reason kept'         = { param($r) -not $r.unlocked[0].ok -and $r.unlocked[0].error -match 'Insufficient access' }
        }),
    @('lockout_source', 'source_pc', 'source', [ordered]@{
            'no error, the computer'           = { param($r) @($r.errors).Count -eq 0 -and $r.name -eq 'PC123' }
            'services as CORP\jdoe and as the UPN (not LocalSystem)' = { param($r) @(One $r.found { $_.kind -eq 'service' }).Count -eq 2 }
            'the scheduled task'               = { param($r) @(One $r.found { $_.kind -eq 'scheduled task' -and $_.name -eq '\Copy reports' }).Count -eq 1 }
            'the disconnected session (not the other user''s)' = { param($r) $s = @(One $r.found { $_.kind -eq 'session' }); $s.Count -eq 1 -and $s[0].detail -like 'Disc*' }
            'OUTLOOK twice'                    = { param($r) @(One $r.found { $_.kind -eq 'process' -and $_.name -eq 'OUTLOOK' -and $_.detail -eq '2 running' }).Count -eq 1 }
            'drive H: mapped'                  = { param($r) @(One $r.found { $_.kind -eq 'mapped drive' -and $_.name -eq 'H:' }).Count -eq 1 }
        }),
    @('lockout_source', 'source_nobody', 'source', [ordered]@{
            'nothing runs as it, no error'     = { param($r) @($r.found).Count -eq 0 -and @($r.errors).Count -eq 0 }
        })
)
foreach ($clm in @($false, $true)) {
    foreach ($c in $cases) {
        $label = "$($c[0])/$($c[1])" + $(if ($clm) { ' (constrained)' } else { '' })
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Script', (Script $c[0]), '-Scenario', $c[1])
        if ($clm) { $a += '-Clm' }
        $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
        $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
        if (-not $line) {
            $count++; $fail++
            Write-Host "FAIL - $label : no result line"; $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }
            continue
        }
        $r = (ConvertFrom-Json -InputObject $line.Substring(16)).($c[2])
        foreach ($k in $c[3].Keys) {
            $count++
            $ok = $false
            try { $ok = [bool](& $c[3][$k] $r) } catch { $ok = $false }
            if ($ok) { Write-Host "ok   - $label : $k" }
            else { $fail++; Write-Host "FAIL - $label : $k"; Write-Host ("       errors: " + (@($r.errors) -join ' | ')) }
        }
    }
}
if ($fail) { Write-Host "$fail of $count test(s) failed"; exit 1 }
Write-Host "all $count tests passed"
