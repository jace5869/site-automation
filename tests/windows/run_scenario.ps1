# Run ONE Windows check script against made-up server data (a "scenario"), for the tests in
# test_win_checks.ps1. The Windows commands the scripts use (Get-CimInstance, Get-WinEvent,
# w32tm.exe, auditpol.exe, ...) are replaced by functions that return the scenario's data, so the
# checks can be tested anywhere PowerShell runs, also on Linux, and in Constrained Language Mode.
#   pwsh -File run_scenario.ps1 -Script roles/win_check_disk/files/win_disk.ps1 -Scenario disk_full [-Clm]
param(
    [Parameter(Mandatory = $true)][string] $Script,
    [Parameter(Mandatory = $true)][string] $Scenario,
    [switch] $Clm
)
$ErrorActionPreference = 'Stop'
$now = Get-Date
function O($h) { return [pscustomobject]$h }
function Ev($log, $id, $provider, $level, $hoursAgo, $message, $props) {
    $p = @()
    foreach ($v in @($props)) { $p += @([pscustomobject]@{ Value = $v }) }
    return [pscustomobject]@{ LogName = $log; Id = $id; ProviderName = $provider; Level = $level
        TimeCreated = $now.AddHours(-$hoursAgo); Message = $message; Properties = $p }
}
$GB = 1073741824
# ---- the scenarios --------------------------------------------------------------------------
$M = @{ cim = @{}; events = @(); logs = @{}; reg = @{}; paths = @(); exe = @{}; exit = @{} }
$cfg = @{}
switch ($Scenario -replace '^audit_fallback$', 'audit') {   # audit_fallback: the audit data without auditpol /backup
    'disk_full' {
        $cfg = @{ warn_pct = 85; crit_pct = 95; ignore = @('E:'); overrides = @(@{ drive = 'F:'; limits = @{ warn = 96; crit = 99 } }) }
        $M.cim.Win32_LogicalDisk = @(
            (O @{ DeviceID = 'C:'; VolumeName = 'OS'; Size = 100 * $GB; FreeSpace = 7 * $GB; DriveType = 3 }),
            (O @{ DeviceID = 'D:'; VolumeName = 'Data'; Size = 100 * $GB; FreeSpace = 2 * $GB; DriveType = 3 }),
            (O @{ DeviceID = 'E:'; VolumeName = 'Ignored'; Size = 100 * $GB; FreeSpace = 1 * $GB; DriveType = 3 }),
            (O @{ DeviceID = 'F:'; VolumeName = 'Override'; Size = 100 * $GB; FreeSpace = 5 * $GB; DriveType = 3 }),
            (O @{ DeviceID = 'G:'; VolumeName = 'Fine'; Size = 100 * $GB; FreeSpace = 50 * $GB; DriveType = 3 }))
    }
    'services' {
        $cfg = @{ required = @('EventLog', 'W32Time'); required_extra = @('NoSuchSvc'); ignore = @('gupdate', 'CDPUserSvc_*'); crash_hours = 24; crash_warn = 3 }
        $M.cim.Win32_Service = @(
            (O @{ Name = 'EventLog'; DisplayName = 'Windows Event Log'; State = 'Running'; StartMode = 'Auto'; ExitCode = 0 }),
            (O @{ Name = 'W32Time'; DisplayName = 'Windows Time'; State = 'Stopped'; StartMode = 'Auto'; ExitCode = 0 }),
            (O @{ Name = 'Spooler'; DisplayName = 'Print Spooler'; State = 'Stopped'; StartMode = 'Auto'; ExitCode = 1067 }),
            (O @{ Name = 'gupdate'; DisplayName = 'Google Update'; State = 'Stopped'; StartMode = 'Auto'; ExitCode = 1 }),
            (O @{ Name = 'CDPUserSvc_4a2b'; DisplayName = 'CDP user'; State = 'Stopped'; StartMode = 'Auto'; ExitCode = 1 }),
            (O @{ Name = 'Delayed'; DisplayName = 'Trigger start'; State = 'Stopped'; StartMode = 'Auto'; ExitCode = 1077 }),
            (O @{ Name = 'Clean'; DisplayName = 'Stopped cleanly'; State = 'Stopped'; StartMode = 'Auto'; ExitCode = 0 }))
        $M.events = @(
            (Ev 'System' 7031 'Service Control Manager' 2 1 'crashed' @('Foo Service', 1)),
            (Ev 'System' 7034 'Service Control Manager' 2 2 'crashed' @('Foo Service', 2)),
            (Ev 'System' 7031 'Service Control Manager' 2 3 'crashed' @('Foo Service', 3)),
            (Ev 'System' 7031 'Service Control Manager' 2 3 'crashed' @('Bar Service', 1)))
    }
    'performance' {
        $cfg = @{ cpu_warn_pct = 90; cpu_crit_pct = 98; mem_warn_pct = 90; mem_crit_pct = 97; pagefile_warn_pct = 80 }
        $M.cim.Win32_PerfFormattedData_PerfOS_Processor = @(O @{ Name = '_Total'; PercentProcessorTime = 95 })
        $M.cim.Win32_ComputerSystem = @(O @{ NumberOfLogicalProcessors = 4; PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.cim.Win32_PerfFormattedData_PerfProc_Process = @(
            (O @{ Name = '_Total'; PercentProcessorTime = 380 }), (O @{ Name = 'Idle'; PercentProcessorTime = 20 }),
            (O @{ Name = 'sqlservr'; PercentProcessorTime = 300 }), (O @{ Name = 'w3wp'; PercentProcessorTime = 60 }))
        $M.cim.Win32_OperatingSystem = @(O @{ TotalVisibleMemorySize = 16777216; FreePhysicalMemory = 314572; LastBootUpTime = $now.AddDays(-12); BuildNumber = '17763'; Caption = 'Windows Server 2019' })
        $M.cim.Win32_PageFileUsage = @(O @{ Name = 'C:\pagefile.sys'; AllocatedBaseSize = 4096; CurrentUsage = 3500 })
        $M.procs = @((O @{ ProcessName = 'sqlservr'; WorkingSet64 = 12 * $GB }), (O @{ ProcessName = 'w3wp'; WorkingSet64 = 2 * $GB }))
    }
    'time_cmos' {
        $M.cim.Win32_Service = @(O @{ Name = 'W32Time'; State = 'Running'; StartMode = 'Auto'; ExitCode = 0 })
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.exe['w32tm.exe /query /status'] = @('Leap Indicator: 3(not synchronized)', 'Stratum: 0 (unspecified)', 'Source: Local CMOS Clock', 'Last Successful Sync Time: unspecified')
    }
    'time_offset' {
        $M.cim.Win32_Service = @(O @{ Name = 'W32Time'; State = 'Running'; StartMode = 'Auto'; ExitCode = 0 })
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.exe['w32tm.exe /query /status'] = @('Leap Indicator: 0(no warning)', 'Stratum: 4', 'Source: dc01.corp.test,0x9', ('Last Successful Sync Time: ' + $now.AddDays(-2).ToString()))
        $M.exe['w32tm.exe /stripchart /computer:dc01.corp.test /samples:1 /dataonly'] = @('Tracking dc01.corp.test [192.0.2.1:123].', 'The current time is 9/28/2026 12:00:00 PM.', '12:00:00, +06.5000000s')
    }
    'time_stopped' {
        $M.cim.Win32_Service = @(O @{ Name = 'W32Time'; State = 'Stopped'; StartMode = 'Manual'; ExitCode = 0 })
    }
    'network' {
        $cfg = @{ dns_names = @('good.test', 'bad.test'); tcp = @(@{ name = 'closed port'; host = '127.0.0.1'; port = 1 }); secure_channel = $true; timeout_ms = 2000 }
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.cim.Win32_NetworkAdapterConfiguration = @(O @{ IPEnabled = $true; DefaultIPGateway = $null; DNSServerSearchOrder = @('192.0.2.1') })
        $M.dnsFail = @('bad.test')
        $M.exe['nltest.exe /sc_query:corp.test'] = @('Flags: 0', 'Trusted DC Name', 'Trusted DC Connection Status Status = 1311 0x51f ERROR_NO_LOGON_SERVERS', 'The command completed successfully')
        $M.tnc = $false
    }
    'network_ok' {
        $cfg = @{ dns_names = @('good.test'); secure_channel = $true }
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.cim.Win32_NetworkAdapterConfiguration = @(O @{ IPEnabled = $true; DefaultIPGateway = @('192.0.2.254'); DNSServerSearchOrder = @('192.0.2.1') })
        $M.dnsFail = @()
        $M.exe['nltest.exe /sc_query:corp.test'] = @('Flags: 30 HAS_IP  HAS_TIMESERV', 'Trusted DC Name \\dc01.corp.test', 'Trusted DC Connection Status Status = 0 0x0 NERR_Success', 'The command completed successfully')
    }
    'eventlog' {
        $cfg = @{ hours = 24; error_warn = 4 }
        $M.events = @(
            (Ev 'System' 6008 'EventLog' 2 3 'The previous system shutdown at 3:00 AM was unexpected.' @()),
            (Ev 'System' 7 'disk' 2 2 'The device, \Device\Harddisk1\DR1, has a bad block.' @()),
            (Ev 'Security' 1102 'Microsoft-Windows-Eventlog' 4 1 'The audit log was cleared.' @()),
            (Ev 'Application' 1000 'Application Error' 2 1 'Faulting application x.exe' @()),
            (Ev 'Application' 1000 'Application Error' 2 5 'Faulting application x.exe' @()),
            (Ev 'System' 10016 'DCOM' 2 4 'DCOM permission' @()),
            (Ev 'System' 6008 'EventLog' 2 72 'old, outside the window' @()))
        $M.logs = @{ System = (O @{ IsLogFull = $false; LogMode = 'Circular'; MaximumSizeInBytes = 33554432 })
            Application = (O @{ IsLogFull = $false; LogMode = 'Circular'; MaximumSizeInBytes = 33554432 })
            Security = (O @{ IsLogFull = $true; LogMode = 'Retain'; MaximumSizeInBytes = 20971520 }) }
    }
    'security' {
        $cfg = @{ av = 'auto'; av_signature_max_days = 7; av_services = @('masvc'); firewall_profiles = @('Domain', 'Private', 'Public'); smb1 = $true; rdp_nla = $true; uac = $true; legacy_tls = $true }
        $M.mp = O @{ AMServiceEnabled = $true; AMRunningMode = 'Normal'; RealTimeProtectionEnabled = $false; AntivirusSignatureAge = 10; AntivirusSignatureVersion = '1.400.1.0' }
        $M.cim.Win32_Service = @()
        $M.fw = @((O @{ Name = 'Domain'; Enabled = 'True' }), (O @{ Name = 'Private'; Enabled = 'True' }), (O @{ Name = 'Public'; Enabled = 'False' }))
        $M.smb = O @{ EnableSMB1Protocol = $true }
        $M.reg['HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'] = O @{ fDenyTSConnections = 0 }
        $M.reg['HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'] = O @{ UserAuthentication = 0 }
        $M.reg['HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'] = O @{ EnableLUA = 1 }
        $M.cim.Win32_OperatingSystem = @(O @{ BuildNumber = '17763' })
    }
    'security_passive' {
        $cfg = @{ av = 'auto'; legacy_tls = $true }
        $M.mp = O @{ AMServiceEnabled = $true; AMRunningMode = 'Passive'; RealTimeProtectionEnabled = $false; AntivirusSignatureAge = 90 }
        $M.fw = @((O @{ Name = 'Domain'; Enabled = 'True' }), (O @{ Name = 'Private'; Enabled = 'True' }), (O @{ Name = 'Public'; Enabled = 'True' }))
        $M.smb = O @{ EnableSMB1Protocol = $false }
        $M.cim.Win32_OperatingSystem = @(O @{ BuildNumber = '20348' })
        $M.reg['HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.0\Server'] = O @{ Enabled = 0 }
        $M.reg['HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.1\Server'] = O @{ Enabled = 0; DisabledByDefault = 1 }
    }
    'audit' {
        $cfg = @{ subcategories = @(
                @{ name = 'Logon'; guid = '0CCE9215-69AE-11D9-BED3-505054503030'; need = 'Success and Failure' },
                @{ name = 'Credential Validation'; guid = '{0CCE923F-69AE-11D9-BED3-505054503030}'; need = 'Success and Failure' },
                @{ name = 'Account Lockout'; guid = '0CCE9217-69AE-11D9-BED3-505054503030'; need = 'Failure' })
            security_log_min_kb = 196608; application_log_min_kb = 32768; system_log_min_kb = 32768; forwarder_services = @('SplunkForwarder') }
        $M.exe['auditpol.exe /get /category:* /r'] = @(
            'Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting,Exclusion Setting', '',
            'SRV01,System,Logon,{0CCE9215-69AE-11D9-BED3-505054503030},Success,',
            'SRV01,System,Credential Validation,{0CCE923F-69AE-11D9-BED3-505054503030},Success and Failure,',
            'SRV01,System,Account Lockout,{0CCE9217-69AE-11D9-BED3-505054503030},Success and Failure,')
        if ($Scenario -eq 'audit') {
            # a server in German: the words differ, the numbers (1 Success, 2 Failure, 3 both) do not
            $M.exe['auditpol.exe /backup'] = @(
                'Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting,Exclusion Setting,Setting Value',
                'SRV01,System,Anmelden,{0CCE9215-69AE-11D9-BED3-505054503030},Erfolg,,1',
                'SRV01,System,Ueberpruefung der Anmeldeinformationen,{0CCE923F-69AE-11D9-BED3-505054503030},Erfolg und Fehler,,3',
                'SRV01,System,Kontosperrung,{0CCE9217-69AE-11D9-BED3-505054503030},Erfolg und Fehler,,3')
        }
        $M.logs = @{ Security = (O @{ MaximumSizeInBytes = 20971520 }); Application = (O @{ MaximumSizeInBytes = 33554432 }); System = (O @{ MaximumSizeInBytes = 33554432 }) }
        $M.cim.Win32_Service = @()
    }
    'accounts' {
        $cfg = @{ password_max_age_days = 60; inactive_days = 35; password_never_expires_allowed = @('Administrator'); inactive_allowed = @(); admins_allowed = @('*\Administrator', 'CORP\Domain Admins') }
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.users = @(
            (O @{ Name = 'Administrator'; SID = 'S-1-5-21-1-2-3-500'; Enabled = $true; PasswordExpires = $null; PasswordLastSet = $now.AddDays(-400); LastLogon = $now.AddDays(-1) }),
            (O @{ Name = 'Guest'; SID = 'S-1-5-21-1-2-3-501'; Enabled = $true; PasswordExpires = $null; PasswordLastSet = $null; LastLogon = $null }),
            (O @{ Name = 'svc_old'; SID = 'S-1-5-21-1-2-3-1001'; Enabled = $true; PasswordExpires = $now.AddDays(30); PasswordLastSet = $now.AddDays(-10); LastLogon = $null }),
            (O @{ Name = 'off'; SID = 'S-1-5-21-1-2-3-1002'; Enabled = $false; PasswordExpires = $null; PasswordLastSet = $now.AddDays(-900); LastLogon = $null }))
        $M.adminsFail = $true
        $M.cim.Win32_Group = @(O @{ Name = 'Administrators'; SID = 'S-1-5-32-544' })
        $M.exe['net.exe localgroup Administrators'] = @('Alias name     Administrators', 'Comment        Administrators have complete and unrestricted access', '', 'Members', '',
            '-------------------------------------------------------------------------------', 'SRV01\Administrator', 'CORP\Domain Admins', 'CORP\jdoe', 'The command completed successfully.')
    }
    'accounts_dc' {
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 5; Domain = 'corp.test' })
    }
    'certs' {
        $cfg = @{ warn_days = 30; crit_days = 7; expired_report_days = 30; stores = @('Cert:\LocalMachine\My'); ignore_subjects = @('CN=ignore*'); endpoints = @() }
        $a = 'A' * 40; $b = 'B' * 40; $c = 'C' * 40; $d = 'D' * 40; $e = 'E' * 40
        $M.exe['netsh.exe http show sslcert'] = @('SSL Certificate bindings:', '', '    IP:port                      : 0.0.0.0:443', "    Certificate Hash             : $($a.ToLower())", '    Application ID               : {4dc3e181-e14b-4a21-b022-59fc669b0914}')
        $M.wsman = @(O @{ PSPath = 'wsman-https' })
        $M.reg['wsman-https'] = O @{ certThumbprint = " $b " }
        $M.certs = @{ 'Cert:\LocalMachine\My' = @(
                (O @{ Subject = 'CN=web01.corp.test'; Thumbprint = $a; NotAfter = $now.AddDays(5) }),
                (O @{ Subject = 'CN=winrm.corp.test'; Thumbprint = $b; NotAfter = $now.AddDays(-700) }),
                (O @{ Subject = 'CN=app.corp.test'; Thumbprint = $c; NotAfter = $now.AddDays(20) }),
                (O @{ Subject = 'CN=old.corp.test'; Thumbprint = $d; NotAfter = $now.AddDays(-700) }),
                (O @{ Subject = 'CN=ignore.me'; Thumbprint = $e; NotAfter = $now.AddDays(1) })) }
    }
    'certs_trust' {
        # "Automatically select the store": a .cer lands in Trusted Root / Intermediate CAs / Other People
        $cfg = @{ warn_days = 30; crit_days = 7; expired_report_days = 30; notice_days = 60; stores = @('Cert:\LocalMachine\My'); trust = $true; trust_skip_stores = @('Disallowed', 'AuthRoot', 'REQUEST', 'SMS'); endpoints = @() }
        $f = 'F' * 40; $g = 'G' * 40; $h = 'ABCD' * 10; $i = '1' * 40; $j = '2' * 40; $k = '3' * 40; $l = '4' * 40
        $M.exe['netsh.exe http show sslcert'] = @('SSL Certificate bindings:', '', '    IP:port                      : 0.0.0.0:8443', "    Certificate Hash             : $h", '    Application ID               : {4dc3e181-e14b-4a21-b022-59fc669b0914}')
        $M.certStores = @((O @{ Name = 'My' }), (O @{ Name = 'Root' }), (O @{ Name = 'CA' }), (O @{ Name = 'AddressBook' }), (O @{ Name = 'Disallowed' }), (O @{ Name = 'AuthRoot' }))
        $M.certs = @{
            'Cert:\LocalMachine\My'          = @()
            'Cert:\LocalMachine\Root'        = @(
                (O @{ Subject = 'CN=test26.corp.test'; Thumbprint = $f; NotAfter = $now.AddDays(26); HasPrivateKey = $false }),
                (O @{ Subject = 'CN=Old Vendor Root'; Thumbprint = $i; NotAfter = $now.AddDays(-4000); HasPrivateKey = $false }),
                (O @{ Subject = 'CN=Far Root'; Thumbprint = $j; NotAfter = $now.AddDays(3000); HasPrivateKey = $false }))
            'Cert:\LocalMachine\CA'          = @(
                (O @{ Subject = 'CN=keyed.corp.test'; Thumbprint = $g; NotAfter = $now.AddDays(20); HasPrivateKey = $true }),
                (O @{ Subject = 'CN=bound.corp.test'; Thumbprint = $h; NotAfter = $now.AddDays(3); HasPrivateKey = $false }))
            'Cert:\LocalMachine\AddressBook' = @(O @{ Subject = 'CN=peer.corp.test'; Thumbprint = $k; NotAfter = $now.AddDays(10); HasPrivateKey = $false })
            'Cert:\LocalMachine\Disallowed'  = @(O @{ Subject = 'CN=revoked'; Thumbprint = $l; NotAfter = $now.AddDays(1); HasPrivateKey = $true })
            'Cert:\LocalMachine\AuthRoot'    = @(O @{ Subject = 'CN=wu'; Thumbprint = $l; NotAfter = $now.AddDays(1); HasPrivateKey = $true })
        }
    }
    { $_ -in @('trust', 'trust_remove', 'trust_remove_dry') } {
        # the Windows trust check against real certificates (tests/trust/fixtures): the dropped root and Sectigo's
        # AAA root in Trusted Root, a compromised intermediate in Intermediate CAs, the root again in AuthRoot (Windows
        # Update's store), AAA in Disallowed (blocked); a Java cacerts (JKS, made here) with the root; two certifi
        # bundles (one with the root and AAA, pip's own with the root only) - under a real folder the fakes pass through
        $fx = Join-Path $PSScriptRoot '..\trust\fixtures'
        function RealCert([string] $n) {
            $pem = Get-Content -LiteralPath (Join-Path $fx "$n.pem") -Raw
            return (New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (, [Convert]::FromBase64String(($pem -replace '-----[A-Z ]+-----', '' -replace '\s', ''))))
        }
        $root = RealCert 'emsign-ecc-root-ca-g3'; $aaa = RealCert 'aaa-certificate-services'; $dv = RealCert 'dv-g3b-emudhra'
        $pem = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\fixtures\pki\root.pem') -Raw
        $harmless = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (, [Convert]::FromBase64String(($pem -replace '-----[A-Z ]+-----', '' -replace '\s', '')))
        # the list, from the role's vars file (name, sha256, spki_sha256, severity, why: one "key: value" per line)
        $known = @(); $cur = $null
        foreach ($l in @(Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\..\roles\check_trust\vars\main.yml'))) {
            if ($l -match '^\s*-\s+name:\s*(.*)$') { if ($cur) { $known += @($cur) }; $cur = @{ name = $Matches[1].Trim().Trim('"') }; continue }
            if ($cur -and $l -match '^\s+(sha256|spki_sha256|severity|why|kind):\s*(.*)$') { $cur[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
        }
        if ($cur) { $known += @($cur) }
        $fs = Join-Path ([System.IO.Path]::GetTempPath()) 'sa-win-trust'
        if (Microsoft.PowerShell.Management\Test-Path -LiteralPath $fs) { Microsoft.PowerShell.Management\Remove-Item -LiteralPath $fs -Recurse -Force }
        foreach ($d in 'java\lib\security', 'py\Lib\site-packages\certifi', 'py\Lib\site-packages\pip\_vendor\certifi', 'backup') { $null = New-Item -ItemType Directory -Path (Join-Path $fs $d) -Force }
        $rd = [byte[]]$root.RawData
        $len = [byte[]][System.BitConverter]::GetBytes([int32]$rd.Length)
        [array]::Reverse($len)                  # big-endian
        $jks = [System.Collections.Generic.List[byte]]::new()
        foreach ($part in @(([byte[]]@(0xFE, 0xED, 0xFE, 0xED, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 2, 0, 6)), ([byte[]][char[]]'emsign'), ([byte[]]@(0, 0, 0, 0, 0, 0, 0, 0, 0, 5)), ([byte[]][char[]]'X.509'), $len, $rd, ([byte[]]::new(20)))) { $jks.AddRange([byte[]]$part) }
        $jks = $jks.ToArray()
        $jf = Join-Path $fs 'java\lib\security\cacerts'
        if ($PSVersionTable.PSVersion.Major -ge 6) { Set-Content -LiteralPath $jf -Value $jks -AsByteStream } else { Set-Content -LiteralPath $jf -Value $jks -Encoding Byte }
        Set-Content -LiteralPath (Join-Path $fs 'java\release') -Value 'JAVA_VERSION="17.0.9"'
        Set-Content -LiteralPath (Join-Path $fs 'py\Lib\site-packages\certifi\cacert.pem') -Value ((Get-Content -LiteralPath (Join-Path $fx 'emsign-ecc-root-ca-g3.pem') -Raw) + (Get-Content -LiteralPath (Join-Path $fx 'aaa-certificate-services.pem') -Raw))
        Set-Content -LiteralPath (Join-Path $fs 'py\Lib\site-packages\certifi\__init__.py') -Value '__version__ = "2025.08.03"'
        Set-Content -LiteralPath (Join-Path $fs 'py\Lib\site-packages\pip\_vendor\certifi\cacert.pem') -Value (Get-Content -LiteralPath (Join-Path $fx 'emsign-ecc-root-ca-g3.pem') -Raw)
        $M.fs = $fs
        $cfg = @{ known = $known; stores = @('Cert:\LocalMachine\Root', 'Cert:\LocalMachine\CA', 'Cert:\LocalMachine\AuthRoot', 'Cert:\CurrentUser\Root'); block_stores = @('Cert:\LocalMachine\Disallowed')
            java = $true; java_dirs = @((Join-Path $fs 'java')); python = $true; python_dirs = @((Join-Path $fs 'py')); backup_dir = (Join-Path $fs 'backup')
            remove = ($Scenario -ne 'trust'); dry_run = ($Scenario -eq 'trust_remove_dry'); severities = @('critical', 'warning'); keystore_password = 'changeit' }
        $M.certs = @{
            'Cert:\LocalMachine\Root'       = @($root, $aaa, $harmless)
            'Cert:\LocalMachine\CA'         = @($dv, $harmless)
            'Cert:\LocalMachine\AuthRoot'   = @($root)
            'Cert:\LocalMachine\Disallowed' = @($aaa)
        }
    }
    'certs_meta' {
        # real certificates (tests/fixtures/pki): what the inventory reads from each - issuer, names, key, tier ...
        $cfg = @{ warn_days = 30; crit_days = 7; expired_report_days = 30; notice_days = 60; stores = @('Cert:\LocalMachine\My'); endpoints = @() }
        $pki = Join-Path $PSScriptRoot '..\fixtures\pki'
        $real = @()
        foreach ($n in 'root', 'int', 'web', 'self') {
            $pem = Get-Content -LiteralPath (Join-Path $pki "$n.pem") -Raw
            $b64 = ($pem -replace '-----[A-Z ]+-----', '' -replace '\s', '')
            $real += @(New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (, [Convert]::FromBase64String($b64)))
        }
        $M.certs = @{ 'Cert:\LocalMachine\My' = $real }
    }
    { $_ -in @('certs_rdp', 'certs_rdp_on') } {
        # Remote Desktop: its own store, the certificate RDP uses, one for Remote Desktop only - left out unless remote_desktop
        $cfg = @{ warn_days = 30; crit_days = 7; expired_report_days = 30; notice_days = 60; stores = @('Cert:\LocalMachine\My', 'Cert:\LocalMachine\Remote Desktop'); trust = $true; trust_skip_stores = @('Disallowed', 'AuthRoot', 'REQUEST', 'SMS'); remote_desktop = ($Scenario -eq 'certs_rdp_on'); endpoints = @() }
        $r = '5' * 40; $u = '6' * 40; $o = '7' * 40; $w = '8' * 40; $x = '9' * 40
        $M.exe['netsh.exe http show sslcert'] = @('SSL Certificate bindings:', '', '    IP:port                      : 0.0.0.0:443', "    Certificate Hash             : $x", '    Application ID               : {4dc3e181-e14b-4a21-b022-59fc669b0914}')
        $M.cim.Win32_TSGeneralSetting = @(O @{ TerminalName = 'RDP-Tcp'; SSLCertificateSHA1Hash = $u })
        $M.certStores = @((O @{ Name = 'My' }), (O @{ Name = 'Remote Desktop' }), (O @{ Name = 'Root' }))
        $rdpEku = @(O @{ ObjectId = '1.3.6.1.4.1.311.54.1.2' })
        $M.certs = @{
            'Cert:\LocalMachine\My'             = @(
                (O @{ Subject = 'CN=rdpused.corp.test'; Thumbprint = $u; NotAfter = $now.AddDays(10) }),
                (O @{ Subject = 'CN=rdponly.corp.test'; Thumbprint = $o; NotAfter = $now.AddDays(10); EnhancedKeyUsageList = $rdpEku }),
                (O @{ Subject = 'CN=web01.corp.test'; Thumbprint = $w; NotAfter = $now.AddDays(10); EnhancedKeyUsageList = @((O @{ ObjectId = '1.3.6.1.5.5.7.3.1' }), $rdpEku[0]) }),
                (O @{ Subject = 'CN=bound.corp.test'; Thumbprint = $x; NotAfter = $now.AddDays(10); EnhancedKeyUsageList = $rdpEku }))
            'Cert:\LocalMachine\Remote Desktop' = @(O @{ Subject = 'CN=SRV01'; Thumbprint = $r; NotAfter = $now.AddDays(10); HasPrivateKey = $true })
            'Cert:\LocalMachine\Root'           = @()
        }
    }
    { $_ -in @('csr', 'csr_check') } {
        # the renewal (win_cert_csr.ps1): certreq is a stand-in; the CSR folder is a real temporary one
        $M.fs = Join-Path ([System.IO.Path]::GetTempPath()) ('sa-csr-' + (Get-Random))
        $null = New-Item -ItemType Directory -Path $M.fs
        function San([string[]] $names) {
            $body = @()
            foreach ($n in $names) {
                $k, $v = $n -split ':', 2
                if ($k -eq 'dns') { $b = @([System.Text.Encoding]::ASCII.GetBytes($v)); $body += @(0x82, $b.Count) + $b }
                elseif ($k -eq 'ip') { $b = @($v -split '\.' | ForEach-Object { [int]$_ }); $body += @(0x87, 4) + $b }
                elseif ($k -eq 'upn') { $body += @(0xA0, 3, 1, 2, 3) }
            }
            return , (@(0x30, $body.Count) + $body)
        }
        function Ext([string] $oid, $raw) { return (O @{ Oid = (O @{ Value = $oid }); RawData = $raw }) }
        function Cert([string] $subject, [string] $tp, [int] $days, $exts, [string] $issuer = 'CN=Test CA, O=Test', $pk = $null) {
            if ($null -eq $pk) { $pk = O @{ Oid = (O @{ Value = '1.2.840.113549.1.1.1' }); EncodedKeyValue = (O @{ RawData = @(1..270) }) } }
            return (O @{ Subject = $subject; Issuer = $issuer; Thumbprint = $tp; NotAfter = $now.AddDays($days); HasPrivateKey = $true
                    Extensions = @($exts); PublicKey = $pk; EnhancedKeyUsageList = @(O @{ ObjectId = '1.3.6.1.5.5.7.3.1' }) })
        }
        $t = @{}; foreach ($x in 'web', 'ca', 'self', 'upn', 'pend', 'old', 'new', 'ec', 'big') { $t[$x] = (('{0:X2}' -f $t.Count) * 20) }
        $ec = O @{ Oid = (O @{ Value = '1.2.840.10045.2.1' }); EncodedParameters = (O @{ RawData = @(0x06, 0x05, 0x2B, 0x81, 0x04, 0x00, 0x22) }) }
        $big = O @{ Oid = (O @{ Value = '1.2.840.113549.1.1.1' }); EncodedKeyValue = (O @{ RawData = @(1..526) }) }
        $M.certs = @{
            'Cert:\LocalMachine\My'      = @(
                (Cert 'CN=web01.corp.test, O="Smith, Jones", C=US' $t.web 10 @((Ext '2.5.29.17' (San @('dns:web01.corp.test', 'dns:web.corp.test', 'ip:192.0.2.3'))))),
                (Cert 'CN=Issuing CA, O=Test' $t.ca 10 @((Ext '2.5.29.19' @(0x30, 0x03, 0x01, 0x01, 0xFF)))),
                (Cert 'CN=self01.corp.test' $t.self 10 @() 'CN=self01.corp.test'),
                (Cert 'CN=upn.corp.test' $t.upn 10 @((Ext '2.5.29.17' (San @('dns:upn.corp.test', 'upn:x'))))),
                (Cert 'CN=pend.corp.test' $t.pend 10 @()),
                (Cert 'CN=old.corp.test' $t.old 10 @()),
                (Cert 'CN=old.corp.test' $t.new 400 @()),
                (Cert 'CN=ec.corp.test' $t.ec 10 @() 'CN=Test CA, O=Test' $ec),
                (Cert 'CN=big.corp.test' $t.big 10 @() 'CN=Test CA, O=Test' $big))
            'Cert:\LocalMachine\REQUEST' = @(O @{ Thumbprint = ('AB' * 20); Subject = 'CN=pend.corp.test' })
        }
        # an earlier run's pending request for pend.corp.test
        $pcsr = Join-Path $M.fs 'pend.corp.test-earlier.csr'
        Microsoft.PowerShell.Management\Set-Content -LiteralPath $pcsr -Value "-----BEGIN NEW CERTIFICATE REQUEST-----`r`nPENDING`r`n-----END NEW CERTIFICATE REQUEST-----"
        ConvertTo-Json @{ request = ('AB' * 20); csr_path = $pcsr } | Microsoft.PowerShell.Management\Set-Content -LiteralPath (Join-Path $M.fs "$($t.pend).json")
        # an earlier run whose request's thumbprint was not known (request ''): not a pending request - a new CSR
        $scsr = Join-Path $M.fs 'ec.corp.test-earlier.csr'
        Microsoft.PowerShell.Management\Set-Content -LiteralPath $scsr -Value "-----BEGIN NEW CERTIFICATE REQUEST-----`r`nSTALE`r`n-----END NEW CERTIFICATE REQUEST-----"
        ConvertTo-Json @{ request = ''; csr_path = $scsr } | Microsoft.PowerShell.Management\Set-Content -LiteralPath (Join-Path $M.fs "$($t.ec).json")
        $cands = @()
        foreach ($x in 'web', 'ca', 'self', 'upn', 'pend', 'old', 'ec', 'big') {
            $c = @($M.certs['Cert:\LocalMachine\My'] | Where-Object { $_.Thumbprint -eq $t[$x] })[0]
            $w = 'LocalMachine\My'; if ($x -eq 'web') { $w += ', used by https 0.0.0.0:443' }
            $cands += @(@{ id = 'certs:' + $t[$x]; store = 'Cert:\LocalMachine\My'; where = $w; subject = $c.Subject; days = 10; expires = '2026-01-01'; severity = 'warning' })
        }
        $cands += @(@{ id = 'certs:tls:127.0.0.1:8443'; store = ''; where = 'TLS app'; subject = 'CN=tls'; days = 5; expires = '2026-01-01'; severity = 'critical' })
        $cfg = @{ certs = $cands; dir = $M.fs; key_bits = 2048; digest = 'sha256'; max = 10; check_mode = ($Scenario -eq 'csr_check') }
    }
    'certs_endpoints_clm' {
        $cfg = @{ stores = @(); endpoints = @(@{ name = 'IIS'; host = '127.0.0.1'; port = 443 }) }
        $M.exe['netsh.exe http show sslcert'] = @()
    }
    'patching' {
        $cfg = @{ warn_days = 35; crit_days = 60; reboot_pending = $true; search_pending = $false }
        $M.paths = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
        $M.hotfix = @((O @{ HotFixID = 'KB5000001'; InstalledOn = $now.AddDays(-70) }), (O @{ HotFixID = 'KB4000001'; InstalledOn = $now.AddDays(-200) }))
        $M.cim.Win32_OperatingSystem = @(O @{ LastBootUpTime = $now.AddDays(-80) })
    }
    'patching_ok' {
        $M.paths = @()
        $M.hotfix = @(O @{ HotFixID = 'KB5000009'; InstalledOn = $now.AddDays(-5) })
    }
    default { throw "unknown scenario $Scenario" }
}
# ---- the stand-ins for the Windows commands ------------------------------------------------
function Test-CimFilter($o, [string] $f) {
    if (-not $f) { return $true }
    foreach ($part in ($f -split '\s+AND\s+')) {
        if ($part -match "^\s*(\w+)\s*(<>|=)\s*'?([^']*)'?\s*$") {
            $v = [string]$o.($Matches[1])
            $eq = ($v -eq $Matches[3]) -or ($Matches[3] -eq 'TRUE' -and $v -eq 'True')
            if ($Matches[2] -eq '=' -and -not $eq) { return $false }
            if ($Matches[2] -eq '<>' -and $eq) { return $false }
        }
    }
    return $true
}
function Get-CimInstance {
    param([string] $ClassName, [string] $Filter, [string] $Namespace, $ErrorAction)
    $data = $M.cim[$ClassName]
    if ($null -eq $data) { if ($ErrorAction -eq 'Stop') { throw "Invalid class $ClassName" } ; return }
    @($data) | Where-Object { Test-CimFilter $_ $Filter }
}
function Invoke-CimMethod { throw 'Invalid namespace' }
function Get-WinEvent {
    param($FilterHashtable, [string[]] $ListLog, $MaxEvents, $ErrorAction)
    if ($ListLog) {
        foreach ($l in $ListLog) { if ($M.logs.ContainsKey($l)) { $M.logs[$l] } elseif ($ErrorAction -eq 'Stop') { throw "no log $l" } }
        return
    }
    $h = $FilterHashtable
    $r = @($M.events | Where-Object {
            (@($h.LogName) -contains $_.LogName) -and
            (-not $h.Id -or @($h.Id) -contains $_.Id) -and
            (-not $h.ProviderName -or @($h.ProviderName) -contains $_.ProviderName) -and
            (-not $h.Level -or @($h.Level) -contains $_.Level) -and
            (-not $h.StartTime -or $_.TimeCreated -ge $h.StartTime) })
    if ($r.Count -eq 0 -and $ErrorAction -eq 'Stop') { throw 'No events were found that match the specified selection criteria.' }
    $r
}
function Get-Process { $M.procs }
function Start-Sleep { }
function Resolve-DnsName { param([string] $Name) if ($M.dnsFail -contains $Name) { throw "$Name : DNS name does not exist" } }
function Test-NetConnection { return [bool]$M.tnc }
function Get-MpComputerStatus { if ($null -eq $M.mp) { throw 'not available' } ; $M.mp }
function Get-NetFirewallProfile { $M.fw }
function Get-SmbServerConfiguration { if ($null -eq $M.smb) { throw 'no smb' } ; $M.smb }
function Get-ItemProperty { param([string] $Path, [string] $Name, $ErrorAction) $M.reg[$Path] }
function Test-Path {
    param([string] $Path, [string] $LiteralPath, [string] $PathType = 'Any')
    $p = $Path; if ($LiteralPath) { $p = $LiteralPath }
    # -PathType too: Windows PowerShell 5.1's Get-FileHash is a script that asks Test-Path -PathType Container
    if ($M.fs -and $p.StartsWith($M.fs)) { return (Microsoft.PowerShell.Management\Test-Path -LiteralPath $p -PathType $PathType) }
    # a store itself exists, as on a server ("Cert:\LocalMachine\REQUEST\" - a request with no thumbprint)
    if ($p -match '^Cert:\\[^\\]+\\[^\\]+\\?$') { return $M.certs.ContainsKey($p.TrimEnd('\')) }
    if ($p -match '^(Cert:\\.+)\\([0-9A-Fa-f]{40})$') { return [bool](@($M.certs[$Matches[1]] | Where-Object { $_.Thumbprint -eq $Matches[2] }).Count) }
    return ($M.paths -contains $p)
}
function Get-HotFix { $M.hotfix }
function Get-LocalUser { if ($null -eq $M.users) { throw 'not available' } ; $M.users }
function Get-LocalGroupMember { if ($M.adminsFail) { throw 'Failed to compare two elements in the array.' } ; $M.admins }
function Get-ChildItem {
    param([string] $Path, $ErrorAction, [switch] $Recurse, [int] $Depth, [string] $Filter, [switch] $File, [string] $LiteralPath)
    $p = $Path; if ($LiteralPath) { $p = $LiteralPath }
    if ($M.fs -and $p -and $p.StartsWith($M.fs)) {
        $a = @{ Path = $p; ErrorAction = 'SilentlyContinue' }
        if ($Recurse) { $a['Recurse'] = $true }; if ($Filter) { $a['Filter'] = $Filter }; if ($File) { $a['File'] = $true }
        return @(Microsoft.PowerShell.Management\Get-ChildItem @a)
    }
    if ($Path -eq 'Cert:\LocalMachine') { return $M.certStores }
    if ($Path -match '^(Cert:\\.+)\\([0-9A-Fa-f]{40})$' -and $M.certs.ContainsKey($Matches[1])) {
        $hit = @($M.certs[$Matches[1]] | Where-Object { $_.Thumbprint -eq $Matches[2] })
        if ($hit.Count) { return $hit[0] } ; throw "no certificate $Path"
    }
    if ($Path -like 'Cert:*') { if ($M.certs -and $M.certs.ContainsKey($Path)) { return $M.certs[$Path] } ; throw "no store $Path" }
    if ($Path -like 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Listener') { return $M.wsman }
    return
}
# the certificate stores' changes (the trust check's removal): Remove-Item on a store entry, Export-/Import-Certificate
function Remove-Item {
    param([string] $Path, [string] $LiteralPath, [switch] $Force, [switch] $Recurse, $ErrorAction)
    $p = $Path; if ($LiteralPath) { $p = $LiteralPath }
    if ($p -match '^(Cert:\\.+)\\([0-9A-Fa-f]{40})$') {
        $st = $Matches[1]; $tp = $Matches[2]
        if (-not $M.certs.ContainsKey($st)) { throw "no store $st" }
        $M.certs[$st] = @($M.certs[$st] | Where-Object { $_.Thumbprint -ne $tp })
        $M.removedCerts += @($p)
        return
    }
    Microsoft.PowerShell.Management\Remove-Item @PSBoundParameters
}
function Export-Certificate { param($Cert, [string] $FilePath, [switch] $Force) Microsoft.PowerShell.Management\Set-Content -LiteralPath $FilePath -Value ([byte[]]$Cert.RawData) -AsByteStream; return $FilePath }
function Import-Certificate {
    param([string] $FilePath, [string] $CertStoreLocation)
    $b = [byte[]](Microsoft.PowerShell.Management\Get-Content -LiteralPath $FilePath -AsByteStream -ReadCount 0)
    $c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (, $b)
    if (-not $M.certs.ContainsKey($CertStoreLocation)) { $M.certs[$CertStoreLocation] = @() }
    $M.certs[$CertStoreLocation] += @($c)
    return $c
}
function Invoke-Exe([string] $Key) {
    $global:LASTEXITCODE = 0
    if ($M.exit.ContainsKey($Key)) { $global:LASTEXITCODE = $M.exit[$Key] }
    if ($M.exe.ContainsKey($Key)) { return $M.exe[$Key] }
    $global:LASTEXITCODE = 1
    return "no mock output for: $Key"
}
function w32tm.exe { Invoke-Exe ('w32tm.exe ' + ($args -join ' ')) }
function nltest.exe { Invoke-Exe ('nltest.exe ' + ($args -join ' ')) }
# auditpol /backup /file:<path>: the scenario's backup lines written to that file (none given: it fails)
function auditpol.exe {
    if ($args[0] -eq '/backup') {
        if (-not $M.exe.ContainsKey('auditpol.exe /backup')) { $global:LASTEXITCODE = 1; return 'Error 0x00000005 occurred: Access is denied.' }
        $file = ([string]$args[1]) -replace '^/file:', ''
        Microsoft.PowerShell.Management\Set-Content -LiteralPath $file -Value $M.exe['auditpol.exe /backup']
        $global:LASTEXITCODE = 0
        return 'The command was successfully executed.'
    }
    Invoke-Exe ('auditpol.exe ' + ($args -join ' '))
}
function netsh.exe { Invoke-Exe ('netsh.exe ' + ($args -join ' ')) }
function net.exe { Invoke-Exe ('net.exe ' + ($args -join ' ')) }
function icacls.exe { $global:LASTEXITCODE = 0 }
# certreq -new -q -machine <inf> <csr>: a CSR file, and a pending request in the REQUEST store
function certutil.exe {
    # -f -decode IN OUT: base64 (the trust check in Constrained Language Mode)
    if ($args[1] -eq '-decode') {
        $b64 = ((Microsoft.PowerShell.Management\Get-Content -LiteralPath $args[2]) -join '') -replace '\s', ''
        Microsoft.PowerShell.Management\Set-Content -LiteralPath $args[3] -Value ([byte[]][Convert]::FromBase64String($b64)) -AsByteStream
        $global:LASTEXITCODE = 0
        return 'CertUtil: -decode command completed successfully.'
    }
    Invoke-Exe ('certutil.exe ' + ($args -join ' '))
}
function certreq.exe {
    $global:LASTEXITCODE = 0
    $inf, $csr = $args[-2], $args[-1]
    $subj = ((Get-Content -LiteralPath $inf) | Where-Object { $_ -like 'Subject = *' }) -replace '^Subject = "(.*)"$', '$1'
    Microsoft.PowerShell.Management\Set-Content -LiteralPath $csr -Value "-----BEGIN NEW CERTIFICATE REQUEST-----`r`nMADE $subj`r`n-----END NEW CERTIFICATE REQUEST-----"
    $M.certs['Cert:\LocalMachine\REQUEST'] += @(O @{ Thumbprint = (('{0:X2}' -f (0x80 + $M.certs['Cert:\LocalMachine\REQUEST'].Count)) * 20); Subject = $subj })
    'CertReq: Request Created'
}
# ---- run the check ---------------------------------------------------------------------------
$env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject $cfg -Depth 6 -Compress
if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
try { & $Script }
finally {
    if ($M.fs) {
        # what certreq was given, for the tests; then the temporary folder goes
        foreach ($f in @(Microsoft.PowerShell.Management\Get-ChildItem -LiteralPath $M.fs -Filter '*.inf')) { '###INF### ' + $f.Name + ' ' + ((Get-Content -LiteralPath $f.FullName) -join '|') }
        '###FILES### ' + (@(Microsoft.PowerShell.Management\Get-ChildItem -LiteralPath $M.fs | ForEach-Object { $_.Name }) -join ' ')
        Remove-Item -LiteralPath $M.fs -Recurse -Force
    }
}
