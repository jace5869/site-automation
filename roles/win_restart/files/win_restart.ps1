# site-automation - Windows - schedule a restart / cancel a scheduled restart (roles/win_restart, run by
# playbooks/win_restart_schedule.yml and playbooks/win_restart_cancel.yml; docs/WINDOWS.md "Schedule a restart").
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG. action:
#   plan     read only: the pending-reboot state and why, the last boot, the server's own time of at_utc, our tasks
#   schedule plan, then our task registered for at_utc with its own script and settings files (restart-task-<id>.ps1,
#            restart-<id>.json) - one per server: an earlier one of ours is replaced in one step, and restored when the new
#            one does not read back right (protected scripts only). only_if_pending with none pending: no task, and an earlier one of ours is
#            removed. Never a restart now.
#   list     read only: our task (task_path + task_name exactly, and written by this job: author, description, script)
#   cancel   our task removed, and its files in dir (restart.log is kept). Another product's task of that name: refused.
# The pending-reboot check is the one the task runs at the restart time (task_script, win_restart_task.ps1), loaded here.
# The script prints ONE line: ###SITE-JSON### {...}. Windows PowerShell 5.1 and PowerShell 7, also in Constrained Language
# Mode. Directory and file security is checked with built-in Windows tools.
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
    <AllowStartOnDemand>false</AllowStartOnDemand>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <DeleteExpiredTaskAfter>P1D</DeleteExpiredTaskAfter>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>$c</Command><Arguments>$a</Arguments></Exec></Actions>
</Task>
"@
}

function Find-SiteTask {
    # the task of that exact folder and name, or $null. A failed lookup stops the run (never taken as "none there").
    param([string]$Path, [string]$Name)
    if ($null -eq (Get-Command -Name Get-ScheduledTask -ErrorAction SilentlyContinue)) { return $null }
    $all = @(Get-ScheduledTask -ErrorAction Stop)
    return ($all | Where-Object { [string]$_.TaskPath -eq $Path -and [string]$_.TaskName -eq $Name } | Select-Object -First 1)
}

function Get-SiteRegistration {
    param($Task, [string[]]$Dirs)
    if (@($Task.Actions).Count -ne 1 -or [string]$Task.Author -ne 'site-automation' -or
        -not ([string]$Task.Description).Contains('(site-automation')) { return }
    foreach ($dir in $Dirs) {
        $prefix = [regex]::Escape((Join-Path $dir 'restart-task-'))
        if ([string]@($Task.Actions)[0].Arguments -match ('(?:^|\s)-File "' + $prefix + '([0-9]+-[0-9]+)\.ps1"\z')) {
            return @{ dir = $dir; id = $Matches[1] }
        }
    }
}

function Get-SiteRestartTask {
    # our one task: the exact folder and name only -> @{ ours = @(its facts) or @(); collision = '' or why it is not ours }
    param([string]$Path, [string]$Name, [string[]]$Dirs)
    $res = @{ ours = @(); collision = '' }
    $t = Find-SiteTask -Path $Path -Name $Name
    if ($null -eq $t) { return $res }
    $registration = Get-SiteRegistration -Task $t -Dirs $Dirs
    if (-not $registration) {
        $res.collision = ('a task ' + $Path + $Name + ' is there that this job did not make (author ' + [string]$t.Author +
                          '): left as it is - rename ours (win_restart_task_name) or remove that one by hand')
        return $res
    }
    $next = ''
    try {
        $i = Get-ScheduledTaskInfo -TaskName $Name -TaskPath $Path
        if ($null -ne $i.NextRunTime -and $i.NextRunTime.Year -gt 2000) { $next = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture) }
    }
    catch { $next = '' }
    $start = ''
    if (@($t.Triggers).Count -gt 0) { $start = [string]@($t.Triggers)[0].StartBoundary }
    # The scheduler records the same UTC boundary in the 1.127.x task. Its files are untrusted.
    $at = $start
    if ($registration.dir -eq $Dirs[0]) { $at = Read-SiteRestartAt -Dir $registration.dir -Id $registration.id }
    $res.ours = @(@{ path = $Path; name = $Name; state = [string]$t.State; next_run = $next; start = $start;
                     description = [string]$t.Description; dir = $registration.dir; id = $registration.id; at_utc = $at })
    return $res
}

