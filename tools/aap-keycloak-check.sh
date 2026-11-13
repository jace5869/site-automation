#!/usr/bin/env bash
# AAP behind STIG Manager's nginx, with the Keycloak login: the check (docs/AAP_KEYCLOAK.md).
#
# It only asks and prints. It changes nothing: no file is written, nginx, AAP and Keycloak are only sent
# GET requests, and no container is touched. (One request is not a GET: the admin login of check 8, which
# changes nothing either.) Run it as any user, on any Linux machine that reaches the two names the way a
# browser does (your workstation, or the STIG Manager host).
#
# It asks the addresses in its settings and nothing else: no proxy is used (whatever http_proxy says), and
# nothing outside your network is called. It works with no internet.
#
# What it checks, one PASS / FAIL / WARN / SKIP line each:
#   1. DNS        the AAP name and the Keycloak name resolve
#   2. TLS        the AAP name's certificate is trusted, is for that name, and how long it lasts
#   3. Gateway    AAP's platform gateway answers through nginx (and nginx does not ask for a card here)
#   4. Keycloak   the realm's .well-known/openid-configuration answers and names the same address
#   5. Redirect   the login AAP starts goes to this Keycloak, comes back to the AAP name over https,
#                 and Keycloak knows that redirect URI for the client
#      Card       what the realm does with a caller that shows no card: refuse (the card login is on), or
#                 show a password form (it is not)
#   6. Websocket  nginx passes the Upgrade headers (the live job output)
#      Card CAs   which CAs nginx asks a card from on Keycloak's address, and whether they are the ones in
#                 CARD_CA_FILE. The line names the file it used.
#   7. Token      only with AAP_TOKEN_FILE: the API answers a token through nginx
#      AAP login  only with AAP_TOKEN_FILE: no LDAP (password) login is enabled in AAP next to the Keycloak one
#   8. LDAP sync  only with KEYCLOAK_ADMIN_PASSWORD_FILE: the realm's LDAP federation is there and on, when it
#                 last synced, how many people the realm holds
#      Groups     only with KEYCLOAK_ADMIN_PASSWORD_FILE: the tokens Keycloak would give a person carry the
#                 claim "groups" (Keycloak's own "evaluate" - no login is made, no token is issued)
#
# Usage:
#   tools/aap-keycloak-check.sh --settings aap-keycloak.env
#   AAP_URL=https://aap.yoursite.mil KEYCLOAK_URL=https://keycloak.yoursite.mil tools/aap-keycloak-check.sh
#
# Options:
#   --settings FILE     a file of NAME=value lines (tools/aap-keycloak.env.example), the one the other two
#                       scripts read. It is read as text, never run. The environment wins over it.
#   -h, --help          this text
#
# Settings (the environment, or the --settings file):
#   AAP_URL             the address people type for AAP, https, no path. Required.
#   KEYCLOAK_URL        Keycloak's address, without /realms/... Required.
#                       An old Keycloak that still lives under /auth: https://keycloak.yoursite.mil/auth
#   KEYCLOAK_REALM      the realm made for AAP (tools/aap-keycloak-realm.py), default aap
#   KEYCLOAK_CLIENT_ID  the client made for AAP, default aap. Only compared with what AAP sends.
#   CA_FILE             the CA chain (PEM) that signed both server certificates. Default: this machine's trust
#                       store.
#   CARD_CA_FILE        the CAs of people's cards (PEM): a copy of the file of nginx's ssl_client_certificate
#                       line. Another network has another PKI: give that network's chain. No file: the CAs nginx
#                       asks for are printed and not compared.
#   AAP_TOKEN_FILE      a file (mode 600) with one AAP token in it, for check 7. Never put the token on the
#                       command line: the script hands it to curl on standard input.
#   KEYCLOAK_ADMIN_PASSWORD_FILE  a file (mode 600) with the Keycloak admin's password, for check 8. It goes to
#                       curl on standard input, never on a command line, and is never printed. No file: check 8
#                       is skipped (this script never asks at a prompt).
#   KEYCLOAK_ADMIN_URL  where the admin API is reached when that is not KEYCLOAK_URL
#   KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_REALM   the admin account and its realm, default admin and master
#   LDAP_URL            when it is set, a realm with no LDAP federation is a FAIL of check 8 (else a SKIP)
#   CHECK_USER          the person whose tokens the Groups line looks at (a Keycloak user name). Default: the
#                       first person the realm lists.
#   AAP_GROUPS_CLAIM    the token claim the Groups line looks for, default groups (AAP's "Groups Claim" field)
#   CHECK_TIMEOUT       seconds per request, default 10
#
# Exit code: 0 when nothing failed, 1 when a check failed, 2 when a setting is missing or wrong.
set -euo pipefail

stop() { printf '%s\n' "$1" >&2; exit 2; }

SETTINGS_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help)
      sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --settings)
      [ $# -ge 2 ] || stop "--settings needs a file (see --help)."
      SETTINGS_FILE="$2"
      shift 2
      ;;
    *) stop "Not an option: $1 (see --help)." ;;
  esac
done
if [ -n "$SETTINGS_FILE" ] && [ ! -r "$SETTINGS_FILE" ]; then
  stop "The settings file $SETTINGS_FILE cannot be read."
fi

