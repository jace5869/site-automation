#!/usr/bin/env bash
# report-web-setup.sh - the report website's own small web server (nginx) for site-automation, in a rootful podman
# container on the STIG Manager host, read and written through STIG Manager's nginx (https://<that server>/reports/).
# The report jobs upload the pages there (HTTP PUT, WebDAV) with a login; anyone may read them. The pages' own scripts
# (site-report.js, site-insights.js) are served with the right types and allowed by the website's own security header,
# so search, sort, CSV and the insights' Custom view work.
#
# What it does, in order (safe to run again: each step checks first and skips what is already there):
#   1. checks; says what it reads of STIG Manager's nginx container and which podman network report-web joins
#   2. the image: the one STIG Manager's nginx already runs (nothing to pull), else a pull (TLS verification off)
#   3. the folders, the upload login (a root-only file) and report-web's own nginx configuration
#   4. starts report-web on that network (no port on the host), or on a port of this host (PUBLISH_PORT)
#   5. makes it start at boot (a Quadlet unit on podman 4.4+, else a generated systemd unit)
#   6. the firewall: nothing by default; only with PUBLISH_PORT and AAP_SOURCES (direct uploads)
#   7. writes the lines to paste into STIG Manager's nginx (PASTE_FILE) - it never changes that nginx itself
#   8. prints what to put in all.yml and in AAP's "Report website" credential
#
# STIG Manager's nginx container is read, never changed or restarted:
#   - its podman network: report-web joins it from outside (podman run --network), so nginx reaches report-web by name.
#     NETWORK=<name> names it by hand; PUBLISH_PORT=<port> joins no network at all.
#   - its running configuration (podman exec <it> nginx -T, read in memory, never printed or kept): only its add_header
#     lines are taken, to repeat them in the pasted block. SKIP_READ_CONFIG=1 reads nothing; the paste file then has a
#     marked place for them.
#
# Usage, as root:
#   SERVER_NAME=stigman.yoursite.mil bash report-web-setup.sh
# Settings (environment variables, all optional):
#   SERVER_NAME      the name people open STIG Manager at (for the addresses printed)  (default: this host's name)
#   NGINX_CONTAINER  STIG Manager's nginx container      (default: found - the running container of an nginx image)
#   NETWORK          the podman network report-web joins (default: that container's network)
#   PUBLISH_PORT     a port on this host instead of the network (e.g. 8085); nginx then reaches HOST_ADDR:PORT
#   HOST_ADDR        the address nginx reaches this host at with PUBLISH_PORT (default: this host's first address)
#   AAP_SOURCES      with PUBLISH_PORT: AAP execution nodes that may upload to the port directly (plain HTTP!)
#   IMAGE            the nginx image                     (default: STIG Manager's nginx image, else docker.io/library/nginx:stable)
#   NAME             the container's name                (default report-web)
#   URL_PATH         the address's path                  (default /reports)
#   DATA_DIR         where the pages live                (default /var/lib/report-web)
#   CONF_DIR         report-web's nginx configuration    (default /etc/report-web)
#   ENV_FILE         the upload login                    (default /root/report-web.env, readable by root only)
#   UPLOAD_USER      the upload login's user name        (default aap-reports)
#   MAX_BODY         the largest file an upload may be   (default 64m; the biggest page, a ticket insights All page, ~2 MB)
#   PASTE_FILE       the lines to paste into STIG Manager's nginx (default /root/report-web-nginx.conf)
#   SKIP_READ_CONFIG=1 / SKIP_SYSTEMD=1 / SKIP_FIREWALL=1   leave those steps out
set -euo pipefail
# the files this script writes are read by the container's nginx: a STIG host's root umask (077) would hide them
umask 022

