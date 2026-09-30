<#
Vendor an ACT-Windows release into vendor/act-windows (the single act.ps1 that the
"ACT for Windows - install" template copies to the Windows hosts).

  .\scripts\update-act-windows.ps1 -Source C:\temp\ACT-Windows-0.6.21.zip     # a release zip
  .\scripts\update-act-windows.ps1 -Source C:\git\ACT-Windows                 # or a checkout

Then: git diff --stat, run tests/check_vendor.sh (Linux/WSL), commit.
#>
param([Parameter(Mandatory)][string]$Source)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('act-win-' + [guid]::NewGuid().ToString('N'))
try {
    $src = (Resolve-Path -LiteralPath $Source).Path
    if (Test-Path -LiteralPath $src -PathType Leaf) {
        Expand-Archive -LiteralPath $src -DestinationPath $tmp
        $f = Get-ChildItem -LiteralPath $tmp -Recurse -Filter act.ps1 | Select-Object -First 1
        if (-not $f) { throw "no act.ps1 in $Source" }
        $src = $f.DirectoryName
    }
    $ps1 = Join-Path $src 'act.ps1'
    if (-not (Test-Path -LiteralPath $ps1)) { throw "not an ACT-Windows release (no act.ps1): $src" }
    $m = Select-String -LiteralPath $ps1 -Pattern "^\`$script:ActVersion\s*=\s*'([^']+)'" | Select-Object -First 1
    if (-not $m) { throw 'could not read the version from act.ps1' }
    $version = $m.Matches[0].Groups[1].Value
    $dest = Join-Path $root 'vendor/act-windows'
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Copy-Item -LiteralPath $ps1 -Destination (Join-Path $dest 'act.ps1') -Force
    [System.IO.File]::WriteAllText((Join-Path $dest 'VERSION'), "$version`n")
    Write-Host "vendored ACT-Windows $version into vendor/act-windows - review with 'git diff --stat', then commit."
    Write-Host "Refresh vendor/CHECKSUMS on Linux/WSL: sha256sum vendor/act/act vendor/act-windows/act.ps1 > vendor/CHECKSUMS"
} finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}
