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
    <AllowStartOnDemand>false</AllowStartOnDemand>
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
    return [bool](Get-SiteRegistrationId -Task $Task -Dir $Dir)
}

function Find-SiteTask {
    # the task of that exact folder and name, or $null. A failed lookup stops the run (never taken as "none there").
    param([string]$Path, [string]$Name)
    if ($null -eq (Get-Command -Name Get-ScheduledTask -ErrorAction SilentlyContinue)) { return $null }
    $all = @(Get-ScheduledTask -ErrorAction Stop)
    return ($all | Where-Object { [string]$_.TaskPath -eq $Path -and [string]$_.TaskName -eq $Name } | Select-Object -First 1)
}

function Get-SiteRegistrationId {
    # the registration id in a task of ours: its action's restart-task-<id>.ps1
    param($Task, [string]$Dir)
    $prefix = [regex]::Escape((Join-Path $Dir 'restart-task-'))
    $m = [regex]::Match([string]@($Task.Actions)[0].Arguments, '(?:^|\s)-File "' + $prefix + '([0-9]+-[0-9]+)\.ps1"$')
    if ($m.Success) { return $m.Groups[1].Value }
    return ''
}

function Get-SiteRestartTask {
    # our one task: the exact folder and name only -> @{ ours = @(its facts) or @(); collision = '' or why it is not ours }
    param([string]$Path, [string]$Name, [string]$Dir)
    $res = @{ ours = @(); collision = '' }
    $t = Find-SiteTask -Path $Path -Name $Name
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
    $id = Get-SiteRegistrationId -Task $t -Dir $Dir
    $res.ours = @(@{ path = $Path; name = $Name; state = [string]$t.State; next_run = $next; start = $start;
                     description = [string]$t.Description; id = $id; at_utc = (Read-SiteRestartAt -Dir $Dir -Id $id) })
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
    Initialize-SiteRestartDir -Dir $Dir -ReadOnly
    $f = Join-Path $Dir ('restart-' + $Id + '.json')
    if (-not (Test-Path -LiteralPath $f)) { return '' }
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
    foreach ($n in @(('restart-task-' + $Id + '.ps1'), ('restart-' + $Id + '.json'))) {
        $f = Join-Path $Dir $n
        if (Test-Path -LiteralPath $f) {
            Assert-SiteRestartItem $f
            Remove-Item -LiteralPath $f -Force
        }
    }
}

function Assert-SiteRestartItem {
    param([string]$Path, [switch]$Directory)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point at $Path" }
    if ($item.PSIsContainer -ne $Directory.IsPresent) { throw "Unexpected file type at $Path" }
}

function Get-SiteRestartAcl {
    param([string]$Path)
    return (ConvertFrom-SddlString -Sddl (Get-Acl -LiteralPath $Path -ErrorAction Stop).Sddl).RawDescriptor
}

function Test-SitePrivateAcl {
    param($Acl, [switch]$Directory)
    $admins = 'S-1-5-32-544'
    $system = 'S-1-5-18'
    if ($Acl.Owner.Value -notin @($admins, $system)) { return $false }
    if ($Directory -and ($Acl.Owner.Value -ne $admins -or -not ([int]$Acl.ControlFlags -band 0x1000))) { return $false }
    if ($Acl.DiscretionaryAcl.Count -ne 2) { return $false }
    $seen = @()
    foreach ($ace in $Acl.DiscretionaryAcl) {
        # A directory grants full control on itself and its children. A file can inherit those same two grants.
        $flags = [int]$ace.AceFlags
        if ($Directory -and $flags -ne 3) { return $false }
        if (-not $Directory -and $flags -notin @(0, 16)) { return $false }
        if ([string]$ace.AceType -ne 'AccessAllowed' -or $ace.AccessMask -ne 0x1f01ff) { return $false }
        $seen += $ace.SecurityIdentifier.Value
    }
    return $admins -in $seen -and $system -in $seen
}

function Assert-SiteRestartFileAcl {
    param([string]$Path)
    Assert-SiteRestartItem $Path
    if (-not (Test-SitePrivateAcl (Get-SiteRestartAcl $Path))) { throw "Unexpected owner or ACL on $Path" }
}

