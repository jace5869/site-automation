# Run ONE of the Active Directory report scripts (roles/ad_report/files/ad_dc.ps1 or ad_domain.ps1)
# against made-up domain data (a "scenario"), for tests/windows/test_ad_collect.ps1. The AD cmdlets
# and the tools the scripts call (dcdiag, repadmin, w32tm, nltest, dfsrmig) are replaced by functions
# that return the scenario's data, so the scripts can be tested anywhere PowerShell runs, also on
# Linux, and in Constrained Language Mode.
#   pwsh -File run_ad_scenario.ps1 -Script roles/ad_report/files/ad_dc.ps1 -Scenario dc_ok [-Clm]
param(
    [Parameter(Mandatory = $true)][string] $Script,
    [Parameter(Mandatory = $true)][string] $Scenario,
    [switch] $Clm
)
$ErrorActionPreference = 'Stop'
$now = Get-Date
$GB = 1073741824
function O($h) { return [pscustomobject]$h }
function T([double] $HoursAgo) { return $now.AddHours(-$HoursAgo).ToString('yyyy-MM-dd HH:mm:ss') }
function Ev($log, $id, $provider, $level, $hoursAgo, $message, $props) {
    $p = @()
    foreach ($v in @($props)) { $p += @([pscustomobject]@{ Value = $v }) }
    return [pscustomobject]@{ LogName = $log; Id = $id; ProviderName = $provider; Level = $level
        TimeCreated = $now.AddHours(-$hoursAgo); Message = $message; Properties = $p }
}
$dom = 'DC=corp,DC=test'
$cfgNc = 'CN=Configuration,' + $dom
$schNc = 'CN=Schema,' + $cfgNc
function Ntds([string] $Dc) { return "CN=NTDS Settings,CN=$Dc,CN=Servers,CN=HQ,CN=Sites,$cfgNc" }
$FAKE = @{ cim = @{}; events = @(); exe = @{}; reg = @{}; paths = @(); files = @{}; dirs = @{}; certs = @(); noAd = $false
    adobj = @{}; search = @(); meta = @{}; groups = @{} }
