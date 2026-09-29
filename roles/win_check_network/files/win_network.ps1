# site-automation - Windows check: network (roles/win_check_network, run by playbooks/win_health_check.yml).
# Default gateway, DNS servers, names that must resolve, TCP services that must answer, domain trust.
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

$timeout = [int](Get-Setting 'timeout_ms' 3000)
$cs = Get-CimInstance -ClassName Win32_ComputerSystem
$adapters = @(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=TRUE')
$gw = @($adapters | ForEach-Object { @($_.DefaultIPGateway) } | Where-Object { $_ })
$dns = @($adapters | ForEach-Object { @($_.DNSServerSearchOrder) } | Where-Object { $_ })
$facts.gateways = $gw
$facts.dns_servers = $dns
if ($gw.Count -eq 0) { Add-Finding 'network:gateway' 'warning' 'no default gateway on any network adapter' 'ipconfig /all; Get-NetRoute -DestinationPrefix 0.0.0.0/0' }
if ($dns.Count -eq 0) { Add-Finding 'network:dns-servers' 'critical' 'no DNS server is configured on any network adapter' 'ipconfig /all' }
# A command that cannot even load (a module blocked by the PowerShell policy) is not a network
# problem: it is reported as "could not test", not as "unreachable".
function Test-CannotRun($Err) { return ([string]$Err.FullyQualifiedErrorId -match 'CommandNotFound|CouldNotAutoload') }
foreach ($n in @(Get-Setting 'dns_names' @())) {
    try { $null = Resolve-DnsName -Name ([string]$n) -DnsOnly -QuickTimeout -ErrorAction Stop }
    catch {
        if (Test-CannotRun $_) { Add-Finding "network:dns:$n" 'warning' "could not test whether $n resolves: $($_.Exception.Message)" 'Resolve-DnsName; nslookup' }
        else { Add-Finding "network:dns:$n" 'critical' "the name $n does not resolve ($($_.Exception.Message))" "Resolve-DnsName $n; ipconfig /all" }
    }
}
# The computer account's trust with the domain ("the trust relationship ... failed" at logon).
if ($cs.PartOfDomain -and [int]$cs.DomainRole -lt 4 -and (Get-Setting 'secure_channel' $true)) {
    $out = @(& nltest.exe "/sc_query:$($cs.Domain)" 2>&1 | ForEach-Object { [string]$_ })
    $text = ($out -join ' ')
    $facts.domain = [string]$cs.Domain
    if ($text -notmatch 'NERR_Success') {
        $why = @($out | Where-Object { $_ -match 'Status|ERROR|error' }) -join ' '
        if (-not $why) { $why = $text }
        Add-Finding 'network:secure-channel' 'critical' ("the trust with the domain {0} is broken, or no domain controller answers: {1}" -f $cs.Domain, $why.Trim()) "nltest /sc_query:$($cs.Domain); Test-ComputerSecureChannel -Verbose"
    }
}
foreach ($t in @(Get-Setting 'tcp' @())) {
    $h = [string]$t.host
    $p = [int]$t.port
    $nm = [string]$t.name
    if (-not $nm) { $nm = "${h}:$p" }
    $ok = $false
    $err = ''
    if ($fullLanguage) {
        $c = New-Object System.Net.Sockets.TcpClient
        try {
            $a = $c.BeginConnect($h, $p, $null, $null)
            if ($a.AsyncWaitHandle.WaitOne($timeout)) { $c.EndConnect($a); $ok = $true }
            else { $err = "no answer within $timeout ms" }
        }
        catch {
            $err = $_.Exception.Message
            if ($_.Exception.InnerException) { $err = $_.Exception.InnerException.Message }
        }
        finally { $c.Close() }
    }
    else {
        try {
            $ok = [bool](Test-NetConnection -ComputerName $h -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue -ErrorAction Stop)
            if (-not $ok) { $err = 'no answer' }
        }
        catch {
            if (Test-CannotRun $_) {
                Add-Finding "network:tcp:${h}:$p" 'warning' "could not test $nm ($h port $p): $($_.Exception.Message)" "Test-NetConnection $h -Port $p"
                continue
            }
            $err = $_.Exception.Message
        }
    }
    if (-not $ok) { Add-Finding "network:tcp:${h}:$p" 'critical' "cannot reach $nm ($h port $p): $err" "Test-NetConnection $h -Port $p; Get-NetRoute; Resolve-DnsName $h" }
}
Write-Result
