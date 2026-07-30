#!/bin/bash
# =============================================================================
# rdp-app-start.sh — RDP host foreground supervisor for the windows-client host.
#
# Run as the container's foreground completion block (host_script sets it via
# `exec /usr/local/bin/rdp-app-start.sh`, mirroring ad-server) so a failed RDP host
# fails the container instead of hiding behind `tail -f /dev/null`.
#
#   1. provision the account + planted root key (idempotent, marker-gated)
#   2. start sshd in the background (survives the foreground xrdp)
#   3. launch a background readiness logger (probes RDP :3389)
#   4. start xrdp-sesman, then exec xrdp --nodaemon in the foreground (PID 1) — the
#      real RDP server. A crashed xrdp -> failed container.
# =============================================================================
set -Eeuo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

log(){ echo "[rdp-app-start] $*"; }
err(){ echo "[rdp-app-start][ERROR] $*" >&2; }

log "xrdp: $(dpkg-query -W -f='${Version}' xrdp 2>/dev/null || echo '?')  pwsh: $(pwsh --version 2>/dev/null | head -1 || echo 'n/a')"

# --- 1. Provision (idempotent) ---------------------------------------------------
/usr/local/bin/rdp-provision.sh

# --- 2. Start sshd in the background --------------------------------------------
mkdir -p /run/sshd && chmod 0755 /run/sshd
[ -f /etc/ssh/ssh_host_ed25519_key ] || ssh-keygen -A >/dev/null 2>&1 || err "ssh-keygen -A failed"
if /usr/sbin/sshd -t 2>/dev/null; then
  /usr/sbin/sshd && log "sshd started (background) — root pubkey + aadmin password login enabled."
else
  /usr/sbin/sshd 2>/dev/null && log "sshd started (config warned -t)." || err "sshd failed to start."
fi

# --- 3. Background readiness logger (pure logging) ------------------------------
# xrdp-sesman must be up before xrdp accepts sessions; start it as a daemon, then poll
# the listener. The compose healthcheck gates coder56 launches on :3389 readiness, but
# this log line gives an in-container signal during bring-up.
mkdir -p /run/xrdp /var/run/xrdp /tmp/.X11-unix
xrdp-sesman 2>/dev/null && log "xrdp-sesman started (daemon)." || err "xrdp-sesman failed to start."
(
  for _ in $(seq 1 60); do
    sleep 1
    if nc -w1 -z 127.0.0.1 3389 2>/dev/null; then
      log "RDP ready: :3389 accepting connections."
      pgrep -x sshd >/dev/null 2>&1 && log "smoke OK: sshd running (shell payoff armed)." \
        || log "WARN: sshd not detected."
      break
    fi
  done
) &

# --- 4. Foreground: xrdp (real RDP server) --------------------------------------
# --nodaemon keeps xrdp in the foreground so it is PID 1 and a crash fails the
# container (mirrors `exec samba -i` on the AD host).
log "starting xrdp in the foreground (PID 1)..."
exec xrdp --nodaemon