# from_file NAME: the value of the last NAME=value line of the settings file, quotes taken off. Read as text.
from_file() {
  local v=""
  [ -n "$SETTINGS_FILE" ] || return 0
  v="$(sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1[[:space:]]*=[[:space:]]*//p" "$SETTINGS_FILE" |
    tail -n 1 | tr -d '\r' | sed 's/[[:space:]]*$//')" || true
  case "$v" in
    \"*\") v="${v#\"}"; v="${v%\"}" ;;
    \'*\') v="${v#\'}"; v="${v%\'}" ;;
  esac
  printf '%s' "$v"
}
# setting NAME DEFAULT: the environment, else the file, else the default.
setting() {
  local v="${!1:-}"
  [ -n "$v" ] || v="$(from_file "$1")"
  printf '%s' "${v:-$2}"
}

AAP_URL="$(setting AAP_URL "")"
KEYCLOAK_URL="$(setting KEYCLOAK_URL "")"
KEYCLOAK_REALM="$(setting KEYCLOAK_REALM aap)"
KEYCLOAK_CLIENT_ID="$(setting KEYCLOAK_CLIENT_ID aap)"
CA_FILE="$(setting CA_FILE "")"
CARD_CA_FILE="$(setting CARD_CA_FILE "")"
AAP_TOKEN_FILE="$(setting AAP_TOKEN_FILE "")"
KEYCLOAK_ADMIN_URL="$(setting KEYCLOAK_ADMIN_URL "")"
KEYCLOAK_ADMIN_USER="$(setting KEYCLOAK_ADMIN_USER admin)"
KEYCLOAK_ADMIN_REALM="$(setting KEYCLOAK_ADMIN_REALM master)"
KEYCLOAK_ADMIN_PASSWORD_FILE="$(setting KEYCLOAK_ADMIN_PASSWORD_FILE "")"
LDAP_URL="$(setting LDAP_URL "")"
CHECK_USER="$(setting CHECK_USER "")"
AAP_GROUPS_CLAIM="$(setting AAP_GROUPS_CLAIM groups)"
CHECK_TIMEOUT="$(setting CHECK_TIMEOUT 10)"

FAILED=0
pass() { printf 'PASS  %-10s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-10s %s\n' "$1" "$2"; FAILED=1; }
warn() { printf 'WARN  %-10s %s\n' "$1" "$2"; }
skip() { printf 'SKIP  %-10s %s\n' "$1" "$2"; }
hint() { printf '                 %s\n' "$1"; }

command -v curl >/dev/null 2>&1 || stop "curl is not installed on this machine."
[ -n "$AAP_URL" ] || stop "Set AAP_URL, e.g. AAP_URL=https://aap.yoursite.mil (see --help)."
[ -n "$KEYCLOAK_URL" ] || stop "Set KEYCLOAK_URL, e.g. KEYCLOAK_URL=https://keycloak.yoursite.mil (see --help)."
AAP_URL="${AAP_URL%/}"
KEYCLOAK_URL="${KEYCLOAK_URL%/}"
case "$AAP_URL" in
  https://*) ;;
  *) stop "AAP_URL must start with https:// - it is the address people type." ;;
esac
case "$KEYCLOAK_URL" in
  https://*) ;;
  *) stop "KEYCLOAK_URL must start with https://" ;;
