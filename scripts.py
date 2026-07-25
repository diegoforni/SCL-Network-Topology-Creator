import json

import app


def ssh_setup_block(username, password):
    return f"""mkdir -p /var/run/sshd
useradd -m -s /bin/bash {app.shell_quote(username)} 2>/dev/null || true
echo {app.shell_quote(username + ':' + password)} | chpasswd || true
/usr/sbin/sshd || true
"""


def opencode_agent_block(host, topology):
    """Generate the OpenCode agent initialization block for a host."""
    agents = app.host_agents(host)
    print(f"🔍 opencode_agent_block: host={host.get('name')}, agents={agents}, generate_opencode_config={app.generate_opencode_config is not None}")
    if not agents or not app.generate_opencode_config:
        print(f"⚠️ opencode_agent_block returning empty: agents={agents}, generate_opencode_config={app.generate_opencode_config}")
        return ''

    # Generate agent configurations for all agents
    agent_configs = {}
    for agent_type in agents:
        try:
            agent_config = app.generate_opencode_config(agent_type)
            agent_configs[agent_type] = agent_config.get('system', {}).get('prompt', 'You are a helpful assistant.')
            print(f"✅ Generated config for agent {agent_type}")
        except (ValueError, KeyError) as e:
            agent_configs[agent_type] = f'# Error generating config for {agent_type}: {e}'
            print(f"❌ Error generating config for {agent_type}: {e}")

    # Guardrail: guarded agents keep the built-in bash exposed. The executor-side
    # plugin intercepts it through tool.execute.before and delegates adjudication
    # to the guardrail agent on 127.0.0.1:4097.
    member_guarded = any(a in app.GUARDED_AGENTS for a in agents)
    # Honor an explicit per-host guardrail_enabled flag (frontend toggle);
    # absent (None) => auto (armed iff a guarded agent is present on the host).
    guarded = host.get('guardrail_enabled') if host.get('guardrail_enabled') is not None else member_guarded
    # tools_block is injected as a kwarg VALUE into the .format() template, so it
    # must use single braces (kwarg values are NOT re-escaped by .format). It is
    # placed at the top level of the heredoc JSON to enable the skill/task tools
    # (so OpenCode discovers ~/.config/opencode/skills/ and native subagents) and
    # while keeping built-in bash visible so tool.execute.before can intercept it.
    tools_entries = ['    "skill": true', '    "task": true', '    "bash": true']
    tools_block = (
        '  "tools": {\n'
        + ',\n'.join(tools_entries)
        + '\n  },\n'
    )

    # Build the agents section for OpenCode config
    agents_section = {}
    for agent_type in agents:
        agents_section[agent_type] = {
            "model": f"e-infra-chat/{app.LLM_MODEL}",
            "bash": True,
            "edit": True,
            "write": True,
            "permission": {
                "default": "allow",
                "bash": "allow",
                "edit": "allow",
                "write": "allow",
                "external_directory": "allow"
            },
            "prompt": agent_configs.get(agent_type, 'You are a helpful assistant.')
        }

    agents_section_json = json.dumps(agents_section, indent=2).replace('\n', '\n  ')

    # Best-effort fallback: expose the guardrail env vars via /etc/profile.d so any
    # login/SSH shell (and any process that sources profile.d) sees them, mirroring
    # what PID 1 reads from the real container env. Only emitted for guarded hosts.
    guardrail_profile_block = '''
# Guardrail env fallback for login/SSH shells (best-effort mirror of PID 1 env)
mkdir -p /etc/profile.d
cat > /etc/profile.d/guardrail.sh <<'GUARDRAIL_PROFILE'
export GUARDRAIL_ENABLED=1
export GUARDRAIL_PROFILE={guardrail_profile_name}
export GUARDRAIL_GOAL={guardrail_goal_quoted}
export GUARDRAIL_HTTP_URL=http://127.0.0.1:4097
GUARDRAIL_PROFILE
chmod 644 /etc/profile.d/guardrail.sh 2>/dev/null || true
''' if guarded else ''

    # Resolve guardrail profile/goal for the profile.d fallback.
    if "soc_god" in agents and "coder56" in agents:
        guardrail_profile_name = "defender"
    elif "soc_god" in agents:
        guardrail_profile_name = "defender"
    elif "coder56" in agents:
        guardrail_profile_name = "coder56"
    else:
        # guarded is True but neither canonical name matched (custom GUARDED_AGENTS):
        # default profile to coder56-style scope keeper.
        guardrail_profile_name = "coder56"
    guardrail_goal_key = "soc_god" if guardrail_profile_name == "defender" else "coder56"
    guardrail_goal = app.GUARDRAIL_GOALS.get(guardrail_goal_key, '')
    guardrail_goal_quoted = app.shell_quote(guardrail_goal)

    return """
# OpenCode Agent Initialization for: {agents_label}
mkdir -p /root/.config/opencode /root/.local/share/opencode /var/log/opencode

# Write opencode.json configuration with environment variable placeholders
# OpenCode will substitute {{env:VAR_NAME}} with actual environment variable values
cat > /root/.config/opencode/opencode.json <<'OPENCODE_JSON'
{{
  "$$schema": "https://opencode.ai/config.json",
{tools_block}  "provider": {{
    "e-infra-chat": {{
      "npm": "@ai-sdk/openai-compatible",
      "name": "e-INFRA CZ Chat API",
      "options": {{
        "baseURL": "{{env:LLM_URL}}",
        "apiKey": "{{env:OPENCODE_API_KEY}}"
      }},
      "models": {{
        "{llm_model}": {{
          "name": "{llm_model}",
          "limit": {{
            "context": 200000,
            "output": 65536
          }}
        }},
        "gemma4": {{
          "name": "Gemma4",
          "limit": {{
            "context": 200000,
            "output": 65536
          }}
        }}
      }}
    }}
  }},
  "model": "e-infra-chat/{llm_model}",
  "autoupdate": false,
  "subagent_depth": 2,
  "compaction": {{
    "auto": true,
    "prune": true
  }},
  "permission": {{
    "default": "allow",
    "edit": {{
      "*": "allow"
    }},
    "write": {{
      "*": "allow"
    }},
    "external_directory": {{
      "*": "allow",
      "/tmp": "allow",
      "/tmp/*": "allow",
      "/tmp*": "allow"
    }}
  }},
  "agent": {agents_section}
}}
OPENCODE_JSON

# Write auth.json - OpenCode will also use {{env:}} placeholders here
cat > /root/.local/share/opencode/auth.json <<'AUTH_JSON'
{{
  "e-infra-chat": {{
    "type": "api",
    "key": "{{env:OPENCODE_API_KEY}}"
  }}
}}
AUTH_JSON

{guardrail_profile_block}
echo "OpenCode agents configured: {agents_label}"
""".format(
        agents_label=', '.join(agents),
        agents_section=agents_section_json,
        llm_model=app.LLM_MODEL,
        tools_block=tools_block,
        guardrail_profile_block=guardrail_profile_block.format(
            guardrail_profile_name=guardrail_profile_name,
            guardrail_goal_quoted=guardrail_goal_quoted,
        ),
    )


