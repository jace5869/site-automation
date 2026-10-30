# A stand-in Evaluate-STIG.ps1 for tests/windows/test_win_stig.ps1: writes the checklists spec.json (next to it) lists
# into <OutputPath>\<host>\Checklist, as CKLB or CKL (-Output), and records its arguments in args.txt. spec.json:
#   {"host": "WIN01", "exit": 0, "sleep": 0, "wrap": false, "say": ["lines it prints"], "reject": "" (a folder: its schema check
#    rejects every checklist and moves it there, as Evaluate-STIG's Bad_CKL), "checklists": [{"file", "stig_id",
#    "display_name", "version", "release", "site", "instance", "rules": [{"sv", "v", "stigid", "sev", "title", "status",
#    "details"}]}]}
param([string[]] $Output = @('CKLB'), [string] $OutputPath = '', [string[]] $SelectSTIG = @(), [string[]] $ExcludeSTIG = @(),
      [string] $ScanType = 'Unclassified', [string] $AnswerKey = '')
$ErrorActionPreference = 'Stop'
$spec = ConvertFrom-Json -InputObject ([string](Get-Content -LiteralPath (Join-Path $PSScriptRoot 'spec.json') -Raw))
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'args.txt') -Value @(
    ('Output=' + ($Output -join ',')), ('OutputPath=' + $OutputPath), ('SelectSTIG=' + ($SelectSTIG -join ',')),
    ('ExcludeSTIG=' + ($ExcludeSTIG -join ',')), ('ScanType=' + $ScanType), ('AnswerKey=' + $AnswerKey), ('User=' + [Environment]::UserName))
foreach ($l in @($spec.say)) { if ($l) { Write-Host $l } }
if ([int]$spec.sleep) { Start-Sleep -Seconds ([int]$spec.sleep) }
$ckl = ($Output -join ',') -match '^CKL$'
$dest = Join-Path (Join-Path $OutputPath $spec.host) 'Checklist'
if ($spec.reject) { $dest = [string]$spec.reject }
New-Item -ItemType Directory -Path $dest -Force | Out-Null
$map = @{ pass = @('not_a_finding', 'NotAFinding'); fail = @('open', 'Open'); na = @('not_applicable', 'Not_Applicable'); nr = @('not_reviewed', 'Not_Reviewed') }
foreach ($c in @($spec.checklists)) {
    if ($null -eq $c) { continue }
    $web = [bool]($c.site -or $c.instance)
    if ($ckl) {
        $x = @('<?xml version="1.0" encoding="UTF-8"?>', '<CHECKLIST><ASSET><HOST_NAME>' + $spec.host + '</HOST_NAME>',
               '<WEB_OR_DATABASE>' + $(if ($web) { 'true' } else { 'false' }) + '</WEB_OR_DATABASE><WEB_DB_SITE>' + $c.site + '</WEB_DB_SITE><WEB_DB_INSTANCE>' + $c.instance + '</WEB_DB_INSTANCE></ASSET><STIGS><iSTIG><STIG_INFO>')
        foreach ($kv in @(@('version', $c.version), @('stigid', $c.stig_id), @('releaseinfo', $c.release), @('title', $c.display_name + ' Security Technical Implementation Guide'))) {
            $x += '<SI_DATA><SID_NAME>' + $kv[0] + '</SID_NAME><SID_DATA>' + $kv[1] + '</SID_DATA></SI_DATA>'
        }
        $x += '</STIG_INFO>'
        foreach ($r in @($c.rules)) {
            $x += '<VULN>'
            foreach ($kv in @(@('Vuln_Num', $r.v), @('Severity', $r.sev), @('Rule_ID', $r.sv + 'r1000_rule'), @('Rule_Ver', $r.stigid), @('Rule_Title', $r.title))) {
                $x += '<STIG_DATA><VULN_ATTRIBUTE>' + $kv[0] + '</VULN_ATTRIBUTE><ATTRIBUTE_DATA>' + $kv[1] + '</ATTRIBUTE_DATA></STIG_DATA>'
            }
            $x += '<STATUS>' + $map[[string]$r.status][1] + '</STATUS><FINDING_DETAILS>' + $r.details + '</FINDING_DETAILS><COMMENTS></COMMENTS></VULN>'
        }
        $x += '</iSTIG></STIGS></CHECKLIST>'
        Set-Content -LiteralPath (Join-Path $dest ($c.file + '.ckl')) -Value ($x -join '') -Encoding UTF8
    }
    else {
        $rules = @()
        foreach ($r in @($c.rules)) {
            $rules += @(@{ rule_id = $r.sv + 'r1000_rule'; group_id = $r.v; rule_version = $r.stigid; severity = $r.sev; rule_title = $r.title
                           status = $map[[string]$r.status][0]; finding_details = [string]$r.details; comments = '' })
        }
        $doc = @{ title = 'Evaluate-STIG'; cklb_version = '1.0'
                  target_data = @{ host_name = $spec.host; is_web_database = $web; web_db_site = [string]$c.site; web_db_instance = [string]$c.instance }
                  stigs = @(@{ stig_name = $c.display_name + ' Security Technical Implementation Guide'; display_name = $c.display_name; stig_id = $c.stig_id
                               version = $c.version; release_info = $c.release; rules = $rules }) }
        Set-Content -LiteralPath (Join-Path $dest ($c.file + '.cklb')) -Value (ConvertTo-Json -InputObject $doc -Depth 6) -Encoding UTF8
    }
    $sep = ' '
    if ($spec.wrap) { $sep = " `r`n" }      # as Windows PowerShell 5.1 wraps a long line into a file
    if ($spec.reject) { Write-Host ("'" + $c.file + $(if ($ckl) { '.ckl' } else { '.cklb' }) + "' : failed schema validation. Moving to" + $sep + $dest) }
}
exit ([int]$spec.exit)