esac
case "${AAP_URL#https://}" in
  */*) stop "AAP_URL must not have a path: AAP needs its own name, it does not work under /something/." ;;
esac
if [ -n "$CA_FILE" ] && [ ! -r "$CA_FILE" ]; then
  stop "CA_FILE=$CA_FILE cannot be read."
fi
if [ -n "$CARD_CA_FILE" ] && [ ! -r "$CARD_CA_FILE" ]; then
  stop "CARD_CA_FILE=$CARD_CA_FILE cannot be read."
fi
if [ -n "$AAP_TOKEN_FILE" ] && [ ! -r "$AAP_TOKEN_FILE" ]; then
  stop "AAP_TOKEN_FILE=$AAP_TOKEN_FILE cannot be read."
fi
if [ -n "$KEYCLOAK_ADMIN_PASSWORD_FILE" ] && [ ! -r "$KEYCLOAK_ADMIN_PASSWORD_FILE" ]; then
  stop "KEYCLOAK_ADMIN_PASSWORD_FILE=$KEYCLOAK_ADMIN_PASSWORD_FILE cannot be read."
fi
KEYCLOAK_ADMIN_URL="${KEYCLOAK_ADMIN_URL:-$KEYCLOAK_URL}"
KEYCLOAK_ADMIN_URL="${KEYCLOAK_ADMIN_URL%/}"

# No proxy, whatever the environment says: every address here is one of yours.
CURL=(curl --silent --http1.1 --noproxy '*' --max-time "$CHECK_TIMEOUT")
if [ -n "$CA_FILE" ]; then
  CURL+=(--cacert "$CA_FILE")
fi

# host_of https://name:port/path -> name ; port_of -> port (443 when none)
host_of() { local h="${1#https://}"; h="${h%%/*}"; printf '%s' "${h%%:*}"; }
port_of() {
  local h="${1#https://}"
  h="${h%%/*}"
  case "$h" in
    *:*) printf '%s' "${h##*:}" ;;
    *) printf '443' ;;
  esac
}

# fetch URL [more curl arguments]: the answer's status in CODE (000 = no answer) and its text in BODY.
CODE=000
BODY=""
fetch() {
  local out
  out="$("${CURL[@]}" --write-out '\n%{http_code}' "$@" 2>/dev/null)" || true
  CODE="${out##*$'\n'}"
  BODY="${out%$'\n'*}"
  [ -n "$CODE" ] || CODE=000
}

# json_text NAME: the first "NAME": "value" in BODY (enough for the few flat values read here).
json_text() {
  printf '%s' "$BODY" | tr -d '\n' | grep -o "\"$1\" *: *\"[^\"]*\"" | head -n 1 | sed 's/^[^:]*: *"//; s/"$//' || true
}

# url_decode TEXT: %2F -> / and + -> space.
url_decode() { local s="${1//+/ }"; printf '%b' "${s//%/\\x}"; }

# query_value URL NAME: one query parameter, decoded.
query_value() {
  local q="${1#*\?}" part
  local IFS='&'
  for part in $q; do
    if [ "${part%%=*}" = "$2" ]; then
      url_decode "${part#*=}"
      return 0
    fi
  done
  return 0
}

# from_json CODE [arguments]: a few lines of Python read BODY (JSON) and print what the check needs. Python 3 is
# what the other two scripts run on. It only reads.
from_json() {
  local code="$1"
  shift
  printf '%s' "$BODY" | python3 -c "$code" "$@" 2>/dev/null || true
}

# ago SECONDS -> "14 minutes", "3 hours", "2 days"
ago() {
  if [ "$1" -lt 120 ]; then
    printf '%s seconds' "$1"
  elif [ "$1" -lt 7200 ]; then
    printf '%s minutes' "$(($1 / 60))"
  elif [ "$1" -lt 172800 ]; then
    printf '%s hours' "$(($1 / 3600))"
  else
    printf '%s days' "$(($1 / 86400))"
  fi
}

AAP_HOST="$(host_of "$AAP_URL")"
AAP_PORT="$(port_of "$AAP_URL")"
KC_HOST="$(host_of "$KEYCLOAK_URL")"
KC_PORT="$(port_of "$KEYCLOAK_URL")"
REALM_URL="$KEYCLOAK_URL/realms/$KEYCLOAK_REALM"

printf 'AAP       %s\nKeycloak  %s (realm %s, client %s)\n' "$AAP_URL" "$KEYCLOAK_URL" "$KEYCLOAK_REALM" \
  "$KEYCLOAK_CLIENT_ID"
printf 'CA files  servers: %s; cards: %s\n\n' "${CA_FILE:-the trust store of this machine}" \
  "${CARD_CA_FILE:-none given (CARD_CA_FILE)}"

# 1. DNS ----------------------------------------------------------------------------------------------------
for name in "$AAP_HOST" "$KC_HOST"; do
  if ! command -v getent >/dev/null 2>&1; then
    skip "DNS" "$name: getent is not on this machine"
  elif address="$(getent hosts "$name" 2>/dev/null | head -n 1)" && [ -n "$address" ]; then
    pass "DNS" "$name is ${address%% *}"
  else
    fail "DNS" "$name does not resolve on this machine"
    if [ "$name" = "$AAP_HOST" ]; then
      hint "Ask for a DNS record for $name that points at the STIG Manager host (guide, step 1.5)."
    else
      hint "Is KEYCLOAK_URL the address STIG Manager's login uses?"
    fi
  fi
done

# 2. TLS ----------------------------------------------------------------------------------------------------
tls_error="$("${CURL[@]}" --show-error --output /dev/null "$AAP_URL/" 2>&1)" && tls_rc=0 || tls_rc=$?
case "$tls_rc" in
  0)
    pass "TLS" "$AAP_HOST: the certificate is trusted and is for this name"
    ;;
  60 | 51)
    fail "TLS" "$AAP_HOST: the certificate is not trusted or not for this name"
    hint "$tls_error"
    hint "The certificate nginx shows must name $AAP_HOST and be signed by your CA (guide, step 2.4)."
    hint "This machine does not trust your CA? Set CA_FILE to the chain."
    ;;
  6 | 7 | 28)
    fail "TLS" "$AAP_HOST port $AAP_PORT does not answer"
    hint "$tls_error"
    ;;
  *)
    fail "TLS" "$AAP_HOST: curl stopped with code $tls_rc"
    hint "$tls_error"
    ;;
esac
if [ "$tls_rc" = 0 ] && command -v openssl >/dev/null 2>&1; then
  cert="$(openssl s_client -connect "$AAP_HOST:$AAP_PORT" -servername "$AAP_HOST" </dev/null 2>/dev/null |
    sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p')" || cert=""
  if [ -n "$cert" ]; then
    ends="$(printf '%s\n' "$cert" | openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//')" || ends=""
    if printf '%s\n' "$cert" | openssl x509 -noout -checkend 2592000 >/dev/null 2>&1; then
      pass "TLS" "the certificate lasts until $ends"
    else
      warn "TLS" "the certificate ends within 30 days: $ends"
    fi
  fi
fi

# 3. The gateway through nginx --------------------------------------------------------------------------------
GATEWAY_OK=0
fetch "$AAP_URL/api/gateway/v1/ping/"
if [ "$CODE" != 200 ]; then
  ping_code="$CODE"
  ping_body="$BODY"
  fetch "$AAP_URL/api/gateway/v1/"
  if [ "$CODE" != 200 ] && [ "$CODE" != 401 ]; then
    CODE="$ping_code"
    BODY="$ping_body"
  fi
fi
case "$CODE" in
  200 | 401)
    GATEWAY_OK=1
    pass "Gateway" "the platform gateway answers through nginx (HTTP $CODE)"
    ;;
  400)
    if printf '%s' "$BODY" | grep -q -i 'SSL certificate'; then
      fail "Gateway" "nginx asks for a client certificate on $AAP_HOST (HTTP 400)"
      hint "Scripts, tokens, webhooks and AAP's own parts carry no card."
      hint "The AAP server block needs the line: ssl_verify_client off;  (guide, step 2.6)"
    else
      fail "Gateway" "HTTP 400 from $AAP_URL/api/gateway/v1/ping/"
      hint "Is the Host header passed? The block needs: proxy_set_header Host \$http_host;"
    fi
    ;;
  404)
    fail "Gateway" "HTTP 404: another server block answered, not AAP"
    hint "nginx has no server block with server_name $AAP_HOST, or it was not reloaded (guide, steps 2.6 to 2.9)."
    ;;
  502 | 503 | 504)
    fail "Gateway" "HTTP $CODE: nginx answers but cannot reach the gateway"
    hint "Check the proxy_pass address and port, and the firewall on the AAP host (guide, step 2.3)."
    ;;
  000)
    fail "Gateway" "no answer from $AAP_URL"
    ;;
  *)
    fail "Gateway" "HTTP $CODE from $AAP_URL/api/gateway/v1/ping/"
    ;;
esac

# 4. Keycloak -------------------------------------------------------------------------------------------------
fetch "$REALM_URL/.well-known/openid-configuration"
if [ "$CODE" = 200 ]; then
  issuer="$(json_text issuer)"
  auth_endpoint="$(json_text authorization_endpoint)"
  if [ "$issuer" = "$REALM_URL" ] && [ -n "$auth_endpoint" ]; then
    pass "Keycloak" "realm $KEYCLOAK_REALM: .well-known/openid-configuration answers, the issuer matches"
  elif [ -z "$issuer" ]; then
    fail "Keycloak" "the answer is not an OpenID configuration (no issuer in it)"
  else
    fail "Keycloak" "the issuer is $issuer, not $REALM_URL"
    hint "Use the address Keycloak names itself by: set KEYCLOAK_URL to it, and use it in AAP too."
  fi
elif [ "$CODE" = 404 ]; then
  fail "Keycloak" "HTTP 404 from $REALM_URL/.well-known/openid-configuration"
  hint "Wrong realm name (KEYCLOAK_REALM=$KEYCLOAK_REALM)? An old Keycloak: add /auth to KEYCLOAK_URL."
elif [ "$CODE" = 400 ] && printf '%s' "$BODY" | grep -q -i 'SSL certificate'; then
  fail "Keycloak" "Keycloak's address asks every caller for a client certificate (HTTP 400)"
  hint "AAP asks Keycloak for the token itself, with no card: the login cannot finish (guide, step 2.6)."
elif [ "$CODE" = 000 ]; then
  fail "Keycloak" "no answer from $REALM_URL"
  hint "The name, the port, or a certificate this machine does not trust (CA_FILE)."
else
  fail "Keycloak" "HTTP $CODE from $REALM_URL/.well-known/openid-configuration"
fi

# 5. The redirect URI -----------------------------------------------------------------------------------------
if [ "$GATEWAY_OK" != 1 ]; then
  skip "Redirect" "the gateway did not answer (check 3)"
else
  fetch "$AAP_URL/api/gateway/v1/ui_auth/"
  login_paths="$(printf '%s' "$BODY" | tr -d '\n' | grep -o '"login_url" *: *"[^"]*"' |
    sed 's/^[^:]*: *"//; s/"$//')" || login_paths=""
  if [ "$CODE" != 200 ]; then
    fail "Redirect" "HTTP $CODE from $AAP_URL/api/gateway/v1/ui_auth/ (the list of logins)"
  elif [ -z "$login_paths" ]; then
    fail "Redirect" "AAP offers no single sign-on login yet"
    hint "Make the authentication method and tick Enabled (guide, step 5)."
  else
    found=0
    while IFS= read -r login_path; do
      [ -n "$login_path" ] || continue
      case "$login_path" in
        /*) login_url="$AAP_URL$login_path" ;;
        *) login_url="$login_path" ;;
      esac
      location="$("${CURL[@]}" --output /dev/null --dump-header - "$login_url" 2>/dev/null |
        tr -d '\r' | grep -i '^location:' | head -n 1 | sed 's/^[Ll]ocation: *//')" || location=""
      case "$location" in
        "$KEYCLOAK_URL"/*) ;;
        *) continue ;;
      esac
      found=1
      redirect_uri="$(query_value "$location" redirect_uri)"
      client_id="$(query_value "$location" client_id)"
      pass "Redirect" "AAP sends the login to this Keycloak (client $client_id)"
      if [ "$client_id" != "$KEYCLOAK_CLIENT_ID" ]; then
        warn "Redirect" "the client is $client_id, you said $KEYCLOAK_CLIENT_ID (KEYCLOAK_CLIENT_ID)"
      fi
      case "$redirect_uri" in
        "$AAP_URL"/*)
          pass "Redirect" "it comes back to $redirect_uri"
          ;;
        http://*)
          fail "Redirect" "it comes back over http: $redirect_uri"
          hint "The gateway does not know the browser used https. The block needs:"
          hint "proxy_set_header X-Forwarded-Proto https;  (guide, step 2.6 and the table in step 6)"
          ;;
        *)
          fail "Redirect" "it comes back to another name: $redirect_uri"
          hint "The gateway builds this from the Host header. The block needs:"
          hint "proxy_set_header Host \$http_host;  (guide, step 2.6)"
          ;;
      esac
      # Ask Keycloak the same thing the browser would: it refuses a redirect URI the client does not list.
      fetch "$location"
      if printf '%s' "$BODY" | grep -q -i 'Invalid parameter: redirect_uri'; then
        fail "Redirect" "Keycloak does not know this redirect URI for client $client_id"
        hint "Keycloak > Clients > $client_id > Valid redirect URIs: paste exactly"
        hint "$redirect_uri  (guide, step 5.6)"
      elif printf '%s' "$BODY" | grep -q -i 'Client not found'; then
        fail "Redirect" "Keycloak has no client $client_id in realm $KEYCLOAK_REALM (guide, step 4.6)"
      elif [ "$CODE" = 400 ] && printf '%s' "$BODY" | grep -q -i 'Invalid username or password'; then
        # The card realm answers a caller with no card this way: past the client and the redirect URI, at the login.
        pass "Redirect" "Keycloak accepts the client and the redirect URI"
        pass "Card" "realm $KEYCLOAK_REALM refuses a caller with no card, and shows no password form"
      elif [ "$CODE" = 200 ] && printf '%s' "$BODY" | grep -q -i 'name="password"'; then
        pass "Redirect" "Keycloak accepts the client and the redirect URI (HTTP $CODE)"
        warn "Card" "realm $KEYCLOAK_REALM shows a user name and password form to a caller with no card"
        hint "Meant (X509_PASSWORD_FORM=on)? Then fine. If not: the card login is not this realm's browser"
        hint "flow. Run tools/aap-keycloak-realm.py and read its lines (guide, step 4.5)."
      elif [ "$CODE" = 200 ] || [ "$CODE" = 302 ] || [ "$CODE" = 303 ]; then
        pass "Redirect" "Keycloak accepts the client and the redirect URI (HTTP $CODE)"
      else
        fail "Redirect" "Keycloak answered HTTP $CODE to the login request"
      fi
    done <<EOF
$login_paths
EOF
    if [ "$found" != 1 ]; then
      fail "Redirect" "no login of AAP goes to $KEYCLOAK_URL"
      hint "Check the URLs in the authentication method (guide, step 5.3)."
    fi
  fi
fi

# The CAs of people's cards ------------------------------------------------------------------------------------
# nginx names the CAs it takes a card from when a browser connects. That list is read here and held against
# CARD_CA_FILE. No card is shown, nothing is logged in.
CA_NAMES_END='Requested Signature Algorithms\|Shared Requested Signature Algorithms\|Client Certificate Types'
CA_NAMES_END="$CA_NAMES_END"'\|Peer signing digest\|Server Temp Key\|---'
if ! command -v openssl >/dev/null 2>&1; then
  skip "Card CAs" "openssl is not on this machine"
else
  asked="$(openssl s_client -connect "$KC_HOST:$KC_PORT" -servername "$KC_HOST" </dev/null 2>/dev/null |
    sed -n "/^Acceptable client certificate CA names/,/^\\($CA_NAMES_END\\)/p" |
    sed '1d;$d' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | sed '/^$/d' | sort -u)" || asked=""
  if [ -z "$asked" ]; then
    warn "Card CAs" "nginx on $KC_HOST names no CA for cards (CARD_CA_FILE: ${CARD_CA_FILE:-none given})"
    hint "Is the card asked for on this address (ssl_verify_client, ssl_client_certificate)? Guide, step 2.6."
  else
    asked_n="$(printf '%s\n' "$asked" | wc -l | tr -d ' ')"
    if [ -z "$CARD_CA_FILE" ]; then
      pass "Card CAs" "nginx on $KC_HOST takes a card from $asked_n CAs. No CARD_CA_FILE given: not compared"
      printf '%s\n' "$asked" | head -n 6 | while IFS= read -r line; do hint "$line"; done
      if [ "$asked_n" -gt 6 ]; then
        hint "... and $((asked_n - 6)) more"
      fi
    else
      mine="$(openssl crl2pkcs7 -nocrl -certfile "$CARD_CA_FILE" 2>/dev/null |
        openssl pkcs7 -print_certs -noout 2>/dev/null | sed -n 's/^subject=[[:space:]]*//p' |
        sed 's/[[:space:]]*$//' | sort -u)" || mine=""
      if [ -z "$mine" ]; then
        fail "Card CAs" "CARD_CA_FILE=$CARD_CA_FILE holds no certificate (PEM)"
      else
        not_asked="$(comm -23 <(printf '%s\n' "$mine") <(printf '%s\n' "$asked"))" || not_asked=""
        not_mine="$(comm -13 <(printf '%s\n' "$mine") <(printf '%s\n' "$asked"))" || not_mine=""
        if [ -z "$not_asked" ] && [ -z "$not_mine" ]; then
          pass "Card CAs" "nginx on $KC_HOST takes a card from the $asked_n CAs of CARD_CA_FILE=$CARD_CA_FILE"
        else
          warn "Card CAs" "nginx on $KC_HOST and CARD_CA_FILE=$CARD_CA_FILE do not name the same CAs"
          if [ -n "$not_asked" ]; then
            printf '%s\n' "$not_asked" | head -n 5 |
              while IFS= read -r line; do hint "in the file, not asked by nginx: $line"; done
          fi
          if [ -n "$not_mine" ]; then
            printf '%s\n' "$not_mine" | head -n 5 |
              while IFS= read -r line; do hint "asked by nginx, not in the file: $line"; done
          fi
          hint "A card of this network must chain to a CA nginx asks for, and Keycloak's truststore needs the"
          hint "same chain (guide, step 4.1). Another network: another chain."
        fi
      fi
    fi
  fi
