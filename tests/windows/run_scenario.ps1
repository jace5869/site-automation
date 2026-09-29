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
switch ($Scenario) {
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
        $M.exe['w32tm.exe /stripchart /computer:dc01.corp.test /samples:1 /dataonly'] = @('Tracking dc01.corp.test [10.0.0.1:123].', 'The current time is 9/28/2026 12:00:00 PM.', '12:00:00, +06.5000000s')
    }
    'time_stopped' {
        $M.cim.Win32_Service = @(O @{ Name = 'W32Time'; State = 'Stopped'; StartMode = 'Manual'; ExitCode = 0 })
    }
    'network' {
        $cfg = @{ dns_names = @('good.test', 'bad.test'); tcp = @(@{ name = 'closed port'; host = '127.0.0.1'; port = 1 }); secure_channel = $true; timeout_ms = 2000 }
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.cim.Win32_NetworkAdapterConfiguration = @(O @{ IPEnabled = $true; DefaultIPGateway = $null; DNSServerSearchOrder = @('10.0.0.1') })
        $M.dnsFail = @('bad.test')
        $M.exe['nltest.exe /sc_query:corp.test'] = @('Flags: 0', 'Trusted DC Name', 'Trusted DC Connection Status Status = 1311 0x51f ERROR_NO_LOGON_SERVERS', 'The command completed successfully')
        $M.tnc = $false
    }
    'network_ok' {
        $cfg = @{ dns_names = @('good.test'); secure_channel = $true }
        $M.cim.Win32_ComputerSystem = @(O @{ PartOfDomain = $true; DomainRole = 3; Domain = 'corp.test' })
        $M.cim.Win32_NetworkAdapterConfiguration = @(O @{ IPEnabled = $true; DefaultIPGateway = @('10.0.0.254'); DNSServerSearchOrder = @('10.0.0.1') })
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
function Test-Path { param([string] $Path) return ($M.paths -contains $Path) }
function Get-HotFix { $M.hotfix }
function Get-LocalUser { if ($null -eq $M.users) { throw 'not available' } ; $M.users }
function Get-LocalGroupMember { if ($M.adminsFail) { throw 'Failed to compare two elements in the array.' } ; $M.admins }
function Get-ChildItem {
    param([string] $Path, $ErrorAction)
    if ($Path -like 'Cert:*') { if ($M.certs -and $M.certs.ContainsKey($Path)) { return $M.certs[$Path] } ; throw "no store $Path" }
    if ($Path -like 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Listener') { return $M.wsman }
    return
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
function auditpol.exe { Invoke-Exe ('auditpol.exe ' + ($args -join ' ')) }
function netsh.exe { Invoke-Exe ('netsh.exe ' + ($args -join ' ')) }
function net.exe { Invoke-Exe ('net.exe ' + ($args -join ' ')) }
# ---- run the check ---------------------------------------------------------------------------
$env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject $cfg -Depth 6 -Compress
if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
& $Script
