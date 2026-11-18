#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.75.0 (playbooks/stig_answer_keys.yml), on the trial host: what its Evaluate-STIG is (its version, its answer file
template's element names, whether it compares ExpectedStatus before ValidationCode), then for each drafted
ValidationCode the fixed read-only check - PowerShell's own parser on the code's syntax tree - and, for the code that
passes it, one trial run as Evaluate-STIG runs it (pwsh, as root here), inside a read-only sandbox (systemd-run: the
kernel keeps the whole file system read-only; no sandbox, no trial). Writes nothing but its own folder (0700, removed at
the end). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stig_answer_host
short_description: An Evaluate-STIG answer key's ValidationCode - checked read-only, then tried once in a sandbox on this host
description:
  - Reads the host's Evaluate-STIG folder - its version, its answer file template's element names (an XML file of the
    folder with an AnswerKey and a ValidationCode), and whether its code compares ExpectedStatus.
  - For each code - the fixed read-only check (PowerShell's parser; allow-lists only - the syntax, the cmdlets and their
    parameters, each program's exact arguments) - and for the code that passes it, a trial run with pwsh, its own time
    limit, inside a read-only sandbox (systemd-run, looked at first). No sandbox that keeps the host read-only - no trial.
  - Writes nothing but a folder of its own (0700), removed at the end.
options:
  evaluate: {description: "Evaluate-STIG's folder or script on the host ('' = not read).", type: str, default: ''}
  pwsh: {description: "pwsh's path ('' = found on PATH).", type: str, default: ''}
  codes: {description: "[{id, code}]: the ValidationCodes.", type: list, elements: dict, default: []}
  trial: {description: Run the code that passes the check., type: bool, default: true}
  timeout: {description: "Seconds a trial may run.", type: int, default: 60}
  sandbox: {description: "systemd-run's path ('' = /usr/bin/systemd-run). Without a sandbox that holds (looked at first), no trial.", type: str, default: ''}
author: site automation
'''

RETURN = r'''
evaluate: {description: "{found, script, version, template, names, why, expected: {found, where, note}}", type: dict, returned: always}
gate: {description: "{id: {ok, why, used: [commands], calls: [[program, args...]]}}", type: dict, returned: always}
trial: {description: "{id: {ran, ok, valid, read, error, seconds, replayed: [command lines read outside, replayed inside]}}", type: dict, returned: always}
error: {description: "Why nothing was checked ('' = checked).", type: str, returned: always}
sandbox: {description: "{ok, why}: the trial's read-only sandbox (systemd-run), looked at before the first trial", type: dict, returned: when checked}
'''

import base64
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

from ansible.module_utils.basic import AnsibleModule

DOCUMENTED = ["ExpectedStatus", "ValidationCode", "ValidTrueStatus", "ValidTrueComment", "ValidFalseStatus", "ValidFalseComment"]
CODE_MAX = 6000

# The read-only check (1.75.0; 1.75.0c: allow-lists only). Fixed: never a setting. Every node of the code's syntax tree
# (PowerShell's own parser) is looked at; anything not on a list refuses the whole code (fails closed).
GATE_PS1 = r'''
param([string] $CodeFile)
$ErrorActionPreference = 'Stop'
$code = [IO.File]::ReadAllText($CodeFile)
$why = New-Object System.Collections.Generic.List[string]
$used = New-Object System.Collections.Generic.List[string]
$calls = New-Object System.Collections.Generic.List[object]
function No([string] $w) { if (-not $why.Contains($w)) { $why.Add($w) } }
# 1.75.0c: allow-lists only. A syntax node, a cmdlet, a cmdlet parameter, a program or a program argument that is not
# listed here refuses the whole code.
$nodes = @('ScriptBlockAst', 'NamedBlockAst', 'StatementBlockAst', 'PipelineAst', 'CommandAst', 'CommandExpressionAst',
           'CommandParameterAst', 'StringConstantExpressionAst', 'ExpandableStringExpressionAst', 'ConstantExpressionAst',
           'VariableExpressionAst', 'AssignmentStatementAst', 'UnaryExpressionAst', 'BinaryExpressionAst', 'ParenExpressionAst',
           'SubExpressionAst', 'ArrayExpressionAst', 'ArrayLiteralAst', 'IndexExpressionAst', 'MemberExpressionAst',
           'InvokeMemberExpressionAst', 'ScriptBlockExpressionAst', 'IfStatementAst', 'ForEachStatementAst', 'ReturnStatementAst',
           'BreakStatementAst', 'ContinueStatementAst', 'TypeExpressionAst', 'TypeConstraintAst', 'ConvertExpressionAst',
           'FileRedirectionAst', 'MergingRedirectionAst', 'TryStatementAst', 'CatchClauseAst')
# the cmdlets and, for each, the only parameters it may be given (their full names)
$cmdlets = @{
    'Get-Content' = @('Path', 'LiteralPath', 'TotalCount', 'Head', 'First', 'Tail', 'Last', 'Raw', 'Delimiter', 'Encoding', 'ReadCount', 'ErrorAction')
    'Select-String' = @('Pattern', 'Path', 'LiteralPath', 'SimpleMatch', 'CaseSensitive', 'Quiet', 'NotMatch', 'AllMatches', 'List', 'Raw',
                        'Context', 'InputObject', 'Encoding', 'ErrorAction')
    'Test-Path' = @('Path', 'LiteralPath', 'PathType', 'ErrorAction')
    'Get-Item' = @('Path', 'LiteralPath', 'Force', 'ErrorAction')
    'Get-ChildItem' = @('Path', 'LiteralPath', 'Filter', 'Include', 'Exclude', 'Recurse', 'Depth', 'File', 'Directory', 'Hidden', 'Force',
                        'Name', 'ErrorAction')
    'Where-Object' = @('FilterScript')
    'ForEach-Object' = @('Process', 'Begin', 'End')
    'Select-Object' = @('First', 'Last', 'Skip', 'SkipLast', 'Unique', 'ExpandProperty', 'Property', 'Index')
    'Measure-Object' = @('Property', 'Sum', 'Minimum', 'Maximum', 'Average', 'Line', 'Word', 'Character')
    'Sort-Object' = @('Property', 'Descending', 'Unique')
    'Write-Verbose' = @('Message')
    'Join-Path' = @('Path', 'ChildPath', 'AdditionalChildPath')
    'Split-Path' = @('Path', 'Parent', 'Leaf', 'LeafBase', 'Extension', 'NoQualifier')
}
$external = @('/usr/bin/stat', '/usr/bin/find', '/usr/sbin/sysctl', '/usr/bin/sysctl', '/usr/bin/rpm', '/usr/bin/systemctl',
              '/usr/sbin/getenforce', '/usr/sbin/sestatus', '/usr/bin/grep', '/usr/bin/getent', '/usr/bin/id', '/usr/sbin/auditctl')
# types a value may be cast to or tested against; none of them is called (static calls: the list below only)
$types = @('regex', 'version', 'int', 'int32', 'int64', 'long', 'string', 'bool', 'boolean', 'char',
           'system.management.automation.errorrecord')
$static = @{ regex = @('Match', 'Matches', 'IsMatch'); version = @('Parse') }
# a string's own reading methods - only on a value that is a string for certain (a literal, a [string] cast, -join)
$strMethods = @('Trim', 'TrimStart', 'TrimEnd', 'StartsWith', 'EndsWith', 'Contains', 'ToLower', 'ToUpper', 'ToLowerInvariant',
                'ToUpperInvariant', 'Substring', 'IndexOf', 'LastIndexOf', 'PadLeft', 'PadRight', 'Equals', 'Split', 'Replace')
$badVars = @('ExecutionContext', 'Host', 'PSCmdlet', 'PSDefaultParameterValues', 'MyInvocation')
$unaryOk = @('Not', 'Exclaim', 'Minus', 'Plus', 'Join', 'Isplit', 'Csplit', 'Bnot', 'Comma')
$stepOps = @('PlusPlus', 'MinusMinus', 'PostfixPlusPlus', 'PostfixMinusMinus')
$binaryOk = @('And', 'Or', 'Xor', 'Is', 'IsNot', 'As', 'Plus', 'Minus', 'Multiply', 'Divide', 'Rem', 'DotDot', 'Format', 'Join',
              'Band', 'Bor', 'Bxor', 'Shl', 'Shr',
              'Ieq', 'Ine', 'Ige', 'Igt', 'Ilt', 'Ile', 'Ilike', 'Inotlike', 'Imatch', 'Inotmatch', 'Ireplace', 'Icontains',
              'Inotcontains', 'Iin', 'Inotin', 'Isplit', 'Ceq', 'Cne', 'Cge', 'Cgt', 'Clt', 'Cle', 'Clike', 'Cnotlike', 'Cmatch',
              'Cnotmatch', 'Creplace', 'Ccontains', 'Cnotcontains', 'Cin', 'Cnotin', 'Csplit')
# the programs' argument grammars: every argument matched whole, in its place; nothing else is passed
$rxPath = '^/[A-Za-z0-9._/*?+@,:-]*$'
$rxPkg = '^[A-Za-z0-9][A-Za-z0-9._+-]*$'
$rxQf = '^(?:%\{(?:NAME|VERSION|RELEASE|ARCH|EPOCH|EPOCHNUM|NEVRA|NEVR|NVRA|NVR|VENDOR|INSTALLTIME|BUILDTIME)(?::date)?\}|[A-Za-z0-9 ._,:=/|-]|\\n)+$'
$rxKey = '^[a-z0-9_][a-z0-9_.]*$'
$rxStatFmt = '^(?=.*%)(?:%[UGaAugn]|[ :])+$'
$rxUnit = '^[A-Za-z0-9][A-Za-z0-9@._:\\-]*$'
$rxName = '^[A-Za-z_][A-Za-z0-9._-]*$'
$findFlags = @('-xdev', '-mount', '-nouser', '-nogroup', '-prune', '-o', '-or', '-a', '-and', '-not', '!', '(', ')', '-print', '-depth', '-empty')
$findVals = [ordered]@{
    '-type' = '^[fdlbcps](?:,[fdlbcps])*$'
    '-perm' = '^[-/]?(?:[0-7]{1,4}|[ugoa]*[+=-][rwxXst]+(?:,[ugoa]*[+=-][rwxXst]+)*)$'
    '-uid' = '^[+-]?[0-9]+$'; '-gid' = '^[+-]?[0-9]+$'
    '-user' = $rxName; '-group' = $rxName
    '-name' = '^[A-Za-z0-9._*?/\[\]^!+@,:~][A-Za-z0-9._*?/\[\]^!+@,:~-]*$'; '-iname' = '^[A-Za-z0-9._*?/\[\]^!+@,:~][A-Za-z0-9._*?/\[\]^!+@,:~-]*$'
    '-path' = '^[A-Za-z0-9._*?/\[\]^!+@,:~][A-Za-z0-9._*?/\[\]^!+@,:~-]*$'; '-ipath' = '^[A-Za-z0-9._*?/\[\]^!+@,:~][A-Za-z0-9._*?/\[\]^!+@,:~-]*$'
    '-maxdepth' = '^[0-9]+$'; '-mindepth' = '^[0-9]+$'
    '-fstype' = '^[a-z0-9]+$'
    '-printf' = '^(?:%[pfugmMUGhklsP]|[A-Za-z0-9 :,=._-]|\\n)+$'
}
$grepFlags = @('-i', '-r', '-h', '-E', '-q', '-s', '-n', '-l', '-c', '-v', '-w', '-x')
function Gram([string] $p, [string[]] $a) {
    $n = $a.Count
    switch -CaseSensitive ($p) {
        '/usr/bin/rpm' {
            if ($n -eq 2 -and $a[0] -ceq '-q' -and $a[1] -cmatch $rxPkg) { return '' }
            if ($n -eq 4 -and $a[0] -ceq '-q' -and @('--qf', '--queryformat') -ccontains $a[1] -and $a[2] -cmatch $rxQf -and $a[3] -cmatch $rxPkg) { return '' }
            return 'rpm: only -q PACKAGE, or -q --qf FORMAT PACKAGE (the FORMAT of %{NAME}-like tags)'
        }
        '/usr/sbin/sysctl' { if ($n -eq 2 -and $a[0] -ceq '-n' -and $a[1] -cmatch $rxKey) { return '' }; return 'sysctl: only -n KEY' }
        '/usr/bin/sysctl' { if ($n -eq 2 -and $a[0] -ceq '-n' -and $a[1] -cmatch $rxKey) { return '' }; return 'sysctl: only -n KEY' }
        '/usr/sbin/auditctl' { if ($n -eq 1 -and $a[0] -ceq '-l') { return '' }; return 'auditctl: only -l (the list)' }
        '/usr/sbin/getenforce' { if ($n -eq 0) { return '' }; return 'getenforce: no arguments' }
        '/usr/sbin/sestatus' { if ($n -eq 0) { return '' }; return 'sestatus: no arguments' }
        '/usr/bin/stat' {
            if ($n -ge 3 -and $a[0] -ceq '-c' -and $a[1] -cmatch $rxStatFmt) {
                $bad = @($a | Select-Object -Skip 2 | Where-Object { $_ -cnotmatch $rxPath })
                if (-not $bad) { return '' }
            }
            return 'stat: only -c FORMAT (of %U %G %a %A %u %g %n) and paths (/...)'
        }
        '/usr/bin/systemctl' {
            if ($n -eq 2 -and @('is-enabled', 'is-active') -ccontains $a[0] -and $a[1] -cmatch $rxUnit) { return '' }
            if ($n -eq 4 -and $a[0] -ceq 'show' -and $a[1] -ceq '-p' -and $a[2] -cmatch '^[A-Za-z]+$' -and $a[3] -cmatch $rxUnit) { return '' }
            return 'systemctl: only is-enabled UNIT, is-active UNIT or show -p PROPERTY UNIT'
        }
        '/usr/bin/getent' {
            if ($n -ge 1 -and $n -le 2 -and @('passwd', 'group', 'shadow', 'gshadow') -ccontains $a[0] -and ($n -eq 1 -or $a[1] -cmatch $rxName)) { return '' }
            return 'getent: only passwd, group, shadow or gshadow, and a name'
        }
        '/usr/bin/id' {
            $i = 0; while ($i -lt $n -and @('-u', '-g', '-G', '-n') -ccontains $a[$i]) { $i++ }
            if ($i -eq $n -or ($i -eq $n - 1 -and $a[$i] -cmatch $rxName)) { return '' }
            return 'id: only -u, -g, -G, -n and a user name'
        }
        '/usr/bin/grep' {
            $i = 0; while ($i -lt $n -and $grepFlags -ccontains $a[$i]) { $i++ }
            $pats = 0; while ($i + 1 -lt $n -and $a[$i] -ceq '-e') { $i += 2; $pats++ }
            if ($pats -eq 0) { if ($i -lt $n -and -not $a[$i].StartsWith('-')) { $i++ } else { return 'grep: the flags (' + ($grepFlags -join ' ') + ', each alone), a pattern (or -e PATTERN) and paths (/...)' } }
            while ($i -lt $n) { if ($a[$i] -cnotmatch $rxPath) { return 'grep: ' + $a[$i] + ' is not a path (/...) - the flags (' + ($grepFlags -join ' ') + ', each alone) come first' }; $i++ }
            return ''
        }
        '/usr/bin/find' {
            $i = 0; while ($i -lt $n -and $a[$i] -cmatch $rxPath) { $i++ }
            if ($i -eq 0) { return 'find: a path (/...) first' }
            while ($i -lt $n) {
                $t = $a[$i]
                if ($findFlags -ccontains $t) { $i++; continue }
                if (@($findVals.Keys) -ccontains $t) {
                    if ($i + 1 -lt $n -and $a[$i + 1] -cmatch $findVals[$t]) { $i += 2; continue }
                    return 'find: ' + $t + ' with a value it does not take'
                }
                return 'find: ' + $t + ' is not allowed (only ' + (($findFlags + @($findVals.Keys)) -join ' ') + ')'
            }
            return ''
        }
    }
    return 'not an allowed program'
}
function TypeKey([string] $n) {
    $k = $n.ToLower().TrimEnd(']').TrimEnd('[')
    if ($k.StartsWith('system.')) {
        switch ($k) { 'system.text.regularexpressions.regex' { return 'regex' } 'system.version' { return 'version' }
                      'system.string' { return 'string' } 'system.int32' { return 'int' } 'system.int64' { return 'int64' }
                      'system.boolean' { return 'bool' } 'system.char' { return 'char' } }
        return $k
    }
    if ($k -eq 'int32') { return 'int' }
    return $k
}
# a plain local variable: $x or $local:x - no other scope, no drive ($env:), not splatted, not one of PowerShell's own objects
function PlainVar($v) {
    if ($v -isnot [System.Management.Automation.Language.VariableExpressionAst] -or $v.Splatted) { return $false }
    $vp = $v.VariablePath
    return (-not $vp.IsDriveQualified) -and ($vp.IsUnqualified -or $vp.IsLocal) -and -not ($vp.IsGlobal -or $vp.IsScript -or $vp.IsPrivate) -and ($badVars -notcontains $vp.UserPath)
}
# the expression inside parentheses and one-element pipelines: ($x) -> $x
function Inner($e) {
    while ($true) {
        if ($e -is [System.Management.Automation.Language.ParenExpressionAst]) { $e = $e.Pipeline; continue }
        if ($e -is [System.Management.Automation.Language.PipelineAst] -and $e.PipelineElements.Count -eq 1 -and
            $e.PipelineElements[0] -is [System.Management.Automation.Language.CommandExpressionAst] -and $e.PipelineElements[0].Redirections.Count -eq 0) {
            $e = $e.PipelineElements[0].Expression; continue
        }
        return $e
    }
}
# a string for certain: a literal, "...", [string]x, x -join y, -join x, x -f y; or an element of x -split y
function IsStr($e) {
    $e = Inner $e
    if ($e -is [System.Management.Automation.Language.StringConstantExpressionAst] -or $e -is [System.Management.Automation.Language.ExpandableStringExpressionAst]) { return $true }
    if ($e -is [System.Management.Automation.Language.ConvertExpressionAst] -and (TypeKey $e.Type.TypeName.FullName) -eq 'string') { return $true }
    if ($e -is [System.Management.Automation.Language.BinaryExpressionAst] -and @('Join', 'Format') -contains [string]$e.Operator) { return $true }
    if ($e -is [System.Management.Automation.Language.UnaryExpressionAst] -and [string]$e.TokenKind -eq 'Join') { return $true }
    if ($e -is [System.Management.Automation.Language.IndexExpressionAst]) {
        $t = Inner $e.Target
        if ($t -is [System.Management.Automation.Language.BinaryExpressionAst] -and @('Split', 'Csplit', 'Isplit') -contains [string]$t.Operator) { return $true }
    }
    return $false
}
# the cmdlet a script block is given to (ForEach-Object / Where-Object), or ''
function BlockOwner($sb) {
    $p = $sb.Parent
    if ($p -is [System.Management.Automation.Language.CommandParameterAst]) { $p = $p.Parent }
    if ($p -is [System.Management.Automation.Language.CommandAst] -and $p.CommandElements[0] -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
        $nm = $p.CommandElements[0].Value
        if (@('ForEach-Object', 'Where-Object') -contains $nm) { return $nm }
    }
    return ''
}
if ($code.Length -gt 6000) { No 'it is over 6000 characters' }
if ($code.Contains([string][char]96)) { No 'it has a backtick (an escape that can hide a command name)' }
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$errs)
if ($errs.Count) { No ('it does not parse: ' + $errs[0].Message) }
foreach ($n in $ast.FindAll({ $true }, $true)) {
    $k = $n.GetType().Name
    if ($nodes -notcontains $k) { No ('it uses ' + $k.Replace('Ast', '') + ' (' + $n.Extent.Text.Split("`n")[0].Trim() + ') - not on the allowed list'); continue }
    switch ($k) {
        'ScriptBlockExpressionAst' {
            if (-not (BlockOwner $n)) { No ('it has a script block not given to ForEach-Object or Where-Object: ' + $n.Extent.Text.Split("`n")[0].Trim()) }
        }
        'CommandAst' {
            $first = $n.CommandElements[0]
            if (@('Unknown', 'Ampersand') -notcontains [string]$n.InvocationOperator) { No ('it runs a command with ' + [string]$n.InvocationOperator); continue }
            if ($first -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) {
                No ('it runs a command from an expression or a variable: ' + $first.Extent.Text); continue
            }
            $name = $first.Value
            $els = @($n.CommandElements | Select-Object -Skip 1)
            $cn = @($cmdlets.Keys | Where-Object { $_ -eq $name })
            if ($cn.Count -eq 1) {
                $c = $cn[0]
                foreach ($e in $els) {
                    if ($e -is [System.Management.Automation.Language.CommandParameterAst] -and $cmdlets[$c] -notcontains $e.ParameterName) {
                        No ($c + ' is given -' + $e.ParameterName + ' (its allowed parameters: ' + ($cmdlets[$c] -join ', ') + ')')
                    }
                }
                if (@('ForEach-Object', 'Where-Object') -contains $c) {
                    # a literal script block only (positional, or after its parameter): a member's name or a variable there
                    # calls a method (ForEach-Object $m with $m = 'Delete')
                    $i = 0
                    while ($i -lt $els.Count) {
                        $e = $els[$i]
                        if ($e -is [System.Management.Automation.Language.ScriptBlockExpressionAst]) { $i++; continue }
                        if ($e -is [System.Management.Automation.Language.CommandParameterAst]) {
                            if ($e.Argument -is [System.Management.Automation.Language.ScriptBlockExpressionAst]) { $i++; continue }
                            if ($null -eq $e.Argument -and $i + 1 -lt $els.Count -and $els[$i + 1] -is [System.Management.Automation.Language.ScriptBlockExpressionAst]) { $i += 2; continue }
                        }
                        No ($c + ' is given ' + $e.Extent.Text + ' - a literal script block only'); $i++
                    }
                }
                if (-not $used.Contains($c)) { $used.Add($c) }
                continue
            }
            if ($external -cnotcontains $name) { No ('it runs ' + $name + ' (not one of the allowed commands)'); continue }
            $args2 = @(); $fixed = $true
            for ($i = 0; $i -lt $els.Count; $i++) {
                $e = $els[$i]
                $prev = if ($i -eq 0) { $first } else { $els[$i - 1] }
                if ($prev.Extent.EndOffset -eq $e.Extent.StartOffset) { No ($name + ' is given ' + $prev.Extent.Text + $e.Extent.Text + ' - write a space between its arguments'); $fixed = $false }
                if ($e -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
                    if ([string]$e.StringConstantType -eq 'BareWord' -and $e.Value -match '[*?\[]' -and -not $e.Value.StartsWith('/')) {
                        No ($name + ' is given ' + $e.Extent.Text + ' unquoted - PowerShell would expand it to file names: quote it'); $fixed = $false
                    }
                    $args2 += $e.Value
                }
                elseif ($e -is [System.Management.Automation.Language.CommandParameterAst] -and $null -eq $e.Argument) { $args2 += $e.Extent.Text }
                elseif ($e -is [System.Management.Automation.Language.ConstantExpressionAst] -and $e.Extent.Text -match '^-?[0-9]+$') { $args2 += $e.Extent.Text }
                else { No ($name + ' is given something that is not a fixed word: ' + $e.Extent.Text); $fixed = $false }
            }
            if (-not $fixed) { continue }
            $bad = Gram $name $args2
            if ($bad) { No ('it runs ' + $name + ' ' + ($args2 -join ' ') + ' - ' + $bad) }
            else { $calls.Add([string[]](@($name) + @($args2))); if (-not $used.Contains($name)) { $used.Add($name) } }
        }
        'TypeExpressionAst' { if ($types -notcontains (TypeKey $n.TypeName.FullName)) { No ('it uses the type [' + $n.TypeName.FullName + ']') } }
        'TypeConstraintAst' { if ($types -notcontains (TypeKey $n.TypeName.FullName)) { No ('it uses the type [' + $n.TypeName.FullName + ']') } }
        'ConvertExpressionAst' { if ($types -notcontains (TypeKey $n.Type.TypeName.FullName)) { No ('it uses the type [' + $n.Type.TypeName.FullName + ']') } }
        'InvokeMemberExpressionAst' {
            if ($n.Member -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { No 'it calls a method named by an expression'; continue }
            $m = $n.Member.Value
            if ($n.Static) {
                $tk = if ($n.Expression -is [System.Management.Automation.Language.TypeExpressionAst]) { TypeKey $n.Expression.TypeName.FullName } else { '' }
                if (-not $static.ContainsKey($tk) -or $static[$tk] -cnotcontains $m) { No ('it calls ' + $n.Extent.Text) }
            } elseif ($strMethods -cnotcontains $m) {
                No ('it calls the method ' + $m + '()')
            } elseif (-not (IsStr $n.Expression)) {
                # a method of a value whose type is not known (a file: (Get-Item x).Replace(...)) - refused
                No ('it calls ' + $m + '() on ' + $n.Expression.Extent.Text + ' - not a string for certain')
            }
        }
        'AssignmentStatementAst' {
            $l = $n.Left
            if ($l -is [System.Management.Automation.Language.ConvertExpressionAst]) { $l = $l.Child }
            if (-not (PlainVar $l) -and -not ($l -is [System.Management.Automation.Language.VariableExpressionAst] -and $l.VariablePath.UserPath -eq 'null')) {
                No ('it assigns to ' + $n.Left.Extent.Text + ' (a plain local variable only)')
            }
        }
        'UnaryExpressionAst' {
            $tk = [string]$n.TokenKind
            if ($stepOps -contains $tk) { if (-not (PlainVar $n.Child)) { No ('it changes ' + $n.Extent.Text + ' (++ and -- on a plain local variable only)') } }
            elseif ($unaryOk -notcontains $tk) { No ('it uses the operator ' + $tk + ': ' + $n.Extent.Text) }
        }
        'BinaryExpressionAst' {
            $op = [string]$n.Operator
            if ($binaryOk -cnotcontains $op) { No ('it uses the operator ' + $op + ': ' + $n.Extent.Text) }
            elseif (@('Is', 'IsNot', 'As') -contains $op -and $n.Right -isnot [System.Management.Automation.Language.TypeExpressionAst]) { No ('it uses -' + $op.ToLower() + ' with a type that is not written as [type]: ' + $n.Extent.Text) }
        }
        'VariableExpressionAst' {
            if ($n.VariablePath.UserPath -ne 'null' -and -not (PlainVar $n)) { No ('it uses ' + $n.Extent.Text + ' (plain local variables only)') }
        }
        'MemberExpressionAst' {
            if ($n.Member -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { No ('it reads a member named by an expression: ' + $n.Extent.Text) }
            elseif (@('psobject', 'psbase', 'psadapted', 'psextended') -contains $n.Member.Value.ToLower()) { No ('it uses ' + $n.Extent.Text) }
            elseif ($n.Static -and -not ($n.Expression -is [System.Management.Automation.Language.TypeExpressionAst] -and $types -contains (TypeKey $n.Expression.TypeName.FullName))) { No ('it uses ' + $n.Extent.Text) }
        }
        'FileRedirectionAst' {
            if (-not ($n.Location -is [System.Management.Automation.Language.VariableExpressionAst] -and $n.Location.VariablePath.UserPath -eq 'null')) {
                No ('it writes to ' + $n.Location.Extent.Text)
            }
        }
    }
}
@{ ok = ($why.Count -eq 0); why = @($why); used = @($used); calls = $calls.ToArray() } | ConvertTo-Json -Compress -Depth 4
'''

# The trial: the code as Evaluate-STIG runs it - its result, and what it wrote with Write-Verbose (what it read)
TRIAL_PS1 = r'''
$code = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@CODE@'))
try {
    $sb = [scriptblock]::Create($code)
    $VerbosePreference = 'Continue'           # what the code says it read (Evaluate-STIG leaves it off: no effect there)
    $all = @(& $sb 4>&1)
    $read = @($all | Where-Object { $_ -is [System.Management.Automation.VerboseRecord] } | ForEach-Object { [string]$_.Message })
    $out = @($all | Where-Object { $_ -isnot [System.Management.Automation.VerboseRecord] })
    $vals = @($out | ForEach-Object { if ($_ -is [bool]) { $_ } elseif ($null -eq $_) { 'null' } else { [string]$_ } })
    $kinds = @($out | ForEach-Object { if ($null -eq $_) { 'null' } else { $_.GetType().Name } })
    @{ ok = $true; values = $vals; kinds = $kinds; read = ($read -join "`n") } | ConvertTo-Json -Compress -Depth 4
} catch {
    @{ ok = $false; error = ([string]$_) } | ConvertTo-Json -Compress -Depth 3
}
'''


# 1.76.0: the trial runs in a kernel-enforced sandbox (a transient systemd unit): the whole file system read-only, its
# OWN /tmp, /var/tmp and /dev/shm (never the host's: a host FIFO or file there is not reachable), the host's network
# namespace (net.* settings are the host's), NO socket of any family (no D-Bus, no systemd, no audit, no network), its own
# processes only, no new privileges. As root: a UID of its own - unused, in no passwd entry, owning no process - that
# keeps only the right to read (CAP_DAC_READ_SEARCH): it cannot signal or trace any host process. A code the read-only
# check let through by mistake still cannot change the host. No sandbox: no trial.
# The rule: an answer that depends on a namespace, a socket or a private mount (/tmp, /var/tmp, /dev/shm) is never read
# inside the sandbox - it is replayed (run once outside, the exact command line the check allowed) or the code is not
# tried.
IPC_KEYS = ["kernel.shmmax", "kernel.shmall", "kernel.shmmni", "kernel.shm_rmid_forced", "kernel.shm_next_id",
            "kernel.msgmax", "kernel.msgmnb", "kernel.msgmni", "kernel.msg_next_id", "kernel.sem", "kernel.sem_next_id",
            "fs.mqueue.msg_max", "fs.mqueue.msgsize_max", "fs.mqueue.queues_max", "fs.mqueue.msg_default",
            "fs.mqueue.msgsize_default"]
# PrivateIPC keeps SysV IPC and message queues off the host: inside, these show the sandbox's own - hidden
IPC_PATHS = ["/proc/sysvipc", "/dev/mqueue"] + ["/proc/sys/" + k.replace(".", "/") for k in IPC_KEYS]
TMP_ROOTS = ["/tmp", "/var/tmp", "/dev/shm"]
SANDBOX = ["ProtectSystem=strict", "ReadOnlyPaths=/", "ProtectHome=read-only", "PrivateTmp=yes", "PrivateDevices=yes",
           "TemporaryFileSystem=/dev/shm", "PrivateIPC=yes"] + ["InaccessiblePaths=-%s" % x for x in IPC_PATHS] + [
           "NoNewPrivileges=yes", "RestrictSUIDSGID=yes", "LockPersonality=yes", "RestrictNamespaces=yes",
           "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectKernelLogs=yes", "ProtectControlGroups=yes",
           "ProtectClock=yes", "ProtectHostname=yes",
           # other UIDs' processes hidden; not ProcSubset=pid - it hides /proc/sys and /proc/cmdline, which checks read
           "ProtectProc=invisible",
           "SystemCallFilter=~@privileged @debug",
           # no address family at all: an allow-list of AF_UNIX, then AF_UNIX taken out (systemd-run does not take "none")
           "RestrictAddressFamilies=AF_UNIX", "RestrictAddressFamilies=~AF_UNIX", "IPAddressDeny=any",
           "CapabilityBoundingSet=CAP_DAC_READ_SEARCH"]
# systemd 257 and later: a PID namespace of its own too (RHEL 10); before, the UID of its own is what keeps host
# processes out of reach (no CAP_KILL, CAP_SYS_PTRACE: a signal to another UID is refused by the kernel)
SANDBOX_PIDS = ["PrivatePIDs=yes"]
CAP_DAC_READ_SEARCH = 1 << 2
# the trial's own UID (as root): high, past the container ranges (systemd: up to 1879048191), never a passwd entry
UID_FIRST, UID_LAST = 2000000000, 2100000000
# what is replayed: every call of these programs (a daemon's socket, the kernel's audit socket, NSS - sssd, LDAP)...
REPLAY_ALWAYS = ["/usr/sbin/auditctl", "/usr/bin/systemctl", "/usr/bin/getent", "/usr/bin/id"]
# ...and every call of these when one of a code's calls needs it: an IPC setting (the namespace), a user or group name
# (NSS: stat %U %G, find -user -group -nouser -nogroup and -printf %u %g), a path in or above /tmp, /var/tmp, /dev/shm
# (find and stat only look at file entries, they never open one: a host FIFO there is safe outside)
REPLAY_IF = ["/usr/sbin/sysctl", "/usr/bin/sysctl", "/usr/bin/stat", "/usr/bin/find"]
REPLAY_MAX = 1048576
PROBE_RO = ["/", "/etc", "/usr", "/var", "/var/lib", "/var/log", "/opt", "/srv", "/root", "/home", "/boot", "/run",
            "/proc/sys/kernel/hostname", "/proc/sys/vm/swappiness", "/proc/sys/net/ipv4/ip_forward", "/sys/kernel", "/sys/fs/cgroup"]
PROBE_PRIVATE = ["/tmp", "/var/tmp", "/dev/shm"]
# the probe, inside the sandbox, writing nothing: what is writable (test -w); that /tmp, /var/tmp and /dev/shm are its
# own; whether a socket of any family can be opened (and /run/systemd/private, D-Bus reached); whether an IPC setting
# can be read; its uid, its effective capabilities, how many processes it sees
PROBE_PS1 = r"""
$w = @(foreach ($p in @(@RO@)) { if (Test-Path -LiteralPath $p) { & /usr/bin/test -w $p; if ($LASTEXITCODE -eq 0) { $p } } })
$priv = @{}
foreach ($p in @('/tmp', '/var/tmp', '/dev/shm')) { $priv[$p] = [string](& /usr/bin/stat -L -c '%d:%i' $p 2>$null) }
$sock = @()
foreach ($t in @(@('Unix', 'Stream'), @('Unix', 'Dgram'), @('InterNetwork', 'Stream'), @('InterNetwork', 'Dgram'), @('InterNetworkV6', 'Stream'),
                @(16, 'Dgram'), @(16, 'Raw'), @(17, 'Raw'))) {
    try { $s = [System.Net.Sockets.Socket]::new([System.Net.Sockets.AddressFamily]$t[0], [System.Net.Sockets.SocketType]$t[1], 'Unspecified'); $s.Dispose(); $sock += ('socket ' + $t[0] + '/' + $t[1]) } catch { }
}
foreach ($u in @('/run/systemd/private', '/run/dbus/system_bus_socket')) {
    try { $s = [System.Net.Sockets.Socket]::new('Unix', 'Stream', 'Unspecified'); $s.Connect([System.Net.Sockets.UnixDomainSocketEndPoint]::new($u)); $sock += "connected $u" } catch { }
}
$ipc = @(foreach ($p in @(@IPC@)) { try { $null = Get-Content -LiteralPath $p -TotalCount 1 -ErrorAction Stop; $p } catch { } })
$cap = ((Get-Content /proc/self/status | Where-Object { $_ -match '^CapEff:' }) -split '\s+')[1]
$seen = @(Get-ChildItem -LiteralPath /proc -Directory | Where-Object { $_.Name -match '^[0-9]+$' }).Count
@{ writable = $w; private = $priv; sockets = $sock; ipc = $ipc; uid = [string](& /usr/bin/id -u); capeff = $cap; seen = $seen } | ConvertTo-Json -Compress -Depth 3
"""
# what a trial would read from the sandbox, not the host (the mount table, IPC, a file's user or group through PowerShell
# - NSS - and the temporary folders, which are its own): such a code is not tried, never given a result the host would
# not give. find and stat of a temporary folder are replayed instead (replays_for)
TMP_RX = r"(?<![\w.-])(/var/tmp|/tmp|/dev/shm)(?![\w.-])"
BLIND = [(r"/proc/(self/|thread-self/|\d+/)?mount|/etc/mtab|mountinfo", "the mount table (the sandbox's own)"),
         (r"/proc/sysvipc|/dev/mqueue|/proc/sys/kernel/(shm|msg|sem)|/proc/sys/fs/mqueue", "an IPC setting through a file (the sandbox's own IPC)"),
         (r"\.(User|Group)\b|Property\s+'?(User|Group)\b|-ExpandProperty\s+'?(User|Group)\b",
          "a file's user or group through PowerShell (names come from NSS - sssd or LDAP need a socket)")]


def find_sandbox(path):
    """systemd-run's path ('' = not here)."""
    for p in [path] if path else ["/usr/bin/systemd-run", "/bin/systemd-run"]:
        if p and os.path.basename(p) == "systemd-run" and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


def systemd_version(runner):
    """systemd-run's version (0 = not known)."""
    try:
        out = subprocess.run([runner, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30).stdout
        m = re.match(r"systemd (\d+)", out.decode("utf-8", "replace"))
        return int(m.group(1)) if m else 0
    except (OSError, subprocess.TimeoutExpired):
        return 0


def _uid_procs(uid):
    """The processes of this host with uid as a real, effective, saved or filesystem UID."""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/status" % d) as f:
                for ln in f:
                    if ln.startswith("Uid:"):
                        if str(uid) in ln.split()[1:]:
                            out.append(int(d))
                        break
        except (OSError, IOError):
            continue
    return out


def uid_free(uid):
    """No passwd or group entry (NSS), no process of this host with it."""
    import grp
    import pwd
    try:
        pwd.getpwuid(uid)
        return False
    except KeyError:
        pass
    try:
        grp.getgrgid(uid)
        return False
    except KeyError:
        pass
    return not _uid_procs(uid)


_TRIAL_UID = []


def trial_uid():
    """The trial's own UID, picked once per run: random in UID_FIRST..UID_LAST, free (uid_free)."""
    import random
    if not _TRIAL_UID:
        rnd = random.SystemRandom()
        for _ in range(50):
            u = rnd.randint(UID_FIRST, UID_LAST)
            if uid_free(u):
                _TRIAL_UID.append(u)
                break
        else:
            raise RuntimeError("no free UID found in %d..%d" % (UID_FIRST, UID_LAST))
    return _TRIAL_UID[0]


def sandbox_cmd(runner, seconds, binds=None, version=0):
    """The systemd-run command line of one sandboxed run (as root: a system unit as the trial's own UID; as another
    user: that user's own systemd). binds: {program path: its replay shim} - each bound read-only over the program.
    version: systemd's (257 and later: a PID namespace of its own too)."""
    root = os.geteuid() == 0
    cmd = [runner] + ([] if root else ["--user"]) + ["--pipe", "--wait", "--collect", "--quiet"]
    props = list(SANDBOX) + (SANDBOX_PIDS if version >= 257 else []) + ["RuntimeMaxSec=%d" % seconds]
    if root:
        u = trial_uid()
        props += ["User=%d" % u, "Group=%d" % u, "AmbientCapabilities=CAP_DAC_READ_SEARCH"]
    for prog, shim in sorted((binds or {}).items()):
        props.append("BindReadOnlyPaths=%s:%s" % (shim, prog))
    for x in props:
        cmd += ["-p", x]
    for x in ["HOME=/tmp", "TMPDIR=/tmp", "XDG_CACHE_HOME=/tmp", "XDG_DATA_HOME=/tmp", "XDG_CONFIG_HOME=/tmp",
              "POWERSHELL_TELEMETRY_OPTOUT=1", "POWERSHELL_UPDATECHECK=Off", "DOTNET_CLI_TELEMETRY_OPTOUT=1",
              "DOTNET_EnableDiagnostics=0", "LANG=C.UTF-8"]:
        cmd += ["-E", x]
    return cmd


def sandbox_env():
    env = dict(os.environ)
    run = "/run/user/%d" % os.geteuid()
    if os.geteuid() != 0 and os.path.exists(run + "/bus"):    # the user's own systemd (tests run so), not another bus
        env.update(XDG_RUNTIME_DIR=run, DBUS_SESSION_BUS_ADDRESS="unix:path=%s/bus" % run)
    return env


def _ps_list(xs):
    return ", ".join("'" + x.replace("'", "''") + "'" for x in xs)


def sandbox_probe(runner, pwsh="/usr/bin/pwsh", version=None):
    """Whether the sandbox works here and holds - looked at from inside, nothing written -> '' or why not."""
    if not runner:
        return "systemd-run was not found"
    if version is None:
        version = systemd_version(runner)
    host = dict((p, "%d:%d" % (os.stat(p).st_dev, os.stat(p).st_ino)) for p in PROBE_PRIVATE if os.path.exists(p))
    ro = PROBE_RO + [os.path.expanduser("~")]
    script = PROBE_PS1.replace("@RO@", _ps_list(ro)).replace("@IPC@", _ps_list([x for x in IPC_PATHS if x.startswith("/proc/sys/")]))
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        if os.geteuid() == 0 and not uid_free(trial_uid()):
            return "its UID %d is in use here" % trial_uid()
        r = subprocess.run(sandbox_cmd(runner, 60, version=version) + [pwsh, "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, env=sandbox_env())
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
        return "systemd-run did not answer (%s)" % e
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        j = json.loads(out[-1])
    except (ValueError, IndexError):
        return "systemd-run could not start it (exit %d: %s)" % (r.returncode, (r.stderr.decode("utf-8", "replace").strip() or "no answer")[:200])
    w = j.get("writable") or []
    w = [w] if isinstance(w, str) else w
    if w:
        return "it left %s writable" % ", ".join(w)
    for place, ident in sorted((j.get("private") or {}).items()):
        if not ident or ident == host.get(place):
            return "its %s is the host's, not its own" % place
    so = j.get("sockets") or []
    so = [so] if isinstance(so, str) else so
    if so:
        return "a socket could be used in it (%s)" % ", ".join(so)
    ipc = j.get("ipc") or []
    ipc = [ipc] if isinstance(ipc, str) else ipc
    if ipc:
        return "an IPC setting can be read in it (%s) - it would be the sandbox's own" % ", ".join(ipc)
    uid = str(j.get("uid")).strip()
    if uid in ("", "0"):
        return "it runs as root"
    if os.geteuid() == 0 and uid != str(trial_uid()):
        return "it runs as UID %s, not its own %d" % (uid, trial_uid())
    try:
        cap = int(str(j.get("capeff") or ""), 16)
    except ValueError:
        return "its capabilities could not be read"
    if cap & ~CAP_DAC_READ_SEARCH:
        return "it keeps capabilities other than reading (CapEff %s)" % j.get("capeff")
    if os.geteuid() == 0 and _uid_procs(trial_uid()):
        return "its UID %d owns a process here" % trial_uid()
    return ""


_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:\\-]*$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")
_PATH = re.compile(r"^/[A-Za-z0-9._/*?+@,:-]*$")
FIND_NEVER = ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls")


def replay_ok(argv):
    """The command lines the module may run itself, outside the sandbox (checked again, as the read-only check did): all
    read-only."""
    if not argv:
        return False
    prog, a = argv[0], argv[1:]
    if prog == "/usr/sbin/auditctl":
        return a == ["-l"]
    if prog == "/usr/bin/systemctl":
        return (len(a) == 2 and a[0] in ("is-enabled", "is-active") and _UNIT.match(a[1]) is not None) or \
            (len(a) == 4 and a[:2] == ["show", "-p"] and re.match(r"^[A-Za-z]+$", a[2]) is not None and _UNIT.match(a[3]) is not None)
    if prog in ("/usr/sbin/sysctl", "/usr/bin/sysctl"):
        return len(a) == 2 and a[0] == "-n" and re.match(r"^[a-z0-9_][a-z0-9_.]*$", a[1]) is not None
    if prog == "/usr/bin/getent":
        return 1 <= len(a) <= 2 and a[0] in ("passwd", "group", "shadow", "gshadow") and (len(a) == 1 or _NAME.match(a[1]) is not None)
    if prog == "/usr/bin/id":
        i = 0
        while i < len(a) and a[i] in ("-u", "-g", "-G", "-n"):
            i += 1
        return i == len(a) or (i == len(a) - 1 and _NAME.match(a[i]) is not None)
    if prog == "/usr/bin/stat":
        return len(a) >= 3 and a[0] == "-c" and re.match(r"^(?=.*%)(?:%[UGaAugn]|[ :])+$", a[1]) is not None and \
            all(_PATH.match(x) for x in a[2:])
    if prog == "/usr/bin/find":
        return len(a) >= 1 and a[0].startswith("/") and not any(x in FIND_NEVER for x in a)
    return False


def _needs(argv):
    """Whether this call's answer depends on what the sandbox cannot see as the host has it."""
    prog, a = argv[0], argv[1:]
    if prog in REPLAY_ALWAYS:
        return True
    if prog in ("/usr/sbin/sysctl", "/usr/bin/sysctl"):
        return len(a) == 2 and a[1] in IPC_KEYS
    if prog == "/usr/bin/stat" and len(a) >= 2 and re.search(r"%[UG]", a[1]) is not None:
        return True
    if prog == "/usr/bin/find":
        paths = []
        for x in a:
            if not x.startswith("/"):
                break
            paths.append(x)
        return any(x in ("-user", "-group", "-nouser", "-nogroup") for x in a) or \
            any(re.search(r"%[ug]", a[i + 1]) for i, x in enumerate(a[:-1]) if x == "-printf") or \
            any(_touches_tmp(x, True) for x in paths)
    if prog == "/usr/bin/stat":
        return any(_touches_tmp(x, False) for x in a[2:])
    return False


def _touches_tmp(path, recursive):
    """A path in /tmp, /var/tmp or /dev/shm - or above one, for a program that walks down (find, grep -r)."""
    p = "/" + path.strip("/")
    for t in TMP_ROOTS:
        if p == t or p.startswith(t + "/") or (recursive and (t + "/").startswith(p.rstrip("/") + "/")):
            return True
    return False


def replays_for(calls):
    """A code's calls (the read-only check's exact command lines) -> the ones to run outside and replay inside: every call
    of REPLAY_ALWAYS, and every call of a REPLAY_IF program when one of its calls needs it (its shim then answers all)."""
    calls = [c for c in calls if c]
    progs = set(c[0] for c in calls if c[0] in REPLAY_ALWAYS) | set(c[0] for c in calls if c[0] in REPLAY_IF and _needs(c))
    out = []
    for c in calls:
        if c[0] in progs and c not in out:
            out.append(c)
    return out


def blind(code, calls=None):
    """What a code would read from the sandbox and not the host -> '' or why it is not tried. calls: the read-only
    check's command lines (find and stat of a temporary folder are replayed; grep there - it would read a host FIFO
    outside - is not tried)."""
    for rx, what in BLIND:
        if re.search(rx, code or "", re.I if "proc" in rx else 0):
            return "it reads %s - the trial cannot show it as the host has it" % what
    tmp = "a temporary folder (/tmp, /var/tmp, /dev/shm: the sandbox has its own)"
    for ln in (code or "").splitlines():
        if re.search(TMP_RX, ln) and not re.search(r"/usr/bin/(find|stat)\b", ln):
            return "it reads %s - the trial cannot show /tmp as the host has it" % tmp
        # a walk down from /, /var or /dev reaches the temporary folders
        if re.search(r"\bGet-ChildItem\b.*-Recurse|\bGet-ChildItem\b.*-Depth", ln, re.I) and \
                re.search(r"""(?:^|[\s'"(])(/|/var/?|/dev/?)(?:['"\s)]|$)""", ln):
            return "it reads %s - the trial cannot show /tmp as the host has it" % tmp
    for c in calls or []:
        if c and c[0] == "/usr/bin/grep":
            rec = "-r" in c
            if any(x.startswith("/") and _touches_tmp(x, rec) for x in c[1:]):
                return "it reads %s - the trial cannot show /tmp as the host has it" % tmp
    return ""


def capture(argv, seconds=30):
    """One read-only command line, run here (outside the sandbox) -> (stdout, stderr, exit code)."""
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"}
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds, env=env)
        return p.stdout[:REPLAY_MAX], p.stderr[:65536], p.returncode
    except subprocess.TimeoutExpired:
        return b"", b"no answer in %d seconds\n" % seconds, 124
    except OSError as e:
        return b"", ("%s\n" % e).encode("utf-8"), 127


def shim_text(prog, answers):
    """The replay shim of one program: answers {(args...): (stdout, stderr, rc)} -> a /bin/sh script."""
    lines = ["#!/bin/sh",
             "# site-automation answer keys (1.76.0): %s replayed - its answer was read once outside the sandbox" % prog,
             "k=$(printf '%s\\n' \"$@\" | base64 -w0)", 'case "$k" in']
    for args, (out, err, rc) in sorted(answers.items()):
        key = base64.b64encode(("\n".join(args) + "\n").encode("utf-8")).decode("ascii")
        lines.append("'%s') printf '%%s' '%s' | base64 -d; printf '%%s' '%s' | base64 -d >&2; exit %d ;;" % (
            key, base64.b64encode(out).decode("ascii"), base64.b64encode(err).decode("ascii"), int(rc) & 255))
    lines += ["esac", 'echo "not captured: %s $*" >&2' % prog, "exit 2", ""]
    return "\n".join(lines)


def write_shims(work, answers):
    """A shim over each program a code's replayed calls use, plus auditctl and systemctl always (on this host) ->
    {program: shim path}. answers: {argv tuple: result}."""
    binds = {}
    progs = set(REPLAY_ALWAYS) | set(a[0] for a in answers)
    for prog in sorted(progs):
        if not os.path.exists(prog):
            continue
        mine = dict((tuple(a[1:]), r) for a, r in answers.items() if a[0] == prog)
        path = os.path.join(work, "replay-%s" % prog.strip("/").replace("/", "-"))
        with open(path, "w") as f:
            f.write(shim_text(prog, mine))
        os.chmod(path, 0o755)
        binds[prog] = path
    return binds


def trial_command(pwsh, code):
    """pwsh with the trial (the code inside it, base64): no file the sandbox must see."""
    wrapper = TRIAL_PS1.replace("@CODE@", base64.b64encode(code.encode("utf-8")).decode("ascii"))
    return [pwsh, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(wrapper.encode("utf-16-le")).decode("ascii")]


def run_trial(pwsh, runner, code, seconds, binds=None, version=None):
    """One trial of a code in the sandbox -> (json or None, error, seconds). binds: the replay shims. version:
    systemd's (None: asked)."""
    t = time.time()
    if version is None:
        version = systemd_version(runner)
    try:
        p = subprocess.run(sandbox_cmd(runner, seconds, binds, version) + trial_command(pwsh, code),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds + 60,
                           env=sandbox_env())
    except subprocess.TimeoutExpired:
        return None, "no answer in %d seconds" % seconds, round(time.time() - t, 1)
    except RuntimeError as e:
        return None, str(e), round(time.time() - t, 1)
    out = p.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        return json.loads(out[-1] if out else ""), "", round(time.time() - t, 1)
    except ValueError:
        err = (p.stderr.decode("utf-8", "replace").strip() or "pwsh gave no answer")[:300]
        if time.time() - t >= seconds:
            err = "no answer in %d seconds (stopped)" % seconds
        return None, err, round(time.time() - t, 1)


def find_pwsh(path):
    if path:
        return path if os.path.isfile(path) else ""
    for d in os.environ.get("PATH", "").split(os.pathsep) + ["/usr/bin", "/opt/microsoft/powershell/7"]:
        p = os.path.join(d, "pwsh")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


def es_folder(path):
    """Evaluate-STIG's folder from check_stig_evaluate (its folder or its script)."""
    if not path:
        return ""
    if os.path.isfile(path):
        d = os.path.dirname(os.path.abspath(path))
        return os.path.dirname(os.path.dirname(d)) if os.path.basename(d) == "Evaluate-STIG" and os.path.basename(os.path.dirname(d)) == "Src" else d
    return path if os.path.isdir(path) else ""


def _files(root, exts, depth=4, most=4000):
    out, base = [], root.rstrip("/").count("/")
    for d, subs, names in os.walk(root):
        if d.count("/") - base >= depth:
            subs[:] = []
        for n in names:
            if n.lower().endswith(exts):
                out.append(os.path.join(d, n))
                if len(out) >= most:
                    return out
    return out


def template_names(root):
    """The answer file template's element names in an AnswerKey -> (path, [names]) or ('', []). 1.76.0 (a real host:
    Evaluate-STIG 1.2610.1 had none found): deeper (6 folders), an XML file with an AnswerKey and a ValidationCode - one
    named like a template or an answer file first - else a schema (*.xsd) that defines AnswerKey: its element names."""
    xs = _files(root, (".xml", ".xsd"), depth=6, most=6000)

    def rank(p):
        n = os.path.basename(p).lower()
        return (1 if n.endswith(".xsd") else 0, 0 if "template" in n else (1 if "answer" in n else 2), len(p))
    xs.sort(key=rank)
    for p in xs[:400]:
        try:
            if os.path.getsize(p) > 5 * 1048576:
                continue
            with open(p, "rb") as f:
                data = f.read()
            if b"AnswerKey" not in data or b"ValidationCode" not in data:
                continue
            root_el = ET.fromstring(data)
        except (OSError, ET.ParseError):
            continue
        if p.lower().endswith(".xsd"):
            names = xsd_names(root_el)
            if names:
                return p, names
            continue
        for ak in root_el.iter():
            if ak.tag.split("}")[-1] == "AnswerKey":
                names = [c.tag.split("}")[-1] for c in list(ak)]
                if "ValidationCode" in names or any(n.lower() == "validationcode" for n in names):
                    return p, names
    return "", []


def xsd_names(root_el):
    """A schema's AnswerKey: the names of the elements it holds, in order ([] when it has none with ValidationCode)."""
    def local(t):
        return t.split("}")[-1]
    types = dict((x.get("name"), x) for x in root_el.iter() if local(x.tag) == "complexType" and x.get("name"))
    for el in root_el.iter():
        if local(el.tag) != "element" or el.get("name") != "AnswerKey":
            continue
        body = [x for x in el if local(x.tag) == "complexType"]
        node = body[0] if body else types.get((el.get("type") or "").split(":")[-1])
        if node is None:
            continue
        names = [x.get("name") or (x.get("ref") or "").split(":")[-1] for x in node.iter() if local(x.tag) == "element" and x is not el]
        names = [n for n in names if n]
        if "ValidationCode" in names:
            return names
    return []


def es_version(root):
    """Evaluate-STIG's version: $EvaluateStigVersion in its script, or the ModuleVersion of its manifest."""
    for p in [os.path.join(root, "Evaluate-STIG.ps1"), os.path.join(root, "Src", "Evaluate-STIG", "Evaluate-STIG.ps1")] + \
            glob.glob(os.path.join(root, "*.psd1")) + glob.glob(os.path.join(root, "Src", "Evaluate-STIG", "*.psd1")):
        try:
            with open(p, "r", errors="replace") as f:
                text = f.read(400000)
        except OSError:
            continue
        m = re.search(r"\$EvaluateStigVersion\s*=\s*['\"]([\d.]+)['\"]", text) or re.search(r"ModuleVersion\s*=\s*['\"]([\d.]+)['\"]", text)
        if m:
            return m.group(1)
    return ""


def expected_check(root):
    """Whether Evaluate-STIG's code compares an answer's ExpectedStatus with its own status (before ValidationCode):
    {found, where, note}. 1.76.0: the engine first - Evaluate-STIG.ps1 and its modules (*.psm1) - never a GUI script as
    proof for the command line runs (a real host had it only in Evaluate-STIG_GUI.ps1)."""
    gui = ""
    files = _files(root, (".ps1", ".psm1"), depth=6, most=3000)
    files.sort(key=lambda p: (0 if p.lower().endswith(".psm1") or os.path.basename(p) == "Evaluate-STIG.ps1" else 1, len(p)))
    for p in files:
        is_gui = "gui" in os.path.basename(p).lower()
        try:
            with open(p, "r", errors="replace") as f:
                for i, ln in enumerate(f, 1):
                    if "ExpectedStatus" in ln and re.search(r"-(c|i)?(eq|ne|in|notin|contains|match)\b", ln, re.I):
                        where = "%s:%d" % (os.path.relpath(p, root), i)
                        if is_gui:
                            gui = gui or where
                            break
                        return {"found": True, "where": where, "note": ""}
        except OSError:
            continue
    return {"found": False, "where": gui, "note": ("only in the GUI script (%s) - not proof for the command line runs" % gui) if gui else ""}


def run_ps(pwsh, script, code_file, seconds):
    t = time.time()
    try:
        p = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-File", script, "-CodeFile", code_file],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds)
    except subprocess.TimeoutExpired:
        return None, "no answer in %d seconds" % seconds, round(time.time() - t, 1)
    out = p.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        return json.loads(out[-1] if out else ""), "", round(time.time() - t, 1)
    except ValueError:
        return None, (p.stderr.decode("utf-8", "replace").strip() or "pwsh gave no answer")[:300], round(time.time() - t, 1)


def main():
    module = AnsibleModule(argument_spec=dict(
        evaluate=dict(type="str", default=""),
        pwsh=dict(type="str", default=""),
        codes=dict(type="list", elements="dict", default=[]),
        trial=dict(type="bool", default=True),
        timeout=dict(type="int", default=60),
        sandbox=dict(type="str", default=""),
    ), supports_check_mode=True)
    p = module.params
    if p.get("sandbox") and os.path.basename(p["sandbox"]) != "systemd-run" and os.path.exists(p["sandbox"]):
        # 1.76.0b: only systemd-run is a sandbox - another program given is a setting's mistake, said, never run
        module.fail_json(msg="sandbox %s is not systemd-run: no trial with it" % p["sandbox"])
    ev = {"found": False, "script": "", "version": "", "template": "", "names": [], "why": "", "expected": {"found": False, "where": ""}}
    root = es_folder(p["evaluate"])
    if root:
        ev.update(found=True, script=root, version=es_version(root))
        ev["template"], ev["names"] = template_names(root)
        ev["expected"] = expected_check(root)
        if not ev["template"]:
            ev["why"] = "no answer file template in %s" % root
    else:
        ev["why"] = "Evaluate-STIG was not found (check_stig_evaluate: %s)" % (p["evaluate"] or "not set")
    pwsh = find_pwsh(p["pwsh"])
    gate, trial = {}, {}
    if not pwsh:
        module.exit_json(changed=False, evaluate=ev, gate={}, trial={}, error="pwsh was not found on the host")
    work = tempfile.mkdtemp(prefix="site-answer-keys-")
    os.chmod(work, 0o700)
    runner, no_sandbox, answers, version = None, "", {}, 0
    if any(c in work for c in " :,\"'"):
        module.exit_json(changed=False, evaluate=ev, gate={}, trial={}, error="its temporary folder %s cannot be bound (a space, : or ,)" % work)
    try:
        gs = os.path.join(work, "gate.ps1")
        with open(gs, "w") as f:
            f.write(GATE_PS1)
        for i, c in enumerate(p["codes"]):
            cid, code = str(c.get("id") or i), str(c.get("code") or "")
            cf = os.path.join(work, "code-%d.ps1" % i)
            with open(cf, "w") as f:
                f.write(code)
            if len(code) > CODE_MAX:
                gate[cid] = {"ok": False, "why": ["it is over %d characters" % CODE_MAX], "used": []}
                continue
            g, err, secs = run_ps(pwsh, gs, cf, 60)
            gate[cid] = g if isinstance(g, dict) else {"ok": False, "why": ["the read-only check did not answer: %s" % err], "used": []}
            gate[cid]["ok"] = bool(gate[cid].get("ok"))
            if not (gate[cid]["ok"] and p["trial"]) or module.check_mode:
                continue
            if runner is None:       # once, before the first trial: no sandbox that keeps the host read-only - no trial
                runner = find_sandbox(p["sandbox"])
                version = systemd_version(runner) if runner else 0
                no_sandbox = sandbox_probe(runner, pwsh, version)
            if no_sandbox:
                trial[cid] = {"ran": False, "ok": False, "valid": None, "read": "", "seconds": 0,
                              "error": "no sandbox (%s): a trial runs only in a read-only sandbox" % no_sandbox}
                continue
            # what the sandbox would show as its own, not the host's (the mount table, IPC files, names through
            # PowerShell): not tried - never a result the host would not give
            calls = [[str(x) for x in c] for c in (gate[cid].get("calls") or []) if isinstance(c, list)]
            why_blind = blind(code, calls)
            if why_blind:
                trial[cid] = {"ran": False, "ok": False, "valid": None, "read": "", "seconds": 0, "error": why_blind}
                continue
            # what needs a socket, the host's IPC namespace or NSS: the exact command lines the check let through, run
            # once here (read-only), their answers replayed inside the sandbox
            todo = replays_for(calls)
            bad = [" ".join(c) for c in todo if not replay_ok(c)]
            if bad:
                trial[cid] = {"ran": False, "ok": False, "valid": None, "read": "", "seconds": 0,
                              "error": "it runs %s, which is replayed only as the read-only check allows" % bad[0]}
                continue
            for argv in todo:
                if tuple(argv) not in answers:
                    answers[tuple(argv)] = capture(argv, max(30, p["timeout"]))
            replayed = [" ".join(c) for c in todo]
            binds = write_shims(work, dict((tuple(c), answers[tuple(c)]) for c in todo))
            r, err, secs = run_trial(pwsh, runner, code, max(5, p["timeout"]), binds, version)
            if not isinstance(r, dict):
                trial[cid] = {"ran": True, "ok": False, "valid": None, "read": "", "error": err, "seconds": secs}
            elif not r.get("ok"):
                trial[cid] = {"ran": True, "ok": False, "valid": None, "read": "", "error": str(r.get("error") or "")[:300], "seconds": secs}
            else:
                vals = r.get("values")
                vals = vals if isinstance(vals, list) else [vals]
                kinds = r.get("kinds")
                kinds = kinds if isinstance(kinds, list) else [kinds]
                one = len(vals) == 1 and kinds == ["Boolean"] and isinstance(vals[0], bool)
                trial[cid] = {"ran": True, "ok": one, "valid": vals[0] if one else None, "read": str(r.get("read") or "")[:300],
                              "error": "" if one else "it gave %s, not one true or false" % (", ".join(str(v) for v in vals[:3]) or "nothing"),
                              "seconds": secs}
            trial[cid]["replayed"] = replayed
    finally:
        shutil.rmtree(work, ignore_errors=True)
    module.exit_json(changed=False, evaluate=ev, gate=gate, trial=trial, error="", sandbox={"ok": not no_sandbox and runner is not None and runner != "", "why": no_sandbox})


if __name__ == "__main__":
    main()
