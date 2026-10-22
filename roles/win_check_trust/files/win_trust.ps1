# site-automation - Windows check: trust (roles/win_check_trust, run by playbooks/win_health_check.yml and
# playbooks/win_trust_report.yml). Which trust stores on the server still hold a certificate authority that was
# compromised or that the browsers dropped: the machine certificate stores (Trusted Root, Intermediate CAs, the
# roots Windows Update keeps, Trusted Publishers ...), the running user's, every Java cacerts, every Python certifi
# bundle - each certificate matched by its SHA-256 fingerprint AND by the SHA-256 of its public key against the
# list the trust check ships (roles/check_trust/vars/main.yml) plus check_trust_extra. A certificate in the
# Disallowed store is "blocked": listed, no finding.
# With remove on (check_trust_remove, and not a dry run): the certificate is exported to the backup folder, put
# in the Disallowed store (that is Windows' blocklist: it wins over every other store, also over the roots Windows
# Update re-adds) and deleted from the store it was in (not from AuthRoot: Windows Update owns that one, and
# Disallowed covers it); a Java entry is deleted with keytool; a certifi bundle loses the block. Then the stores
# are read again. The result has the same shape as the Linux scan's (stores, software, not_read, counts, removed),
# so the trust report shows Windows and Linux in the same tables.
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

$known = @(Get-Setting 'known' @())
$stores = @(Get-Setting 'stores' @('Cert:\LocalMachine\Root', 'Cert:\LocalMachine\CA', 'Cert:\LocalMachine\AuthRoot', 'Cert:\LocalMachine\TrustedPublisher', 'Cert:\LocalMachine\TrustedPeople', 'Cert:\CurrentUser\Root', 'Cert:\CurrentUser\CA'))
$blockStores = @(Get-Setting 'block_stores' @('Cert:\LocalMachine\Disallowed', 'Cert:\CurrentUser\Disallowed'))
$javaOn = [bool](Get-Setting 'java' $true)
$javaDirs = @(Get-Setting 'java_dirs' @())
$pythonOn = [bool](Get-Setting 'python' $true)
$pythonDirs = @(Get-Setting 'python_dirs' @())
$removeOn = [bool](Get-Setting 'remove' $false)
$dryRun = [bool](Get-Setting 'dry_run' $false)
$severities = @(Get-Setting 'severities' @('critical', 'warning'))
$overrides = Get-Setting 'severity_overrides' $null
$backupDir = [string](Get-Setting 'backup_dir' 'C:\ProgramData\site-automation\trust-backup')
$keystorePassword = [string](Get-Setting 'keystore_password' 'changeit')
$timeout = [int](Get-Setting 'timeout' 60)

# ---- the list: by fingerprint and by public key ----------------------------------------------------
$bySha = @{}
$bySpki = @{}
foreach ($e in $known) {
    if ($null -eq $e) { continue }
    $s = ([string]$e.sha256 -replace ':', '').ToUpper()
    $k = ([string]$e.spki_sha256 -replace ':', '').ToUpper()
    if ($s) { $bySha[$s] = $e }
    if ($k -and -not $bySpki.ContainsKey($k)) { $bySpki[$k] = $e }
}
function Get-Severity($e) {
    $n = [string]$e.name
    if ($null -ne $overrides -and $null -ne $overrides.$n) { return ([string]$overrides.$n).ToLower() }
    $s = [string]$e.severity
    if (-not $s) { return 'warning' }
    return $s.ToLower()
}

