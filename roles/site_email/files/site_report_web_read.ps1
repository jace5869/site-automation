# site-automation - the report website on a Windows server (roles/site_email, report_web_host): is the report
# folder (report_web_dest) there, and the index of every report (index.json) in it, read back so the job can put
# this template's line in it. Read-only. The pages themselves are written by the script the site_report_web
# module makes for each run.
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

$dest = [string](Get-Setting 'dest' '')
$parts = @(Get-Setting 'index' @())                 # index.json's path inside the folder, a part per folder
$out = @{ ok = $false; error = ''; index = $null }
try {
    if (-not $dest -or -not (Test-Path -LiteralPath $dest -PathType Container)) {
        $out.error = 'the folder ' + $dest + ' is not there on ' + $env:COMPUTERNAME + ' (report_web_dest)'
    }
    else {
        $out.ok = $true
        if ($parts.Count -gt 0) {
            $path = $dest
            foreach ($p in $parts) { $path = Join-Path -Path $path -ChildPath ([string]$p) }
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $out.index = [string](Get-Content -LiteralPath $path -Raw -Encoding UTF8)
            }
        }
    }
}
catch { $out.ok = $false; $out.error = [string]$_.Exception.Message }

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ read = $out } -Depth 4 -Compress)
