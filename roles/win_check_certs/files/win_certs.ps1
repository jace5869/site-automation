# site-automation - Windows check: certs (roles/win_check_certs, run by playbooks/win_health_check.yml).
# Certificates in the machine stores (and which are in use), TLS endpoints: expired or expiring.
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

$warn = [int](Get-Setting 'warn_days' 30)
$crit = [int](Get-Setting 'crit_days' 7)
$expiredDays = [int](Get-Setting 'expired_report_days' 30)
$ignore = @(Get-Setting 'ignore_subjects' @())
$now = Get-Date
# Which certificates are in use: https bindings (IIS, WSUS, ...), the WinRM HTTPS listener AAP
# connects through, and Remote Desktop.
$bound = @{}
$ipport = ''
foreach ($l in @(& netsh.exe http show sslcert 2>&1 | ForEach-Object { [string]$_ })) {
    if ($l -match '^\s*(IP:port|Hostname:port|Central Certificate Store)\s*:\s*(.+?)\s*$') { $ipport = $Matches[2] }
    elseif ($l -match '^\s*Certificate Hash\s*:\s*([0-9a-fA-F]{40})') { $bound[$Matches[1].ToUpper()] = "https $ipport" }
}
foreach ($k in @(Get-ChildItem -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Listener' -ErrorAction SilentlyContinue)) {
    $v = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
    if ($null -ne $v -and $v.certThumbprint) { $bound[([string]$v.certThumbprint).Trim().ToUpper()] = 'WinRM HTTPS listener: AAP connects through it' }
}
try {
    $rdp = Get-CimInstance -Namespace 'root\cimv2\TerminalServices' -ClassName Win32_TSGeneralSetting -Filter "TerminalName='RDP-Tcp'" -ErrorAction Stop
    if ($rdp.SSLCertificateSHA1Hash) { $bound[([string]$rdp.SSLCertificateSHA1Hash).ToUpper()] = 'Remote Desktop' }
}
catch { }
$inv = @()
function Add-Cert([string] $Subject, [datetime] $NotAfter, [string] $Where, [string] $Id, [bool] $InUse, [string] $Hint) {
    $days = ($NotAfter - $script:now).Days
    if ($NotAfter -lt $script:now -and $days -ge 0) { $days = -1 }
    $exp = $NotAfter.ToString('yyyy-MM-dd')
    $script:inv += @(@{ subject = $Subject; expires = $exp; days = $days; where = $Where })
    if ($NotAfter -lt $script:now) {
        if ($InUse -or (-$days) -le $script:expiredDays) { Add-Finding $Id 'critical' "certificate $Subject ($Where) EXPIRED on $exp" $Hint }
    }
    elseif ($days -le $script:crit) { Add-Finding $Id 'critical' "certificate $Subject ($Where) expires on $exp, in $days days" $Hint }
    elseif ($days -le $script:warn) { Add-Finding $Id 'warning' "certificate $Subject ($Where) expires on $exp, in $days days" $Hint }
}
foreach ($st in @(Get-Setting 'stores' @('Cert:\LocalMachine\My', 'Cert:\LocalMachine\WebHosting', 'Cert:\LocalMachine\Remote Desktop'))) {
    $certs = @()
    try { $certs = @(Get-ChildItem -Path ([string]$st) -ErrorAction Stop) } catch { continue }
    foreach ($c in $certs) {
        if ($null -eq $c.NotAfter) { continue }
        $subj = [string]$c.Subject
        if (-not $subj) { $subj = [string]$c.FriendlyName }
        $skip = $false
        foreach ($p in $ignore) { if ($subj -like [string]$p) { $skip = $true } }
        if ($skip) { continue }
        $tp = ([string]$c.Thumbprint).ToUpper()
        $where = ([string]$st -replace '^Cert:\\', '')
        $use = $false
        if ($bound.ContainsKey($tp)) { $where = $where + ', used by ' + $bound[$tp]; $use = $true }
        Add-Cert $subj ([datetime]$c.NotAfter) $where "certs:$tp" $use "Get-ChildItem $st\$tp | Format-List Subject, Issuer, NotAfter, Thumbprint; certlm.msc"
    }
}
# TLS endpoints: the certificate a client actually gets (needs full .NET, not Constrained Language)
$endpoints = @(Get-Setting 'endpoints' @())
if ($endpoints.Count -gt 0 -and -not $fullLanguage) {
    Add-Finding 'certs:endpoints' 'warning' 'TLS endpoints were not checked: PowerShell runs in Constrained Language Mode on this server' 'the certificate stores were checked; see win_check_certs_endpoints in docs/WINDOWS.md'
}
elseif ($endpoints.Count -gt 0) {
    $cb = [System.Net.Security.RemoteCertificateValidationCallback] { param($s, $c, $ch, $e) $true }
    foreach ($e in $endpoints) {
        $h = [string]$e.host
        $p = [int]$e.port
        $name = [string]$e.name
        if (-not $name) { $name = "${h}:$p" }
        $sn = [string]$e.servername
        if (-not $sn) { $sn = $h }
        $tcp = New-Object System.Net.Sockets.TcpClient
        try {
            $a = $tcp.BeginConnect($h, $p, $null, $null)
            if (-not $a.AsyncWaitHandle.WaitOne(5000)) { throw "no answer within 5 s" }
            $tcp.EndConnect($a)
            $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, $cb)
            $ssl.AuthenticateAsClient($sn)
            $x = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
            Add-Cert ([string]$x.Subject) $x.NotAfter "TLS $name" "certs:tls:${h}:$p" $true "Test-NetConnection $h -Port $p"
            $ssl.Dispose()
        }
        catch { Add-Finding "certs:tls:${h}:$p" 'warning' "could not read the certificate of $name (${h}:$p): $($_.Exception.Message)" "Test-NetConnection $h -Port $p" }
        finally { $tcp.Close() }
    }
}
$facts.inventory = $inv
Write-Result
