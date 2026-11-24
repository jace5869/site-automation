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
        if ($null -ne $i.NextRunTime -and $i.NextRunTime.Year -gt 2000) { $next = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture) }
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
    try { Initialize-SiteRestartDir -Dir $Dir -ReadOnly }
    catch [System.Management.Automation.ItemNotFoundException] { return '' }
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
    Initialize-SiteRestartDir -Dir $Dir -ReadOnly
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

function Invoke-SiteIcacls {
    param([string]$Path, [string[]]$Options)
    $null = & "$env:SystemRoot\System32\icacls.exe" $Path /L @Options
    if ($LASTEXITCODE -ne 0) { throw "icacls $($Options[0]) on $Path failed ($LASTEXITCODE)" }
}

function Get-SiteRestartAcl {
    param([string]$Path, [switch]$Directory)
    # /save supplies the DACL as SDDL, independent of the host language. It omits the owner.
    $record = New-TemporaryFile -ErrorAction Stop
    try {
        Assert-SiteRestartItem $Path -Directory:$Directory
        Invoke-SiteIcacls $Path @('/save', $record.FullName)
        Assert-SiteRestartItem $Path -Directory:$Directory
        $ownerSddl = (Get-Acl -LiteralPath $Path -ErrorAction Stop).Sddl
        Assert-SiteRestartItem $Path -Directory:$Directory
        $lines = @(Get-Content -LiteralPath $record.FullName -Encoding Unicode -ErrorAction Stop)
        if ($lines.Count -ne 2 -or $lines[0] -ne (Split-Path -Leaf $Path) -or $lines[1] -notlike 'D:*') {
            throw "Cannot read DACL on $Path"
        }
        if ($ownerSddl -notmatch '^O:([A-Z]{2}|S-[0-9-]+)(?=G:|D:|S:|$)') { throw "Cannot read owner on $Path" }
        # Decode the owner's SID and the link's DACL together. CIM preserves directory inheritance flags.
        $sddl = 'O:' + $Matches[1] + $lines[1]
        $result = Invoke-CimMethod -ClassName Win32_SecurityDescriptorHelper -MethodName SDDLToWin32SD -Arguments @{ SDDL = $sddl } -ErrorAction Stop
        if ($result.ReturnValue -ne 0 -or -not $result.Descriptor.Owner.SIDString) { throw "Cannot decode security descriptor on $Path" }
        return $result.Descriptor
    }
    finally { Remove-Item -LiteralPath $record.FullName -Force }
}

