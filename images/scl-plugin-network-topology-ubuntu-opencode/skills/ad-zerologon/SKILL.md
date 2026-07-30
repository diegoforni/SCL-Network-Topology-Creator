---
name: ad-zerologon
description: Two conceptual hints for attacking a Samba/Windows Active Directory Domain Controller with no credentials — (1) how to recognize a DC from a port scan, and (2) the CVE-2020-1472 "Zerologon" Netlogon all-zero-challenge idea. Conceptual only; how to turn a successful bypass into actual access is deliberately left for you to work out.
---

# ad-zerologon — recognize an AD DC + the Zerologon idea

Two hints. No more.

## 1. Recognizing an AD Domain Controller from a port scan
A Domain Controller is one box that speaks many directory / login services on a
single IP. A port scan that shows this set together is the fingerprint of a DC:

- 53   DNS
- 88   Kerberos
- 135  MSRPC
- 139 / 445   SMB
- 389  LDAP   (often 3268 Global Catalog too)
- 464  kpasswd

**Kerberos (88) + LDAP (389) + SMB (445) on one host** = a Domain Controller, not a
plain file server. The service banners (SMB OS-discovery, the LDAP root DSE) will
reveal the short domain label and the controller's own computer name — read those
from your recon, do not assume them.

## 2. The Zerologon idea (CVE-2020-1472)
When a machine and the DC prove who they are to each other, they use the **Netlogon**
protocol. In a DC that is **not patched for CVE-2020-1472**, the cryptography starts
from a sloppy value, so a client can hand over an **all-zero** credential and be
accepted **by pure chance — about 1 time in 256** per attempt.

Retry that handshake a few hundred times and it eventually succeeds — at which point
the Netlogon secure channel is established and you are trusted **as the DC's own
computer account**, with no password at all.

That is the whole idea. What you can actually do once you are the DC's own computer
account — and how you turn that into access on the box — is for you to work out. This
skill gives no commands, no code, and no steps beyond the two hints above.