function ConvertTo-SiteUtc {
    param($Value)
    if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
    $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
    return [DateTime]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture, $styles)
}

function Read-SiteRestartAt {
    # the restart moment this registration was made for (its settings file's at_utc), or '' - the report says an unmanage
    # window SolarWinds still shows is likely that restart's (nothing is decided by it)
    param([string]$Dir, [string]$Id)
    if (-not $Id) { return '' }
    try { Assert-SiteRestartPath $Dir }
    catch [System.Management.Automation.ItemNotFoundException] { return '' }
    $f = Join-Path $Dir ('restart-' + $Id + '.json')
    if ($null -eq (Get-SiteRestartItemIfPresent $f)) { return '' }
    Assert-SiteRestartItem $f
    try {
        $value = (Get-Content -LiteralPath $f -Raw | ConvertFrom-Json).at_utc
        return (ConvertTo-SiteUtc $value).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
    } catch { return '' }
}

function Remove-SiteRestartFiles {
    # one registration's script and settings in our folder (restart.log and every other registration's stay)
    param([string]$Dir, [string]$Id)
    if (-not $Id) { return }
    try { Assert-SiteRestartPath $Dir }
    catch [System.Management.Automation.ItemNotFoundException] { return }
    foreach ($n in @(('restart-task-' + $Id + '.ps1'), ('restart-' + $Id + '.json'))) {
        $f = Join-Path $Dir $n
        if ($null -ne (Get-SiteRestartItemIfPresent $f)) {
            Assert-SiteRestartItem $f
            Remove-Item -LiteralPath $f
        }
    }
}

function Complete-SiteRestartCleanup {
    param($Registration, [string]$ProtectedDir, $Result)
    if ($Registration.dir -ne $ProtectedDir) {
        # Only the scheduler record is trusted. Never access the old ProgramData tree for cleanup.
        $Result.cleanup_note = "old files left in $($Registration.dir) - delete that folder by hand"
        return
    }
    Remove-SiteRestartFiles -Dir $ProtectedDir -Id $Registration.id
}

function Get-SiteRestartItemIfPresent {
    param([string]$Path)
    # Only a missing entry is optional. A dangling link is an item, and an unreadable entry stops the job.
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue -ErrorVariable lookupError
    if ($lookupError -and $lookupError[0].CategoryInfo.Category -ne 'ObjectNotFound') { throw $lookupError[0] }
    return $item
}

function Assert-SiteRestartItem {
    param([string]$Path, [switch]$Directory)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point at $Path" }
    if ($item.PSIsContainer -ne $Directory.IsPresent) { throw "Unexpected file type at $Path" }
}

