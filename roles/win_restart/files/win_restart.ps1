# site-automation - Windows - schedule a restart / cancel a scheduled restart (roles/win_restart, run by
# playbooks/win_restart_schedule.yml and playbooks/win_restart_cancel.yml; docs/WINDOWS.md "Schedule a restart").
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG. action:
#   plan     read only: the pending-reboot state and why, the last boot, the server's own time of at_utc, our tasks
#   schedule plan, then our task registered for at_utc with its own script and settings files (restart-task-<id>.ps1,
#            restart-<id>.json) - one per server: an earlier one of ours is replaced in one step, and restored when the new
#            one does not read back right. only_if_pending with none pending: no task, and an earlier one of ours is
#            removed. Never a restart now.
#   list     read only: our task (task_path + task_name exactly, and written by this job: author, description, script)
#   cancel   our task removed, and its files in dir (restart.log is kept). Another product's task of that name: refused.
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

function Test-SiteOurTask {
    # ours: written by this job (Author site-automation, "(site-automation" in its description) and running a script of ours
    # in our folder - another product's task with the same name is never touched
    param($Task, [string]$Dir)
    $acts = @($Task.Actions)
    if ($acts.Count -ne 1) { return $false }
    if ([string]$Task.Author -ne 'site-automation') { return $false }
    if (-not ([string]$Task.Description).Contains('(site-automation')) { return $false }
    return ([string]$acts[0].Arguments).Contains('-File "' + (Join-Path $Dir 'restart-task-'))
}

function Get-SiteRestartTask {
    # our one task: the exact folder and name only -> @{ ours = @(its facts) or @(); collision = '' or why it is not ours }
    param([string]$Path, [string]$Name, [string]$Dir)
    $res = @{ ours = @(); collision = '' }
    if ($null -eq (Get-Command -Name Get-ScheduledTask -ErrorAction SilentlyContinue)) { return $res }
    $t = Get-ScheduledTask -TaskPath $Path -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $t) { return $res }
    if (-not (Test-SiteOurTask -Task $t -Dir $Dir)) {
        $res.collision = ('a task ' + $Path + $Name + ' is there that this job did not make (author ' + [string]$t.Author +
                          '): left as it is - rename ours (win_restart_task_name) or remove that one by hand')
        return $res
    }
    $next = ''
    try {
        $i = Get-ScheduledTaskInfo -TaskName $Name -TaskPath $Path
        if ($null -ne $i.NextRunTime -and $i.NextRunTime.Year -gt 2000) { $next = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') }
    }
    catch { $next = '' }
    $start = ''
    if (@($t.Triggers).Count -gt 0) { $start = [string]@($t.Triggers)[0].StartBoundary }
    $res.ours = @(@{ path = $Path; name = $Name; state = [string]$t.State; next_run = $next; start = $start;
                     description = [string]$t.Description })
    return $res
}

