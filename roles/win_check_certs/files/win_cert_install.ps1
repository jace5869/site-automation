# site-automation - Windows certificate install (roles/win_check_certs/tasks/install_*.yml, run by
# playbooks/win_cert_install_check.yml and win_cert_install_approved.yml). The renewal (win_cert_csr.ps1) left a
# pending request on the server - its new key in the machine key store - and the PKI issued the certificate on
# the request in ServiceNow. Here, by action:
#   pending  the renewals waiting (each <old thumbprint>.json in the folder), with their CSR - the AAP side finds
#            each one's certificate on its request
#   plan     for one issued certificate: what installing it would do - certreq -accept -machine (which puts it
#            together with its key in Personal), and each binding of the old certificate moved to it (https in
#            HTTP.sys - IIS, WSUS ... -, Remote Desktop, the WinRM HTTPS listener) - or why a person does it
#   install  the approved one, planned again first (refused when anything changed): its chain's intermediate CAs
#            into Intermediate Certification Authorities (never a root), certreq -accept, the key's permissions
#            the old key had (and NETWORK SERVICE for Remote Desktop), then each binding moved and read back - and
#            with full PowerShell, the port asked which certificate it serves. Anything wrong: the bindings go back
#            to the old certificate. The old certificate stays in the store, so putting it back is a rebinding.
#   restore  a backup's bindings back to the old certificate (the restore job); the renewal waits again
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

$action = [string](Get-Setting 'action' 'pending')
$dir = [string](Get-Setting 'dir' 'C:\ProgramData\site-automation\renew')
$checkMode = [bool](Get-Setting 'check_mode' $false)
$job = [string](Get-Setting 'job' '')
$rebindKinds = @(Get-Setting 'rebind' @('https', 'rdp', 'winrm') | ForEach-Object { ([string]$_).ToLower() })
$aapHttps = [bool](Get-Setting 'winrm_https_in_use' $false)
$verifyWait = [int](Get-Setting 'verify_wait' 15)
$byHand = [string](Get-Setting 'by_hand' '')
$wsmanKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Listener'
$rdpNs = 'root\cimv2\TerminalServices'

function Get-Info([string] $Path) {
    return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}
