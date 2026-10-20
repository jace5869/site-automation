# site-automation - account lockout (roles/ad_lockout, run by playbooks/ad_lockout.yml on every DC):
# which accounts to look for. The accounts asked for (sAMAccountName, DOMAIN\name or a UPN) found in
# THIS DC's domain - or, when none is asked for, every account locked out now - with what the
# domain's (or the account's fine-grained) lockout policy is. Read-only. The PDC emulator's answer is
# the one used (the lockout is always recorded there first); the others only say who they are.
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

$names = @(Get-Setting 'users' @() | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })
$maxUsers = [int](Get-Setting 'max_users' 25)
$now = Get-Date
$F = @{ errors = @(); read_at = ''; users = @(); locked = @(); locked_total = 0; policy = $null }
function Iso($t) {
    if ($null -eq $t) { return '' }
    try { return ([datetime]$t).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } catch { return '' }
}
function Note([string] $What, $Err) {
    $msg = [string]$Err
    if ($Err -and $Err.Exception) { $msg = [string]$Err.Exception.Message }
    $script:F.errors += @(('{0}: {1}' -f $What, $msg.Trim()))
}
# a value inside an LDAP filter: \ * ( ) and NUL escaped (RFC 4515)
function Ldap([string] $V) {
    return $V.Replace('\', '\5c').Replace('*', '\2a').Replace('(', '\28').Replace(')', '\29').Replace([string][char]0, '\00')
}
function Minutes($Span) {
    if ($null -eq $Span) { return $null }
    try { return [double]([timespan]$Span).TotalMinutes } catch { return $null }
}
$F.read_at = Iso $now

# ---- who am I --------------------------------------------------------------------------------
try {
    $cs = @(Get-CimInstance -ClassName Win32_ComputerSystem)[0]
    $F.name = [string]$cs.Name
    $F.domain = ([string]$cs.Domain).ToLower()
    $F.fqdn = ('{0}.{1}' -f $cs.DNSHostName, $cs.Domain).ToLower()
    $F.role = $(if ([int]$cs.DomainRole -eq 5) { 'pdc' } elseif ([int]$cs.DomainRole -eq 4) { 'dc' } else { 'member' })
}
catch { Note 'computer' $_ }

$F.ad_module = $true
try { Import-Module ActiveDirectory -ErrorAction Stop -WarningAction SilentlyContinue }
catch { $F.ad_module = $false; Note 'ActiveDirectory PowerShell module' $_ }

$props = @('DisplayName', 'UserPrincipalName', 'Enabled', 'LockedOut', 'AccountLockoutTime', 'PasswordLastSet', 'PasswordExpired',
    'PasswordNeverExpires', 'AccountExpirationDate', 'DistinguishedName', 'SamAccountName', 'SID', 'badPwdCount', 'LastBadPasswordAttempt')
function User-Record($u) {
    $r = @{ sam = [string]$u.SamAccountName; upn = [string]$u.UserPrincipalName; sid = [string]$u.SID; dn = [string]$u.DistinguishedName
        display = [string]$u.DisplayName; enabled = [bool]$u.Enabled; locked = [bool]$u.LockedOut; lockout_time = Iso $u.AccountLockoutTime
        password_set = Iso $u.PasswordLastSet; password_expired = [bool]$u.PasswordExpired; password_never_expires = [bool]$u.PasswordNeverExpires
        expires = Iso $u.AccountExpirationDate; policy = '' ; threshold = $null; duration_min = $null; window_min = $null }
    try {
        $pso = Get-ADUserResultantPasswordPolicy -Identity $u.DistinguishedName -Server localhost
        if ($pso) {
            $r.policy = [string]$pso.Name
            $r.threshold = [int]$pso.LockoutThreshold
            $r.duration_min = Minutes $pso.LockoutDuration
            $r.window_min = Minutes $pso.LockoutObservationWindow
        }
    }
    catch { Note ('fine-grained password policy of ' + $r.sam) $_ }
    return $r
}

if ($F.ad_module) {
    try {
        $d = Get-ADDomain -Server localhost
        $F.domain = ([string]$d.DNSRoot).ToLower()
        $F.netbios = [string]$d.NetBIOSName
        $F.pdc = ([string]$d.PDCEmulator).ToLower()
    }
    catch { Note 'Get-ADDomain' $_ }
    try {
        $p = Get-ADDefaultDomainPasswordPolicy -Server localhost
        $F.policy = @{ threshold = [int]$p.LockoutThreshold; duration_min = Minutes $p.LockoutDuration; window_min = Minutes $p.LockoutObservationWindow }
    }
    catch { Note 'the domain''s lockout policy' $_ }

    # ---- the accounts asked for -------------------------------------------------------------------
    foreach ($n in $names) {
        $n = $n.Trim()
        $r = @{ input = $n; here = $true; found = $false }
        $sam = $n
        $upn = ''
        if ($n.Contains('\')) {
            $dom = $n.Substring(0, $n.IndexOf('\'))
            $sam = $n.Substring($n.IndexOf('\') + 1)
            if ($dom -ne [string]$F.netbios -and $dom -ne [string]$F.domain) { $r.here = $false }
        }
        elseif ($n.Contains('@')) {
            $upn = $n
            $sam = $n.Substring(0, $n.IndexOf('@'))
            if ($n.Substring($n.IndexOf('@') + 1) -ne [string]$F.domain) { $sam = '' }
        }
        if ($r.here) {
            try {
                $u = $null
                if ($upn) { $u = @(Get-ADUser -LDAPFilter ('(userPrincipalName={0})' -f (Ldap $upn)) -Properties $props -Server localhost) | Select-Object -First 1 }
                if (-not $u -and $sam) { $u = @(Get-ADUser -LDAPFilter ('(sAMAccountName={0})' -f (Ldap $sam)) -Properties $props -Server localhost) | Select-Object -First 1 }
                if ($u) {
                    $r = (User-Record $u)
                    $r.input = $n
                    $r.here = $true
                    $r.found = $true
                }
            }
            catch { Note ('account ' + $n) $_ }
        }
        $F.users += @($r)
    }

    # ---- no account asked for: every account locked out now -------------------------------------------
    # lockoutTime is kept after the lockout duration has passed (until the next logon): LockedOut,
    # which the DC works out from the policy, says whether it still is.
    if ($names.Count -eq 0) {
        try {
            $all = @(Get-ADUser -LDAPFilter '(&(objectCategory=person)(objectClass=user)(lockoutTime>=1))' -Properties $props -Server localhost |
                    Where-Object { $_.LockedOut })
            $F.locked_total = $all.Count
            $sorted = @($all | Sort-Object -Property AccountLockoutTime -Descending | Select-Object -First $maxUsers)
            foreach ($u in $sorted) {
                $r = (User-Record $u)
                $r.input = $r.sam
                $r.here = $true
                $r.found = $true
                $F.locked += @($r)
            }
        }
        catch { Note 'accounts locked out' $_ }
    }
}

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ find = $F } -Depth 6 -Compress)
