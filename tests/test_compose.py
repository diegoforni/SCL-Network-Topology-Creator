"""Characterization tests for generate_compose + naming + resolve_run_id.

Pins the CURRENT behavior of the monolithic app.py. Golden-master the big
dict emitters; assert exact strings for the pure naming helpers. No real
docker — any function that shells out has its lowest-level helper patched.
"""
import json
import re
from pathlib import Path

import pytest

import app


def _normalize_compose(compose):
    """Return a deterministic copy of a compose dict for golden-mastering.

    ``generate_compose`` embeds the host ``OUTPUTS_HOST_PATH`` into the
    ``/outputs`` volume mount of every agent host. The autouse ``isolate_fs``
    fixture points that at a per-test ``tmp_path`` (whose path includes a
    pytest-managed run counter that changes every run), so the raw dict is
    not byte-stable across runs. We redact that one path to a fixed token so
    the golden is deterministic while still pinning every other field."""
    serialized = json.dumps(compose, sort_keys=True)
    redacted = serialized.replace(app.OUTPUTS_HOST_PATH, '<OUTPUTS_HOST_PATH>')
    return json.loads(redacted)


# ---------------------------------------------------------------------------
# generate_compose
# ---------------------------------------------------------------------------

def test_generate_compose_minimal_golden(minimal_topology, golden):
    out = app.generate_compose(minimal_topology)
    golden('compose_minimal.json', _normalize_compose(out))


def test_generate_compose_minimal_structure(minimal_topology):
    out = app.generate_compose(minimal_topology)
    # Top-level shape.
    assert 'services' in out
    assert 'networks' in out
    topo_id = minimal_topology['id']
    project_prefix = f'scl-topology-{topo_id}'
    # Service KEYS are not prefixed (e.g. 'net1-h1', 'router-router1'), but
    # every container_name carries the project prefix.
    for name, cfg in out['services'].items():
        assert cfg['container_name'].startswith(project_prefix + '-'), (name, cfg.get('container_name'))
    # minimal topology has no agent-enabled hosts, so RUN_ID env is absent on
    # hosts (RUN_ID is only emitted for agent hosts). Assert image instead.
    network = minimal_topology['networks'][0]
    net_key = f'topo_{network["id"]}'
    first_host = network['hosts'][0]
    svc_name = f'{network["id"]}-{first_host["id"]}'
    # No agents and not a repo-server → plain BASE_IMAGE.
    assert out['services'][svc_name]['image'] == app.BASE_IMAGE
    # Router service also uses the base image.
    assert any(s['image'] == app.BASE_IMAGE for s in out['services'].values())
    # The user-facing network is present and uses the project prefix in its name.
    assert net_key in out['networks']
    assert out['networks'][net_key]['name'].startswith('scl-topology-')


def test_generate_compose_rich_golden(rich_topology, golden):
    out = app.generate_compose(rich_topology)
    golden('compose_rich.json', _normalize_compose(out))


def test_generate_compose_rich_agent_host_has_run_id(rich_topology):
    """The coder56 agent host in the rich topology must carry a RUN_ID env."""
    out = app.generate_compose(rich_topology)
    topo_id = rich_topology['id']
    # Find the agent host service (internal network jumpbox).
    agent_services = [
        (name, cfg) for name, cfg in out['services'].items()
        if cfg.get('environment', {}).get('RUN_ID')
    ]
    assert agent_services, "expected at least one agent host with RUN_ID env"
    for name, cfg in agent_services:
        assert cfg['environment']['RUN_ID'] == topo_id


# ---------------------------------------------------------------------------
# resolve_run_id
# ---------------------------------------------------------------------------

def test_resolve_run_id_clean_base():
    # Fresh tmp outputs dir (autouse isolate_fs) → dir absent → clean base.
    assert app.resolve_run_id('topo1') == 'topo1'


def test_resolve_run_id_timestamp_suffix_when_dir_exists():
    base = Path(app.OUTPUTS_HOST_PATH) / 'topo1'
    base.mkdir(parents=True, exist_ok=True)
    run_id = app.resolve_run_id('topo1')
    assert run_id != 'topo1'
    assert re.match(r'^topo1-\d{8}-\d{4}$', run_id), run_id


def test_resolve_run_id_honors_run_id_env(monkeypatch):
    monkeypatch.setenv('RUN_ID', 'override-x')
    assert app.resolve_run_id('topo1') == 'override-x'


def test_resolve_run_id_env_with_existing_dir_suffix(monkeypatch):
    monkeypatch.setenv('RUN_ID', 'override-x')
    base = Path(app.OUTPUTS_HOST_PATH) / 'override-x'
    base.mkdir(parents=True, exist_ok=True)
    run_id = app.resolve_run_id('topo1')
    assert run_id != 'override-x'
    assert re.match(r'^override-x-\d{8}-\d{4}$', run_id), run_id


# ---------------------------------------------------------------------------
# naming helpers (pure)
# ---------------------------------------------------------------------------

def test_compose_project_name():
    assert app.compose_project_name('topo1') == 'scl-topology-topo1'


def test_topology_network_name():
    assert app.topology_network_name('topo1', 'net1') == 'scl-topology-topo1-net1'


def test_hackerlab_container_name():
    # hackerlab container name is global/shared, not per-topology.
    assert app.hackerlab_container_name('topo1') == 'scl-hackerlab'


def test_resolve_topology_network_name_no_docker_fallback(monkeypatch):
    """When docker_run raises (no daemon), resolve falls back to the
    synthesized topology_network_name."""
    def _boom(args):
        raise RuntimeError('no docker')
    monkeypatch.setattr(app, 'docker_run', _boom)
    assert app.resolve_topology_network_name('topo1', 'net1') == \
        app.topology_network_name('topo1', 'net1')


def test_resolve_topology_network_name_matches_existing(monkeypatch):
    """When docker network ls returns a matching live network name, it is
    returned verbatim (preserving real-world casing)."""
    def _fake_ls(args):
        return "scl-topology-topo1-net1\nsome-other-net\n"
    monkeypatch.setattr(app, 'docker_run', _fake_ls)
    assert app.resolve_topology_network_name('topo1', 'net1') == \
        'scl-topology-topo1-net1'


def test_resolve_topology_network_name_no_match_falls_back(monkeypatch):
    """docker works but no matching network exists → synthesized name."""
    def _fake_ls(args):
        return "totally-unrelated\n"
    monkeypatch.setattr(app, 'docker_run', _fake_ls)
    assert app.resolve_topology_network_name('topo1', 'net1') == \
        app.topology_network_name('topo1', 'net1')
