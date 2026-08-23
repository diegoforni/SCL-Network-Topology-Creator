#!/bin/bash
# =============================================================================
# db-app-start.sh — db host foreground supervisor for the db-server host.
#
# Run as the container's foreground completion block (host_script sets it via
# `exec /usr/local/bin/db-app-start.sh`, mirroring ad-server/vuln-web-server) so a
# failed DB fails the container instead of hiding behind `tail -f /dev/null`.
#
#   1. provision the weak-cred SSH account + flag (idempotent, marker-gated)
#   2. start sshd in the background (survives the foreground postgres)
#   3. launch a background readiness+smoke-test logger (:5432)
#   4. exec postgres in the foreground (PID 1), dropped to the `postgres` user —
#      the binary refuses to run as root; foreground so a crash fails the container
#      (mirrors `exec samba -i` / `exec lighttpd -D`).
# =============================================================================
set -Eeuo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

log(){ echo "[db-app-start] $*"; }
err(){ echo "[db-app-start][ERROR] $*" >&2; }

PGVER="$(ls /etc/postgresql | sort -V | tail -1)"
PGCONF="/etc/postgresql/$PGVER/main/postgresql.conf"
PGBIN="/usr/lib/postgresql/$PGVER/bin/postgres"
log "postgres version dir: $PGVER  ($(${PGBIN} --version 2>/dev/null || echo '?'))"

# --- 1. Provision (idempotent) ---------------------------------------------------
/usr/local/bin/db-provision.sh

# --- 2. Start sshd in the background --------------------------------------------
mkdir -p /run/sshd && chmod 0755 /run/sshd
[ -f /etc/ssh/ssh_host_ed25519_key ] || ssh-keygen -A >/dev/null 2>&1 || err "ssh-keygen -A failed"
if /usr/sbin/sshd -t 2>/dev/null; then
  /usr/sbin/sshd && log "sshd started (background) — dbadmin password login enabled; root locked."
else
  /usr/sbin/sshd 2>/dev/null && log "sshd started (config warned -t)." || err "sshd failed to start."
fi

# --- 3. Background readiness + smoke-test logger --------------------------------
(
  for _ in $(seq 1 60); do
    sleep 1
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      log "Postgres ready: :5432 accepting connections."
      pgrep -x sshd >/dev/null 2>&1 && log "smoke OK: sshd running (fallback path armed)." \
        || log "WARN: sshd not detected."
      break
    fi
  done
) &

# --- 4. Foreground: postgres (must run as the postgres user) ---------------------
# `-c config_file=` points at the Debian-managed config, which sets data_directory,
# hba_file, etc. Running the binary directly (not pg_ctlcluster) keeps it in the
# foreground as PID 1 so a crash fails the container.
# Ensure the socket/PID dir exists (unix_socket_directories + external_pid_file point
# here). pg_ctlcluster would recreate it; running the binary directly does not, so a
# fresh tmpfs-backed /run would otherwise leave postgres unable to open its socket.
mkdir -p /var/run/postgresql && chown postgres:postgres /var/run/postgresql && chmod 2775 /var/run/postgresql
log "starting PostgreSQL in the foreground (PID 1)..."
exec su postgres -c "exec ${PGBIN} -c config_file=${PGCONF}"
