# A stand-in ConfigMgr client and CIM for the patch orchestration test. State in $env:FAKECCM_STATE.
# (Test double for tests/windows/patch_sim - not used in production.)
function Get-State { Get-Content -Raw $env:FAKECCM_STATE | ConvertFrom-Json }
function Set-State($s) { $s | ConvertTo-Json -Depth 6 | Set-Content $env:FAKECCM_STATE }
function Get-CimInstance {
    param([string] $ClassName, [string] $Filter, [string] $Namespace, $ErrorAction)
    $s = Get-State
    switch ($ClassName) {
        'SMS_Client' { if (-not $s.client) { throw 'Invalid namespace' }; return [pscustomobject]@{ ClientVersion = '5.0' } }
        'Win32_OperatingSystem' { return [pscustomobject]@{ LastBootUpTime = [datetime]$s.boot } }
        'Win32_Service' {
            foreach ($sv in $s.services) {
                if ($sv.slow -and $sv.state -eq 'Stopped') { $sv.slow_polls = [int]$sv.slow_polls + 1; if ($sv.slow_polls -ge 2) { $sv.state = 'Running' } }
            }
            Set-State $s
            $all = @($s.services | ForEach-Object { [pscustomobject]@{ Name = $_.name; State = $_.state; StartMode = 'Auto' } })
            if ($Filter -match "Name='([^']+)'") { $n = $Matches[1]; return @($all | Where-Object { $_.Name -eq $n }) }
            if ($Filter -match "State='Running'") { return @($all | Where-Object { $_.State -eq 'Running' }) }
            return $all
        }
        'CCM_SoftwareUpdate' {
            # every look moves the updates that are on their way one step on
            $changed = $false
            foreach ($u in $s.updates) {
                $next = @{ 2 = 5; 5 = 7; 7 = $(if ($u.needs_restart) { 8 } else { 12 }) }[[int]$u.EvaluationState]
                if ($null -ne $next -and -not $s.blocked) { $u.EvaluationState = $next; $changed = $true }
                elseif ([int]$u.EvaluationState -eq 2 -and $s.blocked) { $u.EvaluationState = 14; $changed = $true }
            }
            # installed without a restart: compliant, gone from the list
            $s.updates = @($s.updates | Where-Object { [int]$_.EvaluationState -ne 12 })
            Set-State $s
            return @($s.updates | ForEach-Object { [pscustomobject]@{ UpdateID = $_.UpdateID; ArticleID = $_.ArticleID; Name = $_.Name
                        EvaluationState = [int]$_.EvaluationState; ComplianceState = 0; PercentComplete = 0; ErrorCode = 0 } })
        }
    }
}
function Invoke-CimMethod {
    param([string] $Namespace, [string] $ClassName, [string] $MethodName, $Arguments, $ErrorAction)
    $s = Get-State
    switch ($MethodName) {
        'TriggerSchedule' { $s.scans = [int]$s.scans + 1; Set-State $s; return [pscustomobject]@{ ReturnValue = 0 } }
        'DetermineIfRebootPending' { return [pscustomobject]@{ RebootPending = [bool](@($s.updates | Where-Object { [int]$_.EvaluationState -eq 8 }).Count); IsHardRebootPending = $false } }
        'InstallUpdates' {
            $ids = @($Arguments.CCMUpdates | ForEach-Object { $_.UpdateID })
            foreach ($u in $s.updates) { if ($ids -contains $u.UpdateID) { $u.EvaluationState = 2 } }
            $s.install_calls = [int]$s.install_calls + 1
            Set-State $s
            return [pscustomobject]@{ ReturnValue = 0 }
        }
    }
}
function Test-Path { param([string] $Path) return $false }
Export-ModuleMember -Function Get-CimInstance, Invoke-CimMethod