function Protect-SiteRestartDir {
    param([string]$Path)
    # Replace the DACL in one operation. /reset would temporarily re-enable ProgramData inheritance.
    $policy = New-TemporaryFile
    try {
        Set-Content -LiteralPath $policy.FullName -Encoding Unicode -Value @(
            (Split-Path -Leaf $Path), 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)')
        $null = & icacls.exe $Path /setowner '*S-1-5-32-544' /L
        if ($LASTEXITCODE -ne 0) { throw "icacls /setowner on $Path failed ($LASTEXITCODE)" }
        $null = & icacls.exe (Split-Path -Parent $Path) /restore $policy.FullName /L
        if ($LASTEXITCODE -ne 0) { throw "icacls /restore on $Path failed ($LASTEXITCODE)" }
    }
    finally { Remove-Item -LiteralPath $policy.FullName -Force }
    Assert-SiteRestartItem $Path -Directory
    if (-not (Test-SitePrivateAcl (Get-SiteRestartAcl $Path) -Directory)) { throw "Unexpected owner or ACL on $Path" }
}

function Assert-SiteRestartDirControl {
    param([string]$Path, $Acl, [string]$NextDirectory)
    # Changing an ACL cannot revoke an already open handle. Refuse an untrusted owner or a grant that
    # could already have allowed replacement (delete, delete-child, reparse, change DACL or owner).
    $trusted = @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if ($acl.Owner.Value -notin $trusted) {
        $identity = & whoami.exe /user /fo csv /nh
        if ($LASTEXITCODE -ne 0 -or $identity -notmatch '"(S-1-[0-9-]+)"\s*$' -or $Matches[1] -ne $acl.Owner.Value) {
            throw "Unsafe existing owner on $path. Have an administrator provision a trusted directory."
        }
        $trusted += $acl.Owner.Value
    }
    if ($null -eq $acl.DiscretionaryAcl) { throw "Missing DACL on $path" }
    foreach ($ace in $acl.DiscretionaryAcl) {
        if (([int]$ace.AceFlags -band 8) -or $ace.SecurityIdentifier.Value -in $trusted -or
            [string]$ace.AceType -eq 'AccessDenied') { continue }
        if ([string]$ace.AceType -ne 'AccessAllowed' -or ($ace.AccessMask -band 0x500d0040)) {
            throw "Unsafe existing permissions on $path. Have an administrator provision a trusted directory."
        }
        if ($ace.AccessMask -band 0x102) {
            # The old role left ProgramData inheritance on the parent. A directory can only become a
            # junction while empty. Its existing protected restart child cannot be removed via either ACL.
            if (-not $NextDirectory -or -not (Test-Path -LiteralPath $NextDirectory)) {
                throw "Unsafe existing permissions on $path. Have an administrator provision a trusted directory."
            }
            Assert-SiteRestartItem $NextDirectory -Directory
            if (-not (Test-SitePrivateAcl (Get-SiteRestartAcl $NextDirectory) -Directory)) {
                throw "Unsafe existing permissions on $path and its child $NextDirectory"
            }
        }
    }
}

function New-SiteRestartDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    # ProgramData can grant ordinary users write access to newly created directories. Publish an
    # already protected directory by same-volume rename from the job account's temporary directory.
    $temp = Get-Item -LiteralPath $env:TEMP -Force
    if ((Test-SitePrivateAcl (Get-SiteRestartAcl $parent) -Directory) -or
        $temp.PSDrive.Root -ne (Get-Item -LiteralPath $parent -Force).PSDrive.Root) {
        # Under a protected parent, or on another volume, use the inherited ACL after checking it.
        $null = New-Item -ItemType Directory -Path $Path -ErrorAction Stop
        Assert-SiteRestartItem $Path -Directory
        Assert-SiteRestartDirControl $Path (Get-SiteRestartAcl $Path)
        Protect-SiteRestartDir $Path
        return
    }
    $work = New-Item -ItemType Directory -Path (Join-Path $temp.FullName ([guid]::NewGuid().ToString())) -ErrorAction Stop
    $stage = Join-Path $work.FullName (Split-Path -Leaf $Path)
    try {
        Assert-SiteRestartItem $work.FullName -Directory
        Assert-SiteRestartDirControl $work.FullName (Get-SiteRestartAcl $work.FullName)
        $null = New-Item -ItemType Directory -Path $stage -ErrorAction Stop
        Assert-SiteRestartItem $stage -Directory
        Assert-SiteRestartDirControl $stage (Get-SiteRestartAcl $stage)
        Protect-SiteRestartDir $stage
        # Destination is the parent. A concurrently created child of the same name makes the move fail.
        Move-Item -LiteralPath $stage -Destination $parent -ErrorAction Stop
    }
    finally {
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -ErrorAction Stop }
        Remove-Item -LiteralPath $work.FullName -ErrorAction Stop
    }
}

