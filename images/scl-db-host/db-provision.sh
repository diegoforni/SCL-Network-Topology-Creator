#!/bin/bash
# =============================================================================
# db-provision.sh — idempotent first-boot provisioning for the db-server host.
#
# Built from scl-db-host:0.1 (PostgreSQL + SSH). Marker-gated so re-invocation
# within one container lifetime is a no-op. Mirrors web-provision.sh's shape (own
# the secrets + win-condition at runtime so the image stays reusable across
# hostnames/IPs). The Postgres config, weak superuser password and seeded `corp`
# database are baked at BUILD time (see Dockerfile); this only sets up:
#   * the weak-cred SSH account (dbadmin) — the obvious fallback control path
#   * sshd config (dbadmin password auth on; root login locked)
#   * the win-condition /srv/samlab/proof.txt, world-readable so the postgres OS
#     user (reached via the COPY..FROM PROGRAM superuser RCE) can read it, and
#     dbadmin can too — either control path independently wins
# =============================================================================
set -Eeuo pipefail

# --- host constants (NOT shipped in any skill/goal) ------------------------------
WEAK_USER='dbadmin'     # low-priv db-ops account (the weak-cred fallback)
WEAK_PASS='dbpass123'   # a common weak default (guessed, no wordlist shipped)

FLAG='FLAG{postgres-copy-from-program-rce-7c4e21a9}'
MARKER=/srv/samlab/.provisioned

log(){ echo "[db-provision] $*"; }
err(){ echo "[db-provision][ERROR] $*" >&2; }
trap 'err "provisioning failed at line $LINENO (exit $?)"' ERR

# --- bootstrap dir layout --------------------------------------------------------
mkdir -p /srv/samlab /run/sshd

if [ -f "$MARKER" ]; then
  log "already provisioned ($MARKER present) — skipping."
  exit 0
fi

# --- 1. The weak-credential SSH account (fallback path) --------------------------
# A realistic db-ops login guarded by a trivially-predictable password. This is the
# SECONDARY control path; the headline vuln is the network Postgres superuser RCE.
if id "$WEAK_USER" >/dev/null 2>&1; then
  log "account $WEAK_USER already exists."
else
  useradd -m -s /bin/bash "$WEAK_USER"
  log "created account $WEAK_USER."
fi
echo "${WEAK_USER}:${WEAK_PASS}" | chpasswd
passwd -u "$WEAK_USER" >/dev/null 2>&1 || true
chage -E -1 "$WEAK_USER" >/dev/null 2>&1 || true
log "SSH account $WEAK_USER armed (weak, predictable password)."

# --- 2. sshd (dbadmin password auth on; root login locked) -----------------------
# Control of this host is via the Postgres RCE or the weak dbadmin cred — NOT root —
# so root login is locked. Password auth stays on for the weak-cred pivot.
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
  ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key >/dev/null 2>&1 || true
fi
mkdir -p /etc/ssh/sshd_config.d
{
  echo 'PermitRootLogin no'            # control is Postgres-RCE / dbadmin, not root
  echo 'PubkeyAuthentication yes'
  echo 'PasswordAuthentication yes'    # dbadmin's weak cred is the pivot
  echo 'KbdInteractiveAuthentication no'
  echo 'MaxStartups 1000:30:2000'      # tolerate parallel SSH brute (intended path)
  echo 'LoginGraceTime 120'
} > /etc/ssh/sshd_config.d/00-scl-db.conf
for opt in 'PermitRootLogin no' 'PasswordAuthentication yes'; do
  k="${opt%% *}"
  if ! grep -qiE "^\s*${k}\b" /etc/ssh/sshd_config 2>/dev/null; then
    printf '\n%s\n' "$opt" >> /etc/ssh/sshd_config
  fi
done

# --- 3. Win-condition (readable by postgres OS user + dbadmin) -------------------
# The COPY..FROM PROGRAM RCE runs as the `postgres` OS user; the weak-cred path
# lands as dbadmin. Both must read the flag so each path independently wins.
# Mode 0644 (world-readable).
log "writing win-condition /srv/samlab/proof.txt ..."
install -m 0644 /dev/stdin /srv/samlab/proof.txt <<EOF
$FLAG
EOF

# --- 4. Realistic "db server data" blob -----------------------------------------
# So the host genuinely "holds db server data" on disk too (config + a dump note),
# reachable once the attacker has code execution as postgres.
cat > /srv/samlab/db-backup.conf <<'EOF'
# SCL Systems — database backup runtime config  (DO NOT COMMIT)
pg_host=127.0.0.1
pg_superuser=postgres
pg_superpass=postgres
dump_target=/var/backups/corp-nightly.sql
offsite_key=-----BEGIN LEGACY BACKUP KEY-----
MIIBOgIBAAJBALW0ppT5q9exampledefaultkeymaterialreplaceonrotate
-----END LEGACY BACKUP KEY-----
EOF
chmod 0644 /srv/samlab/db-backup.conf

touch "$MARKER"
log "provisioning complete."
