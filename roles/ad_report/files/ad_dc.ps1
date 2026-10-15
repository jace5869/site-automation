# site-automation - Active Directory health report: what ONE domain controller says about itself
# (roles/ad_report, run by playbooks/ad_health_report.yml on every DC). Read-only: queries, dcdiag
# tests that only read, repadmin /showrepl and /queue, w32tm queries, event logs. It changes nothing.
# Everything is asked of THIS DC (localhost): no second hop to another server, so it works over
# WinRM with NTLM or Kerberos without delegation. The judging (what is red, amber, green) is done
# by the report (plugins/filter/ad_filters.py), not here: this script only collects facts.
# ---- common to every site-automation Windows script (keep identical in all of them) -----------
# Settings arrive as JSON in $env:SITE_CHECK_CONFIG (the role's defaults and your inventory).
# The script prints ONE line: ###SITE-JSON### {...}
# Windows PowerShell 5.1 and PowerShell 7; also works in Constrained Language Mode.
$ErrorActionPreference = 'Stop'
$cfgText = '{}'
if ($env:SITE_CHECK_CONFIG) { $cfgText = $env:SITE_CHECK_CONFIG }
$cfg = ConvertFrom-Json -InputObject $cfgText
function Get-Setting([string] $Name, $Default) {
    if ($null -ne $cfg -and $null -ne $cfg.$Name) { return $cfg.$Name }
    return $Default
}
# ---- end of the common part --------------------------------------------------------------------

$hours = [double](Get-Setting 'hours' 24)
$eventHours = [double](Get-Setting 'event_hours' 24)
$now = Get-Date
$since = $now.AddHours(-$hours)
$eventSince = $now.AddHours(-$eventHours)
$dc = @{ errors = @(); read_at = '' }
function Iso($t) {
    if ($null -eq $t) { return '' }
    try { return ([datetime]$t).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } catch { return '' }
}
function Note([string] $What, $Err) {
    $msg = [string]$Err
    if ($Err -and $Err.Exception) { $msg = [string]$Err.Exception.Message }
    $script:dc.errors += @(('{0}: {1}' -f $What, $msg.Trim()))
}
function Lines($Out) { return @($Out | ForEach-Object { [string]$_ }) }
$dc.read_at = Iso $now

# ---- who am I --------------------------------------------------------------------------------
try {
    $cs = @(Get-CimInstance -ClassName Win32_ComputerSystem)[0]
    $os = @(Get-CimInstance -ClassName Win32_OperatingSystem)[0]
    $dc.name = [string]$cs.Name
    $dc.domain = [string]$cs.Domain
    $dc.fqdn = ('{0}.{1}' -f $cs.DNSHostName, $cs.Domain).ToLower()
    $dc.role = $(if ([int]$cs.DomainRole -eq 5) { 'pdc' } elseif ([int]$cs.DomainRole -eq 4) { 'dc' } else { 'member' })
    $dc.os = [string]$os.Caption
    $dc.boot = Iso $os.LastBootUpTime
}
catch { Note 'computer' $_ }

$adModule = $true
try { Import-Module ActiveDirectory -ErrorAction Stop -WarningAction SilentlyContinue }
catch { $adModule = $false; Note 'ActiveDirectory PowerShell module' $_ }
$dc.ad_module = $adModule

# ---- LDAP answers, synchronized, GC --------------------------------------------------------------
$dc.ldap = @{ ok = $false; synchronized = $null; gc_ready = $null; error = '' }
if ($adModule) {
    try {
        $me = Get-ADDomainController -Server localhost
        $dc.site = [string]$me.Site
        $dc.forest = [string]$me.Forest
        $dc.is_gc = [bool]$me.IsGlobalCatalog
        $dc.is_rodc = [bool]$me.IsReadOnly
        $dc.fsmo = @($me.OperationMasterRoles | ForEach-Object { [string]$_ })
        if ($me.HostName) { $dc.fqdn = ([string]$me.HostName).ToLower() }
    }
    catch { Note 'Get-ADDomainController' $_ }
    try {
        $rd = Get-ADRootDSE -Server localhost
        $dc.ldap.ok = $true
        $dc.ldap.synchronized = ([string]$rd.isSynchronized -eq 'True')
        $dc.ldap.gc_ready = ([string]$rd.isGlobalCatalogReady -eq 'True')
    }
    catch { $dc.ldap.error = [string]$_.Exception.Message }
    if ($dc.is_gc) {
        $dc.gc_port = @{ ok = $false; error = '' }
        try { $null = Get-ADRootDSE -Server ('{0}:3268' -f $dc.fqdn); $dc.gc_port.ok = $true }
        catch { $dc.gc_port.error = [string]$_.Exception.Message }
    }
}

