#!/bin/sh
# Build the NSG-observed image variants for the SCL preset topologies,
# always from the LATEST upstream observer code.
#
# Each run refreshes a clone of stratosphereips/NSG-docker-state-creator
# (NSG_SRC_DIR, default: /opt/Agents/NSG-docker-state-creator when /opt/Agents
# exists, else ~/.local/share/nsg-docker-state-creator), then builds every
# missing variant with THIS repo's images/nsg-observer/Dockerfile using the
# fresh clone as build context (upstream supplies observer/ + examples/;
# distro/SCL packaging lives in this repo — no patch re-application).
#
# FORCE=1  rebuild all observed variants even if present. Stop + start a
#          running topology afterwards to pick the new images up (the next
#          start writes evidence to a new timestamped run dir).
# AUTO=1   rebuild all variants when the upstream observer/ tree CONTENT
#          changed since the last successful build (tree-hash stamp kept
#          next to the clone), otherwise only build missing images. This is
#          the mode the topology plugin runs at every container start
#          (images/nsg-observer/plugin-entrypoint.sh), so a plain
#          `docker compose up` always ends with observed images built from
#          the latest upstream observer code.
#
# Kilimanjaro (everything runs as the agent user, uid 1013):
#   docker run --rm --network=host \
#     -v /var/run/docker.sock:/var/run/docker.sock -v /opt/Agents:/opt/Agents \
#     alpine sh -c 'apk add -q git docker-cli su-exec >/dev/null &&
#       su-exec 1013:984 sh /opt/Agents/stratocyberlab/plugins/SCL-Network-Topology-Creator/images/nsg-observer/build-observed-images.sh'
# (first use: chown -R 1013:1012 "$NSG_SRC_DIR" so the agent user can refresh it)
set -eu

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
UPSTREAM=${NSG_UPSTREAM:-https://github.com/stratosphereips/NSG-docker-state-creator.git}
if [ -n "${NSG_SRC_DIR:-}" ]; then
    SRC=$NSG_SRC_DIR
elif [ -d /opt/Agents ]; then
    SRC=/opt/Agents/NSG-docker-state-creator
else
    SRC=$HOME/.local/share/nsg-docker-state-creator
fi
DFILE=$REPO_DIR/images/nsg-observer/Dockerfile

# Side-by-side interpreter for old bases (ad-server = bionic python3.6).
case "$(uname -m)" in
    x86_64)        PY_ARCH=x86_64-unknown-linux-gnu ;;
    aarch64|arm64) PY_ARCH=aarch64-unknown-linux-gnu ;;
    *)             PY_ARCH="" ;;
esac
PY_TAG=20260901; PY_VER=cpython-3.11.16
PYURL=${OBS_PYTHON_URL:-"https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_VER}%2B${PY_TAG}-${PY_ARCH}-install_only.tar.gz"}

STAMP="${NSG_STAMP:-$SRC/.observed-built-stamp}"

