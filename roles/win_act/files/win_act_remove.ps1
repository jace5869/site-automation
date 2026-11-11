# site-automation - remove ACT for Windows from win_act_dir (roles/win_act, win_act_state: absent).
$ErrorActionPreference = 'Stop'
$cfg = ConvertFrom-Json -InputObject $env:SITE_CHECK_CONFIG
$dir = [string]$cfg.dir
# A junction or symbolic link at the folder (any user can make one in C:\ProgramData) would turn the removal onto
# its target (Windows PowerShell 5.1 removes what a junction points to): refused before anything is changed.
$link = Get-Item -LiteralPath $dir -Force -ErrorAction SilentlyContinue
if ($link -and ([string]$link.Attributes -match 'ReparsePoint')) {
    throw ("$dir is a link ($($link.LinkType) to $($link.Target)), not a folder: refused, nothing was changed. " +
        "Remove the link itself, not its target (in cmd.exe: rmdir `"$dir`"), and run the job again.")
}
$was = Test-Path -LiteralPath $dir
if ($was) { Remove-Item -LiteralPath $dir -Recurse -Force }
'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ removed = $was; dir = $dir } -Compress)
