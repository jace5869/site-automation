#!/usr/bin/env python3
"""The nginx blocks for AAP, Keycloak's card login and the report website (docs/AAP_KEYCLOAK.md, part 2).

STIG Manager's nginx - the one already running - gets more blocks. This script writes them, from your settings,
into a folder of its own (./out). You paste them by hand. It never reads or writes nginx's own file, never calls
podman or nginx, and starts no container: it only writes two text files.

  out/aap-nginx-blocks.conf   the blocks, each between "begin" and "end" lines, each saying where it goes
  out/aap-nginx-steps.txt     the steps, with your names in them: the copy, the mount, the paste, nginx -t, the reload

The blocks:
  A  AAP          a server block for AAP's own name. Always.
  B  Keycloak     what Keycloak's address needs so the card reaches the new realm (KEYCLOAK_NGINX):
                  lines   two lines to add to the Keycloak location nginx already has (the default)
                  server  a whole server block for Keycloak's name, when nginx has none
                  off     nothing: Keycloak's address already passes the card
  C  Reports      the report website's folder, served by this nginx (REPORTS), only with REPORT_WEB_FOLDER:
                  location  a location inside STIG Manager's own server block, behind its card check (the default)
                  server    a server block for a name of its own (REPORTS_URL)
                  With REPORTS_UPSTREAM in place of the folder: passed on to a web server that already serves it.

Usage:
  tools/aap-nginx-blocks.py --settings aap-keycloak.env
  tools/aap-nginx-blocks.py --settings aap-keycloak.env --out /tmp/aap-blocks

Options:
  --settings FILE   a file of NAME=value lines (tools/aap-keycloak.env.example). The environment wins over it.
  --out DIR         where the two files go. Default: ./out. Never a folder under /etc.
  --print           print the blocks and write nothing
  -h, --help        this text

Settings (the environment, or the --settings file):
@SETTINGS@
Exit code: 0 written, 2 a setting is missing or wrong.
"""
import os
import re
import sys

