#!/bin/bash
set -euo pipefail

: "${SSH_COMPROMISED_PASS:?SSH_COMPROMISED_PASS must be set}"
: "${SSH_COMPROMISED_USER:=labuser}"
: "${RUN_ID:=run_local}"

printf '%s:%s\n' "${SSH_COMPROMISED_USER}" "${SSH_COMPROMISED_PASS}" | chpasswd

mkdir -p /etc/sudoers.d
echo "labuser ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/labuser
chmod 440 /etc/sudoers.d/labuser
echo 'Defaults env_keep += "OPENCODE_API_KEY"' >/etc/sudoers.d/opencode_env
chmod 440 /etc/sudoers.d/opencode_env

# Create OpenCode auth.json with API key from environment
mkdir -p /root/.local/share/opencode
cat >/root/.local/share/opencode/auth.json <<EOF
{
    "e-infra-chat": {
        "type": "api",
        "key": "${OPENCODE_API_KEY:-}"
    }
}
EOF

# Ensure OpenCode is on PATH for all users/shells (copy binary out of /root)
if [ -x /root/.opencode/bin/opencode ]; then
    rm -f /usr/local/bin/opencode /usr/bin/opencode
    install -m 755 /root/.opencode/bin/opencode /usr/local/bin/opencode
    ln -sf /usr/local/bin/opencode /usr/bin/opencode
fi
cat >/etc/profile.d/opencode.sh <<'EOF'
export PATH="/usr/local/bin:/usr/bin:${PATH}"
EOF
chmod 644 /etc/profile.d/opencode.sh

# Ensure OPENCODE_API_KEY is available in SSH login shells
if [ -n "${OPENCODE_API_KEY:-}" ]; then
    cat >/etc/profile.d/opencode_env.sh <<EOF
export OPENCODE_API_KEY='${OPENCODE_API_KEY}'
EOF
    chmod 644 /etc/profile.d/opencode_env.sh
fi

# Mirror OpenCode auth/config for labuser SSH sessions
install -d -m 700 -o labuser -g labuser /home/labuser/.config/opencode /home/labuser/.local /home/labuser/.local/share /home/labuser/.local/share/opencode
install -d -m 700 -o labuser -g labuser /home/labuser/.local/state
chown -R labuser:labuser /home/labuser/.local
cat >/home/labuser/.local/share/opencode/auth.json <<EOF
{
    "e-infra-chat": {
        "type": "api",
        "key": "${OPENCODE_API_KEY:-}"
    }
}
EOF
chown labuser:labuser /home/labuser/.local/share/opencode/auth.json

# Copy OpenCode configuration (already has {env:OPENCODE_API_KEY} placeholder)
if [ -f /root/.config/opencode/opencode.json.template ]; then
    cp /root/.config/opencode/opencode.json.template /root/.config/opencode/opencode.json
    install -m 600 -o labuser -g labuser /root/.config/opencode/opencode.json.template /home/labuser/.config/opencode/opencode.json
fi

install -m 700 -o labuser -g labuser -d /home/labuser/.ssh

# First, install the key from the secrets volume if it exists
if [ -f /secrets/authorized_keys ]; then
    install -m 600 -o labuser -g labuser /secrets/authorized_keys /home/labuser/.ssh/authorized_keys
    echo "✓ SSH authorized_keys installed from /secrets/authorized_keys"
fi

# Setup SSH authorized_keys for auto_responder from shared volume
# The auto_responder_ssh_keys volume contains the public key that defender will use
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Copy the auto_responder public key to root's authorized_keys
if [ -f /root/.ssh_auto_responder/id_rsa_auto_responder.pub ]; then
    pub_key=$(cat /root/.ssh_auto_responder/id_rsa_auto_responder.pub)
    # Add key if not already present
    if ! grep -qxF "${pub_key}" /root/.ssh/authorized_keys 2>/dev/null; then
        echo "${pub_key}" >> /root/.ssh/authorized_keys
        echo "✓ Auto-responder SSH key installed for root"
    fi
fi

# Ensure the compromised host routes traffic through the router
ip route replace blackhole 172.30.0.254/32 || true
ip route replace blackhole 172.31.0.254/32 || true
ip route replace 172.31.0.0/24 via 172.30.0.1 || true
ip route replace default via 172.30.0.1 dev eth0 || true

