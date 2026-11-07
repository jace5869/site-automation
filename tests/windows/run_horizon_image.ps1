# Run the Horizon golden image check's guest script (roles/horizon/files/horizon_image.ps1) against a made-up golden
# image (a "scenario"), for tests/test_horizon_image.py: the Windows commands it uses (Get-CimInstance, the registry
# through Get-ChildItem / Get-ItemProperty / Test-Path, Get-Process) are replaced by functions that return the
# scenario's data, so the script runs anywhere PowerShell runs, also on Linux, and in Constrained Language Mode.
#   pwsh -File tests/windows/run_horizon_image.ps1 -Scenario ready [-Clm]
param(
    [Parameter(Mandatory = $true)][string] $Scenario,
    [switch] $Clm
)
$ErrorActionPreference = 'Stop'
function O($h) { return [pscustomobject]$h }
$U = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
$U32 = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
$M = @{ cim = @{}; reg = @{}; keys = @{}; paths = @(); procs = @() }
function Program([string] $Root, [string] $Key, [string] $Name, [string] $Version, [string] $Date) {
    $p = "$Root\$Key"
    if (-not $M.keys.ContainsKey($Root)) { $M.keys[$Root] = @() }
    $M.keys[$Root] += @(O @{ PSChildName = $Key; PSPath = $p })
    $M.reg[$p] = O @{ DisplayName = $Name; DisplayVersion = $Version; Publisher = 'test'; InstallDate = $Date }
}
# ---- the scenarios --------------------------------------------------------------------------
$M.paths = @($U, $U32)
$M.cim.Win32_OperatingSystem = @(O @{ Caption = 'Microsoft Windows 11 Enterprise'; Version = '10.0.22631'; BuildNumber = '22631' })
$M.cim.Win32_ComputerSystem = @(O @{ Name = 'W11-GOLD'; DNSHostName = 'w11-gold'; Domain = 'corp.example.mil'; PartOfDomain = $true; Workgroup = $null })
$M.cim.Win32_NetworkAdapterConfiguration = @(O @{ Description = 'vmxnet3 Ethernet Adapter'; DHCPEnabled = $true; IPEnabled = $true; IPAddress = @('192.0.2.50', 'fe80::1') })
$M.cim.SoftwareLicensingProduct = @(O @{ Name = 'Windows(R), Enterprise edition'; Description = 'Windows(R) Operating System, VOLUME_KMSCLIENT channel'
        ProductKeyChannel = 'Volume:GVLK'; LicenseStatus = 1; ADActivationObjectName = $null; KeyManagementServiceMachine = $null
        DiscoveredKeyManagementServiceMachineName = 'kmshost.corp.example.mil'; ApplicationID = '55c92734-d682-4d71-983e-d6ec3f16059f'; PartialProductKey = 'ABCDE' })
$M.cim.Win32_Service = @(
    (O @{ Name = 'vmware-viewcomposer-ga'; DisplayName = 'Omnissa Horizon Instant Clone Agent'; State = 'Running'; StartMode = 'Auto' }),
    (O @{ Name = 'WSNM'; DisplayName = 'Omnissa Horizon View Agent'; State = 'Running'; StartMode = 'Auto' }),
    (O @{ Name = 'frxsvc'; DisplayName = 'FSLogix Apps Services'; State = 'Running'; StartMode = 'Auto' }),
    (O @{ Name = 'wuauserv'; DisplayName = 'Windows Update'; State = 'Stopped'; StartMode = 'Manual' }),
    (O @{ Name = 'TrustedInstaller'; DisplayName = 'Windows Modules Installer'; State = 'Stopped'; StartMode = 'Manual' }),
    (O @{ Name = 'Spooler'; DisplayName = 'Print Spooler'; State = 'Running'; StartMode = 'Auto' }))