NAME="${NAME:-report-web}"
URL_PATH="/${URL_PATH:-/reports}"; URL_PATH="/${URL_PATH#"${URL_PATH%%[!/]*}"}"; URL_PATH="${URL_PATH%/}"
DATA_DIR="${DATA_DIR:-/var/lib/report-web}"
CONF_DIR="${CONF_DIR:-/etc/report-web}"
ENV_FILE="${ENV_FILE:-/root/report-web.env}"
UPLOAD_USER="${UPLOAD_USER:-aap-reports}"
MAX_BODY="${MAX_BODY:-64m}"
PASTE_FILE="${PASTE_FILE:-/root/report-web-nginx.conf}"
NGINX_CONTAINER="${NGINX_CONTAINER:-}"
NETWORK="${NETWORK:-}"
PUBLISH_PORT="${PUBLISH_PORT:-}"
HOST_ADDR="${HOST_ADDR:-}"
AAP_SOURCES="${AAP_SOURCES:-}"
IMAGE="${IMAGE:-}"
SERVER_NAME="${SERVER_NAME:-$(hostname -f 2>/dev/null || hostname)}"
SKIP_READ_CONFIG="${SKIP_READ_CONFIG:-0}"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"
SKIP_FIREWALL="${SKIP_FIREWALL:-0}"
CPORT=8080                                    # report-web's port inside its container

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
note() { printf '   NOTE: %s\n' "$*"; }
die()  { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }
# a file in the container's own user's hands: chown as root, or inside podman's user namespace (rootless, for the tests)
cown() { if [ "$(id -u)" -eq 0 ]; then chown "$@"; else podman unshare chown "$@"; fi; }
cmod() { if [ "$(id -u)" -eq 0 ]; then chmod "$@"; else podman unshare chmod "$@"; fi; }
# put NEW TARGET: TARGET gets NEW's text; an existing TARGET is written in place (a running container mounts that very
# file - a new one in its place would not be seen), its owner and mode kept
put() { if [ -f "$2" ]; then cat "$1" > "$2" && rm -f "$1"; else mv -f "$1" "$2"; fi; }

# ---- 1. checks --------------------------------------------------------------
say "1. Checks"
[ "$(id -u)" -eq 0 ] || [ "${ALLOW_NON_ROOT:-0}" = 1 ] || die "run as root (sudo -i): the container is rootful, like STIG Manager's"
command -v podman >/dev/null || die "podman is not installed (dnf install podman)"
command -v openssl >/dev/null || die "openssl is not installed (dnf install openssl): it makes the upload login's hash"
command -v python3 >/dev/null || die "python3 is not installed (dnf install python3): it reads nginx's add_header lines"
ok "podman $(podman --version | awk '{print $3}')"
case "$MAX_BODY" in *[!0-9kKmMgG]*|'') die "MAX_BODY is $MAX_BODY: a size nginx knows, e.g. 64m" ;; esac
if [ -n "$PUBLISH_PORT" ]; then
  [[ "$PUBLISH_PORT" =~ ^[0-9]+$ ]] || die "PUBLISH_PORT is $PUBLISH_PORT: a port number, e.g. 8085"
fi

# STIG Manager's nginx container: given, or the one running container of an nginx image (report-web left out)
if [ -z "$NGINX_CONTAINER" ]; then
  mapfile -t found < <(podman ps --format '{{.Names}} {{.Image}}' | awk -v me="$NAME" '$1 != me && $2 ~ /nginx/ {print $1}')
  if [ "${#found[@]}" -eq 1 ]; then
    NGINX_CONTAINER="${found[0]}"
  elif [ "${#found[@]}" -gt 1 ]; then
    for c in "${found[@]}"; do [ "$c" = nginx ] && NGINX_CONTAINER=nginx; done
    [ -n "$NGINX_CONTAINER" ] || die "several nginx containers run here (${found[*]}): name STIG Manager's - NGINX_CONTAINER=<name> bash $0"
  fi
fi
if [ -n "$NGINX_CONTAINER" ]; then
  podman container exists "$NGINX_CONTAINER" || die "no container $NGINX_CONTAINER here (podman ps)"
  ok "STIG Manager's nginx container: $NGINX_CONTAINER - read only, never changed, stopped or restarted. Read from it:"
  echo "        - its image and its podman network (podman inspect)"
  if [ "$SKIP_READ_CONFIG" = 1 ]; then
    echo "        - not its configuration (SKIP_READ_CONFIG=1)"
  else
    echo "        - in step 7, its add_header lines (podman exec $NGINX_CONTAINER nginx -T, read in memory: nothing else of it"
    echo "          is printed or kept). SKIP_READ_CONFIG=1 reads none."
  fi
