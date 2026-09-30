<#
.SYNOPSIS
Update YOUR copy of site-automation (on Windows) from a new release, without touching your own files.

.DESCRIPTION
Run it from the NEW release that you extracted (your old copy may not have this script yet):

  1. Extract site-automation-0.3.1.zip somewhere OUTSIDE your repository folder,
     e.g. Downloads\site-automation-0.3.1  (right-click the zip > Extract All).
  2. In VS Code, open a terminal (Terminal > New Terminal; it is PowerShell) and PREVIEW:
       $rel = "$HOME\Downloads\site-automation-0.3.1"
       & "$rel\scripts\update-from-release.ps1" -Clone "C:\git\site-automation"
  3. Read the list, then APPLY:
       & "$rel\scripts\update-from-release.ps1" -Clone "C:\git\site-automation" -Apply
  4. Review in VS Code's Source Control view, Commit, then Sync Changes (push).

-Clone is your repository folder (the one VS Code has open). The preview changes nothing. -Apply
makes your folder match the release, except for YOUR files, which are never changed or deleted:
  poam\poam.csv, playbooks\group_vars\ and host_vars\ (your settings), inventories\site\,
  .site-local, every path listed in .site-local (one per line, e.g. roles/check_tmp/), and every
  file your .gitignore covers (local secrets, reports). A file of yours that you do not have yet
  (e.g. a new settings file) is added once, then never changed again.

Blocked by the execution policy ("running scripts is disabled" / "not digitally signed")? Run it
once like this instead (add -Apply to apply):
  cd $rel
  powershell -ExecutionPolicy Bypass -File .\scripts\update-from-release.ps1 -Clone "C:\git\site-automation"
Works in Windows PowerShell 5.1 and PowerShell 7, also in Constrained Language Mode.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Clone,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'

function Fail([string]$msg) {
    Write-Host ('ERROR: ' + $msg) -ForegroundColor Red
    exit 1
}
function Get-Rel([string]$root, [string]$full) {
    return $full.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
}
# Text files: compare without caring about Windows (CRLF) vs Linux (LF) line endings, because
# Git for Windows usually checks files out with CRLF and the release has LF.
$textExt = @('.yml', '.yaml', '.md', '.py', '.sh', '.ps1', '.j2', '.cfg', '.csv', '.txt', '.json',
    '.ini', '.toml', '.gitignore', '.ansible-lint', '.yamllint', '')
function Test-Same([string]$a, [string]$b) {
    $leaf = Split-Path -Leaf $a
    $dot = $leaf.LastIndexOf('.')
    $ext = ''
    if ($dot -ge 0) { $ext = $leaf.Substring($dot) }
    if ($textExt -contains $ext.ToLower()) {
        $x = Get-Content -LiteralPath $a -Raw
        $y = Get-Content -LiteralPath $b -Raw
        if ($null -eq $x) { $x = '' }
        if ($null -eq $y) { $y = '' }
        return (($x -replace "`r`n", "`n") -ceq ($y -replace "`r`n", "`n"))
    }
    return ((Get-FileHash -LiteralPath $a).Hash -eq (Get-FileHash -LiteralPath $b).Hash)
}
function Get-Files([string]$root) {
    # every file under $root except the .git folder
    Get-ChildItem -LiteralPath $root -Force | Where-Object { $_.Name -ne '.git' } | ForEach-Object {
        if ($_.PSIsContainer) { Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Force } else { $_ }
    }
}

# ---- where things are -------------------------------------------------------------------------
$release = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not (Test-Path -LiteralPath (Join-Path $release 'playbooks/health_check.yml'))) {
    Fail "$release is not a site-automation release (run the script from the extracted release)"
}
if (-not (Test-Path -LiteralPath $Clone)) { Fail "no such folder: $Clone" }
$Clone = (Resolve-Path -LiteralPath $Clone).Path
if (-not (Test-Path -LiteralPath (Join-Path $Clone '.git'))) {
    Fail "$Clone is not a Git repository folder. Use the folder VS Code has open for site-automation."
}
if ($Clone -eq $release) { Fail 'the release and your repository are the same folder: extract the release somewhere else' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail 'git is not found. Run this in a VS Code terminal, or install Git for Windows.'
}

# ---- your copy must be clean, so the update is the only change you review ------------------------
$status = & git -C $Clone status --porcelain
if ($LASTEXITCODE -ne 0) { Fail "git could not read $Clone" }
if ($status) {
    Fail ("$Clone has changes that are not committed. In VS Code (Source Control) commit or discard " +
        'them first, so the update is the only thing you review.')
}
$branch = (& git -C $Clone branch --show-current)

