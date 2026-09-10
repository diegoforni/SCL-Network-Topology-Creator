# NSG observer packaging, maintained in THIS repo.
#
# Built by images/nsg-observer/build-observed-images.sh as:
#   docker build -f <this file> --build-arg BASE_IMAGE=<scl-image> \
#     -t <scl-image>-observed:<tag> <fresh clone of NSG-docker-state-creator>
# The build CONTEXT is a fresh clone of
# https://github.com/stratosphereips/NSG-docker-state-creator — upstream
# supplies the observer/ and examples/ trees; everything distro-specific and
# SCL-specific (package fallback for old bases, Zeek opt-out, side-by-side
# interpreter for python<3.7 bases) is maintained here so upstream changes
# never need patch re-application. Upstream's own Dockerfile is not used.
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

USER root

ARG DEBIAN_FRONTEND=noninteractive
ARG ZEEK_REPOSITORY=xUbuntu_24.04
# INSTALL_ZEEK=0 skips the openSUSE Zeek repo + zeek package entirely (for
# arches the repo does not publish, e.g. arm64, or non-Ubuntu bases like
# Kali). Runtime collectors degrade gracefully via OBS_ENABLE_ZEEK=0.
ARG INSTALL_ZEEK=1
# Package set for the collectors. On older bases (e.g. Ubuntu bionic 18.04,
# used by some SCL ad-server images) libbpf-tools/tlog/bpfcc-tools do not
# exist; the install falls back to only the packages the distro actually
# has (the eBPF/tlog collectors are runtime-disabled in those deployments
# anyway via OBS_ENABLE_BCC=0).
ARG OBS_APT_PACKAGES="acct bpfcc-tools ca-certificates curl gnupg inotify-tools iptables iproute2 iputils-ping jq libbpf-tools libcap2-bin lsof netcat-openbsd nftables procps python3 python3-bpfcc strace tcpdump tlog util-linux zstd"

RUN set -eu \
    && apt-get update \
    && if ! apt-get install -y --no-install-recommends ${OBS_APT_PACKAGES}; then \
           avail="$(apt-cache show ${OBS_APT_PACKAGES} 2>/dev/null | awk '/^Package:/{print $2}' | sort -u)"; \
           echo "fallback: installing available subset: ${avail}"; \
           test -n "${avail}"; \
           apt-get install -y --no-install-recommends ${avail}; \
       fi \
    && if [ "${INSTALL_ZEEK}" = "1" ]; then \
        curl -fsSL "https://download.opensuse.org/repositories/security:/zeek/${ZEEK_REPOSITORY}/Release.key" \
       | gpg --dearmor -o /usr/share/keyrings/security-zeek.gpg \
        && echo "deb [signed-by=/usr/share/keyrings/security-zeek.gpg] https://download.opensuse.org/repositories/security:/zeek/${ZEEK_REPOSITORY}/ /" \
       > /etc/apt/sources.list.d/security-zeek.list \
        && apt-get update \
        && apt-get install -y --no-install-recommends zeek; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Some hosts intentionally block outbound container traffic on port 80. Keep
# runtime APT operations on HTTPS after ca-certificates has been installed.
RUN find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) \
    -exec sed -i \
      -e 's|http://archive.ubuntu.com|https://archive.ubuntu.com|g' \
      -e 's|http://security.ubuntu.com|https://security.ubuntu.com|g' \
      -e 's|http://ports.ubuntu.com|https://ports.ubuntu.com|g' {} +

COPY observer /opt/nsg-observer
COPY examples /opt/nsg-observer/examples

RUN chmod +x /opt/nsg-observer/bin/* /opt/nsg-observer/examples/*.sh \
    && mkdir -p /observation \
    && if [ -x /opt/zeek/bin/zeek ]; then ln -sf /opt/zeek/bin/zeek /usr/local/bin/zeek; fi \
    && printf '\n# NSG observer command logging\n[ -r /opt/nsg-observer/etc/bash-observer.sh ] && . /opt/nsg-observer/etc/bash-observer.sh\n' \
       >> /etc/bash.bashrc

# Side-by-side interpreter for OLD bases whose system python is below the
# observer's 3.7+ requirement (e.g. Ubuntu bionic = 3.6, used by Samba AD
# images whose tooling is compiled against the system python). The observer
# bin/* shebangs are rewritten to this interpreter; system python3, PATH and
# the workload stay untouched. Empty (default) keeps `python3` from the env.
# Use a python-build-standalone install_only build matching the base arch and
# glibc (aarch64-unknown-linux-gnu requires glibc >= 2.17; bionic has 2.27).
ARG OBS_PYTHON_URL=""
RUN if [ -n "${OBS_PYTHON_URL}" ]; then \
        mkdir -p /opt/python3-obs \
        && curl -fsSL "${OBS_PYTHON_URL}" | tar -xz -C /opt/python3-obs --strip-components=1 \
        && PYBIN=/opt/python3-obs/bin/python3 \
        && test -x "${PYBIN}" \
        && "${PYBIN}" -c 'import sys; assert sys.version_info >= (3, 8), sys.version' \
        && for f in /opt/nsg-observer/bin/*; do \
               if head -n 1 "$f" | grep -q '^#!'; then sed -i "1s|.*|#!${PYBIN}|" "$f"; fi; \
           done \
        && "${PYBIN}" -c 'import json,logging,subprocess,hashlib,pathlib,socket' \
        && echo "observer interpreter: ${PYBIN}"; \
    fi

ENV PATH="/opt/zeek/bin:/opt/nsg-observer/bin:${PATH}" \
    OBS_OUTPUT_DIR=/observation \
    OBS_ENABLE_BCC=1 \
    OBS_ENABLE_FILE_EVENTS=1 \
    OBS_ENABLE_NETWORK_TOPOLOGY=1 \
    OBS_ENABLE_PCAP=1 \
    OBS_ENABLE_PROCESS_SNAPSHOTS=1 \
    OBS_ENABLE_SOCKET_SNAPSHOTS=1 \
    OBS_ENABLE_STATE_RECONCILIATION=1 \
    OBS_ENABLE_STATE_GRAPH=1 \
    OBS_ENABLE_TRAJECTORY=1 \
    OBS_ENABLE_STRACE=1 \
    OBS_ENABLE_TTY_RECORDING=1 \
    OBS_ENABLE_ZEEK=1 \
    OBS_PROCESS_INTERVAL=0.10 \
    OBS_SOCKET_INTERVAL=1.0 \
    OBS_NETWORK_INTERVAL=10 \
    OBS_SNAPSHOT_INTERVAL=30 \
    OBS_STATE_INTERVAL=10 \
    OBS_STATE_LEVEL=operational \
    OBS_TRAJECTORY_SENSITIVITY=material \
    OBS_TRAJECTORY_SETTLE_SECONDS=2 \
    OBS_PCAP_FILE_MB=100 \
    OBS_PCAP_FILE_COUNT=10

VOLUME ["/observation"]

ENTRYPOINT ["/opt/nsg-observer/bin/observe-entrypoint"]
CMD ["bash", "-lc", "while true; do sleep 3600; done"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/opt/nsg-observer/bin/observer-healthcheck"]