elif [ -z "$PUBLISH_PORT$NETWORK" ]; then
  die "no running nginx container found: name STIG Manager's (NGINX_CONTAINER=<name>), or give NETWORK=<its network> or PUBLISH_PORT=<port>"
fi

# where nginx reaches report-web: on nginx's own podman network (by name), or on a port of this host
if [ -n "$PUBLISH_PORT" ]; then
  NETWORK=""
  if [ -z "$HOST_ADDR" ]; then
    HOST_ADDR=$(ip -4 route get 192.0.2.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')
    [ -n "$HOST_ADDR" ] || HOST_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -n "$HOST_ADDR" ] || die "no address of this host found: give HOST_ADDR=<this host's address>"
  fi
  UPSTREAM="http://$HOST_ADDR:$PUBLISH_PORT"
  ok "layout: a port of this host - report-web on $HOST_ADDR:$PUBLISH_PORT, joins no network"
else
  if [ -z "$NETWORK" ]; then
    NETWORK=$(podman inspect "$NGINX_CONTAINER" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
    [ -n "$NETWORK" ] || die "$NGINX_CONTAINER is on no podman network of its own: give NETWORK=<name>, or use PUBLISH_PORT=<port>"
  fi
  podman network exists "$NETWORK" || die "no podman network $NETWORK (podman network ls)"
  dns=$(podman network inspect "$NETWORK" --format '{{.DNSEnabled}}' 2>/dev/null || echo unknown)
  # the IPv4 gateway (a dual-stack network lists an IPv6 one too; the paste's resolver takes the IPv4 one)
  GATEWAY=$(podman network inspect "$NETWORK" --format '{{range .Subnets}}{{.Gateway}} {{end}}' | tr ' ' '\n' | grep -v ':' | grep -m1 . || true)
  [ -n "$GATEWAY" ] || die "network $NETWORK has no IPv4 gateway address (podman network inspect $NETWORK): use PUBLISH_PORT=<port>"
  [ "$dns" = false ] && die "network $NETWORK has no DNS (podman network inspect $NETWORK): nginx could not find report-web by name - use PUBLISH_PORT=<port>"
  UPSTREAM="http://$NAME:$CPORT"
  ok "layout: report-web will join podman network $NETWORK from outside (podman run --network): ${NGINX_CONTAINER:-the STIG Manager nginx}"
  echo "        is not changed or restarted; nginx finds report-web by name through $GATEWAY. NETWORK=<name> picks another"
  echo "        network, PUBLISH_PORT=<port> joins none."
fi

if podman container exists "$NAME"; then
  ok "container $NAME already exists - step 4 skipped"
  EXISTS=1
else
  EXISTS=0
  if [ -n "$PUBLISH_PORT" ] && ss -ltnH "( sport = :$PUBLISH_PORT )" | grep -q .; then
    die "port $PUBLISH_PORT is already in use on this host - pick another: PUBLISH_PORT=8086 bash $0"
  fi
fi

# ---- 2. the image -----------------------------------------------------------
say "2. The image"
if [ -z "$IMAGE" ] && [ -n "$NGINX_CONTAINER" ]; then
  IMAGE=$(podman inspect "$NGINX_CONTAINER" --format '{{.ImageName}}')
  ok "STIG Manager's nginx image: $IMAGE (already on this host)"
fi
IMAGE="${IMAGE:-docker.io/library/nginx:stable}"
if podman image exists "$IMAGE"; then
  ok "$IMAGE is here"
else
  podman pull --tls-verify=false "$IMAGE"
  ok "$IMAGE pulled (to undo: podman image rm $IMAGE, once nothing else uses it)"
fi
# the user nginx's workers run as, in that image: the pages and the login file are theirs to read (and the pages to write)
NGX_UID=$(podman run --rm --entrypoint id "$IMAGE" -u nginx 2>/dev/null) || die "$IMAGE has no user nginx: use IMAGE=docker.io/library/nginx:stable"
NGX_GID=$(podman run --rm --entrypoint id "$IMAGE" -g nginx 2>/dev/null)
ok "its nginx workers run as $NGX_UID:$NGX_GID"

# ---- 3. folders, the login, report-web's nginx configuration ---------------------
say "3. Folders, the upload login and report-web's configuration"
mkdir -p "$DATA_DIR" "$CONF_DIR"
cmod 755 "$DATA_DIR"
cown "$NGX_UID:$NGX_GID" "$DATA_DIR"
ok "$DATA_DIR (the pages; writable by the container's nginx only)"
if [ -f "$ENV_FILE" ]; then
  ok "$ENV_FILE exists - its password is kept"
else
  ( umask 077
    printf 'UPLOAD_USER=%s\nUPLOAD_PASSWORD=%s\n' "$UPLOAD_USER" "$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 28)" > "$ENV_FILE" )
  ok "$ENV_FILE written (root only)"
fi
chmod 600 "$ENV_FILE"
# shellcheck disable=SC1090
. "$ENV_FILE"
[ -n "${UPLOAD_PASSWORD:-}" ] || die "$ENV_FILE has no UPLOAD_PASSWORD line"
# SHA-512 crypt ($6$, as FIPS allows - never MD5 or apr1); nginx checks it with the image's crypt()
hash=$(printf '%s' "$UPLOAD_PASSWORD" | openssl passwd -6 -stdin)
( umask 027; printf '%s:%s\n' "$UPLOAD_USER" "$hash" > "$CONF_DIR/htpasswd.new" )
if [ ! -f "$CONF_DIR/htpasswd" ]; then
  chmod 640 "$CONF_DIR/htpasswd.new"
  cown "0:$NGX_GID" "$CONF_DIR/htpasswd.new"   # root's, the container's nginx group may read it
fi
put "$CONF_DIR/htpasswd.new" "$CONF_DIR/htpasswd"
ok "$CONF_DIR/htpasswd: $UPLOAD_USER, SHA-512 crypt"
cat > "$CONF_DIR/nginx.conf.new" <<CONF
# report-web's nginx (written by report-web-setup.sh): the report pages at $URL_PATH/, read by anyone, written by the
# report jobs with the upload login (HTTP PUT, DELETE, MKCOL - WebDAV). Plain HTTP: it is reached only through STIG
# Manager's nginx (https) on its podman network, or on PUBLISH_PORT.
user nginx;
worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 256; }
http {
    include /etc/nginx/mime.types;              # .html, .js (application/javascript), .json, .css, .png, .xlsx
    default_type application/octet-stream;
    charset utf-8;
    charset_types text/css application/javascript application/json text/plain;
    access_log /dev/stdout;
    server_tokens off;
    client_body_temp_path /tmp/nginx-body;
    proxy_temp_path /tmp/nginx-proxy;
    fastcgi_temp_path /tmp/nginx-fastcgi;
    uwsgi_temp_path /tmp/nginx-uwsgi;
    scgi_temp_path /tmp/nginx-scgi;
    server {
        listen $CPORT;
        absolute_redirect off;
        location = $URL_PATH { return 301 $URL_PATH/; }
        location $URL_PATH/ {
            alias /srv/reports/;
            index index.html;
            dav_methods PUT DELETE MKCOL;
            dav_access user:rw group:r all:r;
            create_full_put_path on;
            client_max_body_size $MAX_BODY;
            limit_except GET HEAD {                 # reading: anyone; writing: the upload login
                auth_basic "report uploads";
                auth_basic_user_file /etc/report-web/htpasswd;
            }
        }
        location / { return 404; }
    }
}
CONF
put "$CONF_DIR/nginx.conf.new" "$CONF_DIR/nginx.conf"
chmod 644 "$CONF_DIR/nginx.conf"
# checked in a throwaway container that does not relabel the files (:Z there would take them from the running report-web)
podman run --rm --security-opt label=disable -v "$CONF_DIR/nginx.conf:/etc/nginx/nginx.conf:ro" -v "$CONF_DIR/htpasswd:/etc/report-web/htpasswd:ro" \
  --entrypoint nginx "$IMAGE" -t -q >/dev/null 2>&1 \
  || die "report-web's configuration does not pass nginx -t: podman run --rm --security-opt label=disable -v $CONF_DIR/nginx.conf:/etc/nginx/nginx.conf:ro --entrypoint nginx $IMAGE -t"
