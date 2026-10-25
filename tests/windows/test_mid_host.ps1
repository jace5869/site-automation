# Test for the ServiceNow MID Server host check (roles/servicenow/files/mid_host.ps1) with made-up services (a
# stand-in Get-CimInstance) and an agent folder in a temporary folder - normal and in Constrained Language Mode.
# Windows PowerShell 5.1 and PowerShell 7, on Windows and on Linux:
#   pwsh -File tests/windows/test_mid_host.ps1          powershell -File tests\windows\test_mid_host.ps1
param([switch] $Inner, [switch] $Clm, [string] $Dir)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$script = Join-Path $root 'roles/servicenow/files/mid_host.ps1'

if ($Inner) {
    # ---- one run of the check against the made-up services ----
    $agent = Join-Path $Dir 'mid01\agent'
    $global:testServices = @(
        [pscustomobject]@{ Name = 'snc_mid_mid01'; DisplayName = 'ServiceNow MID Server_mid01'; State = 'Running'; StartMode = 'Auto'
            PathName = ('"' + (Join-Path $agent 'bin\wrapper-windows-x86-64.exe') + '" -s "' + (Join-Path $agent 'conf\wrapper.conf') + '"') },
        [pscustomobject]@{ Name = 'other_mid'; DisplayName = 'ServiceNow MID Server_mid02'; State = 'Stopped'; StartMode = 'Manual'
            PathName = 'C:\nowhere\agent\bin\wrapper.exe' },
        [pscustomobject]@{ Name = 'Spooler'; DisplayName = 'Print Spooler'; State = 'Running'; StartMode = 'Auto'; PathName = 'spoolsv.exe' })
    function Get-CimInstance {
        param($ClassName, $Filter)
        if ($ClassName -eq 'Win32_Service') { return $global:testServices }
        if ($ClassName -eq 'Win32_LogicalDisk') { return [pscustomobject]@{ DeviceID = 'C:'; Size = 100GB; FreeSpace = 8GB } }
    }
    $env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject @{ service_match = 'snc_mid*'; hours = 24 } -Compress
    if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
    & $script
    return
}

# ---- the test: make the agent folder, run the check twice (normal, constrained), check what it read ----
$shell = (Get-Process -Id $PID).Path
$dir = Join-Path ([System.IO.Path]::GetTempPath()) ('sa-mid-' + (Get-Random))
$logs = Join-Path $dir 'mid01\agent\logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
Set-Content -LiteralPath (Join-Path $dir 'mid01\agent\config.xml') -Value @(
    '<?xml version="1.0" encoding="UTF-8"?>', '<parameters>',
    '  <parameter name="url" value="https://dev.example.mil/"/>', '  <parameter name="name" value="mid01"/>', '</parameters>')
$recent = (Get-Date).AddMinutes(-30).ToString('MM/dd/yy HH:mm:ss')
$old = (Get-Date).AddDays(-3).ToString('MM/dd/yy HH:mm:ss')
Set-Content -LiteralPath (Join-Path $logs 'agent0.log.0') -Value @(
    "$old (1) Worker-Standard SEVERE *** ERROR *** too old to count",
    "$recent (2) Worker-Standard SEVERE *** ERROR *** connection refused",
    "$recent (3) Worker-Standard INFO all fine",
    "$recent (4) ECCSender.1 SEVERE could not send")
$wrecent = (Get-Date).AddMinutes(-20).ToString('yyyy/MM/dd HH:mm:ss')
Set-Content -LiteralPath (Join-Path $logs 'wrapper.log') -Value @(
    "STATUS | wrapper  | $wrecent | JVM appears hung: Timed out waiting for signal from JVM.",
    "STATUS | wrapper  | $wrecent | Launching a JVM...")

$fails = 0
function Check([bool] $Ok, [string] $What) {
    if ($Ok) { "ok   - $What" } else { "FAIL - $What"; $script:fails++ }
}
foreach ($mode in @('normal', 'constrained')) {
    $innerArgs = @('-NoProfile', '-File', $PSCommandPath, '-Inner', '-Dir', $dir)
    if ($mode -eq 'constrained') { $innerArgs += '-Clm' }
    $out = @(& $shell @innerArgs 2>&1 | ForEach-Object { [string]$_ })
    $line = @($out | Where-Object { $_.StartsWith('###SITE-JSON### ') }) | Select-Object -Last 1
    if (-not $line) { Check $false "mid_host ($mode): no result line - $($out -join ' | ')"; continue }
    $r = ConvertFrom-Json -InputObject $line.Substring(16)
    $mids = @($r.facts.mids)
    Check ($mids.Count -eq 2) "mid_host ($mode): the two MID services found by name and by display name, not the spooler"
    $a = $mids | Where-Object { $_.service -eq 'snc_mid_mid01' }
    Check ($a.state -eq 'Running' -and $a.start_mode -eq 'Auto') "mid_host ($mode): state and start mode"
    Check ($a.agent_dir -like '*mid01*agent' -and $a.config -and $a.name -eq 'mid01' -and $a.url -eq 'https://dev.example.mil/') "mid_host ($mode): the agent folder from the service, config.xml's name and URL"
    Check ($a.log.exists -and $a.log.count -eq 2 -and $a.log.age_min -lt 5 -and @($a.log.last)[-1] -like '*could not send*') "mid_host ($mode): 2 SEVERE lines in the window (the old one left out), the last one kept"
    Check ($a.wrapper.count -eq 1 -and @($a.wrapper.last)[0] -like '*JVM appears hung*') "mid_host ($mode): one JVM hang in the wrapper log"
    $b = $mids | Where-Object { $_.service -eq 'other_mid' }
    Check ($b.state -eq 'Stopped' -and -not $b.config) "mid_host ($mode): the second one stopped, no config.xml"
    if ($a.agent_dir -match '^[A-Za-z]:') { Check ($a.disk.free_pct -eq 8) "mid_host ($mode): the agent drive's free space" }
}
Remove-Item -LiteralPath $dir -Recurse -Force
if ($fails) { "$fails FAILED"; exit 1 } else { 'ALL PASSED' }
