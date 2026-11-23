# site-automation - Windows - local group members (roles/win_local_groups, run by playbooks/win_local_groups.yml).
# Who is in the server's privileged local groups. READ ONLY: it changes nothing on the server.
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG: {"groups": ["Administrators", "S-1-5-32-555", ...]}.
# A group is found by its well-known SID first (S-1-5-32-544 is Administrators on an English, a German or any other
# Windows), else by its name. Members come from Get-LocalGroupMember; when that fails (an orphaned SID, an Azure AD
# member: "Failed to compare two elements"), from ADSI (WinNT://) - a member whose SID does not resolve is listed as
# "unresolved SID S-1-5-21-...", never dropped.
# The script prints ONE line: ###SITE-JSON### {"computer": "...", "domain": "...", "groups": [...]}
# Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
$wanted = @()
if ($null -ne $cfg -and $null -ne $cfg.groups) { $wanted = @($cfg.groups) }
if ($wanted.Count -eq 0) { $wanted = @('Administrators', 'Remote Desktop Users', 'Remote Management Users', 'Backup Operators',
                                        'Server Operators', 'Print Operators', 'Account Operators') }
$known = @{ 'administrators' = 'S-1-5-32-544'; 'remote desktop users' = 'S-1-5-32-555';
            'remote management users' = 'S-1-5-32-580'; 'backup operators' = 'S-1-5-32-551';
            'power users' = 'S-1-5-32-547'; 'users' = 'S-1-5-32-545'; 'guests' = 'S-1-5-32-546';
            'account operators' = 'S-1-5-32-548'; 'server operators' = 'S-1-5-32-549'; 'print operators' = 'S-1-5-32-550';
            'network configuration operators' = 'S-1-5-32-556'; 'event log readers' = 'S-1-5-32-573';
            'distributed com users' = 'S-1-5-32-562'; 'hyper-v administrators' = 'S-1-5-32-578' }
$computer = $env:COMPUTERNAME
$domain = ''
$domainRole = -1
$os = ''
try { $cs = Get-CimInstance -ClassName Win32_ComputerSystem; $domain = [string]$cs.Domain; $domainRole = [int]$cs.DomainRole } catch { $domain = '' }
try { $os = [string](Get-CimInstance -ClassName Win32_OperatingSystem).Caption } catch { $os = '' }
$haveCmdlet = $null -ne (Get-Command -Name Get-LocalGroupMember -ErrorAction SilentlyContinue)

# the local users: enabled and last logon, by SID (cheap: one call)
$localUsers = @{}
try {
    if ($null -ne (Get-Command -Name Get-LocalUser -ErrorAction SilentlyContinue)) {
        foreach ($u in @(Get-LocalUser)) {
            $ll = ''
            if ($null -ne $u.LastLogon) { $ll = $u.LastLogon.ToString('yyyy-MM-dd') }
            $localUsers[[string]$u.SID.Value] = @{ enabled = [bool]$u.Enabled; last_logon = $ll; name = [string]$u.Name }
        }
    }
} catch { $localUsers = @{} }

function Get-SidText($Bytes) {
    try { return (New-Object System.Security.Principal.SecurityIdentifier -ArgumentList ([byte[]]$Bytes), 0).Value } catch { return '' }
}
function Resolve-Sid([string] $Sid) {
    try { return [string](New-Object System.Security.Principal.SecurityIdentifier $Sid).Translate([System.Security.Principal.NTAccount]).Value } catch { return '' }
}
function Get-Source([string] $Sid, [string] $Name, [string] $Given) {
    if ($Given -eq 'AzureAD' -or $Sid -like 'S-1-12-1-*') { return 'AzureAD' }
    if (-not $Name) { return 'Unresolved' }
    $machineSid = ''
    foreach ($k in $localUsers.Keys) { if ($k -match '^(S-1-5-21-\d+-\d+-\d+)-\d+$') { $machineSid = $Matches[1]; break } }
    if ($Sid -like 'S-1-5-32-*' -or ($machineSid -and $Sid -like ($machineSid + '-*')) -or $Name -like ($computer + '\*')) { return 'Local' }
    if ($Sid -like 'S-1-5-21-*') { return 'Domain' }
    return 'Local'
}
function New-Member([string] $Name, [string] $Sid, [string] $Class, [string] $Given) {
    $src = Get-Source $Sid $Name $Given
    $m = @{ name = $Name; sid = $Sid; type = $Class; source = $src; enabled = $null; last_logon = '' }
    if (-not $Name) { $m.name = 'unresolved SID ' + $Sid; $m.type = 'Unknown' }
    if ($localUsers.ContainsKey($Sid)) { $m.enabled = $localUsers[$Sid].enabled; $m.last_logon = $localUsers[$Sid].last_logon; $m.type = 'User' }
    return $m
}

