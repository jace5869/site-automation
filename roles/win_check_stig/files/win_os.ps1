# site-automation - Windows check: which Windows this is (roles/check_stig, answer_keys.yml, the STIG answer keys).
# Prints ONE line: ###SITE-JSON### {"findings": [], "facts": {"caption", "build", "domain_role"}} - the operating system's name,
# build number and domain role, from WMI. Reads only; changes nothing. Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$facts = @{ caption = ''; build = ''; domain_role = ''; product_type = '' }
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $facts.caption = [string]$os.Caption
    $facts.build = [string]$os.BuildNumber
    $facts.product_type = [string]$os.ProductType      # 1 a client (Windows 10 / 11), 2 a domain controller, 3 a server
} catch { }
try { $facts.domain_role = [string](Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).DomainRole } catch { }
'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ findings = @(); facts = $facts } -Depth 4 -Compress)
