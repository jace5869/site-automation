# site-automation - Horizon golden image check, the guest part (roles/horizon/tasks/image_check_guest.yml, run by
# playbooks/horizon_image_check.yml). It only reads - installed programs (the Horizon Agent, VMware Tools, FSLogix),
# the Horizon registry keys, services, Windows activation, network adapters, a restart pending, Windows servicing
# at work - and prints them. The decisions are made on the AAP side (plugins/filter/horizon_image_filters.py).
# Read-only: it changes nothing on the VM.
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
# A program (auditpol, net, w32tm, netsh, certreq ...): its output lines and exit code. Windows PowerShell 5.1
# makes each line a program writes to stderr an error when 2>&1 is used, and with 'Stop' above that ends the
# script - here they are lines like the others, and the exit code says whether it worked (-1: it did not start).
function Invoke-Native([string] $Exe, [string[]] $Arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    try { $out = @(& $Exe @Arguments 2>&1 | ForEach-Object { [string]$_ }) }
    catch { $out = @([string]$_) }
    finally { $ErrorActionPreference = $old }
    return New-Object -TypeName PSObject -Property @{ Out = $out; Code = $global:LASTEXITCODE }
}
# ---- end of the common part --------------------------------------------------------------------

$facts.errors = @()
function Add-ReadError([string] $Part, [string] $Text) { $script:facts.errors += @(('{0}: {1}' -f $Part, $Text)) }

# The Windows release, the computer and its domain
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $facts.os = @{ caption = [string]$os.Caption; version = [string]$os.Version; build = [string]$os.BuildNumber }
}
catch { Add-ReadError 'os' $_.Exception.Message }
try {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    $facts.computer = @{ name = [string]$cs.Name; dns_name = [string]$cs.DNSHostName; domain = [string]$cs.Domain
                         part_of_domain = [bool]$cs.PartOfDomain; workgroup = [string]$cs.Workgroup }
}
catch { Add-ReadError 'computer' $_.Exception.Message }

# Installed programs, both Uninstall views (64-bit and 32-bit): the Horizon Agent, VMware Tools, FSLogix.
# InstallDate is yyyyMMdd, as the installer wrote it.
$programs = @()
foreach ($root in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
    if (-not (Test-Path -Path $root)) { continue }
    try {
        foreach ($k in @(Get-ChildItem -Path $root)) {
            $p = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
            if ($null -eq $p) { continue }
            $n = [string]$p.DisplayName
            if ($n -match 'Horizon|View Agent|VMware Tools|FSLogix|Omnissa') {
                $programs += @(@{ name = $n; version = [string]$p.DisplayVersion; publisher = [string]$p.Publisher
                                  install_date = [string]$p.InstallDate; key = [string]$k.PSChildName })
            }
        }
    }
    catch { Add-ReadError 'programs' $_.Exception.Message }
}
$facts.programs = $programs

# The Horizon Agent's own registry keys: VMware's (VMware, Inc.\VMware VDM) and Omnissa's (any key under an Omnissa
# vendor key whose name says Horizon or VDM) - the values that name a version, and whether it has an Agent key
$keys = @()
$cands = @('HKLM:\SOFTWARE\VMware, Inc.\VMware VDM')
foreach ($vendor in @('HKLM:\SOFTWARE\Omnissa', 'HKLM:\SOFTWARE\Omnissa, Inc.')) {
    if (Test-Path -Path $vendor) {
        try { $cands += @(Get-ChildItem -Path $vendor | Where-Object { $_.PSChildName -match 'Horizon|VDM' } | ForEach-Object { $vendor + '\' + $_.PSChildName }) }
        catch { Add-ReadError 'omnissa' $_.Exception.Message }
    }
}
foreach ($path in $cands) {
    if (-not (Test-Path -Path $path)) { continue }
    $vals = @{}
    try {
        $item = Get-ItemProperty -Path $path
        foreach ($prop in @($item.PSObject.Properties)) {
            if ($prop.Name -match 'Version|Build' -and $prop.Name -notmatch '^PS') { $vals[$prop.Name] = [string]$prop.Value }
        }
    }
    catch { Add-ReadError 'registry' $_.Exception.Message }
    $keys += @(@{ path = $path.Replace('HKLM:\', 'HKLM\'); values = $vals; agent = (Test-Path -Path ($path + '\Agent')) })
}
$facts.horizon_keys = $keys

# Services: the Instant Clone Agent (vmware-viewcomposer-ga; by its display name too, the name may differ in
# Omnissa releases), the Horizon Agent, VMware Tools, FSLogix, Windows Update and the Windows Modules Installer
$names = @('vmware-viewcomposer-ga', 'omnissa-viewcomposer-ga', 'WSNM', 'VMTools', 'frxsvc', 'frxccds', 'wuauserv', 'TrustedInstaller', 'UsoSvc')
try {
    $facts.services = @(Get-CimInstance -ClassName Win32_Service | Where-Object {
            ($names -contains $_.Name) -or ([string]$_.DisplayName -match 'Instant Clone|Horizon|Omnissa|FSLogix') } | ForEach-Object {
            @{ name = [string]$_.Name; display_name = [string]$_.DisplayName; state = [string]$_.State; start_mode = [string]$_.StartMode } })
}
catch { Add-ReadError 'services' $_.Exception.Message }

# Windows servicing at work right now
$facts.busy = @(Get-Process -Name @('TiWorker', 'TrustedInstaller', 'MoUsoCoreWorker', 'wuauclt', 'UsoClient') -ErrorAction SilentlyContinue |
        ForEach-Object { [string]$_.ProcessName } | Sort-Object -Unique)

# Windows activation: the Windows product with a key (ApplicationID of Windows), its channel and status
try {
    $facts.licensing = @(Get-CimInstance -ClassName SoftwareLicensingProduct -Filter "ApplicationID = '55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" |
            ForEach-Object { @{ name = [string]$_.Name; description = [string]$_.Description; channel = [string]$_.ProductKeyChannel
                                license_status = [int]$_.LicenseStatus; ad_activation = [string]$_.ADActivationObjectName
                                kms = [string]$_.KeyManagementServiceMachine; kms_found = [string]$_.DiscoveredKeyManagementServiceMachineName } })
}
catch { Add-ReadError 'licensing' $_.Exception.Message }

# Network adapters with IP on: DHCP or a fixed address
try {
    $facts.nics = @(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter 'IPEnabled = TRUE' | ForEach-Object {
            @{ description = [string]$_.Description; dhcp = [bool]$_.DHCPEnabled; ips = @($_.IPAddress | ForEach-Object { [string]$_ }) } })
}
catch { Add-ReadError 'nics' $_.Exception.Message }

# A restart pending: component servicing, Windows Update, file renames, a new computer name, a domain join
$sm = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'
$pfro = Get-ItemProperty -Path $sm -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
$active = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName' -Name ComputerName -ErrorAction SilentlyContinue
$next = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName' -Name ComputerName -ErrorAction SilentlyContinue
$facts.reboot = @{
    cbs = (Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
    windows_update = (Test-Path -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
    file_renames = @(@($pfro.PendingFileRenameOperations) | Where-Object { $_ }).Count
    rename = ($null -ne $active -and $null -ne $next -and [string]$active.ComputerName -ne [string]$next.ComputerName)
    domain_join = (Test-Path -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\JoinDomain')
}
Write-Result
