import os
from pathlib import Path
import datetime

import app


def resolve_run_id(topology_id):
    """Compute a unique run id for THIS start so outputs never overwrite.

    base = RUN_ID env override (else the topology id). If ``outputs/<base>/``
    already exists (a prior run), append a timestamp ``-YYYYMMDD-HHMM`` to make
    it unique. The first run of a topology (dir absent) keeps the clean base.
    Computed ONCE per generate_compose and stamped on every container's RUN_ID
    env (attacker, victim, slips) so guardrail verdicts, SLIPS, defender store and
    the agent-manager session capture all land in the same ``outputs/<run_id>/``.
    """
    base = os.environ.get('RUN_ID') or topology_id
    out = Path(app.OUTPUTS_HOST_PATH)
    try:
        if out.exists() and (out / base).exists():
            base = f"{base}-{datetime.datetime.now().strftime('%Y%m%d-%H%M')}"
    except OSError:
        pass
    return base


def generate_compose(topology, opencode_images=None):
    """Generate docker-compose configuration for the topology.

    Args:
        topology: The topology configuration
        opencode_images: Dict mapping base_image -> opencode_image_name
                        If None, uses global OPENCODE_IMAGE for all
    """
    project_prefix = f"scl-topology-{topology['id']}"
    # Single source of truth for this run's outputs dir. Unique per start
    # (timestamp-suffixed if outputs/<base>/ already exists) so re-runs never
    # overwrite. Stamped on every container's RUN_ID env + the .current_run
    # marker so every consumer resolves the same id.
    run_id = resolve_run_id(topology['id'])
    try:
        (Path(app.OUTPUTS_HOST_PATH) / ".current_run").write_text(run_id)
    except OSError:
        pass
    routers = topology.get('routers') or []
    if not routers:
        routers = app.defaultRouters()
    router_by_id, children_map, networks_by_router = app.build_router_maps({**topology, 'routers': routers})
    root_router_id = routers[0]['id']

    # Use provided opencode_images mapping or empty dict
    if opencode_images is None:
        opencode_images = {}

    compose = {
        'services': {},
        'networks': {
            'scl-playground-net': {'external': True, 'name': 'scl-playground-net'}
        }
    }

    # SLIPS monitoring: a shared pcaps volume is declared only when enabled. The
    # capture_source router writes pcaps here; the slips-sensor reads them.
    slips_cfg = (topology.get('monitoring') or {}).get('slips') or {}
    slips_enabled = bool(slips_cfg.get('enabled'))
    pcaps_volume = f'{project_prefix}-pcaps' if slips_enabled else None
    if slips_enabled:
        compose['volumes'] = {pcaps_volume: {'name': pcaps_volume}}

    # User-facing network bridges.
    network_router_ip_maps = {}
    router_network_attaches = {router['id']: [] for router in routers}
    for index, network in enumerate(topology['networks'], start=1):
        network_key = f'topo_{network["id"]}'
        # Only set internal=True if internet access is not requested for this network
        network_internal = not bool(network.get('internet'))
        compose['networks'][network_key] = {
            'name': f'{project_prefix}-{network["id"]}',
            'internal': network_internal,
            'ipam': {'config': [{'subnet': network['cidr']}]},
        }
        router_ids = network.get('router_ids') or [network.get('default_router_id') or root_router_id]
        network_router_ip_maps[network['id']] = app.network_router_ips(network, router_ids)
        for router_id in router_ids:
            router_network_attaches.setdefault(router_id, []).append(network)
    router_default_source_ips = {}
    for router_id, attached_networks in router_network_attaches.items():
        if not attached_networks:
            continue
        preferred_network = next((network for network in topology['networks'] if network.get('default_router_id') == router_id), attached_networks[0])
        router_default_source_ips[router_id] = network_router_ip_maps[preferred_network['id']].get(router_id, app.router_ip(preferred_network['cidr']))

    transit_links = []
    child_routes = {router_id: [] for router_id in router_by_id}
    parent_ip_map = {}
    transit_index = 1
    for parent_id, child_ids in children_map.items():
        for child_id in child_ids:
            subnet = app.transit_subnet(transit_index)
            parent_ip = f'10.250.{transit_index}.2'
            child_ip = f'10.250.{transit_index}.3'
            key = app.transit_network_key(parent_id, child_id)
            transit_links.append({
                'key': key,
                'parent_id': parent_id,
                'child_id': child_id,
                'subnet': subnet,
                'parent_ip': parent_ip,
                'child_ip': child_ip,
            })
            parent_ip_map[child_id] = parent_ip
            for network in app.router_descendant_networks(child_id, children_map, networks_by_router):
                child_routes[parent_id].append({'cidr': network['cidr'], 'via': child_ip})
            transit_index += 1

    for router in routers:
        router_id = router['id']
        service_name = f'router-{app.router_key(router_id)}'
        router_networks = {'scl-playground-net': {}}
        for network in router_network_attaches.get(router_id, []):
            network_key = f'topo_{network["id"]}'
            router_networks[network_key] = {'ipv4_address': network_router_ip_maps[network['id']].get(router_id, app.router_ip(network['cidr']))}
        for link in transit_links:
            if link['parent_id'] == router_id:
                compose['networks'][link['key']] = {
                    'name': f'{project_prefix}-{link["key"]}',
                    'internal': True,
                    'ipam': {'config': [{'subnet': link['subnet']}]},
                }
                router_networks[link['key']] = {'ipv4_address': link['parent_ip']}
            if link['child_id'] == router_id:
                router_networks[link['key']] = {'ipv4_address': link['child_ip']}
        descendant_networks = app.router_descendant_networks(router_id, children_map, networks_by_router)
        router_script_text = app.router_script(
            topology,
            {**router, 'parent_transit_ip': parent_ip_map.get(router_id, '')},
            descendant_networks,
            child_routes.get(router_id, []),
            [link['subnet'] for link in transit_links],
            router_id == root_router_id,
            router_default_source_ips.get(router_id, ''),
        )
        compose['services'][service_name] = {
            'image': app.BASE_IMAGE,
            'container_name': f'{project_prefix}-{service_name}',
            'hostname': router.get('name') or router_id,
            'cap_add': ['NET_ADMIN'],
            'sysctls': {
                'net.ipv4.ip_forward': '1',
                'net.ipv4.conf.all.rp_filter': '0',
                'net.ipv4.conf.default.rp_filter': '0',
            },
            'command': ['sh', '-lc', router_script_text],
            'networks': router_networks,
            'labels': [
                'scl.plugin=network-topology',
                f'scl.topology={topology["id"]}',
                f'scl.router={router_id}',
            ],
        }
        # The capture_source router writes pcaps to the shared volume (NET_RAW for tcpdump).
        if slips_enabled and pcaps_volume and (
            router_id == (slips_cfg.get('capture_source') or '') or
            router.get('name') == (slips_cfg.get('capture_source') or '')
        ):
            compose['services'][service_name]['cap_add'] = ['NET_ADMIN', 'NET_RAW']
            compose['services'][service_name]['volumes'] = [f'{pcaps_volume}:/pcaps']

    for index, network in enumerate(topology['networks'], start=1):
        network_key = f'topo_{network["id"]}'
        gateway_router_id = network.get('default_router_id') or network.get('router_ids', [root_router_id])[0]
        gateway_ip = network_router_ip_maps[network['id']].get(gateway_router_id, app.router_ip(network['cidr']))

        for host_index, host in enumerate(network['hosts'], start=1):
            service_name = f'{network["id"]}-{host["id"]}'

            host_has_agents = bool(app.host_agents(host))
            host_base_image = host.get('image', 'ubuntu:24.04')

            # Dynamic image selection: repo-server / ad-server hosts use their
            # dedicated image (full-stack app / Samba AD DC); agent hosts use their
            # OpenCode variant; everything else uses the plain base image.
            if host.get('type') == 'repo-server':
                host_image = app.REPO_HOST_IMAGE
            elif host.get('type') == 'ad-server':
                host_image = app.AD_HOST_IMAGE
            elif host.get('type') == 'windows-client':
                host_image = app.RDP_HOST_IMAGE
            elif host.get('type') == 'vuln-web-server':
                host_image = app.WEB_HOST_IMAGE
            elif host_has_agents:
                host_image = opencode_images.get(host_base_image, app.OPENCODE_IMAGE)
            else:
                host_image = app.BASE_IMAGE

            service_config = {
                'image': host_image,
                'container_name': f'{project_prefix}-{service_name}',
                'hostname': host['name'],
                'cap_add': ['NET_ADMIN'],
                'command': ['sh', '-lc', app.host_script(topology, network, host, host_index, gateway_ip)],
                'networks': {network_key: {'ipv4_address': app.host_ip(network['cidr'], host_index)}},
                'labels': [
                    'scl.plugin=network-topology',
                    f'scl.topology={topology["id"]}',
                    f'scl.network={network["id"]}',
                    f'scl.host={host["id"]}',
                    f'scl.host_type={host["type"]}',
                    f'scl.has_agents={"true" if host_has_agents else "false"}',
                ],
            }

            # Attach all hosts to scl-playground-net for SCL service connectivity
            service_config['networks']['scl-playground-net'] = {}

            if host.get('type') == 'ad-server':
                # The Samba AD DC provisions on first boot (~15-60s) before `samba -i`
                # serves Kerberos/SMB. Without a healthcheck, a coder56 launch issued
                # right after topology start races provisioning and hits a not-yet-ready
                # DC. Probe SMB auth as the readiness signal (svc_sql is always created).
                service_config['healthcheck'] = {
                    'test': ['CMD', 'bash', '-lc', "smbclient //127.0.0.1/netlogon -U 'SC\\svc_sql%Dragon2024!' -c 'ls' >/dev/null 2>&1"],
                    'interval': '10s',
                    'timeout': '5s',
                    'retries': 18,
                    'start_period': '120s',
                }

            if host.get('type') == 'windows-client':
                # The RDP host provisions on first boot before `xrdp --nodaemon` serves
                # :3389. Without a healthcheck, a coder56 launch issued right after
                # topology start races provisioning and hits a not-yet-ready RDP. Probe
                # the RDP listener as the readiness signal (nc is baked in the image).
                service_config['healthcheck'] = {
                    'test': ['CMD', 'bash', '-lc', 'nc -w1 -z 127.0.0.1 3389 >/dev/null 2>&1'],
                    'interval': '10s',
                    'timeout': '5s',
                    'retries': 18,
                    'start_period': '90s',
                }

            if host.get('type') == 'vuln-web-server':
                # The web host provisions on first boot before `lighttpd -D` serves :80.
                # Without a healthcheck, a coder56 launch issued right after topology start
                # races provisioning and hits a not-yet-ready web server. Probe the HTTP
                # listener as the readiness signal (nc is baked in the image).
                service_config['healthcheck'] = {
                    'test': ['CMD', 'bash', '-lc', 'nc -w1 -z 127.0.0.1 80 >/dev/null 2>&1'],
                    'interval': '10s',
                    'timeout': '5s',
                    'retries': 18,
                    'start_period': '90s',
                }

            # Conditional OpenCode configuration (ports, volumes, environment, healthcheck) only when agents present
            if host_has_agents:
                # Agent scripts (host AGENTS_HOST_PATH) + the shared outputs dir (host, rw)
                # for run-log persistence. The opencode shared modules (/opt/shared),
                # entrypoint.sh and db_admin_opencode_client.py are BAKED INTO the
                # scl-plugin-network-topology-ubuntu-opencode image (its Dockerfile COPYs
                # them from this plugin's images dir), so topology hosts need NO host-path
                # image bind mount — keeping them free of any host image-path dependency.
                volumes = service_config.get('volumes', []) + [
                    f'{app.AGENTS_HOST_PATH}:/app/agents:ro',
                    # Persist agent run logs (timeline + opencode messages) to the shared
                    # host outputs dir so they survive teardown and are Replay-readable.
                    f'{app.OUTPUTS_HOST_PATH}:/outputs',
                ]
                service_config['volumes'] = volumes

                # The static image has /usr/local/bin/entrypoint.sh as ENTRYPOINT.
                # Pass only the host initializer as its command. The entrypoint
                # launches this initializer first, waits for its readiness marker,
                # and only then starts the guardrail/executor OpenCode processes.
                # Starting entrypoint.sh again here created duplicate serves and
                # port-conflict ServeError noise.
                service_config['command'] = [
                    'sh', '-lc', app.host_script(topology, network, host, host_index, gateway_ip)
                ]

                # Add SSH and compromised credentials environment variables
                # Get the actual API key value from the environment at generation time
                # This allows the docker-compose file to work when started directly
                api_key_value = os.environ.get('OPENCODE_API_KEY', '')
                service_config['environment'] = {
                    'OPENCODE_API_KEY': api_key_value,
                    'LLM_URL': app.LLM_URL_FULL,
                    'LLM_MODEL': app.LLM_MODEL,
                    'SSH_COMPROMISED_USER': 'labuser',
                    'SSH_COMPROMISED_PASS': host.get('password', 'strato'),
                    # Write run logs into the mounted /outputs so get_trident_base()
                    # resolves there and logs persist + are aligned with SLIPS/defender.
                    'TRIDENT_HOME': '/outputs',
                    # RUN_ID selects the outputs/<RUN_ID>/ dir for run logs
                    # (guardrail verdicts, SLIPS, per-agent opencode_api_messages).
                    # Default to the topology id; override globally via RUN_ID in .env.
                    'RUN_ID': run_id,
                }

                # Guardrail (auditor) configuration for guarded hosts only.
                # The executor opencode (PID 1, entrypoint) reads these from the
                # real container env to start a second opencode serve on 4097
                # (loopback, NOT published) and place the global guardrail plugin.
                # db_admin and other non-guarded hosts are left untouched.
                host_agent_types = app.host_agents(host)
                member_guarded = any(a in app.GUARDED_AGENTS for a in host_agent_types)
                # Honor an explicit per-host guardrail_enabled flag from the
                # frontend; absent (None) => auto (armed iff a guarded agent is
                # present on the host).
                host_guarded = host.get('guardrail_enabled') if host.get('guardrail_enabled') is not None else member_guarded
                if host_guarded:
                    if 'soc_god' in host_agent_types:
                        guardrail_profile = 'defender'
                    else:
                        guardrail_profile = 'coder56'
                    guardrail_goal_key = 'soc_god' if guardrail_profile == 'defender' else 'coder56'
                    service_config['environment'].update({
                        'GUARDRAIL_ENABLED': '1',
                        'GUARDRAIL_PROFILE': guardrail_profile,
                        'GUARDRAIL_GOAL': app.GUARDRAIL_GOALS.get(guardrail_goal_key, ''),
                        'GUARDRAIL_HTTP_URL': 'http://127.0.0.1:4097',
                        # Temporary verification instrumentation is opt-in and
                        # disabled by default. It is toggled only on disposable
                        # smoke runs via the topology host field.
                        'GUARDRAIL_VERIFY_MARKERS': (
                            '1' if host.get('guardrail_verify_markers') is True else '0'
                        ),
                    })

                # OpenCode HTTP API port — internal only (not published to the host).
                # Publishing host port 4096 for every agent host made multiple agents
                # collide on the same host port; the agent-manager reaches OpenCode
                # over scl-playground-net by container name instead.
                service_config['expose'] = ['4096']

                # Guardrail HTTP API — 127.0.0.1:4097 inside the container. It is
                # loopback-only by construction (the entrypoint binds it to
                # 127.0.0.1); we deliberately do NOT publish it. Exposing it on the
                # internal docker network would let sibling containers reach the
                # guardrail, so we omit it from `expose` entirely.

            compose['services'][service_name] = service_config

    # SLIPS sensor sidecar: joined to scl-playground-net so it can reach the
    # agent-manager, mounting the shared pcaps volume. It runs SLIPS (patched)
    # on the router's captures and forwards alerts to the defender API.
    if slips_enabled and pcaps_volume:
        defender_url = os.environ.get(
            'DEFENDER_URL', 'http://scl-agent-manager-dashboard:8080/api/defender/alerts'
        )
        compose['services']['slips-sensor'] = {
            'image': app.SLIPS_IMAGE,
            'container_name': f'{project_prefix}-slips-sensor',
            'cap_add': ['NET_ADMIN', 'NET_RAW'],
            'volumes': [f'{pcaps_volume}:/pcaps', f'{app.OUTPUTS_HOST_PATH}:/outputs'],
            'networks': {'scl-playground-net': {}},
            'environment': {
                'DEFENDER_URL': defender_url,
                'RUN_ID': run_id,
                'PCAP_DIR': '/pcaps',
            },
            'labels': [
                'scl.plugin=network-topology',
                f'scl.topology={topology["id"]}',
                'scl.role=slips-sensor',
            ],
        }

    return compose


def compose_project_name(topology_id):
    return f'scl-topology-{topology_id}'


def topology_network_name(topology_id, network_id):
    return f'{compose_project_name(topology_id)}-{network_id}'


def hackerlab_container_name(topology_id):
    return 'scl-hackerlab'


def resolve_topology_network_name(topology_id, network_id):
    expected_suffix = f'-{network_id}'
    expected_tokens = {
        topology_id.lower(),
        compose_project_name(topology_id).lower(),
        topology_id,
    }
    try:
        output = app.docker_run(['network', 'ls', '--format', '{{.Name}}'])
    except Exception:
        return topology_network_name(topology_id, network_id)
    for name in output.splitlines():
        lowered = name.lower()
        if lowered.endswith(expected_suffix) and any(token.lower() in lowered for token in expected_tokens):
            return name
    return topology_network_name(topology_id, network_id)