MARK = "site-automation docs/AAP_KEYCLOAK.md"
SETTINGS = [
    ("AAP_URL", "", "the address people type for AAP, https, no path. Required."),
    ("AAP_UPSTREAM", "", "the AAP gateway itself, e.g. https://aap01.yoursite.mil:443. Required."),
    ("NGINX_CONTAINER", "stigman-nginx", "the nginx container's name (the steps name it)"),
    ("NGINX_CONF", "/etc/nginx/conf.d/default.conf", "nginx's file as the container sees it (the steps name it)"),
    ("NGINX_LISTEN", "443 ssl", "the listen line of STIG Manager's own server block, without \"listen\" and \";\""),
    ("NGINX_TLS_DIR", "/etc/nginx/tls", "the certificate folder as the container sees it"),
    ("AAP_TLS_CERT", "aap-fullchain.pem", "AAP's certificate with its chain, a file in that folder"),
    ("AAP_TLS_KEY", "aap-key.pem", "its key, a file in that folder"),
    ("NGINX_CLIENT_CA", "", "the file of STIG Manager's ssl_client_certificate line (the CAs of people's cards), as the\n"
     "container sees it. Needed for KEYCLOAK_NGINX=server and REPORTS=server."),
    ("NGINX_VERIFY_DEPTH", "3", "ssl_verify_depth for those two blocks: how long a card's chain may be"),
    ("KEYCLOAK_URL", "", "Keycloak's address as people's browsers use it. Required."),
    ("KEYCLOAK_NGINX", "lines", "lines, server or off (see above)"),
    ("KEYCLOAK_UPSTREAM", "", "KEYCLOAK_NGINX=server: Keycloak itself, e.g. http://keycloak:8080"),
    ("KEYCLOAK_TLS_CERT", "keycloak-fullchain.pem", "KEYCLOAK_NGINX=server: the certificate for Keycloak's name"),
    ("KEYCLOAK_TLS_KEY", "keycloak-key.pem", "KEYCLOAK_NGINX=server: its key"),
    ("KEYCLOAK_VERIFY_HEADER", "off", "on: also pass nginx's verdict (ssl-client-verify), for a Keycloak started with\n"
     "trust-proxy-verification"),
    ("REPORT_WEB_FOLDER", "", "the folder report_web_dest writes to, as the STIG Manager HOST sees it,\n"
     "e.g. /srv/site-reports. Empty: no reports block."),
    ("REPORTS", "location", "location or server (see above)"),
    ("REPORTS_PATH", "/reports/", "REPORTS=location: the path under STIG Manager's name"),
    ("REPORTS_URL", "", "REPORTS=server: the address of the report website, e.g. https://reports.yoursite.mil"),
    ("REPORTS_TLS_CERT", "reports-fullchain.pem", "REPORTS=server: the certificate for that name"),
    ("REPORTS_TLS_KEY", "reports-key.pem", "REPORTS=server: its key"),
    ("REPORTS_MOUNT", "/usr/share/nginx/site-reports", "where the folder shows inside the nginx container"),
    ("REPORTS_UPSTREAM", "", "no restart: a web server that already serves the folder, e.g. http://web01.yoursite.mil:8080.\n"
     "The block passes requests on to it, and nginx needs no new mount."),
    ("STIGMAN_URL", "", "STIG Manager's own address (the steps print the report website's address from it)"),
]
DEFAULTS = dict((k, d) for k, d, _ in SETTINGS)
# the settings of tools/aap-keycloak-realm.py: one file serves both scripts
OTHER = ("KEYCLOAK_ADMIN_URL KEYCLOAK_REALM KEYCLOAK_PROTECTED_REALMS KEYCLOAK_ADMIN_REALM KEYCLOAK_ADMIN_USER "
         "KEYCLOAK_ADMIN_PASSWORD_FILE CA_FILE KEYCLOAK_TIMEOUT KEYCLOAK_CONTAINER KEYCLOAK_RUNTIME KEYCLOAK_KCADM "
         "KEYCLOAK_KCADM_SERVER KEYCLOAK_CLIENT_ID AAP_REDIRECT_URI AAP_GROUPS AAP_GROUPS_CLAIM X509_FLOW X509_IDENTITY "
         "X509_REGEX X509_USER_ATTRIBUTE X509_PASSWORD_FORM X509_CONFIRM_PAGE X509_OCSP X509_OCSP_RESPONDER X509_CRL "
         "AAP_USERS_FILE LDAP_URL LDAP_NAME LDAP_VENDOR LDAP_USERS_DN LDAP_BIND_DN LDAP_BIND_PASSWORD_FILE "
         "LDAP_USERNAME_ATTRIBUTE LDAP_STARTTLS LDAP_CA_FILE LDAP_GROUP_DN LDAP_GROUP_NESTED LDAP_CARD_ATTRIBUTE "
         "LDAP_SYNC_CHANGED_SECONDS LDAP_SYNC_FULL_SECONDS LDAP_TEAM_GROUPS LDAP_TEAM_MAPPER AAP_TOKEN_FILE CHECK_TIMEOUT "
         "CARD_CA_FILE CHECK_USER").split()


def settings_help():
    out = []
    for k, d, h in SETTINGS:
        first, _, rest = h.partition("\n")
        out.append("  %-24s %s%s" % (k, first, (" Default: %s" % d) if d else ""))
        out += ["  %-24s %s" % ("", line) for line in rest.split("\n") if line]
    return "\n".join(out) + "\n"


class Stop(Exception):
    pass


def read_settings(path, env):
    s = dict(DEFAULTS)
    if path:
        try:
            with open(path) as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    k, eq, v = line.partition("=")
                    k = k.strip()
                    if k.startswith("export "):
                        k = k[7:].strip()
                    if not eq or (k not in DEFAULTS and k not in OTHER):
                        raise Stop("%s line %d: not a setting: %s" % (path, n, k))
                    v = v.strip()
                    if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"":
                        v = v[1:-1]
                    if k in DEFAULTS:
                        s[k] = v
        except (IOError, OSError) as e:
            raise Stop("The settings file cannot be read: %s" % e)
    for k in DEFAULTS:
        if env.get(k, "") != "":
            s[k] = env[k]
    for k in ("AAP_URL", "AAP_UPSTREAM", "KEYCLOAK_URL", "KEYCLOAK_UPSTREAM", "REPORTS_URL", "REPORTS_UPSTREAM", "STIGMAN_URL"):
        s[k] = s[k].rstrip("/")
    return s


SAFE = re.compile(r"^[A-Za-z0-9._/:@\[\]-]+$")          # nothing that could end an nginx line early