# ---- services -----------------------------------------------------------------------------------
$svcNames = @(Get-Setting 'services' @('NTDS', 'Netlogon', 'kdc', 'DNS', 'DFSR', 'NtFrs', 'W32Time', 'ADWS', 'IsmServ', 'LanmanServer'))
$dc.services = @()
try {
    $all = @(Get-CimInstance -ClassName Win32_Service)
    foreach ($n in $svcNames) {
        $s = @($all | Where-Object { [string]$_.Name -eq [string]$n })
        if ($s.Count) { $dc.services += @(@{ name = [string]$s[0].Name; state = [string]$s[0].State; start = [string]$s[0].StartMode }) }
    }
}
catch { Note 'services' $_ }

# ---- dcdiag (tests that only read) ------------------------------------------------------------------
# "......................... DC01 passed test Replications" - English output. The lines between
# "Starting test: X" and its result are kept for a failed test (the reason).
function Read-Dcdiag($Out) {
    $tests = @()
    $buf = @()
    foreach ($l in (Lines $Out)) {
        if ($l -match '^\s*Starting test:\s*(\S+)') { $buf = @(); continue }
        if ($l -match '^\s*\.+\s*(\S+)\s+(passed|failed)\s+test\s+(\S+)') {
            $detail = @()
            if ($Matches[2] -eq 'failed') {
                $detail = @($buf | Where-Object { $_.Trim() -and $_ -notmatch '^\s*\*' } | ForEach-Object { $_.Trim() } | Select-Object -Last 12)
            }
            $tests += @(@{ target = $Matches[1]; result = $Matches[2]; test = $Matches[3]; detail = $detail })
            $buf = @()
            continue
        }
        $buf += @($l)
    }
    return , $tests
}
$skip = @(Get-Setting 'dcdiag_skip' @('SystemLog'))
$dc.dcdiag = @{ ran = $false; tests = @(); error = '' }
try {
    $a = @(('/s:' + $dc.name)) + @($skip | Where-Object { $_ } | ForEach-Object { '/skip:' + $_ })
    $out = dcdiag.exe @a 2>&1
    $dc.dcdiag.exit = $LASTEXITCODE
    $dc.dcdiag.tests = Read-Dcdiag $out
    $dc.dcdiag.ran = $true
    if (@($dc.dcdiag.tests).Count -eq 0) { $dc.dcdiag.error = 'no test result in its output (is dcdiag in English?): ' + ((Lines $out | Select-Object -Last 5) -join ' / ') }
}
catch { $dc.dcdiag.error = [string]$_.Exception.Message }
# DNS: these sub-tests only read. Not DnsDynamicUpdate: that one writes a test record.
if ([bool](Get-Setting 'dcdiag_dns' $true)) {
    $dc.dcdiag_dns = @{ ran = $false; tests = @(); error = ''; problems = @() }
    try {
        $out = dcdiag.exe ('/s:' + $dc.name) /test:DNS /DnsBasic /DnsForwarders /DnsDelegation /DnsRecordRegistration 2>&1
        $dc.dcdiag_dns.tests = Read-Dcdiag $out
        $dc.dcdiag_dns.problems = @(Lines $out | Where-Object { $_ -match '^\s*(Error|Warning):' } | ForEach-Object { $_.Trim() } | Select-Object -Unique -First 15)
        $dc.dcdiag_dns.ran = $true
    }
    catch { $dc.dcdiag_dns.error = [string]$_.Exception.Message }
}

