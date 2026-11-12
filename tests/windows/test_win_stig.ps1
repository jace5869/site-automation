# Tests for the Windows STIG scan (roles/win_check_stig/files/win_stig.ps1) with a stand-in Evaluate-STIG
# (tests/windows/stig/Evaluate-STIG.ps1): run (in this session; on Windows as administrator also as SYSTEM in a
# scheduled task), the history kept per checklist (the result before, when a rule last passed, keep), a dry run (save
# false), the arguments given to Evaluate-STIG, CKL, checklists its schema check rejected, no checklist, a timeout,
# found on the drives or not (run as SYSTEM only when nobody else can change it), its own folder refused when others
# can change it, read what it wrote (this server's newest, not its Previous, not another server's;
# auto), the STIG recheck (only the rules asked for, -SelectVuln or not, no history), and Constrained Language Mode. Windows PowerShell 5.1 and PowerShell 7, on Windows and on Linux:
#   pwsh -File tests/windows/test_win_stig.ps1         powershell -File tests\windows\test_win_stig.ps1
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$script = Join-Path $root 'roles/win_check_stig/files/win_stig.ps1'
$shell = (Get-Process -Id $PID).Path
$onWindows = ($env:OS -eq 'Windows_NT')
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('sa-win-stig-' + (Get-Random))
$tools = Join-Path $tmp 'tools'
$es = Join-Path $tools 'Evaluate-STIG'
New-Item -ItemType Directory -Path $es -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'stig/Evaluate-STIG.ps1') -Destination $es
$wrapper = Join-Path $tmp 'clm.ps1'
Set-Content -LiteralPath $wrapper -Value @'
param([string] $Script)
$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
& $Script
'@
# runs the scan with stand-in permissions: Get-Acl gives each path's SDDL from $env:SA_TEST_ACL (JSON, path -> SDDL;
# any other path: SYSTEM and the Administrators only), whoami the job's account
$aclWrapper = Join-Path $tmp 'acl.ps1'
Set-Content -LiteralPath $aclWrapper -Value @'
param([string] $Script, [switch] $Clm)
$aclMap = @{}
if ($env:SA_TEST_ACL) { foreach ($p in @((ConvertFrom-Json -InputObject $env:SA_TEST_ACL).PSObject.Properties)) { $aclMap[$p.Name] = [string]$p.Value } }
function Get-Acl {
    param([string] $LiteralPath, [string] $Path)
    $p = $LiteralPath; if (-not $p) { $p = $Path }
    $s = 'O:BAG:SYD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)'
    if ($aclMap.ContainsKey($p)) { $s = $aclMap[$p] }
    return [pscustomobject]@{ Sddl = $s }
}
function whoami.exe { $global:LASTEXITCODE = 0; '"host01\svc_aap","S-1-5-21-1-2-3-1500"' }
if ($Clm) { $ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage' }
& $Script
'@
$fail = 0
$count = 0

function TRule([string] $Sv, [string] $Sev, [string] $Status, [string] $Details = '') {
    return @{ sv = $Sv; v = ('V-' + $Sv.Substring(3)); stigid = ('WN22-00-' + $Sv.Substring(3)); sev = $Sev; title = ('Rule ' + $Sv + ' must be set.'); status = $Status; details = $Details }
}
function Ws([string] $First = 'pass') {
    return @{ file = 'WIN01_WinServer2022_V2R4_20261030'; stig_id = 'Windows_Server_2022_STIG'; display_name = 'Microsoft Windows Server 2022'
              version = '2'; release = 'Release: 4 Benchmark Date: 02 Jul 2026'
              rules = @((TRule 'SV-254238' 'high' $First 'Found: it is set.'), (TRule 'SV-254239' 'medium' 'fail' 'Found: LegalNoticeText is empty.'),
                        (TRule 'SV-254240' 'medium' 'nr'), (TRule 'SV-254241' 'low' 'na')) }
}
function Iis {
    return @{ file = 'WIN01_IIS10Site_Default_Web_Site_V2R9_20261030'; stig_id = 'IIS_10-0_Site_STIG'; display_name = 'Microsoft IIS 10.0 Site'
              version = '2'; release = 'Release: 9 Benchmark Date: 02 Jul 2026'; site = 'Default Web Site'; instance = ''
              rules = @((TRule 'SV-218735' 'medium' 'pass'), (TRule 'SV-218736' 'high' 'fail' 'Found: directory browsing is on.')) }
}
function Spec($Checklists, [int] $Exit = 0, [int] $Sleep = 0, [string] $Reject = '', [string[]] $Say = @(), [switch] $Wrap, [switch] $VulnNeedsStig) {
    $s = @{ host = 'WIN01'; exit = $Exit; sleep = $Sleep; reject = $Reject; say = $Say; wrap = [bool]$Wrap; vuln_needs_stig = [bool]$VulnNeedsStig; checklists = @($Checklists) }
    Set-Content -LiteralPath (Join-Path $es 'spec.json') -Value (ConvertTo-Json -InputObject $s -Depth 6) -Encoding UTF8
}
# run the script with these settings -> its facts (or $null)
# -Acl: with the stand-in permissions ($Acl: path -> SDDL)
function Scan($Cfg, [switch] $Clm, $Acl) {
    $c = @{ host = 'WIN01'; run_as = 'direct'; dir = (Join-Path $tmp 'kept'); evaluate = $es; timeout = 60 }
    foreach ($k in $Cfg.Keys) { $c[$k] = $Cfg[$k] }
    $env:SITE_CHECK_CONFIG = ConvertTo-Json -InputObject $c -Depth 4 -Compress
    if ($null -ne $Acl) {
        $env:SA_TEST_ACL = ConvertTo-Json -InputObject $Acl -Compress
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $aclWrapper, '-Script', $script)
        if ($Clm) { $a += '-Clm' }
        $out = @(& $shell @a 2>&1 | ForEach-Object { [string]$_ })
        Remove-Item Env:\SA_TEST_ACL -ErrorAction SilentlyContinue
    }
    elseif ($Clm) { $out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File $wrapper -Script $script 2>&1 | ForEach-Object { [string]$_ }) }
    else { $out = @(& $shell -NoProfile -ExecutionPolicy Bypass -File $script 2>&1 | ForEach-Object { [string]$_ }) }
    $line = @($out | Where-Object { $_ -like '###SITE-JSON### *' }) | Select-Object -Last 1
    if (-not $line) { Write-Host '       no result line:'; $out | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }; return $null }
    $r = ConvertFrom-Json -InputObject $line.Substring(16)
    if (@($r.findings | Where-Object { $_ }).Count) { Write-Host '       findings from the scan script (expected none)'; return $null }
    return $r.facts
}
function Check([string] $Name, [bool] $Ok, $F) {
    $script:count++
    if ($Ok) { Write-Host "ok   - $Name" }
    else {
        $script:fail++
        Write-Host "FAIL - $Name"
        if ($null -ne $F) { Write-Host ('       ' + (ConvertTo-Json -InputObject $F -Depth 3 -Compress).Substring(0, [Math]::Min(900, (ConvertTo-Json -InputObject $F -Depth 3 -Compress).Length))) }
    }
}
# a time in the result: ISO text (PowerShell 7's ConvertFrom-Json makes it a DateTime)
function IsTime($Value) { return ($Value -is [datetime]) -or ([string]$Value -match '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$') }
function Item($Scan, [string] $Key) { return @($Scan.items | Where-Object { $_.key -eq $Key })[0] }
function Summaries([string] $Key) { return @(Get-ChildItem -LiteralPath (Join-Path (Join-Path $tmp 'kept') $Key) -Filter '*_summary.json' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'latest-summary.json' }) }
function ArgsSaid { return [string](Get-Content -LiteralPath (Join-Path $es 'args.txt') -Raw) }

