#!/bin/bash
# =============================================================================
# ad-app-start.sh — AD DC foreground supervisor for the ad-server host.
#
# Run as the container's foreground completion block (host_script sets it via
# `exec /usr/local/bin/ad-app-start.sh`, mirroring greedy-server) so a failed DC
# fails the container instead of hiding behind `tail -f /dev/null`.
#
#   1. provision the domain (idempotent, marker-gated) — ad-provision.sh
#   2. start sshd in the background (survives the foreground samba)
#   3. launch a background readiness+smoke-test logger
#   4. exec samba in the foreground (PID 1) — single AD-DC daemon serving
#      DNS/Kerberos/LDAP/SMB/RPC/kpasswd/GC. A failed DC -> failed container.
# =============================================================================
set -Eeuo pipefail

export PATH=/usr/local/samba/bin:/usr/local/samba/sbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

log(){ echo "[ad-app-start] $*"; }
err(){ echo "[ad-app-start][ERROR] $*" >&2; }

log "samba version: $(samba --version 2>/dev/null | head -1 || echo 'unknown')"

# --- 1. Provision (idempotent) ---------------------------------------------------
/usr/local/bin/ad-provision.sh

# --- 2. Start sshd in the background --------------------------------------------
# /run/sshd is the privilege-separation dir sshd demands at startup.
mkdir -p /run/sshd && chmod 0755 /run/sshd
[ -f /etc/ssh/ssh_host_rsa_key ] || ssh-keygen -A >/dev/null 2>&1 || err "ssh-keygen -A failed"
if /usr/sbin/sshd -t 2>/dev/null; then
  /usr/sbin/sshd && log "sshd started (background) — root pubkey login enabled."
else
  /usr/sbin/sshd 2>/dev/null && log "sshd started (config warned -t)." || err "sshd failed to start."
fi

# --- 3. Background readiness + smoke-test logger (pure logging) -----------------
(
  for _ in $(seq 1 90); do
    sleep 1
    # DC readiness: the Kerberoast decoy still works (svc_sql authenticates + reads
    # the passwords$ blob), proving the AD is actually serving.
    if smbclient "//127.0.0.1/netlogon" -U "SC\\svc_sql%Dragon2024!" -c 'ls' >/dev/null 2>&1; then
      log "DC ready: svc_sql authenticates over SMB (decoy live)."
      if smbclient "//127.0.0.1/passwords\$" -U "SC\\svc_sql%Dragon2024!" -c 'get passwords.txt /tmp/ad-blob.selftest' >/dev/null 2>&1; then
        log "smoke OK: passwords\$ blob retrievable as svc_sql."
      else
        log "WARN: passwords\$ self-test failed."
      fi
      pgrep -x sshd >/dev/null 2>&1 && log "smoke OK: sshd running (shell payoff armed)." \
        || log "WARN: sshd not detected."
      break
    fi
  done
) &

# --- 4. Foreground: samba AD DC --------------------------------------------------
# `-D` is intentionally NOT used; we WANT foreground so this is PID 1 and a Samba
# crash fails the container. A single `samba` process serves every AD-DC role.
log "starting Samba AD DC in the foreground (PID 1)..."
exec samba -i