def host_script(topology, network, host, host_index, gateway):
    role = app.HOST_TYPES[host['type']]['label']
    ip_addr = app.host_ip(network['cidr'], host_index)
    data_content = host.get('data_content') or default_data_for_host(topology, network, host)
    service_block = role_service_block(host['type'])
    ssh_block = ''
    if host.get('ssh_enabled'):
        ssh_block = ssh_setup_block(host['username'], host['password'])

    # Add OpenCode agent block if configured
    agent_block = ''
    agents_list = app.host_agents(host)
    if agents_list:
        print(f"🤖 Adding agents {agents_list} for host {host['name']}")
        agent_block = opencode_agent_block(host, topology)
        if not agent_block:
            print(f"⚠️ opencode_agent_block returned empty for {agents_list}")
    else:
        print(f"ℹ️ No agents configured for host {host['name']}")

    # Internet access configuration.
    # Hosts are dual-homed: their topology subnet + scl-playground-net. We keep the
    # own subnet on-link, send the DEFAULT route to the playground for internet
    # egress, and route every OTHER topology subnet via the router gateway so that
    # inter-subnet traffic traverses the router (where SLIPS captures it) instead of
    # leaking out the playground default to the Docker host (which has no path to the
    # containers' topology addresses and makes cross-subnet targets show "filtered").
    sibling_cidrs = [n['cidr'] for n in topology['networks'] if n.get('id') != network['id']]
    sibling_routes = ''.join(
        f'ip route replace {cidr} via {gateway} dev "$$topo_if" || true\n'
        for cidr in sibling_cidrs
    )
    internet_config = ''
    if network.get('internet'):
        # Detect interfaces by address: Docker's eth0/eth1 ordering is NOT guaranteed
        # across dual-homed containers (topology subnet vs scl-playground-net swap per
        # host), so identify the topology iface by the host's own IP and the playground
        # iface as the other inet interface.
        internet_config = f'''
# Configure internet access via scl-playground-net + route sibling subnets via router
topo_if="$$(ip -o -f inet addr show | awk -v ip='{ip_addr}' '$$2!="lo" && $$4 ~ ip"/" {{print $$2; exit}}')"
pg_if="$$(ip -o -f inet addr show | awk -v t="$$topo_if" '$$2!="lo" && $$2!=t {{print $$2; exit}}')"
pg_gw="$$(ip route show default dev "$$pg_if" 2>/dev/null | awk '{{print $$3; exit}}')"
if [ -n "$$pg_gw" ]; then
    ip route replace default via "$$pg_gw" dev "$$pg_if" || true
fi
# Own subnet stays on-link via the topology interface.
ip route replace {network['cidr']} dev "$$topo_if" || true
# Route every other topology subnet via the router gateway on this subnet.
{sibling_routes}# Configure DNS to use public DNS servers
echo "nameserver 8.8.8.8" > /etc/resolv.conf
echo "nameserver 8.8.4.4" >> /etc/resolv.conf
'''
    else:
        # For isolated networks, default via router (already routes sibling subnets).
        internet_config = f'ip route replace default via {gateway} || true'

    return f"""set -eu
{internet_config.strip()}
mkdir -p /srv/scl-data /srv/www /srv/files /srv/db /var/log/scl /tmp/agents
cat > /etc/scl-host.json <<'JSON'
{json.dumps({'topology': topology['name'], 'network': network['name'], 'host': host['name'], 'role': role, 'ip': ip_addr}, indent=2)}
JSON
cat > /srv/scl-data/README.txt <<'DATA'
{data_content}
DATA
cp /srv/scl-data/README.txt /srv/www/index.txt || true
cp /srv/scl-data/README.txt /srv/files/share.txt || true
{service_block}
{ssh_block}
{agent_block}
touch /tmp/scl-host-init-ready
tail -f /dev/null
"""


