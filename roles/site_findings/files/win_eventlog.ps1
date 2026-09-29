# site-automation (Windows): one Application event log entry per finding, so the SIEM that
# collects event logs sees them too. Warning = event 901, critical = event 902 (type Error).
# Input: $env:SITE_CHECK_CONFIG = {"source": "...", "job": "...", "findings": [{id, severity, summary}]}
# eventcreate.exe creates the source the first time. Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$cfg = ConvertFrom-Json -InputObject $env:SITE_CHECK_CONFIG
$n = 0
foreach ($f in @($cfg.findings)) {
    $type = 'WARNING'
    $id = 901
    if ($f.severity -eq 'critical') { $type = 'ERROR'; $id = 902 }
    $text = ([string]$f.severity).ToUpper() + ' ' + $f.id + ': ' + $f.summary + ' (AAP job ' + $cfg.job + ')'
    if ($text.Length -gt 30000) { $text = $text.Substring(0, 30000) }
    $null = & eventcreate.exe /L APPLICATION /SO $cfg.source /T $type /ID $id /D $text 2>&1
    if ($LASTEXITCODE -eq 0) { $n++ }
}
'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ written = $n } -Compress)
