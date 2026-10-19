# site-automation - Windows certificate renewal: a new key and a CSR (roles/win_check_certs/tasks/csr.yml,
# run by playbooks/win_cert_renew_csr.yml). For each certificate given (its store and thumbprint, from the
# certs check): a new key in the machine's key store and a certificate signing request (CSR) with the
# same subject, names (subjectAltName) and purposes, made by certreq -new -machine. The new key stays on
# the server, in the pending request (certlm.msc > Certificate Enrollment Requests), until the issued
# certificate is installed with certreq -accept -machine, which puts the two together in Personal.
# A run again finds the pending request and reuses its CSR. Nothing in use is changed.
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

$dir = [string](Get-Setting 'dir' 'C:\ProgramData\site-automation\renew')
$keyBits = [int](Get-Setting 'key_bits' 2048)
$digest = [string](Get-Setting 'digest' 'sha256')
$max = [int](Get-Setting 'max' 10)
$selfSigned = [bool](Get-Setting 'self_signed' $false)
$issuerRe = [string](Get-Setting 'issuer' '')
$excludeIssuerRe = [string](Get-Setting 'exclude_issuer' '')
$onlyRe = [string](Get-Setting 'only' '')
$exportable = [bool](Get-Setting 'exportable' $false)
$fileExt = ([string](Get-Setting 'file_ext' 'csr')).TrimStart('.')
$checkMode = [bool](Get-Setting 'check_mode' $false)
$job = [string](Get-Setting 'job' '')
$results = @()
$made = 0

