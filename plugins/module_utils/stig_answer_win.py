# -*- coding: utf-8 -*-
"""1.105.0: STIG answer keys for Windows Server 2019, 2022 and 2025 - a rule's ValidationCode from DISA's check text by a
fixed pattern (the Windows part of plugins/filter/stig_answer_filters.py's drafter). Standard library only.

  classify(check)        which kind of check a DISA check text is: registry value, AuditPol subcategory, user right,
                         role or feature - or other (a person, or ACT, decides it)
  draft(rule, values)    {fixed: True, kind: pattern, pattern, code, reads, uses, true, false, disa} - a ValidationCode that only
                         reads; {fixed: True, kind: needs, needs, example, why} when it needs a site value not given; None when no
                         pattern fits; {kind: role_scoped} when the shared key cannot express applicability. rule: {v, rule, severity, title, check, fix}
  commands_in(code)      every command (cmdlet, native program, .NET type) a code uses, for the allowlist below
  forbidden_forms(code)  uses of the export and cleanup commands outside their exact permitted forms

The trial uses the command inventory plus its own PowerShell syntax checks and restricted command proxies.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

# what a ValidationCode may call and the trial's session may run (cmdlet -> what it does, for the report's "What it reads")
ALLOWED = {
    "Get-SiteValueKind": "reads a registry value's type (the trial's own proxy)",
    "Get-ItemProperty": "reads a registry value",
    "Get-Item": "reads a registry key or a file's properties",
    "Test-Path": "asks whether a registry key or a path exists",
    "Get-ChildItem": "lists a registry key's subkeys or a folder",
    "Remove-Item": "removes the temporary user-rights export",
    "Join-Path": "constructs the temporary export path",
    "[guid]::NewGuid": "generates a unique temporary file name",
    "Get-Content": "reads a text file",
    "Get-WindowsFeature": "asks whether a role or feature is installed",
    "Get-Service": "reads a service's state and start type",
    "Get-CimInstance": "reads a WMI class",
    "Get-LocalGroupMember": "lists a local group's members",
    "Get-LocalUser": "reads a local account",
    "Get-Command": "asks whether a program is there",
    "Select-Object": "picks properties of what was read",
    "Where-Object": "filters what was read",
    "ForEach-Object": "goes through what was read",
    "Sort-Object": "orders what was read",
    "Measure-Object": "counts what was read",
    "Write-Verbose": "says what was read",
    "Out-String": "turns what was read into text",
    "ConvertFrom-Csv": "reads the rows and named columns of CSV text in memory",
    "auditpol.exe": "reads an audit policy subcategory (/get /subcategory: only)",
    "secedit.exe": "exports user rights to the trial or generated temporary file",
}
KINDS = ("registry value", "AuditPol subcategory", "user right", "role or feature", "other")


def classify(check):
    """The kind of a DISA check text (the fixture tests/fixtures/stig_win/rules.json has the class of each)."""
    c = str(check or "")
    low = c.lower()
    if "Registry Path" in c or "registry value" in low:
        return "registry value"
    if "auditpol" in low:
        return "AuditPol subcategory"
    if "User Rights Assignment" in c:
        return "user right"
    if "Get-WindowsFeature" in c or "Roles and Features" in c or "Server Manager" in c:
        return "role or feature"
    return "other"


# Tokens used by our literal-only code generator. Keep quoted data (including
# doubled apostrophes) and comments out of the command inventory.
_PS_TOKEN = re.compile(
    r"""(?ms)@'\r?\n.*?^'@|@"\r?\n.*?^"@|(?P<block><\#)|\#[^\n]*|"""
    r"""'(?:''|[^'])*'|"(?:`.|""|[^"`])*"|`.|"""
    r"""(?P<command>\[[\w.]+\]\s*::\s*\w+(?=\s*\()|\b[\w.-]+\.(?i:exe)\b|\b[A-Za-z][\w]*-[A-Za-z][\w-]*\b)"""
)

_COMMENT_DELIMITER = re.compile(r"<#|#>")