def check_settings(s):
    def bad(text):
        raise Stop(text + " (see --help)")
    for k, v in s.items():
        if v and k != "NGINX_LISTEN" and not SAFE.match(v):
            bad("%s=%s has a character that does not belong in an address, a name or a path" % (k, v))
    if not re.match(r"^[A-Za-z0-9.:\[\]_ =-]+$", s["NGINX_LISTEN"]):
        bad("NGINX_LISTEN is the listen line without \"listen\" and \";\", e.g. 443 ssl")
    for k in ("AAP_URL", "KEYCLOAK_URL"):
        if not s[k]:
            bad("Set %s" % k)
    if not re.match(r"^https://[^/]+$", s["AAP_URL"]):
        bad("AAP_URL must be https:// and a name with no path: AAP does not work under /something/")
    if not re.match(r"^https://[^/]+", s["KEYCLOAK_URL"]):
        bad("KEYCLOAK_URL must start with https://")
    if not re.match(r"^https?://[^/]+$", s["AAP_UPSTREAM"]):
        bad("Set AAP_UPSTREAM to the gateway's own address, e.g. https://aap01.yoursite.mil:443, with no path")
    if host(s["AAP_UPSTREAM"]) == host(s["AAP_URL"]):
        bad("AAP_UPSTREAM is the AAP host itself, not the name people type (AAP_URL): nginx would call itself")
    if s["KEYCLOAK_NGINX"] not in ("lines", "server", "off"):
        bad("KEYCLOAK_NGINX must be lines, server or off")
    if s["KEYCLOAK_NGINX"] == "server":
        if not re.match(r"^https?://[^/]+$", s["KEYCLOAK_UPSTREAM"]):
            bad("KEYCLOAK_NGINX=server needs KEYCLOAK_UPSTREAM, e.g. http://keycloak:8080")
        if not s["NGINX_CLIENT_CA"]:
            bad("KEYCLOAK_NGINX=server needs NGINX_CLIENT_CA: the file of STIG Manager's ssl_client_certificate line")
    if s["REPORTS"] not in ("location", "server"):
        bad("REPORTS must be location or server")
    if s["REPORT_WEB_FOLDER"] and s["REPORTS_UPSTREAM"]:
        bad("Set REPORT_WEB_FOLDER (nginx serves the folder) or REPORTS_UPSTREAM (another web server does), not both")
    if s["REPORT_WEB_FOLDER"] and not s["REPORT_WEB_FOLDER"].startswith("/"):
        bad("REPORT_WEB_FOLDER is a folder on the STIG Manager host and starts with /. An address or a \\\\server\\share "
            "cannot be mounted in the container: mount the share on the host first")
    if s["REPORTS_UPSTREAM"] and not re.match(r"^https?://[^/]+$", s["REPORTS_UPSTREAM"]):
        bad("REPORTS_UPSTREAM must be http:// or https:// and a name, with no path")
    if reports(s):
        if s["REPORTS"] == "location" and not re.match(r"^/[A-Za-z0-9._-]+/$", s["REPORTS_PATH"]):
            bad("REPORTS_PATH must be one folder name between two /, e.g. /reports/")
        if s["REPORTS"] == "server":
            if not re.match(r"^https://[^/]+$", s["REPORTS_URL"]):
                bad("REPORTS=server needs REPORTS_URL, e.g. https://reports.yoursite.mil")
            if not s["NGINX_CLIENT_CA"]:
                bad("REPORTS=server needs NGINX_CLIENT_CA, so the report website asks for the card as STIG Manager does")
        if not s["REPORTS_MOUNT"].startswith("/") or s["REPORTS_MOUNT"].rstrip("/") in ("", "/etc/nginx", s["NGINX_TLS_DIR"].rstrip("/")):
            bad("REPORTS_MOUNT must be a folder of its own inside the container")


def host(url):
    return re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]


def reports(s):
    return bool(s["REPORT_WEB_FOLDER"] or s["REPORTS_UPSTREAM"])


def tls(s, cert, key):
    d = s["NGINX_TLS_DIR"].rstrip("/")
    return ["    ssl_certificate     %s/%s;" % (d, s[cert]), "    ssl_certificate_key %s/%s;" % (d, s[key]),
            "    ssl_protocols       TLSv1.2 TLSv1.3;"]