function Initialize-SiteRestartDir {
    param([string]$Dir, [switch]$ReadOnly)
    if (-not (Split-Path -IsAbsolute $Dir) -or $Dir -match '(?:^|[\\/])\.{1,2}(?:[\\/]|$)|[*?]') {
        throw 'win_restart_dir must be an absolute directory path without wildcards or relative segments'
    }
    $paths = @()
    $p = $Dir.TrimEnd('\', '/')
    while ($p -and $p -ne $env:ProgramData) {
        $up = Split-Path -Parent $p
        if (-not $up) { break }
        $paths = @($p) + $paths
        $p = $up
    }
    if (-not $paths.Count) { throw 'win_restart_dir must be a dedicated directory' }
    $siteDir = Join-Path $env:ProgramData 'site-automation'
    for ($i = 0; $i -lt $paths.Count; $i++) {
        $path = $paths[$i]
        $exists = Test-Path -LiteralPath $path
        if (-not $exists) {
            if ($ReadOnly) { return }
            New-SiteRestartDir $path
        }
        Assert-SiteRestartItem $path -Directory
        if ($ReadOnly) { continue }
        $acl = Get-SiteRestartAcl $path
        if (Test-SitePrivateAcl $acl -Directory) {
            Assert-SiteRestartItem $path -Directory
            continue
        }
        if (-not $exists) { throw "Unexpected owner or ACL on new directory $path" }

        Assert-SiteRestartDirControl $path $acl $paths[$i + 1]
        Assert-SiteRestartItem $path -Directory
        if ($path -eq $paths[-1] -or $path -eq $siteDir) { Protect-SiteRestartDir $path }
    }
}

function Enter-SiteRestartLock {
    # one schedule or cancel at a time on a server: restart.lock in our folder; one older than 10 minutes is left over
    param([string]$Dir)
    $lock = Join-Path $Dir 'restart.lock'
    if (Test-Path -LiteralPath $lock) {
        Assert-SiteRestartItem $lock
        if ((Get-Item -LiteralPath $lock).LastWriteTime -lt (Get-Date).AddMinutes(-10)) { Remove-Item -LiteralPath $lock -Force }
        else { throw ('another schedule or cancel of a restart is running on this server (' + $lock + '): launch it again in a few minutes') }
    }
    try { $null = New-Item -ItemType File -Path $lock -ErrorAction Stop }
    catch { throw ('another schedule or cancel of a restart is running on this server (' + $lock + '): launch it again in a few minutes') }
    return $lock
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
        $atUtc = ConvertTo-SiteUtc $cfg.at_utc
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
    $folder = ([string]$cfg.task_path).Trim().Trim('\')
    $tpath = '\'
    if ($folder) { $tpath += $folder + '\' }
    $lock = ''
    if ($cfg.action -in @('schedule', 'cancel')) {
        Initialize-SiteRestartDir -Dir $dir
        $lock = Enter-SiteRestartLock -Dir $dir
    }
    try {
        $task = Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir
        $ours = $task.ours
        $out.ours = $ours
        if ($task.collision) { throw $task.collision }
        if ($cfg.action -eq 'schedule' -and -not $out.restart -and $ours.Count -gt 0) {
            # skipped (only if pending, none pending): an earlier restart of ours must not stay armed
            Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
            Remove-SiteRestartFiles -Dir $dir -Id ([string]$ours[0].id)
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
            if (Test-Path -LiteralPath $log) { Assert-SiteRestartFileAcl $log }
            # this registration's own files, never rewritten: an earlier task keeps reading its own until it is replaced
            $rid = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss') + '-' + [string](Get-Random -Minimum 1000 -Maximum 9999)
            $script = Join-Path $dir ('restart-task-' + $rid + '.ps1')
            $json = Join-Path $dir ('restart-' + $rid + '.json')
            # Create both names exclusively in the secured directory. Never write through an existing file or link.
            $null = New-Item -ItemType File -Path $script -ErrorAction Stop
            try {
                $null = New-Item -ItemType File -Path $json -ErrorAction Stop
            } catch { Remove-Item -LiteralPath $script -Force; throw }
            $settingsDoc = @{ only_if_pending = [bool]$cfg.only_if_pending; count_file_renames = [bool]$cfg.count_file_renames;
                              comment = [string]$cfg.comment; code = [string]$cfg.code; delay = [int]$cfg.delay;
                              at_utc = $atUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture);
                              id = $rid }

            $oldXml = ''
            try {
                Set-Content -LiteralPath $script -Value ([string]$cfg.task_script) -Encoding ASCII
                Set-Content -LiteralPath $json -Value (ConvertTo-Json -InputObject $settingsDoc -Compress) -Encoding ASCII
                $null = & icacls.exe $script /setowner '*S-1-5-32-544' /L
                if ($LASTEXITCODE -ne 0) { throw "icacls /setowner on $script failed ($LASTEXITCODE)" }
                if ($ours.Count -gt 0) {
                    $oldScript = Join-Path $dir ('restart-task-' + $ours[0].id + '.ps1')
                    Assert-SiteRestartFileAcl $oldScript
                    $oldXml = [string](Export-ScheduledTask -TaskName $prefix -TaskPath $tpath)
                }
                $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                $xml = New-SiteRestartTaskXml -StartUtc $atUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") -EndUtc $atUtc.AddMinutes(30).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") `
                    -Command $ps -Arguments ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '"') -Description ([string]$cfg.description)
                if ($ours.Count -gt 0) {
                    # replace only the task verified as ours in this run, checked again right before (one step: never both, never none)
                    $again = Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir
                    if ($again.collision -or $again.ours.Count -ne 1 -or [string]$again.ours[0].id -ne [string]$ours[0].id) {
                        throw 'the task changed while this job ran'
                    }
                }
                # When no task existed, create only. A task made by another product meanwhile must survive.
                Assert-SiteRestartFileAcl $script
                $null = Register-ScheduledTask -Xml $xml -TaskName $prefix -TaskPath $tpath -Force:($ours.Count -gt 0)
                $info = Get-ScheduledTaskInfo -TaskName $prefix -TaskPath $tpath
                $next = $info.NextRunTime
                if ($null -eq $next -or (($next.ToUniversalTime() - $atUtc).TotalMinutes -gt 1) -or (($atUtc - $next.ToUniversalTime()).TotalMinutes -gt 1)) {
                    throw ('its next run (' + [string]$next + ') is not ' + $out.at_local)
                }
                $back = Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir
                if ($back.collision -or $back.ours.Count -ne 1 -or [string]$back.ours[0].id -ne $rid) { throw 'it does not read back as ours' }
            }
            catch {
                $why = [string]$_.Exception.Message
                $undo = ''
                try {
                    # only what this run wrote is undone: the task there now must carry this run's registration id
                    $cur = Find-SiteTask -Path $tpath -Name $prefix
                    $mine = ($null -ne $cur) -and (Test-SiteOurTask -Task $cur -Dir $dir) -and ((Get-SiteRegistrationId -Task $cur -Dir $dir) -eq $rid)
                    if (-not $mine) {
                        $undo = 'nothing this run registered is there: left as it is'
                        if ($ours.Count -gt 0) { $undo += ' (the earlier restart, ' + [string]$ours[0].next_run + ', stays)' }
                    }
                    elseif ($oldXml) {
                        Assert-SiteRestartFileAcl $oldScript
                        $null = Register-ScheduledTask -Xml $oldXml -TaskName $prefix -TaskPath $tpath -Force
                        $undo = 'rolled back: the earlier restart (' + [string]$ours[0].next_run + ') is scheduled again'
                    }
                    else {
                        Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
                        $undo = 'rolled back: no restart of ours is scheduled on this server'
                    }
                    Remove-SiteRestartFiles -Dir $dir -Id $rid
                }
                catch { $undo = 'the roll back failed too (' + [string]$_.Exception.Message + '): check Task Scheduler ' + $tpath + $prefix + ' by hand' }
                throw ('the task could not be registered right (' + $why + ') - ' + $undo)
            }
            if ($ours.Count -gt 0) { Remove-SiteRestartFiles -Dir $dir -Id ([string]$ours[0].id) }   # the replaced registration's only
            $out.replaced = $ours
            $out.scheduled = @{ path = $tpath; name = $prefix; next_run = $next.ToString('yyyy-MM-dd HH:mm'); id = $rid }
            $out.ours = (Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir).ours
        }
        if ($cfg.action -eq 'cancel') {
            $removed = @()
            if ($ours.Count -gt 0) {
                Unregister-ScheduledTask -TaskName $prefix -TaskPath $tpath -Confirm:$false
                Remove-SiteRestartFiles -Dir $dir -Id ([string]$ours[0].id)
                $removed = $ours
            }
            $out.removed = $removed
            $out.ours = (Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir).ours
        }
    }
    finally {
        if ($lock -and (Test-Path -LiteralPath $lock)) { Remove-Item -LiteralPath $lock -Force }
    }
}
catch {
    $out.error = [string]$_.Exception.Message
}
Write-Output ('###SITE-JSON### ' + (ConvertTo-Json -InputObject $out -Depth 6 -Compress))
