# site-automation - account lockout (roles/ad_lockout, run by playbooks/ad_lockout.yml on every DC):
# what THIS DC knows about the accounts looked for. Read-only.
#   - its own badPwdCount, last bad password and last logon of each account: these are NOT
#     replicated, every DC counts the bad passwords it was given itself
#   - its Security log for those accounts: 4740 locked out (and the caller computer), 4767 unlocked
#     (by whom), 4771 Kerberos pre-authentication failed (the client's address), 4768 a Kerberos
#     ticket refused (e.g. asked for after the lockout), 4776 NTLM credential validation failed (the
#     workstation), 4625 a logon to this DC failed - each with the address or computer it came from
# Each address is resolved to a name (reverse DNS, asked of this DC). The judging - which computer
# is locking the account out, and what to do - is done by the report (plugins/filter/ad_lockout_filters.py).
# Everything is asked of THIS DC (localhost): no second hop.
# ---- common to every site-automation Windows script (keep identical in all of them) -----------
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG (the role's defaults and your inventory).
# The script prints ONE line: ###SITE-JSON### {...}
# Windows PowerShell 5.1 and PowerShell 7; also works in Constrained Language Mode.
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
function Get-Setting([string] $Name, $Default) {
    if ($null -ne $cfg -and $null -ne $cfg.$Name) { return $cfg.$Name }
    return $Default
}
# ---- end of the common part --------------------------------------------------------------------

$users = @(Get-Setting 'users' @())
$hours = [double](Get-Setting 'hours' 24)
$maxEvents = [int](Get-Setting 'max_events' 2000)
$resolveMax = [int](Get-Setting 'resolve_max' 50)
$now = Get-Date
$ms = [int64]($hours * 3600000)
$dc = @{ errors = @(); read_at = ''; users = @(); events = @(); capped = @(); names = @{} }
function Iso($t) {
    if ($null -eq $t) { return '' }
    try { return ([datetime]$t).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } catch { return '' }
}
function Note([string] $What, $Err) {
    $msg = [string]$Err
    if ($Err -and $Err.Exception) { $msg = [string]$Err.Exception.Message }
    $script:dc.errors += @(('{0}: {1}' -f $What, $msg.Trim()))
}
function Ldap([string] $V) {
    return $V.Replace('\', '\5c').Replace('*', '\2a').Replace('(', '\28').Replace(')', '\29').Replace([string][char]0, '\00')
}
# a status code as the event log shows it: 0x18, 0xc000006a (the field is a number, or already text)
function Hex($V) {
    $s = [string]$V
    if ($s -match '^0x') { return $s.ToLower() }
    try {
        $n = [int64]$s
        if ($n -lt 0) { $n += 4294967296 }
        return ('0x{0:x}' -f $n)
    }
    catch { return $s.ToLower() }
}
function Address([string] $V) {
    $s = $V.Trim()
    if ($s -eq '-' -or $s -eq '::1' -or $s -eq '127.0.0.1') { return '' }
    if ($s.StartsWith('::ffff:')) { $s = $s.Substring(7) }
    return $s
}
function Computer([string] $V) {
    $s = $V.Trim().TrimStart('\')
    if ($s -eq '-') { return '' }
    return $s
}
# a name as an XPath string inside the query's XML: in double quotes when it has an apostrophe
# (O'Brien - an account name never has a double quote), & and < written as XML
function XStr([string] $V) {
    $s = $V.Replace('&', '&amp;').Replace('<', '&lt;')
    if ($s.Contains("'")) { return '"' + $s + '"' }
    return "'" + $s + "'"
}
$dc.read_at = Iso $now

# ---- who am I --------------------------------------------------------------------------------
try {
    $cs = @(Get-CimInstance -ClassName Win32_ComputerSystem)[0]
    $dc.name = [string]$cs.Name
    $dc.domain = ([string]$cs.Domain).ToLower()
    $dc.fqdn = ('{0}.{1}' -f $cs.DNSHostName, $cs.Domain).ToLower()
    $dc.role = $(if ([int]$cs.DomainRole -eq 5) { 'pdc' } elseif ([int]$cs.DomainRole -eq 4) { 'dc' } else { 'member' })
}
catch { Note 'computer' $_ }

$adModule = $true
try { Import-Module ActiveDirectory -ErrorAction Stop -WarningAction SilentlyContinue }
catch { $adModule = $false; Note 'ActiveDirectory PowerShell module' $_ }

# ---- each account, as this DC counts it --------------------------------------------------------------
$props = @('SamAccountName', 'LockedOut', 'AccountLockoutTime', 'badPwdCount', 'LastBadPasswordAttempt', 'lastLogon')
foreach ($u in $users) {
    $sam = [string]$u.sam
    $r = @{ sam = $sam; read = $false; locked = $null; lockout_time = ''; bad_pwd_count = $null; bad_password_time = ''; last_logon = '' }
    if ($adModule) {
        try {
            $a = @(Get-ADUser -LDAPFilter ('(sAMAccountName={0})' -f (Ldap $sam)) -Properties $props -Server localhost) | Select-Object -First 1
            if ($a) {
                $r.read = $true
                $r.locked = [bool]$a.LockedOut
                $r.lockout_time = Iso $a.AccountLockoutTime
                $r.bad_pwd_count = [int]$a.badPwdCount
                $r.bad_password_time = Iso $a.LastBadPasswordAttempt
                $ll = [int64]$a.lastLogon
                if ($ll -gt 0) { $r.last_logon = Iso ([datetime]::FromFileTimeUtc($ll)) }
            }
        }
        catch { Note ('account ' + $sam) $_ }
    }
    $dc.users += @($r)
}

# ---- the Security log --------------------------------------------------------------------------------
# One query per account, by TargetUserName (the name as typed by the client may differ in case: the
# stored name, lower and upper case, and the UPN are all asked for). Successes are left out in the
# query itself (4768 / 4776 with Status 0x0), so the cap is spent on failures.
# Fields (Properties) by position:
#   4740: 0 account, 1 caller computer;  4767: 0 account, 4 by, 5 by domain
#   4771: 0 account, 4 status, 6 client address;  4768: 0 account, 6 status, 9 client address
#   4776: 0 package, 1 account, 2 workstation, 3 status
#   4625: 5 account, 7 status, 9 sub status, 10 logon type, 11 logon process, 12 package, 13 workstation, 18 process, 19 address
$ips = @{}
foreach ($u in $users) {
    $sam = [string]$u.sam
    $forms = @($sam, $sam.ToLower(), $sam.ToUpper())
    if ([string]$u.upn) { $forms += @([string]$u.upn) }
    $forms = @($forms | Select-Object -Unique)
    $who = (@($forms | ForEach-Object { "Data[@Name='TargetUserName']=" + (XStr $_) }) -join ' or ')
    $when = "TimeCreated[timediff(@SystemTime) &lt;= $ms]"
    $q = '<QueryList><Query Id="0" Path="Security">' +
        "<Select Path=`"Security`">*[System[(EventID=4740 or EventID=4767 or EventID=4771 or EventID=4625) and $when]] and *[EventData[$who]]</Select>" +
        "<Select Path=`"Security`">*[System[(EventID=4768 or EventID=4776) and $when]] and *[EventData[($who) and Data[@Name='Status']!='0x0']]</Select>" +
        '</Query></QueryList>'
    $evs = @()
    try { $evs = @(Get-WinEvent -FilterXml $q -MaxEvents $maxEvents -ErrorAction Stop) }
    catch {
        $m = [string]$_.Exception.Message
        if ($m -match 'query is invalid') {
            # an older Windows that does not take the Status condition: ask without it (successes are left out below)
            try { $evs = @(Get-WinEvent -FilterXml $q.Replace(" and Data[@Name='Status']!='0x0'", '') -MaxEvents $maxEvents -ErrorAction Stop) }
            catch {
                if ([string]$_.Exception.Message -notmatch 'No events were found') { Note ('Security log for ' + $sam) $_ }
            }
        }
        elseif ($m -notmatch 'No events were found') { Note ('Security log for ' + $sam) $_ }
    }
    if ($evs.Count -ge $maxEvents) { $dc.capped += @($sam) }
    foreach ($e in $evs) {
        $p = @($e.Properties | ForEach-Object { [string]$_.Value })
        $id = [int]$e.Id
        $r = @{ user = $sam; id = $id; time = Iso $e.TimeCreated; computer = ''; ip = ''; status = ''; sub_status = ''; logon_type = $null
            process = ''; package = ''; by = '' }
        if ($id -eq 4740 -and $p.Count -gt 1) { $r.computer = Computer $p[1] }
        elseif ($id -eq 4767 -and $p.Count -gt 5) { $r.by = '{0}\{1}' -f $p[5], $p[4] }
        elseif ($id -eq 4771 -and $p.Count -gt 6) { $r.status = Hex $p[4]; $r.ip = Address $p[6] }
        elseif ($id -eq 4768 -and $p.Count -gt 9) { $r.status = Hex $p[6]; $r.ip = Address $p[9] }
        elseif ($id -eq 4776 -and $p.Count -gt 3) { $r.package = $p[0]; $r.computer = Computer $p[2]; $r.status = Hex $p[3] }
        elseif ($id -eq 4625 -and $p.Count -gt 19) {
            $r.status = Hex $p[7]; $r.sub_status = Hex $p[9]; $r.logon_type = [int]$p[10]; $r.package = $p[12].Trim()
            $r.computer = Computer $p[13]; $r.process = $(if ($p[18] -eq '-') { '' } else { $p[18] }); $r.ip = Address $p[19]
        }
        if ($r.status -eq '0x0') { continue }
        if ($r.ip) { $ips[$r.ip] = $true }
        $dc.events += @($r)
    }
}

# ---- the addresses, as names (reverse DNS) ----------------------------------------------------------------
$n = 0
foreach ($ip in @($ips.Keys | Sort-Object)) {
    if ($n -ge $resolveMax) { break }
    $n++
    try {
        $a = @(Resolve-DnsName -Name $ip -Type PTR -DnsOnly -QuickTimeout -ErrorAction Stop | Where-Object { $_.NameHost }) | Select-Object -First 1
        if ($a) { $dc.names[$ip] = ([string]$a.NameHost).ToLower().TrimEnd('.') }
    }
    catch { $dc.names[$ip] = '' }
}

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ dc = $dc } -Depth 6 -Compress)
