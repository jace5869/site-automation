# A stand-in for the two CIM classes win_os.ps1 reads and the trial's one allowed CIM query (Test double for
# tests/stig/run_stig_answer_keys_win_test.sh - not used in production). $env:FAKE_WIN_CAPTION / FAKE_WIN_BUILD / FAKE_WIN_ROLE set the answer.
function Get-CimInstance {
    param([string] $ClassName, $ErrorAction)
    switch ($ClassName) {
        'Win32_OperatingSystem' {
            $cap = 'Microsoft Windows Server 2022 Standard'; if ($env:FAKE_WIN_CAPTION) { $cap = $env:FAKE_WIN_CAPTION }
            $b = '20348'; if ($env:FAKE_WIN_BUILD) { $b = $env:FAKE_WIN_BUILD }
            $pt = 3; if ($env:FAKE_WIN_PRODUCT_TYPE) { $pt = [int]$env:FAKE_WIN_PRODUCT_TYPE }
            return [pscustomobject]@{ Caption = $cap; BuildNumber = $b; ProductType = $pt }
        }
        'Win32_ComputerSystem' {
            $r = 3; if ($env:FAKE_WIN_ROLE) { $r = [int]$env:FAKE_WIN_ROLE }
            return [pscustomobject]@{ DomainRole = $r; PartOfDomain = $true }
        }
    }
}
Export-ModuleMember -Function Get-CimInstance
