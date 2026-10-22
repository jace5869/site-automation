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
foreach ($l in @((Invoke-Native 'netsh.exe' @('http', 'show', 'sslcert')).Out)) {
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
# ---- what else a certificate says (the inventory, the export): read from its bytes - no .NET methods,
# so Constrained Language too
$script:at = 0
function Read-Len($b) {
    $l = [int]$b[$script:at]; $script:at++
    if ($l -ge 0x80) {
        $n = $l - 0x80; $l = 0
        for ($k = 0; $k -lt $n; $k++) { $l = $l * 256 + [int]$b[$script:at]; $script:at++ }
    }
    return $l
}
function Get-Ext($c, [string] $Oid) {
    foreach ($e in @($c.Extensions)) { if ($null -ne $e -and [string]$e.Oid.Value -eq $Oid) { return , @($e.RawData) } }
    return $null
}
# subjectAltName as openssl writes it: DNS:..., IP:..., email:..., URI:...
function Get-SanList($c) {
    $out = @()
    $b = Get-Ext $c '2.5.29.17'
    if ($null -eq $b -or $b.Count -lt 2 -or [int]$b[0] -ne 0x30) { return , $out }
    $script:at = 1
    $len = Read-Len $b
    $end = $script:at + $len
    while ($script:at -lt $end) {
        $tag = [int]$b[$script:at]; $script:at++
        $l = Read-Len $b
        $v = @()
        if ($l -gt 0) { $v = @($b[$script:at..($script:at + $l - 1)]) }
        $script:at += $l
        $text = -join @($v | ForEach-Object { [string][char][int]$_ })
        if ($tag -eq 0x82) { $out += @('DNS:' + $text) }
        elseif ($tag -eq 0x81) { $out += @('email:' + $text) }
        elseif ($tag -eq 0x86) { $out += @('URI:' + $text) }
        elseif ($tag -eq 0x87 -and $l -eq 4) { $out += @('IP:' + (@($v | ForEach-Object { [string][int]$_ }) -join '.')) }
        elseif ($tag -eq 0x87 -and $l -eq 16) {
            $g = @(); for ($k = 0; $k -lt 16; $k += 2) { $g += @('{0:x}' -f ([int]$v[$k] * 256 + [int]$v[$k + 1])) }
            $out += @('IP:' + ($g -join ':'))
        }
        elseif ($tag -eq 0xA0) { $out += @('othername') }
        elseif ($tag -eq 0xA4) { $out += @('DirName') }
    }
    return , $out
}
function Get-Meta($c) {
    $issuer = [string]$c.Issuer
    $subject = [string]$c.Subject
    $bc = Get-Ext $c '2.5.29.19'
    # basicConstraints: SEQUENCE { BOOLEAN cA TRUE ... }
    $ca = ($null -ne $bc -and $bc.Count -ge 5 -and [int]$bc[2] -eq 1 -and [int]$bc[3] -eq 1 -and [int]$bc[4] -ne 0)
    $self = ($issuer -ne '' -and $issuer -eq $subject)
    # keyUsage: BIT STRING, keyCertSign = 0x04 of its first byte
    $ku = Get-Ext $c '2.5.29.15'
    $signs = ($null -ne $ku -and $ku.Count -ge 4 -and ([int]$ku[3] -band 0x04) -ne 0)
    $key = ''
    if ($null -ne $c.PublicKey) {
        if ([string]$c.PublicKey.Oid.Value -eq '1.2.840.10045.2.1') {
            $hex = -join @(@($c.PublicKey.EncodedParameters.RawData) | ForEach-Object { '{0:X2}' -f [int]$_ })
            $key = 'EC P-256'
            if ($hex -eq '06052B81040022') { $key = 'EC P-384' } elseif ($hex -eq '06052B81040023') { $key = 'EC P-521' }
        }
        elseif ([string]$c.PublicKey.Oid.Value -eq '1.2.840.113549.1.1.1') {
            # the size from the key's length (a modulus of n bits is about n/8 bytes)
            $n = @($c.PublicKey.EncodedKeyValue.RawData).Count
            $bits = 1024
            if ($n -gt 1000) { $bits = 8192 } elseif ($n -gt 500) { $bits = 4096 } elseif ($n -gt 380) { $bits = 3072 } elseif ($n -gt 200) { $bits = 2048 }
            $key = "RSA $bits"
        }
        else { $key = [string]$c.PublicKey.Oid.FriendlyName }
    }
    $sha256 = ''
    $raw = @($c.RawData)
    if ($raw.Count -gt 0 -and $null -ne $raw[0]) {
        # SHA-256 of the certificate: Get-FileHash of its bytes in a temporary file (no .NET hashing in Constrained Language)
        $tmpDir = $env:TEMP
        if (-not $tmpDir) { $tmpDir = [string](Get-Location) }
        $f = Join-Path $tmpDir ('sa-cert-' + [string]$c.Thumbprint + '.cer')
        try {
            if ($PSVersionTable.PSVersion.Major -ge 6) { Set-Content -LiteralPath $f -Value ([byte[]]$raw) -AsByteStream }
            else { Set-Content -LiteralPath $f -Value ([byte[]]$raw) -Encoding Byte }
            $sha256 = [string](Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash
        }
        catch { }
        finally { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
    }
    # root: a self-signed CA that signs certificates (or one in a trust store, Add-Cert) - not a server's own self-signed one
    $tier = 'ca-signed'
    if ($ca -and $self -and $signs) { $tier = 'root' } elseif ($self) { $tier = 'self-signed' } elseif ($ca) { $tier = 'intermediate' }
    if ($issuer -eq '') { $tier = '' }
    return @{ issuer = $issuer; serial = ([string]$c.SerialNumber).ToUpper(); sha1 = ([string]$c.Thumbprint).ToUpper(); sha256 = $sha256.ToUpper()
        key = $key; sig_alg = [string]$c.SignatureAlgorithm.FriendlyName; ca = $ca; signs = $signs; self_signed = $self; sans = (Get-SanList $c); tier = $tier }
}
function Add-Cert([string] $Subject, [datetime] $NotAfter, [string] $Where, [string] $Id, [bool] $InUse, [string] $Hint, [bool] $Trust = $false, [string] $Store = '', $Cert = $null) {
    $days = ($NotAfter - $script:now).Days
    if ($NotAfter -lt $script:now -and $days -ge 0) { $days = -1 }
    $exp = $NotAfter.ToString('yyyy-MM-dd')
    if ($Trust) {
        # a certificate the server trusts: in the report when it expires soon or expired lately, never a finding
        # (Trusted Root holds dozens from Windows, some long expired)
        if ($days -le $script:notice -and (-$days) -le $script:expiredDays) {
            $e = @{ subject = $Subject; expires = $exp; days = $days; where = $Where; trust = $true; id = $Id; store = $Store }
            if ($null -ne $Cert) {
                $m = Get-Meta $Cert; foreach ($k in $m.Keys) { $e[$k] = $m[$k] }
                if ($e.ca -and $e.self_signed) { $e.tier = 'root' }      # a self-signed CA the server trusts
            }
            $script:inv += @($e)
        }
        return
    }
    # id, store: what the renewal (win_cert_csr.ps1) needs to find it again
    $e = @{ subject = $Subject; expires = $exp; days = $days; where = $Where; trust = $false; id = $Id; store = $Store; in_use = $InUse }
    if ($null -ne $Cert) { $m = Get-Meta $Cert; foreach ($k in $m.Keys) { $e[$k] = $m[$k] } }
    $script:inv += @($e)
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
        Add-Cert $subj ([datetime]$c.NotAfter) $where "certs:$tp" $use "Get-ChildItem '$($st.path)\$tp' | Format-List Subject, Issuer, NotAfter, Thumbprint, HasPrivateKey; certlm.msc" $trust $st.path $c
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
            Add-Cert ([string]$x.Subject) $x.NotAfter "TLS $name" "certs:tls:${h}:$p" $true "Test-NetConnection $h -Port $p" $false '' $x
            $ssl.Dispose()
        }
        catch { Add-Finding "certs:tls:${h}:$p" 'warning' "could not read the certificate of $name (${h}:$p): $($_.Exception.Message)" "Test-NetConnection $h -Port $p" }
        finally { $tcp.Close() }
    }
}
$facts.inventory = $inv
Write-Result