$cfg = @{}
# ---- a healthy DC (also the base of the others) ---------------------------------------------------------
function Base-Dc {
    $FAKE.cim.Win32_ComputerSystem = @(O @{ Name = 'DC01'; DNSHostName = 'dc01'; Domain = 'corp.test'; DomainRole = 5 })
    $FAKE.cim.Win32_OperatingSystem = @(O @{ Caption = 'Microsoft Windows Server 2022 Standard'; LastBootUpTime = $now.AddDays(-20) })
    $FAKE.cim.Win32_Service = @(
        (O @{ Name = 'NTDS'; State = 'Running'; StartMode = 'Auto' }), (O @{ Name = 'Netlogon'; State = 'Running'; StartMode = 'Auto' }),
        (O @{ Name = 'kdc'; State = 'Running'; StartMode = 'Auto' }), (O @{ Name = 'DNS'; State = 'Running'; StartMode = 'Auto' }),
        (O @{ Name = 'DFSR'; State = 'Running'; StartMode = 'Auto' }), (O @{ Name = 'W32Time'; State = 'Running'; StartMode = 'Auto' }),
        (O @{ Name = 'ADWS'; State = 'Running'; StartMode = 'Auto' }), (O @{ Name = 'IsmServ'; State = 'Running'; StartMode = 'Auto' }),
        (O @{ Name = 'LanmanServer'; State = 'Running'; StartMode = 'Auto' }), (O @{ Name = 'Spooler'; State = 'Running'; StartMode = 'Auto' }))
    $FAKE.cim.Win32_Share = @((O @{ Name = 'SYSVOL' }), (O @{ Name = 'NETLOGON' }), (O @{ Name = 'C$' }))
    $FAKE.cim.DfsrReplicatedFolderInfo = @(O @{ ReplicatedFolderName = 'SYSVOL Share'; State = 4 })
    $FAKE.cim.Win32_LogicalDisk = @((O @{ DeviceID = 'C:'; Size = 100 * $GB; FreeSpace = 60 * $GB }), (O @{ DeviceID = 'D:'; Size = 50 * $GB; FreeSpace = 40 * $GB }))
    $FAKE.reg['HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters'] = O @{ SysvolReady = 1; SysVol = 'C:\Windows\SYSVOL\sysvol' }
    $FAKE.reg['HKLM:\SYSTEM\CurrentControlSet\Services\NTDS\Parameters'] = O @{ 'DSA Database file' = 'D:\NTDS\ntds.dit'; 'Database log files path' = 'D:\NTDS' }
    $FAKE.me = O @{ Site = 'HQ'; Forest = 'corp.test'; IsGlobalCatalog = $true; IsReadOnly = $false; HostName = 'dc01.corp.test'
        OperationMasterRoles = @('PDCEmulator', 'RIDMaster') }
    $FAKE.rootdse = O @{ isSynchronized = 'TRUE'; isGlobalCatalogReady = 'TRUE'; dnsHostName = 'dc01.corp.test'
        configurationNamingContext = $cfgNc; schemaNamingContext = $schNc }
    $FAKE.exe['dcdiag.exe /s:DC01 /skip:SystemLog'] = @('Directory Server Diagnosis', '   Testing server: HQ\DC01',
        '      Starting test: Connectivity', '         ......................... DC01 passed test Connectivity',
        '      Starting test: Replications', '         ......................... DC01 passed test Replications',
        '   Running enterprise tests on : corp.test', '      Starting test: LocatorCheck', '         ......................... corp.test passed test LocatorCheck')
    $FAKE.exe['dcdiag.exe /s:DC01 /test:DNS /DnsBasic /DnsForwarders /DnsDelegation /DnsRecordRegistration'] = @(
        '      Starting test: DNS', '         ......................... corp.test passed test DNS')
    $FAKE.exe['repadmin.exe /showrepl DC01 /csv'] = @(
        'showrepl_COLUMNS,Destination DSA Site,Destination DSA,Naming Context,Source DSA Site,Source DSA,Transport Type,Number of Failures,Last Failure Time,Last Success Time,Last Failure Status',
        ('showrepl_INFO,HQ,DC01,"DC=corp,DC=test",HQ,DC02,RPC,0,0,' + (T 0.2) + ',0'),
        ('showrepl_INFO,HQ,DC01,"CN=Configuration,DC=corp,DC=test",HQ,DC02,RPC,0,0,' + (T 0.3) + ',0'))
    $FAKE.exe['repadmin.exe /queue DC01'] = @('Repadmin: running command /queue against full DC localhost', 'Queue contains 0 items.')
    $FAKE.exe['w32tm.exe /query /status'] = @('Leap Indicator: 0(no warning)', 'Stratum: 2 (secondary reference - syncd by (S)NTP)',
        'Source: ntp.corp.test,0x8', ('Last Successful Sync Time: ' + (T 0.5)))
    $FAKE.exe['w32tm.exe /stripchart /computer:ntp.corp.test /samples:1 /dataonly'] = @('Tracking ntp.corp.test [10.0.0.9:123].', '12:00:00, +00.0150000s')
    $FAKE.paths = @('/win/debug/netlogon.log')
    $FAKE.files['/win/debug/netlogon.log'] = @('10/14 01:00:00 [SESSION] CORP: something else')
    $FAKE.certs = @(O @{ HasPrivateKey = $true; Subject = 'CN=dc01.corp.test'; Thumbprint = 'AB12'; NotAfter = $now.AddDays(200)
            EnhancedKeyUsageList = @((O @{ ObjectId = '1.3.6.1.5.5.7.3.1' }), (O @{ ObjectId = '1.3.6.1.5.2.3.5' })); DnsNameList = @(O @{ Unicode = 'dc01.corp.test' }) })
    $FAKE.events = @(
        (Ev 'Directory Service' 1644 'NTDS General' 4 1 'expensive search' @()),
        (Ev 'System' 7036 'Service Control Manager' 4 1 'entered the running state' @()))
}
function Base-Domain {
    $FAKE.domain = O @{ DNSRoot = 'corp.test'; NetBIOSName = 'CORP'; DistinguishedName = $dom; DomainSID = 'S-1-5-21-1-2-3'
        DomainMode = 'Windows2016Domain'; PDCEmulator = 'dc01.corp.test'; RIDMaster = 'dc01.corp.test'; InfrastructureMaster = 'dc02.corp.test' }
    $FAKE.rootdse = O @{ isSynchronized = 'TRUE'; dnsHostName = 'dc01.corp.test'; configurationNamingContext = $cfgNc; schemaNamingContext = $schNc }
    $FAKE.forest = O @{ Name = 'corp.test'; ForestMode = 'Windows2016Forest'; RootDomain = 'corp.test'; Domains = @('corp.test')
        SchemaMaster = 'dc01.corp.test'; DomainNamingMaster = 'dc01.corp.test' }
    $FAKE.dcs = @(
        (O @{ HostName = 'dc01.corp.test'; Site = 'HQ'; IPv4Address = '10.0.0.1'; IsGlobalCatalog = $true; IsReadOnly = $false; OperatingSystem = 'Windows Server 2022 Standard'
                NTDSSettingsObjectDN = (Ntds 'DC01'); OperationMasterRoles = @('PDCEmulator', 'RIDMaster', 'SchemaMaster', 'DomainNamingMaster') }),
        (O @{ HostName = 'dc02.corp.test'; Site = 'HQ'; IPv4Address = '10.0.0.2'; IsGlobalCatalog = $true; IsReadOnly = $false; OperatingSystem = 'Windows Server 2022 Standard'
                NTDSSettingsObjectDN = (Ntds 'DC02'); OperationMasterRoles = @('InfrastructureMaster') }))
    $FAKE.tnc = @{ 'dc01.corp.test' = $true; 'dc02.corp.test' = $true }
    $FAKE.adobj[$schNc] = O @{ objectVersion = 88; DistinguishedName = $schNc }
    $FAKE.adobj["CN=RID Manager`$,CN=System,$dom"] = O @{ rIDAvailablePool = [int64]1073741823 * 4294967296 + 5600 }
    $FAKE.adobj["CN=Directory Service,CN=Windows NT,CN=Services,$cfgNc"] = O @{ tombstoneLifetime = 180 }
    $FAKE.meta[$schNc] = @(O @{ AttributeName = 'objectVersion'; LastOriginatingChangeTime = $now.AddDays(-400) })
    $FAKE.optional = @(O @{ Name = 'Recycle Bin Feature'; EnabledScopes = @('CN=Partitions') })
    $FAKE.users = @(O @{ SamAccountName = 'krbtgt'; PasswordLastSet = $now.AddDays(-30) })
    $FAKE.exe["repadmin.exe /showutdvec localhost $dom /latency"] = @('Caching GUIDs.', '..',
        ('HQ\DC01                        @ USN    123456 @ Time ' + (T 0)), ('HQ\DC02                        @ USN    654321 @ Time ' + (T 0.1)),
        ('7a1c5f5e-0000-4000-8000-000000000001 @ USN 1000 @ Time ' + (T 9000)))
    $FAKE.exe['repadmin.exe /showbackup localhost'] = @('Repadmin: running command /showbackup against full DC localhost', '',
        $dom, '        Local-Usn:Originating DSA  Org-Usn  Org-Time/Date        Ver Attribute', '        ========= =============== ========= =============        === =========',
        ('            12345            HQ\DC01     12345 ' + (T 20) + '    3 dSASignature'), '',
        $cfgNc, ('            12346            HQ\DC01     12346 ' + (T 20) + '    3 dSASignature'))
    $FAKE.exe['dfsrmig.exe /getglobalstate'] = @("Current DFSR global state: 'Eliminated'", 'Succeeded.')
    $FAKE.srv = @{ '_ldap._tcp.dc._msdcs.corp.test' = @('dc01.corp.test', 'dc02.corp.test'); '_kerberos._tcp.dc._msdcs.corp.test' = @('dc01.corp.test', 'dc02.corp.test')
        '_ldap._tcp.gc._msdcs.corp.test' = @('dc01.corp.test', 'dc02.corp.test') }
    $FAKE.trusts = @()
    $FAKE.sites = @(O @{ Name = 'HQ' })
    $FAKE.subnets = @(O @{ Name = '10.0.0.0/24'; Site = "CN=HQ,CN=Sites,$cfgNc" })
    $FAKE.groups['S-1-5-21-1-2-3-512'] = O @{ Name = 'Domain Admins'; SamAccountName = 'Domain Admins'; SID = 'S-1-5-21-1-2-3-512'
        DistinguishedName = "CN=Domain Admins,CN=Users,$dom"; member = @("CN=Administrator,CN=Users,$dom") }
    $FAKE.groups['S-1-5-32-544'] = O @{ Name = 'Administrators'; SamAccountName = 'Administrators'; SID = 'S-1-5-32-544'
        DistinguishedName = "CN=Administrators,CN=Builtin,$dom"; member = @("CN=Domain Admins,CN=Users,$dom", "CN=Administrator,CN=Users,$dom") }
    $FAKE.groups['DnsAdmins'] = O @{ Name = 'DnsAdmins'; SamAccountName = 'DnsAdmins'; SID = 'S-1-5-21-1-2-3-1101'; DistinguishedName = "CN=DnsAdmins,CN=Users,$dom"; member = @() }
    $FAKE.meta["CN=Domain Admins,CN=Users,$dom"] = @(O @{ IsLinkValue = $true; AttributeValue = "CN=Administrator,CN=Users,$dom"
            LastOriginatingChangeTime = $now.AddDays(-300); LastOriginatingDeleteTime = [datetime]'1601-01-01'; LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC01') })
    $FAKE.reg['HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters'] = O @{ SysVol = '/sysvol' }
    $pol = '/sysvol/corp.test/Policies'
    $FAKE.paths = @($pol, "$pol/{31B2F340-016D-11D2-945F-00C04FB984F9}/GPT.INI")
    $FAKE.dirs[$pol] = @(O @{ Name = '{31B2F340-016D-11D2-945F-00C04FB984F9}' })
    $FAKE.files["$pol/{31B2F340-016D-11D2-945F-00C04FB984F9}/GPT.INI"] = @('[General]', 'Version=7')
    $FAKE.gpos = @(O @{ Name = '{31B2F340-016D-11D2-945F-00C04FB984F9}'; displayName = 'Default Domain Policy'; whenCreated = $now.AddDays(-900)
            whenChanged = $now.AddDays(-100); versionNumber = 7; flags = 0 })
}
switch ($Scenario) {
    'dc_ok' { Base-Dc }
    'dc_broken' {
        Base-Dc
        $cfg = @{ hours = 24; dcdiag_skip = @('SystemLog', 'DFSREvent') }
        $FAKE.cim.Win32_ComputerSystem = @(O @{ Name = 'DC02'; DNSHostName = 'dc02'; Domain = 'corp.test'; DomainRole = 4 })
        $FAKE.me.OperationMasterRoles = @()
        $FAKE.me.HostName = 'dc02.corp.test'
        $FAKE.rootdse.isSynchronized = 'FALSE'
        $FAKE.cim.Win32_Service = @($FAKE.cim.Win32_Service | Where-Object { $_.Name -ne 'kdc' }) + @(O @{ Name = 'kdc'; State = 'Stopped'; StartMode = 'Auto' })
        $FAKE.cim.Win32_Share = @(O @{ Name = 'NETLOGON' })
        $FAKE.cim.DfsrReplicatedFolderInfo = @(O @{ ReplicatedFolderName = 'SYSVOL Share'; State = 5 })
        $FAKE.cim.Win32_LogicalDisk = @((O @{ DeviceID = 'C:'; Size = 100 * $GB; FreeSpace = 60 * $GB }), (O @{ DeviceID = 'D:'; Size = 50 * $GB; FreeSpace = 1 * $GB }))
        $FAKE.exe.Remove('dcdiag.exe /s:DC01 /skip:SystemLog')
        $FAKE.exe['dcdiag.exe /s:DC02 /skip:SystemLog /skip:DFSREvent'] = @('   Testing server: HQ\DC02',
            '      Starting test: Connectivity', '         ......................... DC02 passed test Connectivity',
            '      Starting test: Replications', '         [Replications Check,DC02] A recent replication attempt failed:',
            '            From DC01 to DC02', '            The replication generated an error (8453):', '            Replication access was denied.',
            '         ......................... DC02 failed test Replications')
        $FAKE.exe['dcdiag.exe /s:DC02 /test:DNS /DnsBasic /DnsForwarders /DnsDelegation /DnsRecordRegistration'] = @(
            '      Starting test: DNS', '         Error: no DNS servers reachable', '         Warning: forwarder 8.8.8.8 does not answer',
            '         ......................... corp.test failed test DNS')
        $FAKE.exe['repadmin.exe /showrepl DC02 /csv'] = @(
            'showrepl_COLUMNS,Destination DSA Site,Destination DSA,Naming Context,Source DSA Site,Source DSA,Transport Type,Number of Failures,Last Failure Time,Last Success Time,Last Failure Status',
            ('showrepl_INFO,HQ,DC02,"DC=corp,DC=test",HQ,DC01,RPC,12,' + (T 0.1) + ',' + (T 30) + ',8453'),
            'showrepl_INFO,HQ,DC02,"CN=Configuration,DC=corp,DC=test",HQ,DC01,RPC,3,0,0,1722')
        $FAKE.exe['repadmin.exe /queue DC02'] = @('Queue contains 75 items.')
        $FAKE.exe['w32tm.exe /query /status'] = @('Source: dc01.corp.test,0x9', 'Stratum: 3', ('Last Successful Sync Time: ' + (T 50)))
        $FAKE.exe['w32tm.exe /stripchart /computer:dc01.corp.test /samples:1 /dataonly'] = @('Tracking dc01.corp.test.', '12:00:00, -91.5000000s')
        $FAKE.domain = O @{ PDCEmulator = 'dc01.corp.test' }
        $FAKE.files['/win/debug/netlogon.log'] = @(
            ($now.AddHours(-2).ToString('MM/dd HH:mm:ss') + ' [SITE] CORP: NO_CLIENT_SITE: WS01 10.9.0.5'),
            ($now.AddHours(-1).ToString('MM/dd HH:mm:ss') + ' [SITE] CORP: NO_CLIENT_SITE: WS01 10.9.0.5'),
            ($now.AddHours(-1).ToString('MM/dd HH:mm:ss') + ' [SITE] CORP: NO_CLIENT_SITE: WS02 10.9.0.6'),
            ($now.AddDays(-5).ToString('MM/dd HH:mm:ss') + ' [SITE] CORP: NO_CLIENT_SITE: OLD 10.9.0.99'))
        $FAKE.certs = @(O @{ HasPrivateKey = $true; Subject = 'CN=dc02.corp.test'; Thumbprint = 'CD34'; NotAfter = $now.AddDays(3)
                EnhancedKeyUsageList = @(O @{ ObjectId = '1.3.6.1.5.5.7.3.1' }); DnsNameList = @() })
        $FAKE.events = @(
            (Ev 'DFS Replication' 2213 'DFSR' 3 2 'The DFS Replication service stopped replication on volume C:.' @()),
            (Ev 'DFS Replication' 2213 'DFSR' 3 1 'The DFS Replication service stopped replication on volume C:. again' @()),
            (Ev 'Directory Service' 2887 'Microsoft-Windows-ActiveDirectory_DomainService' 3 3 '25 unsigned binds' @()),
            (Ev 'System' 5719 'NETLOGON' 2 4 'no domain controller' @()),
            (Ev 'System' 7000 'Service Control Manager' 2 4 'a service failed to start' @()),
            (Ev 'Security' 4728 'Microsoft-Windows-Security-Auditing' 0 2 'member added' @("CN=Mallory,CN=Users,$dom", 'S-1-5-21-1-2-3-1500', 'Domain Admins', 'CORP', 'S-1-5-21-1-2-3-512', 'S-1-5-21-1-2-3-500', 'admin1', 'CORP')),
            (Ev 'Security' 4720 'Microsoft-Windows-Security-Auditing' 0 3 'user created' @('mallory', 'CORP', 'S-1-5-21-1-2-3-1500', 'S-1-5-21-1-2-3-500', 'admin1', 'CORP')),
            (Ev 'Security' 1102 'Microsoft-Windows-Eventlog' 0 1 'log cleared' @('S-1-5-21-1-2-3-500', 'admin1', 'CORP')))
    }
    'dc_german' {
        # a DC whose tools print dates the German way (dd.MM.yyyy)
        Base-Dc
        [System.Threading.Thread]::CurrentThread.CurrentCulture = 'de-DE'
        $de = $now.AddHours(-0.5).ToString('dd.MM.yyyy HH:mm:ss')
        $FAKE.exe['w32tm.exe /query /status'] = @('Source: ntp.corp.test,0x8', 'Stratum: 2', ('Last Successful Sync Time: ' + $de))
        $FAKE.exe['repadmin.exe /showrepl DC01 /csv'] = @(
            'showrepl_COLUMNS,Destination DSA Site,Destination DSA,Naming Context,Source DSA Site,Source DSA,Transport Type,Number of Failures,Last Failure Time,Last Success Time,Last Failure Status',
            ('showrepl_INFO,HQ,DC01,"DC=corp,DC=test",HQ,DC02,RPC,0,0,' + $de + ',0'))
    }
    'dc_no_ad' {
        Base-Dc
        $FAKE.noAd = $true
    }
    'domain_ok' { Base-Domain }
    'domain_changes' {
        Base-Domain
        $cfg = @{ hours = 24; privileged_groups = @('DnsAdmins', 'NoSuchGroup') }
        $FAKE.tnc['dc02.corp.test'] = $false
        $FAKE.domain.InfrastructureMaster = 'dc09.corp.test'
        $FAKE.meta[$schNc] = @(O @{ AttributeName = 'objectVersion'; LastOriginatingChangeTime = $now.AddHours(-3) })
        $FAKE.optional = @(O @{ Name = 'Recycle Bin Feature'; EnabledScopes = @() })
        $FAKE.users = @((O @{ SamAccountName = 'krbtgt'; PasswordLastSet = $now.AddDays(-700) }), (O @{ SamAccountName = 'krbtgt_12345'; PasswordLastSet = $now.AddDays(-10) }))
        $FAKE.adobj["CN=RID Manager`$,CN=System,$dom"] = O @{ rIDAvailablePool = [int64]1073741823 * 4294967296 + 1060000000 }
        $FAKE.adobj["CN=Directory Service,CN=Windows NT,CN=Services,$cfgNc"] = O @{ tombstoneLifetime = $null }
        $FAKE.exe['dfsrmig.exe /getglobalstate'] = @("Current DFSR global state: 'Start'")
        $FAKE.srv['_ldap._tcp.dc._msdcs.corp.test'] = @('dc01.corp.test')
        $FAKE.srvFail = @('_kerberos._tcp.dc._msdcs.corp.test')
        $FAKE.trusts = @(O @{ Name = 'partner.test'; Direction = 'BiDirectional'; TrustType = 'Uplevel'; ForestTransitive = $true; SelectiveAuthentication = $false
                SIDFilteringQuarantined = $false; whenCreated = $now.AddHours(-2); whenChanged = $now.AddHours(-2) })
        $FAKE.exe['nltest.exe /sc_verify:partner.test'] = @('Flags: b0 HAS_IP  HAS_TIMESERV', 'Trusted DC Name \\pdc.partner.test',
            'Trusted DC Connection Status Status = 1311 0x51f ERROR_NO_LOGON_SERVERS', 'Trust Verification Status = 1311 0x51f ERROR_NO_LOGON_SERVERS')
        $FAKE.sites = @((O @{ Name = 'HQ' }), (O @{ Name = 'Branch' }))
        $FAKE.subnets = @((O @{ Name = '10.0.0.0/24'; Site = "CN=HQ,CN=Sites,$cfgNc" }), (O @{ Name = '10.9.0.0/24'; Site = $null }))
        $FAKE.meta["CN=Domain Admins,CN=Users,$dom"] = @(
            (O @{ IsLinkValue = $true; AttributeValue = "CN=Administrator,CN=Users,$dom"; LastOriginatingChangeTime = $now.AddDays(-300)
                    LastOriginatingDeleteTime = [datetime]'1601-01-01'; LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC01') }),
            (O @{ IsLinkValue = $true; AttributeValue = "CN=Mallory,CN=Users,$dom"; LastOriginatingChangeTime = $now.AddHours(-2)
                    LastOriginatingDeleteTime = [datetime]'1601-01-01'; LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC02') }),
            (O @{ IsLinkValue = $true; AttributeValue = "CN=Old Admin,CN=Users,$dom"; LastOriginatingChangeTime = $now.AddHours(-5)
                    LastOriginatingDeleteTime = $now.AddHours(-5); LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC01') }))
        $FAKE.groups['S-1-5-21-1-2-3-512'].member = @("CN=Administrator,CN=Users,$dom", "CN=Mallory,CN=Users,$dom")
        $FAKE.search = @(
            @{ match = 'whenCreated>='; base = $schNc; items = @(O @{ lDAPDisplayName = 'msExampleAttr'; ObjectClass = 'attributeSchema'; whenCreated = $now.AddHours(-3) }) },
            @{ match = '(objectCategory=person)(objectClass=group)(objectClass=computer))(whenCreated'; items = @(
                    (O @{ ObjectClass = 'user'; sAMAccountName = 'mallory'; DistinguishedName = "CN=Mallory,CN=Users,$dom"; whenCreated = $now.AddHours(-3) }),
                    (O @{ ObjectClass = 'computer'; sAMAccountName = 'WS99$'; DistinguishedName = "CN=WS99,OU=PCs,$dom"; whenCreated = $now.AddHours(-4) })) },
            @{ match = '(objectClass=user)(objectClass=group)(objectClass=computer)))'; deleted = $true; items = @(O @{ ObjectClass = 'user'; sAMAccountName = 'bob'; 'msDS-LastKnownRDN' = 'Bob'
                            lastKnownParent = "OU=Staff,$dom"; whenChanged = $now.AddHours(-6) }) },
            @{ match = '(objectCategory=person)(objectClass=computer))(whenChanged'; items = @(
                    (O @{ ObjectClass = 'user'; sAMAccountName = 'carol'; DistinguishedName = "CN=Carol,OU=Staff,$dom"; userAccountControl = 514; lockoutTime = 0; whenCreated = $now.AddDays(-100) }),
                    (O @{ ObjectClass = 'user'; sAMAccountName = 'dave'; DistinguishedName = "CN=Dave,OU=Staff,$dom"; userAccountControl = 512; lockoutTime = 0; whenCreated = $now.AddDays(-100) })) },
            @{ match = '(objectClass=site)(objectClass=subnet)'; base = $cfgNc; items = @(
                    (O @{ ObjectClass = 'subnet'; Name = '10.9.0.0/24'; DistinguishedName = "CN=10.9.0.0/24,CN=Subnets,CN=Sites,$cfgNc"; whenCreated = $now.AddHours(-1); whenChanged = $now.AddHours(-1) }),
                    (O @{ ObjectClass = 'nTDSDSA'; Name = 'NTDS Settings'; DistinguishedName = (Ntds 'DC03'); whenCreated = $now.AddHours(-8); whenChanged = $now.AddHours(-8) })) },
            @{ match = '(objectClass=organizationalUnit)(objectClass=domainDNS))(whenChanged'; items = @(
                    O @{ DistinguishedName = "OU=Staff,$dom"; gPLink = '[LDAP://cn={31B2F340-016D-11D2-945F-00C04FB984F9},cn=policies,cn=system,DC=corp,DC=test;0]' }) })
        $FAKE.meta["CN=Carol,OU=Staff,$dom"] = @(
            (O @{ AttributeName = 'userAccountControl'; LastOriginatingChangeTime = $now.AddHours(-1); LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC01') }),
            (O @{ AttributeName = 'lockoutTime'; LastOriginatingChangeTime = $now.AddDays(-50); LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC01') }))
        $FAKE.meta["CN=Dave,OU=Staff,$dom"] = @(O @{ AttributeName = 'lockoutTime'; LastOriginatingChangeTime = $now.AddHours(-2); LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC02') })
        $FAKE.meta["OU=Staff,$dom"] = @(O @{ AttributeName = 'gPLink'; LastOriginatingChangeTime = $now.AddHours(-1); LastOriginatingChangeDirectoryServerIdentity = (Ntds 'DC01') })
        $FAKE.locked = @(O @{ SamAccountName = 'erin' })
        $pol = '/sysvol/corp.test/Policies'
        $FAKE.dirs[$pol] = @((O @{ Name = '{31B2F340-016D-11D2-945F-00C04FB984F9}' }), (O @{ Name = '{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}' }))
        $FAKE.files["$pol/{31B2F340-016D-11D2-945F-00C04FB984F9}/GPT.INI"] = @('[General]', 'Version=5')
        $FAKE.gpos += @(O @{ Name = '{11111111-2222-3333-4444-555555555555}'; displayName = 'New Policy'; whenCreated = $now.AddHours(-2)
                whenChanged = $now.AddHours(-2); versionNumber = 1; flags = 0 })
    }
    'domain_no_ad' { $FAKE.noAd = $true }
    default { throw "unknown scenario $Scenario" }
}
# ---- the stand-ins ---------------------------------------------------------------------------------------
function Test-CimFilter($o, [string] $f) {
    if (-not $f) { return $true }
    if ($f -match "^\s*(\w+)\s*=\s*'([^']*)'\s*$") { return ([string]$o.($Matches[1]) -eq $Matches[2]) }
    return $true
}
function Get-CimInstance {
    param([string] $ClassName, [string] $Filter, [string] $Namespace, $ErrorAction)
    $data = $FAKE.cim[$ClassName]
    if ($null -eq $data) { throw "Invalid class $ClassName" }
    @($data) | Where-Object { Test-CimFilter $_ $Filter }
}
function Import-Module { if ($FAKE.noAd) { throw "The specified module 'ActiveDirectory' was not loaded because no valid module file was found." } }
function Get-ADDomainController {
    param([string] $Server, $Filter, $Identity)
    if ($null -ne $Filter) { return $FAKE.dcs }
    if (-not $FAKE.me) { throw 'Unable to contact the server.' }
    return $FAKE.me
}
function Get-ADRootDSE { param([string] $Server) if ($Server -like '*:3268' -and $FAKE.gcDown) { throw 'GC down' } ; return $FAKE.rootdse }
function Get-ADDomain { param([string] $Server) if (-not $FAKE.domain) { throw 'no domain' } ; return $FAKE.domain }
function Get-ADForest { param([string] $Server) return $FAKE.forest }
function Get-ADOptionalFeature { param($Filter, [string] $Server) return $FAKE.optional }
function Get-ADUser { param($Filter, $Properties, [string] $Server) return $FAKE.users }
function Get-ADTrust { param($Filter, $Properties, [string] $Server) return $FAKE.trusts }
function Get-ADReplicationSite { param($Filter, [string] $Server) return $FAKE.sites }
function Get-ADReplicationSubnet { param($Filter, $Properties, [string] $Server) return $FAKE.subnets }
function Search-ADAccount { param([switch] $LockedOut, [string] $Server) return $FAKE.locked }
function Get-ADGroup {
    param($Identity, $Properties, [string] $Server)
    if ($FAKE.groups.ContainsKey([string]$Identity)) { return $FAKE.groups[[string]$Identity] }
    throw "Cannot find an object with identity: '$Identity'"
}
function Get-ADReplicationAttributeMetadata {
    param($Object, $Properties, [switch] $ShowAllLinkedValues, [string] $Server)
    $all = @($FAKE.meta[[string]$Object])
    return @($all | Where-Object { $_ -and ($_.IsLinkValue -or @($Properties) -contains [string]$_.AttributeName) })
}
function Get-ADObject {
    param($Identity, [string] $SearchBase, [string] $SearchScope, [string] $LDAPFilter, $Properties, [string] $Server, [switch] $IncludeDeletedObjects)
    if ($Identity) {
        if ($FAKE.adobj.ContainsKey([string]$Identity)) { return $FAKE.adobj[[string]$Identity] }
        throw "Cannot find an object with identity: '$Identity'"
    }
    if ($LDAPFilter -eq '(objectClass=groupPolicyContainer)') { return $FAKE.gpos }
    foreach ($s in $FAKE.search) {
        if ($LDAPFilter.Contains($s.match) -and (-not $s.base -or $SearchBase -eq $s.base) -and ([bool]$s.deleted -eq [bool]$IncludeDeletedObjects)) { return $s.items }
    }
}
function Test-NetConnection { param([string] $ComputerName, $Port, $InformationLevel) return [bool]$FAKE.tnc[$ComputerName] }
function Resolve-DnsName {
    param([string] $Name, $Type, [switch] $DnsOnly, $ErrorAction)
    if ($FAKE.srvFail -contains $Name) { throw "$Name : DNS name does not exist" }
    foreach ($t in @($FAKE.srv[$Name])) { if ($t) { O @{ NameTarget = $t } } }
}
function Get-ItemProperty { param([string] $Path, $ErrorAction) if ($FAKE.reg.ContainsKey($Path)) { return $FAKE.reg[$Path] } ; throw "no key $Path" }
function Test-Path { param([string] $Path) return ($FAKE.paths -contains ($Path -replace '\\', '/')) }
function Join-Path { param([string] $Path, [string] $ChildPath) return (($Path.TrimEnd('/', '\') + '/' + $ChildPath) -replace '\\', '/') }
function Get-Content { param([string] $Path, $Tail, $ErrorAction) return $FAKE.files[($Path -replace '\\', '/')] }
function Get-ChildItem {
    param([string] $Path, [switch] $Directory, $ErrorAction)
    if ($Path -like 'Cert:*') { return $FAKE.certs }
    return $FAKE.dirs[$Path]
}
function Get-WinEvent {
    param($FilterHashtable, $MaxEvents, $ErrorAction)
    $h = $FilterHashtable
    $r = @($FAKE.events | Where-Object { $_.LogName -eq $h.LogName -and (-not $h.Id -or @($h.Id) -contains $_.Id) -and $_.TimeCreated -ge $h.StartTime })
    if ($r.Count -eq 0) { throw 'No events were found that match the specified selection criteria.' }
    $r
}
function Invoke-Exe([string] $Key) {
    $global:LASTEXITCODE = 0
    if ($FAKE.exe.ContainsKey($Key)) { return $FAKE.exe[$Key] }
    $global:LASTEXITCODE = 1
    return "no mock output for: $Key"
}
function dcdiag.exe { Invoke-Exe ('dcdiag.exe ' + ($args -join ' ')) }
function repadmin.exe { Invoke-Exe ('repadmin.exe ' + ($args -join ' ')) }
function w32tm.exe { Invoke-Exe ('w32tm.exe ' + ($args -join ' ')) }
function nltest.exe { Invoke-Exe ('nltest.exe ' + ($args -join ' ')) }
function dfsrmig.exe { Invoke-Exe ('dfsrmig.exe ' + ($args -join ' ')) }
# ---- run it ------------------------------------------------------------------------------------------------
$env:SystemRoot = '/win'
$env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject $cfg -Depth 6 -Compress
if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
& $Script