fi

# 6. Websocket ------------------------------------------------------------------------------------------------
if [ "$GATEWAY_OK" != 1 ]; then
  skip "Websocket" "the gateway did not answer (check 3)"
else
  fetch "$AAP_URL/api/controller/v2/websocket/" --max-time 5 \
    --header "Connection: Upgrade" --header "Upgrade: websocket" --header "Sec-WebSocket-Version: 13" \
    --header "Sec-WebSocket-Key: c2l0ZS1hdXRvbWF0aW9uIQ==" --header "Origin: $AAP_URL"
  case "$CODE" in
    101)
      pass "Websocket" "the Upgrade headers pass: HTTP 101 from /api/controller/v2/websocket/"
      ;;
    401 | 403)
      warn "Websocket" "HTTP $CODE: AAP wants a login first, so this check cannot tell"
      hint "Look at a running job's output in the browser (guide, step 6.3)."
      ;;
    000)
      fail "Websocket" "no answer from /api/controller/v2/websocket/"
      ;;
    *)
      fail "Websocket" "HTTP $CODE, not 101: the Upgrade headers do not reach AAP"
      hint "The block needs proxy_http_version 1.1 and the two lines"
      hint "proxy_set_header Upgrade \$http_upgrade;  proxy_set_header Connection \$connection_upgrade;"
      hint "and the map for \$connection_upgrade above the server block (guide, step 2.6)."
      ;;
  esac