# Refresh the source clone. Prefers plain git (fetch + reset); when this
# container has no route to the upstream (filtered egress, e.g. the topology
# plugin on a firewalled bridge), clones via a HOST-NETWORK helper container
# and streams the tree back with docker cp — needs no host-path knowledge.
# NSG_FORCE_HELPER=1 forces the helper path (for testing).
# Empty $SRC in place — it may be a volume MOUNTPOINT (the plugin mounts the
# nsg-src named volume at /var/lib/nsg-src), which cannot be removed itself.
clear_src() {
    find "$SRC" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
}
fetch_src() {
    if [ "${NSG_FORCE_HELPER:-0}" != "1" ]; then
        if [ -d "$SRC/.git" ] && git -C "$SRC" fetch origin; then
            git -C "$SRC" reset --hard FETCH_HEAD
            return 0
        fi
        if timeout 15 git ls-remote "$UPSTREAM" HEAD >/dev/null 2>&1; then
            clear_src
            git clone "$UPSTREAM" "$SRC"
            return $?
        fi
    fi
    echo "no direct route to $UPSTREAM - cloning via host-network helper container"
    tmp=$SRC.tmp
    rm -rf "$tmp"; mkdir -p "$tmp"
    cid=$(docker run -d --network host --entrypoint sh alpine/git \
              -c "git clone --depth 1 '$UPSTREAM' /src") || return 1
    while [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" = "true" ]; do
        sleep 2
    done
    if [ "$(docker inspect -f '{{.State.ExitCode}}' "$cid" 2>/dev/null)" = "0" ]; then
        docker cp "$cid:/src/." "$tmp/" \
            && clear_src && cp -a "$tmp"/. "$SRC"/ && rm -rf "$tmp" \
            || { rm -rf "$tmp"; docker rm -f "$cid" >/dev/null 2>&1; return 1; }
    else
        echo "helper clone failed:"
        docker logs --tail 5 "$cid" 2>&1 || true
        rm -rf "$tmp"
        docker rm -f "$cid" >/dev/null 2>&1
        return 1
    fi
    docker rm -f "$cid" >/dev/null 2>&1
}

echo "== refresh NSG source: $SRC =="
# Preserve the change-detection stamp across the helper swap below.
OLDSTAMP="$(cat "$STAMP" 2>/dev/null || true)"
fetch_src
if [ -n "$OLDSTAMP" ] && [ ! -f "$STAMP" ]; then
    echo "$OLDSTAMP" > "$STAMP" || true
fi
echo "upstream HEAD: $(git -C "$SRC" rev-parse --short HEAD)"

# Change detection for AUTO mode: hash of the observer/ + examples/ trees the
# Dockerfile actually COPYs. Any commit elsewhere in the repo is a no-op.
OBSTREE="$(git -C "$SRC" rev-parse HEAD:observer)-$(git -C "$SRC" rev-parse HEAD:examples 2>/dev/null || echo none)"
if [ "${FORCE:-0}" != "1" ] && [ "${AUTO:-0}" = "1" ]; then
    if [ "$(cat "$STAMP" 2>/dev/null || true)" != "$OBSTREE" ]; then
        echo "AUTO: observer tree changed since last build (stamp -> $OBSTREE); rebuilding all variants"
        FORCE=1
    else
        echo "AUTO: observer tree unchanged ($(cat "$STAMP")); building missing images only"
    fi
fi

# Base images are builds of this repo's images/ dirs; build when missing.
for name in scl-smb-server scl-db-host scl-web-host scl-ad-host scl-rdp-host; do
    img=$name:0.1
    if docker image inspect "$img" >/dev/null 2>&1; then
        echo "base exists: $img"
    else
        echo "building base: $img"
        docker build --network=host -t "$img" "$REPO_DIR/images/$name"
    fi
done

build_observed() {
    img=$1; shift
    repo=${img%:*}; tag=${img#*:}; obs=$repo-observed:$tag
    if [ "${FORCE:-0}" = "1" ]; then
        docker image rm -f "$obs" >/dev/null 2>&1 || true
    fi
    if docker image inspect "$obs" >/dev/null 2>&1; then
        echo "exists: $obs"
        return 0
    fi
    echo "building: $obs"
    docker build --network=host -f "$DFILE" \
        --build-arg INSTALL_ZEEK=0 --build-arg BASE_IMAGE="$img" "$@" \
        -t "$obs" "$SRC"
}

for img in scl-plugin-network-topology-ubuntu:0.1 scl-smb-server:0.1 \
           scl-db-host:0.1 scl-web-host:0.1 scl-rdp-host:0.1 \
           ubuntu-24.04-opencode:0.1; do
    build_observed "$img"
done
# ad-server: bionic base, needs the side-by-side interpreter (arch-detected).
build_observed scl-ad-host:0.1 --build-arg OBS_PYTHON_URL="$PYURL"

echo "$OBSTREE" > "$STAMP" 2>/dev/null || echo "warning: cannot write stamp $STAMP"

echo "== DONE =="
docker images --format '{{.Repository}}:{{.Tag}}' | grep -- '-observed:0.1' | sort
