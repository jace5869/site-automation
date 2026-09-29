# site-automation - Windows patching through the ConfigMgr client, i.e. what Software Center shows
# (roles/win_patch, playbooks/win_patch.yml). One script, several steps; the step and its
# settings arrive as JSON in $env:SITE_CHECK_CONFIG, e.g. {"action": "list"}.
#
#   preflight  is the ConfigMgr client there and running; language mode; restart pending;
#              the automatic services running now (to compare after the restart)
#   scan       ask the client to rescan for updates and re-evaluate its deployments
#   list       the updates Software Center has for this server and where each one stands
#   install    start installing every update that is missing (like "Install all")
#   restart    restart the server now (shutdown.exe, planned: security fix), after N seconds
#   boot       when the server last started
#   services   which of the given services are not running (yet)
#
# Prints ONE line: ###SITE-JSON### {...}. Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
$action = [string]$cfg.action
$sdk = 'root\ccm\ClientSDK'
# CCM_SoftwareUpdate.EvaluationState, by number
$stateNames = @('none', 'available', 'submitted', 'detecting', 'pre-download', 'downloading',
    'waiting to install', 'installing', 'restart pending (soft)', 'restart pending (hard)', 'waiting for restart',
    'verifying', 'installed', 'error', 'waiting for a maintenance window', 'waiting for a user to log on',
    'waiting for a user to log off', 'waiting for a user to log on (job)', 'waiting for a user to reconnect',
    'pending user logoff', 'pending update', 'waiting to retry', 'waiting for presentation mode to end',
    'waiting for orchestration')