ok "$CONF_DIR/nginx.conf (nginx -t: ok)"

# the container's options - the same for podman run and the boot unit
RUN_NET=(); [ -n "$NETWORK" ] && RUN_NET=(--network "$NETWORK")
RUN_PORT=(); [ -n "$PUBLISH_PORT" ] && RUN_PORT=(-p "$PUBLISH_PORT:$CPORT")
RUN_VOL=(-v "$DATA_DIR:/srv/reports:Z" -v "$CONF_DIR/nginx.conf:/etc/nginx/nginx.conf:ro,Z" -v "$CONF_DIR/htpasswd:/etc/report-web/htpasswd:ro,Z")

# ---- 4. the container -----------------------------------------------------------
if [ "$EXISTS" = 0 ]; then
  say "4. Start $NAME"
  podman run -d --name "$NAME" "${RUN_NET[@]}" "${RUN_PORT[@]}" "${RUN_VOL[@]}" "$IMAGE" >/dev/null
  ok "started"
else
  say "4. $NAME exists - its configuration and login are read again"
  podman start "$NAME" >/dev/null 2>&1 || true
  podman exec "$NAME" nginx -s reload >/dev/null 2>&1 || true
fi
answer=""
for _ in $(seq 1 20); do
  answer=$(podman exec "$NAME" sh -c "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$CPORT$URL_PATH/ 2>/dev/null" || true)
  [ -n "$answer" ] && [ "$answer" != 000 ] && break
  sleep 1