def _command_tokens(code):
    pos = 0
    while True:
        token = _PS_TOKEN.search(code, pos)
        if not token:
            return
        pos = token.end()
        if token.lastgroup == "command":
            yield token
        elif token.lastgroup == "block":
            depth = 1
            for delimiter in _COMMENT_DELIMITER.finditer(code, pos):
                depth += 1 if delimiter.group() == "<#" else -1
                if depth == 0:
                    pos = delimiter.end()
                    break
            else:
                return


def commands_in(code):
    """Inventory cmdlets, executable names and static method calls."""
    return {re.sub(r"\s+", "", token.group()) for token in _command_tokens(code)}


_EXPORT = "secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l"
_REMOVE = "Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue"
_REMOVE_LOG = _REMOVE.replace("$f", "$l")


def forbidden_forms(code):
    """Check restricted commands in the generator's one-statement-per-line syntax."""
    bad = []
    for token in _command_tokens(code):
        expected = {"secedit.exe": (_EXPORT,), "remove-item": (_REMOVE, _REMOVE_LOG)}.get(token.group().lower())
        if expected:
            start = code.rfind("\n", 0, token.start()) + 1
            line = code[start:].split("\n", 1)[0].strip()
            if line not in expected or code[:start].rstrip("\r\n").endswith("`"):
                bad.append(line)
    return bad


def _q(data):
    return "'" + data.replace("'", "''") + "'"


def _pattern(kind, disa, body, expression, what, uses=None):
    code = "\n".join(["try {"] + ["    " + line for line in body] + [
        "    $ok = " + expression, "} catch {", "    Write-Verbose ('read: failed: ' + $_)",
        "    $ok = $false", "}", "return ($ok)"])
    return {"fixed": True, "kind": "pattern", "pattern": kind, "code": code, "disa": disa, "uses": uses or {},
            "reads": [[cmd, ALLOWED[cmd]] for cmd in sorted(commands_in(code))],
            "true": what + " meets the check.", "false": what + " failed the check or could not be verified."}


_REQUIRED = (
    "If the following registry value does not exist or is not configured as specified, this is a finding:",
    "Verify the registry value below. If it does not exist or is not configured as specified, this is a finding.",
)
_DEFAULT = (
    r"The default behavior is for (?:Windows to mark file attachments with their zone information|"
    r"File Explorer heap termination on corruption to be enabled|"
    r"the Windows RSS platform to not use Basic authentication over HTTP connections)\.\n"
    r"If the registry [Vv]alue [Nn]ame below does not exist, this is not a finding\.\n"
    r'If it exists and is configured with a value of "([0-9]{1,10})", this is not a finding\.\n'
    r'If it exists and is configured with a value of "([0-9]{1,10})", this is a finding\.'
)
_EARLY_LAUNCH = (
    'The default behavior is for Early Launch Antimalware - Boot-Start Driver Initialization policy to enforce '
    '"Good, unknown and bad but critical" (preventing "bad").\n'
    'If the registry value name below does not exist, this is not a finding.\n'
    'If it exists and is configured with a value of "0x00000007 (7)", this is a finding.'
)
_EARLY_OPTIONS = (
    "0x00000001 (1), 0x00000003 (3), or 0x00000008 (8) (or if the Value Name does not exist)\n"
    "Possible values for this setting are:\n8 - Good only\n1 - Good and unknown\n"
    '3 - Good, unknown and bad but critical\n7 - All (which includes "bad" and would be a finding)'
)
_DOWNLOAD_OPTIONS = (
    "0x00000000 (0) - No peering (HTTP Only)\n0x00000001 (1) - Peers on same NAT only (LAN)\n"
    "0x00000002 (2) - Local Network / Private group peering (Group)\n"
    "0x00000063 (99) - Simple download mode, no peering (Simple)\n"
    "0x00000064 (100) - Bypass mode, Delivery Optimization not used (Bypass)\n"
    "A value of 0x00000003 (3), Internet, is a finding."
)
_NUM = r"(?:0x[0-9a-fA-F]{1,8} \([0-9]{1,10}\)|[0-9]{1,10})"


def _number(token):
    if token.startswith("0x"):
        hexpart, decimal = token.split(" ")
        n = int(decimal[1:-1])
        if n != int(hexpart, 16):
            return None
    else:
        n = int(token)
    return n if n <= 0xffffffff else None


