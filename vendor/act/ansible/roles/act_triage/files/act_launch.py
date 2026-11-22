#!/usr/bin/env python3
"""Read ACT's keys and settings from stdin and run ACT with the evidence on stdin."""
import json
import os
import re
import signal
import socket
import sys
import time

ACT_KEYS = ("GENAI_KEY", "GENAI_KEYS", "ASKSAGE_KEY", "ASKSAGE_API_KEY", "ASKSAGE_KEYS", "GENAI_BETA_KEY", "GENAI_BETA_KEYS")
ACT_NOT_SECRETS = ("ACT_KEY_COOLDOWN", "ACT_KEY_ROTATION", "ACT_KEY_USAGE_LOG", "ACT_MAX_TOKENS", "ACT_TOKEN_PARAM")
BASE = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ", "TMPDIR",
        "TERM", "NO_COLOR", "XDG_CONFIG_HOME", "PYTHONIOENCODING", "PYTHONUTF8", "http_proxy", "https_proxy",
        "no_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE",
        "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
PREFIXES = ("ACT_", "GENAI_", "ASKSAGE_")
CREDENTIAL = re.compile(r"PASSW|PASSPHRASE|SECRET|APIKEY|CREDENTIAL|COOKIE|PRIVATE|TOKEN|AUTH|SESSION|BEARER|(^|_)(KEYS?|PASS|PWD|PW)(_|$)", re.I)


def refuse(args, error=None):
    message = "act_launch: refused the launcher input"
    if error is not None:
        message += " (%s)" % type(error).__name__
    print(message, file=sys.stderr)
    if "--result-file" in args[:-1]:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with open(args[args.index("--result-file") + 1], "w") as f:
                json.dump({"schema": "act.result/1", "act_version": "", "platform": sys.platform, "host": socket.gethostname(),
                           "user": "", "cwd": os.getcwd(), "task": "", "provider": "", "model": "", "started_at": stamp,
                           "finished_at": stamp, "duration_s": 0, "exit_code": 2, "status": "error", "summary": "",
                           "stop_reason": message, "changed": False, "commands": [], "denied": [], "files_changed": [],
                           "pre_approved_patterns": [], "race": None, "tokens": None, "model_retries": 0}, f)
        except OSError as write_error:
            print("act_launch: could not write the result (%s)" % type(write_error).__name__, file=sys.stderr)
    return 2


def main():
    if len(sys.argv) < 7:
        return refuse([])
    evidence, keep, host_keys, limit = sys.argv[1:5]
    argv = sys.argv[5:]
    allowed = set(BASE).union(keep.split())

    def setting(name):
        return ((name in allowed or name.startswith(PREFIXES))
                and (name in ACT_NOT_SECRETS or not CREDENTIAL.search(name)))

    if host_keys not in ("0", "1"):
        return refuse(argv[2:])
    env = {name: value for name, value in os.environ.items() if name != "ACT_ENV_KEEP" and setting(name)}
    if host_keys == "1":
        env.update((name, os.environ[name]) for name in ACT_KEYS if name in os.environ)

    seen = set()
    remaining = 64 * 1024
    while True:
        line = sys.stdin.buffer.readline(remaining + 1)
        if not line:
            break
        remaining -= len(line)
        if remaining < 0 or len(seen) >= 200:
            return refuse(argv[2:])
        if line.endswith(b"\n"):
            line = line[:-2] if line.endswith(b"\r\n") else line[:-1]
        if not line:
            break
        raw_name, sep, value = line.partition(b"=")
        name = os.fsdecode(raw_name)
        if (not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in seen
                or (name not in ACT_KEYS and not setting(name)) or b"\0" in value):
            return refuse(argv[2:])
        seen.add(name)
        env[name] = os.fsdecode(value)

    try:
        limit = int(limit)
        if limit < 1:
            return refuse(argv[2:])
        with open(evidence, "rb") as f:
            os.dup2(f.fileno(), 0)
        signal.alarm(limit)
        os.execve(argv[0], argv, env)
    except (OSError, OverflowError, ValueError) as error:
        signal.alarm(0)
        return refuse(argv[2:], error)


if __name__ == "__main__":
    sys.exit(main())