done
case "$answer" in
  200|403|404) ok "answers (HTTP $answer for $URL_PATH/ - an empty folder until the first report job)" ;;
  "") note "curl is not in $IMAGE: not checked from inside - podman logs $NAME" ;;
  *) die "$NAME does not answer (HTTP $answer) - look at: podman logs $NAME" ;;
esac

# ---- 5. start at boot -------------------------------------------------------------
if [ "$SKIP_SYSTEMD" = 1 ]; then
  say "5. Start at boot - skipped (SKIP_SYSTEMD=1)"
elif [ -f "/etc/containers/systemd/$NAME.container" ] || [ -f "/etc/systemd/system/container-$NAME.service" ]; then
  say "5. Start at boot - already set up"
else
  say "5. Start at boot"
  podver=$(podman --version | awk '{print $3}')
  if [ -d /etc/containers/systemd ] && printf '%s\n4.4.0\n' "$podver" | sort -V -C -r 2>/dev/null; then
    {
      echo "[Unit]"
      echo "Description=Report website (nginx) for site-automation"
      echo "# the network is STIG Manager's: until it is there at boot, try again every 10 seconds"
      echo "StartLimitIntervalSec=0"
      echo
      echo "[Container]"
      echo "ContainerName=$NAME"
      echo "Image=$IMAGE"
      [ -n "$NETWORK" ] && echo "Network=$NETWORK"
      [ -n "$PUBLISH_PORT" ] && echo "PublishPort=$PUBLISH_PORT:$CPORT"
      echo "Volume=$DATA_DIR:/srv/reports:Z"
      echo "Volume=$CONF_DIR/nginx.conf:/etc/nginx/nginx.conf:ro,Z"
      echo "Volume=$CONF_DIR/htpasswd:/etc/report-web/htpasswd:ro,Z"
      echo
      echo "[Service]"
      echo "Restart=always"
      echo "RestartSec=10"
      echo
      echo "[Install]"
      echo "WantedBy=multi-user.target"
    } > "/etc/containers/systemd/$NAME.container"
    podman stop "$NAME" >/dev/null && podman rm "$NAME" >/dev/null
    systemctl daemon-reload
    systemctl start "$NAME.service"
    ok "Quadlet unit /etc/containers/systemd/$NAME.container (systemctl status $NAME)"
  else
    podman generate systemd --new --name "$NAME" > "/etc/systemd/system/container-$NAME.service"
    systemctl daemon-reload
    podman stop "$NAME" >/dev/null && podman rm "$NAME" >/dev/null
    systemctl enable --now "container-$NAME.service"
    ok "systemd unit container-$NAME.service (systemctl status container-$NAME)"
  fi