def _dword(spec):
    """Return a comparison only after consuming the entire value specification."""
    match = re.fullmatch(
        r"(" + _NUM + r")(?: (or (?:greater|less)|\(or (?:greater|less)\)|"
        r'\(or less, excluding "0" which is effectively disabled\)))?',
        spec)
    if match:
        n = _number(match[1])
        if n is None:
            return None
        if "excluding" in spec:
            return "$n -gt 0 -and $n -le [long]" + _q(str(n))
        qualifier = (match[2] or "").strip("()")
        op = {"": "-eq", "or greater": "-ge", "or less": "-le"}[qualifier]
        return "$n %s [long]%s" % (op, _q(str(n)))
    options = re.fullmatch(r"(?:(?i:any of):? )?(" + _NUM + r"(?:(?:, (?:or )?| or )" + _NUM + r")+)", spec)
    if options:
        numbers = [_number(t) for t in re.findall(_NUM, options[1])]
        if None not in numbers:
            return "@(%s) -contains $n" % ", ".join("[long]" + _q(str(n)) for n in numbers)


def _registry(check):
    intro, sep, fields = check.partition("\nRegistry Hive:")
    default = re.fullmatch(_DEFAULT, intro)
    early_launch = intro == _EARLY_LAUNCH
    if not sep or (intro not in _REQUIRED and not default and not early_launch):
        return None
    match = re.fullmatch(
        r" *HKEY_LOCAL_MACHINE\nRegistry Path:? *(\\[A-Za-z0-9_ .'\\-]+)\n"
        r"Value Name: *([A-Za-z0-9_'-]+)\n(?:Value )?Type: *(REG_DWORD|REG_SZ)\nValue: *(.+)", fields, re.S)
    if not match:
        return None
    path, name, value_type, spec = match.groups()
    if path.strip("\\") == "" or path.startswith("\\\\"):
        return None
    if any(part in ("", ".", "..") for part in path.strip("\\").split("\\")):
        return None
    if name.lower() in ("pspath", "psparentpath", "pschildname", "psdrive", "psprovider", "psobject",
                        "psbase", "psadapted", "psextended", "pstypenames"):
        return None
    path = "HKLM:" + path.rstrip("\\")
    body = ["$path = " + _q(path), "$name = " + _q(name)]
    if early_launch:
        if name != "DriverLoadPolicy" or spec != _EARLY_OPTIONS or value_type != "REG_DWORD":
            return None
        spec = "any of 1, 3, or 8"
    if name == "DODownloadMode" and spec == _DOWNLOAD_OPTIONS and value_type == "REG_DWORD":
        spec = "any of 0, 1, 2, 99, or 100"
    if default:
        suffix = " (or if the Value Name does not exist)"
        if not spec.endswith(suffix) or value_type != "REG_DWORD":
            return None
        spec = spec[:-len(suffix)]
        if not re.fullmatch(_NUM, spec) or _number(spec) != int(default[1]) or int(default[1]) == int(default[2]):
            return None
    if default or early_launch:
        # Only confirmed absence passes. A provider/access error goes to catch.
        body += [r"$parts = $path -split '\\'", "$parent = $parts[0] + '\\'",
                 "foreach ($part in $parts[1..($parts.Count - 1)]) {",
                 "    $children = @(Get-ChildItem -LiteralPath $parent -ErrorAction Stop)",
                 "    foreach ($child in $children) { if (-not $child.PSChildName) { return ($false) } }",
                 "    if ($children.PSChildName -notcontains $part) {",
                 "        Write-Verbose ('read: absent key ' + $parent + $part)", "        return ($true)", "    }",
                 "    $parent += $part + '\\'", "}",
                 "$key = Get-Item -LiteralPath $path -ErrorAction Stop",
                 "$names = $key.Property",
                 "if ($null -eq $names) { return ($false) }",
                 "if ($names -notcontains $name) {", "    Write-Verbose ('read: absent value ' + $name)",
                 "    return ($true)", "}"]
    body += ["$item = Get-ItemProperty -LiteralPath $path -Name $name -ErrorAction Stop",
             "$value = $item.$name", "Write-Verbose ('read: ' + $path + ' ' + $name + ' = ' + $value)"]
    if value_type == "REG_DWORD":
        comparison = _dword(spec)
        if comparison is None:
            return None
        body += ["if ($value -isnot [int]) { return ($false) }", "$n = [long]$value -band 4294967295"]
    else:
        # Accept a single literal, never a prose description of alternatives.
        if not re.fullmatch(r"[A-Za-z0-9_.'-]+|O:BAG:BAD:\(A;;RC;;;BA\)", spec):
            return None
        body += ["$kind = $null", "if (Get-Command -Name Get-SiteValueKind -ErrorAction SilentlyContinue) {",
                 "    $kind = Get-SiteValueKind -LiteralPath $path -Name $name", "} else {",
                 "    $kind = (Get-Item -LiteralPath $path).GetValueKind($name).ToString()", "}"]
        comparison = "$kind -eq 'String' -and $value -is [string] -and $value -ceq " + _q(spec)
    return _pattern(KINDS[0], path, body, comparison, path + " " + name)


