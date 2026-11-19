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
import posixpath
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
# 1.76.1: every path a file-reading cmdlet is given - {cmd, path, literal, recurse}: the module decides, per command, what
# the trial can show as the host has it
$paths = New-Object System.Collections.Generic.List[object]
$pathCmdlets = @('Get-Content', 'Select-String', 'Test-Path', 'Get-Item', 'Get-ChildItem')
$valued = @('Path', 'LiteralPath', 'TotalCount', 'Head', 'First', 'Tail', 'Last', 'Delimiter', 'Encoding', 'ReadCount', 'ErrorAction',
            'Pattern', 'Context', 'InputObject', 'PathType', 'Filter', 'Include', 'Exclude', 'Depth')
# a literal string, or a list of them (in parentheses, @( ), a comma list) -> their values; else $null
function LitStrings($e) {
    while ($e -is [System.Management.Automation.Language.ParenExpressionAst] -or $e -is [System.Management.Automation.Language.ArrayExpressionAst] -or
           ($e -is [System.Management.Automation.Language.PipelineAst] -and $e.PipelineElements.Count -eq 1 -and $e.PipelineElements[0] -is [System.Management.Automation.Language.CommandExpressionAst]) -or
           ($e -is [System.Management.Automation.Language.CommandExpressionAst])) {
        if ($e -is [System.Management.Automation.Language.ParenExpressionAst]) { $e = $e.Pipeline }
        elseif ($e -is [System.Management.Automation.Language.ArrayExpressionAst]) {
            if ($e.SubExpression.Statements.Count -ne 1) { return $null }; $e = $e.SubExpression.Statements[0]
        }
        elseif ($e -is [System.Management.Automation.Language.PipelineAst]) { $e = $e.PipelineElements[0].Expression }
        else { $e = $e.Expression }
    }
    if ($e -is [System.Management.Automation.Language.StringConstantExpressionAst]) { return ,@($e.Value) }
    if ($e -is [System.Management.Automation.Language.ArrayLiteralAst]) {
        $v = @(); foreach ($x in $e.Elements) { if ($x -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { return $null }; $v += $x.Value }
        return ,$v
    }
    return $null
}
function AddPath([string] $c, $e, [bool] $rec) {
    $lit = $e -is [System.Management.Automation.Language.StringConstantExpressionAst]
    $paths.Add(@{ cmd = $c; path = $(if ($lit) { $e.Value } else { $e.Extent.Text }); literal = $lit; recurse = $rec })
}
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
                if ($pathCmdlets -contains $c) {
                    $rec = @($els | Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] -and @('Recurse', 'Depth') -contains $_.ParameterName }).Count -gt 0
                    $patGiven = @($els | Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'Pattern' }).Count -gt 0
                    $pos = 0; $got = 0; $i = 0
                    while ($i -lt $els.Count) {
                        $e = $els[$i]
                        if ($e -is [System.Management.Automation.Language.CommandParameterAst]) {
                            $arg = $e.Argument
                            if ($null -eq $arg -and $valued -contains $e.ParameterName -and $i + 1 -lt $els.Count) { $i++; $arg = $els[$i] }
                            if (@('Path', 'LiteralPath') -contains $e.ParameterName -and $null -ne $arg) { AddPath $c $arg $rec; $got++ }
                            $i++; continue
                        }
                        $want = if ($c -eq 'Select-String' -and -not $patGiven) { 1 } else { 0 }
                        if ($pos -eq $want) { AddPath $c $e $rec; $got++ }
                        $pos++; $i++
                    }
                    $pl = $n.Parent
                    $at = -1
                    if ($pl -is [System.Management.Automation.Language.PipelineAst]) { for ($k = 0; $k -lt $pl.PipelineElements.Count; $k++) { if ($pl.PipelineElements[$k] -eq $n) { $at = $k } } }
                    # no path, and nothing piped in: the current folder - not one the trial can name
                    if ($got -eq 0 -and $at -eq 0 -and $c -ne 'Select-String') {
                        $paths.Add(@{ cmd = $c; path = '(the current folder)'; literal = $false; recurse = $rec })
                    }
                    # 1.76.1b (Codex): a path piped in - a literal string (or a list of them) is that cmdlet's path; any other
                    # input piped to a path cmdlet is not a path the trial can name. Select-String reads files only when
                    # it is given them (Get-ChildItem / Get-Item piped in); text piped to it is text.
                    if ($got -eq 0 -and $at -gt 0) {
                        $prev = $pl.PipelineElements[$at - 1]
                        if ($c -eq 'Select-String') {
                            if ($prev -is [System.Management.Automation.Language.CommandAst] -and $prev.CommandElements[0] -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                                @('Get-ChildItem', 'Get-Item') -contains $prev.CommandElements[0].Value) {
                                $paths.Add(@{ cmd = $c; path = '(the files piped in)'; literal = $false; recurse = $rec })
                            }
                        } else {
                            $ls = if ($prev -is [System.Management.Automation.Language.CommandExpressionAst]) { LitStrings $prev.Expression } else { $null }
                            if ($null -ne $ls) { foreach ($v in $ls) { $paths.Add(@{ cmd = $c; path = $v; literal = $true; recurse = $rec }) } }
                            else { $paths.Add(@{ cmd = $c; path = '(the input piped in)'; literal = $false; recurse = $rec }) }
                        }
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
@{ ok = ($why.Count -eq 0); why = @($why); used = @($used); calls = $calls.ToArray(); paths = $paths.ToArray() } | ConvertTo-Json -Compress -Depth 4
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
# the IPC files: a code that reads them is not tried (BLIND); /dev/mqueue hidden in the sandbox
IPC_PATHS = ["/proc/sysvipc", "/dev/mqueue"] + ["/proc/sys/" + k.replace(".", "/") for k in IPC_KEYS]
TMP_ROOTS = ["/tmp", "/var/tmp", "/dev/shm"]
# 1.76.1: two profiles, by systemd's version (systemd-run --version). Both: the whole file system read-only, its own
# /tmp, /var/tmp and /dev/shm, no socket of any family, no new privileges, root's capabilities cut to reading.
# 1.76.1c: systemd skips some settings with no error when the kernel will not give it a namespace ("assuming containerized
# execution" - a user unit where user namespaces are restricted, a container): a unit with only ProtectSystem= then runs
# with the host writable. A TemporaryFileSystem= is a mount systemd insists on: no mount namespace, no run (226). The
# rest that may be skipped is held another way too: IPC and the clock and host name by the system call filter (which is
# never skipped), the devices and the PID namespace looked at by the probe.
SANDBOX_COMMON = ["ProtectSystem=strict", "ReadOnlyPaths=/", "ProtectHome=read-only", "PrivateTmp=yes", "PrivateDevices=yes",
                  "TemporaryFileSystem=/dev/shm", "TemporaryFileSystem=/run/user:ro", "InaccessiblePaths=-/dev/mqueue",
                  # 1.76.1f: no mount of the host reaches the sandbox after it starts - an automount fired inside (or
                  # one that expires and fires again) mounts in the host's namespace, writable, and would propagate
                  # in past the read-only remount (tried on systemd 255). Automounts are fired before each run instead
                  # (fire_automounts), so they are mounted when the sandbox is made, and read-only in it.
                  "MountFlags=private",
                  # 1.76.1g (review): /sys and its file systems (securityfs, configfs, pstore ...) stayed writable as root -
                  # a ReadOnlyPaths= below "/" is dropped as a no-op by systemd, a read-only bind is not (tried, 255);
                  # /dev/pts is the host's devpts (its users' terminals): an empty one of its own instead
                  "BindReadOnlyPaths=/sys", "BindReadOnlyPaths=-/dev/hugepages", "TemporaryFileSystem=/dev/pts:ro",
                  "NoNewPrivileges=yes", "RestrictNamespaces=yes",
                  "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectControlGroups=yes",
                  "SystemCallFilter=~@privileged @debug",
                  # no address family at all: an allow-list of AF_UNIX, then AF_UNIX taken out (systemd-run does not take "none")
                  "RestrictAddressFamilies=AF_UNIX", "RestrictAddressFamilies=~AF_UNIX", "IPAddressDeny=any",
                  "CapabilityBoundingSet=CAP_DAC_READ_SEARCH"]
# systemd 248 and later (RHEL 9, 10): the kernel's logs, clock and host name protected, no set-UID files, other UIDs'
# processes hidden. 1.76.1c: no PrivateIPC - systemd may skip it, and as root ProtectProc's own /proc showed the IPC
# files it was to hide (tried on the real 255): the IPC calls are refused instead, with every profile, and the IPC files
# are the host's, read as they are (a code that reads them is still not tried, BLIND; sysctl of them still replayed)
SANDBOX_FULL = [
                # LockPersonality: systemd 239's systemd-run does not take it (a unit file would) - tried on the real 239
                "LockPersonality=yes", "RestrictSUIDSGID=yes", "ProtectKernelLogs=yes", "ProtectClock=yes", "ProtectHostname=yes",
                # other UIDs' processes hidden; not ProcSubset=pid - it hides /proc/sys and /proc/cmdline, which checks read
                "ProtectProc=invisible"]
# systemd before 248 (RHEL 8: 239) has none of those six; the same properties another way: no SysV IPC or message queue
# can be made (their system calls refused - not all of @ipc: pwsh needs pipe), no clock set (@clock refused; no
# CAP_SYS_TIME either); the kernel's logs: PrivateDevices hides /dev/kmsg, syslog(2) needs a capability it has not; set-UID
# files: the file system is read-only, NoNewPrivileges; the host name: no CAP_SYS_ADMIN; other UIDs' processes: its own
# UID, no CAP_SYS_PTRACE or CAP_KILL - the kernel refuses their environ, mem, fd and any signal. Without its own IPC the
# IPC files in /proc are the host's: read as they are. A refused call fails (EPERM), it does not stop pwsh.
IPC_CALLS = ["shmget", "shmat", "shmdt", "shmctl", "msgget", "msgsnd", "msgrcv", "msgctl", "semget", "semop",
             "semtimedop", "semctl", "mq_open", "mq_unlink", "mq_timedsend", "mq_timedreceive", "mq_notify", "mq_getsetattr"]
# LockPersonality: systemd 239's systemd-run does not take it (a unit file would) - personality(2) is refused instead.
# 1.76.1c: the IPC calls, the clock and the host name refused with every profile - PrivateIPC, ProtectClock and
# ProtectHostname are among what systemd may skip ("IPC namespace setup failed, ignoring")
SANDBOX_CALLS = ["SystemCallFilter=~" + " ".join(IPC_CALLS), "SystemCallFilter=~@clock sethostname setdomainname",
                 "SystemCallErrorNumber=EPERM"]
SANDBOX_COMMON += SANDBOX_CALLS
SANDBOX_239 = ["SystemCallFilter=~personality"]
FULL_FROM = 248
# systemd 257 and later: a PID namespace of its own too (RHEL 10); before, the UID of its own is what keeps host
# processes out of reach (no CAP_KILL, CAP_SYS_PTRACE: a signal to another UID is refused by the kernel)
SANDBOX_PIDS = ["PrivatePIDs=yes"]
# the tests may ask for the systemd 239 profile on a newer systemd (it holds there too): set here, never a setting
FORCED_VERSION = None


def profile_of(version):
    """'full' (systemd 248+) or '239' (before)."""
    return "full" if int(version or 0) >= FULL_FROM else "239"


def sandbox_props(version):
    v = FORCED_VERSION if FORCED_VERSION is not None else int(version or 0)
    return list(SANDBOX_COMMON) + (SANDBOX_FULL if profile_of(v) == "full" else SANDBOX_239) + (SANDBOX_PIDS if v >= 257 else [])


CAP_DAC_READ_SEARCH = 1 << 2
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
# the probe, inside the sandbox: what is writable (a write tried - a new file of a random name, removed at once); that /tmp, /var/tmp and /dev/shm are its
# own; whether a socket of any family can be opened (and /run/systemd/private, D-Bus reached); whether an IPC setting
# can be read; its uid, its effective capabilities, how many processes it sees
PROBE_PS1 = r"""
# 1.76.1f: its data - the places to look at, the job's PID - from stdin as JSON, never in this script's source
$in = [Console]::In.ReadToEnd() | ConvertFrom-Json
# 1.76.1h: writable = a write that works, never a guess from permission bits (test -w: the Rust coreutils of Ubuntu
# 25.10+ said a 777 /opt was writable on a read-only mount). A folder: a new file of a random name created there
# (CreateNew - never an existing one) and removed at once; a file: opened for writing, nothing written.
$w = @(foreach ($p in @($in.ro)) {
    if ([IO.Directory]::Exists($p)) {
        $t = [IO.Path]::Combine($p, '.site-ak-probe-' + [guid]::NewGuid().ToString('N'))
        $fs = $null
        try { $fs = [IO.File]::Open($t, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write) } catch { }
        if ($null -ne $fs) { $fs.Dispose(); try { [IO.File]::Delete($t) } catch { }; $p }
    } elseif ([IO.File]::Exists($p)) {
        $fs = $null
        try { $fs = [IO.File]::Open($p, [IO.FileMode]::Open, [IO.FileAccess]::Write) } catch { }
        if ($null -ne $fs) { $fs.Dispose(); $p }
    }
})
# 1.76.1f/g: every mount of the sandbox, as it sees them (the topmost at each point), with its file system type: one that
# can be written to (no "ro" of its own) is a host file system it could write - but /proc itself, /dev (its own: no
# host device, /dev/pts an empty one; a devpts there is the host's, refused), /tmp and /var/tmp (its own), its own
# cgroup's files (systemd puts them there), an automount point (autofs: not mounted - a code that reads below one is
# not tried), and a FOLDER it cannot even enter (a root-only one). A file is never exempt. Not read: refused.
$top = [ordered]@{}; $fst = @{}
$rwm = @()
try {
    $mi = $(if ($in.mountinfo) { [string]$in.mountinfo } else { '/proc/self/mountinfo' })
    $bad = @()
    foreach ($l in [IO.File]::ReadAllLines($mi)) {
        if (-not $l) { continue }
        $f = $l.Split(' ')
        $k = [array]::IndexOf($f, '-')
        # 1.76.1h: a line not understood refuses the table (as the host side does), never skipped
        if ($f.Count -le 5 -or $k -le 5 -or $f.Count -le $k + 1) { $bad += @('(a mount table line not understood)'); continue }
        $top[$f[4]] = $f[5]; $fst[$f[4]] = $f[$k + 1]
    }
    $mycg = ''
    try { $mycg = ((Get-Content -LiteralPath '/proc/self/cgroup' -ErrorAction Stop | Where-Object { $_ -match '^0::' }) -replace '^0::', '') } catch { }
    $rwm = @(foreach ($k in $top.Keys) {
        $p = [regex]::Replace([string]$k, '\\([0-7]{3})', { param($m) [string][char][Convert]::ToInt32($m.Groups[1].Value, 8) })
        if (($top[$k].Split(',')) -notcontains 'rw') { continue }
        if ($fst[$k] -eq 'autofs') { continue }
        # 1.76.1h: a name that is not UTF-8 (read as U+FFFD) cannot be looked at by name: writable is refused
        if ($p.Contains([string][char]0xFFFD)) { $p; continue }
        if ($fst[$k] -eq 'devpts') { $p; continue }
        # the kernel's interfaces that take a write only from root or a capability (it has neither: CapEff is looked at)
        if (@('selinuxfs', 'securityfs', 'configfs', 'pstore', 'fusectl', 'efivarfs', 'debugfs', 'tracefs', 'bpf', 'binfmt_misc') -contains $fst[$k]) { continue }
        if ($p -eq '/proc' -or $p -match '^/(dev|tmp|var/tmp)(/|$)') { continue }
        if ($mycg -and $p.StartsWith('/sys/fs/cgroup' + $mycg + '/')) { continue }
        # a place it cannot reach at all (not even look at), or a folder it cannot enter: nothing below can be written
        # (1.76.1h: by the kernel's own answer - a stat of the place, and of "folder/." which needs the right to enter
        # it - never test's reading of permission bits)
        $isDir = [IO.Directory]::Exists($p)
        if (-not $isDir -and -not [IO.File]::Exists($p)) { continue }
        if ($isDir -and -not [IO.Directory]::Exists($p.TrimEnd('/') + '/.')) { continue }
        $p
    })
    if ($top.Count -eq 0) { $rwm = @('(no mount table)') }
    if ($bad.Count) { $rwm = @($bad[0]) + $rwm }
} catch { $rwm = @('(the mount table could not be read)') }
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
# 1.76.1: no SysV IPC object can be touched - shmctl/msgctl of an id that is not there: "invalid id" when the call is
# allowed, "permission denied" when it is refused (nothing made, nothing removed)
$ipcrm = @(foreach ($k in @('-m', '-q')) { if (Test-Path /usr/bin/ipcrm) { [string](@(& /usr/bin/ipcrm $k 2147483646 2>&1) -join ' ') } else { 'no ipcrm' } })
# another UID's process it can see (none: its own PID namespace, or other UIDs hidden): its environment and open files
# unreadable, no signal
$me = [string](& /usr/bin/id -u)
$other = ''
foreach ($d in @(Get-ChildItem -LiteralPath /proc -Directory | Where-Object { $_.Name -match '^[0-9]+$' })) {
    try { $u = ((Get-Content -LiteralPath ($d.FullName + '/status') -ErrorAction Stop | Where-Object { $_ -match '^Uid:' }) -split '\s+')[1] } catch { continue }
    if ($u -and $u -ne $me) { $other = $d.Name; break }
}
$p1 = @(); $sig = $false
if ($other) {
    try { $null = [IO.File]::ReadAllBytes("/proc/$other/environ"); $p1 += "/proc/$other/environ" } catch { }
    # its open files: the links resolved (needs ptrace access) - not their numbers, which CAP_DAC_READ_SEARCH may list
    foreach ($fd in @('0', '1', '2')) { $t = @(& /usr/bin/readlink "/proc/$other/fd/$fd" 2>$null); if ($LASTEXITCODE -eq 0 -and $t) { $p1 += "/proc/$other/fd/$fd"; break } }
    $null = @(& /usr/bin/kill -0 $other 2>&1); $sig = ($LASTEXITCODE -eq 0)
}
# 1.76.1c: the host's devices (PrivateDevices skipped): any block device, or the kernel's memory and log
$dev = @(@(& /usr/bin/find /dev -xdev -type b 2>$null) + @(foreach ($d in @('/dev/mem', '/dev/kmem', '/dev/port', '/dev/kmsg')) { if (Test-Path -LiteralPath $d) { $d } }) | Select-Object -First 4)
# 1.76.1c: the job's own process (as a user other than root, the same UID): seen in it - no PID namespace of its own
$job = ''
try { $job = ((Get-Content -LiteralPath ('/proc/' + [string][int]$in.jobpid + '/stat') -ErrorAction Stop) -replace '^.*\) ', '' -split ' ')[19] } catch { }
@{ writable = $w; private = $priv; sockets = $sock; ipc = $ipc; uid = [string](& /usr/bin/id -u); capeff = $cap; seen = $seen;
   ipcrm = $ipcrm; p1 = $p1; signal = $sig; dev = $dev; job = $job; rwmounts = $rwm } | ConvertTo-Json -Compress -Depth 3
"""
# what a trial would read from the sandbox, not the host (the mount table, IPC, a file's user or group through PowerShell
# - NSS - and the temporary folders, which are its own): such a code is not tried, never given a result the host would
# not give. find and stat of a temporary folder are replayed instead (replays_for)
TMP_RX = r"(?<![\w.-])(/var/tmp|/tmp|/dev/shm)(?![\w.-])"
BLIND = [(r"/proc/(self/|thread-self/|\d+/)?mount|/etc/mtab|mountinfo", "the mount table (the sandbox's own)"),
         (r"/proc/(self|thread-self|\d+)\b|\bGet-Process\b", "a process's /proc, or the processes (the sandbox's own)"),
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


def sandbox_cmd(runner, seconds, binds=None, version=0):
    """The systemd-run command line of one sandboxed run (as root: a system unit as a dynamic user; as another
    user: that user's own systemd). binds: {program path: its replay shim} - each bound read-only over the program.
    version: systemd's (257 and later: a PID namespace of its own too)."""
    root = os.geteuid() == 0
    cmd = [runner] + ([] if root else ["--user"]) + ["--pipe", "--wait", "--collect", "--quiet"]
    props = sandbox_props(version) + ["RuntimeMaxSec=%d" % seconds]
    if root:
        # 1.76.1c: a dynamic user on every systemd - its UID unique to the unit while it runs. A numeric User= with no
        # passwd entry is refused before systemd 257 (exit 217 - tried on the real 239 and 255): 1.76.0's root trial on
        # RHEL 9 never ran
        props += ["DynamicUser=yes", "AmbientCapabilities=CAP_DAC_READ_SEARCH"]
    # 1.76.1f: every mount point of the host read-only by name too - ReadOnlyPaths=/ alone did not hold: with
    # ProtectKernelTunables, ProtectControlGroups or ProtectProc, systemd 255 left /run/lock (a tmpfs of its own)
    # writable (tried). A mount point systemd cannot be given by name: MountsError - no sandbox
    props += ["ReadOnlyPaths=-%s" % m for m in probe_mounts()]
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


# systemd's dynamic users
DYNAMIC_FIRST, DYNAMIC_LAST = 61184, 65519


def _starttime(pid):
    """A process's start time (/proc/<pid>/stat field 22) - with its PID, which process it is."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(") ", 1)[1].split()[19]
    except (OSError, IOError, IndexError):
        return ""


def sandbox_probe(runner, pwsh=None, version=None):
    """Whether the sandbox works here and holds - looked at from inside (1.76.1h: a write is tried, a new file of a
    random name, removed at once - it fails when the sandbox holds) -> '' or why not."""
    if not runner:
        return "systemd-run was not found"
    if version is None:
        version = systemd_version(runner)
    pwsh = pwsh or find_pwsh("")
    if not pwsh:
        return "pwsh was not found (%s)" % ("; ".join(PWSH_WHY) or "not on the PATH")
    if PWSH_ID and not pwsh_same(pwsh):
        return "pwsh %s changed since it was checked" % pwsh
    host = dict((p, "%d:%d" % (os.stat(p).st_dev, os.stat(p).st_ino)) for p in PROBE_PRIVATE if os.path.exists(p))
    fire_automounts()
    try:
        mps = probe_mounts()
    except MountsError as e:
        return str(e)
    ro = PROBE_RO + [os.path.expanduser("~")]
    ro += [m for m in mps if m not in ro]
    data = json.dumps({"ro": ro, "jobpid": os.getpid()}).encode("utf-8")
    # the IPC files in /proc: the host's (1.76.1c: no IPC namespace of its own with any profile), read as they are
    script = PROBE_PS1.replace("@IPC@", "@()")
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # 1.76.1c: as a user other than root, the code runs as that user: only a PID namespace of its own (systemd 257) keeps
    # that user's other processes - the job's own - out of its reach (a signal to one's own UID is allowed)
    v = FORCED_VERSION if FORCED_VERSION is not None else int(version or 0)
    if os.geteuid() != 0 and v < 257:
        return ("as a user other than root the trial needs systemd 257 or later (here %s): before, its code could signal "
                "this user's other processes - run the job as root (become: true)" % (v or "not known"))
    try:
        r = subprocess.run(sandbox_cmd(runner, 60, version=version) + [pwsh, "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                           input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, env=sandbox_env())
    except (OSError, subprocess.TimeoutExpired, RuntimeError, MountsError) as e:
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
    rwm = j.get("rwmounts") or []
    rwm = [rwm] if isinstance(rwm, str) else rwm
    if rwm:
        return "a mount of the host can be written to in it: %s" % ", ".join(rwm[:10])
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
    if os.geteuid() == 0 and not (uid.isdigit() and DYNAMIC_FIRST <= int(uid) <= DYNAMIC_LAST):
        return "it runs as UID %s, not a dynamic user of its own" % uid
    try:
        cap = int(str(j.get("capeff") or ""), 16)
    except ValueError:
        return "its capabilities could not be read"
    if cap & ~CAP_DAC_READ_SEARCH:
        return "it keeps capabilities other than reading (CapEff %s)" % j.get("capeff")
    p1 = j.get("p1") or []
    p1 = [p1] if isinstance(p1, str) else p1
    if p1:
        return "another UID's process can be read in it (%s)" % ", ".join(p1)
    if j.get("signal"):
        return "it can signal another UID's process (kill -0)"
    said = j.get("ipcrm") or []
    said = [said] if isinstance(said, str) else said
    if len(said) != 2 or not all("permission denied" in str(x).lower() for x in said):
        return "SysV IPC is not closed in it (systemd %s: ipcrm said %s)" % (version, "; ".join(str(x) for x in said)[:160] or "nothing")
    dev = j.get("dev") or []
    dev = [dev] if isinstance(dev, str) else dev
    if dev:
        return "the host's devices are in it (%s)" % ", ".join(dev)
    if os.geteuid() != 0 and str(j.get("job") or "") and str(j.get("job")) == _starttime(os.getpid()):
        return "the job's own process can be seen in it (no PID namespace of its own)"
    if os.geteuid() == 0 and uid.isdigit() and _uid_procs(int(uid)):
        return "its dynamic UID %s owns a process here after it ended" % uid
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


# 1.76.1 (Codex): the places the sandbox shows as its own, not the host's - /proc but its global files, which are the host's.
# 1.76.1d (Codex): every place a setting of the sandbox hides or replaces - /run/user (TemporaryFileSystem), the
# dynamic user's /var/lib/private, /var/cache/private and /var/log/private (DynamicUser: inaccessible, its UID gets past
# no mode of theirs; a service's /var/lib/NAME links into them), the kernel's modules (ProtectKernelModules), /dev
# (PrivateDevices: only its pseudo-devices, the same as the host's)
PRIVATE_ROOTS = ["/tmp", "/var/tmp", "/dev", "/proc", "/run/user", "/var/lib/private", "/var/cache/private",
                 "/var/log/private", "/usr/lib/modules", "/lib/modules"]
PROC_HOST = ["/proc/sys", "/proc/cmdline"]
DEV_HOST = ["/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom", "/dev/tty", "/dev/stdin",
            "/dev/stdout", "/dev/stderr"]
# under a private place, what is the host's in the sandbox too
HOST_INSIDE = PROC_HOST + DEV_HOST


def _norm(path):
    """A literal path -> (normalized: . .. // collapsed - up to its first component with a wildcard, wildcard?)."""
    p = posixpath.normpath(re.sub(r"/+", "/", path))
    keep, wild = [], False
    for x in p.split("/"):
        if re.search(r"[*?\[]", x):
            wild = True
            break
        keep.append(x)
    return ("/".join(keep) or "/"), wild


def touch_of(path, recursive=False):
    """-> the place of PRIVATE_ROOTS a path is in - or, for a program or cmdlet that walks down (recursive, or a
    wildcard), above - normalized AND through the host's symlinks (realpath, looked at here, outside the sandbox: an
    alias like /var/../tmp or a link into /tmp is the same place); '' when none. A path that is not absolute: '?'."""
    if not str(path).startswith("/"):
        return "?"
    n, wild = _norm(str(path))
    rec = recursive or wild
    for q in (n, os.path.realpath(n)):
        for t in PRIVATE_ROOTS + AUTOFS_HIDDEN:
            if q == t or q.startswith(t + "/"):
                if any(q == x or q.startswith(x + "/") for x in HOST_INSIDE):
                    continue
                return t
            if rec and (q == "/" or t.startswith(q.rstrip("/") + "/")):
                return t
    return ""


def path_snapshot(paths, calls):
    """1.76.1b: the host's realpath of every literal path a code reads - its cmdlets' (paths) and its programs' arguments
    that are paths (calls) - looked at here, outside the sandbox: taken before the trial and again after; a change means
    the host's links moved during the trial, and its result is dropped."""
    out = {}
    for p in paths or []:
        if isinstance(p, dict) and p.get("literal") and str(p.get("path") or "").startswith("/"):
            n = _norm(str(p["path"]))[0]
            out[n] = os.path.realpath(n)
    for c in calls or []:
        for x in (c or [])[1:]:
            if str(x).startswith("/"):
                n = _norm(str(x))[0]
                out[n] = os.path.realpath(n)
    return out


def path_moved(before, after):
    """The paths whose realpath changed between two snapshots -> ['path: was -> now'] ([] when none)."""
    return ["%s: %s -> %s" % (k, v, after.get(k)) for k, v in sorted((before or {}).items()) if after.get(k) != v]


def _touches_tmp(path, recursive):
    return touch_of(path, recursive) != ""


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


PLACE = {"/tmp": "a temporary folder (/tmp, /var/tmp: the sandbox has its own)",
         "/var/tmp": "a temporary folder (/tmp, /var/tmp: the sandbox has its own)",
         "/dev": "/dev (the sandbox has its own: no host device, its own /dev/shm and message queues)",
         "/proc": "/proc of the processes (the sandbox sees its own, or none of other users)",
         "/run/user": "/run/user (hidden in the sandbox)",
         "/var/lib/private": "a dynamic user's private folder (hidden in the sandbox)",
         "/var/cache/private": "a dynamic user's private folder (hidden in the sandbox)",
         "/var/log/private": "a dynamic user's private folder (hidden in the sandbox)",
         "/usr/lib/modules": "the kernel's modules (hidden in the sandbox)",
         "/lib/modules": "the kernel's modules (hidden in the sandbox)"}


def blind(code, calls=None, paths=None):
    """What a code would read from the sandbox and not the host -> '' or why it is not tried. 1.76.1 (Codex): per command,
    from the read-only check's syntax tree - paths: each file-reading cmdlet's path {cmd, path, literal, recurse} - not
    per line of text. Only a literal path may be read inside, one that is - normalized and through the host's symlinks -
    in none of the sandbox's own places (touch_of); any other is not tried. calls: the programs' command lines - find
    and stat there are replayed (replays_for), grep there is not tried (outside it would read a host FIFO)."""
    for rx, what in BLIND:
        if re.search(rx, code or "", re.I if "proc" in rx else 0):
            return "it reads %s - the trial cannot show it as the host has it" % what
    for p in paths or []:
        if not isinstance(p, dict):
            continue
        if not p.get("literal"):
            return "%s is given a path that is not a plain literal (%s) - the trial reads only literal paths" % (
                p.get("cmd") or "a cmdlet", str(p.get("path") or "")[:80])
        t = touch_of(p.get("path") or "", bool(p.get("recurse")))
        if t == "?":
            return "%s is given %s - not an absolute path" % (p.get("cmd") or "a cmdlet", str(p.get("path"))[:80])
        if t:
            return "it reads %s - the trial cannot show %s as the host has it (%s %s)" % (
                PLACE.get(t, "an automount the sandbox cannot show as the host has it"), t, p.get("cmd") or "", str(p.get("path"))[:80])
    for c in calls or []:
        if c and c[0] == "/usr/bin/grep":
            rec = "-r" in c
            for x in c[1:]:
                t = touch_of(x, rec) if x.startswith("/") else ""
                if t:
                    return "it reads %s - the trial cannot show %s as the host has it (grep %s)" % (PLACE.get(t, "an automount the sandbox cannot show as the host has it"), t, x[:80])
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


class MountsError(RuntimeError):
    """1.76.1f: the host's mounts could not be read or passed to systemd as they are - no sandbox."""


MOUNTS_MAX = 1000


def mounts():
    """1.76.1f: the host's mount points (/proc/self/mountinfo, here, outside the sandbox) -> [(mount point, fs type,
    super options)]. Read as bytes, each record split on ASCII spaces (a mount name may hold any other character), the
    path's octal escapes (\\040 a space ...) undone. Not read -> MountsError (no sandbox)."""
    try:
        with open("/proc/self/mountinfo", "rb") as f:
            data = f.read()
    except (OSError, IOError) as e:
        raise MountsError("the host's mounts could not be read (/proc/self/mountinfo: %s)" % e)
    out = []
    for line in data.split(b"\n"):
        if not line:
            continue
        a = line.split(b" ")
        try:
            sep = a.index(b"-", 6)
        except ValueError:
            raise MountsError("the host's mounts could not be read (a mountinfo line not understood)")
        raw = re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), a[4])
        try:
            mp = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue                    # 1.76.1g: left to the probe (it reads the mount table inside)
        out.append((mp, a[sep + 1].decode("ascii", "replace") if len(a) > sep + 1 else "",
                    a[sep + 3].decode("ascii", "replace") if len(a) > sep + 3 else ""))
    return out


# the sandbox's own: not the host's to keep read-only - the kernel's file systems (guarded by capabilities it has not)
# and the places the sandbox replaces with its own (no host data shown)
OWN_MOUNTS = ("/proc", "/sys", "/dev", "/run/user", "/tmp", "/var/tmp")
# what systemd's property parser takes as a path: no space, quote, backslash or control character (else no sandbox)
SAFE_PATH = re.compile(r"^/[^\s\"'\\\x00-\x1f\x7f]*$")


def probe_mounts():
    """1.76.1f: every mount point of the host the sandbox shows - each made read-only by name in it (sandbox_cmd) and
    looked at by the probe: all but the sandbox's own places, whatever their name (a container's storage too). One
    systemd cannot be given, or more than MOUNTS_MAX -> MountsError: no sandbox rather than one with a mount left out."""
    out = []
    for mp, fs, _ in mounts():
        if fs == "autofs" or any(mp == x or mp.startswith(x + "/") for x in OWN_MOUNTS) or mp in out:
            continue
        # 1.76.1g: a name systemd cannot be given is left out of the list - the probe still looks at it (its own
        # mount table): never a refusal for a name or a count, only for a mount that is writable inside
        if SAFE_PATH.match(mp):
            out.append(mp)
    return out[:MOUNTS_MAX]


# 1.76.1f: automounts the sandbox cannot show as the host has them - an indirect map (its keys are mounted on demand,
# and none is after the sandbox starts), or a direct one that did not mount: a code that reads below one is not tried
AUTOFS_HIDDEN = []


def refresh_autofs():
    """1.76.1g: AUTOFS_HIDDEN from the mount table now - each automount point with nothing mounted on it (an indirect map,
    one that did not mount, or one that expired since): a code that reads below one is not tried. Before each code's
    blind(), so it is never a list of before."""
    try:
        now = mounts()
    except MountsError:
        return False            # 1.76.1h: not known now - the caller runs no trial (never an old list)
    real = set(mp for mp, fs, _ in now if fs != "autofs")
    del AUTOFS_HIDDEN[:]
    AUTOFS_HIDDEN.extend(sorted(set(mp for mp, fs, opts in now if fs == "autofs" and (mp not in real or "indirect" in opts.split(",")))))
    return True


def fire_automounts(budget=30):
    """1.76.1f: each direct automount of the host (autofs) fired here, outside the sandbox, so it is mounted when the
    sandbox is made, read-only in it (MountFlags=private: none fires inside, none arrives later). 1.76.1g: once per
    job (the probe), within one budget for them all - each touch a process given a hard deadline and never waited on
    (one stuck on a dead NFS server cannot hold the job). An indirect map, or one that did not mount: AUTOFS_HIDDEN.
    -> the ones mounted."""
    try:
        before = mounts()
    except MountsError:
        return []
    auto = [(mp, opts) for mp, fs, opts in before if fs == "autofs" and "indirect" not in opts.split(",")]
    end = time.time() + budget
    for mp, _ in auto:
        if time.time() >= end:
            break
        try:
            pr = subprocess.Popen(["/usr/bin/stat", "-f", mp + "/."], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        except OSError:
            continue
        while pr.poll() is None and time.time() < end:
            time.sleep(0.05)
        if pr.poll() is None:
            try:
                pr.kill()                       # not waited on: a process in D state may never go
            except OSError:
                pass
    refresh_autofs()
    real = set()
    try:
        real = set(mp for mp, fs, _ in mounts() if fs != "autofs")
    except MountsError:
        pass
    return [mp for mp, _ in auto if mp in real]


def run_trial(pwsh, runner, code, seconds, binds=None, version=None):
    """One trial of a code in the sandbox -> (json or None, error, seconds). binds: the replay shims. version:
    systemd's (None: asked)."""
    t = time.time()
    if version is None:
        version = systemd_version(runner)
    if PWSH_ID and not pwsh_same(pwsh):
        return None, "pwsh %s changed since it was checked - not run" % pwsh, 0
    try:
        p = subprocess.run(sandbox_cmd(runner, seconds, binds, version) + trial_command(pwsh, code),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds + 60,
                           env=sandbox_env())
    except subprocess.TimeoutExpired:
        return None, "no answer in %d seconds" % seconds, round(time.time() - t, 1)
    except MountsError as e:            # 1.76.1h: the mount table changed or cannot be read - a refusal, said
        return None, "no sandbox (%s): a trial runs only in a read-only sandbox" % e, round(time.time() - t, 1)
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


PWSH_WHY = []
PWSH_ID = {}


def _secure_why(path, stat=os.stat):
    """1.76.1b (Codex): a file root owns, no group or other user can write - nor any folder above it (a writable folder
    lets the file be swapped, as sudo's secure_path checks) -> '' or why not."""
    try:
        st = stat(path)
    except OSError as e:
        return "%s: %s" % (path, e)
    if st.st_uid != 0:
        return "%s is not owned by root" % path
    if st.st_mode & 0o022:
        return "%s can be written by others than root" % path
    d = os.path.dirname(path.rstrip("/")) or "/"
    while True:
        try:
            sd = stat(d)
        except OSError as e:
            return "%s: %s" % (d, e)
        if sd.st_uid != 0:
            return "the folder %s (above %s) is not owned by root" % (d, path)
        if sd.st_mode & 0o022:
            return "the folder %s (above %s) can be written by others than root" % (d, path)
        if d == "/":
            return ""
        d = os.path.dirname(d) or "/"


def pwsh_trusted(path, stat=os.stat):
    """The pwsh the check and the trial run (resolved): root's, and so is every folder above it -> '' or why not."""
    return _secure_why(path, stat)


def _identity(path):
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_uid, st.st_mode, st.st_size, st.st_mtime)


def pwsh_same(path):
    """Right before each run: the pwsh is still the file that was checked (device, inode, owner, mode, size, time)."""
    try:
        return path in PWSH_ID and _identity(path) == PWSH_ID[path]
    except OSError:
        return False


def find_pwsh(path):
    """pwsh - the given path (check_stig_pwsh), else the first on the host's PATH (then /usr/bin, /opt/microsoft/powershell/7),
    resolved through its links; only one root owns that no one else can write, in folders the same - the given path's
    and the resolved one's ('' = none; PWSH_WHY says why). Its identity is kept: each run checks it again (pwsh_same)."""
    del PWSH_WHY[:]
    cands = [path] if path else [os.path.join(d, "pwsh") for d in os.environ.get("PATH", "").split(os.pathsep) + ["/usr/bin", "/opt/microsoft/powershell/7"] if d]
    for c in cands:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            real = os.path.realpath(c)
            bad = pwsh_trusted(real) or (_secure_why(os.path.dirname(os.path.abspath(c)) or "/") if c != real else "")
            if not bad:
                PWSH_ID[real] = _identity(real)
                return real
            PWSH_WHY.append(bad)
            if path:
                return ""
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
    if PWSH_ID and not pwsh_same(pwsh):
        return None, "pwsh %s changed since it was checked - not run" % pwsh, 0
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
    # 1.76.4 (Codex): the trial off - the sandbox's path is never looked at (a lookup may fire an automount)
    if p["trial"] and p.get("sandbox") and os.path.basename(p["sandbox"]) != "systemd-run" and os.path.exists(p["sandbox"]):
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
        module.exit_json(changed=False, evaluate=ev, gate={}, trial={},
                         error="pwsh was not found on the host" + ((" (" + "; ".join(PWSH_WHY) + ")") if PWSH_WHY else ""))
    ev["pwsh"] = pwsh
    work = tempfile.mkdtemp(prefix="site-answer-keys-")
    os.chmod(work, 0o700)
    runner, no_sandbox, answers, version = None, "", {}, 0
    if p["trial"] and any(c in work for c in " :,\"'"):     # 1.76.4 (Codex): bound only for a trial
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
            if gate[cid]["ok"] and not p["trial"]:
                # 1.76.4: the trial is off (answer_keys_trial, the default) - no probe, no systemd-run, no automount fired:
                # the block is offered, marked not tried
                trial[cid] = {"ran": False, "off": True, "ok": False, "valid": None, "read": "", "seconds": 0,
                              "error": "the trial is off (answer_keys_trial)"}
                continue
            if not gate[cid]["ok"] or module.check_mode:
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
            gp = gate[cid].get("paths") or []
            if not refresh_autofs():    # 1.76.1g: the automounts as they are now, not as at the probe
                trial[cid] = {"ran": False, "ok": False, "valid": None, "read": "", "seconds": 0,
                              "error": "no sandbox (the mount table could not be read): a trial runs only in a read-only sandbox"}
                continue
            why_blind = blind(code, calls, [gp] if isinstance(gp, dict) else gp)
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
            snap = path_snapshot(gp if isinstance(gp, list) else [gp], calls)
            r, err, secs = run_trial(pwsh, runner, code, max(5, p["timeout"]), binds, version)
            moved = path_moved(snap, path_snapshot(gp if isinstance(gp, list) else [gp], calls))
            if moved:
                # 1.76.1b: a link the check looked at moved during the trial - its result is not the host's as checked
                trial[cid] = {"ran": False, "ok": False, "valid": None, "read": "", "seconds": secs, "replayed": replayed,
                              "error": "a path changed during the trial (%s)" % "; ".join(moved)[:240]}
                continue
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