# ---- what is yours -----------------------------------------------------------------------------
$protect = @('.site-local', 'poam/poam.csv', 'inventories/site/', 'playbooks/group_vars/', 'playbooks/host_vars/')
$siteLocal = Join-Path $Clone '.site-local'
if (Test-Path -LiteralPath $siteLocal) {
    foreach ($line in (Get-Content -LiteralPath $siteLocal)) {
        $p = ($line -split '#')[0].Trim().Replace('\', '/').TrimStart('/')
        if ($p -and ('/' + $p + '/') -like '*/../*') { Fail ".site-local: '$p' - paths with .. are not allowed" }
        if ($p) { $protect += $p }
    }
}
$ignored = @(& git -C $Clone ls-files --others --ignored --exclude-standard --directory)
function Test-Yours([string]$rel) {
    $r = $rel.ToLower()
    foreach ($p in ($protect + $ignored)) {
        $q = $p.ToLower()
        if ($q.EndsWith('/')) { if ($r.StartsWith($q)) { return $true } }
        elseif ($r -eq $q -or $r.StartsWith($q + '/')) { return $true }   # a file, or a folder written without the slash
    }
    return $false
}

# ---- compare -----------------------------------------------------------------------------------
$plan = @()
$inRelease = @{}
foreach ($f in (Get-Files $release)) {
    $rel = Get-Rel $release $f.FullName
    $inRelease[$rel.ToLower()] = $true
    $dst = Join-Path $Clone $rel
    if (Test-Yours $rel) {
        # yours: added once if you do not have it yet, never changed after that
        if (-not (Test-Path -LiteralPath $dst)) {
            $plan += @{ Action = 'YOURS'; Path = $rel; Src = $f.FullName; Dst = $dst }
        }
        continue
    }
    if (-not (Test-Path -LiteralPath $dst)) {
        $plan += @{ Action = 'NEW'; Path = $rel; Src = $f.FullName; Dst = $dst }
    } elseif (-not (Test-Same $f.FullName $dst)) {
        $plan += @{ Action = 'CHANGED'; Path = $rel; Src = $f.FullName; Dst = $dst }
    }
}
foreach ($f in (Get-Files $Clone)) {
    $rel = Get-Rel $Clone $f.FullName
    if ($inRelease.ContainsKey($rel.ToLower()) -or (Test-Yours $rel)) { continue }
    $plan += @{ Action = 'DELETE'; Path = $rel; Src = ''; Dst = $f.FullName }
}
$plan = @($plan | Sort-Object { $_.Path })

# Settings the release documents in playbooks/group_vars/all.yml that your copy of that file does
# not mention at all (it is yours, so it is never changed; new settings would otherwise go unseen).
function Write-NewSettings {
    $rel = 'playbooks/group_vars/all.yml'
    $rf = Join-Path $release $rel; $mf = Join-Path $Clone $rel
    if (-not ((Test-Path -LiteralPath $rf) -and (Test-Path -LiteralPath $mf))) { return }
    $mine = Get-Content -LiteralPath $mf
    $keys = @(Get-Content -LiteralPath $rf | ForEach-Object { if ($_ -match '^#? ?([a-z][a-z0-9_]*):') { $Matches[1] } } | Sort-Object -Unique)
    $missing = @($keys | Where-Object { $k = $_; -not ($mine | Where-Object { $_ -match ('^[# ]*' + [regex]::Escape($k) + ':') }) })
    if ($missing.Count -eq 0) { return }
    Write-Host ''
    Write-Host "NEW SETTINGS in this release that your $rel does not mention (it is yours, so it is not changed):"
    foreach ($k in $missing) { Write-Host "  $k" }
    Write-Host "  See $rf for what each is, and copy the ones you want."
}

$version = '?'
$m = Select-String -LiteralPath (Join-Path $release 'CHANGELOG.md') -Pattern '^## ([0-9][0-9.]*) ' | Select-Object -First 1
if ($m) { $version = $m.Matches[0].Groups[1].Value }
Write-Host "Release:          $release (version $version)"
Write-Host "Your repository:  $Clone (branch $branch)"
Write-Host ('Yours, never changed: ' + ($protect -join ', ') + ', and everything .gitignore covers')
if ($branch -eq 'main' -or $branch -eq 'master') {
    Write-Host "Note: you are on '$branch'. That is fine: nothing reaches AAP until you push (Sync Changes)."
}
Write-Host ''

if (-not $Apply) {
    Write-Host 'PREVIEW - nothing is changed. What -Apply would do:'
    foreach ($p in $plan) { Write-Host ('  {0,-8} {1}' -f $p.Action, $p.Path) }
    if ($plan.Count -eq 0) { Write-Host '  nothing - your copy already matches this release' }
    Write-Host ''
    $nNew = @($plan | Where-Object { $_.Action -eq 'NEW' }).Count
    $nChg = @($plan | Where-Object { $_.Action -eq 'CHANGED' }).Count
    $nDel = @($plan | Where-Object { $_.Action -eq 'DELETE' }).Count
    $nYours = @($plan | Where-Object { $_.Action -eq 'YOURS' }).Count
    Write-Host ('{0} new, {1} changed, {2} to delete.' -f $nNew, $nChg, $nDel)
    Write-NewSettings
    if ($nYours) { Write-Host ('YOURS = {0} settings file(s) you do not have yet: added once, then yours - never changed again.' -f $nYours) }
    Write-Host 'DELETE = files that are not in the release. If one of them is yours, add its path to'
    Write-Host '.site-local (one per line), commit that, and preview again. CHANGED on one of our files'
    Write-Host 'that you edited = move your change into AAP variables first; the release replaces it.'
    Write-Host 'Looks right? Run the same command again with -Apply at the end.'
    exit 0
}

foreach ($p in $plan) {
    if ($p.Action -eq 'DELETE') {
        Remove-Item -LiteralPath $p.Dst -Force
    } else {
        $dir = Split-Path -Parent $p.Dst
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Copy-Item -LiteralPath $p.Src -Destination $p.Dst -Force
    }
}
Write-Host ('Applied: {0} file(s).' -f $plan.Count)
Write-NewSettings
Write-Host ''
Write-Host 'Next, in VS Code:'
Write-Host '  1. Source Control (Ctrl+Shift+G): the list is every file the update changed. Click one to'
Write-Host '     see what changed. Anything of yours in there? Right-click it > Discard Changes.'
Write-Host "  2. Type a message (e.g. site-automation $version), click Commit, then Sync Changes (push)."
Write-Host '  3. In AAP: Projects > site-automation > Sync (or it syncs itself on the next launch).'
Write-Host 'Changed your mind before committing? Source Control > ... > Discard All Changes.'