_AUDIT = (
    r'Security Option "Audit: Force audit policy subcategory settings \(Windows (?:Vista|7) '
    r'or later\) to override audit policy category settings" must be set to "Enabled" \(WN(?:19|22|25)-SO-000050\) '
    r'for the detailed auditing subcategories to be effective\.\n'
    r'Use the "AuditPol" tool to review the current Audit Policy configuration[:.]\n'
    r'Open (?:"PowerShell" or a "Command Prompt"|PowerShell or a command prompt) with elevated privileges '
    r'\("Run as administrator"\)\.\nEnter "AuditPol /get /category:\*"\.?\n'
    r'Compare the "AuditPol" settings with the following[:.]\n'
    r'If the system does not audit the following, this is a finding[:.]\n'
    r"([A-Za-z/ ]+) >> ([A-Za-z /-]+) - (Success and Failure|Success|Failure)"
)


# Advanced Audit Policy subcategory GUIDs share the prefix 0CCE92 and the same final four fields.
_SUBCATEGORIES = {
    "System": {
        "Security State Change": "10",
        "Security System Extension": "11",
        "System Integrity": "12",
        "IPsec Driver": "13",
        "Other System Events": "14",
    },
    "Logon/Logoff": {
        "Logon": "15",
        "Logoff": "16",
        "Account Lockout": "17",
        "IPsec Main Mode": "18",
        "IPsec Quick Mode": "19",
        "IPsec Extended Mode": "1A",
        "Special Logon": "1B",
        "Other Logon/Logoff Events": "1C",
        "Network Policy Server": "43",
        "User / Device Claims": "47",
        "Group Membership": "49",
    },
    "Object Access": {
        "File System": "1D",
        "Registry": "1E",
        "Kernel Object": "1F",
        "SAM": "20",
        "Certification Services": "21",
        "Application Generated": "22",
        "Handle Manipulation": "23",
        "File Share": "24",
        "Filtering Platform Packet Drop": "25",
        "Filtering Platform Connection": "26",
        "Other Object Access Events": "27",
        "Detailed File Share": "44",
        "Removable Storage": "45",
        "Central Policy Staging": "46",
    },
    "Privilege Use": {
        "Sensitive Privilege Use": "28",
        "Non Sensitive Privilege Use": "29",
        "Other Privilege Use Events": "2A",
    },
    "Detailed Tracking": {
        "Process Creation": "2B",
        "Process Termination": "2C",
        "DPAPI Activity": "2D",
        "RPC Events": "2E",
        "Plug and Play Events": "48",
        "Token Right Adjusted Events": "4A",
    },
    "Policy Change": {
        "Audit Policy Change": "2F",
        "Authentication Policy Change": "30",
        "Authorization Policy Change": "31",
        "MPSSVC Rule-Level Policy Change": "32",
        "Filtering Platform Policy Change": "33",
        "Other Policy Change Events": "34",
    },
    "Account Management": {
        "User Account Management": "35",
        "Computer Account Management": "36",
        "Security Group Management": "37",
        "Distribution Group Management": "38",
        "Application Group Management": "39",
        "Other Account Management Events": "3A",
    },
    "DS Access": {
        "Directory Service Access": "3B",
        "Directory Service Changes": "3C",
        "Directory Service Replication": "3D",
        "Detailed Directory Service Replication": "3E",
    },
    "Account Logon": {
        "Credential Validation": "3F",
        "Kerberos Service Ticket Operations": "40",
        "Other Account Logon Events": "41",
        "Kerberos Authentication Service": "42",
    },
}


