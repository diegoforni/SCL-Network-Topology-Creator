import copy
import ipaddress
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
            # Deliberate control-enabling misconfig flag (passwordless-sudo
            # privesc) — see scripts.py::host_script.
            host['privesc_nopasswd'] = bool(host.get('privesc_nopasswd'))
            # Optional static IP. Must be a valid IPv4 inside the network CIDR,
            # otherwise Docker refuses to assign it on the bridge at start time.
            override = str(host.get('ip_override') or '').strip()
            if override:
                try:
                    override_addr = ipaddress.IPv4Address(override)
                except (ipaddress.AddressValueError, ValueError):
                    raise ValueError(
                        f"Host '{host['name']}' has an invalid ip_override '{override}'."
                    )
                try:
                    network_cidr = ipaddress.IPv4Network(network['cidr'], strict=False)
                except (ipaddress.AddressValueError, ValueError):
                    raise ValueError(
                        f"Network '{network['name']}' has an invalid cidr '{network['cidr']}'."
                    )
                if override_addr not in network_cidr:
                    raise ValueError(
                        f"Host '{host['name']}' ip_override '{override}' is not inside "
                        f"network '{network['name']}' CIDR '{network['cidr']}'."
                    )
                host['ip_override'] = override
            else:
                host.pop('ip_override', None)
            # Per-assignment agent config (parallel to host['agents']): a map of
            # {agent_type: {system_prompt, goal}} supplied by the agent-manager.
            # Sanitize to string values; drop anything malformed.
            agent_config = host.get('agent_config')
            if isinstance(agent_config, dict):
                cleaned = {}
                for a_type, entry in agent_config.items():
                    if isinstance(entry, dict):
                        cleaned[str(a_type)] = {
                            'system_prompt': str(entry.get('system_prompt') or ''),
                            'goal': str(entry.get('goal') or ''),
                        }
                if cleaned:
                    host['agent_config'] = cleaned
                else:
                    host.pop('agent_config', None)
            else:
                host.pop('agent_config', None)
            # A coder56-mcp host exists to run coder56 + HexStrike MCP, so the type
            # IMPLIES the coder56 agent. Auto-seed it so a host saved without an
            # explicit agents list can never start the MCP backend UNGUARDED —
            # host_has_agents, the guardrail env, HEXSTRIKE_ENABLED, and the mcp
            # block all key off the agents list, so without coder56 the host would
            # be a silently half-wired MCP image (no opencode serve, no guard).
            if host['type'] == 'coder56-mcp' and 'coder56' not in host['agents']:
                host['agents'] = ['coder56'] + [a for a in host['agents'] if a != 'coder56']
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
    # Entries are 'src->dst' where each side is a network id, optionally narrowed
    # to a single host as 'net/host' (host-level firewall rules). Unknown ids are
    # kept here (string-shaped) and skipped at nft-render time — dropping them
    # here would silently delete rules the editor will re-resolve once the host
    # is re-added.
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


def read_preset_file(path):
    with open(path, 'r', encoding='utf8') as file:
        return json.load(file)


def resolve_preset(preset_id):
    """Find a preset by its wrapper preset_id or, failing that, its filename stem.

    Returns the parsed preset dict, or None if no match exists. Matching on the
    wrapper id keeps GET /api/presets and POST .../instantiate consistent even
    when the file name (two_networks_tiny) differs from the id (two-networks-tiny).
    """
    if not app.PRESETS_DIR.exists():
        return None
    stem_match = None
    for path in sorted(app.PRESETS_DIR.glob('*.json')):
        try:
            data = read_preset_file(path)
        except (OSError, ValueError):
            continue
        if data.get('preset_id') == preset_id:
            return data
        if stem_match is None and path.stem == preset_id:
            stem_match = data
    return stem_match


def list_presets():
    """Return lightweight metadata for every preset in PRESETS_DIR.

    Counts are read from each preset's wrapper (network_count/host_count) when
    present, and only computed from the nested topology as a fallback.
    """
    presets = []
    if not app.PRESETS_DIR.exists():
        return presets
    for path in sorted(app.PRESETS_DIR.glob('*.json')):
        try:
            data = read_preset_file(path)
        except (OSError, ValueError):
            continue
        topology = data.get('topology') or {}
        networks = topology.get('networks') or []
        network_count = data.get('network_count')
        if network_count is None:
            network_count = len(networks)
        host_count = data.get('host_count')
        if host_count is None:
            host_count = sum(len(network.get('hosts') or []) for network in networks)
        presets.append({
            'preset_id': data.get('preset_id') or path.stem,
            'preset_name': data.get('preset_name') or path.stem,
            'description': data.get('description') or '',
            'tags': data.get('tags') or [],
            'network_count': network_count,
            'host_count': host_count,
        })
    return presets


def instantiate_preset(preset_id, new_id=None, name=None):
    """Materialize a preset into a new draft topology.

    Returns (topology, status_code, error_message). On success error_message is
    None; on failure topology is None.
    """
    data = resolve_preset(preset_id)
    if data is None:
        return None, 404, f"Preset '{preset_id}' not found."

    topology = copy.deepcopy(data.get('topology') or {})
    # Strip instance-specific fields back to draft defaults.
    topology.pop('created_at', None)
    topology.pop('updated_at', None)
    topology['status'] = 'draft'

    if name:
        topology['name'] = name
    elif not topology.get('name'):
        topology['name'] = data.get('preset_name') or preset_id

    if new_id:
        normalized = app.normalize_identifier(new_id, '')
        if not normalized:
            return None, 400, f"Invalid new_id '{new_id}'."
        if app.topology_path(normalized).exists():
            return None, 409, f"Topology '{normalized}' already exists."
        topology['id'] = normalized
    else:
        # Let save_topology() mint a fresh slug-based id so repeated
        # instantiations never collide.
        topology.pop('id', None)

    saved = save_topology(topology)
    return saved, 200, None
