# site-automation - Windows troubleshoot (roles/win_troubleshoot, playbooks/win_troubleshoot.yml).
# Runs the chosen area's read-only PowerShell commands and returns each one's output as text.
# Input: $env:SITE_CHECK_CONFIG = {"commands": [{"why": "...", "cmd": "..."}], "max_lines": 60}
# Prints ONE line: ###SITE-JSON### {"sections": [{why, cmd, lines, error}]}
# Windows PowerShell 5.1 and PowerShell 7; the commands run in the session's language mode.
$ErrorActionPreference = 'Continue'
$cfg = ConvertFrom-Json -InputObject $env:SITE_CHECK_CONFIG
$max = [int]$cfg.max_lines
if ($max -lt 1) { $max = 60 }
$sections = @()
foreach ($c in @($cfg.commands)) {
    $cmdText = [string]$c.cmd
    $text = ''
    $err = ''
    try { $text = (& { Invoke-Expression -Command $cmdText } 2>&1 | Out-String -Width 220) }
    catch { $err = $_.Exception.Message }
    $lines = @(([string]$text) -split "`r?`n" | ForEach-Object { $_.TrimEnd() })
    $first = 0
    while ($first -lt $lines.Count -and -not $lines[$first]) { $first++ }
    $last = $lines.Count - 1
    while ($last -ge $first -and -not $lines[$last]) { $last-- }
    if ($last -lt $first) { $lines = @() } else { $lines = @($lines[$first..$last]) }
    if ($lines.Count -gt $max) { $lines = @($lines[($lines.Count - $max)..($lines.Count - 1)]) }
    $sections += @(@{ why = [string]$c.why; cmd = $cmdText; lines = $lines; error = $err })
}
'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ sections = $sections } -Depth 5 -Compress)