Program $U '{T}' 'VMware Tools' '12.4.0.23259341' '20261001'
Program $U '{A}' 'Omnissa Horizon Agent' '8.14.0.25172' '20261002'
Program $U '{F}' 'Microsoft FSLogix Apps' '2.9.8884.27471' '20261003'
Program $U '{N}' 'Notepad++ (64-bit x64)' '8.6' '20260901'
Program $U32 '{R}' 'Microsoft FSLogix Apps RuleEditor' '2.9.8884.27471' '20261003'
$M.paths += @('HKLM:\SOFTWARE\Omnissa', 'HKLM:\SOFTWARE\Omnissa\Horizon', 'HKLM:\SOFTWARE\Omnissa\Horizon\Agent')
$M.keys['HKLM:\SOFTWARE\Omnissa'] = @((O @{ PSChildName = 'Horizon'; PSPath = 'HKLM:\SOFTWARE\Omnissa\Horizon' }), (O @{ PSChildName = 'Other'; PSPath = 'x' }))
$M.reg['HKLM:\SOFTWARE\Omnissa\Horizon'] = O @{ ProductVersion = '8.14.0'; ProductName = 'Horizon Agent'; BuildNumber = '25172' }
$M.reg['HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName'] = O @{ ComputerName = 'W11-GOLD' }
$M.reg['HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName'] = O @{ ComputerName = 'W11-GOLD' }
switch ($Scenario) {
    'ready' { }
    'not_ready' {
        # VMware's agent (no Omnissa key), its Instant Clone Agent left out; Tools upgraded after it; a workgroup; MAK on
        # Horizon 2209; a fixed address; a restart pending; Windows Update at work; no FSLogix
        $M.keys.Remove('HKLM:\SOFTWARE\Omnissa')
        $M.paths = @($U, $U32, 'HKLM:\SOFTWARE\VMware, Inc.\VMware VDM', 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
        $M.reg['HKLM:\SOFTWARE\VMware, Inc.\VMware VDM'] = O @{ ProductVersion = '8.7.0' }
        $M.keys[$U] = @(); $M.keys[$U32] = @()
        Program $U '{T}' 'VMware Tools' '12.5.0.24276846' '20261020'
        Program $U '{A}' 'VMware Horizon Agent' '8.7.0.20616018' '20260115'
        $M.cim.Win32_ComputerSystem = @(O @{ Name = 'W11-GOLD'; DNSHostName = 'w11-gold'; Domain = 'WORKGROUP'; PartOfDomain = $false; Workgroup = 'WORKGROUP' })
        $M.cim.SoftwareLicensingProduct = @(O @{ Name = 'Windows(R), Enterprise edition'; Description = 'Windows(R) Operating System, VOLUME_MAK channel'
                ProductKeyChannel = 'Volume:MAK'; LicenseStatus = 1 })
        $M.cim.Win32_NetworkAdapterConfiguration = @(O @{ Description = 'vmxnet3 Ethernet Adapter'; DHCPEnabled = $false; IPEnabled = $true; IPAddress = @('192.0.2.60') })
        $M.cim.Win32_Service = @(
            (O @{ Name = 'TrustedInstaller'; DisplayName = 'Windows Modules Installer'; State = 'Running'; StartMode = 'Manual' }),
            (O @{ Name = 'wuauserv'; DisplayName = 'Windows Update'; State = 'Running'; StartMode = 'Manual' }))
        $M.procs = @(O @{ ProcessName = 'TiWorker' })
        $M.reg['HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'] = O @{ PendingFileRenameOperations = @('\??\C:\a.tmp', '', '\??\C:\b.tmp', '') }
    }
    'cim_denied' {
        # the licensing class refused (an account without the right): said in errors, the rest still read
        $M.cim.Remove('SoftwareLicensingProduct')
    }
    default { throw "unknown scenario $Scenario" }
}
# ---- the stand-ins for the Windows commands ------------------------------------------------
function Get-CimInstance {
    param([string] $ClassName, [string] $Filter, $ErrorAction)
    $data = $M.cim[$ClassName]
    if ($null -eq $data) { throw "Access denied: $ClassName" }
    if ($Filter -match 'IPEnabled') { return @($data) | Where-Object { $_.IPEnabled } }
    @($data)
}
function Test-Path { param([string] $Path) return ($M.paths -contains $Path) }
function Get-ChildItem { param([string] $Path, $ErrorAction) if ($M.keys.ContainsKey($Path)) { return $M.keys[$Path] } ; throw "no key $Path" }
function Get-ItemProperty {
    param([string] $Path, [string] $Name, $ErrorAction)
    $v = $M.reg[$Path]
    if ($null -eq $v) { if ($ErrorAction -eq 'SilentlyContinue') { return } ; throw "no key $Path" }
    if ($Name -and $null -eq $v.$Name) { return }
    $v
}
function Get-Process { param([string[]] $Name, $ErrorAction) @($M.procs | Where-Object { $Name -contains $_.ProcessName }) }
# ---- run the script --------------------------------------------------------------------------
if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
& (Join-Path $PSScriptRoot '../../roles/horizon/files/horizon_image.ps1')
