"""Characterization tests for the shell-emitter functions in app.py.

These pin the CURRENT output of the init-script generators (host, router,
hackerlab, ssh, opencode-agent, role-service, default-data) so the flat-module
refactor can prove it is byte-for-byte behavior preserving. Read the function
bodies before touching any assert.
"""
import app


# ---------------------------------------------------------------------------
# shell_quote — the lowest-level escape helper used by every ssh block.
# ---------------------------------------------------------------------------
def test_shell_quote_string_with_apostrophe():
    # "it's" -> 'it'"'"'s'  (close quote, escaped single quote, reopen quote)
    assert app.shell_quote("it's") == "'it'\"'\"'s'"


def test_shell_quote_non_string_is_coerced():
    # shell_quote wraps str(value), so an int becomes the quoted literal.
    assert app.shell_quote(5) == "'5'"


# ---------------------------------------------------------------------------
# ssh_setup_block — shared by host + router ssh setup.
# ---------------------------------------------------------------------------
def test_ssh_setup_block_contains_expected_lines():
    block = app.ssh_setup_block('bob', 'pass')
    assert 'mkdir -p /var/run/sshd' in block
    assert "useradd -m -s /bin/bash 'bob'" in block
    assert 'chpasswd' in block
    # The bob:pass credential is shell-quoted as a single argument to chpasswd.
    assert "'bob:pass'" in block


# ---------------------------------------------------------------------------
# opencode_agent_block — empty for non-agent hosts, populated for agent hosts.
# ---------------------------------------------------------------------------
def test_opencode_agent_block_empty_for_non_agent_host(minimal_topology):
    network = minimal_topology['networks'][0]
    host = network['hosts'][0]  # h1 / host1 — no agents
    assert app.host_agents(host) == []
    assert app.opencode_agent_block(host, minimal_topology) == ''


def test_opencode_agent_block_populated_for_agent_host(rich_topology):
    # The coder56 jumpbox lives on the second ('int') network.
    host = rich_topology['networks'][1]['hosts'][0]
    assert app.host_agents(host) == ['coder56']
    block = app.opencode_agent_block(host, rich_topology)
    assert block != ''
    # Structural asserts (not golden): the guardrail delegation marker + tools.
    assert '4097' in block
    assert '"skill"' in block
    assert '"task"' in block
    assert '"bash"' in block


def test_opencode_agent_block_can_disable_coder56_verifier(rich_topology):
    host = rich_topology['networks'][1]['hosts'][0]
    host['coder56_verifier_enabled'] = False

    block = app.opencode_agent_block(host, rich_topology)

    assert 'DIRECT VALIDATION MODE (coder56 verifier disabled)' in block
    assert 'VERIFICATION GATE (mandatory for each NEW vulnerability' not in block
    assert '"task": {' in block
    assert '"*": "deny"' in block


# ---------------------------------------------------------------------------
# host_script — golden-master the minimal host's full init script.
# ---------------------------------------------------------------------------
def test_host_script_minimal_golden(minimal_topology, golden):
    network = minimal_topology['networks'][0]
    host = network['hosts'][0]
    gateway = app.router_ip(network['cidr'])
    out = app.host_script(minimal_topology, network, host, 0, gateway)
    golden('host_script_minimal.txt', out)


def test_host_script_agent_host_golden(rich_topology, golden):
    # The coder56 host exercises the agent-block embedding path end-to-end.
    network = rich_topology['networks'][1]
    host = network['hosts'][0]
    gateway = app.router_ip(network['cidr'])
    out = app.host_script(rich_topology, network, host, 0, gateway)
    golden('host_script_agent.txt', out)


# ---------------------------------------------------------------------------
# router_script — golden-master the root router's init script.
# ---------------------------------------------------------------------------
def test_router_script_minimal_golden(minimal_topology, golden):
    by_id, children, nets_by_router = app.build_router_maps(minimal_topology)
    router_id = minimal_topology['routers'][0]['id']
    router = by_id[router_id]
    descendant_networks = app.router_descendant_networks(
        router_id, children, nets_by_router)
    out = app.router_script(
        minimal_topology, router, descendant_networks, [], [], is_root=True)
    golden('router_script_minimal.txt', out)


# ---------------------------------------------------------------------------
# hackerlab_script — deterministic one-liner from network + gateway.
# ---------------------------------------------------------------------------
def test_hackerlab_script_golden(minimal_topology, golden):
    network = minimal_topology['networks'][0]
    gateway = app.router_ip(network['cidr'])
    out = app.hackerlab_script(network, gateway)
    golden('hackerlab_script.txt', out)


def test_hackerlab_script_routes_via_gateway():
    network = {'cidr': '10.77.7.0/24'}
    out = app.hackerlab_script(network, app.router_ip(network['cidr']))
    assert 'ip route replace default via 10.77.7.254 || true' in out
    assert 'exec /root/.start-container.sh' in out


# ---------------------------------------------------------------------------
# role_service_block — substring asserts (the exact service command line).
# ---------------------------------------------------------------------------
def test_role_service_block_web_server():
    out = app.role_service_block('web-server')
    assert 'python3 -m http.server 80 -d /srv/www &' in out
    assert 'printf' in out and '/srv/www/index.html' in out


def test_role_service_block_db():
    assert app.role_service_block('db') == (
        "sqlite3 /srv/db/app.db 'create table if not exists notes"
        "(id integer primary key, body text);' || true")


def test_role_service_block_unknown_falls_back_to_noop():
    # Any host type without a dedicated service returns the shell no-op ":".
    assert app.role_service_block('normal-user') == ':'


# ---------------------------------------------------------------------------
# default_data_for_host — golden-master the README seed text.
# ---------------------------------------------------------------------------
def test_default_data_for_host_golden(minimal_topology, golden):
    network = minimal_topology['networks'][0]
    host = network['hosts'][0]
    out = app.default_data_for_host(minimal_topology, network, host)
    golden('default_data_for_host.txt', out)
