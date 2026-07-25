"""Characterization tests for the pure IP/topology-graph helpers in app.py.

These pin the CURRENT behavior of the netmath helpers (subnet arithmetic,
router/host/hackerlab/transit IP assignment, identifier normalization, and the
router-maps graph builders). All deterministic; no fixtures needed beyond the
autouse ``isolate_fs`` and the module import.
"""
import app


# ---------------------------------------------------------------------------
# Subnet arithmetic helpers
# ---------------------------------------------------------------------------

def test_subnet_prefix_drops_prefix_and_host_octet():
    assert app.subnet_prefix('10.77.1.0/24') == '10.77.1'


def test_subnet_prefix_rsplit_last_dot():
    # rsplit on last '.', drops both the host octet and the /prefix.
    assert app.subnet_prefix('192.168.0.5/16') == '192.168.0'


def test_router_ip_appends_254():
    assert app.router_ip('10.77.1.0/24') == '10.77.1.254'


def test_host_ip_offset_zero():
    assert app.host_ip('10.77.1.0/24', 0) == '10.77.1.10'


def test_host_ip_offset_five():
    # 10 + index.
    assert app.host_ip('10.77.1.0/24', 5) == '10.77.1.15'


def test_hackerlab_ip():
    assert app.hackerlab_ip('10.77.1.0/24') == '10.77.1.2'


def test_transit_subnet_index_zero():
    assert app.transit_subnet(0) == '10.250.0.0/29'


def test_transit_subnet_index_three():
    assert app.transit_subnet(3) == '10.250.3.0/29'


# ---------------------------------------------------------------------------
# Identifier normalization
# ---------------------------------------------------------------------------

def test_router_key_normalizes_via_normalize_identifier():
    assert app.router_key('Core Node!') == 'core-node'


def test_router_key_empty_falls_back_to_router():
    assert app.router_key('') == 'router'


def test_transit_network_key():
    assert app.transit_network_key('r1', 'r2') == 'transit_r1_r2'


def test_normalize_identifier_strips_and_lowercases():
    assert app.normalize_identifier('Test Lab!', 'x') == 'test-lab'


def test_normalize_identifier_empty_falls_back():
    assert app.normalize_identifier('', 'fb') == 'fb'


def test_normalize_identifier_none_falls_back():
    assert app.normalize_identifier(None, 'fb') == 'fb'


def test_normalize_identifier_preserves_underscore():
    # Underscore is in the allowed [a-zA-Z0-9_-] set, so it survives.
    assert app.normalize_identifier('UPPER_Case', '') == 'upper_case'


# ---------------------------------------------------------------------------
# network_router_ips — router IP assignment on a subnet
# ---------------------------------------------------------------------------

def test_network_router_ips_single_router():
    assert app.network_router_ips({'cidr': '10.77.1.0/24'}, ['r1']) == \
        {'r1': '10.77.1.254'}


def test_network_router_ips_two_routers():
    # First router -> .254; second -> max(240, 254-1) = 253.
    assert app.network_router_ips({'cidr': '10.77.1.0/24'}, ['r1', 'r2']) == \
        {'r1': '10.77.1.254', 'r2': '10.77.1.253'}


def test_network_router_ips_dedup_preserves_order():
    # ['r1','r1','r2'] deduped (dict.fromkeys) -> only r1, r2; order kept.
    assert app.network_router_ips({'cidr': '10.77.1.0/24'}, ['r1', 'r1', 'r2']) == \
        {'r1': '10.77.1.254', 'r2': '10.77.1.253'}


# ---------------------------------------------------------------------------
# build_router_maps + router_descendant_networks — router graph
# ---------------------------------------------------------------------------

def _two_router_topology():
    """Construct (no validation needed) a topology with a parent router r1 and a
    child router r2, each owning one network."""
    return {
        'routers': [
            {'id': 'r1', 'parent_router_id': ''},
            {'id': 'r2', 'parent_router_id': 'r1'},
        ],
        'networks': [
            {'id': 'n1', 'cidr': '10.77.1.0/24', 'default_router_id': 'r1'},
            {'id': 'n2', 'cidr': '10.77.2.0/24', 'default_router_id': 'r2'},
        ],
    }


def test_build_router_maps_children():
    topo = _two_router_topology()
    _by_id, children, _nbr = app.build_router_maps(topo)
    # r1 has r2 as a child; r2 has none.
    assert children == {'r1': ['r2'], 'r2': []}


def test_build_router_maps_networks_by_router():
    topo = _two_router_topology()
    _by_id, _children, nbr = app.build_router_maps(topo)
    # Each router owns exactly its own network.
    assert [n['id'] for n in nbr['r1']] == ['n1']
    assert [n['id'] for n in nbr['r2']] == ['n2']


def test_router_descendant_networks_root_includes_all():
    topo = _two_router_topology()
    _by_id, children, nbr = app.build_router_maps(topo)
    desc = app.router_descendant_networks('r1', children, nbr)
    # r1 owns n1 and (transitively) child r2 owns n2 -> 2 networks.
    assert len(desc) == 2
    assert [n['id'] for n in desc] == ['n1', 'n2']


def test_router_descendant_networks_child_only_own():
    topo = _two_router_topology()
    _by_id, children, nbr = app.build_router_maps(topo)
    desc = app.router_descendant_networks('r2', children, nbr)
    # r2 has no children -> only its own network.
    assert len(desc) == 1
    assert [n['id'] for n in desc] == ['n2']