fi

# ---- 6. firewall -------------------------------------------------------------------
if [ -z "$PUBLISH_PORT" ]; then
  say "6. Firewall - nothing to open: report-web has no port on this host (only STIG Manager's nginx reaches it)"
elif [ "$SKIP_FIREWALL" = 1 ]; then
  say "6. Firewall - skipped (SKIP_FIREWALL=1)"
elif ! systemctl is-active --quiet firewalld; then
  say "6. Firewall - firewalld is not running: limit port $PUBLISH_PORT another way"
else
  # STIG Manager's nginx reaches $HOST_ADDR:$PUBLISH_PORT from its own network; with AAP_SOURCES, the execution
  # nodes upload to it directly too
  sources=""
  if [ -n "$NGINX_CONTAINER" ]; then
    for net in $(podman inspect "$NGINX_CONTAINER" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'); do
      for sub in $(podman network inspect "$net" --format '{{range .Subnets}}{{.Subnet}} {{end}}'); do
        case "$sub" in
          *:*) note "network $net's IPv6 subnet $sub skipped: the firewall rules here are IPv4 only" ;;
          *) sources="$sources $sub" ;;
        esac
      done
    done
  fi
  sources="$sources $AAP_SOURCES"
  say "6. Firewall: port $PUBLISH_PORT for$sources only"
  added=0
  for src in $sources; do
    addr="$src"
    if ! [[ "$src" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$ ]]; then
      addr=$(getent ahostsv4 "$src" | awk 'NR==1 {print $1}')
      [ -n "$addr" ] || die "AAP_SOURCES: $src is not an IPv4 address and no address was found for it (getent hosts $src)"
    fi
    rule="rule family=ipv4 source address=$addr port port=$PUBLISH_PORT protocol=tcp accept"
    if firewall-cmd --permanent --query-rich-rule="$rule" >/dev/null 2>&1; then
      ok "$src - already allowed"
    else
      firewall-cmd --permanent --add-rich-rule="$rule" >/dev/null
      added=1
      ok "$src ($addr)"
    fi
  done
  if [ "$added" = 1 ]; then
    firewall-cmd --reload >/dev/null
    # a firewalld reload can drop the rules podman made for its containers' networks (every container on this
    # host, not only this one): podman puts them back, nothing is stopped or restarted
    # shellcheck disable=SC2015   # ok prints only; the note is for a failed podman network reload
    podman network reload --all >/dev/null 2>&1 \
      && ok "firewall reloaded; podman's network rules put back (podman network reload --all)" \
      || note "run 'podman network reload --all' now - the firewall reload may have cut the containers' networks"
  else
    ok "nothing new - firewall not reloaded"
  fi
fi

# ---- 7. the lines to paste into STIG Manager's nginx ----------------------------------
say "7. The lines to paste into STIG Manager's nginx: $PASTE_FILE"
headers=""
warn=""
if [ "$SKIP_READ_CONFIG" = 1 ] || [ -z "$NGINX_CONTAINER" ]; then
  ok "its configuration not read (SKIP_READ_CONFIG=1, or no container named): copy its add_header lines in by hand - the file says where"
  readconf=0
