#!/usr/bin/env python3
"""Read ACT's keys and settings from stdin and run ACT with the evidence on stdin.

stdin: the line "act_launch/1", then one JSON object {NAME: value, ...} and nothing after it (whitespace only).
Anything else - cut short, a second object, a value that is not a string - is refused: ACT does not start.
"""
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
        "TERM", "NO_COLOR", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "PYTHONIOENCODING", "PYTHONUTF8", "http_proxy", "https_proxy",
        "no_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE",
        "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
PREFIXES = ("ACT_", "GENAI_", "ASKSAGE_")
HEADER = b"act_launch/1\n"
MAX_INPUT = 1024 * 1024
MAX_NAMES = 200
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CREDENTIAL = re.compile(r"PASSW|PASSPHRASE|SECRET|APIKEY|CREDENTIAL|COOKIE|PRIVATE|TOKEN|AUTH|SESSION|BEARER|(^|_)(KEYS?|PASS|PWD|PW)(_|$)", re.I)


class Pairs(list):
    """A JSON object's NAME/value pairs, in order (a repeated name stays visible)."""


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
                           "pre_approved_patterns": [], "race": None, "tokens": None, "model_retries": 0,
                           "delegations": []}, f)
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
    # site-automation 1.137.0: a job's ACT does not hand work to ACT 0.6.28's sub-agents (delegate) - the jobs pick their
    # own models (site_act_worker_model). The settings below (site_act_env, act_triage_env) may turn it on again.
    env["ACT_DELEGATE"] = "0"

    # 1.104.0 (Codex): one framed JSON object, read whole: a payload cut short never starts ACT with part of it
    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT or not data.startswith(HEADER):
        return refuse(argv[2:])
    try:
        pairs = json.loads(data[len(HEADER):].decode("utf-8"), object_pairs_hook=Pairs)
    except (ValueError, RecursionError):      # not UTF-8, not JSON, cut short, text after the object, deep nesting
        return refuse(argv[2:])
    if not isinstance(pairs, Pairs) or len(pairs) > MAX_NAMES:
        return refuse(argv[2:])
    seen = set()
    for name, value in pairs:
        if (not NAME.fullmatch(name) or name in seen or (name not in ACT_KEYS and not setting(name))
                or not isinstance(value, str) or "\0" in value):
            return refuse(argv[2:])
        seen.add(name)
        env[name] = value

    try:
        limit = int(limit)
        if limit < 1:
            return refuse(argv[2:])
        with open(evidence, "rb") as f:
            os.dup2(f.fileno(), 0)
        signal.alarm(limit)
        os.execve(argv[0], argv, env)
    except (OSError, OverflowError, ValueError) as error:   # E2BIG, a lone surrogate ...
        signal.alarm(0)
        return refuse(argv[2:], error)


if __name__ == "__main__":
    sys.exit(main())
