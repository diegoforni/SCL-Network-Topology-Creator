#!/usr/bin/env bash
# Brings up the Acción del Sur full-stack target inside a repo-server host.
# Launched in the background by role_service_block('repo-server'); the host's
# own startup (host_script) keeps the container alive with `tail -f /dev/null`.
#
# Stack: MariaDB (3306) -> seed -> Node backend (3001) -> nginx (80, frontend + /api proxy)
set -x

REPO=/srv/repo
DB_NAME=accion_del_sur
DB_ROOT_PW=adminadminadmin

# ── 1) MariaDB ────────────────────────────────────────────────────────────────
# bind-address + skip-name-resolve are set in the baked 99-repo-bind.cnf drop-in.
install -d -o mysql -g mysql -m 0755 /var/run/mysqld
if command -v mariadbd-safe >/dev/null 2>&1; then
    mariadbd-safe --skip-syslog >/var/log/mariadb.log 2>&1 &
else
    mysqld_safe --skip-syslog >/var/log/mysql.log 2>&1 &
fi

# Wait until the server accepts connections.
for i in $(seq 1 60); do
    if mysqladmin ping --silent 2>/dev/null; then break; fi
    sleep 1
done

# ── 2) Provision DB + password auth on every root account ─────────────────────
# On a fresh datadir root@localhost uses unix_socket (no password); after we set
# a password it requires one. Pick whichever admin login works right now so this
# is idempotent across in-place restarts (not just fresh containers).
ADMIN=()
if mysql -uroot -e "SELECT 1" >/dev/null 2>&1; then
    ADMIN=(mysql -uroot)
elif mysql -uroot -p"$DB_ROOT_PW" -e "SELECT 1" >/dev/null 2>&1; then
    ADMIN=(mysql -uroot -p"$DB_ROOT_PW")
fi

"${ADMIN[@]}" <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '$DB_ROOT_PW';
CREATE USER IF NOT EXISTS 'root'@'%'         IDENTIFIED BY '$DB_ROOT_PW';
ALTER USER 'root'@'localhost'  IDENTIFIED BY '$DB_ROOT_PW';
ALTER USER 'root'@'127.0.0.1' IDENTIFIED BY '$DB_ROOT_PW';
ALTER USER 'root'@'%'         IDENTIFIED BY '$DB_ROOT_PW';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost'  WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%'          WITH GRANT OPTION;
FLUSH PRIVILEGES;
SQL

# ── 3) Seed schema + data (idempotent: Sequelize sync + admin/admin123) ───────
cd "$REPO/backend"
npm run seed || echo "[repo-host] seed returned non-zero (may already be seeded)"

# ── 3b) Blockchain demo setup: 2 centers + a minted item + on-chain mint so
#        POST /api/transfers (Soroban SFT transfer) works. Needs egress to testnet. ─
node scripts/ensure_transfer_demo.js || echo "[repo-host] demo setup returned non-zero (egress/testnet issue?)"

# ── 4) Backend (respawn on crash) ─────────────────────────────────────────────
mkdir -p "$REPO/backend/uploads"
(
    cd "$REPO/backend"
    while true; do
        node server.js
        echo "[backend] exited, restarting in 3s" >&2
        sleep 3
    done
) >/var/log/backend.log 2>&1 &

# Wait for the API health endpoint (max ~60s).
for i in $(seq 1 60); do
    if curl -sf --max-time 3 http://127.0.0.1:3001/api/health >/dev/null 2>&1; then
        echo "[repo-host] backend healthy"; break
    fi
    sleep 1
done

# ── 5) nginx (frontend + /api,/uploads proxy on :80) ──────────────────────────
nginx -t 2>/var/log/nginx-test.log || cat /var/log/nginx-test.log
nginx >/var/log/nginx-access.log 2>&1 || service nginx start || true

echo "[repo-host] stack up: http://<host>:80  api:3001  db:3306"
