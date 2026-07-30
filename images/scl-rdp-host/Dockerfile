# syntax=docker/dockerfile:1
# SCL RDP host (windows-client) — real RDP server + real PowerShell on Linux.
#
# A host of type `windows-client` (HOST_TYPES in app.py) emulates a Windows desktop
# client: a REAL RDP server (`xrdp`, which speaks the actual MS-RDP protocol so the
# same clients that hit Windows — xfreerdp, rdesktop, mstsc — hit this box too) plus
# Microsoft's REAL cross-platform PowerShell (`pwsh`). The first-boot supervisor
# /usr/local/bin/rdp-app-start.sh provisions the account + planted root SSH key
# (idempotent, marker-gated) and runs `xrdp --nodaemon` as PID 1.
#
# WHY xrdp + pwsh (not real Windows): a faithful Windows-RDP RCE (BlueKeep &c.) targets
# Microsoft's RDP stack and does not affect xrdp, and no public xrdp CVE grants a
# reliable remote shell (verified: CVE-2022-23468 is rated Confidentiality:None by the
# project). So the takeover here is a real, predictable weak RDP credential -> become
# the low-priv user -> read the planted root SSH key -> ssh in as root. See
# CLIENT1_HOST_DESIGN.md. The vuln is a misconfiguration, not a version, so a modern
# base is fine (no 18.04/Samba-from-source gymnastics).
FROM scl-plugin-network-topology-ubuntu:0.1
LABEL maintainer="StratoCyberLab Team"
LABEL description="SCL RDP host — xrdp + PowerShell (red-team engagement target / attacker foothold)"

ENV DEBIAN_FRONTEND=noninteractive

# xrdp (real RDP server) + a working session backend on both paths so an RDP login
# actually lands a desktop: xorgxrdp (Xorg backend) + tigervnc-standalone-server
# (Xvnc backend) + xfce4 (the desktop) + dbus-x11 (xfce needs it). netcat-openbsd
# drives the readiness healthcheck; openssh-server is the post-exploit pivot; openssl
# generates xrdp's TLS cert.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xrdp xorgxrdp \
        xfce4 xfce4-terminal dbus-x11 \
        tigervnc-standalone-server \
        netcat-openbsd openssh-server openssl libicu74 \
    && rm -rf /var/lib/apt/lists/*

# Microsoft PowerShell (pwsh) — real, cross-platform. The MS apt `powershell` package is
# amd64-only and has stale libicu/libssl deps on 24.04/arm64, so install the
# SELF-CONTAINED tarball instead (arch-aware). It bundles its .NET runtime; system
# libssl3 + libicu74 (installed above) are the only runtime deps.
ARG PWSH_VERSION=7.6.4
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) bits=x64;; \
      arm64|aarch64) bits=arm64;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1;; \
    esac; \
    curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell-${PWSH_VERSION}-linux-${bits}.tar.gz" -o /tmp/pwsh.tar.gz; \
    mkdir -p /opt/microsoft/powershell/7; \
    tar zxf /tmp/pwsh.tar.gz -C /opt/microsoft/powershell/7; \
    rm -f /tmp/pwsh.tar.gz; \
    chmod +x /opt/microsoft/powershell/7/pwsh; \
    ln -sf /opt/microsoft/powershell/7/pwsh /usr/bin/pwsh

# xrdp session backend: on login, start an xfce4 desktop. Replace the default
# startwm.sh with a deterministic known-good launcher (the stock one's tail varies
# across xrdp versions and can launch the wrong session).
RUN printf '%s\n' \
        '#!/bin/sh' \
        'if [ -r /etc/default/locale ]; then . /etc/default/locale; fi' \
        'export LANG LANGUAGE LC_CTYPE' \
        'exec startxfce4' \
    > /etc/xrdp/startwm.sh && chmod +x /etc/xrdp/startwm.sh

# xrdp crypto material: RSA keys for classic RDP security + a self-signed TLS cert for
# NLA/TLS clients (xfreerdp default). The package postinst usually makes these, but
# generate explicitly so xrdp starts even if postinst was skipped.
RUN xrdp-keygen xrdp /etc/xrdp/rsakeys.ini 2>/dev/null || true \
    && if [ ! -s /etc/xrdp/cert.pem ] || [ ! -s /etc/xrdp/key.pem ]; then \
         openssl req -x509 -newkey rsa:2048 -nodes \
           -keyout /etc/xrdp/key.pem -out /etc/xrdp/cert.pem -days 3650 \
           -subj "/CN=localhost" 2>/dev/null || true; \
       fi \
    && chmod 600 /etc/xrdp/key.pem

# Classic RDP security (security_layer=rdp) is REQUIRED for this host. Under NLA
# (negotiate, the xrdp default), RDP credential-testers like hydra FALSE-POSITIVE: the
# CredSSP/NLA exchange defeats reliable success/failure detection, so they report wrong
# passwords as valid — which would make the weak-credential exercise unsolvable
# correctly. Classic RDP security lets a cred-tester reliably distinguish the right
# password. Verified end-to-end (hydra finds Welcome1!, rejects others).
RUN sed -i 's/^security_layer=.*/security_layer=rdp/' /etc/xrdp/xrdp.ini \
    && sed -i 's/^crypt_level=.*/crypt_level=low/' /etc/xrdp/xrdp.ini \
    && grep -E '^(security_layer|crypt_level)=' /etc/xrdp/xrdp.ini

# Run-time provisioning (idempotent, marker-gated) + foreground supervisor. They bring
# up the weak-cred account, the planted root SSH key, the sshd config and the
# win-condition artifact, then `exec xrdp --nodaemon` as PID 1.
COPY rdp-provision.sh   /usr/local/bin/rdp-provision.sh
COPY rdp-app-start.sh   /usr/local/bin/rdp-app-start.sh
RUN chmod +x /usr/local/bin/rdp-provision.sh /usr/local/bin/rdp-app-start.sh

# RDP + SSH.
EXPOSE 3389 22

WORKDIR /srv/samlab
