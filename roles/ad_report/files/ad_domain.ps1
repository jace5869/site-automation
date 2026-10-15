# site-automation - Active Directory health report: the domain-wide part (roles/ad_report, run by
# playbooks/ad_health_report.yml on ONE domain controller per domain - the PDC emulator when it is
# in the inventory). Read-only: LDAP reads of this DC's copy of the directory, replication
# metadata, repadmin /showutdvec and /showbackup, DNS lookups, a TCP connect to each role holder,
# nltest /sc_verify for the trusts. It changes nothing. Everything is read from THIS DC (localhost):
# AD is replicated, so its copy has every object - no second hop to another server.
# Facts only: plugins/filter/ad_filters.py decides what is a problem.
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
$maxChanges = [int](Get-Setting 'changes_max' 500)
$now = Get-Date
$since = $now.AddHours(-$hours)
# LDAP generalized time for whenCreated / whenChanged filters
$gen = $since.ToUniversalTime().ToString('yyyyMMddHHmmss') + '.0Z'
$D = @{ errors = @(); hours = $hours }
function Iso($t) {
    if ($null -eq $t) { return '' }
    try {
        $d = [datetime]$t
        if ($d.Year -le 1601) { return '' }
        return $d.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    catch { return '' }
}
function Note([string] $What, $Err) {
    $msg = [string]$Err
    if ($Err -and $Err.Exception) { $msg = [string]$Err.Exception.Message }
    $script:D.errors += @(('{0}: {1}' -f $What, $msg.Trim()))
}
function Lines($Out) { return @($Out | ForEach-Object { [string]$_ }) }
function Recent($t) { return ($null -ne $t -and ([datetime]$t) -ge $since) }
function Short([string] $Dn) {
    if ($Dn -match '^(CN|OU|DC)=((\\,|[^,])+)') { return ($Matches[2] -replace '\\,', ',') }
    return $Dn
}
$D.read_at = Iso $now

try { Import-Module ActiveDirectory -ErrorAction Stop -WarningAction SilentlyContinue }
catch {
    Note 'ActiveDirectory PowerShell module' $_
    '###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ domain = $D } -Depth 8 -Compress)
    exit 0
}
$OnDc = @{ Server = 'localhost' }   # every AD cmdlet asks this DC (splatted: @OnDc)

# ---- domain, forest, functional levels, schema --------------------------------------------------------
$dom = $null
try {
    $dom = Get-ADDomain @OnDc
    $root = Get-ADRootDSE @OnDc
    $D.dns = [string]$dom.DNSRoot
    $D.netbios = [string]$dom.NetBIOSName
    $D.dn = [string]$dom.DistinguishedName
    $D.sid = [string]$dom.DomainSID
    $D.mode = [string]$dom.DomainMode
    $D.config_nc = [string]$root.configurationNamingContext
    $D.schema_nc = [string]$root.schemaNamingContext
    $D.collector = [string]$root.dnsHostName
}
catch {
    Note 'Get-ADDomain' $_
    '###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ domain = $D } -Depth 8 -Compress)
    exit 0
}
$D.forest = @{}
try {
    $f = Get-ADForest @OnDc
    $D.forest = @{ name = [string]$f.Name; mode = [string]$f.ForestMode; root_domain = [string]$f.RootDomain
        domains = @($f.Domains | ForEach-Object { [string]$_ }); schema_master = [string]$f.SchemaMaster; naming_master = [string]$f.DomainNamingMaster }
}
catch { Note 'Get-ADForest' $_ }
$isRoot = ($D.forest.root_domain -and $D.forest.root_domain -eq $D.dns)
$D.schema = @{ version = $null; changed = ''; new_objects = @() }
try {
    $sc = Get-ADObject -Identity $D.schema_nc -Properties objectVersion @OnDc
    $D.schema.version = [int]$sc.objectVersion
    $md = @(Get-ADReplicationAttributeMetadata -Object $D.schema_nc -Properties objectVersion @OnDc)
    if ($md.Count) { $D.schema.changed = Iso $md[0].LastOriginatingChangeTime }
    $D.schema.new_objects = @(Get-ADObject -SearchBase $D.schema_nc -SearchScope OneLevel -LDAPFilter "(whenCreated>=$gen)" -Properties whenCreated, lDAPDisplayName @OnDc |
        Select-Object -First $maxChanges | ForEach-Object { @{ name = [string]$_.lDAPDisplayName; class = [string]$_.ObjectClass; time = Iso $_.whenCreated } })
}
catch { Note 'schema' $_ }

# ---- domain controllers, FSMO role holders ------------------------------------------------------------
$D.dcs = @()
try {
    foreach ($x in @(Get-ADDomainController -Filter * @OnDc)) {
        $D.dcs += @(@{ name = ([string]$x.HostName).ToLower(); site = [string]$x.Site; ip = [string]$x.IPv4Address; gc = [bool]$x.IsGlobalCatalog
                rodc = [bool]$x.IsReadOnly; os = [string]$x.OperatingSystem; ntds = [string]$x.NTDSSettingsObjectDN; roles = @($x.OperationMasterRoles | ForEach-Object { [string]$_ }) })
    }
}
catch { Note 'Get-ADDomainController -Filter *' $_ }
$holders = @(
    @('PDC emulator', [string]$dom.PDCEmulator), @('RID master', [string]$dom.RIDMaster), @('Infrastructure master', [string]$dom.InfrastructureMaster))
if ($isRoot) { $holders += @(@('Schema master', $D.forest.schema_master), @('Domain naming master', $D.forest.naming_master)) }
$D.fsmo = @()
$tested = @{}
foreach ($h in $holders) {
    $name = ([string]$h[1]).ToLower()
    if ($name -and -not $tested.ContainsKey($name)) {
        $ok = $null
        try { $ok = [bool](Test-NetConnection -ComputerName $name -Port 389 -InformationLevel Quiet -WarningAction SilentlyContinue) } catch { $ok = $null }
        $tested[$name] = $ok
    }
    $D.fsmo += @(@{ role = $h[0]; holder = $name; reachable = $(if ($name) { $tested[$name] } else { $null }) })
}

# ---- RID pool, tombstone lifetime, Recycle Bin, krbtgt -----------------------------------------------------
$D.rid = @{}
try {
    $rm = Get-ADObject -Identity ('CN=RID Manager$,CN=System,' + $D.dn) -Properties rIDAvailablePool @OnDc
    # high 32 bits: the RIDs there are in all; low 32 bits: the next free one ([math] is not allowed in Constrained Language Mode)
    $pool = [int64]$rm.rIDAvailablePool
    $hi = $pool -shr 32
    $D.rid = @{ issued = [int64]($pool - $hi * 4294967296); total = $hi }
}
catch { Note 'RID pool' $_ }
$D.tombstone_days = $null
try {
    $ds = Get-ADObject -Identity ('CN=Directory Service,CN=Windows NT,CN=Services,' + $D.config_nc) -Properties tombstoneLifetime @OnDc
    if ($ds.tombstoneLifetime) { $D.tombstone_days = [int]$ds.tombstoneLifetime }
}
catch { Note 'tombstone lifetime' $_ }
$D.recycle_bin = $null
try {
    $rb = @(Get-ADOptionalFeature -Filter "Name -like 'Recycle Bin Feature'" @OnDc)
    if ($rb.Count) { $D.recycle_bin = (@($rb[0].EnabledScopes).Count -gt 0) }
}
catch { Note 'Recycle Bin' $_ }
$D.krbtgt = @()
try {
    $D.krbtgt = @(Get-ADUser -Filter "SamAccountName -like 'krbtgt*'" -Properties PasswordLastSet @OnDc |
        ForEach-Object { @{ name = [string]$_.SamAccountName; pwd_last_set = Iso $_.PasswordLastSet } })
}
catch { Note 'krbtgt' $_ }

# ---- replication seen from here: every DC's last change reached this DC (up-to-dateness vector) ----------------
# "Site\DC02 @ USN 123 @ Time 2026-10-15 05:59:31". Retired entries (GUIDs, deleted DSAs) are left out
# by the report: only current DC names are matched.
$D.utdv = @()
try {
    foreach ($l in (Lines (repadmin.exe /showutdvec localhost $D.dn /latency 2>&1))) {
        if ($l -match '^\s*(\S.*?)\s+@\s*USN\s+\d+\s+@\s*Time\s+(.+?)\s*$') {
            $t = ''
            $when = $Matches[2]
            try { $t = Iso ([datetime]$when) } catch { try { $t = Iso (Get-Date -Date $when) } catch { } }
            $D.utdv += @(@{ dsa = $Matches[1].Trim(); last = $t })
        }
    }
}
catch { Note 'repadmin /showutdvec' $_ }
# Last backup of each partition: the dSASignature of the partition head (repadmin /showbackup).
$D.backups = @()
try {
    $part = ''
    foreach ($l in (Lines (repadmin.exe /showbackup localhost 2>&1))) {
        if ($l -match '^(DC|CN)=\S') { $part = $l.Trim(); continue }
        if ($part -and $l -match '(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+\d+\s+dSASignature') {
            $D.backups += @(@{ partition = $part; last = Iso ([datetime]$Matches[1]) })
            $part = ''
        }
    }
}
catch { Note 'repadmin /showbackup' $_ }
$D.sysvol_migration = ''
try {
    $g = Lines (dfsrmig.exe /getglobalstate 2>&1)
    $m = @($g | Where-Object { $_ -match "state:\s*'([^']+)'" })
    if ($m.Count -and ($m[0] -match "state:\s*'([^']+)'")) { $D.sysvol_migration = $Matches[1] }
}
catch { }

# ---- DNS: every DC registered its SRV records ---------------------------------------------------------------
$D.dns_srv = @()
$records = @(('_ldap._tcp.dc._msdcs.' + $D.dns), ('_kerberos._tcp.dc._msdcs.' + $D.dns))
if ($D.forest.name) { $records += @('_ldap._tcp.gc._msdcs.' + $D.forest.name) }
foreach ($r in $records) {
    try {
        $t = @(Resolve-DnsName -Name $r -Type SRV -DnsOnly -ErrorAction Stop | Where-Object { $_.NameTarget } | ForEach-Object { ([string]$_.NameTarget).ToLower().TrimEnd('.') })
        $D.dns_srv += @(@{ record = $r; targets = $t; error = '' })
    }
    catch { $D.dns_srv += @(@{ record = $r; targets = @(); error = [string]$_.Exception.Message }) }
}

# ---- trusts ------------------------------------------------------------------------------------------------
$D.trusts = @()
try {
    foreach ($t in @(Get-ADTrust -Filter * -Properties whenCreated, whenChanged @OnDc)) {
        $x = @{ name = [string]$t.Name; direction = [string]$t.Direction; type = [string]$t.TrustType; forest = [bool]$t.ForestTransitive
            selective = [bool]$t.SelectiveAuthentication; quarantined = [bool]$t.SIDFilteringQuarantined
            created = Iso $t.whenCreated; changed = Iso $t.whenChanged; verify = '' }
        if ([bool](Get-Setting 'trust_verify' $true) -and @('Outbound', 'BiDirectional') -contains $x.direction) {
            try {
                $v = Lines (nltest.exe ('/sc_verify:' + $x.name) 2>&1)
                $s = @($v | Where-Object { $_ -match 'Trust Verification Status = |Connection Status Status = ' })
                $x.verify = $(if ($s.Count) { ($s -join ' / ').Trim() } else { (($v | Select-Object -First 2) -join ' / ').Trim() })
            }
            catch { $x.verify = 'nltest: ' + [string]$_.Exception.Message }
        }
        $D.trusts += @($x)
    }
}
catch { Note 'trusts' $_ }

# ---- sites and subnets; changes to sites, subnets, site links, DCs (configuration) -----------------------------
$D.sites = @()
try {
    $subnets = @(Get-ADReplicationSubnet -Filter * -Properties Site @OnDc)
    foreach ($s in @(Get-ADReplicationSite -Filter * @OnDc)) {
        $sn = [string]$s.Name
        $D.sites += @(@{ name = $sn; dcs = @($D.dcs | Where-Object { $_.site -eq $sn }).Count
                subnets = @($subnets | Where-Object { (Short ([string]$_.Site)) -eq $sn }).Count })
    }
    $D.subnets_without_site = @($subnets | Where-Object { -not $_.Site } | ForEach-Object { [string]$_.Name })
}
catch { Note 'sites and subnets' $_ }
$D.config_changes = @()
try {
    $cls = '(|(objectClass=site)(objectClass=subnet)(objectClass=siteLink)(objectClass=nTDSDSA)(objectClass=server)(objectClass=crossRef))'
    foreach ($o in @(Get-ADObject -SearchBase $D.config_nc -LDAPFilter "(&$cls(|(whenCreated>=$gen)(whenChanged>=$gen)))" -Properties whenCreated, whenChanged @OnDc | Select-Object -First $maxChanges)) {
        $name = $(if ([string]$o.ObjectClass -eq 'nTDSDSA') { Short ($o.DistinguishedName -replace '^CN=NTDS Settings[^,]*,', '') } else { [string]$o.Name })
        $D.config_changes += @(@{ class = [string]$o.ObjectClass; name = $name; change = $(if (Recent $o.whenCreated) { 'created' } else { 'changed' })
                time = Iso $(if (Recent $o.whenCreated) { $o.whenCreated } else { $o.whenChanged }) })
    }
    foreach ($o in @(Get-ADObject -SearchBase $D.config_nc -IncludeDeletedObjects -LDAPFilter "(&(isDeleted=TRUE)(whenChanged>=$gen)$cls)" -Properties whenChanged, 'msDS-LastKnownRDN', lastKnownParent @OnDc | Select-Object -First $maxChanges)) {
        $name = [string]$o.'msDS-LastKnownRDN'
        if ([string]$o.ObjectClass -eq 'nTDSDSA') { $name = Short ([string]$o.lastKnownParent) }
        $D.config_changes += @(@{ class = [string]$o.ObjectClass; name = $name; change = 'deleted'; time = Iso $o.whenChanged })
    }
}
catch { Note 'configuration changes' $_ }

# ---- privileged groups: members now, and who was added or removed (replication metadata of `member`) ---------------
# The metadata has every value's add/remove time and the DC it was made on, with no auditing needed.
$rids = @(@('Domain Admins', '512'), @('Group Policy Creator Owners', '520'), @('Key Admins', '526'), @('Cert Publishers', '517'))
if ($isRoot) { $rids += @(@('Enterprise Admins', '519'), @('Schema Admins', '518'), @('Enterprise Key Admins', '527')) }
$sids = @()
foreach ($r in $rids) { $sids += @($D.sid + '-' + $r[1]) }
$sids += @('S-1-5-32-544', 'S-1-5-32-548', 'S-1-5-32-549', 'S-1-5-32-550', 'S-1-5-32-551')
$names = @(@(Get-Setting 'privileged_groups' @('DnsAdmins')) | Where-Object { $_ })
$D.groups = @()
$seen = @{}
foreach ($id in @($sids + $names)) {
    $g = $null
    try { $g = Get-ADGroup -Identity $id -Properties member @OnDc }
    catch {
        if ($names -contains $id) { Note "group $id" $_ }
        continue
    }
    if ($seen.ContainsKey([string]$g.DistinguishedName)) { continue }
    $seen[[string]$g.DistinguishedName] = $true
    $x = @{ name = [string]$g.Name; sam = [string]$g.SamAccountName; sid = [string]$g.SID; members = @($g.member).Count
        member_names = @(@($g.member) | Select-Object -First 25 | ForEach-Object { Short ([string]$_) }); changes = @() }
    try {
        foreach ($m in @(Get-ADReplicationAttributeMetadata -Object $g.DistinguishedName -Properties member -ShowAllLinkedValues @OnDc)) {
            if (-not $m.IsLinkValue) { continue }
            $del = Iso $m.LastOriginatingDeleteTime
            $chg = $m.LastOriginatingChangeTime
            if (-not (Recent $chg)) { continue }
            $x.changes += @(@{ member = [string]$m.AttributeValue; action = $(if ($del) { 'removed' } else { 'added' }); time = Iso $chg
                    dc = Short ((([string]$m.LastOriginatingChangeDirectoryServerIdentity) -replace '^CN=NTDS Settings[^,]*,', '')) })
        }
    }
    catch { Note ('membership changes of ' + $x.name) $_ }
    $D.groups += @($x)
}

# ---- users, groups and computers: created, deleted, enabled / disabled / unlocked ---------------------------------
$D.created = @()
try {
    $D.created = @(Get-ADObject -LDAPFilter "(&(|(objectCategory=person)(objectClass=group)(objectClass=computer))(whenCreated>=$gen))" -Properties whenCreated, sAMAccountName @OnDc |
        Select-Object -First $maxChanges | ForEach-Object { @{ class = [string]$_.ObjectClass; name = [string]$_.sAMAccountName; dn = [string]$_.DistinguishedName; time = Iso $_.whenCreated } })
}
catch { Note 'objects created' $_ }
$D.deleted = @()
try {
    $D.deleted = @(Get-ADObject -IncludeDeletedObjects -LDAPFilter "(&(isDeleted=TRUE)(whenChanged>=$gen)(|(objectClass=user)(objectClass=group)(objectClass=computer)))" -Properties whenChanged, 'msDS-LastKnownRDN', lastKnownParent, sAMAccountName @OnDc |
        Select-Object -First $maxChanges | ForEach-Object { @{ class = [string]$_.ObjectClass; name = $(if ($_.sAMAccountName) { [string]$_.sAMAccountName } else { [string]$_.'msDS-LastKnownRDN' })
                parent = [string]$_.lastKnownParent; time = Iso $_.whenChanged } })
}
catch { Note 'objects deleted (reading Deleted Objects needs Domain Admins or delegated rights)' $_ }
$D.account_changes = @()
try {
    $changed = @(Get-ADObject -LDAPFilter "(&(|(objectCategory=person)(objectClass=computer))(whenChanged>=$gen))" -Properties userAccountControl, lockoutTime, sAMAccountName, whenCreated @OnDc | Select-Object -First $maxChanges)
    foreach ($o in $changed) {
        if (Recent $o.whenCreated) { continue }
        foreach ($m in @(Get-ADReplicationAttributeMetadata -Object $o.DistinguishedName -Properties userAccountControl, lockoutTime @OnDc)) {
            if (-not (Recent $m.LastOriginatingChangeTime)) { continue }
            $what = ''
            if ([string]$m.AttributeName -eq 'userAccountControl') { $what = $(if (([int]$o.userAccountControl -band 2) -eq 2) { 'disabled' } else { 'enabled' }) }
            elseif ([string]$m.AttributeName -eq 'lockoutTime') { $what = $(if ([int64]('0' + [string]$o.lockoutTime) -eq 0) { 'unlocked' } else { 'locked out' }) }
            if ($what) {
                $D.account_changes += @(@{ class = [string]$o.ObjectClass; name = [string]$o.sAMAccountName; change = $what; time = Iso $m.LastOriginatingChangeTime
                        dc = Short ((([string]$m.LastOriginatingChangeDirectoryServerIdentity) -replace '^CN=NTDS Settings[^,]*,', '')) })
            }
        }
    }
    $D.changes_capped = ($changed.Count -ge $maxChanges)
}
catch { Note 'account changes' $_ }
$D.locked_out = @()
try { $D.locked_out = @(Search-ADAccount -LockedOut @OnDc | Select-Object -First 100 | ForEach-Object { [string]$_.SamAccountName }) }
catch { Note 'locked-out accounts' $_ }

# ---- Group Policy: versions in AD against SYSVOL, orphans, created / changed / deleted, links changed ----------------
$D.gpos = @()
$D.orphan_folders = @()
try {
    $gpos = @(Get-ADObject -SearchBase ('CN=Policies,CN=System,' + $D.dn) -SearchScope OneLevel -LDAPFilter '(objectClass=groupPolicyContainer)' -Properties displayName, whenCreated, whenChanged, versionNumber, flags @OnDc)
    $sysvol = ''
    try { $sysvol = [string](Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters' -ErrorAction Stop).SysVol } catch { }
    $pol = $(if ($sysvol) { Join-Path (Join-Path $sysvol $D.dns) 'Policies' } else { '' })
    $folders = @()
    if ($pol -and (Test-Path -Path $pol)) { $folders = @(Get-ChildItem -Path $pol -Directory -ErrorAction Stop | Where-Object { $_.Name -match '^\{[0-9A-Fa-f-]{36}\}$' } | ForEach-Object { ([string]$_.Name).ToUpper() }) }
    foreach ($g in $gpos) {
        $guid = ([string]$g.Name).ToUpper()
        $sv = $null
        $ini = $(if ($pol) { Join-Path (Join-Path $pol $guid) 'GPT.INI' } else { '' })
        if ($ini -and (Test-Path -Path $ini)) {
            foreach ($l in @(Get-Content -Path $ini -ErrorAction Stop)) { if ($l -match '^\s*Version\s*=\s*(\d+)') { $sv = [int64]$Matches[1] } }
        }
        $D.gpos += @(@{ guid = $guid; name = [string]$g.displayName; created = Iso $g.whenCreated; changed = Iso $g.whenChanged
                ad_version = [int64]$g.versionNumber; sysvol_version = $sv; folder = ($folders -contains $guid); flags = [int]$g.flags })
    }
    $D.sysvol_read = [bool]$folders.Count
    $known = @($gpos | ForEach-Object { ([string]$_.Name).ToUpper() })
    $D.orphan_folders = @($folders | Where-Object { $known -notcontains $_ })
}
catch { Note 'Group Policy objects' $_ }
$D.gpo_deleted = @()
try {
    $D.gpo_deleted = @(Get-ADObject -IncludeDeletedObjects -LDAPFilter "(&(isDeleted=TRUE)(objectClass=groupPolicyContainer)(whenChanged>=$gen))" -Properties whenChanged, displayName, 'msDS-LastKnownRDN' @OnDc |
        ForEach-Object { @{ name = [string]$_.displayName; guid = [string]$_.'msDS-LastKnownRDN'; time = Iso $_.whenChanged } })
}
catch { }
$D.gpo_links = @()
try {
    $targets = @(Get-ADObject -LDAPFilter "(&(|(objectClass=organizationalUnit)(objectClass=domainDNS))(whenChanged>=$gen))" -Properties gPLink @OnDc | Select-Object -First $maxChanges)
    $targets += @(Get-ADObject -SearchBase ('CN=Sites,' + $D.config_nc) -SearchScope OneLevel -LDAPFilter "(&(objectClass=site)(whenChanged>=$gen))" -Properties gPLink @OnDc | Select-Object -First $maxChanges)
    foreach ($o in $targets) {
        $md = @(Get-ADReplicationAttributeMetadata -Object $o.DistinguishedName -Properties gPLink @OnDc | Where-Object { Recent $_.LastOriginatingChangeTime })
        if (-not $md.Count) { continue }
        $links = @()
        foreach ($m in [regex]::Matches([string]$o.gPLink, 'cn=(\{[0-9A-Fa-f-]{36}\})', 'IgnoreCase')) { $links += @(([string]$m.Groups[1].Value).ToUpper()) }
        $D.gpo_links += @(@{ target = [string]$o.DistinguishedName; time = Iso $md[0].LastOriginatingChangeTime; links = $links
                dc = Short ((([string]$md[0].LastOriginatingChangeDirectoryServerIdentity) -replace '^CN=NTDS Settings[^,]*,', '')) })
    }
}
catch { Note 'Group Policy links' $_ }

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ domain = $D } -Depth 8 -Compress)
