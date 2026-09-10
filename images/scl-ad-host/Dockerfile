# syntax=docker/dockerfile:1
# SCL AD host (ad-server) — Samba AD domain controller.
#
# A host of type `ad-server` (HOST_TYPES in app.py) emulates a Windows AD domain
# controller. The single `samba` daemon serves DNS(:53) Kerberos(:88) MSRPC(:135)
# NetBIOS(:139) LDAP(:389) SMB(:445) kpasswd(:464) and the GC(:3268); sshd(:22) is
# also present. The domain is provisioned at FIRST BOOT by
# /usr/local/bin/ad-app-start.sh (idempotent, marker-gated), so the DC adapts to its
# runtime hostname/IP — no baked DB, no runtime egress. Recon reveals the services,
# accounts, and shares; the host exposes a non-trivial but crackable weakness.
#
# WHY 18.04 + SOURCE: this Samba version cannot build on a modern base — it needs
# gcc-7 + python2.7, only present on Ubuntu 18.04 (EOL, hence the old-releases
# mirror). The from-source recipe is proven; all samba paths land under
# /usr/local/samba (smb.conf at /usr/local/samba/etc/smb.conf, private DB at
# /usr/local/samba/private/, samba-tool/ldb*/smbclient under /usr/local/samba/bin).
FROM ubuntu:18.04
LABEL maintainer="StratoCyberLab Team"
LABEL description="SCL AD host — Samba AD DC (red-team engagement target)"

ENV DEBIAN_FRONTEND=noninteractive

# 18.04 is EOL and has been dropped from old-releases.ubuntu.com entirely, so
# redirect apt to the frozen snapshot archive instead (timestamp = bionic's
# final state). Path-inclusive patterns: sources.list lines carry /ubuntu and
# the snapshot layout nests the timestamp under it
# (snapshot.ubuntu.com/ubuntu/<TS>/dists/...).
RUN sed -i -e 's|http://archive.ubuntu.com/ubuntu|https://snapshot.ubuntu.com/ubuntu/20230601T000000Z|g' \
           -e 's|http://security.ubuntu.com/ubuntu|https://snapshot.ubuntu.com/ubuntu/20230601T000000Z|g' \
           /etc/apt/sources.list

# Build deps for Samba (era-matched: gcc-7, python2.7). AD-DC is enabled by default.
# openssh-server provides the sshd used by the foreground supervisor.
# The snapshot mirror is https-only but the base image ships no CA bundle:
# bootstrap with TLS verification relaxed JUST long enough to install
# ca-certificates, then re-verify normally (apt still checks the frozen
# Release signatures against the bionic keyring).
RUN printf 'Acquire::https::Verify-Peer "false";\nAcquire::https::Verify-Host "false";\n' \
        > /etc/apt/apt.conf.d/99-tls-bootstrap \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -f /etc/apt/apt.conf.d/99-tls-bootstrap \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc make pkg-config \
        python2.7 python2.7-dev python \
        perl flex bison \
        libgnutls28-dev libacl1-dev libattr1-dev \
        libpam0g-dev libarchive-dev zlib1g-dev libpopt-dev libbsd-dev libcap-dev \
        libreadline-dev libncurses5-dev \
        libjansson-dev liblmdb-dev \
        docbook-xsl xsltproc \
        wget ca-certificates \
        dnsutils iproute2 procps \
        openssh-server \
    && rm -rf /var/lib/apt/lists/*

# Fetch + extract the Samba source tarball.
WORKDIR /usr/src
RUN wget -q https://download.samba.org/pub/samba/stable/samba-4.6.3.tar.gz \
    && tar xzf samba-4.6.3.tar.gz

# Configure (AD-DC on; skip optional pam/cups/ldap/ads to trim deps). Install under
# /usr/local/samba so all samba paths are deterministic. Compile is the long step.
WORKDIR /usr/src/samba-4.6.3
RUN ./configure --without-pam --disable-cups --without-ldap --without-ads \
    --prefix=/usr/local/samba 2>&1 | tail -40 \
    && make -j"$(nproc)" 2>&1 | tail -40 \
    && make install 2>&1 | tail -40

# Expose the built binaries on PATH for the provision/start scripts.
ENV PATH=/usr/local/samba/bin:/usr/local/samba/sbin:$PATH

# Data blob seeded into a protected share at provisioning time.
COPY passwords.txt /srv/samlab/passwords.txt

# Provisioning (idempotent, marker-gated) + foreground supervisor. They bring up the
# domain, user accounts, shares, sshd config, and the win-condition artifact, then
# `exec samba -i` as PID 1.
COPY ad-provision.sh /usr/local/bin/ad-provision.sh
COPY ad-app-start.sh /usr/local/bin/ad-app-start.sh
RUN chmod +x /usr/local/bin/ad-provision.sh /usr/local/bin/ad-app-start.sh

# DNS Kerberos MSRPC NetBIOS LDAP SMB kpasswd GC + SSH.
EXPOSE 53 88 135 139 389 445 464 3268 22

WORKDIR /srv/samlab
