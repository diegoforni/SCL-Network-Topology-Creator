#!/bin/sh
# Topology-plugin container entrypoint. On every container start, kick off a
# background refresh of the NSG observed image variants (clone/fetch the
# latest upstream observer code, rebuild when it actually changed — see
# images/nsg-observer/build-observed-images.sh AUTO mode), then exec the
# plugin CMD. The API starts serving immediately; builds land minutes later
# and only affect topologies started AFTER they finish.
#
# Set NSG_AUTO_UPDATE=0 to skip the refresh entirely (e.g. air-gapped hosts).
set -eu

if [ "${NSG_AUTO_UPDATE:-1}" = "1" ] && [ -r /app/images/nsg-observer/build-observed-images.sh ]; then
    mkdir -p "${NSG_SRC_DIR:-/var/lib/nsg-src}"
    AUTO=1 NSG_SRC_DIR="${NSG_SRC_DIR:-/var/lib/nsg-src}" \
        nohup sh /app/images/nsg-observer/build-observed-images.sh \
        >>"${NSG_BOOTSTRAP_LOG:-/tmp/nsg-observer-bootstrap.log}" 2>&1 &
    echo "NSG observer image refresh started in background (log inside this container: ${NSG_BOOTSTRAP_LOG:-/tmp/nsg-observer-bootstrap.log})"
fi

exec "$@"
