# A stand-in for Windows' Get-HotFix (tests/windows/patch_age_sim): one installed update, FAKE_HOTFIX_DAYS days ago
# (none when it is empty), so roles/win_check_patching runs under PowerShell 7 on Linux.
function Get-HotFix {
    [CmdletBinding()] param()
    if (-not $env:FAKE_HOTFIX_DAYS) { return }
    New-Object -TypeName PSObject -Property @{ HotFixID = 'KB5099999'; InstalledOn = (Get-Date).AddDays(-[int]$env:FAKE_HOTFIX_DAYS) }
}
Export-ModuleMember -Function Get-HotFix
