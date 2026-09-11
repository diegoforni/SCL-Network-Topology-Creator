import ipaddress

import app


def _net(cidr):
    """Parse a CIDR tolerantly (host bits set are masked off, e.g. for
    presets that store 213.47.23.192/26 style blocks)."""
    return ipaddress.ip_network(str(cidr), strict=False)


def subnet_prefix(cidr):
    # Legacy /24-only helper (drops the last octet regardless of the netmask).
    # Kept for backward compatibility with callers that format names/labels;
    # every ADDRESS computation must use the ipaddress-based helpers below so
    # non-/24 blocks (e.g. the three-nets internet segment 213.47.23.192/26)
    # never produce out-of-subnet IPs.
    return cidr.split('/')[0].rsplit('.', 1)[0]


def router_ip(cidr):
    # Last usable address: .254 on a /24, .254 on 213.47.23.192/26, etc.
    return str(_net(cidr).broadcast_address - 1)


def host_ip(cidr, host_index, host=None):
    # A host may pin a static address via ip_override (must be inside the
    # network CIDR so Docker can assign it on the bridge). Fall back to the
    # deterministic offset scheme otherwise, WRAPPED into the block's usable
    # range so non-/24 CIDRs stay valid (a preset cidr of 213.47.23.192/26
    # with the legacy scheme produced 213.47.23.11 — outside the subnet —
    # and docker rejected the whole topology start).
    if host:
        override = str(host.get('ip_override') or '').strip()
        if override:
            try:
                if ipaddress.ip_address(override) in _net(cidr):
                    return override
            except ValueError:
                pass
            print(f"⚠️  host ip_override '{override}' invalid or outside {cidr}; using computed address")
    n = _net(cidr)
    usable = n.num_addresses - 2
    if usable <= 0:
        raise ValueError(f"network {cidr} is too small to address hosts")
    offset = (10 + int(host_index) - 1) % usable + 1
    return str(n.network_address + offset)


def hackerlab_ip(cidr):
    n = _net(cidr)
    if n.num_addresses < 4:
        return str(n.network_address + 1)
    return str(n.network_address + 2)


def router_key(router_id):
    return app.normalize_identifier(router_id, 'router')


def transit_network_key(parent_id, child_id):
    return f"transit_{router_key(parent_id)}_{router_key(child_id)}"


def transit_subnet(index):
    return f'10.250.{index}.0/29'


def network_router_ips(network, router_ids):
    assigned = list(dict.fromkeys(router_ids))
    n = _net(network['cidr'])
    base = int(n.network_address)
    bc = int(n.broadcast_address)
    ip_map = {}
    for offset, router_id in enumerate(assigned):
        # Primary router takes the last usable address; stacked routers walk
        # down from it with the same spacing the legacy /24 scheme used
        # (.254, .253-.240), clamped to the block.
        if offset == 0:
            ip_int = bc - 1
        else:
            ip_int = max(bc - 15, bc - 1 - offset)
        ip_map[router_id] = str(ipaddress.ip_address(ip_int))
    return ip_map


def build_router_maps(topology):
    routers = topology.get('routers') or []
    by_id = {router['id']: router for router in routers}
    children = {router['id']: [] for router in routers}
    for router in routers:
        parent_id = router.get('parent_router_id') or ''
        if parent_id and parent_id in children:
            children[parent_id].append(router['id'])
    networks_by_router = {router['id']: [] for router in routers}
    for network in topology.get('networks', []):
        owner = network.get('default_router_id') or network.get('router_id') or routers[0]['id']
        networks_by_router.setdefault(owner, []).append(network)
    return by_id, children, networks_by_router


def router_descendant_networks(router_id, children_map, networks_by_router):
    nets = list(networks_by_router.get(router_id, []))
    for child_id in children_map.get(router_id, []):
        nets.extend(router_descendant_networks(child_id, children_map, networks_by_router))
    return nets
