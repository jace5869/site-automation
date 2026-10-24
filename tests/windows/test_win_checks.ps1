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
$fsTrust = Join-Path ([System.IO.Path]::GetTempPath()) 'sa-win-trust'      # the trust scenarios' made-up Java and Python folders
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
    @('audit', 'audit_fallback', @{ 'audit:policy' = 'warning'; 'audit:log-size:Security' = 'warning'; 'audit:forwarder:SplunkForwarder' = 'critical' }),
    @('accounts', 'accounts', @{ 'accounts:guest' = 'critical'; 'accounts:password-age:Administrator' = 'warning'; 'accounts:inactive:svc_old' = 'warning'; 'accounts:admin:CORP\jdoe' = 'warning' }),
    @('accounts', 'accounts_dc', @{}),
    @('certs', 'certs', @{ ('certs:' + ('A' * 40)) = 'critical'; ('certs:' + ('B' * 40)) = 'critical'; ('certs:' + ('C' * 40)) = 'warning' }),
    @('certs', 'certs_trust', @{ ('certs:' + ('G' * 40)) = 'warning'; ('certs:' + ('ABCD' * 10)) = 'critical' }),
    @('trust', 'trust', @{ 'trust:system' = 'critical'; ('trust:java:' + (Join-Path $fsTrust 'java\lib\security\cacerts')) = 'warning'; 'trust:python' = 'warning' }),
    @('trust', 'trust_remove_dry', @{ 'trust:system' = 'critical'; ('trust:java:' + (Join-Path $fsTrust 'java\lib\security\cacerts')) = 'warning'; 'trust:python' = 'warning' }),
    @('trust', 'trust_remove', @{ 'trust:python' = 'warning' }),
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
# The trust stores: listed when they expire soon, never a finding; the long-expired and far-off ones left out
foreach ($clm in @($false, $true)) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Script', (Script 'certs'), '-Scenario', 'certs_trust')
    if ($clm) { $a += '-Clm' }
    $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
    $count++
    $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
    $bad = @()
    if (-not $line) { $bad += 'no result line' }
    else {
        $inv = @((ConvertFrom-Json -InputObject $line.Substring(16)).facts.inventory)
        $w = @{}
        foreach ($c in $inv) { $w[[string]$c.subject] = [string]$c.where }
        $want = @{ 'CN=test26.corp.test' = 'LocalMachine\Root (Trusted Root Certification Authorities) (trust store)'
            'CN=peer.corp.test' = 'LocalMachine\AddressBook (Other People) (trust store)'
            'CN=keyed.corp.test' = 'LocalMachine\CA (Intermediate Certification Authorities), with its private key'
            'CN=bound.corp.test' = 'LocalMachine\CA (Intermediate Certification Authorities), used by https 0.0.0.0:8443' }
        foreach ($k in $want.Keys) { if ($w[$k] -ne $want[$k]) { $bad += "$k where '$($w[$k])', expected '$($want[$k])'" } }
        foreach ($k in $w.Keys) { if (-not $want.ContainsKey($k)) { $bad += "$k should not be listed" } }
    }
    $label = 'certs/certs_trust inventory' + $(if ($clm) { ' (constrained)' } else { '' })
    if ($bad.Count) { $fail++; Write-Host "FAIL - $label : $($bad -join '; ')" } else { Write-Host "ok   - $label" }
}
# What the inventory reads from a certificate (tests/fixtures/pki): issuer, serial, SHA-1 and SHA-256, key, names, tier
$metaWant = @{
    'CN=Test Root CA, O=Test PKI, C=US'      = @{ tier = 'root'; key = 'RSA 3072'; sans = ''; sha1 = '07D72068104FB45E5366A7556CDE5CF5F171342F'
        sha256 = 'EAEC495D8D35C5A18931154768 44DD2961B89C6F859543A9E3158E9DD4B0420C' -replace ' ', ''; serial = '0F965ECAB20EF6EA9F895AA1C292DCD86E14B1D3'; issuer = 'CN=Test Root CA, O=Test PKI, C=US' }
    'CN=Test Issuing CA, O=Test PKI, C=US'   = @{ tier = 'intermediate'; key = 'EC P-256'; sans = ''; sha1 = '176D43333E4A3290F6E056CBD9DD0C96FB182F6F'
        sha256 = '6F384CB03A8DB04CCC468BD8A6D33F6B500FB508E0323CCAAEB427B164800055'; serial = '1001'; issuer = 'CN=Test Root CA, O=Test PKI, C=US' }
    'CN=web01.example.mil, O=Test PKI, C=US' = @{ tier = 'ca-signed'; key = 'RSA 2048'; sans = 'DNS:web01.example.mil, DNS:web.example.mil, IP:192.0.2.10'
        sha1 = '487B8E272CBC7625EE18207F58559BD961FD0561'; sha256 = '9D4FE442B5105EFD55B528022F310DCFF9255DE764E3222CC7B7BA2F43974DFA'; serial = '2002'; issuer = 'CN=Test Issuing CA, O=Test PKI, C=US' }
    'CN=self01.example.mil'                  = @{ tier = 'self-signed'; key = 'EC P-384'; sans = 'DNS:self01.example.mil'
        sha1 = '938C3203826F24460951E54C049918E8A6CF5FAF'; sha256 = 'EEA658E2F979C700DA680A77500100D5CDAF0184EB1CCE7416A3E45645D08833'; serial = '293F2F1A9421CDD6DDD13E31C835E8A6D3371238'; issuer = 'CN=self01.example.mil' }
}
foreach ($clm in @($false, $true)) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Script', (Script 'certs'), '-Scenario', 'certs_meta')
    if ($clm) { $a += '-Clm' }
    $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
    $count++
    $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
    $bad = @()
    if (-not $line) { $bad += 'no result line'; $out | Select-Object -Last 5 | ForEach-Object { $bad += $_ } }
    else {
        $got = @{}
        foreach ($c in @((ConvertFrom-Json -InputObject $line.Substring(16)).facts.inventory)) { $got[[string]$c.subject] = $c }
        foreach ($k in $metaWant.Keys) {
            $c = $got[$k]
            if ($null -eq $c) { $bad += "$k not listed"; continue }
            $w = $metaWant[$k]
            $have = @{ tier = [string]$c.tier; key = [string]$c.key; sans = (@($c.sans) -join ', '); sha1 = [string]$c.sha1; sha256 = [string]$c.sha256; serial = [string]$c.serial; issuer = [string]$c.issuer }
            foreach ($f in $w.Keys) { if ($have[$f] -ne $w[$f]) { $bad += "$k ${f}: '$($have[$f])', expected '$($w[$f])'" } }
            if (-not [string]$c.sig_alg) { $bad += "$k has no signature algorithm" }
        }
    }
    $label = 'certs/certs_meta inventory fields' + $(if ($clm) { ' (constrained)' } else { '' })
    if ($bad.Count) { $fail++; Write-Host "FAIL - $label : $($bad -join '; ')" } else { Write-Host "ok   - $label" }
}
# Remote Desktop: left out of the report (and so of the renewal) unless remote_desktop is on
$rdpWant = @{
    certs_rdp    = @{ 'CN=web01.corp.test' = 'LocalMachine\My'; 'CN=bound.corp.test' = 'LocalMachine\My, used by https 0.0.0.0:443' }
    certs_rdp_on = @{ 'CN=web01.corp.test' = 'LocalMachine\My'; 'CN=bound.corp.test' = 'LocalMachine\My, used by https 0.0.0.0:443'
        'CN=rdpused.corp.test' = 'LocalMachine\My, used by Remote Desktop'; 'CN=rdponly.corp.test' = 'LocalMachine\My'; 'CN=SRV01' = 'LocalMachine\Remote Desktop' }
}
foreach ($scen in 'certs_rdp', 'certs_rdp_on') {
    foreach ($clm in @($false, $true)) {
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Script', (Script 'certs'), '-Scenario', $scen)
        if ($clm) { $a += '-Clm' }
        $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
        $count++
        $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
        $bad = @()
        if (-not $line) { $bad += 'no result line' }
        else {
            $r = ConvertFrom-Json -InputObject $line.Substring(16)
            $w = @{}
            foreach ($c in @($r.facts.inventory)) { $w[[string]$c.subject] = [string]$c.where }
            $want = $rdpWant[$scen]
            foreach ($k in $want.Keys) { if ($w[$k] -ne $want[$k]) { $bad += "$k where '$($w[$k])', expected '$($want[$k])'" } }
            foreach ($k in $w.Keys) { if (-not $want.ContainsKey($k)) { $bad += "$k should not be listed" } }
            $n = @($r.findings | Where-Object { $_ }).Count
            if ($n -ne $want.Count) { $bad += "$n finding(s), expected $($want.Count)" }
        }
        $label = "certs/$scen" + $(if ($clm) { ' (constrained)' } else { '' })
        if ($bad.Count) { $fail++; Write-Host "FAIL - $label : $($bad -join '; ')" } else { Write-Host "ok   - $label" }
    }
}
# The renewal (win_cert_csr.ps1): what gets a CSR, what does not and why, what certreq is given
foreach ($scen in 'csr', 'csr_check') {
    foreach ($clm in @($false, $true)) {
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Script', (Join-Path $root 'roles/win_check_certs/files/win_cert_csr.ps1'), '-Scenario', $scen)
        if ($clm) { $a += '-Clm' }
        $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
        $count++
        $label = "certs/$scen" + $(if ($clm) { ' (constrained)' } else { '' })
        $bad = @()
        $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
        if (-not $line) { $fail++; Write-Host "FAIL - $label : no result line"; $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }; continue }
        $got = @{}
        foreach ($c in @((ConvertFrom-Json -InputObject $line.Substring(16)).facts.certs)) { $got[[string]$c.subject] = $c }
        $made = $(if ($scen -eq 'csr') { 'made' } else { 'would make' })
        $want = @{ 'CN=web01.corp.test, O="Smith, Jones", C=US' = $made; 'CN=Issuing CA, O=Test' = 'skipped'; 'CN=self01.corp.test' = 'skipped'
            'CN=upn.corp.test' = 'failed'; 'CN=pend.corp.test' = 'exists'; 'CN=old.corp.test' = 'skipped'; 'CN=ec.corp.test' = $made
            'CN=big.corp.test' = $made; 'CN=tls' = 'skipped' }
        foreach ($k in $want.Keys) { if ([string]$got[$k].status -ne $want[$k]) { $bad += "$k is '$($got[$k].status)', expected $($want[$k]) ($($got[$k].reason))" } }
        $web = $got['CN=web01.corp.test, O="Smith, Jones", C=US']
        if ((@($web.sans) -join ',') -ne 'web01.corp.test,web.corp.test,192.0.2.3') { $bad += "web01 names: $(@($web.sans) -join ',')" }
        if ($web.install -notmatch 'certreq -accept -machine' -or $web.install -notmatch 'https binding 0.0.0.0:443') { $bad += "web01 install: $($web.install)" }
        if ($got['CN=pend.corp.test'].csr -notmatch '^-----BEGIN CERTIFICATE REQUEST-----\nPENDING') { $bad += 'the pending CSR was not reused' }
        if ([string]$got['CN=ec.corp.test'].csr -match 'STALE') { $bad += 'a CSR without a known request was reused' }
        if ($got['CN=old.corp.test'].reason -notmatch 'a newer certificate') { $bad += 'old: ' + $got['CN=old.corp.test'].reason }
        if ($got['CN=self01.corp.test'].reason -notmatch 'win_cert_renew_self_signed') { $bad += 'self: ' + $got['CN=self01.corp.test'].reason }
        $inf = (@($out | Where-Object { $_ -like '###INF### *' }) -join "`n")
        if ($scen -eq 'csr') {
            if ($web.file_name -notmatch '^web01\.corp\.test-[0-9a-f]{8}\.csr$') { $bad += "web01 file name $($web.file_name)" }
            foreach ($t in 'Subject = "CN=web01.corp.test, O=""Smith, Jones"", C=US"', 'MachineKeySet = TRUE', '_continue_ = "dns=web.corp.test&"', '_continue_ = "ipaddress=192.0.2.3&"',
                'Exportable = FALSE', 'KeyAlgorithm = ECDSA_P384', 'KeyLength = 4096', 'OID=1.3.6.1.5.5.7.3.1') {
                if (-not $inf.Contains($t)) { $bad += "certreq was not given: $t" }
            }
        }
        elseif ($inf) { $bad += 'Check mode made a request' }
        if ($bad.Count) { $fail++; Write-Host "FAIL - $label : $($bad -join '; ')" } else { Write-Host "ok   - $label" }
    }
}
# The install (win_cert_install.ps1): scenario -> what the result must be, what the stand-ins must hold after
$old = 'A1' * 20; $new = 'B2' * 20
$installCases = @(
    @('install_pending', 'pending', '', ''),
    @('install_plan', 'plan', 'ready', 'Personal (certreq -accept -machine)|https ipport=0.0.0.0:443|https hostnameport=www.corp.test:443|winrm *+HTTPS|rdp RDP-Tcp'),
    @('install_plan_aaphttps', 'plan', 'by hand', 'AAP connects through it'),
    @('install_plan_sql', 'plan', 'by hand', 'SQL Server MSSQL16.MSSQLSERVER'),
    @('install_plan_gone', 'plan', 'held', 'the pending request (the new key) is gone'),
    @('install_plan_problems', 'plan', 'held', 'expired on 2026-01-01'),
    @('install_plan_done', 'plan', 'installed already', 'nothing uses the old one'),
    @('install', 'installed', 'installed', ''),
    @('install_check', 'installed', 'would install', ''),
    @('install_rollback', 'installed', 'rolled back', 'hostnameport=www.corp.test:443'),
    @('install_changed', 'installed', 'refused', 'changed since the approval'),
    @('install_accept_fails', 'installed', 'failed', 'certreq -accept -machine did not put it in Personal'),
    @('restore', 'restored', 'restored', ''),
    @('restore_check', 'restored', 'would restore', ''))
