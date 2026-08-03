# syntax=docker/dockerfile:1
# `smb-server` host image for the SCL network-topology plugin (see HOST_TYPES
# in app.py). Deliberately-vulnerable SMB file share: Samba installed here (at
# BUILD time, host-side internet) because the host typically runs on a
# non-internet-enabled topology subnet with no runtime egress to reach apt.
#
# Vulnerability model: anonymous/guest access to a world-writable share — no
# credentials, no exploit code needed, matches NetSecGame's own "no
# ExploitConfig, plain access" pattern for every non-SMB-CVE service in its
# scenarios. Mirrors NetSecGame's smb_server private-data set (3 files, 2
# distinct simulated owners).

FROM scl-plugin-network-topology-ubuntu:0.1

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends samba \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/log/samba /var/lib/samba/private /run/samba

# Deliberately insecure smb.conf: guest-only security, no password required,
# the [share] is world-writable and browsable by anyone.
COPY smb.conf /etc/samba/smb.conf

# Seeded private data (matches NetSecGame's smb_server: 3 items, 2 owners).
RUN mkdir -p /srv/samba/share
COPY DataFromServer1.txt Data2FromServer1.txt Data3FromServer1.txt /srv/samba/share/
RUN chmod -R 0777 /srv/samba/share

EXPOSE 445 139
