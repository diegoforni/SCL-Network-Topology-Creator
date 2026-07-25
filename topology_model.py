import json
import uuid

import app


def validate_topology(topology):
    if not isinstance(topology, dict):
        raise ValueError('Topology must be a JSON object.')
    name = str(topology.get('name') or '').strip()
    if not name:
        raise ValueError('Topology name is required.')
    networks = topology.get('networks')
    if not isinstance(networks, list) or not networks:
        raise ValueError('At least one network is required.')
    if len(networks) > 8:
        raise ValueError('At most 8 networks are supported in this first version.')

    seen_networks = set()
    for index, network in enumerate(networks, start=1):
        network['id'] = app.normalize_identifier(network.get('id'), f'net{index}')
        network['name'] = str(network.get('name') or network['id']).strip()
        network['cidr'] = str(network.get('cidr') or f'10.77.{index}.0/24').strip()
        network['internet'] = bool(network.get('internet'))
        if network['id'] in seen_networks:
            raise ValueError(f"Duplicate network id '{network['id']}'.")
        seen_networks.add(network['id'])
        hosts = network.get('hosts')
        if not isinstance(hosts, list) or not hosts:
            raise ValueError(f"Network '{network['name']}' needs at least one host.")
        if len(hosts) > 24:
            raise ValueError(f"Network '{network['name']}' has more than 24 hosts.")
        for host_index, host in enumerate(hosts, start=1):
            host['id'] = app.normalize_identifier(host.get('id'), f'h{index}_{host_index}')
            host['name'] = app.normalize_identifier(host.get('name'), f'{network["id"]}-{host_index}')
            host['type'] = host.get('type') if host.get('type') in app.HOST_TYPES else 'normal-user'
            host['image'] = 'ubuntu:24.04'
            host['username'] = app.normalize_identifier(host.get('username'), 'student')
            host['password'] = str(host.get('password') or 'strato')
            host['generate_data'] = bool(host.get('generate_data'))
            host['data_prompt'] = str(host.get('data_prompt') or '')
            host['data_content'] = str(host.get('data_content') or '')
            host['agent_enabled'] = bool(host.get('agent_enabled', False))
            host['agent_type'] = str(host.get('agent_type', '') or '')
            host['agents'] = app.host_agents(host)
        legacy_router_id = network.get('router_id')
        router_ids = network.get('router_ids')
        if not isinstance(router_ids, list):
          router_ids = []
        if legacy_router_id:
            router_ids = [legacy_router_id] + [router_id for router_id in router_ids if router_id != legacy_router_id]
        network['router_ids'] = router_ids
        network['default_router_id'] = network.get('default_router_id') or legacy_router_id or ''

    firewall = topology.setdefault('router', {}).setdefault('firewall', {})
    allowed = firewall.get('allowed') or []
    firewall['allowed'] = [
        pair for pair in allowed
        if isinstance(pair, str) and '->' in pair
    ]
    router = topology.setdefault('router', {})
    router['ssh_enabled'] = bool(router.get('ssh_enabled'))
    router['username'] = app.normalize_identifier(router.get('username'), 'admin')
    router['password'] = str(router.get('password') or 'strato')
    routers = topology.get('routers')
    if not isinstance(routers, list) or not routers:
        routers = [{
            'id': 'router1',
            'name': 'core',
            'parent_router_id': '',
            'ssh_enabled': bool(router.get('ssh_enabled')),
            'username': router['username'],
            'password': router['password'],
        }]
    seen_router_ids = set()
    normalized_routers = []
    for index, router_item in enumerate(routers, start=1):
        router_item['id'] = app.normalize_identifier(router_item.get('id'), f'router{index}')
        router_item['name'] = str(router_item.get('name') or router_item['id']).strip()
        router_item['parent_router_id'] = app.normalize_identifier(router_item.get('parent_router_id'), '') if router_item.get('parent_router_id') else ''
        router_item['ssh_enabled'] = bool(router_item.get('ssh_enabled'))
        router_item['username'] = app.normalize_identifier(router_item.get('username'), 'admin')
        router_item['password'] = str(router_item.get('password') or 'strato')
        if router_item['id'] in seen_router_ids:
            router_item['id'] = f"{router_item['id']}-{index}"
        seen_router_ids.add(router_item['id'])
        normalized_routers.append(router_item)
    root_router_id = normalized_routers[0]['id']
    for router_item in normalized_routers[1:]:
        if not router_item['parent_router_id'] or router_item['parent_router_id'] == router_item['id'] or router_item['parent_router_id'] not in seen_router_ids:
            router_item['parent_router_id'] = root_router_id
    topology['routers'] = normalized_routers
    router_ids = {item['id'] for item in normalized_routers}
    for network in networks:
        attached = [router_id for router_id in (network.get('router_ids') or []) if router_id in router_ids]
        if not attached:
            attached = [root_router_id]
        default_router_id = network.get('default_router_id') if network.get('default_router_id') in router_ids else ''
        if default_router_id not in attached:
            default_router_id = attached[0]
        network['router_ids'] = [default_router_id] + [router_id for router_id in dict.fromkeys(attached) if router_id != default_router_id]
        network['default_router_id'] = network['router_ids'][0]
        network['router_id'] = network['default_router_id']
    infrastructure = topology.setdefault('infrastructure', {})
    hackerlab_network_id = infrastructure.get('hackerlab_network_id')
    if not hackerlab_network_id or hackerlab_network_id not in seen_networks:
        infrastructure['hackerlab_network_id'] = networks[0]['id']
    # SLIPS monitoring (opt-in, default off). capture_source is a router host id/name
    # whose traffic gets tcpdump'd to a shared pcaps volume for the slips-sensor.
    monitoring = topology.setdefault('monitoring', {})
    slips = monitoring.setdefault('slips', {})
    slips.setdefault('enabled', False)
    slips.setdefault('capture_source', root_router_id)
    slips.setdefault('defender_enabled', True)
    return topology