# Enable bash history for labuser to track commands
echo 'HISTFILE=/home/labuser/.bash_history' >> /home/labuser/.bashrc
echo 'HISTSIZE=10000' >> /home/labuser/.bashrc
echo 'HISTFILESIZE=10000' >> /home/labuser/.bashrc
echo 'shopt -s histappend' >> /home/labuser/.bashrc
echo 'PROMPT_COMMAND="history -a"' >> /home/labuser/.bashrc
touch /home/labuser/.bash_history
chown labuser:labuser /home/labuser/.bash_history
chmod 600 /home/labuser/.bash_history

# Reduce synthetic SSH friction for autonomous experiments.
cat >/home/labuser/.ssh/config <<'EOF'
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
EOF
chown labuser:labuser /home/labuser/.ssh/config
chmod 600 /home/labuser/.ssh/config

# Start SSH server in background
/usr/sbin/sshd

# Re-assert default route in case container networking reset it.
ip route replace default via 172.30.0.1 dev eth0 || true

# --- GUARDRAIL runtime (Option B: second opencode serve on 127.0.0.1:4097) ---
# IMPORTANT: the guardrail runtime MUST be brought up BEFORE the executor opencode
# serve (4096) starts. opencode loads ~/.config/opencode/plugins/*.ts only at
# process startup; if the executor serve is launched first, the global plugin
# (guardrail.ts) is not yet in place, the custom "bash" forwarder never registers,
# and the whole guardrail mechanism is silently inert. So: place the plugin, start
# the 4097 serve, wait for readiness, and only THEN start the 4096 serve below.
#
# The executor (4096) loads the global plugin (guardrail.ts) which registers a custom
# "bash" tool that forwards non-trivial commands to the guardrail agent. The guardrail
# runs isolated (separate XDG_CONFIG_HOME/HOME) so it does NOT load the plugin and
# keeps its own real bash. Only enabled when GUARDRAIL_ENABLED=1.
GUARDRAIL_PID=""
if [[ "${GUARDRAIL_ENABLED:-0}" == "1" ]]; then
    echo "👁  GUARDRAIL_ENABLED=1: bringing up guardrail runtime..."

    # 1. Isolated config + home dirs, and the executor global plugin dir.
    mkdir -p /root/.guardrail-config /root/.guardrail-home /root/.config/opencode/plugins

    # 2. EXECUTOR plugin (loaded ONLY by the 4096 serve).
    #    /root/.guardrail-config must NOT contain a plugins/ dir (isolation):
    #    defensively purge any stray plugins dir so the guardrail can never load the
    #    executor plugin (and recurse into itself).
    rm -rf /root/.guardrail-config/plugins   # guarantee isolation
    mkdir -p /root/.guardrail-config
    if [[ -f /opt/guardrail/guardrail.ts ]]; then
        cp /opt/guardrail/guardrail.ts /root/.config/opencode/plugins/guardrail.ts
    fi
    if [[ -f /opt/guardrail/guardrail_client.ts ]]; then
        cp /opt/guardrail/guardrail_client.ts /root/.config/opencode/plugins/guardrail_client.ts
    fi
    if [[ -f /opt/guardrail/package.json ]]; then
        cp /opt/guardrail/package.json /root/.config/opencode/plugins/package.json
    fi

    # 3. Guardrail agent config (baked) -> where opencode actually reads it.
    #    opencode resolves its global config as $XDG_CONFIG_HOME/opencode/opencode.json
    #    (note the extra /opencode/ subdir) OR ~/.config/opencode/opencode.json (HOME-based).
    #    Place it in BOTH locations so the guardrail serve loads glm-5.2 + the guardrail agents
    #    regardless of which path opencode consults; otherwise it falls back to the built-in
    #    default provider (opencode-go / qwen*) which is unconfigured and fails every prompt.
    if [[ -f /opt/guardrail/opencode-guardrail.json ]]; then
        mkdir -p /root/.guardrail-config/opencode /root/.guardrail-home/.config/opencode
        cp /opt/guardrail/opencode-guardrail.json /root/.guardrail-config/opencode/opencode.json
        cp /opt/guardrail/opencode-guardrail.json /root/.guardrail-home/.config/opencode/opencode.json
    else
        echo "⚠️  /opt/guardrail/opencode-guardrail.json missing; guardrail will start without an agent config."
    fi

    # 4. Copy auth.json so the guardrail can call the LLM.
    mkdir -p /root/.guardrail-home/.local/share/opencode
    if [[ -f /root/.local/share/opencode/auth.json ]]; then
        cp /root/.local/share/opencode/auth.json /root/.guardrail-home/.local/share/opencode/auth.json
    fi

    # 5. Launch the guardrail opencode serve on loopback (NOT published).
    guardrail_log="/var/log/opencode-guardrail-serve.log"
    touch "${guardrail_log}"
    echo "Starting guardrail OpenCode serve on 127.0.0.1:4097..."
    HOME=/root/.guardrail-home XDG_CONFIG_HOME=/root/.guardrail-config \
        opencode serve --hostname 127.0.0.1 --port 4097 >>"${guardrail_log}" 2>&1 &
    GUARDRAIL_PID=$!
    echo "✅ Guardrail serve started (PID ${GUARDRAIL_PID})"

    # Poll readiness up to ~30s. Use explicit curl timeouts so a slow-starting
    # guardrail can never hang the entrypoint indefinitely.
    for i in $(seq 1 60); do
        if curl -sf --connect-timeout 2 --max-time 3 "http://127.0.0.1:4097/global/health" >/dev/null 2>&1; then
            echo "✅ Guardrail serve ready (127.0.0.1:4097) after ${i}*0.5s"
            break
        fi
        sleep 0.5
    done
    if ! curl -sf --connect-timeout 2 --max-time 3 "http://127.0.0.1:4097/global/health" >/dev/null 2>&1; then
        echo "⚠️  Guardrail serve did NOT become ready on 127.0.0.1:4097 within 30s (see ${guardrail_log}). Executor plugin will fail-safe (refuse+escalate)."
    fi