function Resolve-SiteRestartDir {
    param([string]$Dir)
    # Expand the literal ProgramFiles prefix in a setting received as JSON.
    $sep = [IO.Path]::DirectorySeparatorChar
    $dir = $Dir.Replace('\', $sep).Replace('/', $sep).TrimEnd($sep)
    if (-not $env:ProgramFiles) { throw 'Cannot resolve win_restart_dir without ProgramFiles' }
    $root = $env:ProgramFiles.TrimEnd($sep)
    foreach ($prefix in @('$env:ProgramFiles', $root)) {
        if (-not $dir.StartsWith($prefix + $sep, [StringComparison]::OrdinalIgnoreCase)) { continue }
        $relative = $dir.Substring($prefix.Length + 1)
        foreach ($part in $relative.Split($sep)) {
            if (-not $part -or $part -match '[. ]$|[\x00-\x1f<>:"|?*]|^(?:CON|PRN|AUX|NUL|(?:LPT|COM)[0-9\u00b9\u00b2\u00b3])(?:\.|$)') {
                throw "Invalid path segment in win_restart_dir: '$part'"
            }
        }
        $path = Join-Path $root $relative
        if ($path -match '[\[\]]') { throw 'win_restart_dir cannot contain square brackets' }
        if ($path -match '%[^%]+%') { throw 'win_restart_dir cannot contain a variable that Task Scheduler would expand' }
        return $path
    }
    throw 'win_restart_dir must be a directory beneath Program Files ($env:ProgramFiles)'
}

function Assert-SiteRestartPath {
    # Walk from the filesystem root so no child is opened or created through a reparse point.
    param([string]$Dir, [switch]$Create)
    $parent = Split-Path -Parent $Dir
    if ($parent) { Assert-SiteRestartPath $parent -Create:$Create }
    $sep = [IO.Path]::DirectorySeparatorChar
    $belowRoot = $Dir.StartsWith($env:ProgramFiles.TrimEnd($sep) + $sep, [StringComparison]::OrdinalIgnoreCase)
    try { Assert-SiteRestartItem $Dir -Directory }
    catch [System.Management.Automation.ItemNotFoundException] {
        if (-not $Create -or -not $belowRoot) { throw }
        $null = New-Item -Path $Dir -ItemType Directory -ErrorAction Stop
        Assert-SiteRestartItem $Dir -Directory
    }
    # Program Files itself too: a weakened root would let its children be renamed or swapped
    $isRoot = $Dir.TrimEnd($sep).Equals($env:ProgramFiles.TrimEnd($sep), [StringComparison]::OrdinalIgnoreCase)
    if ($belowRoot -or $isRoot) { Assert-SiteRestartAcl $Dir }
}

function Assert-SiteRestartAcl {
    param([string]$Path)
    # CIM decodes the SDDL into SIDs and numeric access masks on localized Windows and in CLM.
    $sddl = (Get-Acl -LiteralPath $Path -ErrorAction Stop).Sddl
    $sd = Invoke-CimMethod -ClassName Win32_SecurityDescriptorHelper -MethodName SDDLToWin32SD -Arguments @{ SDDL = $sddl } -ErrorAction Stop
    if ($sd.ReturnValue -ne 0 -or $null -eq $sd.Descriptor.DACL) { throw "Cannot read DACL on $Path" }
    $trusted = @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if ($sd.Descriptor.Owner.SIDString -notin $trusted) { throw "Untrusted owner on $Path" }
    foreach ($ace in $sd.Descriptor.DACL) {
        if ($ace.AceType -eq 1) { continue }  # deny
        $sid = $ace.Trustee.SIDString
        if ($ace.AceType -ne 0 -or -not $sid -or $null -eq $ace.AccessMask) { throw "Unrecognized ACE on $Path" }
        if ($sid -in $trusted) { continue }
        if ($sid -eq 'S-1-3-0' -and ($ace.AceFlags -band 8)) { continue }  # CREATOR OWNER, inherit-only
        # Only read, execute, synchronize and generic read/execute may be granted to other trustees.
        if ($ace.AccessMask -band 0x5fedff56) { throw "Unsafe permissions on $Path (write or control granted to $sid)" }
    }
}

function Assert-SiteRestartLocation {
    param([string]$Dir, [string[]]$Files, [switch]$Create)
    Assert-SiteRestartPath $Dir -Create:$Create
    foreach ($file in $Files) {
        Assert-SiteRestartItem $file
        Assert-SiteRestartAcl $file
    }
}

function Enter-SiteRestartLock {
    # one schedule or cancel at a time on a server: restart.lock in our folder; one older than 10 minutes is left over
    param([string]$Dir)
    $lock = Join-Path $Dir 'restart.lock'
    $item = Get-SiteRestartItemIfPresent $lock
    if ($null -ne $item) {
        Assert-SiteRestartItem $lock
        if ($item.LastWriteTime -lt (Get-Date).AddMinutes(-10)) { Remove-Item -LiteralPath $lock -Force }
        else { throw ('another schedule or cancel of a restart is running on this server (' + $lock + '): launch it again in a few minutes') }
    }
    try { $null = New-Item -ItemType File -Path $lock -ErrorAction Stop }
    catch { throw ('another schedule or cancel of a restart is running on this server (' + $lock + '): launch it again in a few minutes') }
    return $lock
}

$out = @{ computer = $env:COMPUTERNAME; action = [string]$cfg.action }
try {
    $dir = Resolve-SiteRestartDir ([string]$cfg.dir)
    $tz = [TimeZoneInfo]::Local
    $out.tz_id = [string]$tz.Id
    $out.now_local = (Get-Date).ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture)
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem
        $boot = [datetime]$os.LastBootUpTime
        $out.boot = $boot.ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture)
        $out.uptime_days = [int](((Get-Date) - $boot).TotalHours * 10 / 24) / 10
    }
    catch { $out.boot = ''; $out.uptime_days = $null }
    $prefix = [string]$cfg.task_name
    if ($cfg.action -in @('plan', 'schedule')) {
        $atUtc = ConvertTo-SiteUtc $cfg.at_utc
        $atLocal = $atUtc.ToLocalTime()
        $out.at_local = $atLocal.ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture) + ' ' + (Format-SiteOffset -Offset ($atLocal - $atUtc)) + ' (' + $tz.Id + ')'
        $probes = Read-SiteRestartProbes
        $out.probes = $probes
        $out.reasons = @(Get-SiteRestartReasons -Probes $probes -CountFileRenames ([bool]$cfg.count_file_renames))
        $d = Get-SiteRestartDecision -OnlyIfPending ([bool]$cfg.only_if_pending) -Reasons $out.reasons
        $out.restart = [bool]$d.restart
        $out.why = [string]$d.why
    }
    $taskDirs = @($dir)
    if ($env:ProgramData) { $taskDirs += Join-Path $env:ProgramData 'site-automation\restart' }
    $folder = ([string]$cfg.task_path).Trim().Trim('\')
    $tpath = '\'
    if ($folder) { $tpath += $folder + '\' }
    $lock = ''
    if ($cfg.action -in @('schedule', 'cancel')) {
        Assert-SiteRestartLocation $dir -Create
        $lock = Enter-SiteRestartLock -Dir $dir
    }
    try {
        $task = Get-SiteRestartTask -Path $tpath -Name $prefix -Dirs $taskDirs
        $ours = $task.ours
        $out.ours = $ours
        if ($task.collision) { throw $task.collision }
        if ($cfg.action -eq 'schedule' -and -not $out.restart -and $ours.Count -gt 0) {
            # skipped (only if pending, none pending): an earlier restart of ours must not stay armed
            Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
            Complete-SiteRestartCleanup $ours[0] $dir $out
            $out.removed = $ours
        }
        if ($cfg.action -eq 'schedule' -and $out.restart) {
            if ($atUtc -le (Get-Date).ToUniversalTime().AddSeconds(60)) {
                throw ('the restart time ' + $out.at_local + ' has passed while the job ran: launch it again')
            }
            if ($null -eq (Get-Command -Name Register-ScheduledTask -ErrorAction SilentlyContinue)) {
                throw 'Register-ScheduledTask is not on this server (Windows Server 2012 or later has it)'
            }
            $log = Join-Path $dir 'restart.log'
            if ($null -ne (Get-SiteRestartItemIfPresent $log)) {
                Assert-SiteRestartItem $log
                Assert-SiteRestartAcl $log
            }
            # this registration's own files, never rewritten: an earlier task keeps reading its own until it is replaced
            $rid = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss', [Globalization.CultureInfo]::InvariantCulture) + '-' + [string](Get-Random -Minimum 1000 -Maximum 9999)
            $script = Join-Path $dir ('restart-task-' + $rid + '.ps1')
            $json = Join-Path $dir ('restart-' + $rid + '.json')
            # Create both names exclusively in the secured directory. Never write through an existing file or link.
            $null = New-Item -ItemType File -Path $script -ErrorAction Stop
            try {
                $null = New-Item -ItemType File -Path $json -ErrorAction Stop
            } catch {
                Remove-Item -LiteralPath $script -Force
                throw
            }
            $settingsDoc = @{ only_if_pending = [bool]$cfg.only_if_pending; count_file_renames = [bool]$cfg.count_file_renames;
                              comment = [string]$cfg.comment; code = [string]$cfg.code; delay = [int]$cfg.delay;
                              at_utc = $atUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture);
                              id = $rid }

            $oldXml = ''
            try {
                Assert-SiteRestartLocation $dir @($script, $json)
                Set-Content -LiteralPath $script -Value ([string]$cfg.task_script) -Encoding ASCII
                Set-Content -LiteralPath $json -Value (ConvertTo-Json -InputObject $settingsDoc -Compress) -Encoding ASCII
                if ($ours.Count -gt 0 -and $ours[0].dir -eq $dir) {
                    $oldScript = Join-Path $dir ('restart-task-' + $ours[0].id + '.ps1')
                    $oldXml = [string](Export-ScheduledTask -TaskName $prefix -TaskPath $tpath)
                }
                $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                $endUtc = $atUtc.AddMinutes(30).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
                $xml = New-SiteRestartTaskXml -StartUtc $settingsDoc.at_utc -EndUtc $endUtc `
                    -Command $ps -Arguments ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '"') -Description ([string]$cfg.description)
                if ($ours.Count -gt 0) {
                    # replace only the task verified as ours in this run, checked again right before (one step: never both, never none)
                    $again = Get-SiteRestartTask -Path $tpath -Name $prefix -Dirs $taskDirs
                    if ($again.collision -or $again.ours.Count -ne 1 -or $again.ours[0].dir -ne $ours[0].dir -or $again.ours[0].id -ne $ours[0].id) {
                        throw 'the task changed while this job ran'
                    }
                }
                # When no task existed, create only. A task made by another product meanwhile must survive.
                Assert-SiteRestartLocation $dir @($script, $json)
                $null = Register-ScheduledTask -Xml $xml -TaskName $prefix -TaskPath $tpath -Force:($ours.Count -gt 0)
                $info = Get-ScheduledTaskInfo -TaskName $prefix -TaskPath $tpath
                $next = $info.NextRunTime
                if ($null -eq $next -or (($next.ToUniversalTime() - $atUtc).TotalMinutes -gt 1) -or (($atUtc - $next.ToUniversalTime()).TotalMinutes -gt 1)) {
                    throw ('its next run (' + [string]$next + ') is not ' + $out.at_local)
                }
                $back = Get-SiteRestartTask -Path $tpath -Name $prefix -Dirs $dir
                if ($back.collision -or $back.ours.Count -ne 1 -or [string]$back.ours[0].id -ne $rid) { throw 'it does not read back as ours' }
            }
            catch {
                $why = [string]$_.Exception.Message
                $undo = ''
                try {
                    # only what this run wrote is undone: the task there now must carry this run's registration id
                    $cur = Find-SiteTask -Path $tpath -Name $prefix
                    $mine = (Get-SiteRegistration -Task $cur -Dirs $dir).id -eq $rid
                    if (-not $mine) {
                        $undo = 'nothing this run registered is there: left as it is'
                        if ($ours.Count -gt 0) { $undo += ' (the earlier restart, ' + [string]$ours[0].next_run + ', stays)' }
                    }
                    elseif ($oldXml) {
                        $oldJson = Join-Path $dir ('restart-' + $ours[0].id + '.json')
                        Assert-SiteRestartLocation $dir @($oldScript, $oldJson)
                        $null = Register-ScheduledTask -Xml $oldXml -TaskName $prefix -TaskPath $tpath -Force
                        $undo = 'rolled back: the earlier restart (' + [string]$ours[0].next_run + ') is scheduled again'
                    }
                    else {
                        Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
                        if ($ours.Count -gt 0) { Complete-SiteRestartCleanup $ours[0] $dir $out }
                        $undo = 'rolled back: no restart of ours is scheduled on this server'
                    }
                    Remove-SiteRestartFiles -Dir $dir -Id $rid
                }
                catch { $undo = 'the roll back failed too (' + [string]$_.Exception.Message + '): check Task Scheduler ' + $tpath + $prefix + ' by hand' }
                throw ('the task could not be registered right (' + $why + ') - ' + $undo)
            }
            if ($ours.Count -gt 0) { Complete-SiteRestartCleanup $ours[0] $dir $out }   # the replaced registration's only
            $out.replaced = $ours
            $out.scheduled = @{ path = $tpath; name = $prefix; next_run = $next.ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture); id = $rid }
            $out.ours = (Get-SiteRestartTask -Path $tpath -Name $prefix -Dirs $taskDirs).ours
        }
        if ($cfg.action -eq 'cancel') {
            $removed = @()
            if ($ours.Count -gt 0) {
                Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
                Complete-SiteRestartCleanup $ours[0] $dir $out
                $removed = $ours
            }
            $out.removed = $removed
            $out.ours = (Get-SiteRestartTask -Path $tpath -Name $prefix -Dirs $taskDirs).ours
        }
    }
    finally {
        if ($lock) {
            Assert-SiteRestartPath $dir
            Assert-SiteRestartItem $lock
            Remove-Item -LiteralPath $lock -Force
        }
    }
}
catch {
    $out.error = [string]$_.Exception.Message
}
Write-Output ('###SITE-JSON### ' + (ConvertTo-Json -InputObject $out -Depth 6 -Compress))