foreach ($clm in @($false, $true)) {
    foreach ($c in $installCases) {
        $scen, $what, $want, $text = $c
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Script', (Join-Path $root 'roles/win_check_certs/files/win_cert_install.ps1'), '-Scenario', $scen)
        if ($clm) { $a += '-Clm' }
        $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
        $count++
        $label = "certs/$scen" + $(if ($clm) { ' (constrained)' } else { '' })
        $bad = @()
        $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
        $st = @($out | Where-Object { $_ -like '###STATE### *' }) | Select-Object -Last 1
        if (-not $line -or -not $st) { $fail++; Write-Host "FAIL - $label : no result line"; $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }; continue }
        $facts = (ConvertFrom-Json -InputObject $line.Substring(16)).facts
        $state = ConvertFrom-Json -InputObject $st.Substring(12)
        $ssl = (@($state.ssl) -join ' ')
        $moved = ($ssl -eq "ipport=0.0.0.0:443=$new hostnameport=www.corp.test:443=$new ipport=0.0.0.0:8443=$('F6' * 20)") -and $state.rdp -eq $new -and $state.winrm -eq $new
        $kept = ($ssl -eq "ipport=0.0.0.0:443=$old hostnameport=www.corp.test:443=$old ipport=0.0.0.0:8443=$('F6' * 20)") -and $state.rdp -eq $old -and $state.winrm -eq $old
        $tree = @($state.tree) -join ' '
        if ($what -eq 'pending') {
            $p = @($facts.pending)
            if ($p.Count -ne 1 -or $p[0].stem -notmatch '^web01\.corp\.test-[0-9a-f]{8}$' -or $p[0].csr -notmatch '^-----BEGIN CERTIFICATE REQUEST-----\nWEB01') { $bad += "pending: $(ConvertTo-Json -Compress $p)" }
        }
        else {
            $r = @($facts.$what)[0]
            if ([string]$r.status -ne $want) { $bad += "status '$($r.status)', expected '$want' ($($r.reason))" }
            if ($text -and $what -eq 'plan' -and $want -eq 'ready' -and ((@($r.files) -join '|') -ne $text)) { $bad += "bindings: $(@($r.files) -join '|')" }
            elseif ($text -and $want -ne 'ready' -and ([string]$r.reason).IndexOf($text) -lt 0) { $bad += "reason: $($r.reason)" }
        }
        switch ($scen) {
            'install' {
                if (-not $moved) { $bad += "the bindings did not move: $ssl rdp $($state.rdp) winrm $($state.winrm)" }
                if (-not $state.accepted) { $bad += 'certreq -accept was not run' }
                if ((@($state.addstore) -join ' ') -ne ('D4' * 20 + '.cer')) { $bad += "intermediates added: $(@($state.addstore) -join ' ') (only the intermediate, never the root)" }
                if ((@($state.grants) -join '|') -ne 'newkey CORP\svc_web:(R)|newkey *S-1-5-20:(R)') { $bad += "key grants: $(@($state.grants) -join '|')" }
                if ($tree -notmatch "/renew/installed/[0-9.-]+-$old/$old\.json" -or $tree -match "/renew/$old\.json" -or $tree -notmatch "/renew/backup/[0-9.-]+-$old/backup\.json") { $bad += "files: $tree" }
                if ($tree -match 'incoming') { $bad += 'the incoming folder was left' }
            }
            'install_rollback' {
                if (-not $kept) { $bad += "not rolled back: $ssl rdp $($state.rdp) winrm $($state.winrm)" }
                if ($tree -notmatch "/renew/$old\.json") { $bad += 'the renewal no longer waits' }
            }
            { $_ -in @('install_check', 'install_changed', 'install_accept_fails') } {
                # install_changed: Remote Desktop moved to another certificate before the approved job - and stays there
                if ($scen -eq 'install_changed') { $kept = $kept -or (($ssl -like "ipport=0.0.0.0:443=$old *") -and $state.rdp -eq ('F6' * 20) -and $state.winrm -eq $old) }
                if (-not $kept) { $bad += "changed: $ssl rdp $($state.rdp) winrm $($state.winrm)" }
                if ($scen -eq 'install_check' -and ($state.accepted -or @($state.netsh).Count -or $tree -match 'backup')) { $bad += 'a dry run changed something' }
                if ($scen -eq 'install_accept_fails' -and @($state.netsh).Count) { $bad += 'a binding was touched after certreq failed' }
            }
            'restore' {
                if (-not $kept) { $bad += "not restored: $ssl rdp $($state.rdp) winrm $($state.winrm)" }
                if ($tree -notmatch "/renew/$old\.json") { $bad += "the renewal does not wait again: $tree" }
            }
            'restore_check' { if (-not $moved) { $bad += 'a dry run changed something' } }
        }
        if ($bad.Count) { $fail++; Write-Host "FAIL - $label : $($bad -join '; ')" } else { Write-Host "ok   - $label" }
    }
}
# Constrained Language Mode: TLS endpoints are reported as not checkable, not as an error
$out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File $runner -Script (Script 'certs') -Scenario 'certs_endpoints_clm' -Clm 2>&1 | ForEach-Object { [string]$_ })
$count++
if (($out -join "`n") -match '"id":"certs:endpoints"') { Write-Host 'ok   - certs/endpoints in constrained mode are reported, not an error' }
else { $fail++; Write-Host 'FAIL - certs/endpoints in constrained mode'; $out | Select-Object -Last 5 | ForEach-Object { Write-Host "       $_" } }
# The common part is the same in every check (it says so), and its Invoke-Native reads a program that writes to
# stderr instead of ending the script: Windows PowerShell 5.1 makes those lines errors under 'Stop' when 2>&1 is used.
$withCommon = @(Get-ChildItem -Path (Join-Path $root 'roles') -Recurse -Filter '*.ps1' | Where-Object {
        (Get-Content -LiteralPath $_.FullName -Raw) -match 'common to every site-automation Windows check' })
