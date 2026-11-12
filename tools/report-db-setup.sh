#!/usr/bin/env bash
# report-db-setup.sh - the report history database (MySQL 8.4) for site-automation, in its own rootful podman
# container on this host. Separate from any other database on the host (STIG Manager's stays untouched).
#
# What it does, in order (safe to run again: each step checks first and skips what is already there):
#   1. checks: root, podman, the port is free
#   2. pulls the image (TLS verification off, as this network needs)
#   3. makes the data folder and the passwords file (/root/report-db.env, readable by root only)
#   4. starts the container and waits until MySQL answers
#   5. creates the database and two accounts: report_writer (AAP writes) and report_reader (read only)
#   6. makes it start at boot (a Quadlet unit on podman 4.4+, else a generated systemd unit)
#   7. opens the port in firewalld to the AAP execution nodes only
#   8. prints what to put in AAP's "Report database" credential
#
# Usage, as root:
#   AAP_SOURCES="192.0.2.21 192.0.2.22" bash report-db-setup.sh
# Settings (environment variables, all optional except AAP_SOURCES for the firewall):
#   IMAGE        the MySQL image             (default docker.io/library/mysql:8.4)
#   NAME         the container's name        (default report-db)
#   PORT         the port on this host       (default 3309; 3306 and 3307 may already be used by other databases)
#   DATA_DIR     where the data lives        (default /var/lib/report-db)
#   DB_NAME      the database                (default site_reports)
#   AAP_SOURCES  the AAP execution nodes' addresses (or names, looked up), space separated: only they may reach the port
#   AAP_HOSTS    which client hosts the accounts accept, MySQL style (default '%'; e.g. '192.0.2.%')
#   SKIP_SYSTEMD=1 / SKIP_FIREWALL=1   leave those steps out
set -euo pipefail

IMAGE="${IMAGE:-docker.io/library/mysql:8.4}"
NAME="${NAME:-report-db}"
PORT="${PORT:-3309}"
DATA_DIR="${DATA_DIR:-/var/lib/report-db}"
DB_NAME="${DB_NAME:-site_reports}"
AAP_SOURCES="${AAP_SOURCES:-}"
AAP_HOSTS="${AAP_HOSTS:-%}"
ENV_FILE="${ENV_FILE:-/root/report-db.env}"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"
SKIP_FIREWALL="${SKIP_FIREWALL:-0}"

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
die()  { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }

# ---- 1. checks --------------------------------------------------------------
say "1. Checks"
[ "$(id -u)" -eq 0 ] || [ "${ALLOW_NON_ROOT:-0}" = 1 ] || die "run as root (sudo -i), the container is rootful"
command -v podman >/dev/null || die "podman is not installed (dnf install podman)"
ok "podman $(podman --version | awk '{print $3}')"
if podman container exists "$NAME"; then
  ok "container $NAME already exists - steps 2 to 4 skipped"
  EXISTS=1
else
  EXISTS=0
  if ss -ltnH "( sport = :$PORT )" | grep -q .; then
    die "port $PORT is already in use on this host - pick another: PORT=3310 bash $0"
  fi
  ok "port $PORT is free"
fi

# ---- 2. the image -----------------------------------------------------------
if [ "$EXISTS" = 0 ]; then
  say "2. Pull $IMAGE (TLS verification off)"
  if podman image exists "$IMAGE"; then
    ok "already here"
  else
    podman pull --tls-verify=false "$IMAGE"
  fi
fi

# ---- 3. data folder and passwords ---------------------------------------------
say "3. Data folder and passwords"
mkdir -p "$DATA_DIR"
# once the container runs, MySQL owns the folder (rootless: as a sub-uid we may not chmod) - its mode is left as it is
if [ "$EXISTS" = 0 ]; then
  chmod 700 "$DATA_DIR"
elif ! chmod 700 "$DATA_DIR" 2>/dev/null; then
  ok "$DATA_DIR is the container's now - its mode left as it is"
fi
ok "$DATA_DIR"
if [ -f "$ENV_FILE" ]; then
  ok "$ENV_FILE exists - its passwords are kept"
