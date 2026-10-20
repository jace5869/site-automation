# site-automation - account lockout (roles/ad_lockout, playbooks/ad_lockout.yml with ad_lockout_unlock):
# unlock the accounts given, on THIS DC (the PDC emulator, which every DC tells about a lockout first),
# then read each one back. The ONLY change the lockout job makes. Only accounts locked out now are
# given to it. Everything is done on THIS DC (localhost): no second hop.
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
$out = @{ errors = @(); unlocked = @() }
try { Import-Module ActiveDirectory -ErrorAction Stop -WarningAction SilentlyContinue }
catch { $out.errors += @('ActiveDirectory PowerShell module: ' + [string]$_.Exception.Message) }
foreach ($u in $users) {
    $r = @{ sam = [string]$u.sam; ok = $false; locked_after = $null; error = '' }
    if (-not $out.errors) {
        try {
            Unlock-ADAccount -Identity ([string]$u.dn) -Server localhost -Confirm:$false
            $a = Get-ADUser -Identity ([string]$u.dn) -Properties LockedOut -Server localhost
            $r.locked_after = [bool]$a.LockedOut
            $r.ok = -not $r.locked_after
            if ($r.locked_after) { $r.error = 'still locked out after the unlock' }
        }
        catch { $r.error = [string]$_.Exception.Message }
    }
    else { $r.error = 'not unlocked: ' + $out.errors[0] }
    $out.unlocked += @($r)
}

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ unlock = $out } -Depth 5 -Compress)