def _audit(check):
    match = re.fullmatch(_AUDIT, check)
    if not match:
        return None
    category, subcategory, setting = match.groups()
    suffix = _SUBCATEGORIES.get(category, {}).get(subcategory)
    if not suffix:
        return None
    guid = "{0CCE92" + suffix + "-69AE-11D9-BED3-505054503030}"
    body = [r"$override = (Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name "
            "'SCENoApplyLegacyAuditPolicy' -ErrorAction Stop).SCENoApplyLegacyAuditPolicy",
            "Write-Verbose ('read: audit policy override = ' + $override)",
            "if ($override -isnot [int] -or $override -ne 1) { return ($false) }",
            "$name = " + _q(guid), "$csv = auditpol.exe /get ('/subcategory:' + $name) /r",
            "if ($LASTEXITCODE -ne 0) { return ($false) }",
            "$rows = @($csv | ConvertFrom-Csv -ErrorAction Stop -WarningAction Stop)",
            "if ($rows.Count -ne 1) { return ($false) }",
            "$columns = @($rows[0].PSObject.Properties)",
            "if ($columns.Count -lt 5 -or $columns[3].Value -ne $name) { return ($false) }",
            # Non-English Windows is unsupported. /get /r localizes the inclusion label and supplies no reliable numeric value.
            "$setting = $columns[4].Value", "Write-Verbose ('read: ' + $name + ' = ' + $setting)"]
    comparison = "$setting -eq 'Success and Failure'"
    if setting != "Success and Failure":
        comparison += " -or $setting -eq " + _q(setting)
    result = _pattern(KINDS[1], 'AuditPol /get /subcategory:%s /r' % guid, body, comparison, subcategory)
    result["false"] += " Non-English Windows is unsupported."
    return result


_RIGHTS = {
    "Access Credential Manager as a trusted caller": "SeTrustedCredManAccessPrivilege",
    "Access this computer from the network": "SeNetworkLogonRight",
    "Act as part of the operating system": "SeTcbPrivilege",
    "Add workstations to domain": "SeMachineAccountPrivilege",
    "Allow log on locally": "SeInteractiveLogonRight",
    "Allow log on through Remote Desktop Services": "SeRemoteInteractiveLogonRight",
    "Back up files and directories": "SeBackupPrivilege",
    "Change the system time": "SeSystemtimePrivilege",
    "Create a pagefile": "SeCreatePagefilePrivilege",
    "Create a token object": "SeCreateTokenPrivilege",
    "Create global objects": "SeCreateGlobalPrivilege",
    "Create permanent shared objects": "SeCreatePermanentPrivilege",
    "Create symbolic links": "SeCreateSymbolicLinkPrivilege",
    "Debug programs": "SeDebugPrivilege",
    "Deny access to this computer from the network": "SeDenyNetworkLogonRight",
    "Deny log on as a batch job": "SeDenyBatchLogonRight",
    "Deny log on as a service": "SeDenyServiceLogonRight",
    "Deny log on locally": "SeDenyInteractiveLogonRight",
    "Deny log on through Remote Desktop Services": "SeDenyRemoteInteractiveLogonRight",
    "Impersonate a client after authentication": "SeImpersonatePrivilege",
    "Increase scheduling priority": "SeIncreaseBasePriorityPrivilege",
    "Load and unload device drivers": "SeLoadDriverPrivilege",
    "Log on as a batch job": "SeBatchLogonRight",
    "Log on as a service": "SeServiceLogonRight",
    "Manage auditing and security log": "SeSecurityPrivilege",
    "Modify firmware environment values": "SeSystemEnvironmentPrivilege",
    "Perform volume maintenance tasks": "SeManageVolumePrivilege",
    "Profile single process": "SeProfileSingleProcessPrivilege",
    "Restore files and directories": "SeRestorePrivilege",
    "Shut down the system": "SeShutdownPrivilege",
    "Take ownership of files or other objects": "SeTakeOwnershipPrivilege",
}

