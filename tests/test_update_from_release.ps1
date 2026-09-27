# Test scripts/update-from-release.ps1 against a throw-away "work repository".
# The release is this checkout. Runs in Windows PowerShell 5.1 and PowerShell 7 (Windows or Linux):
#   powershell -File tests/test_update_from_release.ps1      pwsh -File tests/test_update_from_release.ps1
# Continue, not Stop: Windows PowerShell 5.1 turns git's harmless stderr warnings (line endings)
# into terminating errors under Stop. Every step is checked explicitly instead.
$ErrorActionPreference = 'Continue'
$release = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$script = Join-Path $release 'scripts/update-from-release.ps1'
$tmpRoot = [System.IO.Path]::GetTempPath()          # outside this checkout (the test copies it)
$work = Join-Path $tmpRoot ('sa-update-test-' + (Get-Random))
$fail = 0
function Check([bool]$ok, [string]$what) {
    if ($ok) { Write-Host "ok   - $what" } else { Write-Host "FAIL - $what" -ForegroundColor Red; $script:fail++ }
}
function Run([string[]]$extra) {
    $shell = (Get-Process -Id $PID).Path
    return (& $shell -NoProfile -ExecutionPolicy Bypass -File $script -Clone $work @extra 2>&1 | Out-String)
}

# ---- an "old" work repository: the release, then made older, plus the site's own files -----------
New-Item -ItemType Directory -Path $work | Out-Null
Get-ChildItem -LiteralPath $release -Force | Where-Object { $_.Name -ne '.git' } |
    Copy-Item -Destination $work -Recurse -Force
& git -C $work init -q
& git -C $work config user.email test@example.invalid
& git -C $work config user.name test
Set-Content -LiteralPath (Join-Path $work 'README.md') -Value 'OLD-README-MARKER'
Remove-Item -LiteralPath (Join-Path $work 'docs/HOW_IT_FITS_TOGETHER.md')
Set-Content -LiteralPath (Join-Path $work 'docs/OLD_NOTES.md') -Value 'removed in the new release'
Set-Content -LiteralPath (Join-Path $work 'poam/poam.csv') -Value "POAM ID,Status`nREAL-1,Ongoing"
New-Item -ItemType Directory -Path (Join-Path $work 'roles/check_tmp/tasks') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $work 'roles/check_tmp/tasks/main.yml') -Value '- debug: msg=mine'
Set-Content -LiteralPath (Join-Path $work '.site-local') -Value 'roles/check_tmp/   # our own check'
& git -C $work add -A 2>$null
& git -C $work commit -q -m 'the site copy' 2>$null
Set-Content -LiteralPath (Join-Path $work '.vault_pass') -Value 'local secret (git-ignored)'

# ---- preview -------------------------------------------------------------------------------------
$out = Run @()
Check ($out -match 'PREVIEW') 'preview runs'
Check ($out -match 'CHANGED\s+README\.md') 'preview: README.md CHANGED'
Check ($out -match 'NEW\s+docs/HOW_IT_FITS_TOGETHER\.md') 'preview: removed file comes back as NEW'
Check ($out -match 'DELETE\s+docs/OLD_NOTES\.md') 'preview: file not in the release is DELETE'
Check (-not ($out -match '(NEW|CHANGED|DELETE)\s+poam/poam\.csv')) 'preview: poam.csv not listed'
Check (-not ($out -match '(NEW|CHANGED|DELETE)\s+roles/check_tmp')) 'preview: own role (.site-local) not listed'
Check (-not ($out -match '(NEW|CHANGED|DELETE)\s+\.vault_pass')) 'preview: git-ignored file not listed'
Check ((Get-Content -LiteralPath (Join-Path $work 'README.md') -Raw) -match 'OLD-README-MARKER') 'preview changed nothing'

# ---- apply ---------------------------------------------------------------------------------------
$out = Run @('-Apply')
Check ($out -match 'Applied') 'apply runs'
Check (-not ((Get-Content -LiteralPath (Join-Path $work 'README.md') -Raw) -match 'OLD-README-MARKER')) 'README.md updated'
Check (Test-Path -LiteralPath (Join-Path $work 'docs/HOW_IT_FITS_TOGETHER.md')) 'missing file restored'
Check (-not (Test-Path -LiteralPath (Join-Path $work 'docs/OLD_NOTES.md'))) 'removed file deleted'
Check ((Get-Content -LiteralPath (Join-Path $work 'poam/poam.csv') -Raw) -match 'REAL-1') 'poam.csv kept'
Check (Test-Path -LiteralPath (Join-Path $work 'roles/check_tmp/tasks/main.yml')) 'own role kept'
Check (Test-Path -LiteralPath (Join-Path $work '.vault_pass')) 'git-ignored file kept'

# ---- a second preview finds nothing, and a dirty copy is refused ----------------------------------
& git -C $work add -A 2>$null
& git -C $work commit -q -m 'update' 2>$null
$out = Run @()
Check ($out -match '0 new, 0 changed, 0 to delete') 'after apply + commit: nothing left to do'
Add-Content -LiteralPath (Join-Path $work 'README.md') -Value 'local edit'
$out = Run @()
Check ($out -match 'not committed') 'uncommitted changes are refused'

Remove-Item -LiteralPath $work -Recurse -Force
if ($fail) { Write-Host "$fail check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all checks passed'