else
  umask 077
  gen() { head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24; }
  {
    echo "MYSQL_ROOT_PASSWORD=$(gen)"
    echo "REPORT_WRITER_PASSWORD=$(gen)"
    echo "REPORT_READER_PASSWORD=$(gen)"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "$ENV_FILE written (root only)"
fi
# shellcheck disable=SC1090
. "$ENV_FILE"
# the container gets only the root password (the accounts' passwords stay in $ENV_FILE)
CT_ENV="${ENV_FILE%.env}.container.env"
( umask 077; echo "MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD" > "$CT_ENV" )

# ---- 4. the container -----------------------------------------------------------
if [ "$EXISTS" = 0 ]; then
  say "4. Start $NAME on port $PORT"
  podman run -d --name "$NAME" \
    -p "$PORT:3306" \
    -v "$DATA_DIR:/var/lib/mysql:Z" \
    --env-file "$CT_ENV" \
    --health-cmd 'mysqladmin ping -h 127.0.0.1 --silent' --health-interval 30s \
    "$IMAGE" >/dev/null
  ok "started"
fi
podman start "$NAME" >/dev/null 2>&1 || true
printf '   waiting for MySQL'
for _ in $(seq 1 60); do
  if podman exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$NAME" mysql -uroot -h 127.0.0.1 -e "SELECT 1" >/dev/null 2>&1; then
    echo " - answering"
    break
  fi
  printf '.'
  sleep 3
done
podman exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$NAME" mysql -uroot -h 127.0.0.1 -e "SELECT 1" >/dev/null 2>&1 \
  || die "MySQL did not answer in 3 minutes - look at: podman logs $NAME"

# ---- 5. database and accounts -----------------------------------------------------
say "5. Database $DB_NAME and the two accounts"
podman exec -i -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$NAME" mysql -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'report_writer'@'$AAP_HOSTS' IDENTIFIED BY '$REPORT_WRITER_PASSWORD';
CREATE USER IF NOT EXISTS 'report_reader'@'$AAP_HOSTS' IDENTIFIED BY '$REPORT_READER_PASSWORD';
ALTER USER 'report_writer'@'$AAP_HOSTS' IDENTIFIED BY '$REPORT_WRITER_PASSWORD';
ALTER USER 'report_reader'@'$AAP_HOSTS' IDENTIFIED BY '$REPORT_READER_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW
  ON \`$DB_NAME\`.* TO 'report_writer'@'$AAP_HOSTS';
GRANT SELECT, SHOW VIEW ON \`$DB_NAME\`.* TO 'report_reader'@'$AAP_HOSTS';
FLUSH PRIVILEGES;
SQL
ok "database $DB_NAME, accounts report_writer and report_reader (from hosts '$AAP_HOSTS')"

# ---- 6. start at boot -------------------------------------------------------------
if [ "$SKIP_SYSTEMD" = 1 ]; then
  say "6. Start at boot - skipped (SKIP_SYSTEMD=1)"
elif [ -f "/etc/containers/systemd/$NAME.container" ] || [ -f "/etc/systemd/system/container-$NAME.service" ]; then
  say "6. Start at boot - already set up"
else
  say "6. Start at boot"
  podver=$(podman --version | awk '{print $3}')
  if [ -d /etc/containers/systemd ] && printf '%s\n4.4.0\n' "$podver" | sort -V -C -r 2>/dev/null; then
    cat > "/etc/containers/systemd/$NAME.container" <<UNIT
[Unit]
Description=Report history database (MySQL) for site-automation

[Container]
ContainerName=$NAME
Image=$IMAGE
PublishPort=$PORT:3306
Volume=$DATA_DIR:/var/lib/mysql:Z
EnvironmentFile=$CT_ENV
HealthCmd=mysqladmin ping -h 127.0.0.1 --silent
HealthInterval=30s

[Service]
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
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

# ---- 7. firewall -------------------------------------------------------------------
if [ "$SKIP_FIREWALL" = 1 ]; then
  say "7. Firewall - skipped (SKIP_FIREWALL=1)"
elif ! systemctl is-active --quiet firewalld; then
  say "7. Firewall - firewalld is not running: limit port $PORT to the AAP nodes another way"
elif [ -z "$AAP_SOURCES" ]; then
  say "7. Firewall - AAP_SOURCES not given: port $PORT stays closed; run again with AAP_SOURCES=\"<addresses>\""
else
  say "7. Firewall: port $PORT for $AAP_SOURCES only"
  added=0
  for src in $AAP_SOURCES; do
    # firewalld takes addresses only: a host name is looked up first
    addr="$src"
    if ! [[ "$src" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$ ]]; then
      addr=$(getent ahostsv4 "$src" | awk 'NR==1 {print $1}')
      [ -n "$addr" ] || die "AAP_SOURCES: $src is not an IPv4 address and no address was found for it (getent hosts $src)"
    fi
    rule="rule family=ipv4 source address=$addr port port=$PORT protocol=tcp accept"
    if firewall-cmd --permanent --query-rich-rule="$rule" >/dev/null 2>&1; then
      ok "$src${addr:+ ($addr)} - already allowed"
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
    podman network reload --all >/dev/null 2>&1 \
      && ok "firewall reloaded; podman's network rules put back (podman network reload --all)" \
      || echo "   NOTE: run 'podman network reload --all' now - the firewall reload may have cut the containers' networks"
  else
    ok "nothing new - firewall not reloaded"
  fi
fi

# ---- 8. what AAP needs -------------------------------------------------------------
say "8. Done. In AAP, a \"Report database\" credential with:"
cat <<INFO
   host:      $(hostname -f)
   port:      $PORT
   database:  $DB_NAME
   user:      report_writer
   password:  the REPORT_WRITER_PASSWORD line of $ENV_FILE  (sudo grep WRITER $ENV_FILE)
   (report_reader / REPORT_READER_PASSWORD is for read-only use, e.g. a dashboard later)

   Check from an AAP node:  nc -zv $(hostname -f) $PORT
   Logs:                    podman logs $NAME
INFO
