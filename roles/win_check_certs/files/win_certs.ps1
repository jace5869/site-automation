# site-automation - Windows check: certs (roles/win_check_certs, run by playbooks/win_health_check.yml).
# Certificates in the machine stores (and which are in use), TLS endpoints: expired or expiring.
# With trust on (the certificate report turns it on): every other machine store too - Trusted Root,
# Intermediate CAs, Other People, ... (where "Automatically select the store" puts a .cer file).
# A certificate there with its private key is the server's own and checked like the others; one
# without is one the server trusts: listed in the report, never a finding.
# Remote Desktop's certificates are left out unless remote_desktop is on (win_check_certs_remote_desktop):
# the Remote Desktop store, the one RDP uses, and those for Remote Desktop only (Windows or the GPO renews them).
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
$notice = [int](Get-Setting 'notice_days' 60)
$ignore = @(Get-Setting 'ignore_subjects' @())
$trustOn = [bool](Get-Setting 'trust' $false)
$trustSkip = @(Get-Setting 'trust_skip_stores' @('Disallowed', 'AuthRoot', 'REQUEST', 'SMS'))
$rdpOn = [bool](Get-Setting 'remote_desktop' $false)
$now = Get-Date
# Which certificates are in use: https bindings (IIS, WSUS, ...), the WinRM HTTPS listener AAP
# connects through, and Remote Desktop (with remote_desktop on).
$bound = @{}
$rdpTp = ''
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
    if ($rdp.SSLCertificateSHA1Hash) { $rdpTp = ([string]$rdp.SSLCertificateSHA1Hash).ToUpper() }
}
catch { }
if ($rdpTp -and $rdpOn -and -not $bound.ContainsKey($rdpTp)) { $bound[$rdpTp] = 'Remote Desktop' }
# Remote Desktop Authentication: the extended key usage of the RDP certificate template
$rdpEku = '1.3.6.1.4.1.311.54.1.2'
$inv = @()
function Add-Cert([string] $Subject, [datetime] $NotAfter, [string] $Where, [string] $Id, [bool] $InUse, [string] $Hint, [bool] $Trust = $false, [string] $Store = '') {
    $days = ($NotAfter - $script:now).Days
    if ($NotAfter -lt $script:now -and $days -ge 0) { $days = -1 }
    $exp = $NotAfter.ToString('yyyy-MM-dd')
    if ($Trust) {
        # a certificate the server trusts: in the report when it expires soon or expired lately, never a finding
        # (Trusted Root holds dozens from Windows, some long expired)
        if ($days -le $script:notice -and (-$days) -le $script:expiredDays) { $script:inv += @(@{ subject = $Subject; expires = $exp; days = $days; where = $Where; trust = $true; id = $Id; store = $Store }) }
        return
    }
    # id, store: what the renewal (win_cert_csr.ps1) needs to find it again
    $script:inv += @(@{ subject = $Subject; expires = $exp; days = $days; where = $Where; trust = $false; id = $Id; store = $Store; in_use = $InUse })
    if ($NotAfter -lt $script:now) {
        if ($InUse -or (-$days) -le $script:expiredDays) { Add-Finding $Id 'critical' "certificate $Subject ($Where) EXPIRED on $exp" $Hint }
    }
    elseif ($days -le $script:crit) { Add-Finding $Id 'critical' "certificate $Subject ($Where) expires on $exp, in $days days" $Hint }
    elseif ($days -le $script:warn) { Add-Finding $Id 'warning' "certificate $Subject ($Where) expires on $exp, in $days days" $Hint }
}
$own = @(Get-Setting 'stores' @('Cert:\LocalMachine\My', 'Cert:\LocalMachine\WebHosting', 'Cert:\LocalMachine\Remote Desktop'))
if (-not $rdpOn) { $own = @($own | Where-Object { [string]$_ -notmatch '\\Remote Desktop$' }) }
$stores = @()
foreach ($st in $own) { $stores += @(@{ path = [string]$st; trust = $false }) }
if ($trustOn) {
    foreach ($s in @(Get-ChildItem -Path 'Cert:\LocalMachine' -ErrorAction SilentlyContinue)) {
        $p = 'Cert:\LocalMachine\' + [string]$s.Name
        if ($own -contains $p -or $trustSkip -contains [string]$s.Name) { continue }
        if (-not $rdpOn -and [string]$s.Name -eq 'Remote Desktop') { continue }
        $stores += @(@{ path = $p; trust = $true })
    }
}
# the names certlm.msc shows
$storeNames = @{ Root = 'Trusted Root Certification Authorities'; CA = 'Intermediate Certification Authorities'
    AddressBook = 'Other People'; TrustedPeople = 'Trusted People'; TrustedPublisher = 'Trusted Publishers' }
foreach ($st in $stores) {
    $certs = @()
    try { $certs = @(Get-ChildItem -Path $st.path -ErrorAction Stop) } catch { continue }
    $short = ($st.path -replace '^Cert:\\', '')
    $leaf = ($short -replace '^.*\\', '')
    if ($storeNames.ContainsKey($leaf)) { $short = $short + ' (' + $storeNames[$leaf] + ')' }
    foreach ($c in $certs) {
        if ($null -eq $c.NotAfter) { continue }
        $subj = [string]$c.Subject
        if (-not $subj) { $subj = [string]$c.FriendlyName }
        $skip = $false
        foreach ($p in $ignore) { if ($subj -like [string]$p) { $skip = $true } }
        if ($skip) { continue }
        $tp = ([string]$c.Thumbprint).ToUpper()
        if (-not $rdpOn -and -not $bound.ContainsKey($tp)) {
            # the one Remote Desktop uses, or one for Remote Desktop only: left out
            if ($tp -eq $rdpTp) { continue }
            $ekus = @($c.EnhancedKeyUsageList | ForEach-Object { [string]$_.ObjectId } | Where-Object { $_ })
            if ($ekus.Count -gt 0 -and @($ekus | Where-Object { $_ -ne $rdpEku }).Count -eq 0) { continue }
        }
        $where = $short
        $trust = $st.trust
        # in a trust store with its private key: the server's own certificate, put in the wrong store
        if ($trust -and $c.HasPrivateKey) { $trust = $false; $where = $where + ', with its private key' }
        $use = $false
        if ($bound.ContainsKey($tp)) { $where = $where + ', used by ' + $bound[$tp]; $use = $true; $trust = $false }
        if ($trust) { $where = $where + ' (trust store)' }
        Add-Cert $subj ([datetime]$c.NotAfter) $where "certs:$tp" $use "Get-ChildItem '$($st.path)\$tp' | Format-List Subject, Issuer, NotAfter, Thumbprint, HasPrivateKey; certlm.msc" $trust $st.path
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