# ---- reading the certificate's extensions from their bytes (no .NET methods: Constrained Language too)
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
# subjectAltName -> certreq's form (dns=..., ipaddress=..., email=..., url=...); a kind it cannot write: $script:sanBad
function Get-Sans($c) {
    $script:sanBad = ''
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
        if ($tag -eq 0x82) { $out += @('dns=' + $text) }
        elseif ($tag -eq 0x81) { $out += @('email=' + $text) }
        elseif ($tag -eq 0x86) { $out += @('url=' + $text) }
        elseif ($tag -eq 0x87 -and $l -eq 4) { $out += @('ipaddress=' + (@($v | ForEach-Object { [string][int]$_ }) -join '.')) }
        elseif ($tag -eq 0x87 -and $l -eq 16) {
            $g = @(); for ($k = 0; $k -lt 16; $k += 2) { $g += @('{0:x}' -f ([int]$v[$k] * 256 + [int]$v[$k + 1])) }
            $out += @('ipaddress=' + ($g -join ':'))
        }
        elseif ($tag -eq 0xA0) { $script:sanBad = 'an otherName (a UPN or a GUID)' }
        elseif ($tag -eq 0xA4) { $script:sanBad = 'a directory name' }
        else { $script:sanBad = ('a name of kind 0x{0:X2}' -f $tag) }
    }
    return , $out
}
function Test-Ca($c) {
    $b = Get-Ext $c '2.5.29.19'
    # SEQUENCE { BOOLEAN cA TRUE ... }
    return ($null -ne $b -and $b.Count -ge 5 -and [int]$b[2] -eq 1 -and [int]$b[3] -eq 1 -and [int]$b[4] -ne 0)
}
function Get-Key($c) {
    # RSA: its size from the key's length (a bigger old key keeps its size); EC: the same curve
    if ([string]$c.PublicKey.Oid.Value -eq '1.2.840.10045.2.1') {
        $hex = -join @(@($c.PublicKey.EncodedParameters.RawData) | ForEach-Object { '{0:X2}' -f [int]$_ })
        if ($hex -eq '06052B81040022') { return @{ alg = 'ECDSA_P384'; bits = 384; text = 'EC P-384'; usage = '0x80' } }
        if ($hex -eq '06052B81040023') { return @{ alg = 'ECDSA_P521'; bits = 521; text = 'EC P-521'; usage = '0x80' } }
        return @{ alg = 'ECDSA_P256'; bits = 256; text = 'EC P-256'; usage = '0x80' }
    }
    $n = @($c.PublicKey.EncodedKeyValue.RawData).Count
    $old = 2048
    if ($n -gt 1000) { $old = 8192 } elseif ($n -gt 500) { $old = 4096 } elseif ($n -gt 380) { $old = 3072 }
    $bits = $script:keyBits
    if ($old -gt $bits) { $bits = $old }
    return @{ alg = 'RSA'; bits = $bits; text = "RSA $bits-bit"; usage = '0xa0' }
}
function Get-Eku($c) {
    $out = @()
    try { foreach ($u in @($c.EnhancedKeyUsageList)) { if ($u -and $u.ObjectId) { $out += @([string]$u.ObjectId) } } } catch { }
    if (-not $out.Count) { $out = @('1.3.6.1.5.5.7.3.1') }      # server authentication
    return , $out
}
function Get-Cn([string] $Subject) {
    if ($Subject -match '(^|,\s*)CN=("([^"]*)"|[^,]*)') { if ($Matches[3]) { return $Matches[3] } ; return $Matches[2].Trim() }
    return $Subject
}
function Get-Safe([string] $Text) {
    $t = ($Text -replace '[^A-Za-z0-9._-]+', '_').Trim('._')
    if ($t.Length -gt 60) { $t = $t.Substring(0, 60) }
    if (-not $t) { $t = 'certificate' }
    return $t
}
function Get-Install([string] $Where) {
    $u = 'certreq -accept -machine <the issued certificate>.cer (on the server, as an administrator: it pairs it with the new key and puts it in LocalMachine\My). Then use it where the old one is: '
    if ($Where -match 'used by https (.+)$') {
        return $u + "the https binding $($Matches[1]) (IIS Manager > the site > Bindings > the certificate; or netsh http update sslcert ipport=$($Matches[1]) certhash=<new thumbprint> appid=<the same appid>). Then remove the old certificate."
    }
    if ($Where -match 'WinRM HTTPS listener') {
        return $u + "the WinRM HTTPS listener AAP connects through: Set-Item -Path WSMan:\localhost\Listener\<the HTTPS listener>\CertificateThumbprint -Value <new thumbprint> (winrm enumerate winrm/config/listener shows it). Test the AAP connection, then remove the old certificate."
    }
    if ($Where -match 'Remote Desktop') {
        return $u + "Remote Desktop: Set-CimInstance or wmic /namespace:\\root\cimv2\TerminalServices PATH Win32_TSGeneralSetting Set SSLCertificateSHA1Hash=<new thumbprint> (or the GPO that sets it). Then remove the old certificate."
    }
    return $u + 'the program that uses it (it may pick a certificate by its subject or thumbprint). Then remove the old certificate.'
}

