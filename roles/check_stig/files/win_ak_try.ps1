# Run one draft per fresh session. The caller supplies the cmdlet inventory from
# stig_answer_win.ALLOWED. Settings may narrow the built-in inventory.

param([switch]$Worker)

$ErrorActionPreference = 'Stop'
if ($Worker) {
    $packet = [Convert]::FromBase64String([Console]::In.ReadLine())
    $cfg = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString($packet))
} else {
    $cfg = ConvertFrom-Json -InputObject $env:SITE_CHECK_CONFIG
}
$proxies = @('secedit.exe', 'Remove-Item', 'auditpol.exe', 'Get-SiteValueKind')
$inventory = @('Get-ItemProperty', 'Get-Item', 'Test-Path', 'Get-ChildItem', 'Get-Content',
    'Join-Path', '[guid]::NewGuid', 'Get-WindowsFeature', 'Get-Service', 'Get-CimInstance',
    'Get-LocalGroupMember', 'Get-LocalUser', 'Get-Command', 'Select-Object', 'Where-Object',
    'ForEach-Object', 'Sort-Object', 'Measure-Object', 'Write-Verbose', 'Out-String', 'ConvertFrom-Csv')
$allowed = @($inventory | Where-Object { $null -eq $cfg.allowed -or $_ -in $cfg.allowed } | Sort-Object)
$seconds = 30
if ($null -ne $cfg.timeout -and [int]$cfg.timeout -gt 0) { $seconds = [int]$cfg.timeout }
$reads = @('Get-Content','Get-Item','Get-ItemProperty','Get-ChildItem','Test-Path')
$roots = @()
if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') {
    $work = [string]$cfg.work_dir
    if (!$work) { $work = Join-Path ([IO.Path]::GetTempPath()) ('site-ak-' + [guid]::NewGuid()) }
    $work = [IO.Path]::GetFullPath($work)
    if ($null -ne $cfg.read_roots) { $roots = @($cfg.read_roots) }
    else {
        $roots = @($work)
        if ($env:SystemRoot) { $roots += $env:SystemRoot }
        if ($env:ProgramData) { $roots += Join-Path $env:ProgramData 'site-automation' }
    }
    $roots = @($roots | ForEach-Object { [IO.Path]::GetFullPath($_) } | Select-Object -Unique)
}