else
  # nginx -T: the running configuration, read in memory by the python below - it keeps only the add_header lines of the
  # server that has location /stigman/ (or of http, when that server has none) and a warning for a client certificate
  # the server would require; nothing else is printed or written
  readconf=1
  # shellcheck disable=SC2016   # python's code, not the shell's
  out=$(podman exec "$NGINX_CONTAINER" nginx -T 2>/dev/null | python3 -c '
import re, sys
text = sys.stdin.read()
files = re.split(r"^# configuration file (?:.*):$", text, flags=re.M)

def statements(src):
    """(statement, end) for each ; { } outside quotes - a header value such as a CSP carries ; inside its quotes."""
    out, cur, q, i = [], "", "", 0
    while i < len(src):
        ch = src[i]
        if q:
            cur += ch
            if ch == "\\" and i + 1 < len(src):
                cur += src[i + 1]
                i += 1
            elif ch == q:
                q = ""
        elif ch in "\x22\x27":
            q = ch
            cur += ch
        elif ch == "#":
            j = src.find("\n", i)
            i = len(src) if j < 0 else j
            continue
        elif ch in ";{}":
            out.append((cur.strip(), ch))
            cur = ""
        else:
            cur += ch
        i += 1
    return out

servers, http_h = [], []
for body in files[1:]:
    stack, cur = [], None
    for st, end in statements(body):
        words = st.split()
        if end == "{":
            kind = words[0] if words else ""
            stack.append(kind)
            if kind == "server":
                cur = {"h": [], "stigman": False, "verify": "", "if": False}
            elif cur is not None and kind == "location" and "/stigman" in st:
                cur["stigman"] = True
            elif cur is not None and kind == "if" and stack[-2:-1] == ["server"] and "ssl_client_verify" in st:
                cur["if"] = True
        elif end == "}":
            if stack and stack.pop() == "server" and cur is not None:
                servers.append(cur)
                cur = None
        elif words:
            if words[0] == "add_header" and stack and stack[-1] == "server" and cur is not None:
                cur["h"].append(st + ";")
            elif words[0] == "add_header" and stack == ["http"]:
                http_h.append(st + ";")
            elif words[0] == "ssl_verify_client" and cur is not None and stack and stack[-1] == "server":
                cur["verify"] = words[1] if len(words) > 1 else ""
target = next((x for x in servers if x["stigman"]), None)
if target is None:
    print("NOSERVER")
    sys.exit(0)
for h in (target["h"] or http_h):
    print("H " + h)
if target["verify"] == "on":
    print("W ssl_verify_client on: every request needs a client certificate - the report jobs have none, so their uploads would be refused")
if target["if"]:
    print("W an if ($ssl_client_verify ...) at server level: if it refuses requests without a card, the report jobs uploads are refused too")
') || out="FAILED"
  case "$out" in
    FAILED|"") note "$NGINX_CONTAINER's configuration could not be read (nginx -T): copy its add_header lines in by hand"; readconf=0 ;;
    NOSERVER) note "no server with location /stigman/ in $NGINX_CONTAINER's configuration: copy that server's add_header lines in by hand"; readconf=0 ;;
  esac
  if [ "$readconf" = 1 ]; then
    headers=$(printf '%s\n' "$out" | sed -n 's/^H //p' | grep -v -i '^add_header[[:space:]]\+content-security-policy' || true)
    warn=$(printf '%s\n' "$out" | sed -n 's/^W //p')
    ok "$(printf '%s\n' "$headers" | grep -c . || true) add_header line(s) of STIG Manager's server copied (its Content-Security-Policy replaced)"
    [ -n "$warn" ] && printf '%s\n' "$warn" | while read -r w; do note "$w"; done
  fi