foreach ($cand in @(Get-Setting 'certs' @())) {
    $where = [string]$cand.where
    $id = [string]$cand.id
    $r = @{ wheres = @($where); ids = @($id); subject = [string]$cand.subject; issuer = ''; expires = [string]$cand.expires
        days = [int]$cand.days; severity = [string]$cand.severity; status = ''; reason = '' }
    $tp = ($id -replace '^certs:', '').ToUpper()
    $store = [string]$cand.store
    if (-not $store -or $tp -notmatch '^[0-9A-F]{40}$') {
        $r.status = 'skipped'
        $r.reason = 'served on a port only (win_check_certs_endpoints): its certificate store is not known - find the certificate in certlm.msc; the one in its store gets a CSR'
        $results += @($r); continue
    }
    $c = $null
    try { $c = Get-ChildItem -Path "$store\$tp" -ErrorAction Stop } catch { }
    if ($null -eq $c) {
        $r.status = 'skipped'; $r.reason = "no longer in $store (replaced or removed since the check)"
        $results += @($r); continue
    }
    $subject = [string]$c.Subject
    $issuer = [string]$c.Issuer
    $r.subject = $subject; $r.issuer = $issuer
    $self = ($subject -eq $issuer)
    $sans = Get-Sans $c
    $names = @($sans | ForEach-Object { ($_ -split '=', 2)[1] })
    $real = @($names + @(Get-Cn $subject) | Where-Object { $_ -and $_ -notmatch '^(localhost(\..*)?|127\.\d+\.\d+\.\d+|::1|0:0:0:0:0:0:0:1)$' })
    if ($onlyRe -and $where -notmatch $onlyRe) { $r.status = 'skipped'; $r.reason = 'its store is not in win_cert_renew_only' }
    elseif (Test-Ca $c) { $r.status = 'skipped'; $r.reason = 'a CA certificate: replace it with the CA''s new certificate from the PKI - there is no CSR for it' }
    elseif ($self -and -not $selfSigned) { $r.status = 'skipped'; $r.reason = 'self-signed (made on the server - by its application or a person - not by a PKI): no CSR (win_cert_renew_self_signed: true makes one, for the PKI to issue a real certificate in its place)' }
    elseif ($self -and -not $real.Count) { $r.status = 'skipped'; $r.reason = 'self-signed, and only for localhost: a PKI issues no certificate for it' }
    elseif ($excludeIssuerRe -and $issuer -match $excludeIssuerRe) { $r.status = 'skipped'; $r.reason = "issued by $issuer - in win_cert_renew_exclude_issuer (renewed by its own tool)" }
    elseif ($issuerRe -and -not $self -and $issuer -notmatch $issuerRe) { $r.status = 'skipped'; $r.reason = "issued by $issuer - not win_cert_renew_issuer" }
    elseif ($script:sanBad) { $r.status = 'failed'; $r.reason = "its names (subjectAltName) hold $($script:sanBad), which certreq cannot copy from here: make the CSR by hand (certlm.msc > the certificate > All Tasks > Advanced Operations > Renew This Certificate with the Same Key, or a new request)" }
    if ($r.status) { $results += @($r); continue }
    # a newer one for the same subject is there already: installed, not yet put to use
    $newer = @(Get-ChildItem -Path $store -ErrorAction SilentlyContinue | Where-Object { $_.Subject -eq $subject -and $_.Thumbprint -ne $c.Thumbprint -and $_.NotAfter -gt $c.NotAfter -and $_.HasPrivateKey })
    if ($newer.Count) {
        $n = $newer[0]
        $r.status = 'skipped'
        $r.reason = "a newer certificate for the same subject is in $store already (thumbprint $($n.Thumbprint), expires $(([datetime]$n.NotAfter).ToString('yyyy-MM-dd'))): use it where the old one is used, then remove the old one - no CSR"
        $results += @($r); continue
    }
    $key = Get-Key $c
    $eku = Get-Eku $c
    $short = Get-Safe (Get-Cn $subject)
    $r.sans = @($names)
    $r.key = $key.text
    $r.self_signed = $self
    $r.key_path = 'the new key: in the server''s machine key store, with the pending request (certlm.msc > Certificate Enrollment Requests)'
    $r.install = Get-Install $where
    $info = Join-Path $dir "$tp.json"
    $r.file_name = "$short.$fileExt"
    $r.csr_path = Join-Path $dir "$short-$($tp.Substring(0, 8)).csr"
    # the pending request of an earlier run: its CSR again (the key is still in the REQUEST store)
    $pending = $null
    if (Test-Path -LiteralPath $info) {
        try {
            $pending = Get-Content -LiteralPath $info -Raw | ConvertFrom-Json
            if (-not ((Test-Path -LiteralPath ([string]$pending.csr_path)) -and (Test-Path -LiteralPath ("Cert:\LocalMachine\REQUEST\" + [string]$pending.request)))) { $pending = $null }
        }
        catch { $pending = $null }
    }
    if ($pending) { $r.status = 'exists'; $r.csr_path = [string]$pending.csr_path }
    elseif ($made -ge $max) { $r.status = 'skipped'; $r.reason = "more than win_cert_renew_max ($max) new CSRs in this run: the next run makes it" }
    elseif ($checkMode) { $r.status = 'would make'; $made++ }
    else {
        try {
            if (-not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
                # administrators and SYSTEM only (the CSRs are public; the folder is kept tidy)
                $null = & icacls.exe $dir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' 2>&1
            }
            $inf = $r.csr_path -replace '\.csr$', '.inf'
            $lines = @('[Version]', 'Signature="$Windows NT$"', '', '[NewRequest]',
                ('Subject = "' + ($subject -replace '"', '""') + '"'),
                'MachineKeySet = TRUE', ('KeyAlgorithm = ' + $key.alg), ('KeyLength = ' + $key.bits),
                'ProviderName = "Microsoft Software Key Storage Provider"', ('HashAlgorithm = ' + $digest),
                'RequestType = PKCS10', ('Exportable = ' + $(if ($exportable) { 'TRUE' } else { 'FALSE' })), ('KeyUsage = ' + $key.usage),
                ('FriendlyName = "' + ($short + ' renewal ' + (Get-Date).ToString('yyyy-MM-dd')) + '"'), 'SMIME = FALSE', '',
                '[EnhancedKeyUsageExtension]')
            foreach ($o in $eku) { $lines += @("OID=$o") }
            if ($sans.Count) {
                $lines += @('', '[Extensions]', '2.5.29.17 = "{text}"')
                foreach ($s in $sans) { $lines += @('_continue_ = "' + $s + '&"') }
            }
            Set-Content -LiteralPath $inf -Value $lines -Encoding Unicode
            $before = @(Get-ChildItem -Path 'Cert:\LocalMachine\REQUEST' -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.Thumbprint })
            if (Test-Path -LiteralPath $r.csr_path) { Remove-Item -LiteralPath $r.csr_path -Force }
            $out = @(& certreq.exe -new -q -machine $inf $r.csr_path 2>&1 | ForEach-Object { [string]$_ })
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $r.csr_path)) { throw ('certreq -new failed: ' + (($out | Where-Object { $_ }) -join ' ')) }
            $req = @(Get-ChildItem -Path 'Cert:\LocalMachine\REQUEST' -ErrorAction SilentlyContinue | Where-Object { $before -notcontains [string]$_.Thumbprint } | ForEach-Object { [string]$_.Thumbprint })
            ConvertTo-Json -InputObject @{ certificate = $subject; issuer = $issuer; old_thumbprint = $tp; store = $store; expires = $r.expires
                found_in = @($where); names = @($names); key = $key.text; request = $(if ($req.Count) { $req[0] } else { '' })
                csr_path = $r.csr_path; made = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss UTC'); aap_job = $job } |
                Set-Content -LiteralPath $info -Encoding UTF8
            $r.status = 'made'
            $made++
        }
        catch { $r.status = 'failed'; $r.reason = [string]$_.Exception.Message }
    }
    if ($r.status -in @('made', 'exists')) {
        $text = (Get-Content -LiteralPath $r.csr_path -Raw) -replace 'NEW CERTIFICATE REQUEST', 'CERTIFICATE REQUEST' -replace "`r`n", "`n"
        $r.csr = $text.Trim() + "`n"
        $h = [string](Get-FileHash -LiteralPath $r.csr_path -Algorithm SHA256).Hash
        # named after the CSR itself: a CSR made again is a new attachment
        $r.csr_sha256 = $h.ToLower()
        $r.file_name = "$short-$($h.Substring(0, 8).ToLower()).$fileExt"
    }
    $results += @($r)
}
$facts.certs = $results
Write-Result
