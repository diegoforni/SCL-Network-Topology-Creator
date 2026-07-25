"""Characterization tests for app.py — model layer.

Covers: validate_topology, summarize, host_agents, normalize_identifier,
slugify, list_topologies. All assertions pin the CURRENT behavior of the
monolith (derived by reading app.py), not an aspirational spec.
"""
import copy

import pytest

import app
from conftest import MINIMAL


# ---------------------------------------------------------------------------
# normalize_identifier
# ---------------------------------------------------------------------------
def test_normalize_identifier_basic():
    assert app.normalize_identifier('Test Lab!', 'x') == 'test-lab'


def test_normalize_identifier_empty_uses_fallback():
    assert app.normalize_identifier('', 'fb') == 'fb'


def test_normalize_identifier_none_uses_fallback():
    assert app.normalize_identifier(None, 'fb') == 'fb'


def test_normalize_identifier_underscore_preserved():
    # underscores/hyphens survive the regex; result is lowercased, trailing '-' stripped
    assert app.normalize_identifier('UPPER_Case', '') == 'upper_case'


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------
def test_slugify_matches_normalize_identifier():
    # slugify wraps normalize_identifier-style collapsing (lowercases + collapses
    # non-alphanumeric to '-'). Pin that it agrees on a plain example.
    assert app.slugify('Test Lab') == app.normalize_identifier('Test Lab', '')


def test_slugify_collapses_non_alnum():
    assert app.slugify('My Cool Lab!') == 'my-cool-lab'


# ---------------------------------------------------------------------------
# host_agents
# ---------------------------------------------------------------------------
def test_host_agents_explicit_list():
    assert app.host_agents({'agents': ['coder56']}) == ['coder56']


def test_host_agents_legacy_fields():
    host = {'agent_enabled': True, 'agent_type': 'coder56'}
    assert app.host_agents(host) == ['coder56']


def test_host_agents_dedup():
    assert app.host_agents({'agents': ['a', 'a', 'b']}) == ['a', 'b']


def test_host_agents_none():
    assert app.host_agents({}) == []


def test_host_agents_legacy_disabled_no_agent():
    # agent_enabled False -> legacy agent_type not added
    assert app.host_agents({'agent_enabled': False, 'agent_type': 'coder56'}) == []


# ---------------------------------------------------------------------------
# validate_topology — happy path
# ---------------------------------------------------------------------------
def test_validate_topology_happy_path():
    topo = copy.deepcopy(MINIMAL)
    ret = app.validate_topology(topo)
    assert isinstance(ret, dict)
    # returns the same (mutated) object
    assert ret is topo
    net0 = topo['networks'][0]
    host0 = net0['hosts'][0]
    assert host0['image'] == 'ubuntu:24.04'
    assert host0['password'] == 'strato'
    assert host0['username'] == 'student'
    # default routers list created
    routers = topo['routers']
    assert isinstance(routers, list)
    assert len(routers) >= 1
    assert routers[0]['id'] == 'router1'
    # monitoring.slips default off
    assert topo['monitoring']['slips']['enabled'] is False
    # hackerlab network id defaults to first network id
    assert topo['infrastructure']['hackerlab_network_id'] == 'net1'


# ---------------------------------------------------------------------------
# validate_topology — rejections
# ---------------------------------------------------------------------------
def test_validate_topology_rejects_non_dict():
    with pytest.raises(ValueError):
        app.validate_topology(['not', 'a', 'dict'])


def test_validate_topology_rejects_empty_name():
    with pytest.raises(ValueError):
        app.validate_topology({'name': '   ', 'networks': [{'hosts': [{}]}]})


def test_validate_topology_rejects_missing_networks():
    with pytest.raises(ValueError):
        app.validate_topology({'name': 'x'})


def test_validate_topology_rejects_too_many_networks():
    topo = {
        'name': 'too many',
        'networks': [
            {'id': f'n{i}', 'hosts': [{'id': 'h', 'name': 'h', 'type': 'normal-user'}]}
            for i in range(9)
        ],
    }
    with pytest.raises(ValueError):
        app.validate_topology(topo)


def test_validate_topology_rejects_network_no_hosts():
    with pytest.raises(ValueError):
        app.validate_topology({'name': 'x', 'networks': [{'id': 'n1', 'hosts': []}]})


def test_validate_topology_rejects_too_many_hosts():
    topo = {
        'name': 'too many hosts',
        'networks': [
            {
                'id': 'n1',
                'hosts': [
                    {'id': f'h{i}', 'name': f'h{i}', 'type': 'normal-user'}
                    for i in range(25)
                ],
            }
        ],
    }
    with pytest.raises(ValueError):
        app.validate_topology(topo)


def test_validate_topology_rejects_duplicate_network_id():
    topo = {
        'name': 'dup',
        'networks': [
            {'id': 'samename', 'hosts': [{'id': 'h', 'name': 'h', 'type': 'normal-user'}]},
            {'id': 'samename', 'hosts': [{'id': 'h2', 'name': 'h2', 'type': 'normal-user'}]},
        ],
    }
    with pytest.raises(ValueError):
        app.validate_topology(topo)


# ---------------------------------------------------------------------------
# validate_topology mutates in place
# ---------------------------------------------------------------------------
def test_validate_topology_mutates_in_place():
    topo = copy.deepcopy(MINIMAL)
    host0 = topo['networks'][0]['hosts'][0]
    assert 'agents' not in host0
    app.validate_topology(topo)
    assert 'agents' in host0
    assert host0['agents'] == []


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------
def test_summarize_keys_and_fields(minimal_topology):
    summary = app.summarize(minimal_topology)
    assert isinstance(summary, dict)
    for key in ('id', 'name', 'created_at', 'updated_at', 'networks', 'hosts', 'running'):
        assert key in summary
    assert summary['id'] == minimal_topology['id']
    assert summary['name'] == minimal_topology['name']
    assert summary['networks'] == len(minimal_topology['networks'])
    assert summary['hosts'] == sum(len(n['hosts']) for n in minimal_topology['networks'])
    # no compose file written in tmp -> is_running short-circuits to False
    assert summary['running'] is False


# ---------------------------------------------------------------------------
# list_topologies
# ---------------------------------------------------------------------------
def _save(name, topo_id):
    """Persist a validated topology of the given display name under topo_id."""
    spec = copy.deepcopy(MINIMAL)
    spec['name'] = name
    app.validate_topology(spec)
    spec['id'] = topo_id
    spec['created_at'] = '2026-01-01T00:00:00Z'
    spec['updated_at'] = '2026-01-01T00:00:00Z'
    app.write_json(app.topology_path(topo_id), spec)


def test_list_topologies_counts_and_shapes():
    _save('Alpha', 'alpha')
    _save('Beta', 'beta')
    results = app.list_topologies()
    assert len(results) == 2
    for entry in results:
        assert isinstance(entry, dict)
        # every entry is a summarize() dict
        assert set(entry.keys()) >= {'id', 'name', 'networks', 'hosts', 'running'}
    ids = {e['id'] for e in results}
    assert ids == {'alpha', 'beta'}


def test_list_topologies_deletes_ssh_lab():
    _save('Alpha', 'alpha')
    _save('ssh lab', 'ssh-lab-topo')
    results = app.list_topologies()
    ids = {e['id'] for e in results}
    assert 'ssh-lab-topo' not in ids
    assert ids == {'alpha'}
    # the ssh lab files are removed from disk
    assert not app.topology_path('ssh-lab-topo').exists()
