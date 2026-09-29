# site-automation - Windows check: security (roles/win_check_security, run by playbooks/win_health_check.yml).
# Defender (when it is the active antivirus), other endpoint services, firewall, SMBv1, RDP NLA, UAC, TLS 1.0/1.1.
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

$av = [string](Get-Setting 'av' 'auto')
$sigMax = [int](Get-Setting 'av_signature_max_days' 7)
# Antivirus: Microsoft Defender when it is the active antivirus (auto), or always (defender).
if ($av -ne 'none') {
    $mp = $null
    try { $mp = Get-MpComputerStatus -ErrorAction Stop } catch { $mp = $null }
    $mode = ''
    if ($null -ne $mp) { $mode = [string]$mp.AMRunningMode }
    $active = ($null -ne $mp) -and [bool]$mp.AMServiceEnabled -and ($mode -eq '' -or $mode -eq 'Normal')
    $facts.defender = @{ present = ($null -ne $mp); mode = $mode; active = $active }
    if ($av -eq 'defender' -and $null -eq $mp) {
        Add-Finding 'security:defender' 'critical' 'Microsoft Defender Antivirus is not available on this server' 'Get-MpComputerStatus; Get-WindowsFeature Windows-Defender'
    }
    elseif ($null -ne $mp -and ($av -eq 'defender' -or $active)) {
        if (-not [bool]$mp.RealTimeProtectionEnabled) {
            Add-Finding 'security:defender-realtime' 'critical' 'Microsoft Defender real-time protection is off' 'Get-MpComputerStatus | Format-List *Enabled*'
        }
        if ([int]$mp.AntivirusSignatureAge -gt $sigMax) {
            Add-Finding 'security:defender-signatures' 'warning' ('Defender antivirus signatures are {0} days old (maximum {1}; version {2})' -f $mp.AntivirusSignatureAge, $sigMax, $mp.AntivirusSignatureVersion) 'Get-MpComputerStatus | Format-List AntivirusSignature*; Update-MpSignature'
        }
    }
}
# Other endpoint protection that must run (e.g. Trellix / McAfee agent services)
foreach ($n in @(Get-Setting 'av_services' @())) {
    $s = Get-CimInstance -ClassName Win32_Service -Filter ("Name='{0}'" -f ([string]$n -replace "'", ''))
    if ($null -eq $s -or $s.State -ne 'Running') {
        $state = 'not installed'
        if ($null -ne $s) { $state = [string]$s.State }
        Add-Finding "security:av-service:$n" 'critical' "endpoint protection service $n is $state" "Get-Service $n"
    }
}
# Windows Firewall: every required profile on
$need = @(Get-Setting 'firewall_profiles' @('Domain', 'Private', 'Public'))
try {
    foreach ($p in @(Get-NetFirewallProfile -ErrorAction Stop)) {
        if ($need -contains [string]$p.Name -and [string]$p.Enabled -ne 'True') {
            Add-Finding "security:firewall:$($p.Name)" 'critical' "Windows Firewall is off for the $($p.Name) profile" 'Get-NetFirewallProfile | Format-Table Name, Enabled'
        }
    }
}
catch { $facts.firewall_unreadable = $_.Exception.Message }
# SMBv1 (STIG: off)
if (Get-Setting 'smb1' $true) {
    try {
        $smb = Get-SmbServerConfiguration -ErrorAction Stop
        if ([bool]$smb.EnableSMB1Protocol) { Add-Finding 'security:smb1' 'critical' 'SMBv1 is enabled on the file server side (STIG: it must be off)' 'Get-SmbServerConfiguration | Format-List EnableSMB1Protocol; Set-SmbServerConfiguration -EnableSMB1Protocol $false' }
    }
    catch { }
}
# Remote Desktop without Network Level Authentication
if (Get-Setting 'rdp_nla' $true) {
    $ts = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -ErrorAction SilentlyContinue
    $rdpOn = ($null -ne $ts) -and ([int]$ts.fDenyTSConnections -eq 0)
    $nla = 1
    $tcp = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -ErrorAction SilentlyContinue
    if ($null -ne $tcp -and $null -ne $tcp.UserAuthentication) { $nla = [int]$tcp.UserAuthentication }
    $pol = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services' -ErrorAction SilentlyContinue
    if ($null -ne $pol -and $null -ne $pol.UserAuthentication) { $nla = [int]$pol.UserAuthentication }
    if ($rdpOn -and $nla -ne 1) { Add-Finding 'security:rdp-nla' 'warning' 'Remote Desktop accepts connections without Network Level Authentication' 'Get-ItemProperty ''HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'' -Name UserAuthentication' }
}
# User Account Control
if (Get-Setting 'uac' $true) {
    $sys = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -ErrorAction SilentlyContinue
    if ($null -ne $sys -and $null -ne $sys.EnableLUA -and [int]$sys.EnableLUA -ne 1) { Add-Finding 'security:uac' 'critical' 'User Account Control is off (EnableLUA = 0)' 'Get-ItemProperty ''HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'' -Name EnableLUA' }
}
# TLS 1.0 / 1.1 on the server side (STIG: off). No registry value = the OS default, which is on
# before Windows Server 2025.
if (Get-Setting 'legacy_tls' $true) {
    $build = [int](Get-CimInstance -ClassName Win32_OperatingSystem).BuildNumber
    foreach ($v in @('TLS 1.0', 'TLS 1.1')) {
        $k = Get-ItemProperty -Path ("HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$v\Server") -ErrorAction SilentlyContinue
        $on = $build -lt 26100
        if ($null -ne $k -and $null -ne $k.Enabled) { $on = ([int]$k.Enabled -ne 0) }
        if ($null -ne $k -and $null -ne $k.DisabledByDefault -and [int]$k.DisabledByDefault -eq 1 -and $null -eq $k.Enabled) { $on = $false }
        if ($on) { Add-Finding "security:$($v -replace ' ', '')" 'warning' "$v is not turned off for incoming connections (SCHANNEL Server key; STIG: off)" "Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$v\Server'" }
    }
}
Write-Result
