# site-automation - Windows - schedule a restart / cancel a scheduled restart (roles/win_restart, run by
# playbooks/win_restart_schedule.yml and playbooks/win_restart_cancel.yml; docs/WINDOWS.md "Schedule a restart").
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG. action:
#   plan     read only: the pending-reboot state and why, the last boot, the server's own time of at_utc, our tasks
#   schedule plan, then (unless only_if_pending finds none pending) our task registered for at_utc - one per server: an
#            earlier one of ours is replaced. Never a restart now.
#   list     read only: our tasks (name starts with task_name, any folder)
#   cancel   our tasks removed, and the task's files in dir (restart.log is kept)
# The pending-reboot check is the one the task runs at the restart time (task_script, win_restart_task.ps1), loaded here.
# The script prints ONE line: ###SITE-JSON### {...}. Windows PowerShell 5.1 and PowerShell 7, also in Constrained Language
# Mode (no .NET calls but on core types: icacls for the folder, the task registered from its XML).
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
$SiteRestartLibrary = $true
Invoke-Expression -Command ([string]$cfg.task_script)

function Format-SiteOffset {
    # local - UTC of the same moment -> 'UTC+01:00'
    param([TimeSpan]$Offset)
    $sign = '+'
    $h = $Offset.Hours
    $m = $Offset.Minutes
    if ($Offset -lt [TimeSpan]::Zero) { $sign = '-'; $h = -$h; $m = -$m }
    return ('UTC{0}{1:00}:{2:00}' -f $sign, $h, $m)
}

function ConvertTo-SiteXmlText {
    param([string]$Text)
    return ($Text -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;' -replace '"', '&quot;')
}

function New-SiteRestartTaskXml {
    # the task as Task Scheduler's XML: the moment in UTC (the same instant in any time zone), never later (an end 30 minutes
    # on, no StartWhenAvailable: a server that was off at the time does not restart when it comes back), deleted a day after
    param([string]$StartUtc, [string]$EndUtc, [string]$Command, [string]$Arguments, [string]$Description)
    $d = ConvertTo-SiteXmlText -Text $Description
    $c = ConvertTo-SiteXmlText -Text $Command
    $a = ConvertTo-SiteXmlText -Text $Arguments
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Author>site-automation</Author><Description>$d</Description></RegistrationInfo>
  <Triggers><TimeTrigger><StartBoundary>$StartUtc</StartBoundary><EndBoundary>$EndUtc</EndBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>false</StartWhenAvailable>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <DeleteExpiredTaskAfter>P1D</DeleteExpiredTaskAfter>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>$c</Command><Arguments>$a</Arguments></Exec></Actions>
</Task>
"@
}

function Get-SiteOurTasks {
    param([string]$Prefix)
    if ($null -eq (Get-Command -Name Get-ScheduledTask -ErrorAction SilentlyContinue)) { return , @() }
    $out = @()
    foreach ($t in @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like ($Prefix + '*') })) {
        $next = ''
        try {
            $i = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath
            if ($null -ne $i.NextRunTime -and $i.NextRunTime.Year -gt 2000) { $next = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') }
        }
        catch { $next = '' }
        $start = ''
        if (@($t.Triggers).Count -gt 0) { $start = [string]@($t.Triggers)[0].StartBoundary }
        $out += @(@{ path = [string]$t.TaskPath; name = [string]$t.TaskName; state = [string]$t.State; next_run = $next;
                     start = $start; description = [string]$t.Description })
    }
    return , $out
}