def router_management_block(router):
    if not router.get('ssh_enabled'):
        return ''
    return ssh_setup_block(router.get('username') or 'admin', router.get('password') or 'strato')


def hackerlab_script(network, gateway):
    return f"""set -eu
ip route replace default via {gateway} || true
exec /root/.start-container.sh
"""


def default_data_for_host(topology, network, host):
    return (
        f"Topology: {topology['name']}\n"
        f"Network: {network['name']}\n"
        f"Host: {host['name']}\n"
        f"Role: {app.HOST_TYPES[host['type']]['label']}\n"
        "No AI-generated data was requested for this host.\n"
    )


def role_service_block(host_type):
    if host_type == 'web-server':
        return "printf '<h1>SCL web server</h1><pre>%s</pre>' \"$(cat /srv/scl-data/README.txt)\" > /srv/www/index.html\npython3 -m http.server 80 -d /srv/www &"
    if host_type == 'repo-server':
        # The full app stack (MariaDB + Node backend + nginx/frontend) is baked
        # into the image; this supervisor brings it up. See repo-app-start.sh.
        return "bash /usr/local/bin/repo-app-start.sh >/var/log/repo-app.log 2>&1 &"
    if host_type == 'file-server':
        return "python3 -m http.server 8080 -d /srv/files &"
    if host_type == 'db':
        # Create the notes table (the data seeding can be done by the agent if needed)
        return "sqlite3 /srv/db/app.db 'create table if not exists notes(id integer primary key, body text);' || true"
    if host_type == 'log-server':
        return "cp /srv/scl-data/README.txt /var/log/scl/training.log || true"
    return ":"


