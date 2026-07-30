# syntax=docker/dockerfile:1
# SCL web host (vuln-web-server) — lighttpd + SSH, carrying Shellshock (CVE-2014-6271).
#
# A host of type `vuln-web-server` (HOST_TYPES in app.py) runs lighttpd (:80, static
# site + a /cgi-bin/status.sh CGI) and sshd (:22, bash shell). The takeover vuln is
# Shellshock: the CGI interpreter is a deliberately-vulnerable OLD bash (4.3 base,
# patch-level-0 — released Feb 2014, BEFORE the Sept-2014 Shellshock fix). lighttpd's
# mod_cgi exports HTTP headers as HTTP_* env vars, which vulnerable bash parses at
# startup, so a single crafted header yields direct RCE as the web user. The system
# /bin/bash is left untouched; only the CGI interpreter is exploitable.
#
# Provisioning (weak SSH cred, flag, sshd config) runs at FIRST BOOT via
# /usr/local/bin/web-app-start.sh (idempotent, marker-gated), so the image stays
# reusable across hostnames/IPs and ships no build-time secrets.

FROM scl-plugin-network-topology-ubuntu:0.1
LABEL maintainer="StratoCyberLab Team"
LABEL description="SCL web host — lighttpd + SSH with Shellshock RCE (red-team engagement target)"

ENV DEBIAN_FRONTEND=noninteractive

# ── Runtime deps: lighttpd (HTTP) + openssh-server (SSH) + netcat (healthcheck) +
#    curl (smoke), plus build tooling to compile the vulnerable bash from source. ──
RUN apt-get update && apt-get install -y --no-install-recommends \
        lighttpd \
        openssh-server \
        netcat-openbsd \
        curl \
        build-essential gcc make wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Build a Shellshock-VULNERABLE bash (4.3 base / patch-level-0). ────────────────
# GNU bash tarballs are patch-level-0 for their minor version; the Shellshock fix for
# 4.3 only exists as patch bash43-025+, which the base tarball does NOT include — so
# 4.3.tar.gz is vulnerable to CVE-2014-6271 AND CVE-2014-7169. (Do NOT use a high
# patch-level 3.2/4.x tarball: those are Shellshock-patched.) Installed under its own
# prefix and symlinked to /usr/local/bin/bash-vuln; /bin/bash is left untouched.
# CFLAGS=-fcommon defends against the gcc-10+ default -fno-common multiple-definition
# linker errors that old C codebases (bash 4.3) can trip.
WORKDIR /usr/src
RUN wget -q https://ftp.gnu.org/gnu/bash/bash-4.3.tar.gz \
    && tar xzf bash-4.3.tar.gz \
    && cd bash-4.3 \
    && export CFLAGS='-fcommon' \
    && ./configure --prefix=/usr/local/bash-vuln 2>&1 | tail -15 \
    && make -j"$(nproc)" 2>&1 | tail -15 \
    && make install 2>&1 | tail -15 \
    && ln -sf /usr/local/bash-vuln/bin/bash /usr/local/bin/bash-vuln \
    && /usr/local/bin/bash-vuln --version | head -1 \
    && rm -rf /usr/src/bash-4.3 /usr/src/bash-4.3.tar.gz

# ── Build-time assertion: the compiled bash MUST be Shellshock-vulnerable. ─────────
# If someone swaps in a patched tarball, the injected `() { :;}; ...` is NOT executed
# and grep finds nothing → build fails loudly instead of shipping a non-exploitable host.
RUN if env 'x=() { :;}; echo SHELLSHOCK_CONFIRMED' /usr/local/bin/bash-vuln -c ':' 2>/dev/null \
        | grep -q SHELLSHOCK_CONFIRMED; then \
        echo "bash-vuln is Shellshock-vulnerable (CVE-2014-6271) ✓"; \
    else \
        echo "ERROR: /usr/local/bin/bash-vuln is NOT Shellshock-vulnerable (patched tarball?)"; \
        exit 1; \
    fi

# ── Web content: a plausible "SCL Systems" status page + the benign status CGI. ────
# status.sh is the VEHICLE (it prints uptime); the RCE is the vulnerable interpreter.
# The index links /cgi-bin/status.sh so the CGI surface is discoverable by recon.
COPY www/ /var/www/scl/
RUN chmod 0755 /var/www/scl && chmod 0755 /var/www/scl/cgi-bin \
    && chmod 0755 /var/www/scl/cgi-bin/status.sh

# ── lighttpd config (standalone; invoked directly by the supervisor). ─────────────
COPY scl-lighttpd.conf /etc/lighttpd/scl-lighttpd.conf

# ── Provisioning (idempotent, marker-gated) + foreground supervisor. ──────────────
COPY web-provision.sh /usr/local/bin/web-provision.sh
COPY web-app-start.sh /usr/local/bin/web-app-start.sh
RUN chmod +x /usr/local/bin/web-provision.sh /usr/local/bin/web-app-start.sh

EXPOSE 80 22
WORKDIR /srv/samlab
