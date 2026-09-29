# site-automation - Windows check: performance (roles/win_check_performance, run by playbooks/win_health_check.yml).
# CPU (three samples), memory, page file; the busiest processes are named in the finding.
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

$cpuWarn = [int](Get-Setting 'cpu_warn_pct' 90)
$cpuCrit = [int](Get-Setting 'cpu_crit_pct' 98)
$memWarn = [int](Get-Setting 'mem_warn_pct' 90)
$memCrit = [int](Get-Setting 'mem_crit_pct' 97)
$pfWarn = [int](Get-Setting 'pagefile_warn_pct' 80)
# CPU: three samples, one second apart
$samples = @()
for ($i = 0; $i -lt 3; $i++) {
    $p = Get-CimInstance -ClassName Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
    $samples += @([int]$p.PercentProcessorTime)
    if ($i -lt 2) { Start-Sleep -Seconds 1 }
}
$cpu = [int](($samples | Measure-Object -Average).Average)
$ncpu = [int](Get-CimInstance -ClassName Win32_ComputerSystem).NumberOfLogicalProcessors
if ($ncpu -lt 1) { $ncpu = 1 }
$topCpu = @(Get-CimInstance -ClassName Win32_PerfFormattedData_PerfProc_Process |
    Where-Object { $_.Name -ne '_Total' -and $_.Name -ne 'Idle' } |
    Sort-Object -Property PercentProcessorTime -Descending | Select-Object -First 5 |
    ForEach-Object { '{0} {1}%' -f $_.Name, [int]($_.PercentProcessorTime / $ncpu) }) -join ', '
$os = Get-CimInstance -ClassName Win32_OperatingSystem
$total = [double]$os.TotalVisibleMemorySize * 1KB
$freeMem = [double]$os.FreePhysicalMemory * 1KB
$memPct = [int](100 * ($total - $freeMem) / $total)
$topMem = @(Get-Process | Sort-Object -Property WorkingSet64 -Descending | Select-Object -First 5 |
    ForEach-Object { '{0} {1:N1} GiB' -f $_.ProcessName, ($_.WorkingSet64 / 1GB) }) -join ', '
$hintCpu = 'Troubleshoot job with ts_area = performance; Get-Process | Sort-Object CPU -Descending | Select-Object -First 15'
$hintMem = 'Troubleshoot job with ts_area = performance; Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 15'
if ($cpu -ge $cpuCrit) { Add-Finding 'performance:cpu' 'critical' "CPU is $cpu% busy (critical at $cpuCrit%); busiest: $topCpu" $hintCpu }
elseif ($cpu -ge $cpuWarn) { Add-Finding 'performance:cpu' 'warning' "CPU is $cpu% busy (warning at $cpuWarn%); busiest: $topCpu" $hintCpu }
if ($memPct -ge $memCrit) { Add-Finding 'performance:memory' 'critical' "memory is $memPct% used (critical at $memCrit%); largest: $topMem" $hintMem }
elseif ($memPct -ge $memWarn) { Add-Finding 'performance:memory' 'warning' "memory is $memPct% used (warning at $memWarn%); largest: $topMem" $hintMem }
foreach ($f in @(Get-CimInstance -ClassName Win32_PageFileUsage)) {
    if ([int]$f.AllocatedBaseSize -le 0) { continue }
    $pp = [int](100 * [int]$f.CurrentUsage / [int]$f.AllocatedBaseSize)
    if ($pp -ge $pfWarn) {
        Add-Finding "performance:pagefile:$($f.Name)" 'warning' "page file $($f.Name) is $pp% used ($($f.CurrentUsage) of $($f.AllocatedBaseSize) MB; warning at $pfWarn%): the server is short of memory" $hintMem
    }
}
$facts.cpu_pct = $cpu
$facts.memory_pct = $memPct
$facts.top_cpu = $topCpu
$facts.top_memory = $topMem
$facts.last_boot = ([datetime]$os.LastBootUpTime).ToString('yyyy-MM-dd HH:mm')
$facts.uptime_days = [int]((Get-Date) - [datetime]$os.LastBootUpTime).TotalDays
Write-Result