fi

# 7. A token through nginx ------------------------------------------------------------------------------------
if [ -z "$AAP_TOKEN_FILE" ]; then
  skip "Token" "no AAP_TOKEN_FILE given (guide, step 6.4)"
elif [ "$GATEWAY_OK" != 1 ]; then
  skip "Token" "the gateway did not answer (check 3)"
else
  token="$(tr -d ' \t\r\n' <"$AAP_TOKEN_FILE")"
  case "$token" in
    *[!A-Za-z0-9._~+/=-]* | "")
      fail "Token" "$AAP_TOKEN_FILE does not hold one token"
      ;;
    *)
      # The token goes to curl on standard input (a config), so it is never an argument a process list shows.
      out="$(printf 'header = "Authorization: Bearer %s"\n' "$token" |
        "${CURL[@]}" --config - --write-out '\n%{http_code}' "$AAP_URL/api/gateway/v1/me/" 2>/dev/null)" || true
      CODE="${out##*$'\n'}"
      case "$CODE" in
        200) pass "Token" "the API answers the token through nginx" ;;
        401 | 403)
          fail "Token" "HTTP $CODE: AAP refuses the token"
          hint "Is it an AAP token (Access Management > Users > you > Tokens), not expired?"
          ;;
        *) fail "Token" "HTTP ${CODE:-000} from $AAP_URL/api/gateway/v1/me/" ;;
      esac
      ;;
  esac
  unset out

  # The same token reads AAP's login methods: an LDAP one beside Keycloak's is a password login with no card.
  if [ -n "${token:-}" ] && [ "${CODE:-000}" = 200 ]; then
    if ! command -v python3 >/dev/null 2>&1; then
      skip "AAP login" "python3 is not on this machine"
    else
      out="$(printf 'header = "Authorization: Bearer %s"\n' "$token" |
        "${CURL[@]}" --config - --write-out '\n%{http_code}' "$AAP_URL/api/gateway/v1/authenticators/?page_size=200" \
          2>/dev/null)" || true
      CODE="${out##*$'\n'}"
      BODY="${out%$'\n'*}"
      if [ "$CODE" = 200 ]; then
        methods="$(from_json '