# Placeholders in DISA text identify domain-relative SIDs that the trial cannot verify.
_SIDS = {
    "Administrators": "S-1-5-32-544", "Users": "S-1-5-32-545", "Guests": "S-1-5-32-546",
    "Backup Operators": "S-1-5-32-551", "Remote Desktop Users": "S-1-5-32-555",
    "Local Service": "S-1-5-19", "Network Service": "S-1-5-20", "Service": "S-1-5-6",
    "Authenticated Users": "S-1-5-11", "Enterprise Domain Controllers": "S-1-5-9",
    "Local account": "S-1-5-113", "Local account and member of Administrators group": "S-1-5-114",
    "Enterprise Admins": "S-1-5-root domain-519", "Domain Admins": "S-1-5-domain-512",
    "Domain Controllers": "S-1-5-domain-516",
}

_RIGHT_INTRO = (
    r'Verify the effective setting in Local Group Policy Editor\.\nRun (?:"gpedit\.msc"|gpedit\.msc)\.\n'
    r'Navigate to Local Computer Policy >> Computer Configuration >> Windows Settings >> Security Settings >> '
    r'Local Policies >> User Rights Assignment\.\n'
)
_CORE = (r'\nFor server core installations, run the following command:\n'
         r'Secedit /Export /Areas User_Rights /cfg c:\\path\\filename\.txt\nReview the text file\.\n')
_APPLICATION = (
    r'\nIf an application requires this user right, this (?:would not be|is not) a finding\.\n'
    r'Vendor documentation must support the requirement for having the user right\.\n'
    r'The requirement must be documented with the (?:ISSO|(?i:information system security officer) \(ISSO\))\.\n'
    r'The application account must meet requirements for application account passwords, such as length '
    r'\(WN(19|22|25)-00-000050\) and required frequency of changes \(WN\1-00-000060\)\.'
    r'(?:\nPasswords for application accounts with this user right must be protected as highly privileged accounts\.)?'
)
_AUDITORS = ("\nIf the organization has an Auditors group, the assignment of this group to the user right "
             "would not be a finding.")


def _right_members(check):
    # Require the GUI and server-core instructions to describe the same
    # assignment, including their condition and every listed principal.
    intro = re.match(_RIGHT_INTRO, check)
    if not intro:
        return None
    parts = re.split(_CORE, check[intro.end():])
    if len(parts) != 2:
        return None
    gui, core = parts
    application = re.search(_APPLICATION + r'\Z', core)
    auditors = False
    if application:
        core = core[:application.start()]
        auditors = core.endswith(_AUDITORS)
        if auditors:
            core = core[:-len(_AUDITORS)]
    headers = (
        ("subset", 'If any {group} other than the following are granted the {right}, this is a finding'),
        ("superset", 'If the following {group} are not defined for the {right}, this is a finding'),
        ("empty", 'If any {group} are granted the {right}, this is a finding'),
        ("subset", 'Only the following {group} (?:may be|are) granted the {right}'),
        ("exact", 'Exactly the following {group} must be granted the {right}'),
        ("superset", 'The {right} must include the following {group}'),
        ("superset", 'The {right} must deny the following {group}'),
    )
    gui_header, _, gui_list = gui.partition("\n")
    core_header, _, core_list = core.partition("\n")
    for direction, header in headers:
        match = re.fullmatch(header.format(group="accounts or groups", right=r'"([A-Za-z ]+)" (?:user )?right')
                             + r'[.:]', gui_header, re.I)
        if not match:
            continue
        right = _RIGHTS.get(match[1])
        if not right or not re.fullmatch(header.format(group=r'(?:SIDs|SID\(s\))', right='"' + right + '" user right')
                                         + r'[.:]', core_header, re.I):
            return None
        if "must deny" in header and not right.startswith("SeDeny"):
            return None
        names = []
        for line in gui_list.splitlines():
            entry = re.fullmatch(r'- ([A-Za-z ]+?)(?: Group)?\.?', line)
            if not entry or entry[1] not in _SIDS or "domain-" in _SIDS[entry[1]]:
                return None
            names.append(entry[1])
        if (not names) != (direction == "empty") or (application and direction != "subset"):
            return None
        if sorted(core_list.splitlines()) == sorted(_SIDS[n] + " (" + n + ")" for n in names):
            key = None
            if application:
                key = "approved_" + re.sub(r'(?<!^)(?=[A-Z])', '_', right).lower() + "_accounts"
            return right, sorted(set(names)), direction, key, auditors