# Return the exact literal path to pass to the cmdlet. Reject alternate provider
# syntax and links in any existing component before entering the provider.
$pathGuard = {
    param($akInputPath, $akBase, $akRoots)
    if ($akInputPath -isnot [string] -or !$akInputPath -or
        $akInputPath -match '\.\.|^[\\/]{2}|[\x00-\x1f]') { return }
    if ($akInputPath -match '^Microsoft\.PowerShell\.Core[\\/]') { return }
    if ($akInputPath -match '^HKLM:\\' -and $akInputPath.Substring(5) -notmatch ':') { return $akInputPath }
    $akWindows = [IO.Path]::DirectorySeparatorChar -eq '\'
    if ($akInputPath -match '[*?`]|^(?:~|[\\])' -or
        ($akInputPath -match ':' -and !($akWindows -and
         $akInputPath -match '^[A-Za-z]:[\\/][^:]*$'))) { return }
    try {
        $akSeparator = [IO.Path]::DirectorySeparatorChar
        $akLocal = $akInputPath.Replace('\', $akSeparator)
        $akFull = [IO.Path]::GetFullPath([IO.Path]::Combine($akBase, $akLocal))
        $akComparison = if ($akWindows) { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
        foreach ($akRoot in $akRoots) {
            $akPrefix = $akRoot.TrimEnd($akSeparator) + $akSeparator
            if (!$akFull.Equals($akRoot, $akComparison) -and !$akFull.StartsWith($akPrefix, $akComparison)) { continue }
            for ($akPart = $akFull; $akPart; $akPart = [IO.Path]::GetDirectoryName($akPart)) {
                if ($akWindows -and [IO.Path]::GetFileName($akPart) -match
                    '[. ]$|^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') { return }
                try {
                    if ([IO.File]::GetAttributes($akPart) -band [IO.FileAttributes]::ReparsePoint) { return }
                } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {}
            }
            return $akFull
        }
    } catch {}
}
$facts = [ordered]@{ results = @(); clean = $true; ran_in = [ordered]@{ mode = 'ConstrainedLanguage'
    clean = $true; read_roots = $roots; commands = $allowed; proxies = $proxies; powershell = [string]$PSVersionTable.PSVersion }; leftover = @() }

# Only plain local variables can be changed. In particular, provider-qualified
# variables, session preferences and the proxy bindings are outside the dialect.
function Test-Local($variable) {
    $name = $variable.VariablePath.UserPath
    $name -match '^[a-z][a-z0-9_]*$' -and $name -notmatch '^(ps|ak|env|global|local|script|executioncontext|log.*event$|error|input|args|home|host|matches|ofs|foreach|switch|lastexitcode|outputencoding|shellid|enabledexperimentalfeatures)|preference$'
}

$auditArgument = "('/subcategory:' + `$name)"
$auditPattern = '(?:[A-Za-z][A-Za-z0-9 -]*|\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\})'
$fileBindings = @('$f = $env:SITE_AK_TEMP_INF',
    '$f = Join-Path $env:TEMP (''site-ak-'' + [guid]::NewGuid().ToString(''N'') + ''.inf'')',
    '$l = $f + ''.log''')

function Test-BoundBefore($use, $assignments) {
    for ($scope = $use.Parent; $scope; $scope = $scope.Parent) {
        if ($scope -isnot [System.Management.Automation.Language.StatementBlockAst] -and $scope -isnot [System.Management.Automation.Language.NamedBlockAst]) { continue }
        foreach ($assignment in $assignments) {
            if ($assignment.Parent -eq $scope -and $assignment.Extent.EndOffset -le $use.Extent.StartOffset) {
                return $true
            }
        }
    }
    return $false
}

function Test-Draft([string]$code, $row, $fixed) {
    if ($code -match '(?im)#requires\b|\busing\s+(module|assembly)\b') { return 'directive is not allowed' }
    $tokens = $null
    $errors = $null
    $tree = [System.Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$errors)
    if ($errors.Count) {
        return 'parse error'
    }
    if ($tree.ScriptRequirements) { return 'directive is not allowed' }

    $allowedTypes = @('int','long','string','bool')
    $nodes = $tree.FindAll({ $true }, $true)
    $commands = @($nodes | Where-Object { $_ -is [System.Management.Automation.Language.CommandAst] })
    $writes = @($commands | Where-Object { $_.GetCommandName() -in @('secedit.exe','Remove-Item') })
    $auditName = @($commands | Where-Object { $_.GetCommandName() -eq 'auditpol.exe' -and
        $_.CommandElements[2].Extent.Text -ceq $auditArgument })
    $bindings = @{ f = @(); l = @(); name = @() }
    foreach ($node in $nodes) {
        if ($node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left -is [System.Management.Automation.Language.VariableExpressionAst]) {
            $var = $node.Left.VariablePath.UserPath
            if ($bindings.ContainsKey($var)) { $bindings[$var] += $node }
            if ($writes.Count -and $var -in @('f','l') -and $node.Extent.Text -cnotin $fileBindings) {
                $row.refused += 'refused: export path assignment'
                return $row.refused[-1]
            }
            if ($auditName.Count -and $var -eq 'name') {
                $literal = $node.Right.Expression
                if ($node.Operator -ne 'Equals' -or $literal -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or
                    $literal.StringConstantType -notin @('SingleQuoted','DoubleQuoted') -or
                    $literal.Value -cnotmatch "\A$auditPattern\z") { return 'invalid audit subcategory assignment' }
            }
        }
        if ($node -is [System.Management.Automation.Language.ForEachStatementAst] -and
            (($writes.Count -and $node.Variable.VariablePath.UserPath -in @('f','l')) -or
             ($auditName.Count -and $node.Variable.VariablePath.UserPath -eq 'name'))) { return 'protected loop variable' }
    }
    foreach ($node in $nodes) {
        switch ($node.GetType().Name) {
            CommandAst {
                $name = $node.GetCommandName()
                if ($node.InvocationOperator -ne 'Unknown' -or !$name) { return 'indirect command is not allowed' }
                $elements = $node.CommandElements
                if ($name -in $proxies) {
                    if ($name -in @('secedit.exe','Remove-Item') -and !($fixed -is [bool] -and $fixed)) {
                        $row.refused += "refused: $name reserved for the fixed patterns"
                        return $row.refused[-1]
                    }
                    $parts = @($elements | ForEach-Object { $_.Extent.Text })
                    $form = $parts -join ' '
                    $ok = switch ($name) {
                        'secedit.exe' { $form -ceq 'secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l' }
                        'Remove-Item' { $form -cin @('Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue',
                            'Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue') }
                        'Get-SiteValueKind' {
                            $parts.Count -eq 5 -and $parts[0] -ceq 'Get-SiteValueKind' -and $parts[1] -ceq '-LiteralPath' -and $parts[3] -ceq '-Name' -and
                            @($elements[2,4] | Where-Object { $_ -isnot [System.Management.Automation.Language.VariableExpressionAst] -and
                                !($_ -is [System.Management.Automation.Language.StringConstantExpressionAst] -and $_.StringConstantType -eq 'SingleQuoted') }).Count -eq 0
                        }
                        'auditpol.exe' { $parts.Count -eq 4 -and $parts[0] -ceq 'auditpol.exe' -and
                            $parts[1] -ceq '/get' -and $parts[3] -ceq '/r' -and
                            (($elements[2] -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                              $elements[2].StringConstantType -in @('SingleQuoted','DoubleQuoted') -and
                              $elements[2].Value -cmatch "\A/subcategory:$auditPattern\z") -or
                             ($parts[2] -ceq $auditArgument -and
                              (Test-BoundBefore $node $bindings.name))) }
                    }
                    if ($ok -and $name -in @('Remove-Item','secedit.exe')) {
                        foreach ($var in @('f','l')) {
                            if (('$' + $var) -cin $parts -and !(Test-BoundBefore $node $bindings[$var])) { $ok = $false }
                        }
                    }
                    if (!$ok) { $row.refused += "refused: $name"; return $row.refused[-1] }
                } elseif ($name -notin $allowed) { return 'command is not allowed' }
                if ($name -in $reads) {
                    # Require one explicit path or a leading positional path.
                    $paths = @(for ($i = 1; $i -lt $elements.Count; $i++) {
                        $e = $elements[$i]
                        if ($e -is [System.Management.Automation.Language.CommandParameterAst] -and $e.ParameterName -in @('Path','LiteralPath')) {
                            if ($e.Argument) { $e.Argument } else { $elements[$i+1] }
                        } elseif ($i -eq 1 -and $e -isnot [System.Management.Automation.Language.CommandParameterAst]) { $e }
                    })
                    if ($paths.Count -ne 1 -or ($paths[0] -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                        !(& $pathGuard $paths[0].Value $work $roots))) {
                        $row.refused += "refused: $name"; return $row.refused[-1]
                    }
                }
                # Pipeline binding can supply parameters by property name. Bind
                # host reads explicitly and allow only data-processing sinks.
                if ($node.Parent -is [System.Management.Automation.Language.PipelineAst] -and $node.Parent.PipelineElements[0] -ne $node -and
                    $name -notin @('Select-Object','Where-Object','ForEach-Object','Sort-Object',
                        'Measure-Object','Write-Verbose','Out-String','ConvertFrom-Csv')) {
                    return 'unexpected pipeline input'
                }
                # A CIM provider can have side effects when it enumerates a class.
                if ($name -eq 'Get-CimInstance' -and $node.Extent.Text -notmatch
                    '^Get-CimInstance\s+-ClassName\s+Win32_ComputerSystem(?:\s+-ErrorAction\s+Stop)?$') {
                    return 'unexpected CIM query'
                }
                # The member-name overload of ForEach-Object invokes methods.
                if ($name -eq 'ForEach-Object' -and ($elements.Count -ne 2 -or
                    $elements[1] -isnot [System.Management.Automation.Language.ScriptBlockExpressionAst])) { return 'expected a script block' }
            }
            CommandParameterAst {
                if ($node.Parent.GetCommandName() -eq 'Remove-Item') { break }
                if ($node.ParameterName -notin @('Path','LiteralPath','Name','ClassName','Property','Filter',
                    'Force','Raw','Message','ErrorAction','WarningAction','Online','FeatureName',
                    'First','Last','Unique','Descending','Line','Word','Character','Quiet','PathType')) {
                    return 'parameter is not allowed'
                }
            }
            VariableExpressionAst {
                # Unix environment names are case sensitive.
                if ($node.VariablePath.DriveName -eq 'env' -and [IO.Path]::DirectorySeparatorChar -eq '/' -and
                    ($node.VariablePath.UserPath -split ':', 2)[1] -cnotin @('SITE_AK_TEMP_INF','TEMP','SystemRoot')) {
                    return 'variable is not allowed'
                }
                if ($node.Splatted -or (!(Test-Local $node) -and $node.VariablePath.UserPath -notin
                    @('_', 'LASTEXITCODE', 'env:SITE_AK_TEMP_INF','env:TEMP','env:SystemRoot'))) { return 'variable is not allowed' }
            }
            AssignmentStatementAst {
                if ($writes.Count -and $node.Extent.Text -ceq $fileBindings[2] -and
                    !(Test-BoundBefore $node $bindings.f)) { return 'unbound export path' }
                if ($node.Left -isnot [System.Management.Automation.Language.VariableExpressionAst] -or !(Test-Local $node.Left)) {
                    return 'only local variables can be assigned'
                }
            }
            ForEachStatementAst { if (!(Test-Local $node.Variable)) { return 'expected a local loop variable' } }
            UnaryExpressionAst {
                if ($node.TokenKind -notin @('Not','Exclaim','Minus','Plus','Bnot','Comma')) { return 'unary operator is not allowed' }
            }
            CommandExpressionAst {}
            PipelineAst { if ($node.Background) { return 'background pipeline is not allowed' } }
            MemberExpressionAst { if ($node.Static) { return 'static member is not allowed' } }
            InvokeMemberExpressionAst {
                if ('[guid]::NewGuid' -in $allowed -and $node.Extent.Text -cin @(
                    '[guid]::NewGuid()', "[guid]::NewGuid().ToString('N')")) { break }
                # the fixed REG_SZ pattern's own fallback for Evaluate-STIG's full session (the trial uses the Get-SiteValueKind proxy and never reaches it)
                if ($fixed -and $node.Extent.Text -cin @('(Get-Item -LiteralPath $path).GetValueKind($name)',
                    '(Get-Item -LiteralPath $path).GetValueKind($name).ToString()')) { break }
                return 'method is not allowed'
            }
            BinaryExpressionAst {
                if ($node.Operator -eq 'As') { return '-as is not allowed' }
                if ($node.Operator -in @('Is','IsNot') -and
                    ($node.Right -isnot [System.Management.Automation.Language.TypeExpressionAst] -or
                     $node.Right.TypeName.FullName -notin $allowedTypes)) { return 'expected an allowed literal type' }
            }
            ConvertExpressionAst { if ($node.Type.TypeName.FullName -notin $allowedTypes) { return 'cast is not allowed' } }
            TypeExpressionAst {
                if ($node.Parent -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and $node.TypeName.FullName -eq 'guid') { break }
                if ($node.TypeName.FullName -notin $allowedTypes) { return 'type is not allowed' } }
            TypeConstraintAst { if ($node.TypeName.FullName -notin $allowedTypes) { return 'cast is not allowed' } }
            default {
                if ($node.GetType().Name -notin @('ScriptBlockAst','NamedBlockAst','StatementBlockAst',
                    'ParenExpressionAst','ConstantExpressionAst','StringConstantExpressionAst',
                    'ExpandableStringExpressionAst','SubExpressionAst',
                    'ArrayExpressionAst','ArrayLiteralAst','ScriptBlockExpressionAst','IndexExpressionAst',
                    'IfStatementAst','ReturnStatementAst','TryStatementAst','CatchClauseAst','WhileStatementAst',
                    'ForStatementAst','BreakStatementAst','ContinueStatementAst')) { return 'syntax is not allowed' }
            }
        }
    }
    return ''
}

# Native/application and cmdlet handles are bound by the harness. No external
# command search path or general-purpose deletion command is installed.
function New-TrialState($fixed) {
    $state = [System.Management.Automation.Runspaces.InitialSessionState]::Create()
    $state.LanguageMode = 'ConstrainedLanguage'
    # Native execution looks up the FileSystem provider by its qualified name.
    foreach ($provider in [System.Management.Automation.Runspaces.InitialSessionState]::CreateDefault().Providers) {
        if ($provider.Name -in @('Registry','FileSystem')) {
            $state.Providers.Add($provider.Clone())
        }
    }
    $readHandles = @{}
    foreach ($name in $allowed) {
        if ($name -eq '[guid]::NewGuid') { continue }
        $cmd = Get-Command -Name $name -CommandType Cmdlet -ErrorAction SilentlyContinue
        if ($name -in $reads) { $readHandles[$name] = $cmd; continue }
        if ($cmd) { $state.Commands.Add([System.Management.Automation.Runspaces.SessionStateCmdletEntry]::new($name, $cmd.ImplementingType, $null)) }
    }
    $reader = {
        param($akRegistryPath, $akValueName)
        $akKey = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($akRegistryPath.Substring(6))
        try { [string]$akKey.GetValueKind($akValueName) } finally { if ($akKey) { $akKey.Close() } }
    }
    if ($env:SITE_AK_TEST -ceq '1' -and $env:SITE_AK_TEST_KIND) {
        $reader = { $env:SITE_AK_TEST_KIND }
    }
    $bindings = @{ VerbosePreference = 'Continue'; PSModuleAutoLoadingPreference = 'None'
        akPath = $env:SITE_AK_TEMP_INF; akGuard = $pathGuard; akRoots = $roots; akBase = $work
        akReads = $readHandles; akReader = $reader; akTemp = $env:TEMP; akSystemRoot = $env:SystemRoot
        akAuditPattern = $auditPattern
        akFileItem = (Get-Command Get-Item -CommandType Cmdlet)
        akEncoding = [Text.Encoding]::Default }
    foreach ($name in @('secedit.exe','auditpol.exe')) {
        if ($name -eq 'secedit.exe' -and !$fixed) { continue }
        $exe = $name
        if ($env:OS -eq 'Windows_NT') { $exe = Join-Path ([Environment]::SystemDirectory) $name }
        $bindings['ak' + $name.Split('.')[0]] = (Get-Command $exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1)
    }
    foreach ($entry in $bindings.GetEnumerator()) {
        $state.Variables.Add([System.Management.Automation.Runspaces.SessionStateVariableEntry]::new($entry.Key, $entry.Value, ''))
    }
    $functions = @{
        'secedit.exe' = {
            if ($args.Count -ne 7 -or $args[0] -cne '/export' -or $args[1] -cne '/areas' -or
                $args[2] -cne 'USER_RIGHTS' -or $args[3] -cne '/cfg' -or
                $args[4] -isnot [string] -or !$akPath.Equals($args[4], [StringComparison]::Ordinal) -or $args[5] -cne '/log' -or
                $args[6] -isnot [string] -or !("$akPath.log").Equals($args[6], [StringComparison]::Ordinal)) {
                $global:akDenied += @('refused: secedit.exe'); return
            }
            $global:LASTEXITCODE = -1
            & $akSecedit /export /areas USER_RIGHTS /cfg $akPath /log "$akPath.log"
        }
        'Remove-Item' = {
            if ($args.Count -ne 5 -or $args[0] -cne '-LiteralPath' -or $args[1] -isnot [string] -or
                !($akPath.Equals($args[1], [StringComparison]::Ordinal) -or
                    ("$akPath.log").Equals($args[1], [StringComparison]::Ordinal)) -or
                $args[2] -cne '-Force' -or $args[3] -cne '-ErrorAction' -or
                $args[4] -cne 'SilentlyContinue') { $global:akDenied += @('refused: Remove-Item'); return }
            [IO.File]::Delete($args[1])
        }
        'Get-SiteValueKind' = {
            param($LiteralPath, $Name)
            if ($LiteralPath -isnot [string] -or $LiteralPath -notmatch '^HKLM:\\' -or
                $LiteralPath -match '\.\.|[\x00-\x1f]' -or $Name -isnot [string] -or $Name.Length -gt 255) {
                $global:akDenied += @('refused: Get-SiteValueKind'); return
            }
            $akKind = & $akReader $LiteralPath $Name
            if ($akKind -cin @('String','DWord','MultiString','ExpandString','Binary','QWord')) { $akKind }
        }
        'auditpol.exe' = {
            if ($args.Count -ne 3 -or $args[0] -cne '/get' -or $args[1] -isnot [string] -or
                $args[1] -cnotmatch "\A/subcategory:$akAuditPattern\z" -or $args[2] -cne '/r') {
                $global:akDenied += @('refused: auditpol.exe'); return
            }
            $global:LASTEXITCODE = -1
            & $akAuditpol /get $args[1] /r
        }
    }
    foreach ($name in $readHandles.Keys) {
        $readFunction = {
            [CmdletBinding(PositionalBinding=$false)]
            param([Parameter(Position=0)]$Path, $LiteralPath, $Name, $Filter, $PathType,
                [switch]$Force, [switch]$Raw)
            $akCommand = $MyInvocation.MyCommand.Name
            $akInput = if ($PSBoundParameters.ContainsKey('LiteralPath')) { ,$LiteralPath } else { ,$Path }
            $akResolved = & $akGuard $akInput $akBase $akRoots
            if (!$akResolved -or ($PSBoundParameters.ContainsKey('Filter') -and
                ($Filter -isnot [string] -or $Filter -match '[\\/:]|\.\.'))) {
                $global:akDenied += @("refused: $akCommand"); return
            }
            $null = $PSBoundParameters.Remove('Path')
            $PSBoundParameters['LiteralPath'] = $akResolved
            if ($akCommand -ne 'Get-Content' -or $akResolved -match '^HKLM:') {
                & $akReads[$akCommand] @PSBoundParameters
                return
            }
            $null = $PSBoundParameters.Remove('Raw')
            $file = & $akFileItem @PSBoundParameters
            if (!$file) { return }
            try {
                $stream = [IO.File]::OpenRead($file.FullName)
                try {
                    # Bound the actual read as well as checking the file's length.
                    $bytes = if ($stream.Length -le 1MB) { ,([IO.BinaryReader]::new($stream).ReadBytes(1MB + 1)) }
                    if ($null -eq $bytes -or $bytes.Length -gt 1MB) {
                        $global:akDenied += @('refused: Get-Content exceeds 1 MB')
                        return
                    }
                } finally { $stream.Dispose() }
                if (!$bytes.Length) { return }
                $reader = [IO.StreamReader]::new([IO.MemoryStream]::new($bytes), $akEncoding, $true)
                try {
                    if ($Raw) { $reader.ReadToEnd() }
                    else { while ($null -ne ($line = $reader.ReadLine())) { $line } }
                } finally { $reader.Dispose() }
            } catch { $PSCmdlet.WriteError($_) }
        }
        $functions[$name] = if ($name -eq 'Get-ChildItem') {
            $readFunction.ToString().Replace('$LiteralPath, $Name,', '$LiteralPath, [switch]$Name,')
        } else { $readFunction }
    }
    foreach ($entry in $functions.GetEnumerator()) {
        if (!$fixed -and $entry.Key -in @('secedit.exe','Remove-Item')) { continue }
        $state.Commands.Add([System.Management.Automation.Runspaces.SessionStateFunctionEntry]::new($entry.Key, $entry.Value.ToString()))
    }
    return $state
}

function Invoke-Trial($code, $row, $fixed) {
    $space = [RunspaceFactory]::CreateRunspace((New-TrialState $fixed))
    $ps = [powershell]::Create()
    try {
        $space.Open()
        $null = $space.SessionStateProxy.Path.SetLocation([System.Management.Automation.WildcardPattern]::Escape($work))
        $ps.Runspace = $space
        # Rewrite only parsed environment references, including interpolation.
        $environment = @{ 'env:SITE_AK_TEMP_INF' = 'akPath'; 'env:TEMP' = 'akTemp'; 'env:SystemRoot' = 'akSystemRoot' }
        $tokens = $null
        $errors = $null
        $tree = [System.Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$errors)
        $references = $tree.FindAll({ param($n) $n -is [System.Management.Automation.Language.VariableExpressionAst] -and $n.VariablePath.DriveName -eq 'env' }, $true)
        foreach ($ref in ($references | Sort-Object { $_.Extent.StartOffset } -Descending)) {
            $code = $code.Remove($ref.Extent.StartOffset, $ref.Extent.EndOffset - $ref.Extent.StartOffset).Insert(
                $ref.Extent.StartOffset, '${' + $environment[$ref.VariablePath.UserPath] + '}')
        }
        $null = $ps.AddScript($code)
        $row.ran = $true
        [Console]::Error.WriteLine('ready')
        $values = $ps.Invoke()
        if ($values.Count -eq 1 -and $values[-1] -is [bool]) { $row.result = $values[-1] }
        else { $row.why = "no boolean ($($values.Count) output values)" }
    } finally {
        if ($row.ran) {
            $denied = $space.SessionStateProxy.GetVariable('akDenied')
            if ($denied) { $row.refused = @($denied); $row.result = $null; $row.why = $denied[-1] }
        }
        foreach ($record in $ps.Streams.Verbose) {
            if ($row.verbose.Count -eq 40) { $row.verbose[39] = '[cut]'; break }
            $line = $record.Message
            # Keep partial secrets out of the input to the caller's redactor.
            if ($line.Length -gt 4000) { $line = '[cut]' }
            $row.verbose += $line
        }
        $ps.Dispose()
        $space.Dispose()
    }
}

if ($Worker) {
    $row = [ordered]@{ ran = $false; result = $null; why = ''; verbose = @(); refused = @() }
    try {
        $row.why = Test-Draft $cfg.code $row $cfg.fixed
        if (!$row.why) { Invoke-Trial $cfg.code $row $cfg.fixed }
    }
    catch { $row.why = 'trial setup or execution failed' }
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    [Console]::WriteLine((ConvertTo-Json -InputObject $row -Depth 4 -Compress))
    return
}

function New-WorkerJob([IntPtr]$processHandle) {
    if (!('TrialWorkerJob' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class TrialWorkerJob {
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimits {
        public BasicLimits Basic;
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern SafeFileHandle CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetInformationJobObject(SafeFileHandle job, int kind, ref ExtendedLimits limits, int size);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);

    public static SafeFileHandle Attach(IntPtr process) {
        var job = CreateJobObject(IntPtr.Zero, null);
        var limits = new ExtendedLimits();
        limits.Basic.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (job.IsInvalid || !SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf(limits))
                          || !AssignProcessToJobObject(job, process)) {
            var error = new Win32Exception();
            job.Dispose();
            throw error;
        }
        return job;
    }
}
'@
    }
    [TrialWorkerJob]::Attach($processHandle)
}

function Invoke-Worker($draft, $row) {
    $packet = ConvertTo-Json -Compress -InputObject @{
        code = $draft.code; fixed = ($draft.fixed -is [bool] -and $draft.fixed)
        allowed = $allowed; work_dir = $work; read_roots = $roots
    }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $desktop = $PSVersionTable.PSEdition -eq 'Desktop'
    $engine = if ($desktop) { 'powershell.exe' } elseif ($IsWindows) { 'pwsh.exe' } else { 'pwsh' }
    $start.FileName = Join-Path $PSHOME $engine
    $start.Arguments = '-NoLogo -NoProfile -NonInteractive -File "' + $workerPath.Replace('"','\"') + '" -Worker'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.EnvironmentVariables.Remove('SITE_CHECK_CONFIG')
    $process = [Diagnostics.Process]::Start($start)
    $job = $null
    try {
        # The worker blocks on stdin until containment is established.
        if ($desktop) { $job = New-WorkerJob $process.Handle }
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $timeoutMs = 1000 * $seconds
        $output = $process.StandardOutput.ReadToEndAsync()
        $process.StandardInput.AutoFlush = $true
        $inputTask = $process.StandardInput.WriteLineAsync([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($packet)))
        # Allow up to four extra seconds for startup. A slow module import can
        # also use the trial budget, but the overall deadline remains bounded.
        $ready = $process.StandardError.ReadLineAsync()
        if (!$ready.Wait($timeoutMs + 4000)) { $row.why = 'timed out'; return }
        $null = $inputTask.GetAwaiter().GetResult()
        $process.StandardInput.Close()
        if ($ready.Result -and $ready.Result -cne 'ready') { throw 'worker setup failed' }
        $remaining = [int][math]::Max(0, $timeoutMs + 4000 - $clock.ElapsedMilliseconds)
        if (!$process.WaitForExit([math]::Min($timeoutMs, $remaining))) {
            $row.ran = $ready.Result -ceq 'ready'
            $row.why = 'timed out'
            return
        }
        if ($process.ExitCode -ne 0) { throw 'worker failed' }
        $reply = ConvertFrom-Json $output.GetAwaiter().GetResult()
        foreach ($field in @('ran','result','why','verbose','refused')) { $row[$field] = $reply.$field }
    } finally {
        if ($job) { $job.Dispose() }
        if (!$process.HasExited) {
            if (!$desktop) { $process.Kill($true) }
            elseif (!$job) { $process.Kill() }
            $process.WaitForExit()
        }
        $process.Dispose()
    }
}

$owned = $false
$workerFolder = $null
$workerPath = $null
$oldInf = $env:SITE_AK_TEMP_INF
try {
    foreach ($draft in $cfg.codes) {
        $row = [ordered]@{ key = $draft.key; valid = $false; why = ''; ran = $false; result = $null
            verbose = @(); refused = @(); seconds = 0 }
        $facts.results += $row
        if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
            $row.why = 'the host language policy prevents parser and runspace setup'
            continue
        }
        $claimed = $false
        $watch = [Diagnostics.Stopwatch]::StartNew()
        try {
            $row.why = Test-Draft $draft.code $row $draft.fixed
            $row.valid = !$row.why
            if (!$row.valid -or $cfg.trial -isnot [bool] -or !$cfg.trial) { continue }
            if ($owned) { $row.why = 'previous cleanup failed'; continue }
            if (![IO.Path]::GetFileName($work).StartsWith('site-ak-')) { throw 'invalid work folder' }
            $parent = [IO.Path]::GetDirectoryName($work)
            if (!$parent -or ![IO.Directory]::Exists($parent)) { throw 'missing parent' }
            $inf = [IO.Path]::Combine($work, 'policy.inf')
            # Rename claims the configured name atomically. Two WinRM jobs
            # requesting the same folder must never share cleanup ownership.
            $fresh = Join-Path $parent ('site-ak-' + [guid]::NewGuid())
            $null = New-Item -ItemType Directory -Path $fresh
            try { [IO.Directory]::Move($fresh, $work) }
            catch { [IO.Directory]::Delete($fresh); throw }
            $owned = $claimed = $true
            $env:SITE_AK_TEMP_INF = $inf
            if (!$workerPath) {
                if (!$workerFolder) {
                    $fresh = Join-Path ([IO.Path]::GetTempPath()) ('site-ak-w-' + [guid]::NewGuid())
                    $workerFolder = (New-Item -ItemType Directory -Path $fresh).FullName
                }
                # External scripts expose the loaded source. Inline invocations
                # expose the body of the running script block instead.
                $source = $MyInvocation.MyCommand.ScriptContents
                if (!$source -and $MyInvocation.MyCommand -is [System.Management.Automation.ScriptInfo]) {
                    $source = $MyInvocation.MyCommand.ScriptBlock.ToString()
                }
                if (!$source) { $source = [IO.File]::ReadAllText($PSCommandPath) }
                $path = Join-Path $workerFolder 'worker.ps1'
                [IO.File]::WriteAllText($path, $source, [Text.Encoding]::UTF8)
                $workerPath = $path
            }
            Invoke-Worker $draft $row
        } catch { $row.why = 'trial setup or execution failed' }
        finally {
            if ($claimed) {
                try { Remove-Item -LiteralPath $work -Recurse -Force; $owned = $false }
                catch {
                    try { $names = @(Get-ChildItem -LiteralPath $work -Force -Recurse -Name) }
                    catch { $names = @() }
                    if (!$names.Count) { $names = @('.') }
                    $facts.leftover += $names
                    $facts.clean = $facts.ran_in.clean = $false
                    $row.result = $null
                    $row.why = 'left files behind: ' + ($names -join ', ')
                }
                $env:SITE_AK_TEMP_INF = $oldInf
            }
            $row.seconds = [math]::Round($watch.Elapsed.TotalSeconds, 3)
        }
    }
} finally {
    if ($owned) {
        $env:SITE_AK_TEMP_INF = $oldInf
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($workerFolder) {
        try { Remove-Item -LiteralPath $workerFolder -Force -Recurse }
        catch {
            $facts.leftover += $workerFolder
            $facts.clean = $facts.ran_in.clean = $false
        }
    }
}
'###SITE-JSON### ' + (ConvertTo-Json -InputObject ([ordered]@{ findings = @(); facts = $facts }) -Depth 8 -Compress)
