# Run ONE of the account lockout scripts (roles/ad_lockout/files/lockout_*.ps1) against a made-up
# domain (a "scenario"), for tests/windows/test_lockout_collect.ps1 and tests/ad/make_lockout_fixtures.sh.
# The AD cmdlets, Get-WinEvent (it reads the XPath query it is given: the names, the event IDs, the
# time window and the Status filter), Resolve-DnsName, Get-ScheduledTask, Get-Process and quser are
# replaced by functions that return the scenario's data, so the scripts can be tested anywhere
# PowerShell runs, also on Linux, and in Constrained Language Mode.
#   pwsh -File run_lockout_scenario.ps1 -Script roles/ad_lockout/files/lockout_dc.ps1 -Scenario dc_pdc [-Clm] [-KeepConfig]
# -KeepConfig: the settings the caller gave (SITE_CHECK_CONFIG) instead of the scenario's - for
# tests/ad/run_lockout_test.sh, where the role's own tasks run the scripts through this.
param(
    [Parameter(Mandatory = $true)][string] $Script,
    [Parameter(Mandatory = $true)][string] $Scenario,
    [switch] $Clm,
    [switch] $KeepConfig
)
$ErrorActionPreference = 'Stop'
$now = Get-Date
function O($h) { return [pscustomobject]$h }
# an event: its fields (Properties) in the order Windows writes them for that ID
function Ev([int] $Id, [double] $MinutesAgo, $Fields) {
    $p = @()
    foreach ($v in @($Fields)) { $p += @([pscustomobject]@{ Value = $v }) }
    return [pscustomobject]@{ LogName = 'Security'; Id = $Id; TimeCreated = $now.AddMinutes(-$MinutesAgo); Properties = $p }
}
function E4740($User, $Caller, $Ago) { Ev 4740 $Ago @($User, $Caller, 'S-1-5-21-1-2-3-1105', 'S-1-5-18', 'DC01$', 'CORP', '0x3e7') }
function E4767($User, $By, $Ago) { Ev 4767 $Ago @($User, 'CORP', 'S-1-5-21-1-2-3-1105', 'S-1-5-21-1-2-3-500', $By, 'CORP', '0x1234') }
# Kerberos: Status and the address as Windows gives them - a number, the address in IPv6 form
function E4771($User, $Status, $Ip, $Ago) { Ev 4771 $Ago @($User, 'S-1-5-21-1-2-3-1105', 'krbtgt/CORP', '0x40810010', [uint32]$Status, 2, $Ip, 50123, '', '', '') }
function E4768($User, $Status, $Ip, $Ago) { Ev 4768 $Ago @($User, 'CORP', 'S-1-0-0', 'krbtgt', 'S-1-0-0', '0x40810010', [uint32]$Status, '0xffffffff', '-', $Ip, 50124) }
function E4776($User, $Ws, $Status, $Ago) { Ev 4776 $Ago @('MICROSOFT_AUTHENTICATION_PACKAGE_V1_0', $User, $Ws, [uint32]$Status) }
# 4625: Status as a negative Int32 (as some systems hand it over)
function E4625($User, $Ws, $Ip, $Type, $Ago) {
    Ev 4625 $Ago @('S-1-5-18', 'DC01$', 'CORP', '0x3e7', 'S-1-0-0', $User, 'CORP', [int](-1073741715), '%%2313', [uint32]3221225578,
        [uint32]$Type, 'NtLmSsp ', 'NTLM', $Ws, '-', '-', 0, '0x0', '-', $Ip, '0')
}
$FAKE = @{ cim = @{}; events = @(); users = @(); pso = @{}; ptr = @{}; noAd = $false; unlockFail = ''; tasks = @(); procs = @(); quser = @()
    reg = @{}; regkeys = @{}; queries = @() }