# ---- replication: inbound partners of this DC, and its queue ------------------------------------
# A time as repadmin / w32tm print it. A [datetime] cast reads it the US way only; Get-Date reads it
# in the DC's own format (e.g. 15.10.2026 05:59:31), so it is the second try.
function To-Time([string] $s) {
    if (-not $s -or $s -eq '0' -or $s -match '^\s*\(?never\)?\s*$|unspecified') { return '' }
    try { return Iso ([datetime]$s) } catch { }
    try { return Iso (Get-Date -Date $s) } catch { return '' }
}
$dc.repl = @()
try {
    $csv = @(repadmin.exe /showrepl $dc.name /csv 2>&1 | ForEach-Object { [string]$_ })
    $hdr = @($csv | Where-Object { $_ -like 'showrepl_COLUMNS*' })
    if (-not $hdr.Count) { throw ('repadmin gave no table: ' + (($csv | Select-Object -First 3) -join ' / ')) }
    $rows = @(ConvertFrom-Csv -InputObject ($csv | Where-Object { $_ -like 'showrepl_*' }))
    foreach ($r in $rows) {
        if ([string]$r.showrepl_COLUMNS -ne 'showrepl_INFO') { continue }
        $dc.repl += @(@{
                nc = [string]$r.'Naming Context'; source = [string]$r.'Source DSA'; source_site = [string]$r.'Source DSA Site'
                transport = [string]$r.'Transport Type'; failures = [int]('0' + ([string]$r.'Number of Failures' -replace '\D', ''))
                last_failure = To-Time ([string]$r.'Last Failure Time'); last_success = To-Time ([string]$r.'Last Success Time')
                status = [string]$r.'Last Failure Status' })
    }
}
catch { Note 'repadmin /showrepl' $_ }
try {
    $q = Lines (repadmin.exe /queue $dc.name 2>&1)
    $m = @($q | Where-Object { $_ -match 'Queue contains (\d+) items' })
    if ($m.Count -and ($m[0] -match 'Queue contains (\d+) items')) { $dc.queue = [int]$Matches[1] }
}
catch { Note 'repadmin /queue' $_ }

# ---- SYSVOL ---------------------------------------------------------------------------------------
$dc.sysvol = @{ shares = @(); ready = $null; engine = ''; dfsr_state = $null }
try { $dc.sysvol.shares = @(Get-CimInstance -ClassName Win32_Share | Where-Object { @('SYSVOL', 'NETLOGON') -contains [string]$_.Name } | ForEach-Object { [string]$_.Name }) }
catch { Note 'shares' $_ }
try {
    $p = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters' -ErrorAction Stop
    if ($null -ne $p.SysvolReady) { $dc.sysvol.ready = [int]$p.SysvolReady }
    $dc.sysvol.path = [string]$p.SysVol
}
catch { Note 'Netlogon parameters' $_ }
try {
    $rf = @(Get-CimInstance -Namespace 'root/microsoftdfs' -ClassName DfsrReplicatedFolderInfo -Filter "ReplicatedFolderName='SYSVOL Share'" -ErrorAction Stop)
    if ($rf.Count) { $dc.sysvol.engine = 'dfsr'; $dc.sysvol.dfsr_state = [int]$rf[0].State }
}
catch { }
if (-not $dc.sysvol.engine) {
    $frs = @($dc.services | Where-Object { $_.name -eq 'NtFrs' -and $_.state -eq 'Running' })
    $dc.sysvol.engine = $(if ($frs.Count) { 'frs' } else { '' })
}

# ---- event logs: errors, and the events that matter on a DC whatever their level ------------------
$watch = @{
    'Directory Service' = @(467, 1084, 1168, 1311, 1388, 1645, 1865, 1925, 1926, 1988, 2042, 2087, 2088, 2089, 2886, 2887, 2889)
    'DFS Replication'   = @(2104, 2213, 4012, 4614, 5002, 5008, 5014)
    'DNS Server'        = @(4000, 4004, 4013, 4015)
    'System'            = @(5719, 5722, 5723, 5805, 5807, 7, 19, 26, 27, 29, 24, 36, 47, 129, 134, 1030, 1058)
}
# System: only the sources that are about the DC's job (Netlogon, KDC, time, Group Policy)
$sysProviders = @(Get-Setting 'system_providers' @('NETLOGON', 'Microsoft-Windows-Kerberos-Key-Distribution-Center', 'KDC',
        'Microsoft-Windows-Time-Service', 'Microsoft-Windows-GroupPolicy', 'LsaSrv', 'Microsoft-Windows-Security-Kerberos'))
$maxEvents = [int](Get-Setting 'max_events' 2000)
$groups = @{}
$dc.events = @()
foreach ($log in @($watch.Keys)) {
    $evs = @()
    try { $evs = @(Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = $eventSince } -MaxEvents $maxEvents -ErrorAction Stop) }
    catch {
        if ([string]$_.Exception.Message -notmatch 'No events were found|There is not an event log|does not exist') { Note "event log $log" $_ }
        continue
    }
    foreach ($e in $evs) {
        $lvl = [int]$e.Level
        $known = ($watch[$log] -contains [int]$e.Id)
        if (-not $known -and @(1, 2) -notcontains $lvl) { continue }
        if ($log -eq 'System' -and $sysProviders -notcontains [string]$e.ProviderName) { continue }
        $k = '{0}|{1}|{2}' -f $log, $e.ProviderName, $e.Id
        if (-not $groups.ContainsKey($k)) {
            $groups[$k] = @{ log = $log; source = [string]$e.ProviderName; id = [int]$e.Id; level = $lvl; count = 0; last = ''; message = ''; last_time = $null }
        }
        $g = $groups[$k]
        $g.count = $g.count + 1
        if (-not $g.last -or $e.TimeCreated -ge $g.last_time) {
            $g.last_time = $e.TimeCreated
            $g.last = Iso $e.TimeCreated
            $msg = [string]$e.Message
            if ($msg.Length -gt 400) { $msg = $msg.Substring(0, 400) }
            $g.message = ($msg -replace '\s+', ' ').Trim()
        }
        if ($lvl -lt $g.level -and $lvl -gt 0) { $g.level = $lvl }
    }
}
foreach ($k in @($groups.Keys)) {
    $g = $groups[$k]
    $dc.events += @(@{ log = $g.log; source = $g.source; id = $g.id; level = $g.level; count = $g.count; last = $g.last; message = $g.message })
}