def _user_right(check, values):
    parsed = _right_members(check)
    if not parsed:
        return None
    right, names, direction, key, auditors = parsed
    extra = []
    if key:
        extra = values.get(key) if isinstance(values, dict) else None
        if not isinstance(extra, (list, tuple)) or not all(
                isinstance(n, str) and re.fullmatch(r"[\w.'$-]+(?: [\w.'$-]+)*(?:\\[\w.'$-]+(?: [\w.'$-]+)*)?", n)
                for n in extra):
            why = ("List application accounts whose need for this right has vendor support and ISSO documentation "
                   "and whose passwords meet the rule's requirements.")
            if "protected as highly privileged accounts" in check:
                why += " Verify that their passwords are protected as highly privileged accounts."
            if auditors:
                why += " Include the organization's Auditors group if it exists."
            return {"fixed": True, "kind": "needs", "needs": key, "example": {key: ["ExampleAccount"]},
                    "why": why + " Use names or full SIDs as exported. Supply an empty list when none qualify."}
    body = ["$right = " + _q(right), "$f = $env:SITE_AK_TEMP_INF",
             r"if (-not ($f -and $f -match '^(?:[a-z]:[\\/]|/)(?:[^\\/:]+[\\/])*site-ak-[^\\/:]*[\\/][^\\/:]+\.inf\z' "
             "-and -not (Test-Path -LiteralPath $f -ErrorAction Stop) "
             "-and -not (Test-Path -LiteralPath ($f + '.log') -ErrorAction Stop))) {",
             "    $f = Join-Path $env:TEMP ('site-ak-' + [guid]::NewGuid().ToString('N') + '.inf')", "}",
             "$l = $f + '.log'",
             "try {", "    $null = $(", "        " + _EXPORT, "    )",
             "    if ($LASTEXITCODE -ne 0) { return ($false) }",
             "    $lines = @(Get-Content -LiteralPath $f -ErrorAction Stop)",
             "    $members = ''", "    $section = ''", "    $found = $false", "    $policy = $false",
             "    foreach ($line in $lines) {", "        $line = $line -replace '^\\s+|\\s+$', ''",
             "        if ($line -match '^\\[') { $section = $line }",
             r"        if (-not $section -and $line -match '^Se[A-Za-z]+\s*=') { $policy = $true }",
             "        if ($section -and $section -ne '[Privilege Rights]') { continue }",
             "        if ($section -eq '[Privilege Rights]') { $policy = $true }",
             r"        if ($line -match ('^' + $right + '\b')) {",
             r"            if ($found -or $line -notmatch ('^' + $right + '\s*=\s*(.*)$')) { return ($false) }",
             "            $members = $line -replace ('^' + $right + '\\s*=\\s*'), ''", "            $found = $true", "        }", "    }",
             "    Write-Verbose ('read: ' + $right + ' = ' + $members)", "    if (-not $policy) { return ($false) }"]
    cleanup = ["} finally {", "    " + _REMOVE, "    " + _REMOVE_LOG, "}"]
    if not names:
        return _pattern(KINDS[2], right, body + cleanup, "$members -eq ''", right)
    if direction != "subset":
        body.append("    $seen = @()")
    if extra:
        body.append("    $approved = @(" + ", ".join(map(_q, sorted(set(extra)))) + ")")
    body += ["    if ($members) {", "        foreach ($entry in ($members -split ',')) {",
             "            $value = $entry -replace '^\\s+|\\s+$', ''", "            $sid = $null",
             "            if (-not $value) { return ($false) }"]
    if extra:
        body.append(r"            if ($approved -contains ($value -replace '^\*(?=S-1-)', '')) { continue }")
    for name in names:
        sid = _SIDS[name]
        aliases = [name]
        if sid.startswith("S-1-5-32-"):
            aliases.append("BUILTIN\\" + name)
        if name in ("Local Service", "Network Service", "Service", "Authenticated Users"):
            aliases.append("NT AUTHORITY\\" + name)
        aliases += [sid, "*" + sid]
        test = "$value -in @(" + ", ".join(map(_q, aliases)) + ")"
        body.append("            if (" + test + ") { $sid = " + _q(sid) + " }")
    if direction != "superset":
        body.append("            if (-not $sid) { return ($false) }")
    if direction != "subset":
        body.append("            if ($sid -and $seen -notcontains $sid) { $seen += $sid }")
    body += ["        }", "    }"]
    comparison = "$true" if direction == "subset" else "$seen.Count -eq " + str(len(names))
    return _pattern(KINDS[2], right, body + cleanup, comparison, right)