# ---- run Evaluate-STIG -------------------------------------------------------------------------------------------
Spec @((Ws), (Iis))
$f = Scan @{}
$ws = $null; $iis = $null
if ($f) { $ws = @($f.scans | Where-Object { $_.benchmark -eq 'Windows_Server_2022_STIG' })[0]; $iis = @($f.scans | Where-Object { $_.benchmark -eq 'IIS_10-0_Site_STIG' })[0] }
Check 'run: two checklists, sorted by name' ($f -and $f.scanned -and @($f.scans).Count -eq 2 -and $f.scans[0].label -eq 'Microsoft IIS 10.0 Site - Default Web Site' -and $f.scans[1].label -eq 'Microsoft Windows Server 2022' -and $f.found_by -eq 'win_check_stig_evaluate') $f
Check 'run: the version, the counts, one item per rule' ($ws -and $ws.stig_version -eq 'V2R4' -and $ws.counts.pass -eq 1 -and $ws.counts.fail -eq 1 -and $ws.counts.notchecked -eq 1 -and $ws.counts.notapplicable -eq 1 -and @($ws.items).Count -eq 4 -and $ws.scanner -eq 'Evaluate-STIG' -and $ws.platform -eq 'windows' -and $ws.history -eq 'items') $ws
$i9 = $null; $i8 = $null
if ($ws) { $i9 = Item $ws 'SV-254239'; $i8 = Item $ws 'SV-254238' }
Check 'run: a fail has its title and what Evaluate-STIG found; a pass neither; nothing before' ($i9 -and $i9.title -eq 'Rule SV-254239 must be set.' -and $i9.details -eq 'Found: LegalNoticeText is empty.' -and $i9.severity -eq 'medium' -and $i9.v -eq 'V-254239' -and $i9.stigid -eq 'WN22-00-254239' -and $i8 -and $null -eq $i8.title -and $null -eq $i8.details -and $null -eq $i8.prev -and $null -eq $i8.passed -and -not $ws.previous.time) $ws
Check 'run: the web site checklist has its own key and site' ($iis -and $iis.key -eq 'IIS_10-0_Site_STIG_Default_Web_Site' -and $iis.site -eq 'Default Web Site' -and $iis.counts.fail -eq 1) $iis
Check 'run: the checklist and the summary kept in its own folder, the run folder gone' ($ws -and (Test-Path -LiteralPath $ws.saved.results) -and (Test-Path -LiteralPath $ws.saved.summary) -and $ws.saved.results -like '*_checklist.cklb' -and (Summaries 'Windows_Server_2022_STIG').Count -eq 1 -and -not @(Get-ChildItem -LiteralPath (Join-Path $tmp 'kept') -Directory -Filter 'run-*').Count) $ws
$a = ArgsSaid
Check 'run: -Output CKLB into a folder of its own, no -SelectSTIG' ($a -match 'Output=CKLB' -and $a -match 'OutputPath=.*run-' -and $a -match '(?m)^SelectSTIG=\s*$') $a

