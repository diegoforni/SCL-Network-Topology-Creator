# syntax=docker/dockerfile:1
# SCL coder56-mcp host (KALI base) — coder56 (opencode) wired to HexStrike AI MCP,
# with the full pentest toolset installed so all 150 MCP tools actually EXECUTE.
#
# WHY KALI: HexStrike wraps 150+ external CLIs (nmap, sqlmap, metasploit, ghidra,
# zap, wpscan, ffuf, nuclei, ...). On the Ubuntu opencode base only a handful were
# installed, so most tools returned "binary not found". Kali-rolling ships nearly
# all of them via apt. To keep the proven opencode + coder56 + guardrail stack, we
# multi-stage COPY those artifacts out of the Ubuntu opencode image onto a Kali base
# (same linux/arm64; the opencode bun binary is forward-compatible with Kali's glibc).
#
# opencode+coder56+guardrail are NOT rebuilt here — they are reused verbatim from
# scl-plugin-network-topology-ubuntu-opencode:0.1, so this image inherits the exact,
# verified guardrail MCP gating (guardrail.ts) + coder56 agents + entrypoint.sh.

# --- stage 1: the proven Ubuntu opencode image (source of opencode/coder56/guardrail) ---
FROM scl-plugin-network-topology-ubuntu-opencode:0.1 AS oc

# --- stage 2: Kali base + reused artifacts + tools + HexStrike ---
FROM kalilinux/kali-rolling:latest
LABEL maintainer="StratoCyberLab Team"
LABEL description="SCL coder56-mcp host (Kali) — coder56 (opencode) + HexStrike AI MCP + full pentest toolset; MCP calls guardrail-gated"

ENV DEBIAN_FRONTEND=noninteractive

# Reuse the EXACT opencode + coder56 + guardrail artifacts from the verified image.
COPY --from=oc /root/.opencode /root/.opencode
COPY --from=oc /root/.config/opencode /root/.config/opencode
COPY --from=oc /opt/guardrail /opt/guardrail
COPY --from=oc /opt/shared /opt/shared
COPY --from=oc /usr/local/bin/entrypoint.sh /usr/local/bin/entrypoint.sh

# opencode on PATH (the entrypoint + opencode serve rely on it).
ENV PATH="/root/.opencode/bin:${PATH}"
RUN mkdir -p /root/.config/opencode/plugins /var/run/sshd /opt/agents \
 && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
 && chmod +x /usr/local/bin/entrypoint.sh \
 # labuser + sshd: entrypoint.sh does `chpasswd labuser` and starts sshd, assuming
 # the account exists (the ubuntu opencode Dockerfile creates it; that doesn't carry
 # over via COPY). Recreate it here or the entrypoint's chpasswd fails under set -e.
 && useradd -m -s /bin/bash labuser \
 && echo "labuser ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/labuser \
 && chmod 440 /etc/sudoers.d/labuser \
 && ssh-keygen -A 2>/dev/null || true

# The full pentest toolset (Kali apt). install-tools.sh is fault-tolerant — a
# missing package never aborts; gaps are reported in the final sweep.
COPY install-tools.sh /tmp/install-tools.sh
RUN chmod +x /tmp/install-tools.sh && /tmp/install-tools.sh && rm -f /tmp/install-tools.sh

# HexStrike backend + MCP bridge dependencies + vendored files.
WORKDIR /opt/hexstrike-ai
COPY hexstrike-requirements.txt /opt/hexstrike-ai/hexstrike-requirements.txt
# --ignore-installed: Kali's apt already provides flask/psutil/requests/aiohttp/bs4/
# selenium/mitmproxy as externally-managed packages without pip RECORDs, so a plain
# `pip install` aborts with uninstall-no-record-file. --ignore-installed lays the
# requested versions down without uninstalling the apt copies. Fallback to just `mcp`
# (FastMCP — the only dep NOT in apt) if the full set still conflicts.
RUN pip3 install --break-system-packages --ignore-installed --no-cache-dir \
        -r /opt/hexstrike-ai/hexstrike-requirements.txt \
    || pip3 install --break-system-packages --ignore-installed --no-cache-dir "mcp>=1.0,<2"
COPY hexstrike_server.py hexstrike_mcp.py /opt/hexstrike-ai/
RUN chmod +x /opt/hexstrike-ai/hexstrike_server.py /opt/hexstrike-ai/hexstrike_mcp.py \
 && grep -q 'app.run(host="0.0.0.0"' /opt/hexstrike-ai/hexstrike_server.py \
 && sed -i 's/app.run(host="0.0.0.0"/app.run(host="127.0.0.1"/' /opt/hexstrike-ai/hexstrike_server.py \
 && grep -q 'app.run(host="127.0.0.1"' /opt/hexstrike-ai/hexstrike_server.py
# Lazy-import mitmproxy: on Kali/Python 3.13, `import mitmproxy` -> passlib -> bcrypt 4.x
# crashes with "module bcrypt has no attribute __about__" (passlib is unmaintained).
# hexstrike doesn't need mitmproxy at startup (only the ~2 proxy tools use it), so make
# the import optional — the server boots; proxy tools no-op if mitmproxy is unusable.
RUN python3 - <<'PY'
p = "/opt/hexstrike-ai/hexstrike_server.py"
s = open(p).read()
old = ("import mitmproxy\n"
       "from mitmproxy import http as mitmhttp\n"
       "from mitmproxy.tools.dump import DumpMaster\n"
       "from mitmproxy.options import Options as MitmOptions")
new = ("try:\n"
       "    import mitmproxy\n"
       "    from mitmproxy import http as mitmhttp\n"
       "    from mitmproxy.tools.dump import DumpMaster\n"
       "    from mitmproxy.options import Options as MitmOptions\n"
       "except Exception as _mitmproxy_import_err:\n"
       "    mitmproxy = None; mitmhttp = None; DumpMaster = None; MitmOptions = None")
assert old in s, "mitmproxy import block not found"
open(p, "w").write(s.replace(old, new))
PY

# 4096 = opencode executor (internal-only). 8888 is loopback-only (NOT published).
EXPOSE 4096
WORKDIR /opt/agents

# Gap-fix (after the cached tool+hexstrike layers): install the 2 genuinely-missing
# binaries from the sweep (responder, falco) and wire the wordlist files hexstrike
# hard-codes (rockyou/dirb/api) to the installed seclists. Kept as the final layer so
# the big tool+hexstrike layers stay cached across this change.
COPY gap-fix.sh /tmp/gap-fix.sh
RUN chmod +x /tmp/gap-fix.sh && /tmp/gap-fix.sh && rm -f /tmp/gap-fix.sh

# IMPORTANT: must be declared explicitly — FROM kali resets image metadata, so the
# ubuntu opencode image's ENTRYPOINT does NOT carry over via COPY. Without this,
# the CMD (host initializer) runs as PID 1 and entrypoint.sh (SSH/guardrail/
# hexstrike/executor startup) never executes.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