function Get-Stamp {
    return (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss.fff')
}
# a new folder <date>-name under parent (administrators and SYSTEM only, as the renewal's)
function New-Folder([string] $Parent, [string] $Name) {
    if (-not (Test-Path -LiteralPath $Parent)) {
        New-Item -ItemType Directory -Path $Parent -Force | Out-Null
        $null = Invoke-Native 'icacls.exe' @($Parent, '/inheritance:r', '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F')
    }
    $d = Join-Path $Parent ((Get-Stamp) + '-' + $Name)
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    return $d
}
function Get-Cert([string] $Store, [string] $Tp) {
    if (-not $Tp) { return $null }
    $p = $Store + '\' + $Tp
    if (Test-Path -LiteralPath $p) { return (Get-ChildItem -Path $p) }
    return $null
}

# ---- the renewals waiting in the folder ---------------------------------------------------------
function Get-Pending {
    $out = @()
    if (-not (Test-Path -LiteralPath $dir)) { return , $out }
    foreach ($f in @(Get-ChildItem -LiteralPath $dir -Filter '*.json' -File)) {
        try { $info = Get-Info $f.FullName } catch { continue }
        $csrPath = [string]$info.csr_path
        if (-not $csrPath -or -not (Test-Path -LiteralPath $csrPath)) { continue }
        $csr = ((Get-Content -LiteralPath $csrPath -Raw) -replace 'NEW CERTIFICATE REQUEST', 'CERTIFICATE REQUEST') -replace "`r`n", "`n"
        $name = Split-Path -Leaf $csrPath
        $short = $name -replace '-[0-9A-Fa-f]{8}\.csr$', ''
        # the CSR's name in the order: <common name>-<8 characters of the CSR file's SHA-256> (win_cert_csr.ps1)
        $h = ([string](Get-FileHash -LiteralPath $csrPath -Algorithm SHA256).Hash).Substring(0, 8).ToLower()
        $out += @(@{ base = ($f.Name -replace '\.json$', ''); stem = "$short-$h"; csr = $csr.Trim() + "`n"; expires = [string]$info.expires
                names = @($info.names); subject = [string]$info.certificate; found_in = @($info.found_in); request = [string]$info.request })
    }
    return , $out
}

# ---- what uses a certificate (its thumbprint) -----------------------------------------------------
function Get-SslBindings([string] $Only) {
    $a = @('http', 'show', 'sslcert')
    if ($Only) { $a += @($Only) }
    $r = Invoke-Native 'netsh.exe' $a
    $out = @()
    $cur = $null
    foreach ($l in @($r.Out)) {
        if ($l -match '^\s*(IP:port|Hostname:port|Central Certificate Store)\s*:\s*(.+?)\s*$') {
            if ($cur) { $out += @($cur) }
            $k = @{ 'IP:port' = 'ipport'; 'Hostname:port' = 'hostnameport'; 'Central Certificate Store' = 'ccs' }[$Matches[1]]
            $cur = @{ kind = 'https'; key = ($k + '=' + $Matches[2]); hash = ''; appid = ''; store = 'My'; ccs = ($k -eq 'ccs') }
        }
        elseif ($cur -and $l -match '^\s*Certificate Hash\s*:\s*([0-9a-fA-F]{40})') { $cur.hash = $Matches[1].ToUpper() }
        elseif ($cur -and $l -match '^\s*Application ID\s*:\s*(\{[0-9a-fA-F-]+\})') { $cur.appid = $Matches[1] }
        elseif ($cur -and $l -match '^\s*Certificate Store Name\s*:\s*(.+?)\s*$' -and $Matches[1] -ne '(null)') { $cur.store = $Matches[1] }
    }
    if ($cur) { $out += @($cur) }
    return , $out
}
function Get-WinrmListeners {
    $out = @()
    foreach ($k in @(Get-ChildItem -Path $wsmanKey -ErrorAction SilentlyContinue)) {
        $v = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue      # its name has a * (Address *)
        if ($null -ne $v -and $v.certThumbprint) {
            $name = ([string]$k.PSPath) -replace '^.*\\', ''          # the key's name: <address>+<transport>
            $addr, $tr = $name -split '\+', 2
            $out += @(@{ kind = 'winrm'; key = $name; address = $addr; transport = $tr; path = [string]$k.PSPath
                    hash = ([string]$v.certThumbprint).Trim().ToUpper() })
        }
    }
    return , $out
}
function Get-RdpHash {
    try {
        $s = Get-CimInstance -Namespace $rdpNs -ClassName Win32_TSGeneralSetting -Filter "TerminalName='RDP-Tcp'" -ErrorAction Stop
        if ($s.SSLCertificateSHA1Hash) { return ([string]$s.SSLCertificateSHA1Hash).ToUpper() }
    }
    catch { }
    return ''
}
function Get-SqlUses([string] $Tp) {
    $out = @()
    foreach ($k in @(Get-ChildItem -Path 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server' -ErrorAction SilentlyContinue)) {
        $leaf = ([string]$k.PSPath) -replace '^.*\\', ''
        if ($leaf -notlike 'MSSQL*.*') { continue }
        $v = Get-ItemProperty -LiteralPath ([string]$k.PSPath + '\MSSQLServer\SuperSocketNetLib') -ErrorAction SilentlyContinue
        if ($null -ne $v -and ([string]$v.Certificate).Trim().ToUpper() -eq $Tp) { $out += @($leaf) }
    }
    return , $out
}
# every binding of a thumbprint: [{kind, key, ...}]
function Get-Uses([string] $Tp) {
    $out = @()
    foreach ($b in (Get-SslBindings '')) { if ($b.hash -eq $Tp) { $out += @($b) } }
    foreach ($l in (Get-WinrmListeners)) { if ($l.hash -eq $Tp) { $out += @($l) } }
    if ((Get-RdpHash) -eq $Tp) { $out += @(@{ kind = 'rdp'; key = 'RDP-Tcp'; hash = $Tp }) }
    return , $out
}

# ---- the plan --------------------------------------------------------------------------------------
function Get-Plan($iss) {
    $base = [string]$iss.base
    $c = $iss.cert
    $item = @{ base = $base; subject = ''; expires = ''; ritm = [string]$iss.ritm; ritm_sys_id = [string]$iss.ritm_sys_id; status = ''; reason = ''
        steps = @(); bindings = @(); notes = @(); files = @(); units = @(); verify = @(); state = ''
        new = @{ sha256 = [string]$c.sha256; sha1 = ([string]$c.sha1).ToUpper(); serial = [string]$c.serial; subject = [string]$c.subject
            issuer = [string]$c.issuer; not_after = [string]$c.not_after; key = [string]$c.key }
        cert_pem = [string]$c.pem; chain_pem = @(@($iss.chain) | Where-Object { $_ } | ForEach-Object { [string]$_.pem })
        chain_roots = @(@($iss.chain) | Where-Object { $_ -and $_.self_signed } | ForEach-Object { ([string]$_.sha1).ToUpper() })
        chain_sha1 = @(@($iss.chain) | Where-Object { $_ } | ForEach-Object { ([string]$_.sha1).ToUpper() }); source = [string]$iss.source }
    $infoPath = Join-Path $dir "$base.json"
    if (-not $base -or -not (Test-Path -LiteralPath $infoPath)) {
        $item.status = 'gone'; $item.reason = "no renewal $base waiting on this server (installed or removed since)"
        return $item
    }
    $info = Get-Info $infoPath
    $item.subject = [string]$info.certificate
    $item.expires = [string]$info.expires
    if ($byHand) {
        $item.status = 'by hand'; $item.reason = $byHand; $item.steps = @('certreq -accept -machine <the issued certificate>.cer, then its bindings')
        return $item
    }
    $old = ([string]$info.old_thumbprint).ToUpper()
    if (-not $old) { $old = $base.ToUpper() }
    $item.old = $old
    $new = $item.new.sha1
    $store = [string]$info.store
    if (-not $store) { $store = 'Cert:\LocalMachine\My' }
    $wrong = @()
    if (@($iss.problems).Count -gt 0 -and $iss.problems[0]) { $wrong += @(@($iss.problems) -join '; ') }
    # the new certificate already with its key in Personal (accepted before), or its pending request still there
    $have = Get-Cert 'Cert:\LocalMachine\My' $new
    if ($have -and $have.HasPrivateKey) { $item.state = 'accepted' }
    elseif ([string]$info.request -match '^[0-9A-Fa-f]{40}$' -and (Test-Path -LiteralPath ('Cert:\LocalMachine\REQUEST\' + [string]$info.request))) { $item.state = 'pending' }
    else { $wrong += @('the pending request (the new key) is gone from this server: this certificate cannot be installed here - the next renewal makes a new CSR') }
    $uses = Get-Uses $old
    $sql = Get-SqlUses $old
    $hand = @()
    $steps = @()
    foreach ($b in $uses) {
        if ($b.kind -eq 'https' -and $b.ccs) { $hand += @("the Central Certificate Store binding $($b.key) (its certificate is a file on the share)"); continue }
        if ($rebindKinds -notcontains $b.kind) { $hand += @("its $($b.kind) binding ($($b.key)) - not in win_cert_install_rebind"); continue }
        if ($b.kind -eq 'winrm' -and $b.transport -eq 'HTTPS' -and $aapHttps) {
            $hand += @("the WinRM HTTPS listener ($($b.key)): AAP connects through it")
            $steps += @("Set-Item -Path WSMan:\localhost\Listener\<the HTTPS listener>\CertificateThumbprint -Value $new -Force")
            continue
        }
        $item.bindings += @(@{ kind = $b.kind; key = $b.key; appid = [string]$b.appid; store = [string]$b.store; address = [string]$b.address
                transport = [string]$b.transport })
    }
    foreach ($s in $sql) {
        $hand += @("SQL Server $s (it needs a restart to take another certificate)")
        $steps += @("point SQL Server $s at the new certificate (thumbprint ${new}: SQL Server Configuration Manager, Protocols, Certificate) and restart it")
    }
    if ($hand.Count) {
        $item.status = 'by hand'
        $item.reason = 'used by ' + ($hand -join '; ') + ': a person installs it' + $(if ($wrong.Count) { ' - and: ' + ($wrong -join '; ') } else { '' })
        $item.steps = @("certreq -accept -machine <the issued certificate>.cer") + $steps
        $item.bindings = @()
        return $item
    }
    if ($wrong.Count) { $item.status = 'held'; $item.reason = $wrong -join '; '; return $item }
    if (-not (Get-Cert $store $old)) { $item.notes += @("the old certificate is no longer in $store") }
    if ($item.state -eq 'accepted' -and $item.bindings.Count -eq 0) {
        $item.status = 'installed already'; $item.reason = 'it is in Personal with its key, and nothing uses the old one any more'
        return $item
    }
    if ($item.bindings.Count -eq 0) { $item.notes += @('nothing found that uses the old one (https, Remote Desktop, WinRM): it goes into the store only - a program that names the old thumbprint needs the new one') }
    $item.files = @($(if ($item.state -eq 'accepted') { 'Personal (it is there with its key)' } else { 'Personal (certreq -accept -machine)' })) +
        @($item.bindings | ForEach-Object { $_.kind + ' ' + $_.key })
    $item.status = 'ready'
    return $item
}

# ---- the install --------------------------------------------------------------------------------
function Get-KeyFile([string] $Tp) {
    $r = Invoke-Native 'certutil.exe' @('-store', 'My', $Tp)
    $name = ''; $cng = $false
    foreach ($l in @($r.Out)) {
        if ($l -match 'Unique container name:\s*(\S+)') { $name = $Matches[1] }
        if ($l -match 'Provider\s*=\s*.*Key Storage Provider') { $cng = $true }
    }
    if (-not $name) { return '' }
    foreach ($p in @((Join-Path $env:ProgramData "Microsoft\Crypto\Keys\$name"), (Join-Path $env:ProgramData "Microsoft\Crypto\RSA\MachineKeys\$name"))) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    if ($cng) { return (Join-Path $env:ProgramData "Microsoft\Crypto\Keys\$name") }
    return ''
}
# the old key's extra permissions (a service account, NETWORK SERVICE ...) on the new key - read; Remote Desktop needs NETWORK SERVICE
function Copy-KeyAccess([string] $OldTp, [string] $NewTp, [bool] $Rdp) {
    $notes = @()
    $newKey = Get-KeyFile $NewTp
    if (-not $newKey) { return 'the new key''s file was not found: its permissions were not copied' }
    $oldKey = Get-KeyFile $OldTp
    $grants = @()
    if ($oldKey) {
        # icacls lists "<the file> NT AUTHORITY\SYSTEM:(F)", then one "<who>:(<rights>)" per line
        foreach ($l in @((Invoke-Native 'icacls.exe' @($oldKey)).Out)) {
            $t = [string]$l
            if ($t.StartsWith($oldKey)) { $t = $t.Substring($oldKey.Length) }
            $t = $t.Trim()
            if ($t -notmatch '^(.+?):(\([A-Z]+\))*\(([A-Z,]+)\)$') { continue }
            $who = $Matches[1].Trim()
            if ($who -match '^(NT AUTHORITY\\SYSTEM|BUILTIN\\Administrators|CREATOR OWNER)$') { continue }
            $grants += @("${who}:(R)")         # read: all a program needs of a key, whatever it had on the old one
        }
    }
    if ($Rdp -and -not ($grants | Where-Object { $_ -like 'NT AUTHORITY\NETWORK SERVICE:*' })) { $grants += @('*S-1-5-20:(R)') }
    foreach ($g in $grants) {
        $r = Invoke-Native 'icacls.exe' @($newKey, '/grant', $g)
        if ($r.Code -eq 0) { $notes += @("the new key: $g") } else { $notes += @("the new key: $g could not be granted ($(@($r.Out) -join ' '))") }
    }
    return $notes
}
function Set-Binding($b, [string] $Tp) {
    if ($b.kind -eq 'https') {
        $r = Invoke-Native 'netsh.exe' @('http', 'update', 'sslcert', $b.key, "certhash=$Tp", "appid=$($b.appid)", "certstorename=$($b.store)")
        if ($r.Code -ne 0) {
            # an older netsh without update: the binding made again, the same application and store
            $null = Invoke-Native 'netsh.exe' @('http', 'delete', 'sslcert', $b.key)
            $r = Invoke-Native 'netsh.exe' @('http', 'add', 'sslcert', $b.key, "certhash=$Tp", "appid=$($b.appid)", "certstorename=$($b.store)")
        }
        $now = @(Get-SslBindings $b.key | Where-Object { $_.key -eq $b.key })
        if ($now.Count -and $now[0].hash -eq $Tp) { return '' }
        return "netsh http update sslcert $($b.key): $(@($r.Out) -join ' ')"
    }
    if ($b.kind -eq 'rdp') {
        try {
            $s = Get-CimInstance -Namespace $rdpNs -ClassName Win32_TSGeneralSetting -Filter "TerminalName='RDP-Tcp'" -ErrorAction Stop
            Set-CimInstance -InputObject $s -Property @{ SSLCertificateSHA1Hash = $Tp } -ErrorAction Stop | Out-Null
        }
        catch { return "Remote Desktop: $($_.Exception.Message)" }
        if ((Get-RdpHash) -eq $Tp) { return '' }
        return 'Remote Desktop still has another certificate'
    }
    if ($b.kind -eq 'winrm') {
        try {
            $l = @(Get-ChildItem -Path 'WSMan:\localhost\Listener' | Where-Object { @($_.Keys) -contains ('Transport=' + $b.transport) -and @($_.Keys) -contains ('Address=' + $b.address) })
            if (-not $l.Count) { return "the WinRM listener $($b.key) was not found" }
            Set-Item -Path ('WSMan:\localhost\Listener\' + $l[0].Name + '\CertificateThumbprint') -Value $Tp -Force -ErrorAction Stop
        }
        catch { return "WinRM listener $($b.key): $($_.Exception.Message)" }
        $now = @(Get-WinrmListeners | Where-Object { $_.key -eq $b.key })
        if ($now.Count -and $now[0].hash -eq $Tp) { return '' }
        return "the WinRM listener $($b.key) still has another certificate"
    }
    return "a binding of kind $($b.kind) is not moved by this job"
}
# the certificate an https binding's port serves now (full PowerShell only: .NET's TLS client)
function Get-Served($b) {
    $script:servedWhy = ''
    if (-not $fullLanguage -or $b.kind -ne 'https') { return '' }
    $hp = ($b.key -split '=', 2)[1]
    $h, $p = $hp -split ':(?=[0-9]+$)', 2
    $sni = $h
    if ($b.key -like 'ipport=*') { if ($h -eq '0.0.0.0' -or $h -eq '[::]') { $h = '127.0.0.1' }; $sni = $env:COMPUTERNAME }
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $a = $tcp.BeginConnect($h, [int]$p, $null, $null)
        if (-not $a.AsyncWaitHandle.WaitOne(5000)) { $script:servedWhy = "${h}:$p does not answer"; return '' }
        $tcp.EndConnect($a)
        $cb = [System.Net.Security.RemoteCertificateValidationCallback] { param($s, $c, $ch, $e) $true }
        $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, $cb)
        $ssl.AuthenticateAsClient($sni)
        $x = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
        $ssl.Dispose()
        return ([string]$x.Thumbprint).ToUpper()
    }
    catch { $script:servedWhy = "${h}:$p - " + [string]$_.Exception.Message; return '' }
    finally { $tcp.Close() }
}
function Undo-Bindings($done, [string] $Old) {
    foreach ($b in $done) { $null = Set-Binding $b $Old }
}
function Install-One($ap) {
    $script:incoming = ''
    try { return (Install-Approved $ap) }
    finally { if ($script:incoming -and (Test-Path -LiteralPath $script:incoming)) { Remove-Item -LiteralPath $script:incoming -Recurse -Force } }
}
function Install-Approved($ap) {
    $base = [string]$ap.base
    $res = @{ base = $base; subject = [string]$ap.subject; ritm = [string]$ap.ritm; ritm_sys_id = [string]$ap.ritm_sys_id; status = ''; reason = ''
        backup = ''; files = @(); units = @(); verified = @(); notes = @(); new = $ap.new }
    if (@('ready', 'installed already') -notcontains [string]$ap.status) { $res.status = 'refused'; $res.reason = "it was not ready in the plan ($($ap.status))"; return $res }
    $chain = @()
    for ($i = 0; $i -lt @($ap.chain_pem).Count; $i++) {
        $chain += @(@{ pem = [string]@($ap.chain_pem)[$i]; sha1 = [string]@($ap.chain_sha1)[$i]; self_signed = (@($ap.chain_roots) -contains [string]@($ap.chain_sha1)[$i]) })
    }
    $iss = @{ base = $base; ritm = $ap.ritm; ritm_sys_id = $ap.ritm_sys_id; source = $ap.source; problems = @()
        cert = @{ pem = [string]$ap.cert_pem; sha1 = [string]$ap.new.sha1; sha256 = [string]$ap.new.sha256; serial = [string]$ap.new.serial
            subject = [string]$ap.new.subject; issuer = [string]$ap.new.issuer; not_after = [string]$ap.new.not_after; key = [string]$ap.new.key }
        chain = $chain }
    $now = Get-Plan $iss
    if ($now.status -eq 'installed already') {
        if (-not $checkMode) { Save-Installed $base $ap '' @() }
        $res.status = 'installed already'; $res.reason = $now.reason; return $res
    }
    if ($now.status -ne 'ready') { $res.status = 'refused'; $res.reason = "changed since the approval - now $($now.status): $($now.reason)"; return $res }
    $want = (@($ap.bindings) | ForEach-Object { $_.kind + ' ' + $_.key } | Sort-Object) -join ', '
    $got = (@($now.bindings) | ForEach-Object { $_.kind + ' ' + $_.key } | Sort-Object) -join ', '
    if ($want -ne $got) { $res.status = 'refused'; $res.reason = "changed since the approval - it is now used by: $got (approved: $want)"; return $res }
    $res.files = @($now.files)
    $new = $now.new.sha1
    $old = $now.old
    if ($checkMode) { $res.status = 'would install'; return $res }
    # the leaf, and its chain's intermediate CAs (never a root: a root is trusted by policy, not by this job)
    $in = New-Folder (Join-Path $dir 'incoming') $base
    $script:incoming = $in
    $leaf = Join-Path $in "$new.cer"
    Set-Content -LiteralPath $leaf -Value ([string]$ap.cert_pem) -Encoding Ascii
    foreach ($c in $chain) {
        if ($c.self_signed -or -not $c.sha1) { continue }
        if (Test-Path -LiteralPath ('Cert:\LocalMachine\CA\' + $c.sha1.ToUpper())) { continue }
        $f = Join-Path $in ($c.sha1 + '.cer')
        Set-Content -LiteralPath $f -Value $c.pem -Encoding Ascii
        $r = Invoke-Native 'certutil.exe' @('-addstore', 'CA', $f)
        if ($r.Code -eq 0) { $res.notes += @("its intermediate CA $($c.sha1) added to Intermediate Certification Authorities") }
    }
    if ($now.state -ne 'accepted') {
        $r = Invoke-Native 'certreq.exe' @('-accept', '-machine', '-q', $leaf)
        $have = Get-Cert 'Cert:\LocalMachine\My' $new
        if ($r.Code -ne 0 -or -not $have -or -not $have.HasPrivateKey) {
            $res.status = 'failed'
            $res.reason = "certreq -accept -machine did not put it in Personal with its key: $((@($r.Out) | Where-Object { $_ }) -join ' ') - nothing else was changed"
            return $res
        }
    }
    foreach ($n in @(Copy-KeyAccess $old $new (@($now.bindings | Where-Object { $_.kind -eq 'rdp' }).Count -gt 0))) { $res.notes += @([string]$n) }
    $bk = New-Folder (Join-Path $dir 'backup') $base
    $res.backup = Split-Path -Leaf $bk
    $rec = @{ base = $base; subject = $now.subject; made = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss UTC'); aap_job = $job
        ritm = [string]$ap.ritm; old_thumbprint = $old; new_thumbprint = $new; bindings = @($now.bindings) }
    ConvertTo-Json -InputObject $rec -Depth 5 | Set-Content -LiteralPath (Join-Path $bk 'backup.json') -Encoding UTF8
    $done = @()
    foreach ($b in $now.bindings) {
        $why = Set-Binding $b $new
        if ($why) {
            Undo-Bindings ($done + @($b)) $old
            $res.status = 'rolled back'; $res.reason = "$why - the bindings are back on the old certificate (the new one stays in Personal)"
            return $res
        }
        $done += @($b)
    }
    foreach ($b in $done) {
        if ($b.kind -ne 'https' -or -not $fullLanguage) { continue }
        $tp = ''
        $tries = $verifyWait
        if ($tries -lt 1) { $tries = 1 }
        for ($i = 0; $i -lt $tries; $i++) {
            $tp = Get-Served $b
            if ($tp -eq $new -or -not $tp) { break }
            Start-Sleep -Seconds 1
        }
        if ($tp -eq $new) { $res.verified += @($b.key) }
        elseif (-not $tp) { $res.notes += @("$($b.key): the port was not asked ($($script:servedWhy)) - the binding was read back") }
        elseif ($tp) {
            Undo-Bindings $done $old
            $res.status = 'rolled back'; $res.reason = "$($b.key) serves $tp, not the new certificate - the bindings are back on the old one"
            return $res
        }
    }
    if (-not $fullLanguage) { $res.notes += @('Constrained Language: the bindings were read back, the ports not asked') }
    Save-Installed $base $ap $res.backup $done
    $res.status = 'installed'
    return $res
}
function Save-Installed([string] $Base, $Ap, [string] $Backup, $Done) {
    $d = New-Folder (Join-Path $dir 'installed') $Base
    $info = Get-Info (Join-Path $dir "$Base.json")
    foreach ($f in @((Join-Path $dir "$Base.json"), [string]$info.csr_path, ([string]$info.csr_path -replace '\.csr$', '.inf'))) {
        if ($f -and (Test-Path -LiteralPath $f)) { Move-Item -LiteralPath $f -Destination $d -Force }
    }
    ConvertTo-Json -InputObject @{ base = $Base; installed = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss UTC'); aap_job = $job
        ritm = [string]$Ap.ritm; new = $Ap.new; backup = $Backup; bindings = @($Done) } -Depth 5 | Set-Content -LiteralPath (Join-Path $d 'installed.json') -Encoding UTF8
}

# ---- restore -------------------------------------------------------------------------------------
function Get-Backups {
    $out = @()
    $root = Join-Path $dir 'backup'
    if (-not (Test-Path -LiteralPath $root)) { return , $out }
    foreach ($d in @(Get-ChildItem -LiteralPath $root | Sort-Object Name)) {
        $f = Join-Path $d.FullName 'backup.json'
        if (-not (Test-Path -LiteralPath $f)) { continue }
        try { $r = Get-Info $f } catch { continue }
        $out += @(@{ name = $d.Name; base = [string]$r.base; subject = [string]$r.subject; made = [string]$r.made; restored = [string]$r.restored
                files = @(@($r.bindings) | ForEach-Object { $_.kind + ' ' + $_.key }); path = $f })
    }
    return , $out
}
function Restore-Backups {
    $all = Get-Backups
    $name = [string](Get-Setting 'backup' '')
    $only = [string](Get-Setting 'only' '')
    $pick = @()
    if ($name) {
        $pick = @($all | Where-Object { $_.name -eq $name })
        if (-not $pick.Count) { return @{ backups = $all; restored = @(@{ backup = $name; status = 'failed'; reason = "no backup $name on this server" }) } }
    }
    elseif ($only) {
        $newest = @{}
        foreach ($b in $all) { if (($b.base + ' ' + $b.subject) -match $only) { $newest[$b.base] = $b } }
        $pick = @($newest.Values | Sort-Object { $_.name })
    }
    $out = @()
    foreach ($b in $pick) {
        $rec = Get-Info $b.path
        $r = @{ backup = $b.name; base = [string]$rec.base; subject = [string]$rec.subject; status = ''; reason = ''; files = @($b.files); units = @() }
        $old = ([string]$rec.old_thumbprint).ToUpper()
        if (-not (Get-Cert 'Cert:\LocalMachine\My' $old)) { $r.status = 'failed'; $r.reason = "the old certificate $old is no longer in Personal"; $out += @($r); continue }
        if ($checkMode) { $r.status = 'would restore'; $out += @($r); continue }
        $bad = @()
        foreach ($x in @($rec.bindings)) {
            $bd = @{ kind = [string]$x.kind; key = [string]$x.key; appid = [string]$x.appid; store = [string]$x.store; address = [string]$x.address; transport = [string]$x.transport }
            $why = Set-Binding $bd $old
            if ($why) { $bad += @($why) }
        }
        if ($bad.Count) { $r.status = 'failed'; $r.reason = $bad -join '; '; $out += @($r); continue }
        # the renewal waits again: the next install check finds the new certificate in Personal and moves the bindings again
        $src = @(Get-ChildItem -LiteralPath (Join-Path $dir 'installed') -ErrorAction SilentlyContinue | Where-Object { $_.Name -like ('*-' + $rec.base) } | Sort-Object Name)
        if ($src.Count) {
            foreach ($f in @(Get-ChildItem -LiteralPath $src[-1].FullName -File | Where-Object { $_.Name -ne 'installed.json' })) {
                if (-not (Test-Path -LiteralPath (Join-Path $dir $f.Name))) { Move-Item -LiteralPath $f.FullName -Destination $dir }
            }
        }
        $rec | Add-Member -NotePropertyName restored -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss UTC')) -Force
        ConvertTo-Json -InputObject $rec -Depth 5 | Set-Content -LiteralPath $b.path -Encoding UTF8
        $r.status = 'restored'
        $out += @($r)
    }
    return @{ backups = $all; restored = $out }
}

if ($action -eq 'pending') { $facts.pending = Get-Pending }
elseif ($action -eq 'plan') { $facts.plan = @(Get-Plan (Get-Setting 'issued' $null)) }
elseif ($action -eq 'install') { $facts.installed = @(Install-One (Get-Setting 'approved' $null)) }
elseif ($action -eq 'restore') { $r = Restore-Backups; $facts.backups = @($r.backups); $facts.restored = @($r.restored) }
else { Add-Finding 'certs:install' 'warning' "unknown action $action" '' }
Write-Result