Start-Sleep -Seconds 1
Spec @((Ws 'fail'), (Iis))
$f = Scan @{}
$ws = $null
if ($f) { $ws = @($f.scans | Where-Object { $_.benchmark -eq 'Windows_Server_2022_STIG' })[0] }
$i8 = $null
if ($ws) { $i8 = Item $ws 'SV-254238' }
Check 'next run: a rule that passed fails - the result before and when it last passed' ($i8 -and $i8.result -eq 'fail' -and $i8.prev -eq 'pass' -and (IsTime $i8.passed) -and (IsTime $ws.previous.time) -and $i8.title) $i8

Start-Sleep -Seconds 1
$f = Scan @{ keep = 2 }
$i8 = $null
if ($f) { $i8 = Item (@($f.scans | Where-Object { $_.benchmark -eq 'Windows_Server_2022_STIG' })[0]) 'SV-254238' }
Check 'keep 2: two runs kept; still known to have passed (kept in the summary)' ((Summaries 'Windows_Server_2022_STIG').Count -eq 2 -and @(Get-ChildItem -LiteralPath (Join-Path (Join-Path $tmp 'kept') 'Windows_Server_2022_STIG') -Filter '*_checklist.cklb').Count -eq 2 -and $i8 -and $i8.passed -and $i8.prev -eq 'fail') $i8

Start-Sleep -Seconds 1
$f = Scan @{ save = $false }
Check 'dry run (save false): scans, keeps nothing' ($f -and $f.scanned -and (Summaries 'Windows_Server_2022_STIG').Count -eq 2 -and -not $f.scans[0].saved.summary) $f