$parts = @($withCommon | ForEach-Object { [regex]::Match((Get-Content -LiteralPath $_.FullName -Raw),
            '(?s)# ---- common to every site-automation Windows check.*?# ---- end of the common part').Value } | Sort-Object -Unique)
$count++
if ($withCommon.Count -ge 12 -and $parts.Count -eq 1) { Write-Host "ok   - the common part is identical in all $($withCommon.Count) checks" }
else { $fail++; Write-Host "FAIL - the common part differs between the checks ($($parts.Count) versions in $($withCommon.Count) files)" }
$fn = [regex]::Match($parts[0], '(?ms)^function Invoke-Native.*?^\}').Value
$probe = Join-Path ([System.IO.Path]::GetTempPath()) ('invoke-native-' + [guid]::NewGuid().ToString('N') + '.ps1')
Set-Content -LiteralPath $probe -Encoding UTF8 -Value (@('param([string] $Shell)', "`$ErrorActionPreference = 'Stop'", $fn, @'
$say = "[Console]::Error.WriteLine('a warning on stderr'); 'line one'; exit "
$ok = Invoke-Native $Shell @('-NoProfile', '-Command', ($say + '0'))
$bad = Invoke-Native $Shell @('-NoProfile', '-Command', ($say + '3'))
$none = Invoke-Native 'no-such-program-here.exe' @('x')
'###OK### ' + $ok.Code + ' ' + (@($ok.Out) -join '|')
'###BAD### ' + $bad.Code + ' ' + (@($bad.Out) -join '|')
'###NONE### ' + $none.Code
'###STILL-STOP### ' + $ErrorActionPreference
'@) -join "`n")
$out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File $probe -Shell $shell 2>&1 | ForEach-Object { [string]$_ })
Remove-Item -LiteralPath $probe -Force
$o = $out -join "`n"
$count++
$okNative = ($o -match '###OK### 0 .*line one') -and ($o -match '###OK### 0 .*a warning on stderr') -and ($o -match '###BAD### 3 ')
$okNative = $okNative -and ($o -match '###NONE### -1') -and ($o -match '###STILL-STOP### Stop')
if ($okNative) {
    Write-Host 'ok   - Invoke-Native: stderr lines read (exit 0 and 3), a missing program is -1, the script goes on'
}
else { $fail++; Write-Host 'FAIL - Invoke-Native'; $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" } }
if ($fail) { Write-Host "$fail of $count test(s) failed"; exit 1 }
Write-Host "all $count tests passed"