else
    echo "GUARDRAIL_ENABLED!=1 (='${GUARDRAIL_ENABLED:-<unset>}'); guardrail runtime skipped."
fi

# Start OpenCode HTTP server for remote API access (used by auto_responder).
# This runs AFTER the guardrail runtime above so the executor process picks up the
# global guardrail plugin (guardrail.ts) from ~/.config/opencode/plugins at startup.
opencode_log="/var/log/opencode-serve.log"
touch "${opencode_log}"
echo "Starting OpenCode HTTP server on 0.0.0.0:4096..."
cd /tmp && opencode serve --hostname 0.0.0.0 --port 4096 >>"${opencode_log}" 2>&1 &
OPENCODE_PID=$!
echo "✅ OpenCode serve started (PID ${OPENCODE_PID})"

# Run the compose-supplied command if present. The Dockerfile ENTRYPOINT is
# /usr/local/bin/entrypoint.sh (this script) and compose only sets `command:`
# (no entrypoint: override), so the command arrives as "$@". Earlier this script
# ended with `exec tail -f /dev/null` and dropped "$@" on the floor, which meant
# the host_script + opencode_agent_block (which writes opencode.json with
# tools.bash:false for guarded hosts) NEVER ran. We now invoke the passed command
# in the background and block on wait so PID 1 stays alive. When no command is
# supplied we idle on `tail -f /dev/null` for backward compatibility.
AGENT_SCRIPT_PID=""
if [ $# -gt 0 ]; then
    "$@" &
    AGENT_SCRIPT_PID=$!
fi

# Cleanup trap: kill both serves (and the agent script) on EXIT/INT/TERM.
cleanup() {
    echo "Cleaning up background processes..."
    if [[ -n "${AGENT_SCRIPT_PID}" ]] && kill -0 "${AGENT_SCRIPT_PID}" >/dev/null 2>&1; then
        kill "${AGENT_SCRIPT_PID}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${GUARDRAIL_PID}" ]] && kill -0 "${GUARDRAIL_PID}" >/dev/null 2>&1; then
        kill "${GUARDRAIL_PID}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${OPENCODE_PID}" ]] && kill -0 "${OPENCODE_PID}" >/dev/null 2>&1; then
        kill "${OPENCODE_PID}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

# Block until a child exits. If no command was passed we idle on tail so the
# container stays up (backward-compatible with manual/interactive runs).
if [ -n "${AGENT_SCRIPT_PID}" ]; then
    wait
else
    exec tail -f /dev/null
fi