# ---- clients in no AD site (netlogon.log NO_CLIENT_SITE) -----------------------------------------------
$dc.no_client_site = @{ count = 0; clients = @(); error = '' }
try {
    $nl = Join-Path $env:SystemRoot 'debug\netlogon.log'
    if (Test-Path -Path $nl) {
        $ips = @{}
        $year = $now.Year
        foreach ($l in @(Get-Content -Path $nl -Tail 20000 -ErrorAction Stop)) {
            if ($l -notmatch '^(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d) .*NO_CLIENT_SITE:\s*(\S+)\s+(\S+)') { continue }
            $t = $null
            try { $t = Get-Date -Year $year -Month ([int]$Matches[1]) -Day ([int]$Matches[2]) -Hour ([int]$Matches[3]) -Minute ([int]$Matches[4]) -Second ([int]$Matches[5]) } catch { continue }
            if ($t -gt $now.AddDays(1)) { $t = $t.AddYears(-1) }
            if ($t -lt $eventSince) { continue }
            $dc.no_client_site.count = $dc.no_client_site.count + 1
            $ips[$Matches[7]] = $Matches[6]
        }
        $dc.no_client_site.clients = @($ips.Keys | Sort-Object | Select-Object -First 50 | ForEach-Object { '{0} ({1})' -f $_, $ips[$_] })
        $dc.no_client_site.distinct = $ips.Count
    }
}
catch { $dc.no_client_site.error = [string]$_.Exception.Message }

# ---- time --------------------------------------------------------------------------------------------
$dc.time = @{ source = ''; stratum = ''; last_sync = ''; offset = $null; ref = ''; error = '' }
try {
    $st = Lines (w32tm.exe /query /status 2>&1)
    foreach ($l in $st) {
        if ($l -match '^\s*Source:\s*(.+)$') { $dc.time.source = $Matches[1].Trim() }
        elseif ($l -match '^\s*Stratum:\s*(\d+)') { $dc.time.stratum = $Matches[1] }
        elseif ($l -match '^\s*Last Successful Sync Time:\s*(.+)$') { $dc.time.last_sync = To-Time $Matches[1].Trim() }
    }
    if (-not $dc.time.source) { $dc.time.error = 'w32tm /query /status: ' + (($st | Select-Object -First 2) -join ' / ') }
}
catch { $dc.time.error = [string]$_.Exception.Message }
# The offset: a DC against the PDC emulator (the domain's time source); the PDC against its NTP source.
$ref = ''
if ($dc.role -eq 'pdc') {
    $src = ($dc.time.source -replace ',0x[0-9a-fA-F]+$', '').Trim()
    if ($src -and $src -notmatch 'Local CMOS Clock|Free-running System Clock|VM IC Time') { $ref = $src }
}
elseif ($adModule) {
    try { $ref = [string](Get-ADDomain -Server localhost).PDCEmulator } catch { Note 'PDC emulator' $_ }
}
if ($ref) {
    $dc.time.ref = $ref
    try {
        $sc = Lines (w32tm.exe /stripchart ('/computer:' + $ref) /samples:1 /dataonly 2>&1)
        $o = @($sc | Where-Object { $_ -match ',\s*([+-]?\d+(\.\d+)?)s\s*$' })
        if ($o.Count -and ($o[-1] -match ',\s*([+-]?\d+(\.\d+)?)s\s*$')) { $dc.time.offset = [double]$Matches[1] }
        else { $dc.time.offset_error = (($sc | Select-Object -Last 2) -join ' / ') }
    }
    catch { $dc.time.offset_error = [string]$_.Exception.Message }
}