# ---- the arguments, CKL ------------------------------------------------------------------------------------------
Spec @((Ws))
$f = Scan @{ select = @('WinServer2022', 'IIS10Site'); exclude = @('Edge'); args = @('-ScanType', 'Classified', '-AnswerKey', "Site's key"); save = $false }
$a = ArgsSaid
Check 'win_check_stig_evaluate_select / _exclude / _args reach Evaluate-STIG' ($f -and $f.scanned -and $a -match 'SelectSTIG=WinServer2022,IIS10Site' -and $a -match 'ExcludeSTIG=Edge' -and $a -match 'ScanType=Classified' -and $a -match "AnswerKey=Site's key") $a
$f = Scan @{ output = 'CKL'; save = $false }
$ws = $null
if ($f) { $ws = $f.scans[0] }
$i9 = $null
if ($ws) { $i9 = Item $ws 'SV-254239' }
Check 'CKL: read as the CKLB' ($ws -and $ws.stig_version -eq 'V2R4' -and $ws.label -eq 'Microsoft Windows Server 2022' -and $ws.counts.pass -eq 1 -and $i9 -and $i9.details -eq 'Found: LegalNoticeText is empty.' -and $ws.checklist -like '*.ckl') $ws

# ---- what went wrong ---------------------------------------------------------------------------------------------
$bad = Join-Path (Join-Path (Join-Path $tmp 'estemp') 'Evaluate-STIG') 'Bad_CKL'
Set-Content -LiteralPath (Join-Path (New-Item -ItemType Directory -Path (Split-Path -Parent $bad) -Force).FullName 'Evaluate-STIG.log') -Value 'log'
Spec @((Ws)) -Reject $bad -Say @('ERROR: (Get-V254240) : Failed')
$f = Scan @{ save = $false }
Check 'its schema check rejected the checklist: read from Bad_CKL, said' ($f -and $f.scanned -and @($f.evaluate_rejected).Count -eq 1 -and @($f.evaluate_failed) -contains 'V-254240' -and $f.evaluate_log -like '*Evaluate-STIG.log') $f
Spec @((Ws)) -Reject $bad -Say @('ERROR: (Get-V254240) : Failed') -Wrap
$f = Scan @{ save = $false }
Check 'rejected, its line wrapped before the folder (Windows PowerShell 5.1): still read' ($f -and $f.scanned -and @($f.evaluate_rejected).Count -eq 1) $f
Spec @((Ws)) -Reject $bad -Say @('ERROR: (Get-V254240) : Failed')
$f = Scan @{ save = $false; rejected = $false }
Check 'win_check_stig_evaluate_rejected false: not scanned, why' ($f -and -not $f.scanned -and $f.why -match 'schema check rejected WIN01_WinServer2022_V2R4_20261030.cklb' -and $f.why -match 'V-254240') $f
Spec @() -Exit 3 -Say @('ERROR: no STIG applies')
$f = Scan @{ save = $false }
Check 'no checklist: not scanned, its exit and what it said' ($f -and -not $f.scanned -and $f.why -match 'wrote no checklist \(exit 3' -and $f.why -match 'no STIG applies' -and $f.evaluate_exit -eq 3) $f
Spec @((Ws)) -Sleep 30
$f = Scan @{ save = $false; timeout = 3 }
Check 'win_check_stig_timeout: stopped, not scanned' ($f -and -not $f.scanned -and $f.why -match 'longer than 3 seconds') $f

