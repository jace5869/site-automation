# site-automation - Windows check: disk (roles/win_check_disk, run by playbooks/win_health_check.yml).
# Every local fixed volume (C:, D:, ...): percent used, and optionally a minimum of free GiB.
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

$warn = [int](Get-Setting 'warn_pct' 85)
$crit = [int](Get-Setting 'crit_pct' 95)
$minFree = [double](Get-Setting 'min_free_gb' 0)
$ignore = @(@(Get-Setting 'ignore' @()) | ForEach-Object { ([string]$_).ToUpper().TrimEnd('\') })
$over = @{}
foreach ($o in @(Get-Setting 'overrides' @())) { $over[([string]$o.drive).ToUpper().TrimEnd('\')] = $o.limits }
$hint = 'Troubleshoot job with ts_area = disk (largest folders); Dism /Online /Cleanup-Image /AnalyzeComponentStore'
$vols = @()
foreach ($d in @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3')) {
    $size = [double]$d.Size
    if ($size -le 0) { continue }
    $drive = ([string]$d.DeviceID).ToUpper()
    if ($ignore -contains $drive) { continue }
    $w = $warn
    $c = $crit
    if ($over.ContainsKey($drive)) {
        if ($null -ne $over[$drive].warn) { $w = [int]$over[$drive].warn }
        if ($null -ne $over[$drive].crit) { $c = [int]$over[$drive].crit }
    }
    $free = [double]$d.FreeSpace
    $pct = [int](100 * ($size - $free) / $size)
    $freeGb = [int]($free / 1GB * 10) / 10
    $sizeGb = [int]($size / 1GB * 10) / 10
    $vols += @(@{ drive = $drive; label = [string]$d.VolumeName; used_pct = $pct; free_gb = $freeGb; size_gb = $sizeGb })
    $tail = ", $freeGb GiB free of $sizeGb GiB"
    if ($pct -ge $c) { Add-Finding "disk:$drive" 'critical' "$drive is $pct% full (critical at $c%)$tail" $hint }
    elseif ($pct -ge $w) { Add-Finding "disk:$drive" 'warning' "$drive is $pct% full (warning at $w%)$tail" $hint }
    elseif ($minFree -gt 0 -and $freeGb -lt $minFree) {
        Add-Finding "disk:$drive" 'warning' "$drive has only $freeGb GiB free (minimum $minFree GiB), $pct% full" $hint
    }
}
$facts.volumes = $vols
Write-Result