function Assert-SiteRestartAcl {
    param([string]$Path, [switch]$Directory, [switch]$NewDirectory)
    $acl = Get-SiteRestartAcl $Path -Directory:$Directory
    $admins = 'S-1-5-32-544'
    $system = 'S-1-5-18'
    $trusted = @($admins, $system, 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if ($acl.Owner.SIDString -notin $trusted) { throw "Unsafe existing owner $($acl.Owner.SIDString) on $Path" }
    if ($null -eq $acl.DACL) { throw "Missing DACL on $Path" }
    $full = @()
    foreach ($ace in $acl.DACL) {
        $sid = $ace.Trustee.SIDString
        if ($Directory -and $ace.AceType -eq 1) { continue }
        if ($ace.AceType -ne 0 -or -not $sid -or $null -eq $ace.AccessMask) { throw "Unrecognized ACE on $Path" }
        # Read, execute, synchronize and generic read/execute are safe for other trustees.
        # Check even inherit-only grants: these can make the task's files writable.
        if ($sid -notin $trusted -and ($ace.AccessMask -band 0x5fedff56)) {
            throw "Unsafe existing permissions on $Path (write or control granted to $sid)"
        }
        if ($ace.AccessMask -eq 0x1f01ff) {
            $flags = [int]$ace.AceFlags
            if (($Directory -and $flags -eq 3) -or (-not $Directory -and $flags -in @(0, 16))) { $full += $sid }
        }
    }
    if (-not $Directory -and ($system -notin $full -or $admins -notin $full)) {
        throw "Missing SYSTEM or Administrators full control on $Path"
    }
    if ($NewDirectory -and ($acl.Owner.SIDString -ne $admins -or -not ([int]$acl.ControlFlags -band 0x1000) -or
        $acl.DACL.Count -ne 2 -or $system -notin $full -or $admins -notin $full)) {
        throw "Unexpected owner or ACL on new directory $Path"
    }
}

function New-SiteRestartDir {
    param([string]$Path)
    # md reports an existing name as a failure, including a directory created after our lookup.
    # Delayed expansion passes the path as data without interpreting shell characters in it.
    $env:SITE_RESTART_NEW_DIR = $Path
    try {
        $null = & "$env:SystemRoot\System32\cmd.exe" /d /e:off /v:on /c 'md "!SITE_RESTART_NEW_DIR!"'
        if ($LASTEXITCODE -ne 0) { throw "Could not create new directory $Path ($LASTEXITCODE)" }
    }
    finally { Remove-Item Env:SITE_RESTART_NEW_DIR }
    Assert-SiteRestartItem $Path -Directory
    Invoke-SiteIcacls $Path @('/setowner', '*S-1-5-32-544')
    Assert-SiteRestartItem $Path -Directory
    Invoke-SiteIcacls $Path @('/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F')
    Assert-SiteRestartAcl $Path -Directory -NewDirectory
}

function Initialize-SiteRestartDir {
    param([string]$Dir, [switch]$ReadOnly)
    if (-not (Split-Path -IsAbsolute $Dir) -or $Dir -match '(?:^|[\\/])\.{1,2}(?:[\\/]|$)|[*?]') {
        throw 'win_restart_dir must be an absolute directory path without wildcards or relative segments'
    }
    if ($Dir -notmatch '^(?:[a-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+[\\/]|/(?!/))') { throw 'win_restart_dir needs a filesystem root' }
    $root = $Matches[0]
    $path = $root
    $paths = @(foreach ($part in ($Dir.Substring($root.Length) -split '[\\/]')) {
        if ($part) {
            $path = Join-Path $path $part
            $path
        }
    })
    if (-not $paths.Count -or $paths[-1] -eq $env:ProgramData) { throw 'win_restart_dir must be a dedicated directory' }
    Assert-SiteRestartItem $root -Directory
    $aboveManagedDirs = $env:ProgramData -in $paths
    foreach ($path in $paths) {
        if ($aboveManagedDirs) {
            Assert-SiteRestartItem $path -Directory
            $aboveManagedDirs = $path -ne $env:ProgramData
            continue
        }
        try { Assert-SiteRestartItem $path -Directory }
        catch [System.Management.Automation.ItemNotFoundException] {
            if ($ReadOnly) { throw }
            New-SiteRestartDir $path
            continue
        }
        Assert-SiteRestartAcl $path -Directory
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
            if (Test-Path -LiteralPath $log) { Assert-SiteRestartAcl $log }
            # this registration's own files, never rewritten: an earlier task keeps reading its own until it is replaced
            $rid = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss', [Globalization.CultureInfo]::InvariantCulture) + '-' + [string](Get-Random -Minimum 1000 -Maximum 9999)
            $script = Join-Path $dir ('restart-task-' + $rid + '.ps1')
            $json = Join-Path $dir ('restart-' + $rid + '.json')
            # Create both names exclusively in the secured directory. Never write through an existing file or link.
            Initialize-SiteRestartDir -Dir $dir -ReadOnly
            $null = New-Item -ItemType File -Path $script -ErrorAction Stop
            try {
                $null = New-Item -ItemType File -Path $json -ErrorAction Stop
            } catch {
                Initialize-SiteRestartDir -Dir $dir -ReadOnly
                Assert-SiteRestartItem $script
                Remove-Item -LiteralPath $script -Force
                throw
            }
            $settingsDoc = @{ only_if_pending = [bool]$cfg.only_if_pending; count_file_renames = [bool]$cfg.count_file_renames;
                              comment = [string]$cfg.comment; code = [string]$cfg.code; delay = [int]$cfg.delay;
                              at_utc = $atUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture);
                              id = $rid }

            $oldXml = ''
            try {
                foreach ($file in @($script, $json)) {
                    Assert-SiteRestartItem $file
                    Invoke-SiteIcacls $file @('/setowner', '*S-1-5-32-544')
                }
                Initialize-SiteRestartDir -Dir $dir -ReadOnly
                Assert-SiteRestartAcl $script
                Set-Content -LiteralPath $script -Value ([string]$cfg.task_script) -Encoding ASCII
                Assert-SiteRestartAcl $json
                Set-Content -LiteralPath $json -Value (ConvertTo-Json -InputObject $settingsDoc -Compress) -Encoding ASCII
                if ($ours.Count -gt 0) {
                    $oldScript = Join-Path $dir ('restart-task-' + $ours[0].id + '.ps1')
                    Assert-SiteRestartAcl $oldScript
                    $oldXml = [string](Export-ScheduledTask -TaskName $prefix -TaskPath $tpath)
                }
                $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                $endUtc = $atUtc.AddMinutes(30).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
                $xml = New-SiteRestartTaskXml -StartUtc $settingsDoc.at_utc -EndUtc $endUtc `
                    -Command $ps -Arguments ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '"') -Description ([string]$cfg.description)
                if ($ours.Count -gt 0) {
                    # replace only the task verified as ours in this run, checked again right before (one step: never both, never none)
                    $again = Get-SiteRestartTask -Path $tpath -Name $prefix -Dir $dir
                    if ($again.collision -or $again.ours.Count -ne 1 -or [string]$again.ours[0].id -ne [string]$ours[0].id) {
                        throw 'the task changed while this job ran'
                    }
                }
                # When no task existed, create only. A task made by another product meanwhile must survive.
                Initialize-SiteRestartDir -Dir $dir -ReadOnly
                Assert-SiteRestartAcl $script
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
                        Initialize-SiteRestartDir -Dir $dir -ReadOnly
                        Assert-SiteRestartAcl $oldScript
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
            $out.scheduled = @{ path = $tpath; name = $prefix; next_run = $next.ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture); id = $rid }
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
        if ($lock) {
            Initialize-SiteRestartDir -Dir $dir -ReadOnly
            Assert-SiteRestartItem $lock
            Remove-Item -LiteralPath $lock -Force
        }
    }
}
catch {
    $out.error = [string]$_.Exception.Message
}
Write-Output ('###SITE-JSON### ' + (ConvertTo-Json -InputObject $out -Depth 6 -Compress))