# ---- where Evaluate-STIG is ----------------------------------------------------------------------------------------
Spec @((Ws))
$drives = Join-Path $tmp 'drives'
$hit = Join-Path $drives 'Evaluate-STIG-1.2507'
New-Item -ItemType Directory -Path $drives -Force | Out-Null
Copy-Item -LiteralPath $es -Destination $hit -Recurse
$hitScript = Join-Path $hit 'Evaluate-STIG.ps1'
$userOwns = 'O:S-1-5-21-1-2-3-1001G:SYD:AI(A;ID;FA;;;S-1-5-21-1-2-3-1001)(A;ID;FA;;;SY)(A;ID;FA;;;BA)'
$f = Scan @{ evaluate = ''; search = @($drives); save = $false } -Acl @{}
Check 'found on the drives (a folder named Evaluate-STIG*), only SYSTEM and the Administrators can change it: run' ($f -and $f.scanned -and $f.evaluate -eq $hitScript -and $f.found_by -match 'search') $f
$f = Scan @{ evaluate = ''; search = @($drives); save = $false } -Acl @{} -Clm
Check 'Constrained Language Mode: found on the drives, its permissions read' ($f -and $f.scanned -and $f.evaluate -eq $hitScript) $f
$f = Scan @{ evaluate = ''; search = @($drives); save = $false } -Acl @{ $hit = 'O:BAG:SYD:AI(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;0x1301bf;;;AU)' }
Check 'found, but other accounts can write to its folder: not run, says who and what to set' ($f -and -not $f.scanned -and -not $f.evaluate -and $f.why -match 'not run as SYSTEM' -and $f.why -match 'AU \(0x1301bf\)' -and $f.why -match 'win_check_stig_evaluate' -and $f.why.Contains($hitScript)) $f
$f = Scan @{ evaluate = ''; search = @($drives); save = $false } -Acl @{ (Join-Path $hit 'spec.json') = $userOwns }
Check 'found, but a file in its folder belongs to another account: not run, names the file' ($f -and -not $f.scanned -and $f.why.Contains((Join-Path $hit 'spec.json')) -and $f.why -match 'owner S-1-5-21-1-2-3-1001') $f
$f = Scan @{ evaluate = ''; search = @($drives); save = $false } -Acl @{ $drives = 'O:BAG:SYD:AI(A;OICI;FA;;;BA)(A;OICI;0x1301ff;;;BU)' }
Check 'found, but the folder above lets others replace it: not run' ($f -and -not $f.scanned -and $f.why.Contains($drives + ': BU')) $f
Copy-Item -LiteralPath $es -Destination (Join-Path $drives 'Evaluate-STIG-old') -Recurse
$f = Scan @{ evaluate = ''; search = @($drives); save = $false } -Acl @{}
Check 'found in two places: none is picked, both named' ($f -and -not $f.scanned -and $f.why -match 'found in 2 places' -and $f.why.Contains($hitScript) -and $f.why -match 'win_check_stig_evaluate') $f
Remove-Item -LiteralPath (Join-Path $drives 'Evaluate-STIG-old') -Recurse -Force
$f = Scan @{ evaluate = ''; search = @($drives); save = $false }
Check 'found where its permissions are not known to be safe (a temporary folder; no ACLs off Windows): not run' ($f -and -not $f.scanned -and $f.why -match 'not run as SYSTEM' -and $f.why.Contains($hitScript)) $f
$f = Scan @{ evaluate = $hit; save = $false } -Acl @{ $hit = 'O:BAG:SYD:AI(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;0x1301bf;;;AU)' }
Check 'win_check_stig_evaluate names it: run as it is' ($f -and $f.scanned -and $f.found_by -eq 'win_check_stig_evaluate') $f
# its own folder: one that is there and that others can change is refused; the job's account may own it
$open = Join-Path $tmp 'kept-open'
New-Item -ItemType Directory -Path $open -Force | Out-Null
$f = Scan @{ dir = $open } -Acl @{ $open = $userOwns }
Check 'win_check_stig_dir there already, another account owns it: not scanned, says what to do' ($f -and -not $f.scanned -and $f.why -match 'can be changed by others' -and $f.why -match 'win_check_stig_dir' -and -not @(Get-ChildItem -LiteralPath $open).Count) $f
$f = Scan @{ dir = $open } -Acl @{ $open = 'O:S-1-5-21-1-2-3-1500G:SYD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)' }
Check "win_check_stig_dir owned by the job's own account: used" ($f -and $f.scanned -and (Test-Path -LiteralPath $f.scans[0].saved.summary)) $f
# Windows writes the built-in Administrator as LA in an SDDL (a job running as it makes its folders so): LA counts as the
# Administrators - it can never be taken out of them; any other alias (DA: Domain Admins) counts as someone else
$f = Scan @{ dir = $open } -Acl @{ $open = 'O:LAG:SYD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;LA)' }
Check 'win_check_stig_dir made by the built-in Administrator (LA in its SDDL): used' ($f -and $f.scanned) $f
$f = Scan @{ dir = $open } -Acl @{ $open = 'O:LAG:SYD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;LA)' } -Clm
Check '... the same in Constrained Language Mode' ($f -and $f.scanned) $f
$f = Scan @{ dir = $open } -Acl @{ $open = 'O:BAG:SYD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;DA)' } -Clm
Check '... an entry for another alias (DA, Domain Admins): refused, named as the SDDL does' ($f -and -not $f.scanned -and $f.why -match 'DA \(FA\)') $f
New-Item -ItemType Directory -Path (Join-Path $tmp 'empty') -Force | Out-Null
$f = Scan @{ evaluate = ''; search = @((Join-Path $tmp 'empty')); save = $false }
Check 'not found: says where it looked and what to set' ($f -and -not $f.scanned -and $f.why -match 'not found' -and $f.why -match 'win_check_stig_evaluate') $f
$f = Scan @{ evaluate = (Join-Path $tmp 'empty'); save = $false }
Check 'win_check_stig_evaluate without the script: says so' ($f -and -not $f.scanned -and $f.why -match 'no Evaluate-STIG.ps1 there') $f