import sys, json
d = json.load(sys.stdin)
rows = d.get("results", d) if isinstance(d, dict) else d
for m in rows:
    kind = str(m.get("type", "")).rsplit(".", 1)[-1].lower()
    print("%s\x1f%s\x1f%s" % ("on" if m.get("enabled") else "off", kind, str(m.get("name", "")).replace("\n", " ")))
')"
        password_logins=0
        sso_logins=0
        while IFS=$'\x1f' read -r state kind name; do
          [ "$state" = on ] || continue
          case "$kind" in
            ldap | radius | tacacs)
              password_logins=$((password_logins + 1))
              warn "AAP login" "the ${kind^^} authenticator \"$name\" is enabled next to the Keycloak one:"
              hint "a password login that bypasses the card is on."
              hint "AAP > Access Management > Authentication Methods > $name > untick Enabled (guide, step 5.9)."
              ;;
            oidc | keycloak | saml) sso_logins=$((sso_logins + 1)) ;;
          esac
        done <<EOF
$methods
EOF
        if [ -z "$methods" ]; then
          skip "AAP login" "AAP's list of authentication methods could not be read"
        elif [ "$password_logins" = 0 ]; then
          pass "AAP login" "no LDAP, RADIUS or TACACS+ method is enabled in AAP ($sso_logins single sign-on enabled)"
          hint "The local accounts stay: that is the way in when Keycloak is down (guide, step 5.8)."
        fi
      elif [ "$CODE" = 401 ] || [ "$CODE" = 403 ]; then
        skip "AAP login" "HTTP $CODE: this token may not read the authentication methods (an admin's or auditor's can)"
      else
        skip "AAP login" "HTTP ${CODE:-000} from $AAP_URL/api/gateway/v1/authenticators/"
      fi
    fi
  fi
  unset token out
