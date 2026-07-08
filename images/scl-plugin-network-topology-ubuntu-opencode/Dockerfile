FROM scl-plugin-network-topology-ubuntu:0.1

LABEL maintainer="StratoCyberLab Team"
LABEL description="SCL Network Topology Plugin with OpenCode support"

# Base image (scl-plugin-network-topology-ubuntu:0.1) is Ubuntu 24.04 and already
# provides bash, curl, sudo, openssh, python3, sqlite3. Ensure openssh-server/shadow
# are present (non-fatal: the foundation image already has most of these).
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server shadow \
    && rm -rf /var/lib/apt/lists/* 2>/dev/null || true

# Install OpenCode
RUN curl -fsSL https://opencode.ai/install | bash

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
