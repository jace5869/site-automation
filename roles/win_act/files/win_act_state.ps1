# site-automation - ACT for Windows: what is installed where, and is it locked down?
# (roles/win_act, playbooks/win_act_install.yml). Read-only.
# Input: $env:SITE_CHECK_CONFIG = {"dir": "C:\\ProgramData\\act", "sha256": "..."}
# Prints ONE line: ###SITE-JSON### {present, sha256, sha_ok, unblocked, dir_ok, file_ok, ok, problems}
# Owners and permissions are read from the SDDL text, so it also works in Constrained Language Mode.
$ErrorActionPreference = 'Stop'
$cfg = ConvertFrom-Json -InputObject $env:SITE_CHECK_CONFIG
$dir = [string]$cfg.dir
$want = ([string]$cfg.sha256).ToUpper()
$file = Join-Path -Path $dir -ChildPath 'act.ps1'
# only Administrators (BA / S-1-5-32-544) and SYSTEM (SY / S-1-5-18) may own it or have rights on it
$trusted = @('BA', 'SY', 'S-1-5-32-544', 'S-1-5-18')
function Test-Sddl([string] $Sddl, [bool] $NeedProtected) {
    $owner = ''
    if ($Sddl -match '^O:([^:]+?)G:') { $owner = $Matches[1] }
    $dacl = ''
    if ($Sddl -match 'D:([^()]*)(\(.*)$') { $flags = $Matches[1]; $dacl = $Matches[2] } else { $flags = '' }
    $sids = @([regex]::Matches($dacl, '\(([^;]*);([^;]*);([^;]*);([^;]*);([^;]*);([^)]*)\)') | ForEach-Object { $_.Groups[6].Value })
    $types = @([regex]::Matches($dacl, '\(([^;]*);') | ForEach-Object { $_.Groups[1].Value })
    $others = @($sids | Where-Object { $trusted -notcontains $_ })
    $ok = ($trusted -contains $owner) -and ($others.Count -eq 0) -and ($sids.Count -gt 0)
    if ($NeedProtected -and $flags -notmatch 'P') { $ok = $false }
    return @{ ok = $ok; owner = $owner; others = $others; protected = ($flags -match 'P') }
}
$r = @{ dir = $dir; present = (Test-Path -LiteralPath $file); sha256 = ''; sha_ok = $false; unblocked = $false
    dir_ok = $false; file_ok = $false; problems = @()
    language_mode = [string]$ExecutionContext.SessionState.LanguageMode }
if (Test-Path -LiteralPath $dir) {
    $d = Test-Sddl ((Get-Acl -LiteralPath $dir).Sddl) $true
    $r.dir_ok = $d.ok
    if (-not $d.ok) { $r.problems += @("the folder is owned by $($d.owner), protected=$($d.protected), others with rights: $($d.others -join ', ')") }
}
else { $r.problems += @('the folder does not exist') }
if ($r.present) {
    $r.sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToUpper()
    $r.sha_ok = ($r.sha256 -eq $want)
    if (-not $r.sha_ok) { $r.problems += @('act.ps1 is not the version in the repository (SHA256 differs)') }
    # blocked = marked as downloaded from the internet (a Zone.Identifier stream)
    $blocked = $false
    try { $blocked = ($null -ne (Get-Item -LiteralPath $file -Stream 'Zone.Identifier' -ErrorAction Stop)) } catch { $blocked = $false }
    $r.unblocked = -not $blocked
    if ($blocked) { $r.problems += @('act.ps1 is blocked (marked as downloaded from the internet)') }
    $f = Test-Sddl ((Get-Acl -LiteralPath $file).Sddl) $false
    $r.file_ok = $f.ok
    if (-not $f.ok) { $r.problems += @("act.ps1 is owned by $($f.owner), others with rights: $($f.others -join ', ')") }
}
else { $r.problems += @('act.ps1 is not there') }
$r.ok = $r.dir_ok -and $r.file_ok -and $r.sha_ok -and $r.unblocked
'###SITE-JSON### ' + (ConvertTo-Json -InputObject $r -Compress)
