FROM scl-plugin-network-topology-ubuntu:0.1

LABEL maintainer="StratoCyberLab Team"
LABEL description="SCL Network Topology Plugin with OpenCode support"

# Base image (scl-plugin-network-topology-ubuntu:0.1) is Ubuntu 24.04 and already
# provides bash, curl, sudo, openssh, python3, sqlite3. Ensure openssh-server/shadow
# are present (non-fatal: the foundation image already has most of these).
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server shadow \
    && rm -rf /var/lib/apt/lists/* 2>/dev/null || true

# Pin OpenCode to the plugin API version verified by this image. The installer
# supports --version; assert the resulting binary so a bad/stale download cannot
# silently produce a different runtime.
ARG OPENCODE_VERSION=1.18.3
RUN curl -fsSL https://opencode.ai/install | bash -s -- --version "${OPENCODE_VERSION}" \
    && test "$(/root/.opencode/bin/opencode --version)" = "${OPENCODE_VERSION}"

# Add OpenCode bin directory to PATH
ENV PATH="/root/.opencode/bin:${PATH}"

# Create required directories
RUN mkdir -p /root/.config/opencode \
    /root/.local/share/opencode \
    /opt/shared \
    /opt/agents \
    /var/log/agent \
    /secrets

# Create labuser for SSH access (used by entrypoint script) — Ubuntu uses useradd
RUN useradd -m -s /bin/bash labuser && \
    echo "labuser ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/labuser && \
    chmod 440 /etc/sudoers.d/labuser && \
    mkdir -p /var/run/sshd && \
    ssh-keygen -A

# Copy shared modules for OpenCode clients
COPY shared/ /opt/shared/

# Copy db_admin client for Trident agents
COPY db_admin_opencode_client.py /opt/agents/

# Install Python requests library for OpenCode HTTP API clients (Ubuntu pip)
RUN pip3 install --no-cache-dir requests --break-system-packages 2>/dev/null || \
    pip3 install --no-cache-dir requests || \
    (apt-get update && apt-get install -y --no-install-recommends python3-requests && rm -rf /var/lib/apt/lists/* 2>/dev/null) || true

# Create symlink for opencode
RUN ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode

# Copy OpenCode configuration template
COPY opencode.json /root/.config/opencode/opencode.json.template
# Also copy as actual config (will be overridden by env vars)
COPY opencode.json /root/.config/opencode/opencode.json
# Markdown agents (coder56_lead coordinator + coder56_phase worker). opencode loads
# ~/.config/opencode/agents/*.md alongside the JSON config, and these survive the
# runtime topology Cmd that overwrites opencode.json with a coder56-only config —
# so both topology-host and throwaway containers get the lead/phase agents from one
# source. (The native_subagents orchestration mode uses these.)
COPY agents/ /root/.config/opencode/agents/

# Global AGENTS.md — opencode auto-loads ~/.config/opencode/AGENTS.md every session
# (global rules). This is the agent's PERSISTENT long-term memory protocol: it points
# the agent at the host-backed /outputs/agent-memory/MEMORY.md (mounted RW, survives
# container teardown) and instructs it to read + append reusable findings there. The
# runtime topology Cmd only overwrites opencode.json, NOT this file, so it persists.
COPY AGENTS.md /root/.config/opencode/AGENTS.md

# Bake the briiirussell/cybersecurity-skills into the global OpenCode skills dir.
# These are Claude/opencode SKILL.md files; OpenCode discovers them on-demand via its
# native `skill` tool from ~/.config/opencode/skills/<name>/SKILL.md (HOME=/root here),
# so every agent on this serve (coder56, soc_god) can invoke them. Pinned to a commit
# for reproducibility; refresh by changing CS_SKILLS_REF below.
ARG CS_SKILLS_REF=c9ade0349f8ecd5c9c68a82a05d2b6012c5fa1d7
RUN mkdir -p /root/.config/opencode/skills \
    && curl -fsSL "https://github.com/briiirussell/cybersecurity-skills/archive/${CS_SKILLS_REF}.tar.gz" -o /tmp/cs.tgz \
    && tar -xzf /tmp/cs.tgz -C /tmp \
    && cp -r /tmp/cybersecurity-skills-*/skills/* /root/.config/opencode/skills/ \
    && rm -rf /tmp/cs.tgz /tmp/cybersecurity-skills-* \
    && echo "Installed $(find /root/.config/opencode/skills -name SKILL.md | wc -l) cybersecurity skills"

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Bake guardrail artifacts (global plugin + isolated guardrail config).
# Plugin lives at the canonical global-plugins path loaded by the executor opencode;
# guardrail config is staged under /opt/guardrail and copied into an isolated config dir at startup.
COPY guardrail/ /opt/guardrail/
RUN mkdir -p /root/.config/opencode/plugins

# Expose OpenCode HTTP API port (executor). Guardrail HTTP API (4097) is loopback-only and unpublished.
EXPOSE 4096

# Set working directory
WORKDIR /opt/agents

# Run entrypoint script
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