fi

# 8. The people from the directory, and the groups claim ------------------------------------------------------
# Reads through Keycloak's admin API. The login is the one request here that is not a GET; it changes nothing.
ADMIN_TOKEN=""
admin_fetch() {
  local out
  out="$(printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN" |
    "${CURL[@]}" --config - --write-out '\n%{http_code}' "$KEYCLOAK_ADMIN_URL/admin/realms/$KEYCLOAK_REALM$1" \
      2>/dev/null)" || true
  CODE="${out##*$'\n'}"
  BODY="${out%$'\n'*}"
  [ -n "$CODE" ] || CODE=000
}
# for a curl config on standard input: \ and " are escaped
config_text() { sed 's/\\/\\\\/g; s/"/\\"/g'; }

if [ -z "$KEYCLOAK_ADMIN_PASSWORD_FILE" ]; then
  skip "LDAP sync" "no KEYCLOAK_ADMIN_PASSWORD_FILE given (guide, step 6.5)"
  skip "Groups" "no KEYCLOAK_ADMIN_PASSWORD_FILE given (guide, step 6.5)"
elif ! command -v python3 >/dev/null 2>&1; then
  skip "LDAP sync" "python3 is not on this machine"
  skip "Groups" "python3 is not on this machine"
else
  admin_password="$(head -n 1 "$KEYCLOAK_ADMIN_PASSWORD_FILE" | tr -d '\r\n')"
  out="$(printf 'data-urlencode = "username=%s"\ndata-urlencode = "password=%s"\n' \
    "$(printf '%s' "$KEYCLOAK_ADMIN_USER" | config_text)" "$(printf '%s' "$admin_password" | config_text)" |
    "${CURL[@]}" --config - --data grant_type=password --data client_id=admin-cli --write-out '\n%{http_code}' \
      "$KEYCLOAK_ADMIN_URL/realms/$KEYCLOAK_ADMIN_REALM/protocol/openid-connect/token" 2>/dev/null)" || true
  unset admin_password
  CODE="${out##*$'\n'}"
  BODY="${out%$'\n'*}"
  ADMIN_TOKEN="$(json_text access_token)"
  unset out
  BODY=""
  if [ -z "$ADMIN_TOKEN" ]; then
    if [ "$CODE" = 401 ] || [ "$CODE" = 400 ]; then
      fail "LDAP sync" "Keycloak did not accept the admin account $KEYCLOAK_ADMIN_USER (realm $KEYCLOAK_ADMIN_REALM)"
    else
      fail "LDAP sync" "no admin login at $KEYCLOAK_ADMIN_URL (HTTP ${CODE:-000})"
      hint "The admin address is closed from this machine? Run the check on the Keycloak host, with"
      hint "KEYCLOAK_ADMIN_URL=http://localhost:8080 (guide, step 4.2)."
    fi
    skip "Groups" "no admin login"
  else
    # --- LDAP sync
    admin_fetch "/components?type=org.keycloak.storage.UserStorageProvider"
    federations="$(from_json '
import sys, json
for c in json.load(sys.stdin):
    if c.get("providerId") != "ldap":
        continue
    g = lambda k, d="": str((c.get("config", {}).get(k) or [d])[0]).replace("\n", " ")
    row = [str(c.get("name", "")), g("enabled", "true"), g("connectionUrl"), g("editMode"), g("lastSync", "0")]
    row += [g("changedSyncPeriod", "-1"), g("fullSyncPeriod", "-1"), "yes" if g("customUserSearchFilter") else "no"]
    print("\x1f".join(row))