$groups = @()
foreach ($w in $wanted) {
    $asked = [string]$w
    $sid = ''
    if ($asked -match '^S-1-5-32-\d+$') { $sid = $asked } elseif ($known.ContainsKey($asked.ToLower())) { $sid = $known[$asked.ToLower()] }
    $g = @{ asked = $asked; sid = $sid; name = ''; found = $false; method = ''; error = ''; members = @() }
    $local = $null
    try {
        if ($haveCmdlet) {
            # a group that is not there is GroupNotFound; any other error (access denied) is said, never taken as "no such group"
            if ($sid) {
                try { $local = Get-LocalGroup -SID $sid -ErrorAction Stop }
                catch { if ($_.FullyQualifiedErrorId -notlike 'GroupNotFound*') { throw } }
            }
            if ($null -eq $local) {
                try { $local = Get-LocalGroup -Name $asked -ErrorAction Stop }
                catch { if ($_.FullyQualifiedErrorId -notlike 'GroupNotFound*') { throw } }
            }
            if ($null -ne $local) { $g.name = [string]$local.Name; $g.sid = [string]$local.SID.Value }
        } else {
            if ($sid) { $g.name = (Resolve-Sid $sid) -replace '^.*\\', '' }
            if (-not $g.name) { $g.name = $asked }
        }
    } catch { $g.error = 'not looked up: ' + $_.Exception.Message }
    if (-not $g.name -and $g.error -and $sid) {
        # the lookup failed (not "not found"): ADSI may still read it, by its name in this language
        $g.name = (Resolve-Sid $sid) -replace '^.*\\', ''
    }
    if (-not $g.name) {
        if (-not $g.error) { $g.error = 'no such local group on this server' }
        $groups += @($g)
        continue
    }
    $g.found = $true
    $done = $false
    if ($haveCmdlet -and $null -ne $local) {
        try {
            foreach ($m in @(Get-LocalGroupMember -Group $local -ErrorAction Stop)) {
                $ms = [string]$m.SID.Value
                $mn = [string]$m.Name
                if ($mn -eq $ms) { $mn = '' }
                $g.members += @(New-Member $mn $ms ([string]$m.ObjectClass) ([string]$m.PrincipalSource))
            }
            $g.method = 'Get-LocalGroupMember'
            $done = $true
        } catch { $g.error = 'Get-LocalGroupMember failed (' + $_.Exception.Message + '), read with ADSI' ; $g.members = @() }
    }
    if (-not $done) {
        try {
            $adsi = [ADSI]('WinNT://./' + $g.name + ',group')
            foreach ($o in @($adsi.psbase.Invoke('Members'))) {
                # each property on its own: a stale entry loses what it cannot say, never the members after it
                $path = ''; $cls = ''; $ms = ''
                try { $path = [string]$o.GetType().InvokeMember('ADsPath', 'GetProperty', $null, $o, $null) } catch { $path = '' }
                try { $cls = [string]$o.GetType().InvokeMember('Class', 'GetProperty', $null, $o, $null) } catch { $cls = '' }
                try { $ms = Get-SidText ($o.GetType().InvokeMember('objectSid', 'GetProperty', $null, $o, $null)) } catch { $ms = '' }
                if (-not $ms -and $path -match '(S-1-[0-9-]+)$') { $ms = $Matches[1] }
                $mn = ''
                if ($ms) { $mn = Resolve-Sid $ms }
                if (-not $mn -and $path -and $path -notmatch '^WinNT://S-1-') { $mn = ($path -replace '^WinNT://', '') -replace '/', '\' }
                if (-not $ms -and -not $mn) { $ms = 'unknown (ADSI: ' + $path + ')' }
                if ($cls) { $cls = $cls.Substring(0, 1).ToUpper() + $cls.Substring(1) } else { $cls = 'Unknown' }
                $g.members += @(New-Member $mn $ms $cls '')
            }
            $g.method = 'ADSI'
        } catch {
            $g.found = $false
            $g.error = ($g.error + ' ADSI failed: ' + $_.Exception.Message).Trim()
        }
    }
    $groups += @($g)
}
'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ computer = $computer; domain = $domain; domain_role = $domainRole; os = $os; groups = @($groups) } -Depth 8 -Compress)
