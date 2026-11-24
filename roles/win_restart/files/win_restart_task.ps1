# site-automation - the scheduled restart (roles/win_restart; docs/WINDOWS.md "Schedule a restart").
# The job "Windows - schedule a restart" writes this file as restart-task-<id>.ps1 with restart-<id>.json beside it
# (each registration its own, never rewritten) into win_restart_dir and registers a
# one-time scheduled task (SYSTEM) that runs it at the chosen time. Then:
#   only_if_pending false: shutdown /r /t <delay> /d <code> /c <comment> (System event 1074 carries the comment)
#   only_if_pending true:  the pending-reboot state is read again first; none pending = no restart (logged in restart.log
#                          and the Application event log, source site-automation, event 101)
# The job's own script (win_restart.ps1) loads the functions below with $SiteRestartLibrary = $true (one pending-reboot
# check for the plan and for the task). Windows PowerShell 5.1 and PowerShell 7.

function Read-SiteRestartProbes {
    # The registry and the ConfigMgr client, read only -> a hashtable the decision below takes (tests feed their own)
    $p = @{ cbs = $false; wu = $false; file_renames = $false; rename = $false; ccm = $null; active_name = ''; pending_name = '' }
    $p.cbs = Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
    $p.wu = Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    $pfro = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
    if ($null -ne $pfro -and @($pfro.PendingFileRenameOperations | Where-Object { "$_".Trim() }).Count -gt 0) { $p.file_renames = $true }
    $a = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName' -Name ComputerName -ErrorAction SilentlyContinue
    $n = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName' -Name ComputerName -ErrorAction SilentlyContinue
    if ($null -ne $a -and $null -ne $n) {
        $p.active_name = [string]$a.ComputerName
        $p.pending_name = [string]$n.ComputerName
        $p.rename = ($p.active_name -ne $p.pending_name)
    }
    try {
        $r = Invoke-CimMethod -Namespace 'root\ccm\ClientSDK' -ClassName CCM_ClientUtilities -MethodName DetermineIfRebootPending -ErrorAction Stop
        $p.ccm = [bool]($r.RebootPending -or $r.IsHardRebootPending)
    }
    catch { $p.ccm = $null }                    # no ConfigMgr client: not asked
    return $p
}

function Get-SiteRestartReasons {
    # The probes -> why a reboot is pending (the same words as the patching check), [] when none
    param($Probes, [bool]$CountFileRenames = $false)
    $why = @()
    if ($Probes.cbs) { $why += @('component servicing') }
    if ($Probes.wu) { $why += @('Windows Update') }
    if ($Probes.ccm -eq $true) { $why += @('ConfigMgr client') }
    if ($Probes.rename) { $why += @('computer rename') }
    if ($Probes.file_renames -and $CountFileRenames) { $why += @('file rename operations') }
    return $why
}

function Get-SiteRestartDecision {
    # restart or not: only_if_pending with no reason = no
    param([bool]$OnlyIfPending, $Reasons)
    $r = @($Reasons | Where-Object { $_ })
    if (-not $OnlyIfPending) { return @{ restart = $true; why = 'restart at the chosen time' } }
    if ($r.Count -gt 0) { return @{ restart = $true; why = 'a reboot is pending: ' + ($r -join ', ') } }
    return @{ restart = $false; why = 'no reboot pending' }
}

function Write-SiteRestartLog {
    param([string]$Dir, [string]$Text)
    $line = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz') + ' ' + $Text
    try { Add-Content -LiteralPath (Join-Path $Dir 'restart.log') -Value $line -Encoding ASCII } catch { }
}

if (-not $SiteRestartLibrary) {
    $dir = $PSScriptRoot
    # this registration's own settings: restart-task-<id>.ps1 reads restart-<id>.json (never another registration's)
    $json = $PSCommandPath -replace 'restart-task-([^\\]+)\.ps1$', 'restart-$1.json'
    if ($json -eq $PSCommandPath) { Write-SiteRestartLog -Dir $dir -Text ('NOT RUN - not a registration script: ' + $PSCommandPath); exit 1 }
    $cfg = Get-Content -LiteralPath $json -Raw | ConvertFrom-Json
    # never before its time: a Run clicked in Task Scheduler (or a clock that jumped) does nothing earlier than 2 minutes before
    $atU = $null
    if ($cfg.at_utc -is [datetime]) { $atU = $cfg.at_utc.ToUniversalTime() }
    elseif ([string]$cfg.at_utc) {
        $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
        $atU = [DateTime]::Parse([string]$cfg.at_utc, [Globalization.CultureInfo]::InvariantCulture, $styles)
    }
    if ($null -eq $atU) { Write-SiteRestartLog -Dir $dir -Text 'NOT RUN - no restart time in its settings'; exit 0 }
    if ((Get-Date).ToUniversalTime() -lt $atU.AddMinutes(-2)) {
        Write-SiteRestartLog -Dir $dir -Text ('NOT YET - started before its time (' + $atU.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") + '): no restart')
        exit 0
    }
    $reasons = @()
    if ($cfg.only_if_pending) { $reasons = @(Get-SiteRestartReasons -Probes (Read-SiteRestartProbes) -CountFileRenames ([bool]$cfg.count_file_renames)) }
    $d = Get-SiteRestartDecision -OnlyIfPending ([bool]$cfg.only_if_pending) -Reasons $reasons
    if (-not $d.restart) {
        $msg = 'site-automation: the scheduled restart did not restart this server: no reboot pending (only if pending). ' + [string]$cfg.comment
        Write-SiteRestartLog -Dir $dir -Text ('SKIPPED - ' + $d.why)
        try { & "$env:SystemRoot\System32\eventcreate.exe" /T INFORMATION /ID 101 /L APPLICATION /SO site-automation /D $msg | Out-Null } catch { }
        exit 0
    }
    Write-SiteRestartLog -Dir $dir -Text ('RESTART - ' + $d.why + ' - ' + [string]$cfg.comment)
    & "$env:SystemRoot\System32\shutdown.exe" /r /t ([int]$cfg.delay) /d ([string]$cfg.code) /c ([string]$cfg.comment)
    exit $LASTEXITCODE
}