def summarize(topology):
    return {
        'id': topology['id'],
        'name': topology['name'],
        'created_at': topology.get('created_at'),
        'updated_at': topology.get('updated_at'),
        'networks': len(topology.get('networks', [])),
        'hosts': sum(len(network.get('hosts', [])) for network in topology.get('networks', [])),
        'running': app.is_running(topology['id']),
    }


def list_topologies():
    app.TOPOLOGIES_DIR.mkdir(parents=True, exist_ok=True)
    topologies = []
    for path in sorted(app.TOPOLOGIES_DIR.glob('*/topology.json')):
        try:
            topology = app.read_json(path)
            if str(topology.get('name') or '').strip().lower() == 'ssh lab':
                try:
                    path.unlink()
                except OSError:
                    continue
                compose_file = app.compose_path(path.parent.name)
                if compose_file.exists():
                    try:
                        compose_file.unlink()
                    except OSError:
                        pass
                continue
            topologies.append(summarize(topology))
        except (OSError, json.JSONDecodeError):
            continue
    return topologies


def save_topology(payload):
    topology = validate_topology(payload)
    topology_id = app.normalize_identifier(topology.get('id'), app.slugify(topology['name']))
    if not topology.get('id'):
        topology_id = f'{topology_id}-{uuid.uuid4().hex[:6]}'
    topology['id'] = topology_id
    existing_path = app.topology_path(topology_id)
    existing = app.read_json(existing_path) if existing_path.exists() else {}
    topology['created_at'] = existing.get('created_at') or app.now_ts()
    topology['updated_at'] = app.now_ts()
    app.write_json(existing_path, topology)
    compose = app.generate_compose(topology)
    with open(app.compose_path(topology_id), 'w', encoding='utf8') as file:
        json.dump(compose, file, indent=2)
        file.write('\n')
    if app.is_running(topology_id):
        app.sync_hackerlab_runtime(topology)
    return topology
