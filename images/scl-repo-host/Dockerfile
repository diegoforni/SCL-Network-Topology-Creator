# syntax=docker/dockerfile:1
# Full-stack "Acción del Sur" host image for the SCL network-topology plugin.
#
# A host of type `repo-server` (see HOST_TYPES in app.py) runs the entire app:
# the Node/Express + Sequelize API (:3001), a MariaDB database (:3306), and an
# nginx front door on :80 serving the built React/Vite frontend with /api + /uploads
# reverse-proxied to the backend — so the whole app is reachable same-origin from
# any host in the topology.
#
# The repo is cloned at BUILD time by the Docker daemon (host egress), so the
# topology host needs no runtime internet and the third-party repo is never
# vendored into this plugin's git tree. Backend secrets live only in the baked,
# git-ignored backend.env → /srv/repo/backend/.env (never pushed).

ARG REPO_HOST_URL=https://github.com/JuanLoncharich/accion_del_sur

FROM scl-plugin-network-topology-ubuntu:0.1

ARG REPO_HOST_URL
ENV DEBIAN_FRONTEND=noninteractive

# ── System deps: git (clone), MariaDB (DB), nginx (front door) ─────────────────
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        mariadb-server \
        mariadb-client \
        nginx \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 20 LTS (for Vite + Tailwind v4 the frontend needs Node >= 20) ─────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node -v && npm -v

# ── Clone the repo (shallow, single branch) and drop .git ─────────────────────
RUN git clone --depth 1 --single-branch "${REPO_HOST_URL}" /srv/repo \
    && rm -rf /srv/repo/.git

# ── Bake backend runtime config (git-ignored source file; lives only in image) ─
COPY backend.env /srv/repo/backend/.env

# ── Install backend deps ──────────────────────────────────────────────────────
RUN cd /srv/repo/backend && npm install --no-audit --no-fund

# ── Build the frontend (Vite build → frontend/dist) ───────────────────────────
RUN cd /srv/repo/frontend && npm install --no-audit --no-fund \
    && npm run build \
    && test -f /srv/repo/frontend/dist/index.html

# ── nginx: enable our site, drop the default "Welcome to nginx" (port clash) ──
COPY nginx-repo.conf /etc/nginx/sites-available/repo
RUN rm -f /etc/nginx/sites-enabled/default \
    && ln -sf /etc/nginx/sites-available/repo /etc/nginx/sites-enabled/repo

# ── MariaDB: bind on all interfaces + skip-name-resolve so TCP clients match the
#    'root'@'127.0.0.1'/'root'@'%' accounts (otherwise 127.0.0.1 reverse-DNSes to
#    "localhost" and hits the unix_socket-authenticated root@localhost). ─────────
RUN printf '[mysqld]\nbind-address = 0.0.0.0\nskip-name-resolve = 1\n' > /etc/mysql/mariadb.conf.d/99-repo-bind.cnf

# ── Supervisor: MariaDB -> seed -> backend -> nginx ───────────────────────────
COPY repo-app-start.sh /usr/local/bin/repo-app-start.sh
RUN chmod +x /usr/local/bin/repo-app-start.sh

# ── Idempotent blockchain demo setup: ensures 2 centers + a minted item + an
#    on-chain mint so POST /api/transfers (Soroban SFT transfer) works on boot. ─
COPY ensure_transfer_demo.js /srv/repo/backend/scripts/ensure_transfer_demo.js

EXPOSE 80 3001 3306
WORKDIR /srv/repo
