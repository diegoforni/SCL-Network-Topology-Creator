import app


def subnet_prefix(cidr):
    return cidr.split('/')[0].rsplit('.', 1)[0]


def router_ip(cidr):
    return f'{subnet_prefix(cidr)}.254'


def host_ip(cidr, host_index, host=None):
    # A host may pin a static address via ip_override (must be inside the
    # network CIDR so Docker can assign it on the bridge). Fall back to the
    # deterministic array-index scheme otherwise.
    if host:
        override = str(host.get('ip_override') or '').strip()
        if override:
            return override
    return f'{subnet_prefix(cidr)}.{10 + host_index}'


def hackerlab_ip(cidr):
    return f'{subnet_prefix(cidr)}.2'


def router_key(router_id):
    return app.normalize_identifier(router_id, 'router')


def transit_network_key(parent_id, child_id):
    return f"transit_{router_key(parent_id)}_{router_key(child_id)}"


def transit_subnet(index):
    return f'10.250.{index}.0/29'


def network_router_ips(network, router_ids):
    assigned = list(dict.fromkeys(router_ids))
    ip_map = {}
    for offset, router_id in enumerate(assigned):
        if offset == 0:
            host_octet = 254
        else:
            host_octet = max(240, 254 - offset)
        ip_map[router_id] = f"{subnet_prefix(network['cidr'])}.{host_octet}"
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
