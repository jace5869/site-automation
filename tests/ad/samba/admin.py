#!/usr/bin/python3
"""What the test does as the domain's administrator, on the DC itself (never by the job): sa-dc-admin recycle-bin
enables the Recycle Bin; sa-dc-admin dns <name> <ip> registers a DC's SRV and A records the way its Netlogon would."""
import sys

import ldb
from samba.auth import system_session
from samba.param import LoadParm
from samba.samdb import SamDB

lp = LoadParm()
lp.load_default()
db = SamDB(url="/var/lib/samba/private/sam.ldb", session_info=system_session(), lp=lp)
base = str(db.get_default_basedn())
if sys.argv[1] == "recycle-bin":
    m = ldb.Message()
    m.dn = ldb.Dn(db, "")
    m["enableOptionalFeature"] = ldb.MessageElement("CN=Partitions,CN=Configuration,%s:766ddcd8-acd0-445e-f3b9-a7f9b6744f2a" % base,
                                                    ldb.FLAG_MOD_ADD, "enableOptionalFeature")
    db.modify(m)
    print("the Recycle Bin is on")
