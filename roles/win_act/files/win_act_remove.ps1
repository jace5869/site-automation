# site-automation - remove ACT for Windows from win_act_dir (roles/win_act, win_act_state: absent).
$ErrorActionPreference = 'Stop'
$cfg = ConvertFrom-Json -InputObject $env:SITE_CHECK_CONFIG
$dir = [string]$cfg.dir
$was = Test-Path -LiteralPath $dir
if ($was) { Remove-Item -LiteralPath $dir -Recurse -Force }
'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ removed = $was; dir = $dir } -Compress)