function Out-Result($r) { '###SITE-JSON### ' + (ConvertTo-Json -InputObject $r -Depth 6 -Compress) }
function Get-StateName([int] $s) {
    if ($s -ge 0 -and $s -lt $stateNames.Count) { return $stateNames[$s] }
    return "state $s"
}
function Get-Missing {
    return @(Get-CimInstance -Namespace $sdk -ClassName CCM_SoftwareUpdate -Filter 'ComplianceState=0' -ErrorAction Stop)
}
function Test-RestartPending {
    $why = @()
    try {
        $r = Invoke-CimMethod -Namespace $sdk -ClassName CCM_ClientUtilities -MethodName DetermineIfRebootPending -ErrorAction Stop
        if ($r.RebootPending -or $r.IsHardRebootPending) { $why += @('ConfigMgr client') }
    }
    catch { }
    if (Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { $why += @('component servicing') }
    if (Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { $why += @('Windows Update') }
    return $why
}
function Get-BootTime {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    return ([datetime]$os.LastBootUpTime).ToString('yyyy-MM-ddTHH:mm:ss')
}
switch ($action) {
    'preflight' {
        $client = $true
        $why = ''
        try { $null = Get-CimInstance -Namespace 'root\ccm' -ClassName SMS_Client -ErrorAction Stop }
        catch { $client = $false; $why = $_.Exception.Message }
        $svc = Get-CimInstance -ClassName Win32_Service -Filter "Name='CcmExec'"
        $state = 'not installed'
        if ($null -ne $svc) { $state = [string]$svc.State }
        $running = @(Get-CimInstance -ClassName Win32_Service -Filter "StartMode='Auto' AND State='Running'" | ForEach-Object { [string]$_.Name })
        $pending = @()
        if ($client) { $pending = @(Test-RestartPending) }
        Out-Result @{ client = $client; client_error = $why; ccmexec = $state; boot = (Get-BootTime)
            language_mode = [string]$ExecutionContext.SessionState.LanguageMode
            restart_pending = $pending; running_services = $running }
    }
    'scan' {
        # Software Updates Scan Cycle; then (unless evaluate is false, as in a dry run) the
        # Software Updates Assignments Evaluation Cycle, which can itself start required updates
        # whose deadline has passed.
        $ids = @('{00000000-0000-0000-0000-000000000113}')
        if ($cfg.evaluate -ne $false) { $ids += @('{00000000-0000-0000-0000-000000000108}') }
        foreach ($id in $ids) {
            $null = Invoke-CimMethod -Namespace 'root\ccm' -ClassName SMS_Client -MethodName TriggerSchedule -Arguments @{ sScheduleID = $id } -ErrorAction Stop
        }
        Out-Result @{ scan = 'started'; evaluated = ($ids.Count -gt 1) }
    }
    'list' {
        $items = @()
        $inProgress = 0; $restart = 0; $installed = 0; $errors = 0; $blocked = 0; $waiting = 0
        foreach ($u in Get-Missing) {
            $s = [int]$u.EvaluationState
            $err = [int64]$u.ErrorCode
            $items += @(@{ id = [string]$u.UpdateID; kb = [string]$u.ArticleID; name = [string]$u.Name; state = $s
                    state_text = (Get-StateName $s); percent = [int]$u.PercentComplete; error = ('0x{0:X8}' -f ($err -band 4294967295)) })
            if ($s -le 1) { $waiting++ }
            elseif (@(8, 9, 10) -contains $s) { $restart++ }
            elseif ($s -eq 12) { $installed++ }
            elseif ($s -eq 13) { $errors++ }
            elseif (@(14, 15, 16, 17, 18, 19, 22) -contains $s) { $blocked++ }
            else { $inProgress++ }
        }
        $pending = @(Test-RestartPending)
        Out-Result @{ updates = $items; count = $items.Count; waiting = $waiting; in_progress = $inProgress
            restart = $restart; installed = $installed; errors = $errors; blocked = $blocked; restart_pending = $pending }
    }
    'install' {
        if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
            throw 'Installing through the ConfigMgr client needs FullLanguage PowerShell; this server runs PowerShell in Constrained Language Mode.'
        }
        # every missing update that is not already on its way (available, or failed before)
        $todo = @(Get-Missing | Where-Object { @(0, 1, 13) -contains [int]$_.EvaluationState })
        $rv = -1
        if ($todo.Count -gt 0) {
            $list = $todo
            try { $list = [ciminstance[]]$todo } catch { $list = $todo }
            $r = Invoke-CimMethod -Namespace $sdk -ClassName CCM_SoftwareUpdatesManager -MethodName InstallUpdates -Arguments @{ CCMUpdates = $list } -ErrorAction Stop
            $rv = [int]$r.ReturnValue
        }
        Out-Result @{ started = $todo.Count; return_value = $rv; names = @($todo | ForEach-Object { [string]$_.Name }) }
    }
    'restart' {
        $delay = [int]$cfg.delay_sec
        if ($delay -lt 5) { $delay = 5 }
        $boot = Get-BootTime
        $msg = [string]$cfg.message
        if (-not $msg) { $msg = 'site-automation: restart after installing updates' }
        $out = @(& shutdown.exe /r /t $delay /d p:2:17 /c $msg 2>&1 | ForEach-Object { [string]$_ })
        if ($LASTEXITCODE -ne 0) { throw ('shutdown.exe failed (exit ' + $LASTEXITCODE + '): ' + ($out -join ' ')) }
        Out-Result @{ boot_before = $boot; restart_in_sec = $delay }
    }
    'boot' {
        $svc = Get-CimInstance -ClassName Win32_Service -Filter "Name='CcmExec'"
        $state = 'not installed'
        if ($null -ne $svc) { $state = [string]$svc.State }
        Out-Result @{ boot = (Get-BootTime); ccmexec = $state }
    }
    'services' {
        $ignore = @($cfg.ignore)
        $missing = @()
        foreach ($n in @($cfg.expected)) {
            $name = [string]$n
            $skip = $false
            foreach ($p in $ignore) { if ($name -like [string]$p) { $skip = $true } }
            if ($skip) { continue }
            $s = Get-CimInstance -ClassName Win32_Service -Filter ("Name='{0}'" -f ($name -replace "'", ''))
            if ($null -eq $s -or $s.State -ne 'Running') { $missing += @($name) }
        }
        Out-Result @{ missing = $missing }
    }
    default { throw "unknown action '$action'" }
}
