# site-automation - Windows check: patching (roles/win_check_patching, run by playbooks/win_health_check.yml).
# A restart pending for installed updates, days since the last update, optionally missing updates.
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

$warn = [int](Get-Setting 'warn_days' 35)
$crit = [int](Get-Setting 'crit_days' 60)
$hint = 'Troubleshoot job with ts_area = updates; Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10'
# A reboot that updates are waiting for
$reasons = @()
if (Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { $reasons += @('component servicing') }
if (Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { $reasons += @('Windows Update') }
try {
    $ccm = Invoke-CimMethod -Namespace 'root\ccm\ClientSDK' -ClassName CCM_ClientUtilities -MethodName DetermineIfRebootPending -ErrorAction Stop
    if ($ccm.RebootPending -or $ccm.IsHardRebootPending) { $reasons += @('ConfigMgr client') }
}
catch { }
$pfro = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
$facts.pending_file_renames = ($null -ne $pfro)
$facts.reboot_pending = $reasons
if ($reasons.Count -gt 0 -and (Get-Setting 'reboot_pending' $true)) {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    Add-Finding 'patching:reboot' 'warning' ('a restart is pending ({0}): installed updates are not active until the server restarts; up since {1}' -f ($reasons -join ', '), ([datetime]$os.LastBootUpTime).ToString('yyyy-MM-dd')) $hint
}
# When were updates last installed
$hf = @(Get-HotFix -ErrorAction SilentlyContinue | Where-Object { $_.InstalledOn } | Sort-Object -Property InstalledOn -Descending)
if ($hf.Count -eq 0) {
    Add-Finding 'patching:age' 'warning' 'no installed updates were found (Get-HotFix is empty)' $hint
}
else {
    $last = [datetime]$hf[0].InstalledOn
    $days = [int]((Get-Date) - $last).TotalDays
    $facts.last_update = $last.ToString('yyyy-MM-dd')
    $facts.last_kb = [string]$hf[0].HotFixID
    $facts.days_since_update = $days
    $text = 'the last update ({0}) was installed {1} days ago, on {2}' -f $hf[0].HotFixID, $days, $last.ToString('yyyy-MM-dd')
    if ($days -ge $crit) { Add-Finding 'patching:age' 'critical' "$text (critical at $crit days)" $hint }
    elseif ($days -ge $warn) { Add-Finding 'patching:age' 'warning' "$text (warning at $warn days)" $hint }
}
# Optional, slower: ask Windows Update which updates are missing (needs full .NET / COM)
if (Get-Setting 'search_pending' $false) {
    if (-not $fullLanguage) { $facts.search = 'skipped: Constrained Language Mode' }
    else {
        try {
            $session = New-Object -ComObject Microsoft.Update.Session
            $result = $session.CreateUpdateSearcher().Search("IsInstalled=0 and Type='Software' and IsHidden=0")
            $titles = @()
            $sev = @{}
            foreach ($u in $result.Updates) {
                $titles += @([string]$u.Title)
                $s = [string]$u.MsrcSeverity
                if (-not $s) { $s = 'unrated' }
                if ($sev.ContainsKey($s)) { $sev[$s] = $sev[$s] + 1 } else { $sev[$s] = 1 }
            }
            $facts.missing_updates = $titles
            if ($titles.Count -gt 0) {
                $by = @($sev.Keys | ForEach-Object { '{0} {1}' -f $sev[$_], $_ }) -join ', '
                $level = 'warning'
                if ($sev.ContainsKey('Critical')) { $level = 'critical' }
                Add-Finding 'patching:missing' $level ("$($titles.Count) update(s) are available and not installed ($by); first: " + $titles[0]) $hint
            }
        }
        catch { $facts.search = 'failed: ' + $_.Exception.Message }
    }
}
Write-Result
