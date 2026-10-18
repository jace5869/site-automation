#!/bin/bash
# ee-check: what this execution environment has. Run it inside the image:
#   podman run --rm <image> ee-check
# the Python Ansible runs on (the first line of the ansible command), not the system's python3
py=$(head -1 "$(command -v ansible)" 2>/dev/null | sed -e 's/^#! *//' -e 's/ .*//')
[ -x "$py" ] || py=python3
echo "== Python: $($py --version 2>&1)   ansible-core: $(ansible --version 2>/dev/null | head -1)"
echo "== Python libraries"
for m in winrm:pywinrm requests_kerberos:requests-kerberos requests_ntlm:requests-ntlm pypsrp:pypsrp \
         spnego:pyspnego gssapi:gssapi krb5:krb5 sansldap:sansldap ldap3:ldap3 dns:dnspython \
         pyVmomi:pyVmomi com.vmware.vcenter_client:vmware-vcenter \
         kerberos:pykerberos requests:requests solidfire:solidfire-sdk-python \
         pymysql:PyMySQL pymssql:pymssql \
         pylibsshext:ansible-pylibssh paramiko:paramiko ncclient:ncclient netaddr:netaddr textfsm:textfsm \
         jmespath:jmespath cryptography:cryptography; do
  mod=${m%%:*}; name=${m#*:}
  if $py -c "import $mod" 2>/dev/null; then echo "  YES  $name"; else echo "  no   $name"; fi
done
echo "== Collections"
ansible-galaxy collection list 2>/dev/null | grep -E '^[a-z0-9_]+\.[a-z0-9_]+ ' | sort
echo "== Tools"
for t in kinit klist ssh sshpass dig ping; do
  if command -v $t >/dev/null; then echo "  YES  $t"; else echo "  no   $t"; fi
done
echo "== Kerberos (FIPS, Active Directory)"
if [ -e /etc/krb5.conf.d/crypto-policies ]; then echo "  /etc/krb5.conf.d/crypto-policies is there: on a FIPS host AD's AES-SHA1 keys are refused"
else echo "  crypto-policies link removed"; fi
grep -h permitted_enctypes /etc/krb5.conf.d/* 2>/dev/null | sed 's/^ */  /'
echo "== Your CA certificates added: $(ls /etc/pki/ca-trust/source/anchors/ 2>/dev/null | wc -l)"
