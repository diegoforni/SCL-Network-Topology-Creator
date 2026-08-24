"""End-to-end render tests for the three_nets preset (CYST/NetSecGame two_nets,
faithful 3-subnet split + host-level firewall matrix).

These pin the preset's CONTRACT: the nftables forward chain generated from it
must reproduce the CYST FirewallChainConfig FORWARD rules exactly — every
allowed pair present, every non-listed pair absent. If the preset or the
host-level firewall renderer drifts, these break loudly.
"""
import json
import re
from pathlib import Path

import app


def _load_preset_topology():
    preset_path = Path(__file__).resolve().parent.parent / 'presets' / 'three_nets.json'
    data = json.loads(preset_path.read_text())
    topology = data['topology']
    app.validate_topology(topology)
    topology['id'] = 'three-nets-test'
    return topology


def _forward_accept_rules(topology):
    by_id, children, nets_by_router = app.build_router_maps(topology)
    router_id = topology['routers'][0]['id']
    script = app.router_script(
        topology, by_id[router_id],
        app.router_descendant_networks(router_id, children, nets_by_router),
        [], [], is_root=True)
    # Indented rules inside chain forward {{ ... }} — capture saddr/daddr pairs.
    return re.findall(r'^\s+ip saddr (\S+) ip daddr (\S+) accept$', script, re.M)


def test_three_nets_structure():
    t = _load_preset_topology()
    nets = {n['id']: n for n in t['networks']}
    assert set(nets) == {'server', 'client', 'backend', 'internet'}
    assert nets['server']['cidr'] == '192.168.1.0/24'
    assert nets['client']['cidr'] == '192.168.2.0/24'
    assert nets['backend']['cidr'] == '192.168.3.0/24'       # AD + DB kept apart, per CYST
    assert nets['internet']['cidr'] == '213.47.23.192/26'
    hosts = {h['id']: h for n in t['networks'] for h in n['hosts']}
    assert set(hosts) == {
        'smb-server', 'web-server', 'other-server-1',
        'client-1', 'client-2', 'client-3', 'client-4', 'client-5',
        'ad-server', 'db-server', 'outside-node',
    }
    # CYST IPs preserved: servers .2/.4/.5, AD .2, DB .3, outside .195.
    # Clients shifted +1 (CYST .2-.7 -> .3-.7): .2 is reserved on the
    # hackerlab-attached network for the operator console.
    assert hosts['smb-server']['ip_override'] == '192.168.1.2'
    assert hosts['web-server']['ip_override'] == '192.168.1.4'
    assert hosts['other-server-1']['ip_override'] == '192.168.1.5'
    assert hosts['ad-server']['ip_override'] == '192.168.3.2'
    assert hosts['db-server']['ip_override'] == '192.168.3.3'
    assert hosts['outside-node']['ip_override'] == '213.47.23.195'
    # Linux equivalents of the CYST Windows nodes.
    assert hosts['client-1']['type'] == 'windows-client'   # xrdp+pwsh for RDP client_1
    assert hosts['client-2']['type'] == 'windows-client'
    assert hosts['ad-server']['type'] == 'ad-server'       # Samba 4 AD DC
    assert hosts['smb-server']['type'] == 'smb-server'     # vulnerable Samba share


def test_three_nets_firewall_matrix_matches_cyst():
    t = _load_preset_topology()
    allowed = {(s, d) for s, d in _forward_accept_rules(t)}
    internet = '213.47.23.192/26'

    # Clients (SCL .3-.7) each reach ONLY smb (.2), web (.4), other-1 (.5).
    for c in ['192.168.2.3', '192.168.2.4', '192.168.2.5', '192.168.2.6', '192.168.2.7']:
        for s in ['192.168.1.2', '192.168.1.4', '192.168.1.5']:
            assert (c, s) in allowed, f"missing client->server rule {c}->{s}"

    # Only smb + web reach AD (.2) and DB (.3) — never a client.
    for s in ['192.168.1.2', '192.168.1.4']:
        for d in ['192.168.3.2', '192.168.3.3']:
            assert (s, d) in allowed, f"missing server->backend rule {s}->{d}"

    # Internet: CYST clients 1/3/4 (SCL .3/.5/.6) + the three servers; clients
    # 2/5 (.4/.7) have NO path to the internet segment.
    for s in ['192.168.2.3', '192.168.2.5', '192.168.2.6',
              '192.168.1.2', '192.168.1.4', '192.168.1.5']:
        assert (s, internet) in allowed, f"missing internet rule {s}"
    for s in ['192.168.2.4', '192.168.2.7']:
        assert (s, internet) not in allowed, f"client {s} must NOT reach internet"

    # No server->server, no server->client, no client->backend, no backend egress.
    for s, d in allowed:
        if s.startswith('192.168.1.'):
            assert d.startswith('192.168.3.') or d == internet, f"server {s} -> {d} not allowed by CYST"
        if s.startswith('192.168.2.'):
            assert d.startswith('192.168.1.') or d == internet, f"client {s} -> {d} not allowed by CYST"
        if s.startswith('192.168.3.'):
            assert False, f"backend {s} must have no outbound rules (CYST: DB/AD cannot go to router)"