# ---- bytes: hashing (a temporary file and Get-FileHash: no .NET hashing in Constrained Language) ----
$tmpDir = $env:TEMP
if (-not $tmpDir) { $tmpDir = [string](Get-Location) }
$script:tmpN = 0
function Get-BytesHash($bytes) {
    $b = [byte[]]@($bytes)
    if ($b.Count -eq 0) { return '' }
    $script:tmpN++
    $f = Join-Path $tmpDir ('sa-trust-' + $PID + '-' + $script:tmpN + '.bin')
    try {
        if ($PSVersionTable.PSVersion.Major -ge 6) { Set-Content -LiteralPath $f -Value $b -AsByteStream }
        else { Set-Content -LiteralPath $f -Value $b -Encoding Byte }
        return ([string](Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash).ToUpper()
    }
    catch { return '' }
    finally { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
}
function Read-FileBytes([string] $Path) {
    if ($PSVersionTable.PSVersion.Major -ge 6) { return [byte[]](Get-Content -LiteralPath $Path -AsByteStream -ReadCount 0) }
    return [byte[]](Get-Content -LiteralPath $Path -Encoding Byte -ReadCount 0)
}
# DER: the element at $b[$i] -> tag, start of its value, end
function Read-Tlv($b, [int] $i) {
    if ($i + 2 -gt $b.Count) { throw 'short' }
    $tag = [int]$b[$i]; $l = [int]$b[$i + 1]; $hdr = 2
    if ($l -band 0x80) {
        $n = $l -band 0x7F
        if ($n -eq 0 -or $n -gt 4 -or $i + 2 + $n -gt $b.Count) { throw 'length' }
        $l = 0
        for ($k = 0; $k -lt $n; $k++) { $l = $l * 256 + [int]$b[$i + 2 + $k] }
        $hdr = 2 + $n
    }
    $start = $i + $hdr
    if ($start + $l -gt $b.Count) { throw 'overrun' }
    return @($tag, $start, ($start + $l))
}
# the SubjectPublicKeyInfo of a certificate (DER), as bytes; empty when the bytes cannot be walked
function Get-SpkiBytes($der) {
    try {
        $t = Read-Tlv $der 0
        $t = Read-Tlv $der $t[1]                # TBSCertificate
        $pos = [int]$t[1]
        $x = Read-Tlv $der $pos
        if ([int]$x[0] -eq 0xA0) { $pos = [int]$x[2] }   # [0] version
        for ($k = 0; $k -lt 5; $k++) { $x = Read-Tlv $der $pos; $pos = [int]$x[2] }   # serial, algorithm, issuer, validity, subject
        $x = Read-Tlv $der $pos
        return [byte[]]@($der[$pos..([int]$x[2] - 1)])
    }
    catch { return [byte[]]@() }
}
function Get-Subject($der) {
    # the CN of the subject, for a certificate matched by its key only
    try {
        $t = Read-Tlv $der 0; $t = Read-Tlv $der $t[1]; $pos = [int]$t[1]
        $x = Read-Tlv $der $pos; if ([int]$x[0] -eq 0xA0) { $pos = [int]$x[2] }
        for ($k = 0; $k -lt 4; $k++) { $x = Read-Tlv $der $pos; $pos = [int]$x[2] }
        $x = Read-Tlv $der $pos
        $s = [int]$x[1]; $e = [int]$x[2]
        for ($i = $s; $i -lt $e - 5; $i++) {
            if ([int]$der[$i] -eq 0x06 -and [int]$der[$i + 1] -eq 3 -and [int]$der[$i + 2] -eq 0x55 -and [int]$der[$i + 3] -eq 4 -and [int]$der[$i + 4] -eq 3) {
                $v = Read-Tlv $der ($i + 5)
                $chars = @($der[([int]$v[1])..([int]$v[2] - 1)] | ForEach-Object { [char][int]$_ })
                return (-join $chars)
            }
        }
    }
    catch { }
    return ''
}
# a certificate's bytes -> the list entry it matches (with how), or $null
function Match-Der($der) {
    $s = Get-BytesHash $der
    if ($s -and $bySha.ContainsKey($s)) { $e = $bySha[$s]; return @{ name = [string]$e.name; severity = (Get-Severity $e); why = [string]$e.why; how = 'certificate'; sha256 = $s; kind = [string]$e.kind } }
    $spki = Get-SpkiBytes $der
    if ($spki.Count -gt 0) {
        $k = Get-BytesHash $spki
        if ($k -and $bySpki.ContainsKey($k)) { $e = $bySpki[$k]; return @{ name = [string]$e.name; severity = (Get-Severity $e); why = [string]$e.why; how = 'key'; sha256 = $s; cn = (Get-Subject $der); kind = [string]$e.kind } }
    }
    return $null
}
# base64 -> bytes: .NET in full language; certutil.exe -decode (a temporary file) in Constrained Language Mode
function ConvertFrom-B64([string] $b64) {
    if ($fullLanguage) { return [byte[]][Convert]::FromBase64String($b64) }
    $script:tmpN++
    $f = Join-Path $tmpDir ('sa-trust-' + $PID + '-' + $script:tmpN)
    try {
        Set-Content -LiteralPath ($f + '.b64') -Value $b64
        $r = Invoke-Native 'certutil.exe' @('-f', '-decode', ($f + '.b64'), ($f + '.der'))
        if ($r.Code -ne 0) { throw 'certutil -decode failed' }
        return (Read-FileBytes ($f + '.der'))
    }
    finally {
        Remove-Item -LiteralPath ($f + '.b64') -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ($f + '.der') -Force -ErrorAction SilentlyContinue
    }
}
# the PEM blocks of a text: [(whole match start, length, base64 text)] - Select-String works in every language mode
function Get-PemBlocks([string] $text) {
    $out = @()
    foreach ($m1 in @(($text | Select-String -Pattern '(?s)-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----\r?\n?' -AllMatches).Matches)) {
        if ($null -eq $m1) { continue }
        $out += @(@{ index = $m1.Index; length = $m1.Length; b64 = ($m1.Groups[1].Value -replace '\s', '') })
    }
    return $out
}
# every certificate in a PEM text -> bytes
function Get-PemCerts([string] $text) {
    $out = @()
    foreach ($blk in @(Get-PemBlocks $text)) {
        try { $out += , (ConvertFrom-B64 $blk.b64) } catch { }
    }
    return , $out
}
# a JKS keystore -> the trusted certificates with their aliases; $null when the bytes are not a JKS
function Get-JksEntries($b) {
    if ($b.Count -lt 12 -or [int]$b[0] -ne 0xFE -or [int]$b[1] -ne 0xED -or [int]$b[2] -ne 0xFE -or [int]$b[3] -ne 0xED) { return $null }
    $pos = 4
    function U4 { $v = ([int]$b[$script:pos] * 16777216) + ([int]$b[$script:pos + 1] * 65536) + ([int]$b[$script:pos + 2] * 256) + [int]$b[$script:pos + 3]; $script:pos += 4; return $v }
    function Utf { $n = ([int]$b[$script:pos] * 256) + [int]$b[$script:pos + 1]; $script:pos += 2; $s = -join @($b[$script:pos..($script:pos + $n - 1)] | ForEach-Object { [char][int]$_ }); if ($n -eq 0) { $s = '' }; $script:pos += $n; return $s }
    $script:pos = 4
    $out = @()
    try {
        $version = U4
        $count = U4
        for ($i = 0; $i -lt $count; $i++) {
            $start = $script:pos
            $tag = U4
            $alias = Utf
            $script:pos += 8
            if ($tag -eq 1) {
                $n = U4; $script:pos += $n
                $chain = U4
                for ($j = 0; $j -lt $chain; $j++) { if ($version -eq 2) { $null = Utf }; $n = U4; $script:pos += $n }
            }
            elseif ($tag -eq 2) {
                if ($version -eq 2) { $null = Utf }
                $n = U4
                $der = [byte[]]@($b[$script:pos..($script:pos + $n - 1)])
                $script:pos += $n
                $out += , @{ alias = $alias; der = $der; start = $start; end = $script:pos; trusted = $true }
                continue
            }
            else { break }
            $out += , @{ alias = $alias; der = $null; start = $start; end = $script:pos; trusted = $false }
        }
    }
    catch { }
    return @{ version = $version; entries = @($out); end = $script:pos }
}
# the keystore without some entries, with the integrity digest keytool checks (SHA-1 over the password as UTF-16BE,
# 'Mighty Aphrodite', the data) - a temporary file and Get-FileHash, so it works in Constrained Language Mode too
function Write-Jks([string] $Path, $b, $Jks, $DropAliases, [string] $Password) {
    $keep = @($Jks.entries | Where-Object { $DropAliases -notcontains $_.alias })
    $n = [int]$keep.Count
    $body = [byte[]]@(0xFE, 0xED, 0xFE, 0xED) + [byte[]]@(0, 0, 0, [int]$Jks.version) + [byte[]]@((($n -shr 24) -band 255), (($n -shr 16) -band 255), (($n -shr 8) -band 255), ($n -band 255))
    foreach ($e in $keep) { $body += [byte[]]@($b[$e.start..($e.end - 1)]) }
    $pw = @()
    foreach ($ch in $Password.ToCharArray()) { $pw += @(0, [int][char]$ch) }
    $salted = [byte[]]@($pw) + [byte[]][char[]]'Mighty Aphrodite' + $body
    $script:tmpN++
    $f = Join-Path $tmpDir ('sa-trust-' + $PID + '-' + $script:tmpN + '.jks')
    try {
        if ($PSVersionTable.PSVersion.Major -ge 6) { Set-Content -LiteralPath $f -Value $salted -AsByteStream } else { Set-Content -LiteralPath $f -Value $salted -Encoding Byte }
        $hex = [string](Get-FileHash -LiteralPath $f -Algorithm SHA1).Hash
    }
    finally { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
    $digest = @()
    for ($i = 0; $i -lt 40; $i += 2) { $digest += @([int]('0x' + $hex.Substring($i, 2))) }
    $all = $body + [byte[]]@($digest)
    if ($PSVersionTable.PSVersion.Major -ge 6) { Set-Content -LiteralPath $Path -Value $all -AsByteStream } else { Set-Content -LiteralPath $Path -Value $all -Encoding Byte }
}

# ---- reading ---------------------------------------------------------------------------------------
$script:recs = @()
$script:notRead = @()
$script:certCount = 0
$script:fileCount = 0
function Add-Store([string] $Kind, [string] $Where, $Ders, [string] $Version, [string] $Note, [bool] $Blocklist) {
    $hits = @(); $blocked = @(); $seen = @{}
    foreach ($der in @($Ders)) {
        if ($null -eq $der -or $der.Count -eq 0) { continue }
        $hit = Match-Der $der
        if ($null -eq $hit) { continue }
        if ($Blocklist) { if ($blocked -notcontains $hit.name) { $blocked += @($hit.name) }; continue }
        $key = $hit.name + '|' + $hit.how + '|' + [string]$hit.cn
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $hits += @($hit)
    }
    $script:certCount += @($Ders).Count
    $rec = @{ kind = $Kind; where = $Where; version = $Version; count = @($Ders).Count; hits = @($hits); blocked = @($blocked); note = $Note }
    $script:recs += @($rec)
    return $rec
}
function Skip([string] $Kind, [string] $Where, [string] $Reason) {
    $script:notRead += @(@{ kind = $Kind; where = $Where; reason = $Reason })
}
function Read-Stores {
    $script:recs = @(); $script:notRead = @(); $script:certCount = 0; $script:fileCount = 0
    foreach ($st in $stores + $blockStores) {
        $isBlock = $blockStores -contains $st
        try { $certs = @(Get-ChildItem -Path $st -ErrorAction Stop) }
        catch {
            if ($st -like 'Cert:\CurrentUser*') { continue }      # a store the running account does not have
            Skip 'winstore' $st ('could not be read: ' + [string]$_)
            continue
        }
        $ders = @()
        $byTp = @{}
        foreach ($c in @($certs)) {
            if ($null -eq $c -or $null -eq $c.RawData) { continue }
            $d = [byte[]]@($c.RawData)
            $ders += , $d
            $byTp[([string]$c.Thumbprint).ToUpper()] = $d
        }
        $rec = Add-Store $(if ($isBlock) { 'blocklist' } else { 'winstore' }) $st $ders '' '' $isBlock
        $rec['thumbprints'] = @{}
        foreach ($c in @($certs)) {
            if ($null -eq $c -or $null -eq $c.RawData) { continue }
            $hit = Match-Der ([byte[]]@($c.RawData))
            if ($null -ne $hit) { $rec['thumbprints'][([string]$c.Thumbprint).ToUpper()] = $hit.name }
        }
    }
    # a certificate in a Disallowed store is blocked everywhere: Windows refuses it whatever other store holds it
    $blockedNames = @()
    foreach ($rec in @($script:recs)) { if ($rec.kind -eq 'blocklist') { $blockedNames += @($rec.blocked) } }
    foreach ($rec in @($script:recs)) {
        if ($rec.kind -ne 'winstore') { continue }
        foreach ($h in @($rec.hits)) { if ($blockedNames -contains [string]$h.name -and $rec.blocked -notcontains [string]$h.name) { $rec.blocked += @([string]$h.name) } }
    }
    if ($javaOn) {
        foreach ($f in @(Find-Files $javaDirs 'cacerts')) { if ($f) { Read-Java ([string]$f) } }
    }
    if ($pythonOn) {
        foreach ($f in @(Find-Files $pythonDirs 'cacert.pem')) {
            if (-not $f) { continue }
            if ((Split-Path -Leaf (Split-Path -Parent ([string]$f))) -ne 'certifi') { continue }
            Read-Pem 'python' ([string]$f)
        }
    }
}
function Find-Files($Dirs, [string] $Name) {
    $out = @()
    foreach ($d in @($Dirs)) {
        if (-not $d) { continue }
        foreach ($root in @(Resolve-Path -Path $d -ErrorAction SilentlyContinue)) {
            try { $out += @(Get-ChildItem -Path ([string]$root) -Recurse -Depth 7 -Filter $Name -File -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.FullName }) }
            catch { }
        }
    }
    return @($out | Sort-Object -Unique)
}
function Get-JavaVersion([string] $Cacerts) {
    foreach ($rel in @('..\..\release', '..\..\..\release')) {
        $r = Join-Path (Split-Path -Parent $Cacerts) $rel
        if (Test-Path -LiteralPath $r) {
            foreach ($l in @(Get-Content -LiteralPath $r -ErrorAction SilentlyContinue)) { if ($l -match '^JAVA_VERSION="?([^"]+)') { return $Matches[1] } }
        }
    }
    return ''
}
function Get-Keytool([string] $Cacerts) {
    foreach ($rel in @('..\..\bin\keytool.exe', '..\..\..\bin\keytool.exe', '..\..\bin\keytool')) {
        $k = Join-Path (Split-Path -Parent $Cacerts) $rel
        if (Test-Path -LiteralPath $k) { return [string](Resolve-Path -LiteralPath $k) }
    }
    return ''
}
function Read-Java([string] $f) {
    $script:fileCount++
    $version = Get-JavaVersion $f
    try { $b = Read-FileBytes $f } catch { Skip 'java' $f ('not readable: ' + [string]$_); return }
    $jks = Get-JksEntries $b
    if ($null -eq $jks) {
        # PKCS12 (JDK 18 and later): keytool lists it, matched by the fingerprints it prints
        $kt = Get-Keytool $f
        if (-not $kt) { Skip 'java' $f 'not a JKS keystore, and no keytool next to it to read it'; return }
        $r = Invoke-Native $kt @('-list', '-keystore', $f, '-storepass', $keystorePassword)
        if ($r.Code -ne 0) { Skip 'java' $f ('keytool could not list it: ' + (($r.Out | Select-Object -First 2) -join ' ')); return }
        $hits = @(); $n = 0; $seen = @{}; $alias = ''; $aliases = @{}
        foreach ($l in @($r.Out)) {
            if ($l -match '^(.+?),\s+\S+ \d+, \d{4},\s+trustedCertEntry') { $alias = $Matches[1].Trim(); continue }
            if ($l -match '\(SHA-?256\):\s*([0-9A-Fa-f:]{95})') {
                $n++
                $fp = ($Matches[1] -replace ':', '').ToUpper()
                if ($bySha.ContainsKey($fp)) {
                    $e = $bySha[$fp]
                    if (-not $seen.ContainsKey([string]$e.name)) { $seen[[string]$e.name] = $true; $hits += @(@{ name = [string]$e.name; severity = (Get-Severity $e); why = [string]$e.why; how = 'certificate'; sha256 = $fp; kind = [string]$e.kind }) }
                    if ($alias) { $aliases[$alias] = [string]$e.name }
                }
            }
        }
        $script:certCount += $n
        $script:recs += @(@{ kind = 'java'; where = $f; version = $version; count = $n; hits = @($hits); blocked = @(); aliases = $aliases; keytool = $kt
                note = 'read with keytool (by fingerprint: a certificate with the same key under another name is not seen)' })
        return
    }
    $entries = @($jks.entries | Where-Object { $_.trusted })
    $rec = Add-Store 'java' $f @($entries | ForEach-Object { , $_.der }) $version '' $false
    $al = @{}
    foreach ($en in @($entries)) { $mt = Match-Der $en.der; if ($null -ne $mt) { $al[[string]$en.alias] = $mt.name } }
    $rec['aliases'] = $al
    $rec['keytool'] = Get-Keytool $f
    $rec['jks'] = $true
}
function Read-Pem([string] $Kind, [string] $f) {
    $script:fileCount++
    try { $text = [string](Get-Content -LiteralPath $f -Raw -ErrorAction Stop) } catch { Skip $Kind $f ('not readable: ' + [string]$_); return }
    $version = ''
    $init = Join-Path (Split-Path -Parent $f) '__init__.py'
    if (Test-Path -LiteralPath $init) { foreach ($l in @(Get-Content -LiteralPath $init -ErrorAction SilentlyContinue)) { if ($l -match '__version__\s*=\s*[''"]([^''"]+)') { $version = $Matches[1]; break } } }
    $null = Add-Store $Kind $f (Get-PemCerts $text) $version '' $false
}

# ---- removal ---------------------------------------------------------------------------------------
$script:actions = @()
$script:notRemoved = @()
$script:backup = ''
$script:changed = $false
function Wanted($rec) {
    $names = @()
    foreach ($h in @($rec.hits)) { if ($severities -contains [string]$h.severity -and $names -notcontains $h.name) { $names += @([string]$h.name) } }
    return @($names | Sort-Object)
}
function Add-Action($rec, [string] $Action, $Names, [bool] $Done, [string] $Backup, [string] $Err) {
    $script:actions += @(@{ kind = [string]$rec.kind; where = [string]$rec.where; names = @($Names); action = $Action; done = ($Done -and -not $dryRun); planned = $dryRun; backup = $Backup; error = $Err })
    if ($Done -and -not $dryRun) { $script:changed = $true }
}
function Leave($rec, [string] $Why) {
    $script:notRemoved += @(@{ kind = [string]$rec.kind; where = [string]$rec.where; names = @(Wanted $rec); why = $Why })
}
function Get-BackupDir {
    if ($dryRun) { return '' }
    if (-not $script:backup) {
        $script:backup = Join-Path $backupDir (Get-Date -Format 'yyyy-MM-dd-HHmmss')
        $null = New-Item -ItemType Directory -Path $script:backup -Force
    }
    return $script:backup
}
function Remove-FromStore($rec) {
    $names = @(Wanted $rec)
    if ($names.Count -eq 0) { return }
    $st = [string]$rec.where
    $disallowed = $(if ($st -like 'Cert:\CurrentUser*') { 'Cert:\CurrentUser\Disallowed' } else { 'Cert:\LocalMachine\Disallowed' })
    $authRoot = ($st -like '*\AuthRoot')
    $tps = @()
    foreach ($tp in @($rec.thumbprints.Keys)) { if ($names -contains [string]$rec.thumbprints[$tp]) { $tps += @($tp) } }
    $action = 'put in ' + $disallowed + ' (Windows'' blocklist)' + $(if ($authRoot) { ' - left in AuthRoot: Windows Update owns that store, Disallowed overrides it' } else { ' and deleted from ' + $st })
    if ($dryRun) { Add-Action $rec $action $names $false '' ''; return }
    $errors = @()
    $dir = Get-BackupDir
    $backups = @()
    foreach ($tp in $tps) {
        try {
            $c = Get-ChildItem -Path ($st + '\' + $tp) -ErrorAction Stop
            $file = Join-Path $dir ((($st -replace '[:\\/]+', '_').Trim('_')) + '-' + $tp + '.cer')
            $null = Export-Certificate -Cert $c -FilePath $file -Force
            $backups += @($file)
            $null = Import-Certificate -FilePath $file -CertStoreLocation $disallowed
            if (-not $authRoot) { Remove-Item -LiteralPath ($st + '\' + $tp) -Force -ErrorAction Stop }
        }
        catch { $errors += @($tp + ': ' + [string]$_) }
    }
    Add-Action $rec $action $names ($errors.Count -eq 0) ($backups -join ', ') ($errors -join '; ')
}
function Remove-FromJava($rec) {
    $names = @(Wanted $rec)
    if ($names.Count -eq 0) { return }
    $kt = [string]$rec.keytool
    $native = [bool]$rec.jks
    if (-not $kt -and -not $native) { Leave $rec 'not a JKS keystore and no keytool next to it: keytool -delete by hand'; return }
    $aliases = @()
    foreach ($a in @($rec.aliases.Keys)) { if ($names -contains [string]$rec.aliases[$a]) { $aliases += @([string]$a) } }
    if ($aliases.Count -eq 0) { Leave $rec 'its matching entries are key entries, or matched by key only: keytool -delete by hand'; return }
    $action = 'deleted the entries ' + ($aliases -join ', ') + $(if ($native) { ' from the keystore' } else { ' with keytool' }) + ' - restart the Java services that use it (a running JVM keeps the old trust until it restarts)'
    if ($dryRun) { Add-Action $rec $action $names $false '' ''; return }
    $dir = Get-BackupDir
    $file = Join-Path $dir ((([string]$rec.where) -replace '[:\\/]+', '_').Trim('_'))
    try { Copy-Item -LiteralPath $rec.where -Destination $file -Force } catch { Add-Action $rec $action $names $false '' ('backup failed: ' + [string]$_); return }
    if ($native) {
        try {
            $b = Read-FileBytes $rec.where
            Write-Jks $rec.where $b (Get-JksEntries $b) $aliases $keystorePassword
            Add-Action $rec $action $names $true $file ''
        }
        catch { Add-Action $rec $action $names $false $file ([string]$_) }
        return
    }
    $errors = @()
    foreach ($a in $aliases) {
        $r = Invoke-Native $kt @('-delete', '-alias', $a, '-keystore', [string]$rec.where, '-storepass', $keystorePassword)
        if ($r.Code -ne 0) { $errors += @($a + ': ' + (($r.Out | Select-Object -First 1) -join ' ')) }
    }
    Add-Action $rec $action $names ($errors.Count -eq 0) $file ($errors -join '; ')
}
function Remove-FromPem($rec) {
    $names = @(Wanted $rec)
    if ($names.Count -eq 0) { return }
    $f = [string]$rec.where
    try { $text = [string](Get-Content -LiteralPath $f -Raw -ErrorAction Stop) } catch { Leave $rec ('not readable: ' + [string]$_); return }
    $kept = 0; $parts = @(); $last = 0; $cut = $false
    foreach ($blk in @(Get-PemBlocks $text)) {
        $der = $null
        try { $der = ConvertFrom-B64 $blk.b64 } catch { }
        $w = $null
        if ($null -ne $der) { $w = Match-Der $der }
        if ($null -eq $w -or $names -notcontains $w.name) { $kept++; continue }
        $seg = @($text.Substring($last, $blk.index - $last) -split "`n")
        while ($seg.Count -gt 0 -and ((-not $seg[-1].Trim()) -or ($seg[-1].StartsWith('#') -and -not $seg[-1].StartsWith('##')))) { $seg = @($seg | Select-Object -SkipLast 1) }
        $parts += @(($seg -join "`n") + $(if ($seg.Count) { "`n" } else { '' }))
        $last = $blk.index + $blk.length
        $cut = $true
    }
    $parts += @($text.Substring($last))
    if (-not $cut) { Leave $rec 'its matching certificate is not a PEM block: take it out by hand'; return }
    if ($kept -eq 0) { Leave $rec 'the bundle holds nothing else - cut, it would leave the environment without roots: pip install -U certifi there'; return }
    $action = 'removed the block from the bundle (' + $kept + ' certificates kept)'
    if ($dryRun) { Add-Action $rec $action $names $false '' ''; return }
    $dir = Get-BackupDir
    $file = Join-Path $dir (($f -replace '[:\\/]+', '_').Trim('_'))
    try {
        Copy-Item -LiteralPath $f -Destination $file -Force
        Set-Content -LiteralPath $f -Value ($parts -join '') -NoNewline
        Add-Action $rec $action $names $true $file ''
    }
    catch { Add-Action $rec $action $names $false $file ([string]$_) }
}

# ---- run -------------------------------------------------------------------------------------------
Read-Stores
if ($removeOn) {
    foreach ($rec in @($script:recs)) {
        if (@(Wanted $rec).Count -eq 0) { continue }
        switch ([string]$rec.kind) {
            'winstore' { Remove-FromStore $rec }
            'java' { Remove-FromJava $rec }
            'python' { Remove-FromPem $rec }
            default { Leave $rec 'no removal for this kind' }
        }
    }
    $removed = @{ actions = @($script:actions); not_removed = @($script:notRemoved); backup = $script:backup; changed = $script:changed }
    if ($script:changed) { Read-Stores }
}

# ---- findings: one per kind of store, the worst certificate it holds (blocked ones: none) --------
$blockedNames = @()
foreach ($rec in @($script:recs)) { if ($rec.kind -eq 'blocklist') { $blockedNames += @($rec.blocked) } }
$order = @{ critical = 0; warning = 1; notice = 2 }
$groups = @{}
foreach ($rec in @($script:recs)) {
    if ($rec.kind -eq 'blocklist') { continue }
    $g = $(if ($rec.kind -eq 'winstore') { 'trust:system' } elseif ($rec.kind -eq 'python') { 'trust:python' } else { 'trust:' + $rec.kind + ':' + $rec.where })
    foreach ($h in @($rec.hits)) {
        if ([string]$h.severity -eq 'notice' -or $rec.blocked -contains [string]$h.name) { continue }
        if (-not $groups.ContainsKey($g)) { $groups[$g] = @{ kind = $rec.kind; wheres = @(); hits = @{}; sev = 'warning' } }
        if ($groups[$g].wheres -notcontains $rec.where) { $groups[$g].wheres += @($rec.where) }
        $groups[$g].hits[[string]$h.name] = $h
        if ($order[[string]$h.severity] -lt $order[$groups[$g].sev]) { $groups[$g].sev = [string]$h.severity }
    }
}
foreach ($g in @($groups.Keys | Sort-Object)) {
    $grp = $groups[$g]
    $names = @($grp.hits.Keys | Sort-Object { $order[[string]$grp.hits[$_].severity] }, { $_ })
    $first = $grp.hits[$names[0]]
    $blockedNote = ''
    foreach ($n in $names) { if ($blockedNames -contains $n) { $blockedNote = ' - ' + $n + ' is in the Disallowed store already: the store entry stays until it is deleted' } }
    $where = $(switch ($grp.kind) {
            'winstore' { 'the Windows certificate stores (' + (@($grp.wheres | ForEach-Object { ($_ -split '\\')[-1] } | Sort-Object -Unique) -join ', ') + ')' }
            'python' { [string]$grp.wheres.Count + ' Python certifi bundle' + $(if ($grp.wheres.Count -ne 1) { 's' }) + ' (' + ($grp.wheres -join ', ') + ')' }
            default { $grp.wheres[0] + ' (Java cacerts)' }
        })
    $hint = $(switch ($grp.kind) {
            'winstore' { 'certutil -store Root | findstr /i emsign - fix: put the certificate in the Disallowed store and delete it from the store it is in (check_trust_remove does both); the roots Windows Update keeps (AuthRoot) are overridden by Disallowed' }
            'python' { 'pip install -U "certifi>=2026.10.19" in each Python environment the finding lists (check_trust_remove cuts the block out)' }
            default { 'keytool -list -keystore "' + $grp.wheres[0] + '" -storepass changeit | findstr /i emsign - fix: update the JDK, or keytool -delete -alias <alias> (check_trust_remove does this)' }
        })
    Add-Finding $g $grp.sev ($where + ' trusts ' + ($names -join ', ') + ' - ' + [string]$first.why + $blockedNote) $hint
}
if (@($script:recs | Where-Object { $_.kind -eq 'winstore' }).Count -eq 0) {
    Add-Finding 'trust:unreadable' 'warning' 'no Windows certificate store could be read on this server' 'the account AAP connects with cannot open Cert:\LocalMachine: run the check as an administrator'
}

# the stores' records without the working fields (thumbprints, aliases, keytool: not for the report)
$clean = @()
foreach ($rec in @($script:recs)) {
    $clean += @(@{ kind = $rec.kind; where = $rec.where; version = $rec.version; count = $rec.count; hits = @($rec.hits); blocked = @($rec.blocked); note = [string]$rec.note })
}
$facts = @{ stores = @($clean); software = @(); not_read = @($script:notRead); counts = @{ files = $script:fileCount; certificates = $script:certCount } }
if ($removeOn) { $facts['removed'] = $removed }
Write-Result
