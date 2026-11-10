#!/bin/sh
# Start a test DC: SA_ROLE=provision (the first DC, SA_IP its address) or join (another DC; its DNS is the first DC's).
# SA_ADMIN_PASS: the domain's Administrator password. /sa-certs/<name>.{crt,key} and ca.crt, when mounted: its LDAPS
# certificate (else Samba makes its own). Prints SA-DC-READY when it serves.
set -u
name="$(hostname -s)"
[ -f /etc/samba/smb.conf ] && mv /etc/samba/smb.conf /etc/samba/smb.conf.orig
# xattr_tdb: the NT ACLs in a file, not in security.* xattrs a rootless container may not set
VFS="vfs objects = dfs_samba4 acl_xattr xattr_tdb"
if [ "$SA_ROLE" = provision ]; then
    samba-tool domain provision --realm=CORP.EXAMPLE.MIL --domain=CORP --server-role=dc --dns-backend=SAMBA_INTERNAL \
        --adminpass="$SA_ADMIN_PASS" --host-name="$name" --host-ip="$SA_IP" --option="$VFS" \
        --option="dns forwarder = 127.0.0.1" > /tmp/setup.log 2>&1 || { tail -8 /tmp/setup.log; exit 1; }
else
    printf '[libdefaults]\n default_realm = CORP.EXAMPLE.MIL\n dns_lookup_kdc = true\n' > /etc/krb5.conf
    samba-tool domain join corp.example.mil DC -U Administrator --password="$SA_ADMIN_PASS" --realm=CORP.EXAMPLE.MIL \
        --dns-backend=SAMBA_INTERNAL --option="$VFS" > /tmp/setup.log 2>&1 || { tail -8 /tmp/setup.log; exit 1; }
fi
if [ -f "/sa-certs/$name.crt" ]; then
    tls=/var/lib/samba/private/tls
    mkdir -p "$tls"
    cp "/sa-certs/$name.crt" "$tls/cert.pem"
    cp "/sa-certs/$name.key" "$tls/key.pem"
    cp /sa-certs/ca.crt "$tls/ca.pem"
    chmod 600 "$tls/key.pem"
fi
echo SA-DC-READY
exec samba -i --debug-stdout -d 1