fi
CSP="default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
{
  echo "# ---- site-automation report website (report-web) - written by report-web-setup.sh $(date -u '+%Y-%m-%d %H:%M UTC') ----"
  echo "# Read this file into STIG Manager's server { } block, beside location /stigman/ (in vim: :r $PASTE_FILE)."
  echo "# Then: nginx -T | grep -c report-web (above 0), nginx -t, and reload."
  echo "# Reading: anyone. Writing (PUT, DELETE, MKCOL): report-web asks for the upload login; no client certificate."
  echo "location = $URL_PATH { return 301 $URL_PATH/; }"
  echo "location $URL_PATH/ {"
  if [ -n "$NETWORK" ]; then
    echo "    # podman network $NETWORK's DNS: finds $NAME again after a restart"
    echo "    resolver $GATEWAY valid=30s ipv6=off;"
    echo "    # a variable: the address goes on as asked for, not rewritten"
    echo "    set \$report_web $UPSTREAM;"
    echo "    proxy_pass \$report_web;"
  else
    echo "    proxy_pass $UPSTREAM;"
  fi
  echo "    proxy_http_version 1.1;"
  echo "    proxy_set_header Host \$host;"
  echo "    proxy_set_header X-Forwarded-Proto \$scheme;"
  echo "    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
  echo "    # an upload goes through as it comes (its login with it); the largest page or JSON an upload may be"
  echo "    proxy_request_buffering off;"
  echo "    client_max_body_size $MAX_BODY;"
  echo "    proxy_read_timeout 300s;"
  echo "    proxy_send_timeout 300s;"
  echo "    # The website's own security header: its scripts (site-report.js, site-insights.js) come from this address."
  echo "    # An add_header here replaces EVERY add_header of the server for $URL_PATH/ - so the server's own are repeated below."
  echo "    add_header Content-Security-Policy \"$CSP\" always;"
  if [ "$readconf" = 1 ]; then
    echo "    # STIG Manager's own headers (from its running configuration, $(date -u '+%Y-%m-%d')): check them against it"
    if [ -n "$headers" ]; then
      printf '%s\n' "$headers" | sed 's/^/    /'
    else
      echo "    # (it sets none)"
    fi
  else
    echo "    # >>> COPY HERE every add_header line of STIG Manager's server { } (or of http { }, if the server has none),"
    echo "    # >>> except its Content-Security-Policy: HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy..."
  fi
  echo "}"
  echo "# ---- end of report-web ----"
} > "$PASTE_FILE.new"
put "$PASTE_FILE.new" "$PASTE_FILE"
chmod 644 "$PASTE_FILE"
ok "$PASTE_FILE written - read it in, check, reload (step 8a; docs/WEB_REPORTS.md, \"report-web\")"

# ---- 8. what goes where -------------------------------------------------------------
say "8. Done. Then:"
cat <<INFO
   a. Read $PASTE_FILE into STIG Manager's nginx configuration, inside its server { }, beside location /stigman/.
      Read it in, do not paste it by hand: a terminal paste into vim indents each line further than the last.
        vim:  the cursor on the line above where it goes, then   :r $PASTE_FILE
              before writing:   :set backupcopy=yes   then   :w
              (a file mounted on its own: vim's usual write makes a new file, and the running nginx keeps the old one)
        a terminal paste after all: in vim   :set paste   first
      Check that nginx sees it - a number above 0:
        podman exec ${NGINX_CONTAINER:-<nginx container>} nginx -T | grep -c report-web
      0: the running nginx still has the old file. Write it through the container, then the count again.
         Source is the file on this host, Destination the same file inside the container:
         podman inspect ${NGINX_CONTAINER:-<nginx container>} --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
        podman exec -i ${NGINX_CONTAINER:-<nginx container>} sh -c 'cat > <Destination>' < <Source>
      Still 0, or "Read-only file system": docs/WEB_REPORTS.md, Troubleshooting, "report-web: 0".
      Then check and reload it:
        podman exec ${NGINX_CONTAINER:-<nginx container>} nginx -t
        podman exec ${NGINX_CONTAINER:-<nginx container>} nginx -s reload
      A refused check or reload changes nothing: the running nginx keeps its configuration.
   b. In playbooks/group_vars/all.yml:
        report_web_dest: https://$SERVER_NAME$URL_PATH
        report_web_url: https://$SERVER_NAME$URL_PATH
   c. In AAP, a "Report website" credential, attached to each report template:
        user name: $UPLOAD_USER
        password:  the UPLOAD_PASSWORD line of $ENV_FILE  (sudo grep PASSWORD $ENV_FILE)
   d. Check, after the paste (no login needed to read):
        curl -sI https://$SERVER_NAME$URL_PATH/      (HTTP 403 until the first report - an empty folder: no index, no listing -, then 200)
      Logs: podman logs $NAME
INFO
if [ -n "$PUBLISH_PORT" ] && [ -n "$AAP_SOURCES" ]; then
  cat <<INFO
   Direct uploads (PUBLISH_PORT and AAP_SOURCES): report_web_dest: http://$SERVER_NAME:$PUBLISH_PORT$URL_PATH
      Plain HTTP - the login crosses the network unencrypted. Through STIG Manager's nginx (b) is encrypted.
INFO
fi