_DOCUMENTED = (
    "Required roles and features will vary based on the function of the individual system.\n"
    "Roles and features specifically required to be disabled per the STIG are identified in separate requirements.\n"
)
_INVENTORY = 'Get-WindowsFeature | Where-Object {$_. installstate -eq "installed"} | Format-List Displayname, Name, Featuretype'
_DOCUMENTATION_CHECKS = (
    _DOCUMENTED + "If the organization has not documented the roles and features required for the system(s), "
    'this is a finding.\nThe PowerShell command "' + _INVENTORY.replace('"installed"', '"Installed"') +
    '" will list all roles and features with an "Installed" state.',
    _DOCUMENTED + "Run the following Powershell command to determine installed features:\n" + _INVENTORY +
    "\nIf the organization has not documented the roles and features displayed as required for the system, this is a finding.",
)


def _documented_features(values):
    key = "documented_required_features"
    names = values.get(key) if isinstance(values, dict) else None
    if not isinstance(names, (list, tuple)) or not names or not all(
            isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", n) for n in names):
        return {"fixed": True, "kind": "needs", "needs": key, "example": {key: ["Example-Feature"]},
                "why": "Names of all roles and features documented as required for this system."}
    names = sorted(set(names))
    body = ["$required = @(" + ", ".join(_q(n) for n in names) + ")",
            "$features = @(Get-WindowsFeature -ErrorAction Stop)",
            "if ($features.Count -eq 0) { return ($false) }", "$documented = $true",
            "foreach ($feature in $features) {",
            "    Write-Verbose ('read: ' + $feature.Name + ' = ' + $feature.InstallState)",
            "    if (-not $feature.Name -or $feature.Installed -isnot [bool]) { return ($false) }",
            "    if ($feature.Installed -and $required -notcontains $feature.Name) { $documented = $false }", "}"]
    return _pattern(KINDS[3], "Get-WindowsFeature", body, "$documented", "Documented feature inventory", {key: names})


_FEATURE = (
    r'Open (?:"PowerShell"|PowerShell)\.\nEnter "Get-WindowsFeature (?:\| Where Name -eq |-Name )'
    r"([A-Za-z0-9][A-Za-z0-9-]*)" + r'"\.\nIf "Installed State" is "Installed", this is a finding\.\n'
    r'An Installed State of "Available" or "Removed" is not a finding\.'
)


def _feature(check):
    match = re.fullmatch(_FEATURE, check)
    if not match:
        return None
    name = match[1]
    body = ["$name = " + _q(name), "$rows = @(Get-WindowsFeature -Name $name -ErrorAction Stop)",
            "if ($rows.Count -ne 1 -or $rows[0].Name -ne $name) { return ($false) }",
            "$feature = $rows[0]", "Write-Verbose ('read: ' + $name + ' = ' + $feature.InstallState)"]
    return _pattern(KINDS[3], "Get-WindowsFeature -Name " + name, body,
                    "$feature.Installed -is [bool] -and -not $feature.Installed "
                    "-and $feature.InstallState -in @('Available', 'Removed')", name)


def draft(rule, values=None):
    """Match complete check texts. Unmodelled conditions stay open for review."""
    check = (rule or {}).get("check")
    if not isinstance(check, str):
        return None
    check = "\n".join(line.strip() for line in check.splitlines() if line.strip())
    if re.search(r"^This applies to (?:domain controllers|member servers)\b", check, re.I | re.M):
        return {"kind": "role_scoped"}
    if check in _DOCUMENTATION_CHECKS:
        return _documented_features(values)
    kind = classify(check)
    if kind == KINDS[2]:
        return _user_right(check, values)
    parser = {KINDS[0]: _registry, KINDS[1]: _audit, KINDS[3]: _feature}.get(kind)
    if parser:
        return parser(check)
    return None