def block_aap(s):
    return [
        "# --- AAP: begin (%s) ---" % MARK,
        "# Where: at the very END of nginx's file, after the last }. Never inside STIG Manager's server block.",
        "map $http_upgrade $connection_upgrade {",
        "    default upgrade;",
        "    ''      close;",
        "}",
        "server {",
        "    listen %s;" % s["NGINX_LISTEN"],
        "    server_name %s;" % host(s["AAP_URL"]),
        ""] + tls(s, "AAP_TLS_CERT", "AAP_TLS_KEY") + [
        "    ssl_verify_client off;                 # no card asked here: Keycloak checks the card",
        "",
        "    client_max_body_size 200m;             # collections and images sent to automation hub",
        "",
        "    location / {",
        "        proxy_pass %s;" % s["AAP_UPSTREAM"],
        "        proxy_http_version 1.1;",
        "        proxy_ssl_server_name on;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Proto https;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header Upgrade $http_upgrade;",
        "        proxy_set_header Connection $connection_upgrade;",
        "        proxy_read_timeout 600s;",
        "        proxy_send_timeout 600s;",
        "    }",
        "}",
        "# --- AAP: end ---"]


def card_headers(s, pad):
    out = [pad + "proxy_set_header ssl-client-cert $ssl_client_escaped_cert;   # the card's certificate, for Keycloak"]
    if s["KEYCLOAK_VERIFY_HEADER"].lower() in ("on", "true", "yes", "1"):
        out.append(pad + "proxy_set_header ssl-client-verify $ssl_client_verify;      # nginx's verdict on it")
    return out


def block_keycloak(s):
    if s["KEYCLOAK_NGINX"] == "off":
        return []
    if s["KEYCLOAK_NGINX"] == "lines":
        return [
            "# --- AAP, Keycloak's card: begin (%s) ---" % MARK,
            "# Where: INSIDE the location that already passes requests on to Keycloak (the one whose proxy_pass names",
            "# Keycloak), beside its other proxy_set_header lines. A line that is already there: leave it, do not add it twice.",
            "# The server block around that location must ask for the card without demanding it:",
            "#     ssl_verify_client optional;",
            "# It says \"on\"? Then AAP, which calls Keycloak with no card to finish a login, is refused: see the guide, step 2.6."
        ] + card_headers(s, "") + ["# --- AAP, Keycloak's card: end ---"]
    return [
        "# --- AAP, Keycloak: begin (%s) ---" % MARK,
        "# Where: at the very END of nginx's file, after the last }.",
        "server {",
        "    listen %s;" % s["NGINX_LISTEN"],
        "    server_name %s;" % host(s["KEYCLOAK_URL"]),
        ""] + tls(s, "KEYCLOAK_TLS_CERT", "KEYCLOAK_TLS_KEY") + [
        "    ssl_client_certificate %s;" % s["NGINX_CLIENT_CA"],
        "    ssl_verify_client optional;            # the card is asked for, not demanded: AAP calls Keycloak with none",
        "    ssl_verify_depth %s;" % s["NGINX_VERIFY_DEPTH"],
        "",
        "    location / {",
        "        proxy_pass %s;" % s["KEYCLOAK_UPSTREAM"],
        "        proxy_http_version 1.1;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Proto https;",
        "        proxy_set_header X-Forwarded-For $remote_addr;",
        "        proxy_buffer_size 16k;                 # a card's certificate makes a long header",
        "        proxy_buffers 8 16k;"] + card_headers(s, "        ") + [
        "    }",
        "}",
        "# --- AAP, Keycloak: end ---"]


def reports_inner(s, pad):
    if s["REPORTS_UPSTREAM"]:
        return [pad + "proxy_pass %s/;" % s["REPORTS_UPSTREAM"],
                pad + "proxy_http_version 1.1;",
                pad + "proxy_set_header Host %s;" % re.sub(r"^https?://", "", s["REPORTS_UPSTREAM"]),
                pad + "proxy_set_header X-Forwarded-Proto https;",
                pad + "proxy_set_header X-Forwarded-For $remote_addr;",
                pad + "limit_except GET HEAD { deny all; }   # people only read here"]
    return [pad + "index index.html;",
            pad + "limit_except GET HEAD { deny all; }   # people only read here; the jobs write the folder, not nginx",
            pad + "add_header Cache-Control \"no-cache\";    # a new run's page shows at once",
            pad + "add_header X-Content-Type-Options nosniff;"]