$out = @{ computer = $env:COMPUTERNAME; action = [string]$cfg.action }
try {
    $tz = [TimeZoneInfo]::Local
    $out.tz_id = [string]$tz.Id
    $out.now_local = (Get-Date).ToString('yyyy-MM-dd HH:mm')
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem
        $boot = [datetime]$os.LastBootUpTime
        $out.boot = $boot.ToString('yyyy-MM-dd HH:mm')
        $out.uptime_days = [int](((Get-Date) - $boot).TotalHours * 10 / 24) / 10
    }
    catch { $out.boot = ''; $out.uptime_days = $null }
    $prefix = [string]$cfg.task_name
    if ($cfg.action -in @('plan', 'schedule')) {
        $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
        $atUtc = [DateTime]::Parse([string]$cfg.at_utc, [Globalization.CultureInfo]::InvariantCulture, $styles)
        $atLocal = $atUtc.ToLocalTime()
        $out.at_local = $atLocal.ToString('yyyy-MM-dd HH:mm') + ' ' + (Format-SiteOffset -Offset ($atLocal - $atUtc)) + ' (' + $tz.Id + ')'
        $probes = Read-SiteRestartProbes
        $out.probes = $probes
        $out.reasons = @(Get-SiteRestartReasons -Probes $probes -CountFileRenames ([bool]$cfg.count_file_renames))
        $d = Get-SiteRestartDecision -OnlyIfPending ([bool]$cfg.only_if_pending) -Reasons $out.reasons
        $out.restart = [bool]$d.restart
        $out.why = [string]$d.why
    }
    $ours = Get-SiteOurTasks -Prefix $prefix
    $out.ours = $ours
    if ($cfg.action -eq 'schedule' -and $out.restart) {
        if ($atUtc -le (Get-Date).ToUniversalTime().AddSeconds(60)) {
            throw ('the restart time ' + $out.at_local + ' has passed while the job ran: launch it again')
        }
        if ($null -eq (Get-Command -Name Register-ScheduledTask -ErrorAction SilentlyContinue)) {
            throw 'Register-ScheduledTask is not on this server (Windows Server 2012 or later has it)'
        }
        $dir = [string]$cfg.dir
        # the folder: only SYSTEM and Administrators (the task runs its script as SYSTEM); owner Administrators, nothing inherited
        $null = New-Item -ItemType Directory -Path $dir -Force
        $null = & icacls.exe $dir /setowner '*S-1-5-32-544'
        if ($LASTEXITCODE -ne 0) { throw ('icacls /setowner on ' + $dir + ' failed (' + $LASTEXITCODE + ')') }
        $null = & icacls.exe $dir /reset
        $null = & icacls.exe $dir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
        if ($LASTEXITCODE -ne 0) { throw ('icacls on ' + $dir + ' failed (' + $LASTEXITCODE + ')') }
        $script = Join-Path $dir 'restart-task.ps1'
        $json = Join-Path $dir 'restart.json'
        foreach ($f in @($script, $json)) { if (Test-Path -Path $f) { Remove-Item -Path $f -Force } }
        Set-Content -Path $script -Value ([string]$cfg.task_script) -Encoding ASCII
        $settingsDoc = @{ only_if_pending = [bool]$cfg.only_if_pending; count_file_renames = [bool]$cfg.count_file_renames;
                          comment = [string]$cfg.comment; code = [string]$cfg.code; delay = [int]$cfg.delay; at_utc = [string]$cfg.at_utc }
        Set-Content -Path $json -Value (ConvertTo-Json -InputObject $settingsDoc -Compress) -Encoding ASCII
        # one restart of ours per server: the earlier ones go first
        $replaced = @()
        foreach ($t in $ours) {
            Unregister-ScheduledTask -TaskName $t.name -TaskPath $t.path -Confirm:$false
            $replaced += @($t)
        }
        $out.replaced = $replaced
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $xml = New-SiteRestartTaskXml -StartUtc $atUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") -EndUtc $atUtc.AddMinutes(30).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") `
            -Command $ps -Arguments ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '"') -Description ([string]$cfg.description)
        try { $null = Register-ScheduledTask -Xml $xml -TaskName $prefix -TaskPath ([string]$cfg.task_path) -Force }
        catch { $out.removed_earlier = $true; throw }
        $info = Get-ScheduledTaskInfo -TaskName $prefix -TaskPath ([string]$cfg.task_path)
        $next = $info.NextRunTime
        if ($null -eq $next -or (($next.ToUniversalTime() - $atUtc).TotalMinutes -gt 1) -or (($atUtc - $next.ToUniversalTime()).TotalMinutes -gt 1)) {
            Unregister-ScheduledTask -TaskName $prefix -TaskPath ([string]$cfg.task_path) -Confirm:$false
            throw ('the task''s next run (' + [string]$next + ') is not ' + $out.at_local + ': it was removed again')
        }
        $out.scheduled = @{ path = [string]$cfg.task_path; name = $prefix; next_run = $next.ToString('yyyy-MM-dd HH:mm') }
        $out.ours = Get-SiteOurTasks -Prefix $prefix
    }
    if ($cfg.action -eq 'cancel') {
        $removed = @()
        foreach ($t in $ours) {
            Unregister-ScheduledTask -TaskName $t.name -TaskPath $t.path -Confirm:$false
            $removed += @($t)
        }
        foreach ($f in @('restart-task.ps1', 'restart.json')) {
            $p = Join-Path ([string]$cfg.dir) $f
            if (Test-Path -Path $p) { Remove-Item -Path $p -Force }
        }
        $out.removed = $removed
        $out.ours = Get-SiteOurTasks -Prefix $prefix
    }
}
catch {
    $out.error = [string]$_.Exception.Message
}
Write-Output ('###SITE-JSON### ' + (ConvertTo-Json -InputObject $out -Depth 6 -Compress))
