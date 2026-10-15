# Tests for the Active Directory report scripts (roles/ad_report/files/ad_dc.ps1, ad_domain.ps1),
# with made-up domain data (run_ad_scenario.ps1). Every scenario runs twice: normal (FullLanguage)
# and in Constrained Language Mode, as on DCs with WDAC / AppLocker. Runs in Windows PowerShell 5.1
# and PowerShell 7, on Windows and on Linux:
#   pwsh -File tests/windows/test_ad_collect.ps1         powershell -File tests\windows\test_ad_collect.ps1
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$runner = Join-Path $PSScriptRoot 'run_ad_scenario.ps1'
$shell = (Get-Process -Id $PID).Path
$fail = 0
$count = 0
function Script([string] $Name) { return (Join-Path $root "roles/ad_report/files/$Name.ps1") }
# Each case: script, scenario, the top key of the result (dc / domain), and checks: a scriptblock
# per name that gets the result and returns $true when it is right.
$cases = @(
    @('ad_dc', 'dc_ok', 'dc', [ordered]@{
            'no error'                  = { param($r) @($r.errors).Count -eq 0 }
            'name, domain, PDC'         = { param($r) $r.name -eq 'DC01' -and $r.domain -eq 'corp.test' -and $r.role -eq 'pdc' -and $r.fqdn -eq 'dc01.corp.test' }
            'LDAP answers, synchronized' = { param($r) $r.ldap.ok -and $r.ldap.synchronized -and $r.gc_port.ok }
            'dcdiag: 3 tests passed'    = { param($r) @($r.dcdiag.tests | Where-Object { $_.result -eq 'passed' }).Count -eq 3 }
            'dcdiag DNS passed'         = { param($r) @($r.dcdiag_dns.tests).Count -eq 1 -and $r.dcdiag_dns.tests[0].result -eq 'passed' }
            'replication: 2 links, no failure' = { param($r) @($r.repl).Count -eq 2 -and @($r.repl | Where-Object { $_.failures -gt 0 }).Count -eq 0 -and [string]$r.repl[0].last_success -ne '' }
            'queue 0'                   = { param($r) $r.queue -eq 0 }
            'SYSVOL shared, DFSR normal' = { param($r) @($r.sysvol.shares).Count -eq 2 -and $r.sysvol.engine -eq 'dfsr' -and $r.sysvol.dfsr_state -eq 4 -and $r.sysvol.ready -eq 1 }
            'time: PDC against its NTP source' = { param($r) $r.time.ref -eq 'ntp.corp.test' -and [double]$r.time.offset -eq 0.015 }
            'disks of database, logs, SYSVOL' = { param($r) @($r.disks).Count -eq 3 -and @($r.disks | Where-Object { $_.what -eq 'database' })[0].free_pct -eq 80 }
            'certificate for LDAPS and KDC' = { param($r) @($r.certs).Count -eq 1 -and @($r.certs[0].purposes).Count -eq 2 }
            'no event (informational ones left out)' = { param($r) @($r.events).Count -eq 0 }
        }),
    @('ad_dc', 'dc_broken', 'dc', [ordered]@{
            'not synchronized'          = { param($r) $r.ldap.synchronized -eq $false }
            'dcdiag skips both, Replications failed with its reason' = { param($r) $t = @($r.dcdiag.tests | Where-Object { $_.result -eq 'failed' }); $t.Count -eq 1 -and $t[0].test -eq 'Replications' -and (@($t[0].detail) -join ' ') -match '8453' }
            'dcdiag DNS failed, errors kept' = { param($r) $r.dcdiag_dns.tests[0].result -eq 'failed' -and @($r.dcdiag_dns.problems).Count -eq 2 }
            'replication failures, never succeeded' = { param($r) $a = @($r.repl | Where-Object { $_.failures -eq 12 }); $b = @($r.repl | Where-Object { $_.status -eq '1722' }); $a.Count -eq 1 -and $a[0].status -eq '8453' -and $b[0].last_success -eq '' }
            'queue 75'                  = { param($r) $r.queue -eq 75 }
            'kdc stopped'               = { param($r) @($r.services | Where-Object { $_.name -eq 'kdc' -and $_.state -eq 'Stopped' }).Count -eq 1 }
            'SYSVOL share missing, DFSR in error' = { param($r) @($r.sysvol.shares) -notcontains 'SYSVOL' -and $r.sysvol.dfsr_state -eq 5 }
            'time: DC against the PDC, -91.5 s' = { param($r) $r.time.ref -eq 'dc01.corp.test' -and [double]$r.time.offset -eq -91.5 }
            'database disk 2% free'     = { param($r) @($r.disks | Where-Object { $_.what -eq 'database' })[0].free_pct -eq 2 }
            'no client site: 2 addresses of the last day' = { param($r) $r.no_client_site.distinct -eq 2 -and $r.no_client_site.count -eq 3 }
            'events: 2213 twice, 2887, 5719 (not the other System source)' = { param($r) $e = @($r.events); $e.Count -eq 3 -and @($e | Where-Object { $_.id -eq 2213 })[0].count -eq 2 }
            'security: member added, user created, log cleared, with who' = { param($r) $s = @($r.security); $s.Count -eq 3 -and @($s | Where-Object { $_.id -eq 4728 -and $_.group -eq 'Domain Admins' -and $_.member -like 'CN=Mallory*' -and $_.by -eq 'CORP\admin1' }).Count -eq 1 }
        }),
    @('ad_dc', 'dc_german', 'dc', [ordered]@{
            'dates in the DC''s own format are read' = { param($r) [string]$r.time.last_sync -ne '' -and [string]$r.repl[0].last_success -ne '' }
        }),
    @('ad_dc', 'dc_no_ad', 'dc', [ordered]@{
            'still answers, says the module is missing' = { param($r) $r.ad_module -eq $false -and (@($r.errors) -join ' ') -match 'ActiveDirectory' -and -not $r.ldap.ok }
        }),
    @('ad_domain', 'domain_ok', 'domain', [ordered]@{
            'no error'                  = { param($r) @($r.errors).Count -eq 0 }
            'domain and forest'         = { param($r) $r.dns -eq 'corp.test' -and $r.forest.name -eq 'corp.test' -and $r.schema.version -eq 88 }
            'two DCs'                   = { param($r) @($r.dcs).Count -eq 2 }
            'five FSMO roles, all answer' = { param($r) @($r.fsmo).Count -eq 5 -and @($r.fsmo | Where-Object { -not $_.reachable }).Count -eq 0 }
            'RID pool'                  = { param($r) $r.rid.total -eq 1073741823 -and $r.rid.issued -eq 5600 }
            'tombstone, Recycle Bin, krbtgt' = { param($r) $r.tombstone_days -eq 180 -and $r.recycle_bin -eq $true -and @($r.krbtgt).Count -eq 1 }
            'up-to-dateness: 3 entries' = { param($r) @($r.utdv).Count -eq 3 }
            'backups of 2 partitions'   = { param($r) @($r.backups).Count -eq 2 -and [string]$r.backups[0].last -ne '' }
            'SYSVOL migration eliminated' = { param($r) $r.sysvol_migration -eq 'Eliminated' }
            'SRV records'               = { param($r) @($r.dns_srv).Count -eq 3 -and @($r.dns_srv[0].targets).Count -eq 2 }
            'privileged groups, no change' = { param($r) @($r.groups).Count -eq 3 -and @($r.groups | Where-Object { @($_.changes).Count }).Count -eq 0 }
            'GPO matches SYSVOL'        = { param($r) @($r.gpos).Count -eq 1 -and $r.gpos[0].sysvol_version -eq 7 -and $r.gpos[0].folder }
            'no change'                 = { param($r) @($r.created).Count + @($r.deleted).Count + @($r.account_changes).Count + @($r.config_changes).Count + @($r.gpo_links).Count -eq 0 }
        }),
    @('ad_domain', 'domain_changes', 'domain', [ordered]@{
            'only the unknown group is an error' = { param($r) @($r.errors).Count -eq 1 -and $r.errors[0] -match 'NoSuchGroup' }
            'infrastructure master not answering' = { param($r) @($r.fsmo | Where-Object { $_.holder -eq 'dc09.corp.test' -and $_.reachable -eq $false }).Count -eq 1 }
            'schema changed, new attribute' = { param($r) $r.schema.changed -ne '' -and $r.schema.new_objects[0].name -eq 'msExampleAttr' }
            'Recycle Bin off, tombstone unset' = { param($r) $r.recycle_bin -eq $false -and $null -eq $r.tombstone_days }
            'RID pool almost used'      = { param($r) $r.rid.issued -eq 1060000000 }
            'SRV: one missing, one not resolved' = { param($r) @($r.dns_srv[0].targets).Count -eq 1 -and $r.dns_srv[1].error -ne '' }
            'trust does not verify'     = { param($r) $r.trusts[0].verify -match '1311' }
            'site without DC and subnet, subnet without site' = { param($r) @($r.sites | Where-Object { $_.name -eq 'Branch' -and $_.dcs -eq 0 -and $_.subnets -eq 0 }).Count -eq 1 -and @($r.subnets_without_site)[0] -eq '10.9.0.0/24' }
            'Domain Admins: 1 added, 1 removed, on which DC' = { param($r) $c = @(@($r.groups | Where-Object { $_.name -eq 'Domain Admins' })[0].changes); $c.Count -eq 2 -and @($c | Where-Object { $_.action -eq 'added' -and $_.dc -eq 'DC02' }).Count -eq 1 -and @($c | Where-Object { $_.action -eq 'removed' }).Count -eq 1 }
            'created: a user and a computer' = { param($r) @($r.created).Count -eq 2 }
            'deleted: bob'              = { param($r) @($r.deleted).Count -eq 1 -and $r.deleted[0].name -eq 'bob' }
            'carol disabled, dave unlocked (an old change left out)' = { param($r) $a = @($r.account_changes); $a.Count -eq 2 -and @($a | Where-Object { $_.name -eq 'carol' -and $_.change -eq 'disabled' }).Count -eq 1 -and @($a | Where-Object { $_.name -eq 'dave' -and $_.change -eq 'unlocked' }).Count -eq 1 }
            'locked out now'            = { param($r) @($r.locked_out)[0] -eq 'erin' }
            'config: subnet and DC created' = { param($r) @($r.config_changes).Count -eq 2 -and @($r.config_changes | Where-Object { $_.class -eq 'nTDSDSA' -and $_.name -eq 'DC03' }).Count -eq 1 }
            'GPO: version mismatch, missing folder, orphan folder' = { param($r) $g = @($r.gpos); @($g | Where-Object { $_.sysvol_version -eq 5 -and $_.ad_version -eq 7 }).Count -eq 1 -and @($g | Where-Object { -not $_.folder }).Count -eq 1 -and @($r.orphan_folders).Count -eq 1 }
            'GPO link changed on OU=Staff' = { param($r) @($r.gpo_links).Count -eq 1 -and $r.gpo_links[0].target -like 'OU=Staff*' -and @($r.gpo_links[0].links).Count -eq 1 }
        }),
    @('ad_domain', 'domain_no_ad', 'domain', [ordered]@{
            'still answers, says the module is missing' = { param($r) (@($r.errors) -join ' ') -match 'ActiveDirectory' }
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