def block_reports(s):
    if not reports(s):
        return []
    mount = s["REPORTS_MOUNT"].rstrip("/")
    if s["REPORTS"] == "location":
        p = s["REPORTS_PATH"]
        out = ["# --- site-automation reports: begin (%s) ---" % MARK,
               "# Where: INSIDE STIG Manager's own server block, above its \"location / {\" line. It then sits behind the",
               "# same certificate and the same card check as STIG Manager.",
               "location = %s { return 301 %s; }" % (p.rstrip("/"), p),
               "location %s {" % p]
        if not s["REPORTS_UPSTREAM"]:
            out.append("    alias %s/;" % mount)
        return out + reports_inner(s, "    ") + ["}", "# --- site-automation reports: end ---"]
    out = ["# --- site-automation reports: begin (%s) ---" % MARK,
           "# Where: at the very END of nginx's file, after the last }.",
           "server {",
           "    listen %s;" % s["NGINX_LISTEN"],
           "    server_name %s;" % host(s["REPORTS_URL"]),
           ""] + tls(s, "REPORTS_TLS_CERT", "REPORTS_TLS_KEY") + [
           "    ssl_client_certificate %s;" % s["NGINX_CLIENT_CA"],
           "    ssl_verify_client on;                  # the card, as at STIG Manager",
           "    ssl_verify_depth %s;" % s["NGINX_VERIFY_DEPTH"],
           "    server_tokens off;",
           ""]
    if not s["REPORTS_UPSTREAM"]:
        out.append("    root %s;" % mount)
    return out + ["    location / {"] + reports_inner(s, "        ") + ["    }", "}", "# --- site-automation reports: end ---"]


def blocks_text(s):
    parts = [block_aap(s), block_keycloak(s), block_reports(s)]
    head = ["# Made by tools/aap-nginx-blocks.py (%s)." % MARK,
            "# Paste each part where its \"Where:\" line says. This file itself is never read by nginx.",
            ""]
    return "\n".join(head + ["\n".join(p) + "\n" for p in parts if p])


def reports_address(s):
    if s["REPORTS"] == "server":
        return s["REPORTS_URL"]
    return (s["STIGMAN_URL"] or "https://<STIG Manager's name>") + s["REPORTS_PATH"].rstrip("/")