def router_script(topology, router, descendant_networks, child_routes, transit_subnets, is_root, default_source_ip=''):
    allowed_pairs = set(topology.get('router', {}).get('firewall', {}).get('allowed', []))
    forward_rules = []
    if is_root:
        for network in descendant_networks:
            if network.get('internet'):
                forward_rules.append(f"ip saddr {network['cidr']} oifname \"$$wan_if\" accept")
        for subnet in transit_subnets:
            forward_rules.append(f"ip saddr {subnet} oifname \"$$wan_if\" accept")
    for source in topology.get('networks', []):
        for dest in topology.get('networks', []):
            if source['id'] == dest['id']:
                continue
            if f"{source['id']}->{dest['id']}" in allowed_pairs:
                forward_rules.append(f"ip saddr {source['cidr']} ip daddr {dest['cidr']} accept")
    if not forward_rules:
        forward_rules.append('counter drop')
    route_lines = []
    if not is_root and router.get('parent_transit_ip'):
        src_clause = f" src {default_source_ip}" if default_source_ip else ''
        route_lines.append(f"ip route replace default via {router['parent_transit_ip']}{src_clause} || true")
    for route in child_routes:
        route_lines.append(f"ip route replace {route['cidr']} via {route['via']} || true")
    if not route_lines:
        route_lines.append(':')
    forward_block = '\n    '.join(forward_rules)
    route_block = '\n'.join(route_lines)
    nat_block = """
table ip nat {
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    oifname "$$wan_if" masquerade
  }
}
""" if is_root else ''
    # SLIPS capture: if this router is the monitoring capture_source, dump pcaps
    # (rotated every 30s, excluding the OpenCode API port) to /pcaps. Rotation
    # yields completed captures (cap_HHMMSS.pcap) that watch_pcaps will process;
    # a single growing router.pcap would be skipped by the guardrail.
    slips_cfg = (topology.get('monitoring') or {}).get('slips') or {}
    capture_source = slips_cfg.get('capture_source') or ''
    is_capture_router = bool(slips_cfg.get('enabled')) and bool(capture_source) and (
        router.get('id') == capture_source or router.get('name') == capture_source
    )
    capture_block = (
        "mkdir -p /pcaps && "
        # Supervisor loop that keeps tcpdump alive for the life of the router.
        # The router entrypoint runs under `set -eu`; errexit propagates into
        # this subshell, so an UNGUARDED non-zero tcpdump exit tears down the
        # whole loop and blinds the sensor (that is what previously killed
        # capture for good after a single tcpdump exit). Run the subshell with
        # `set +e` and guard tcpdump with `|| true` so the loop always restarts.
        # Note: on tcpdump 4.99.x `-G 30` rotation is traffic-triggered (an idle
        # interface never rotates) and `-W` does NOT make tcpdump self-exit, so
        # the loop itself is what keeps capture running. Prune to the newest 40
        # captures each cycle so the shared /pcaps volume stays bounded.
        "( set +e; while true; do "
        "ls -1t /pcaps/cap_*.pcap 2>/dev/null | tail -n +41 | xargs -r rm -f; "
        "tcpdump -i any -U -G 30 -w '/pcaps/cap_%H%M%S.pcap' 'not port 4096' >/dev/null 2>&1 || true; "
        "sleep 1; "
        "done ) >/dev/null 2>&1 &"
        if is_capture_router else ''
    )
    return f"""set -eu
{route_block}
{router_management_block(router)}
wan_if="$(ip route show default | awk '{{print $$5; exit}}')"
cat > /tmp/router-rules.nft <<EOF
flush ruleset
table inet filter {{
  chain forward {{
    type filter hook forward priority 0; policy drop;
    ct state established,related accept
    iifname "lo" accept
    {forward_block}
  }}
}}
{nat_block}EOF
nft -f /tmp/router-rules.nft || true
{capture_block}
    tail -f /dev/null
"""