# ---- read what it wrote ------------------------------------------------------------------------------------------
$out = Join-Path $tmp 'compliance'
foreach ($d in @('WIN01/Checklist', 'WIN01/Previous/2026-09-01', 'OTHER/Checklist')) { New-Item -ItemType Directory -Path (Join-Path $out $d) -Force | Out-Null }
Spec @((Ws 'fail'))
& $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $es 'Evaluate-STIG.ps1') -OutputPath (Join-Path $tmp 'w') | Out-Null
Copy-Item -LiteralPath (Join-Path $tmp 'w/WIN01/Checklist/WIN01_WinServer2022_V2R4_20261030.cklb') -Destination (Join-Path $out 'WIN01/Previous/2026-09-01/old.cklb')
Copy-Item -LiteralPath (Join-Path $tmp 'w/WIN01/Checklist/WIN01_WinServer2022_V2R4_20261030.cklb') -Destination (Join-Path $out 'OTHER/Checklist/other.cklb')
Start-Sleep -Seconds 1
Spec @((Ws))
& $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $es 'Evaluate-STIG.ps1') -OutputPath (Join-Path $tmp 'w') | Out-Null
Copy-Item -LiteralPath (Join-Path $tmp 'w/WIN01/Checklist/WIN01_WinServer2022_V2R4_20261030.cklb') -Destination (Join-Path $out 'WIN01/Checklist/new.cklb')
$f = Scan @{ read = $out; dir = (Join-Path $tmp 'kept-read') }
$ws = $null
if ($f) { $ws = $f.scans[0] }
Check "read: this server's newest checklist, not Previous, not another server's" ($f -and $f.scanned -and @($f.scans).Count -eq 1 -and $ws.checklist -eq 'new.cklb' -and $ws.counts.pass -eq 1 -and (IsTime $ws.checklist_time) -and -not $f.evaluate) $f
$pub = Join-Path $tmp 'pub'
New-Item -ItemType Directory -Path (Join-Path $pub 'Documents/STIG_Compliance/WIN01/Checklist') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $out 'WIN01/Checklist/new.cklb') -Destination (Join-Path $pub 'Documents/STIG_Compliance/WIN01/Checklist/new.cklb')
$oldPublic = $env:PUBLIC
$env:PUBLIC = $pub
$f = Scan @{ read = 'auto'; save = $false }
Check "read auto: Evaluate-STIG's default output folder" ($f -and $f.scanned -and $f.found_by -match 'default output' -and $f.read -like '*STIG_Compliance') $f
$f = Scan @{ evaluate = ''; search = @((Join-Path $tmp 'empty')); save = $false }
Check 'not found, but its checklists are in the default folder: says read auto' ($f -and -not $f.scanned -and $f.why -match 'win_check_stig_evaluate_read: auto') $f
$env:PUBLIC = $oldPublic
$f = Scan @{ read = (Join-Path $tmp 'nothere'); save = $false }
Check 'read: no folder - not scanned' ($f -and -not $f.scanned -and $f.why -match 'no folder') $f