def steps_text(s):
    c, conf = s["NGINX_CONTAINER"], s["NGINX_CONF"]
    mount = s["REPORTS_MOUNT"].rstrip("/")
    folder = s["REPORT_WEB_FOLDER"].rstrip("/")
    n = [0]
    out = ["The nginx steps, with your names (%s, part 2 has the reasons)." % MARK,
           "On the STIG Manager host. <the file> is nginx's file on the host: the left side of the line that",
           "ends in .conf in",
           "    sudo podman inspect %s --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'" % c,
           ""]

    def step(title, lines):
        n[0] += 1
        out.append("%d. %s" % (n[0], title))
        out.extend(("   " + line).rstrip() for line in lines)
        out.append("")
    step("Keep a copy of nginx's file.", ["    sudo cp <the file> <the file>.before-aap"])
    certs = ["%s and %s (AAP)" % (s["AAP_TLS_CERT"], s["AAP_TLS_KEY"])]
    if s["KEYCLOAK_NGINX"] == "server":
        certs.append("%s and %s (Keycloak)" % (s["KEYCLOAK_TLS_CERT"], s["KEYCLOAK_TLS_KEY"]))
    if reports(s) and s["REPORTS"] == "server":
        certs.append("%s and %s (reports)" % (s["REPORTS_TLS_CERT"], s["REPORTS_TLS_KEY"]))
    step("Put the certificates in the certificate folder (the left side of the line that ends in %s). Use cp, not mv."
         % s["NGINX_TLS_DIR"], ["Files: " + "; ".join(certs) + ".",
                                "    sudo podman exec %s ls %s" % (c, s["NGINX_TLS_DIR"]),
                                "You should see each file in the list."])
    if folder:
        step("THE MOUNT - THIS RESTARTS STIG MANAGER'S NGINX. People lose STIG Manager and AAP for some seconds. "
             "Do it in a quiet hour.",
             ["A running container cannot be given a folder: the mount is added to how the container starts, and the",
              "container is started again. People's logins survive it (they live in Keycloak and the browser).",
              "",
              "nginx runs from a systemd unit (a quadlet, /etc/containers/systemd/%s.container): add this line under" % c,
              "[Container], below the other Volume= lines:",
              "    Volume=%s:%s:ro,z" % (folder, mount),
              "then:",
              "    sudo systemctl daemon-reload",
              "    sudo systemctl restart %s.service" % c,
              "",
              "nginx runs from a compose file: add this line under the nginx service's \"volumes:\", then start that one",
              "service again, in the compose file's folder:",
              "    - %s:%s:ro,z" % (folder, mount),
              "    sudo podman-compose up -d --force-recreate --no-deps <the nginx service>",
              "",
              "(\",z\" lets the container read the folder under SELinux. On a folder that is an NFS or CIFS mount leave",
              "\",z\" out - write \":ro\" - and allow it once with: sudo setsebool -P virt_use_nfs 1, or virt_use_samba 1.)",
              "Check:",
              "    sudo podman exec %s ls %s" % (c, mount),
              "You should see the report website's files (index.html once a report has run).",
              "No restart at all is possible only when another web server already serves the folder: set",
              "REPORTS_UPSTREAM to it (and no REPORT_WEB_FOLDER), run this script again, and skip this step."])
    step("Paste the blocks of aap-nginx-blocks.conf into <the file>, each where its \"Where:\" line says.",
         ["Edit the file IN PLACE: sudo nano <the file> does; in vim first  :set backupcopy=yes",
          "(the container sees that one file: an editor that saves a new file in its place leaves nginx reading the old one)."])
    step("Check the container sees the lines. This must print 1:",
         ["    sudo podman exec %s grep -c 'server_name %s' %s" % (c, host(s["AAP_URL"]), conf),
          "A 0: the editor saved a new file. Put the copy back (sudo cp <the file>.before-aap <the file>) and edit again."])
    step("Test it. Nothing is loaded yet.",
         ["    sudo podman exec %s nginx -t" % c,
          "You should see \"syntax is ok\" and \"test is successful\". Anything else: put the copy back, nothing has changed."])
    step("Load it. nginx reloads without dropping anyone.", ["    sudo podman exec %s nginx -s reload" % c])
    checks = ["    curl -s -o /dev/null -w '%%{http_code}\\n' %s/api/gateway/v1/ping/" % s["AAP_URL"],
              "You should see 200. Then open STIG Manager in the browser: it works as before, and still asks for the card."]
    if reports(s):
        checks += ["The report website, in the browser, with your card:  %s/" % reports_address(s),
                   "In the report jobs set  report_web_url: %s  so the emails link to it." % reports_address(s)]
    step("Check.", checks)
    out += ["To take it all out again: delete everything between each \"begin\" and \"end\" line (or put the copy back),",
            "then steps %d and %d again.%s" % (n[0] - 2, n[0] - 1,
                                             " The Volume line may stay; removing it is one more restart." if folder else "")]
    return "\n".join(out) + "\n"


def run(argv, env, out):
    settings, outdir, show = "", "out", False
    args = list(argv)
    while args:
        a = args.pop(0)
        if a in ("-h", "--help"):
            out(__doc__.replace("@SETTINGS@\n", settings_help()).rstrip())
            return 0
        elif a == "--print":
            show = True
        elif a in ("--settings", "--out"):
            if not args:
                raise Stop("%s needs a value (see --help)" % a)
            if a == "--settings":
                settings = args.pop(0)
            else:
                outdir = args.pop(0)
        else:
            raise Stop("Not an option: %s (see --help)" % a)
    s = read_settings(settings, env)
    check_settings(s)
    text, steps = blocks_text(s), steps_text(s)
    if show:
        out(text.rstrip())
        return 0
    real = os.path.realpath(outdir)
    if real == "/etc" or real.startswith("/etc/") or "nginx" in real.split(os.sep):
        raise Stop("--out %s: this script never writes where nginx's own files live. Pick a folder of your own." % outdir)
    os.makedirs(real, exist_ok=True)
    for name, body in (("aap-nginx-blocks.conf", text), ("aap-nginx-steps.txt", steps)):
        path = os.path.join(real, name)
        if os.path.exists(path):
            with open(path) as f:
                if MARK not in f.read(4096):
                    raise Stop("%s is there and this script did not write it. It is left alone: pick another --out." % path)
        with open(path, "w") as f:
            f.write(body)
        out("wrote %s" % path)
    out("")
    out("Nothing else was touched. Next: %s, top to bottom." % os.path.join(outdir, "aap-nginx-steps.txt"))
    return 0


def main():
    try:
        return run(sys.argv[1:], os.environ, print)
    except Stop as e:
        sys.stderr.write("%s\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