# ---- disks of the database, its logs and SYSVOL ---------------------------------------------------------
$dc.disks = @()
try {
    $ntds = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\NTDS\Parameters' -ErrorAction Stop
    $where = @(@('database', [string]$ntds.'DSA Database file'), @('logs', [string]$ntds.'Database log files path'), @('sysvol', [string]$dc.sysvol.path))
    $vols = @(Get-CimInstance -ClassName Win32_LogicalDisk)
    foreach ($w in $where) {
        if (-not $w[1] -or $w[1].Length -lt 2) { continue }
        $drive = $w[1].Substring(0, 2).ToUpper()
        $v = @($vols | Where-Object { ([string]$_.DeviceID).ToUpper() -eq $drive })
        if (-not $v.Count -or -not [double]$v[0].Size) { continue }
        # rounded to one decimal with integer casts: [math] is not allowed in Constrained Language Mode
        $dc.disks += @(@{ what = $w[0]; path = $w[1]; drive = $drive; free_pct = [int64](1000.0 * [double]$v[0].FreeSpace / [double]$v[0].Size) / 10
                free_gb = [int64](10.0 * [double]$v[0].FreeSpace / 1073741824) / 10 })
    }
}
catch { Note 'database / log / SYSVOL disks' $_ }

# ---- certificates LDAPS and Kerberos (KDC) use ------------------------------------------------------------
$dc.certs = @()
try {
    foreach ($c in @(Get-ChildItem -Path 'Cert:\LocalMachine\My' -ErrorAction Stop)) {
        if (-not $c.HasPrivateKey) { continue }
        $oids = @($c.EnhancedKeyUsageList | ForEach-Object { [string]$_.ObjectId })
        $purposes = @()
        if ($oids -contains '1.3.6.1.5.5.7.3.1') { $purposes += @('ldaps') }
        if ($oids -contains '1.3.6.1.5.2.3.5') { $purposes += @('kdc') }
        if (-not $purposes.Count) { continue }
        $dns = @($c.DnsNameList | ForEach-Object { [string]$_.Unicode })
        $dc.certs += @(@{ subject = [string]$c.Subject; thumbprint = [string]$c.Thumbprint; not_after = Iso $c.NotAfter; purposes = $purposes; dns = $dns })
    }
}
catch { Note 'certificates' $_ }

# ---- the Security log: who made the changes (only when auditing records them) -------------------------------
# Account management and group membership events. Their fields (Properties) by position:
#   4720/4722/4725/4726/4767/4741/4743: 0 target, 1 target domain, 4 by, 5 by domain
#   4728/4729/4732/4733/4756/4757: 0 member DN, 2 group, 3 group domain, 6 by, 7 by domain
#   4706/4707 (trust): 0 trusted domain, 3 by, 4 by domain;  1102 (log cleared), 4794 (DSRM password): 1 by, 2 by domain
$dc.security = @()
if ([bool](Get-Setting 'security_events' $true)) {
    $ids = @(4720, 4722, 4725, 4726, 4741, 4743, 4767, 4728, 4729, 4732, 4733, 4756, 4757, 4706, 4707, 4739, 4794, 1102)
    try {
        $evs = @(Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = $ids; StartTime = $since } -MaxEvents $maxEvents -ErrorAction Stop)
        foreach ($e in $evs) {
            $p = @($e.Properties | ForEach-Object { [string]$_.Value })
            $id = [int]$e.Id
            $r = @{ id = $id; time = Iso $e.TimeCreated; target = ''; member = ''; group = ''; by = '' }
            if (@(4728, 4729, 4732, 4733, 4756, 4757) -contains $id) {
                if ($p.Count -gt 7) { $r.member = $p[0]; $r.group = $p[2]; $r.by = '{0}\{1}' -f $p[7], $p[6] }
            }
            elseif (@(4706, 4707) -contains $id) {
                if ($p.Count -gt 4) { $r.target = $p[0]; $r.by = '{0}\{1}' -f $p[4], $p[3] }
            }
            elseif (@(1102, 4794) -contains $id) {
                if ($p.Count -gt 2) { $r.by = '{0}\{1}' -f $p[2], $p[1] }
            }
            elseif ($id -eq 4739) {
                if ($p.Count -gt 5) { $r.target = $p[1]; $r.by = '{0}\{1}' -f $p[5], $p[4] }
            }
            elseif ($p.Count -gt 5) { $r.target = $p[0]; $r.by = '{0}\{1}' -f $p[5], $p[4] }
            $dc.security += @($r)
        }
        $dc.security_capped = ($evs.Count -ge $maxEvents)
    }
    catch {
        if ([string]$_.Exception.Message -notmatch 'No events were found') { Note 'Security log' $_ }
    }
}

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ dc = $dc } -Depth 8 -Compress)