# ---- the STIG recheck: only the rules asked for (vulns), the history left alone (history false) ----------------------
function Recheck($Scan) { return @{ ws = @($Scan.scans | Where-Object { $_.benchmark -eq 'Windows_Server_2022_STIG' })[0]; iis = @($Scan.scans | Where-Object { $_.benchmark -eq 'IIS_10-0_Site_STIG' })[0] } }
$re = Join-Path $tmp 'kept-re'
Spec @((Ws), (Iis))
$f = Scan @{ vulns = @('V-254238', 'V-218736'); history = $false; dir = $re }
$a = ArgsSaid
$x = $null
if ($f) { $x = Recheck $f }
Check 'recheck: -SelectVuln with the rules asked for' ($f -and $f.scanned -and $f.selected -and $f.vulns_asked -eq 2 -and $a -match 'SelectVuln=V-254238,V-218736') @($f, $a)
Check 'recheck: only those rules, no history' ($x -and $x.ws -and @($x.ws.items).Count -eq 1 -and $x.ws.items[0].key -eq 'SV-254238' -and $x.ws.items[0].result -eq 'pass' -and -not $x.ws.history -and @($x.iis.items).Count -eq 1 -and $x.iis.items[0].result -eq 'fail') $x
Check 'recheck: each checklist kept in recheck, nothing with the scans' ($x -and $x.ws.saved.results -like '*recheck*Windows_Server_2022_STIG.cklb' -and (Test-Path -LiteralPath $x.ws.saved.results) -and -not (Test-Path -LiteralPath (Join-Path $re 'Windows_Server_2022_STIG'))) $x
$old = Join-Path $tmp 'old\Evaluate-STIG'
New-Item -ItemType Directory -Path $old -Force | Out-Null
Set-Content -LiteralPath (Join-Path $old 'Evaluate-STIG.ps1') -Value ((Get-Content -LiteralPath (Join-Path $es 'Evaluate-STIG.ps1') -Raw) -replace 'SelectVuln', 'PickVuln') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $es 'spec.json') -Destination $old
$f = Scan @{ evaluate = $old; vulns = @('V-254238'); history = $false; save = $false }
$x = $null
if ($f) { $x = Recheck $f }
Check 'recheck, an Evaluate-STIG without -SelectVuln: run in full, only the rules asked for kept' ($f -and $f.scanned -and -not $f.selected -and [string](Get-Content -LiteralPath (Join-Path $old 'args.txt') -Raw) -match 'PickVuln=\r?\n' -and $x.ws -and @($x.ws.items).Count -eq 1 -and -not $x.iis.items) $f
Check 'recheck, save false: nothing kept' ($f -and -not $x.ws.saved.results) $x
Spec @((Ws), (Iis)) -VulnNeedsStig
$f = Scan @{ vulns = @('V-254238'); history = $false; save = $false }
$x = $null
if ($f) { $x = Recheck $f }
Check 'recheck, -SelectVuln refused (nothing written): once more in full, said' ($f -and $f.scanned -and -not $f.selected -and $f.select_note -match 'needs -SelectSTIG' -and $x.ws -and @($x.ws.items).Count -eq 1) $f