$cfg = @{}
function Base {
    $FAKE.cim.Win32_ComputerSystem = @(O @{ Name = 'DC01'; DNSHostName = 'dc01'; Domain = 'corp.test'; DomainRole = 5 })
    $FAKE.domain = O @{ DNSRoot = 'corp.test'; NetBIOSName = 'CORP'; PDCEmulator = 'dc01.corp.test' }
    $FAKE.policy = O @{ LockoutThreshold = 5; LockoutDuration = [timespan]::FromMinutes(30); LockoutObservationWindow = [timespan]::FromMinutes(30) }
    $FAKE.users = @(
        (O @{ SamAccountName = 'jdoe'; UserPrincipalName = 'jdoe@corp.test'; DisplayName = 'Jane Doe'; Enabled = $true; LockedOut = $true
                AccountLockoutTime = $now.AddMinutes(-20); PasswordLastSet = $now.AddDays(-1); PasswordExpired = $false; PasswordNeverExpires = $false
                AccountExpirationDate = $null; DistinguishedName = 'CN=Jane Doe,OU=Staff,DC=corp,DC=test'; SID = 'S-1-5-21-1-2-3-1105'
                badPwdCount = 5; LastBadPasswordAttempt = $now.AddMinutes(-20); lastLogon = $now.AddDays(-1).ToFileTimeUtc() }),
        (O @{ SamAccountName = 'erin'; UserPrincipalName = 'erin@corp.test'; DisplayName = 'Erin'; Enabled = $true; LockedOut = $true
                AccountLockoutTime = $now.AddMinutes(-90); PasswordLastSet = $now.AddDays(-40); PasswordExpired = $false; PasswordNeverExpires = $false
                AccountExpirationDate = $null; DistinguishedName = 'CN=Erin,OU=Staff,DC=corp,DC=test'; SID = 'S-1-5-21-1-2-3-1106'
                badPwdCount = 0; LastBadPasswordAttempt = $null; lastLogon = 0 }),
        (O @{ SamAccountName = 'bob'; UserPrincipalName = ''; DisplayName = 'Bob'; Enabled = $true; LockedOut = $false
                AccountLockoutTime = $now.AddDays(-3); PasswordLastSet = $now.AddDays(-10); PasswordExpired = $false; PasswordNeverExpires = $true
                AccountExpirationDate = $null; DistinguishedName = 'CN=Bob,OU=Staff,DC=corp,DC=test'; SID = 'S-1-5-21-1-2-3-1107'
                badPwdCount = 0; LastBadPasswordAttempt = $null; lastLogon = 0 }),
        (O @{ SamAccountName = "o'brien"; UserPrincipalName = ''; DisplayName = "Pat O'Brien"; Enabled = $true; LockedOut = $false
                AccountLockoutTime = $null; PasswordLastSet = $now.AddDays(-5); PasswordExpired = $true; PasswordNeverExpires = $false
                AccountExpirationDate = $null; DistinguishedName = "CN=Pat O'Brien,OU=Staff,DC=corp,DC=test"; SID = 'S-1-5-21-1-2-3-1108'
                badPwdCount = 1; LastBadPasswordAttempt = $now.AddMinutes(-5); lastLogon = 0 }))
    $FAKE.pso['CN=Erin,OU=Staff,DC=corp,DC=test'] = O @{ Name = 'Admins-PSO'; LockoutThreshold = 3; LockoutDuration = [timespan]::Zero
        LockoutObservationWindow = [timespan]::FromMinutes(15) }
}
Base
switch ($Scenario) {
    'find_one' { $cfg = @{ users = @('CORP\jdoe') } }
    'find_upn' { $cfg = @{ users = @('JDOE@corp.test', 'nobody', 'OTHER\x', "o'brien") } }
    'find_all' { $cfg = @{ users = @(); max_users = 25 } }
    'find_no_ad' { $FAKE.noAd = $true; $cfg = @{ users = @('jdoe') } }
    'dc_pdc' {
        $cfg = @{ users = @(@{ sam = 'jdoe'; upn = 'jdoe@corp.test' }, @{ sam = 'erin'; upn = '' }); hours = 24 }
        $FAKE.events = @(
            (E4740 'jdoe' 'PC123' 20),
            (E4767 'jdoe' 'helpdesk1' 300),
            (E4771 'JDOE' 0x12 '::ffff:192.0.2.50' 10), (E4771 'jdoe' 0x12 '::ffff:192.0.2.50' 15),
            (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 20), (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 25), (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 30),
            (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 35),
            (E4776 'jdoe' '\\PC123' 3221225578 40), (E4776 'jdoe' 'PC123' 0 41),
            (E4768 'jdoe@corp.test' 0 '::ffff:192.0.2.50' 42), (E4768 'jdoe' 0x12 '::ffff:192.0.2.50' 12),
            (E4625 'jdoe' 'WS9' '192.0.2.60' 3 45),
            (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' (60 * 30)),
            (E4771 'someoneelse' 0x18 '::ffff:192.0.2.51' 5))
        $FAKE.ptr['192.0.2.50'] = 'pc123.corp.test.'
    }
    'dc_bdc' {
        $FAKE.cim.Win32_ComputerSystem = @(O @{ Name = 'DC02'; DNSHostName = 'dc02'; Domain = 'corp.test'; DomainRole = 4 })
        $FAKE.users[0].badPwdCount = 3
        $cfg = @{ users = @(@{ sam = 'jdoe'; upn = 'jdoe@corp.test' }); hours = 24 }
        $FAKE.events = @((E4771 'jdoe' 0x18 '::ffff:192.0.2.25' 50), (E4771 'jdoe' 0x18 '::ffff:192.0.2.25' 55),
            (E4771 'jdoe' 0x18 '192.0.2.99' 58))
        $FAKE.ptr['192.0.2.25'] = 'exch01.corp.test'
    }
    'dc_capped' {
        $cfg = @{ users = @(@{ sam = 'jdoe'; upn = '' }); hours = 24; max_events = 3 }
        $FAKE.events = @((E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 1), (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 2),
            (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 3), (E4771 'jdoe' 0x18 '::ffff:192.0.2.50' 4))
    }
    'dc_quote' {
        $cfg = @{ users = @(@{ sam = "o'brien"; upn = '' }); hours = 24 }
        $FAKE.events = @((E4776 "o'brien" 'LAPTOP7' 3221225578 5))
    }
    'unlock_ok' { $cfg = @{ users = @(@{ sam = 'jdoe'; dn = 'CN=Jane Doe,OU=Staff,DC=corp,DC=test' }) } }
    'unlock_denied' { $FAKE.unlockFail = 'Insufficient access rights to perform the operation'; $cfg = @{ users = @(@{ sam = 'jdoe'; dn = 'CN=Jane Doe,OU=Staff,DC=corp,DC=test' }) } }
    'source_pc' {
        $FAKE.cim.Win32_ComputerSystem = @(O @{ Name = 'PC123'; DNSHostName = 'pc123'; Domain = 'corp.test'; DomainRole = 1 })
        $FAKE.cim.Win32_Service = @(
            (O @{ Name = 'BackupAgent'; DisplayName = 'Backup Agent'; StartName = 'CORP\jdoe'; State = 'Running'; StartMode = 'Auto' }),
            (O @{ Name = 'Spooler'; DisplayName = 'Print Spooler'; StartName = 'LocalSystem'; State = 'Running'; StartMode = 'Auto' }),
            (O @{ Name = 'Sync'; DisplayName = 'Sync'; StartName = 'jdoe@corp.test'; State = 'Stopped'; StartMode = 'Manual' }))
        $FAKE.tasks = @((O @{ TaskPath = '\'; TaskName = 'Copy reports'; State = 'Ready'; Principal = (O @{ UserId = 'CORP\jdoe' }) }),
            (O @{ TaskPath = '\Microsoft\'; TaskName = 'Other'; State = 'Ready'; Principal = (O @{ UserId = 'SYSTEM' }) }))
        $FAKE.procs = @((O @{ ProcessName = 'OUTLOOK'; UserName = 'CORP\jdoe' }), (O @{ ProcessName = 'OUTLOOK'; UserName = 'CORP\jdoe' }),
            (O @{ ProcessName = 'explorer'; UserName = 'CORP\other' }))
        $FAKE.quser = @(' USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME',
            ' jdoe                                      3  Disc        1+02:00  10/19/2026 9:00 AM',
            '>other                 rdp-tcp#5           2  Active          .  10/20/2026 8:00 AM')
        $FAKE.regkeys['Registry::HKEY_USERS\S-1-5-21-1-2-3-1105\Network'] = @((O @{ PSChildName = 'H'; PSPath = 'HKU-NET-H' }))
        $FAKE.reg['HKU-NET-H'] = O @{ RemotePath = '\\files\home\jdoe' }
        $cfg = @{ users = @(@{ sam = 'jdoe'; domain = 'corp.test'; netbios = 'CORP'; upn = 'jdoe@corp.test'; sid = 'S-1-5-21-1-2-3-1105' }) }
    }
    'source_nobody' {
        $FAKE.cim.Win32_ComputerSystem = @(O @{ Name = 'PC9'; DNSHostName = 'pc9'; Domain = 'corp.test'; DomainRole = 1 })
        $FAKE.cim.Win32_Service = @()
        $FAKE.quser = @('No User exists for *')
        $cfg = @{ users = @(@{ sam = 'jdoe'; domain = 'corp.test'; netbios = 'CORP'; upn = ''; sid = 'S-1-5-21-1-2-3-1105' }) }
    }
    default { throw "unknown scenario $Scenario" }
}
# ---- the stand-ins ---------------------------------------------------------------------------------------
function Get-CimInstance {
    param([string] $ClassName, [string] $Filter, [string] $Namespace, $ErrorAction)
    $data = $FAKE.cim[$ClassName]
    if ($null -eq $data) { throw "Invalid class $ClassName" }
    @($data)
}
function Import-Module { if ($FAKE.noAd) { throw "The specified module 'ActiveDirectory' was not loaded because no valid module file was found." } }
function Get-ADDomain { param([string] $Server) return $FAKE.domain }
function Get-ADDefaultDomainPasswordPolicy { param([string] $Server) return $FAKE.policy }
function Get-ADUserResultantPasswordPolicy { param($Identity, [string] $Server) return $FAKE.pso[[string]$Identity] }
function Get-ADUser {
    param($Identity, [string] $LDAPFilter, $Filter, $Properties, [string] $Server)
    if ($Identity) {
        $u = @($FAKE.users | Where-Object { $_.DistinguishedName -eq [string]$Identity })
        if (-not $u) { throw "Cannot find an object with identity: '$Identity'" }
        return $u[0]
    }
    if ($LDAPFilter -like '*lockoutTime>=1*') { return @($FAKE.users | Where-Object { $_.AccountLockoutTime }) }
    $v = $LDAPFilter -replace '^\((\w+)=(.*)\)$', '$2'
    $attr = $LDAPFilter -replace '^\((\w+)=(.*)\)$', '$1'
    $v = $v.Replace('\28', '(').Replace('\29', ')').Replace('\2a', '*').Replace('\5c', '\')
    if ($attr -eq 'userPrincipalName') { return @($FAKE.users | Where-Object { $_.UserPrincipalName -and $_.UserPrincipalName -eq $v }) }
    return @($FAKE.users | Where-Object { $_.SamAccountName -eq $v })
}
function Unlock-ADAccount {
    param($Identity, [string] $Server, [switch] $Confirm)
    if ($FAKE.unlockFail) { throw $FAKE.unlockFail }
    foreach ($u in $FAKE.users) { if ($u.DistinguishedName -eq [string]$Identity) { $u.LockedOut = $false } }
}
# reads the query as Windows would: TargetUserName (exact), the IDs of each Select, its time window, Status != 0x0
function Get-WinEvent {
    param($FilterXml, $FilterHashtable, $MaxEvents, $ErrorAction)
    $q = [string]$FilterXml
    $FAKE.queries += @($q)
    $field = @{ 4740 = 0; 4767 = 0; 4771 = 0; 4768 = 0; 4776 = 1; 4625 = 5 }
    $status = @{ 4768 = 6; 4776 = 3 }
    $r = @()
    foreach ($sel in [regex]::Matches($q, '<Select Path="Security">(.*?)</Select>')) {
        $x = $sel.Groups[1].Value
        $ids = @([regex]::Matches($x, 'EventID=(\d+)') | ForEach-Object { [int]$_.Groups[1].Value })
        $names = @([regex]::Matches($x, "TargetUserName'\]=(?:'([^']*)'|`"([^`"]*)`")") | ForEach-Object { ($_.Groups[1].Value + $_.Groups[2].Value).Replace('&lt;', '<').Replace('&amp;', '&') })
        $ms = [double]([regex]::Match($x, '&lt;= (\d+)').Groups[1].Value)
        $noOk = $x.Contains("Data[@Name='Status']!='0x0'")
        foreach ($e in $FAKE.events) {
            if ($ids -notcontains $e.Id) { continue }
            if (($now - $e.TimeCreated).TotalMilliseconds -gt $ms) { continue }
            $n = [string]$e.Properties[$field[$e.Id]].Value
            if (-not @($names | Where-Object { $_ -ceq $n })) { continue }
            if ($noOk -and $status.ContainsKey($e.Id) -and [int64]$e.Properties[$status[$e.Id]].Value -eq 0) { continue }
            $r += @($e)
        }
    }
    $r = @($r | Sort-Object -Property TimeCreated -Descending | Select-Object -First $MaxEvents)
    if ($r.Count -eq 0) { throw 'No events were found that match the specified selection criteria.' }
    $r
}
function Resolve-DnsName {
    param([string] $Name, $Type, [switch] $DnsOnly, [switch] $QuickTimeout, $ErrorAction)
    if (-not $FAKE.ptr.ContainsKey($Name)) { throw "$Name : DNS name does not exist" }
    O @{ NameHost = $FAKE.ptr[$Name] }
}
function Get-ScheduledTask { return $FAKE.tasks }
function Get-Process { param([switch] $IncludeUserName) return $FAKE.procs }
function quser.exe { $global:LASTEXITCODE = 0; return $FAKE.quser }
function Test-Path { param([string] $Path) return $FAKE.regkeys.ContainsKey($Path) }
function Get-ChildItem { param([string] $Path) return $FAKE.regkeys[$Path] }
function Get-ItemProperty { param([string] $Path) return $FAKE.reg[$Path] }
# ---- run it ------------------------------------------------------------------------------------------------
if (-not ($KeepConfig -and $env:SITE_CHECK_CONFIG)) { $env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject $cfg -Depth 6 -Compress }
if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
& $Script