function Remove-SiteRestartFiles {
    # our task scripts and settings in our folder, but the registration $Keep (restart.log stays)
    param([string]$Dir, [string]$Keep = '')
    if (-not (Test-Path -Path $Dir)) { return }
    foreach ($f in @(Get-ChildItem -Path $Dir -File -ErrorAction SilentlyContinue)) {
        $n = [string]$f.Name
        if (($n -like 'restart-task-*.ps1' -or $n -like 'restart-*.json') -and -not ($Keep -and $n.Contains($Keep))) {
            Remove-Item -LiteralPath $f.FullName -Force
        }
    }
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
    $dir = [string]$cfg.dir
    $tpath = [string]$cfg.task_path
    $task = Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir
    $ours = $task.ours
    $out.ours = $ours
    if ($task.collision) { throw $task.collision }
    if ($cfg.action -eq 'schedule' -and -not $out.restart) {
        # skipped (only if pending, none pending): an earlier restart of ours must not stay armed
        if ($ours.Count -gt 0) {
            Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
            Remove-SiteRestartFiles -Dir $dir
            $out.removed = $ours
        }
    }
    if ($cfg.action -eq 'schedule' -and $out.restart) {
        if ($atUtc -le (Get-Date).ToUniversalTime().AddSeconds(60)) {
            throw ('the restart time ' + $out.at_local + ' has passed while the job ran: launch it again')
        }
        if ($null -eq (Get-Command -Name Register-ScheduledTask -ErrorAction SilentlyContinue)) {
            throw 'Register-ScheduledTask is not on this server (Windows Server 2012 or later has it)'
        }
        # the folder: only SYSTEM and Administrators (the task runs its script as SYSTEM); owner Administrators, nothing inherited
        $null = New-Item -ItemType Directory -Path $dir -Force
        $null = & icacls.exe $dir /setowner '*S-1-5-32-544'
        if ($LASTEXITCODE -ne 0) { throw ('icacls /setowner on ' + $dir + ' failed (' + $LASTEXITCODE + ')') }
        $null = & icacls.exe $dir /reset
        $null = & icacls.exe $dir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
        if ($LASTEXITCODE -ne 0) { throw ('icacls on ' + $dir + ' failed (' + $LASTEXITCODE + ')') }
        # this registration's own files, never rewritten: an earlier task keeps reading its own until it is gone
        $rid = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss') + '-' + [string](Get-Random -Minimum 1000 -Maximum 9999)
        $script = Join-Path $dir ('restart-task-' + $rid + '.ps1')
        $json = Join-Path $dir ('restart-' + $rid + '.json')
        Set-Content -Path $script -Value ([string]$cfg.task_script) -Encoding ASCII
        $settingsDoc = @{ only_if_pending = [bool]$cfg.only_if_pending; count_file_renames = [bool]$cfg.count_file_renames;
                          comment = [string]$cfg.comment; code = [string]$cfg.code; delay = [int]$cfg.delay; at_utc = [string]$cfg.at_utc }
        Set-Content -Path $json -Value (ConvertTo-Json -InputObject $settingsDoc -Compress) -Encoding ASCII
        $oldXml = ''
        if ($ours.Count -gt 0) { $oldXml = [string](Export-ScheduledTask -TaskName $prefix -TaskPath $tpath) }
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $xml = New-SiteRestartTaskXml -StartUtc $atUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") -EndUtc $atUtc.AddMinutes(30).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") `
            -Command $ps -Arguments ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '"') -Description ([string]$cfg.description)
        try {
            # -Force replaces our earlier task in one step: there is never a moment with both, or (before this) with none
            $null = Register-ScheduledTask -Xml $xml -TaskName $prefix -TaskPath $tpath -Force
            $info = Get-ScheduledTaskInfo -TaskName $prefix -TaskPath $tpath
            $next = $info.NextRunTime
            if ($null -eq $next -or (($next.ToUniversalTime() - $atUtc).TotalMinutes -gt 1) -or (($atUtc - $next.ToUniversalTime()).TotalMinutes -gt 1)) {
                throw ('its next run (' + [string]$next + ') is not ' + $out.at_local)
            }
            $back = Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir
            if ($back.collision -or $back.ours.Count -ne 1 -or -not ([string]$back.ours[0].description).Contains([string]$cfg.description)) {
                throw 'it does not read back as ours'
            }
        }
        catch {
            $why = [string]$_.Exception.Message
            $undo = ''
            try {
                if ($oldXml) {
                    $null = Register-ScheduledTask -Xml $oldXml -TaskName $prefix -TaskPath $tpath -Force
                    $undo = 'rolled back: the earlier restart (' + [string]$ours[0].next_run + ') is scheduled again'
                }
                else {
                    if ($null -ne (Get-ScheduledTask -TaskPath $tpath -TaskName $prefix -ErrorAction SilentlyContinue)) {
                        Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
                    }
                    $undo = 'rolled back: no restart of ours is scheduled on this server'
                }
                foreach ($f in @($script, $json)) { if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force } }
            }
            catch { $undo = 'the roll back failed too (' + [string]$_.Exception.Message + '): check Task Scheduler ' + $tpath + $prefix + ' by hand' }
            throw ('the task could not be registered right (' + $why + ') - ' + $undo)
        }
        Remove-SiteRestartFiles -Dir $dir -Keep $rid          # the earlier registration's files, now that it is replaced
        $out.replaced = $ours
        $out.scheduled = @{ path = $tpath; name = $prefix; next_run = $next.ToString('yyyy-MM-dd HH:mm'); id = $rid }
        $out.ours = (Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir).ours
    }
    if ($cfg.action -eq 'cancel') {
        $removed = @()
        if ($ours.Count -gt 0) {
            Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
            $removed = $ours
        }
        Remove-SiteRestartFiles -Dir $dir
        $out.removed = $removed
        $out.ours = (Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir).ours
    }
}
catch {
    $out.error = [string]$_.Exception.Message
}
Write-Output ('###SITE-JSON### ' + (ConvertTo-Json -InputObject $out -Depth 6 -Compress))
