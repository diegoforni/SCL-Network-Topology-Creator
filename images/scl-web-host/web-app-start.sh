#!/bin/bash
# =============================================================================
# web-app-start.sh — web host foreground supervisor for the vuln-web-server host.
#
# Run as the container's foreground completion block (host_script sets it via
# `exec /usr/local/bin/web-app-start.sh`, mirroring ad-server/windows-client) so a
# failed web host fails the container instead of hiding behind `tail -f /dev/null`.
#
#   1. provision the weak-cred account + flag (idempotent, marker-gated)
#   2. start sshd in the background (survives the foreground lighttpd)
#   3. ensure lighttpd runtime dirs + launch a background readiness logger (:80)
#   4. exec lighttpd in the foreground (PID 1) — `-D` keeps it from daemonizing so a
#      crash fails the container (mirrors `exec samba -i` / `exec xrdp --nodaemon`).
# =============================================================================
set -Eeuo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

log(){ echo "[web-app-start] $*"; }
err(){ echo "[web-app-start][ERROR] $*" >&2; }

log "lighttpd: $(dpkg-query -W -f='${Version}' lighttpd 2>/dev/null || echo '?')  bash-vuln: $(/usr/local/bin/bash-vuln --version 2>/dev/null | head -1 || echo 'n/a')"

# --- 1. Provision (idempotent) ---------------------------------------------------
/usr/local/bin/web-provision.sh

# --- 2. Start sshd in the background --------------------------------------------
mkdir -p /run/sshd && chmod 0755 /run/sshd
[ -f /etc/ssh/ssh_host_ed25519_key ] || ssh-keygen -A >/dev/null 2>&1 || err "ssh-keygen -A failed"
if /usr/sbin/sshd -t 2>/dev/null; then
  /usr/sbin/sshd && log "sshd started (background) — webadmin password login enabled; root locked."
else
  /usr/sbin/sshd 2>/dev/null && log "sshd started (config warned -t)." || err "sshd failed to start."
fi

# --- 3. Ensure lighttpd runtime dirs + readiness logger -------------------------
# lighttpd writes its logs / upload cache here; they must exist + be www-data-writable.
mkdir -p /var/log/lighttpd /var/cache/lighttpd/uploads
chown -R www-data:www-data /var/log/lighttpd /var/cache/lighttpd 2>/dev/null || true
chmod 0750 /var/log/lighttpd /var/cache/lighttpd/uploads 2>/dev/null || true

(
  for _ in $(seq 1 60); do
    sleep 1
    if nc -w1 -z 127.0.0.1 80 2>/dev/null; then
      log "HTTP ready: :80 accepting connections."
      pgrep -x sshd >/dev/null 2>&1 && log "smoke OK: sshd running (fallback path armed)." \
        || log "WARN: sshd not detected."
      break
    fi
  done
) &

# --- 4. Foreground: lighttpd (HTTP server) --------------------------------------
# `-D` (== --no-daemonize) keeps lighttpd in the foreground so it is PID 1 and a crash
# fails the container (mirrors `exec samba -i` / `exec xrdp --nodaemon`).
log "starting lighttpd in the foreground (PID 1)..."
exec lighttpd -D -f /etc/lighttpd/scl-lighttpd.conf