# ---- Constrained Language Mode -------------------------------------------------------------------------------------
Spec @((Ws), (Iis))
$f = Scan @{ vulns = @('V-254239'); history = $false; dir = (Join-Path $tmp 'kept-re-clm') } -Clm
$x = $null
if ($f) { $x = Recheck $f }
Check 'Constrained Language Mode: the recheck' ($f -and $f.scanned -and $f.selected -and $x.ws -and @($x.ws.items).Count -eq 1 -and $x.ws.items[0].result -eq 'fail' -and (Test-Path -LiteralPath $x.ws.saved.results)) $f
Spec @((Ws), (Iis))
$f = Scan @{ dir = (Join-Path $tmp 'kept-clm') } -Clm
Check 'Constrained Language Mode: the same' ($f -and $f.scanned -and @($f.scans).Count -eq 2 -and $f.scans[1].counts.fail -eq 1 -and (Test-Path -LiteralPath $f.scans[1].saved.summary)) $f
$f = Scan @{ read = $out; save = $false } -Clm
Check 'Constrained Language Mode: read' ($f -and $f.scanned -and $f.scans[0].checklist -eq 'new.cklb') $f
Spec @((Ws)) -Reject $bad -Say @('ERROR: (Get-V254240) : Failed')
$f = Scan @{ save = $false } -Clm
Check 'Constrained Language Mode: a rejected checklist and what Evaluate-STIG said' ($f -and $f.scanned -and @($f.evaluate_rejected).Count -eq 1 -and $f.stderr -match 'failed schema validation') $f
$f = Scan @{ evaluate = ''; search = @((Join-Path $tmp 'empty')); save = $false } -Clm
Check 'Constrained Language Mode: not found' ($f -and -not $f.scanned -and $f.why -match 'not found') $f

# ---- as SYSTEM in a scheduled task (Windows, as administrator) -------------------------------------------------------
$admin = $false
if ($onWindows) { $admin = (& whoami.exe /groups) -match 'S-1-16-12288' }
if ($admin) {
    Spec @((Ws), (Iis))
    $f = Scan @{ run_as = 'task'; dir = (Join-Path $env:ProgramData ('sa-win-stig-' + (Get-Random))) }
    $a = ArgsSaid
    Check 'run_as task: Evaluate-STIG ran as SYSTEM, the task deleted' ($f -and $f.scanned -and @($f.scans).Count -eq 2 -and $a -match 'User=(SYSTEM|.*\$)' -and -not @(Get-ScheduledTask -TaskName 'site-automation STIG scan*' -ErrorAction SilentlyContinue).Count) @($f, $a)
    if ($f -and $f.scans) { Remove-Item -LiteralPath (Split-Path -Parent $f.scans[0].saved.dir) -Recurse -Force -ErrorAction SilentlyContinue }
    $f = Scan @{ run_as = 'task'; vulns = @('V-254238', 'V-218736'); history = $false; save = $false }
    $a = ArgsSaid
    Check 'run_as task: the recheck, -SelectVuln as one array' ($f -and $f.scanned -and $f.selected -and $a -match 'SelectVuln=V-254238,V-218736') @($f, $a)
    # found on the drives, with the server's real permissions: a folder only SYSTEM and the Administrators can change
    # is run as SYSTEM; once the Users may change it, it is not
    $pd = Join-Path $env:ProgramData ('sa-win-stig-found-' + (Get-Random))
    New-Item -ItemType Directory -Path $pd -Force | Out-Null
    & icacls.exe $pd /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    Spec @((Ws))
    Copy-Item -LiteralPath $es -Destination (Join-Path $pd 'Evaluate-STIG') -Recurse
    & icacls.exe $pd /setowner '*S-1-5-32-544' /T /C | Out-Null
    $f = Scan @{ evaluate = ''; search = @($pd); run_as = 'task'; save = $false }
    Check 'found in a folder only SYSTEM and the Administrators can change (real permissions): run as SYSTEM' ($f -and $f.scanned -and $f.found_by -match 'search') $f
    & icacls.exe (Join-Path $pd 'Evaluate-STIG') /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null
    $f = Scan @{ evaluate = ''; search = @($pd); run_as = 'task'; save = $false }
    Check 'the same folder once the Users may change it (real permissions): not run' ($f -and -not $f.scanned -and $f.why -match 'not run as SYSTEM' -and $f.why -match 'BU') $f
    Remove-Item -LiteralPath $pd -Recurse -Force -ErrorAction SilentlyContinue
}
else { Write-Host 'skip - run_as task (needs Windows and an administrator)' }

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item Env:\SA_TEST_ACL -ErrorAction SilentlyContinue
Remove-Item Env:\SITE_CHECK_CONFIG -ErrorAction SilentlyContinue
if ($fail) { Write-Host "$fail of $count test(s) failed"; exit 1 }
Write-Host "all $count tests passed"
