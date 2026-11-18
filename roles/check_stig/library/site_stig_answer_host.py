#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.75.0 (playbooks/stig_answer_keys.yml), on the host: what its Evaluate-STIG is (its version, its answer file
template's element names, whether it compares ExpectedStatus before ValidationCode), then for each drafted
ValidationCode the fixed read-only check - PowerShell's own parser on the code's syntax tree. It runs NO code: 1.75.0
has no trial (it comes in 1.75.1). Writes nothing but its own folder (0700, removed at the end). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stig_answer_host
short_description: An Evaluate-STIG answer key's ValidationCode - checked read-only (not tried: never run on this host; the trial comes in 1.75.1)
description:
  - Reads the host's Evaluate-STIG folder - its version, its answer file template's element names (an XML file of the
    folder with an AnswerKey and a ValidationCode), and whether its code compares ExpectedStatus.
  - For each code - the fixed read-only check (PowerShell's parser; allow-lists only - the syntax, the cmdlets and their
    parameters, each program's exact arguments). The code itself is never run (1.75.0 has no trial).
  - Writes nothing but a folder of its own (0700), removed at the end.
options:
  evaluate: {description: "Evaluate-STIG's folder or script on the host ('' = not read).", type: str, default: ''}
  pwsh: {description: "pwsh's path ('' = found on PATH).", type: str, default: ''}
  codes: {description: "[{id, code}]: the ValidationCodes.", type: list, elements: dict, default: []}
author: site automation
'''

RETURN = r'''
evaluate: {description: "{found, script, version, template, names, why, expected: {found, where, note}}", type: dict, returned: always}
gate: {description: "{id: {ok, why, used: [commands]}}", type: dict, returned: always}
error: {description: "Why nothing was checked ('' = checked).", type: str, returned: always}
'''

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
            if ($bad) { No ('it runs ' + $name + ' ' + ($args2 -join ' ') + ' - ' + $bad) } elseif (-not $used.Contains($name)) { $used.Add($name) }
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
@{ ok = ($why.Count -eq 0); why = @($why); used = @($used) } | ConvertTo-Json -Compress -Depth 3
'''

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
    """The answer file template's element names in an AnswerKey (an XML file of Evaluate-STIG's folder with an AnswerKey and a
    ValidationCode; a name with Template first) -> (path, [names]) or ('', [])."""
    xs = _files(root, (".xml",))
    xs.sort(key=lambda p: (0 if "template" in os.path.basename(p).lower() else 1, len(p)))
    for p in xs[:300]:
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
        for ak in root_el.iter():
            if ak.tag.split("}")[-1] == "AnswerKey":
                names = [c.tag.split("}")[-1] for c in list(ak)]
                if "ValidationCode" in names or any(n.lower() == "validationcode" for n in names):
                    return p, names
    return "", []


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
    {found, where} - the first line of its .ps1/.psm1 that does."""
    for p in _files(root, (".ps1", ".psm1"), depth=5, most=2000):
        try:
            with open(p, "r", errors="replace") as f:
                for i, ln in enumerate(f, 1):
                    if "ExpectedStatus" in ln and re.search(r"-(c|i)?(eq|ne|in|notin|contains|match)\b", ln, re.I):
                        return {"found": True, "where": "%s:%d" % (os.path.relpath(p, root), i)}
        except OSError:
            continue
    return {"found": False, "where": ""}


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
    ), supports_check_mode=True)
    p = module.params
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
    gate = {}
    if not pwsh:
        module.exit_json(changed=False, evaluate=ev, gate={}, error="pwsh was not found on the host")
    work = tempfile.mkdtemp(prefix="site-answer-keys-")
    os.chmod(work, 0o700)
    try:
        gs = os.path.join(work, "gate.ps1")
        with open(gs, "w") as f:
            f.write(GATE_PS1)
        for i, c in enumerate(p["codes"]):
            cid, code = str(c.get("id") or i), str(c.get("code") or "")
            if len(code) > CODE_MAX:
                gate[cid] = {"ok": False, "why": ["it is over %d characters" % CODE_MAX], "used": []}
                continue
            cf = os.path.join(work, "code-%d.ps1" % i)
            with open(cf, "w") as f:
                f.write(code)
            # the check only parses the code (PowerShell's parser); the code is never run here
            g, err, secs = run_ps(pwsh, gs, cf, 60)
            gate[cid] = g if isinstance(g, dict) else {"ok": False, "why": ["the read-only check did not answer: %s" % err], "used": []}
            gate[cid]["ok"] = bool(gate[cid].get("ok"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    module.exit_json(changed=False, evaluate=ev, gate=gate, error="")


if __name__ == "__main__":
    main()
