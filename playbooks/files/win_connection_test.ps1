# site-automation - Windows connection test (playbooks/win_connection_test.yml).
# Tells what the Windows runbooks need to know about this server. Read-only.
# Each value is read on its own, so one that cannot be read does not hide the others.
$ErrorActionPreference = 'Continue'
$os = $null
try { $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop } catch { }
$cs = $null
try { $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop } catch { }
$groups = ''
try { $groups = (@(& whoami.exe /groups 2>$null) -join ' ') } catch { }
$user = ''
try { $user = [string](& whoami.exe 2>$null) } catch { }
$r = @{
    powershell    = [string]$PSVersionTable.PSVersion
    language_mode = [string]$ExecutionContext.SessionState.LanguageMode
    os            = [string]$os.Caption
    build         = [string]$os.BuildNumber
    computer      = [string]$cs.Name
    domain        = [string]$cs.Domain
    domain_role   = [int]$cs.DomainRole
    user          = $user
    local_admin   = [bool]($groups -match 'S-1-5-32-544')
}
'###SITE-JSON### ' + (ConvertTo-Json -InputObject $r -Compress)
