# site-automation - account lockout (roles/ad_lockout, playbooks/ad_lockout.yml with
# ad_lockout_check_sources): on a computer the bad passwords came from, what runs as the account and
# could be holding an old password. Read-only:
#   - services whose Log On As is the account
#   - scheduled tasks that run as the account
#   - its sessions (quser): a DISCONNECTED Remote Desktop session keeps trying with the old password
#   - processes running as the account
#   - drives it has mapped (only while it is logged on: its registry is loaded then)
# ---- common to every site-automation Windows script (keep identical in all of them) -----------
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG (the role's defaults and your inventory).
# The script prints ONE line: ###SITE-JSON### {...}
# Windows PowerShell 5.1 and PowerShell 7; also works in Constrained Language Mode.
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
function Get-Setting([string] $Name, $Default) {
    if ($null -ne $cfg -and $null -ne $cfg.$Name) { return $cfg.$Name }
    return $Default
}
# ---- end of the common part --------------------------------------------------------------------

$users = @(Get-Setting 'users' @())
$Out = @{ errors = @(); found = @(); name = '' }
function Note([string] $What, $Err) {
    $msg = [string]$Err
    if ($Err -and $Err.Exception) { $msg = [string]$Err.Exception.Message }
    $script:Out.errors += @(('{0}: {1}' -f $What, $msg.Trim()))
}
function Add([string] $Sam, [string] $Kind, [string] $Name, [string] $Detail) {
    $script:Out.found += @(@{ user = $Sam; kind = $Kind; name = $Name; detail = $Detail })
}
# quser writes "No User exists for *" to its error output when nobody is logged on
function Read-Sessions {
    $ErrorActionPreference = 'Continue'
    return @(quser.exe 2>&1 | ForEach-Object { [string]$_ })
}
try { $Out.name = [string]@(Get-CimInstance -ClassName Win32_ComputerSystem)[0].Name } catch { Note 'computer' $_ }

$services = $null
$tasks = $null
$procs = $null
$sessions = $null
try { $services = @(Get-CimInstance -ClassName Win32_Service) } catch { Note 'services' $_ }
try { $tasks = @(Get-ScheduledTask) } catch { Note 'scheduled tasks' $_ }
try { $procs = @(Get-Process -IncludeUserName) } catch { Note 'processes' $_ }
try { $sessions = Read-Sessions } catch { Note 'sessions (quser)' $_ }

foreach ($u in $users) {
    $sam = [string]$u.sam
    # the forms a Log On As / Run As name takes: CORP\jdoe, jdoe@corp.example.mil (UPN), the SID
    $ids = @(('{0}\{1}' -f $u.netbios, $sam), ('{0}@{1}' -f $sam, $u.domain))
    if ([string]$u.upn) { $ids += @([string]$u.upn) }
    if ([string]$u.sid) { $ids += @([string]$u.sid) }
    $ids = @($ids | ForEach-Object { $_.ToLower() })
    foreach ($s in @($services)) {
        if ($s -and $ids -contains ([string]$s.StartName).ToLower()) {
            Add $sam 'service' ([string]$s.Name) ('{0} - {1}, {2}' -f $s.DisplayName, $s.State, $s.StartMode)
        }
    }
    foreach ($t in @($tasks)) {
        if (-not $t) { continue }
        $id = ([string]$t.Principal.UserId).ToLower()
        if ($ids -contains $id -or $id -eq $sam.ToLower()) {
            Add $sam 'scheduled task' (([string]$t.TaskPath) + ([string]$t.TaskName)) ([string]$t.State)
        }
    }
    foreach ($l in @($sessions)) {
        # USERNAME  SESSIONNAME  ID  STATE  IDLE TIME  LOGON TIME (a disconnected session has no SESSIONNAME)
        if ($l -match '^\s*>?(\S+)\s+(?:(\S+)\s+)?(\d+)\s+(\w+)\s+(\S+)\s+(.+?)\s*$' -and $Matches[1] -eq $sam -and $Matches[3] -ne 'ID') {
            Add $sam 'session' ('session ' + $Matches[3]) ('{0}, idle {1}, logged on {2}' -f $Matches[4], $Matches[5], $Matches[6])
        }
    }
    $mine = @($procs | Where-Object { $_ -and $ids -contains ([string]$_.UserName).ToLower() })
    foreach ($g in @($mine | Group-Object -Property ProcessName | Sort-Object -Property Count -Descending | Select-Object -First 15)) {
        Add $sam 'process' ([string]$g.Name) $(if ($g.Count -gt 1) { '{0} running' -f $g.Count } else { 'running' })
    }
    if ([string]$u.sid) {
        $key = 'Registry::HKEY_USERS\{0}\Network' -f $u.sid
        try {
            if (Test-Path -Path $key) {
                foreach ($k in @(Get-ChildItem -Path $key)) {
                    $remote = [string](Get-ItemProperty -Path $k.PSPath).RemotePath
                    Add $sam 'mapped drive' ([string]$k.PSChildName + ':') $remote
                }
            }
        }
        catch { Note ('mapped drives of ' + $sam) $_ }
    }
}

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ source = $Out } -Depth 5 -Compress)