')"
    if [ "$CODE" != 200 ]; then
      fail "LDAP sync" "HTTP $CODE from the admin API of realm $KEYCLOAK_REALM"
    elif [ -z "$federations" ]; then
      if [ -n "$LDAP_URL" ]; then
        fail "LDAP sync" "realm $KEYCLOAK_REALM has no LDAP federation, and LDAP_URL is set"
        hint "Run tools/aap-keycloak-realm.py and read its lines (guide, step 4.8)."
      else
        skip "LDAP sync" "realm $KEYCLOAK_REALM has no LDAP federation (no LDAP_URL either)"
      fi
    else
      admin_fetch "/users/count"
      people="$(printf '%s' "$BODY" | tr -d -c '0-9')"
      now="$(date +%s)"
      while IFS=$'\x1f' read -r fed_name fed_on fed_url fed_mode fed_last fed_changed fed_full fed_filter; do
        [ -n "$fed_name" ] || continue
        if [ "$fed_on" != true ]; then
          fail "LDAP sync" "the federation $fed_name ($fed_url) is switched off"
          continue
        fi
        pass "LDAP sync" "the federation $fed_name ($fed_url, $fed_mode) is on; the realm holds ${people:-?} people"
        if [ "$fed_mode" != READ_ONLY ]; then
          warn "LDAP sync" "the federation $fed_name is $fed_mode, not READ_ONLY: Keycloak may write to the directory"
        fi
        if [ "$fed_filter" != yes ]; then
          warn "LDAP sync" "the federation $fed_name has no filter: every person under its users DN can log in"
          hint "LDAP_GROUP_DN names the one group whose members may (guide, step 4.8)."
        fi
        case "$fed_last$fed_changed$fed_full" in
          *[!0-9-]*) continue ;;
        esac
        timed="$fed_changed"
        if [ "$timed" -le 0 ]; then
          timed="$fed_full"
        fi
        if [ "$fed_last" -le 0 ]; then
          warn "LDAP sync" "no sync has finished yet. A member is still taken in at the first card login"
        else
          age=$((now - fed_last))
          when="$(date -u -d "@$fed_last" '+%Y-%m-%d %H:%M UTC' 2>/dev/null)" || when="$fed_last"
          if [ "$timed" -gt 0 ] && [ "$age" -gt $((timed * 2 + 120)) ]; then
            warn "LDAP sync" "the last sync finished $when, $(ago "$age") ago: the timed one (every $timed s) is late"
            hint "Keycloak keeps no result of a sync, only the time of the last one that finished. Its log has the"
            hint "reason. Console: User federation > $fed_name > Action > Sync changed users shows it too."
          else
            pass "LDAP sync" "the last sync finished $when, $(ago "$age") ago"
          fi
        fi
        if [ "$fed_changed" -le 0 ] && [ "$fed_full" -le 0 ]; then
          warn "LDAP sync" "no timed sync is set (LDAP_SYNC_CHANGED_SECONDS, LDAP_SYNC_FULL_SECONDS)"
          hint "A person taken out of the group is refused at the next card login anyway. Their name and"
          hint "groups in Keycloak go stale until a sync."
        fi
      done <<EOF
$federations
EOF
    fi

    # --- the groups claim, by Keycloak's own "evaluate"
    admin_fetch "/clients?clientId=$KEYCLOAK_CLIENT_ID"
    client_uuid="$(from_json 'import sys, json
d = json.load(sys.stdin)
print(d[0]["id"] if d else "")')"
    if [ -n "$CHECK_USER" ]; then
      admin_fetch "/users?exact=true&username=$CHECK_USER"
    else
      admin_fetch "/users?max=1"
    fi
    person="$(from_json 'import sys, json
d = json.load(sys.stdin)
print("%s\x1f%s" % (d[0]["id"], d[0].get("username", "")) if d else "")')"
    person_id="${person%%$'\x1f'*}"
    person_name="${person#*$'\x1f'}"
    if [ -z "$client_uuid" ]; then
      fail "Groups" "realm $KEYCLOAK_REALM has no client $KEYCLOAK_CLIENT_ID"
    elif [ -z "$person_id" ]; then
      if [ -n "$CHECK_USER" ]; then
        skip "Groups" "the realm has no person $CHECK_USER (CHECK_USER)"
      else
        skip "Groups" "the realm holds no person yet: nobody to look at"
      fi
    else
      missing=""
      empty=""
      for kind in id-token access-token userinfo; do
        admin_fetch "/clients/$client_uuid/evaluate-scopes/generate-example-$kind?scope=openid&userId=$person_id"
        claim="$(from_json 'import sys, json
d = json.load(sys.stdin)
name = sys.argv[1]
print("none" if name not in d else len(d[name]) if isinstance(d[name], list) else 1)' "$AAP_GROUPS_CLAIM")"
        case "$claim" in
          none | "") missing="$missing${missing:+, }$kind" ;;
          0) empty=yes ;;
          *) group_count="$claim" ;;
        esac
      done
      if [ -n "$missing" ]; then
        fail "Groups" "a token of $person_name has no claim \"$AAP_GROUPS_CLAIM\" ($missing): AAP's maps get nothing"
        hint "The client $KEYCLOAK_CLIENT_ID needs a Group Membership mapper: claim $AAP_GROUPS_CLAIM, full path off,"
        hint "in the ID token, the access token and userinfo. tools/aap-keycloak-realm.py offers it (guide, step 4.5)."
      elif [ -n "$empty" ]; then
        warn "Groups" "the claim \"$AAP_GROUPS_CLAIM\" is in the tokens, and empty for $person_name (in no group)"
        hint "Another person: CHECK_USER. Groups come from the directory (LDAP_TEAM_GROUPS) or from Keycloak."
      else
        pass "Groups" "the tokens of $person_name carry the claim \"$AAP_GROUPS_CLAIM\" (groups: ${group_count:-1})"
      fi
    fi
  fi
  ADMIN_TOKEN=""
fi

printf '\n'
if [ "$FAILED" = 0 ]; then
  printf 'Nothing failed.\n'
else
  printf 'At least one check failed. Each FAIL names the guide step: docs/AAP_KEYCLOAK.md\n'
fi
exit "$FAILED"
