"""
Unit tests for preset + agent-config plumbing in the network-topology plugin.

These run in-process (no Docker) against app.py. They cover the parts of the
two_networks_tiny feature that must hold regardless of whether an OpenCode
runtime is provisioned:

  * ip_override is honored and validated (in-subnet) at compose time.
  * The two_networks_tiny preset instantiates and produces the expected IPs
    and one-directional firewall rules.
  * opencode_agent_block injects the researcher's system_prompt + goal into the
    generated OpenCode config, even when the persona template module
    (generate_opencode_config) is unavailable.

Run: python3 tests/test_agent_config_injection.py   (or via pytest)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("TOPOLOGY_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault(
    "TOPOLOGY_PRESETS_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "presets")),
)

import app  # noqa: E402


def test_ip_override_honored_and_default_preserved():
    host_override = {"ip_override": "213.47.23.195"}
    assert app.host_ip("213.47.23.0/24", 1, host_override) == "213.47.23.195"
    # No override -> deterministic array-index scheme (first host = .11).
    assert app.host_ip("10.10.10.0/24", 1, {}) == "10.10.10.11"
    assert app.host_ip("10.10.10.0/24", 1, None) == "10.10.10.11"


def test_ip_override_out_of_subnet_rejected():
    topo = {
        "name": "bad",
        "networks": [{
            "id": "n", "name": "n", "cidr": "10.10.10.0/24",
            "hosts": [{"id": "h", "name": "h", "type": "normal-user",
                       "ip_override": "213.47.23.195"}],
        }],
    }
    try:
        app.validate_topology(topo)
    except ValueError as exc:
        assert "not inside" in str(exc)
    else:
        raise AssertionError("expected out-of-subnet ip_override to be rejected")


def test_two_networks_tiny_preset_ips_and_firewall():
    topo, status, err = app.instantiate_preset("two-networks-tiny", new_id="unit-tnt")
    assert err is None and status == 200, (status, err)
    compose = app.generate_compose(topo)
    ip = lambda svc, net: compose["services"][svc]["networks"][net]["ipv4_address"]
    assert ip("client-client-host", "topo_client") == "10.10.10.11"
    assert ip("server-file-server", "topo_server") == "10.10.20.11"
    assert ip("cnc-cnc-host", "topo_cnc") == "213.47.23.195"
    router = compose["services"]["router-router1"]["command"][-1]
    assert "ip saddr 10.10.10.0/24 ip daddr 10.10.20.0/24 accept" in router
    assert "ip saddr 10.10.10.0/24 ip daddr 213.47.23.0/24 accept" in router
    # No reverse rule -> server/cnc cannot initiate back to client.
    assert "ip daddr 10.10.10.0/24 accept" not in router


def test_opencode_agent_block_injects_prompt_and_goal():
    host = {
        "name": "client-host", "type": "normal-user",
        "agents": ["coder56"],
        "agent_config": {"coder56": {"system_prompt": "PROMPT_ABC", "goal": "GOAL_XYZ"}},
    }
    block = app.opencode_agent_block(host, {"name": "t"})
    assert block, "block should be non-empty even without the persona module"
    assert "PROMPT_ABC" in block
    assert "GOAL_XYZ" in block
    assert "Assignment goal" in block


if __name__ == "__main__":
    test_ip_override_honored_and_default_preserved()
    test_ip_override_out_of_subnet_rejected()
    test_two_networks_tiny_preset_ips_and_firewall()
    test_opencode_agent_block_injects_prompt_and_goal()
    print("PASS: all network-topology unit tests")
