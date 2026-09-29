# site-automation - Windows check: accounts (roles/win_check_accounts, run by playbooks/win_health_check.yml).
# Guest disabled, local password age and never-expires, inactive local accounts, local Administrators members.
# Read-only: it changes nothing on the server.
# ---- common to every site-automation Windows check (keep identical in all of them) -----------
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG (the role's defaults and your inventory).
# The script prints ONE line: ###SITE-JSON### {"findings": [{id, severity, summary, hint}], "facts": {...}}
# Windows PowerShell 5.1 and PowerShell 7; also works in Constrained Language Mode (a few parts
# that need full .NET say so and are skipped there).
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
$findings = @()
$facts = @{}
$fullLanguage = ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage')
function Add-Finding([string] $Id, [string] $Severity, [string] $Summary, [string] $Hint) {
    $script:findings += @(@{ id = $Id; severity = $Severity; summary = $Summary; hint = $Hint })
}
function Get-Setting([string] $Name, $Default) {
    if ($null -ne $cfg -and $null -ne $cfg.$Name) { return $cfg.$Name }
    return $Default
}
function Write-Result {
    '###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ findings = @($script:findings); facts = $script:facts } -Depth 6 -Compress)
}
# ---- end of the common part --------------------------------------------------------------------

$cs = Get-CimInstance -ClassName Win32_ComputerSystem
if ([int]$cs.DomainRole -ge 4) {
    $facts.note = 'domain controller: it has no local accounts (domain accounts are managed in AD)'
    Write-Result
    return
}
$maxAge = [int](Get-Setting 'password_max_age_days' 60)
$inactiveDays = [int](Get-Setting 'inactive_days' 35)
$neverAllowed = @(Get-Setting 'password_never_expires_allowed' @())
$inactiveAllowed = @(Get-Setting 'inactive_allowed' @())
function Test-Like([string] $Name, $Patterns) {
    foreach ($p in @($Patterns)) { if ($Name -like [string]$p) { return $true } }
    return $false
}
$now = Get-Date
$users = $null
try { $users = @(Get-LocalUser -ErrorAction Stop) } catch { $users = $null }
if ($null -eq $users) {
    $facts.note = 'Get-LocalUser is not available: only the Guest account and the Administrators group are checked'
    foreach ($u in @(Get-CimInstance -ClassName Win32_UserAccount -Filter 'LocalAccount=True')) {
        if (([string]$u.SID).EndsWith('-501') -and -not $u.Disabled) {
            Add-Finding 'accounts:guest' 'critical' "the built-in Guest account ($($u.Name)) is enabled" 'Get-LocalUser; Disable-LocalUser -Name Guest'
        }
    }
}
else {
    $facts.local_users = @($users | ForEach-Object { '{0} ({1})' -f $_.Name, $(if ($_.Enabled) { 'enabled' } else { 'disabled' }) })
    foreach ($u in $users) {
        $name = [string]$u.Name
        $sid = [string]$u.SID
        if ($sid.EndsWith('-501')) {
            if ($u.Enabled) { Add-Finding 'accounts:guest' 'critical' "the built-in Guest account ($name) is enabled" 'Get-LocalUser; Disable-LocalUser -Name Guest' }
            continue
        }
        if (-not $u.Enabled) { continue }
        if ($null -eq $u.PasswordExpires -and -not (Test-Like $name $neverAllowed)) {
            Add-Finding "accounts:never-expires:$name" 'warning' "local account $name has a password that never expires" "Get-LocalUser $name | Format-List *; Set-LocalUser -Name $name -PasswordNeverExpires `$false"
        }
        if ($null -ne $u.PasswordLastSet -and $maxAge -gt 0) {
            $age = [int]($now - [datetime]$u.PasswordLastSet).TotalDays
            if ($age -gt $maxAge) { Add-Finding "accounts:password-age:$name" 'warning' "the password of local account $name is $age days old (maximum $maxAge)" "Get-LocalUser $name | Format-List PasswordLastSet, PasswordExpires" }
        }
        if ($inactiveDays -gt 0 -and -not (Test-Like $name $inactiveAllowed)) {
            $last = 'never'
            $idle = $true
            if ($null -ne $u.LastLogon) {
                $last = ([datetime]$u.LastLogon).ToString('yyyy-MM-dd')
                $idle = ([int]($now - [datetime]$u.LastLogon).TotalDays -gt $inactiveDays)
            }
            if ($idle) { Add-Finding "accounts:inactive:$name" 'warning' "local account $name is enabled but has not logged on for more than $inactiveDays days (last: $last)" "Get-LocalUser $name | Format-List LastLogon, Enabled; Disable-LocalUser -Name $name" }
        }
    }
}
# Members of the local Administrators group (S-1-5-32-544)
$members = @()
try { $members = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop | ForEach-Object { [string]$_.Name }) }
catch {
    # Get-LocalGroupMember fails on orphaned SIDs: read `net localgroup` instead
    $g = Get-CimInstance -ClassName Win32_Group -Filter "SID='S-1-5-32-544'"
    $lines = @(& net.exe localgroup $g.Name 2>&1 | ForEach-Object { [string]$_ })
    $in = $false
    foreach ($l in $lines) {
        if ($l -match '^-{5,}') { $in = $true; continue }
        if ($in -and $l.Trim() -and $l -notmatch 'command completed') { $members += @($l.Trim()) }
    }
}
$facts.administrators = $members
$allowed = @(Get-Setting 'admins_allowed' @())
if ($allowed.Count -gt 0) {
    foreach ($m in $members) {
        if (-not (Test-Like $m $allowed)) {
            Add-Finding "accounts:admin:$m" 'warning' "$m is a member of the local Administrators group, and not in win_check_accounts_admins_allowed" 'Get-LocalGroupMember -SID S-1-5-32-544'
        }
    }
}
Write-Result
