import json
import os
import subprocess

import app


def run_compose(topology_id, args):
    file = app.compose_path(topology_id)
    if not file.exists():
        raise FileNotFoundError('Generated docker-compose.yml not found. Save the topology first.')
    cmd = app.docker_command() + ['-p', app.compose_project_name(topology_id), '-f', str(file)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f'Compose command failed: {cmd}')
    return result.stdout


def start_topology(topology_id, force_rebuild=False):
    app.ensure_base_image()
    topology = app.read_json(app.topology_path(topology_id))

    # Ensure OS-specific OpenCode images are built
    # Check environment variable for force rebuild
    if os.environ.get('FORCE_REBUILD_IMAGES', '').lower() in ('1', 'true', 'yes'):
        force_rebuild = True

    opencode_images = app.ensure_opencode_images(topology, force_rebuild=force_rebuild)
    app.ensure_slips_image(topology)
    app.ensure_repo_image(topology, force_rebuild=force_rebuild)
    app.ensure_ad_image(topology, force_rebuild=force_rebuild)
    app.ensure_rdp_image(topology, force_rebuild=force_rebuild)
    app.ensure_web_image(topology, force_rebuild=force_rebuild)
    app.ensure_smb_image(topology, force_rebuild=force_rebuild)
    app.ensure_coder56_mcp_image(topology, force_rebuild=force_rebuild)

    compose = app.generate_compose(topology, opencode_images)
    with open(app.compose_path(topology_id), 'w', encoding='utf8') as file:
        json.dump(compose, file, indent=2)
        file.write('\n')
    try:
        run_compose(topology_id, ['down'])
    except Exception:
        pass
    run_compose(topology_id, ['up', '-d', '--remove-orphans'])
    sync_hackerlab_runtime(topology)
    return {'status': 'started'}


def cleanup_topology_networks(topology_id):
    """Remove a topology's docker networks after ``compose down``.

    ``docker compose down`` cannot delete a per-topology network while an
    external endpoint is still attached to it. ``sync_hackerlab_runtime``
    connects the shared ``scl-hackerlab`` container to one of the topology's
    networks on start, and that container is not owned by the compose project,
    so compose leaves the network behind. Stopped topologies then leak their
    ``10.77.x`` networks and block any later topology that reuses the same
    subnet (its start fails silently inside the background compose job).

    Force-disconnect every attached endpoint and delete each network
    explicitly. Idempotent and best-effort: a missing network or an individual
    failure is swallowed so one bad network cannot abort cleanup of the rest.
    """
    path = app.topology_path(topology_id)
    topology = app.read_json(path) if path.exists() else {}
    removed = []
    for network in topology.get('networks') or []:
        network_id = network.get('id')
        if not network_id:
            continue
        name = app.resolve_topology_network_name(topology_id, network_id)
        try:
            details = docker_inspect_json(['network', 'inspect', name]) or {}
            # `docker network inspect` returns a JSON list; normalize to the
            # single network dict so .get('Containers') works below.
            if isinstance(details, list):
                details = details[0] if details else {}
        except Exception:
            details = {}
        for endpoint in (details.get('Containers') or {}).values():
            endpoint_name = endpoint.get('Name')
            if endpoint_name:
                try:
                    docker_run(['network', 'disconnect', '-f', name, endpoint_name])
                except Exception:
                    pass
        try:
            docker_run(['network', 'rm', name])
            removed.append(name)
        except Exception:
            pass
    return removed


def stop_topology(topology_id):
    try:
        run_compose(topology_id, ['down'])
    except Exception:
        # Cleanup must still run even if compose down failed (e.g. the compose
        # file was already removed); otherwise the topology's networks leak.
        pass
    cleanup_topology_networks(topology_id)
    return {'status': 'stopped'}


def docker_inspect_json(args):
    result = subprocess.run(['docker'] + args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f'Docker command failed: {args}')
    output = result.stdout.strip()
    if not output:
        return {}
    return json.loads(output)


def docker_run(args):
    result = subprocess.run(['docker'] + args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f'Docker command failed: {args}')
    return result.stdout


def sync_hackerlab_runtime(topology):
    infrastructure = topology.get('infrastructure') or {}
    network_id = infrastructure.get('hackerlab_network_id')
    networks = topology.get('networks') or []
    selected_network = next((network for network in networks if network.get('id') == network_id), None)
    if not selected_network:
        return {'status': 'skipped'}
    container_name = app.hackerlab_container_name(topology['id'])
    selected_network_name = app.resolve_topology_network_name(topology['id'], selected_network['id'])
    router_ids = selected_network.get('router_ids') or [selected_network.get('default_router_id') or '']
    router_ids = [router_id for router_id in dict.fromkeys(router_ids) if router_id]
    if not router_ids:
        router_ids = [selected_network.get('default_router_id') or '']
    ip_map = app.network_router_ips(selected_network, router_ids)
    gateway_router_id = selected_network.get('default_router_id') or router_ids[0]
    gateway_ip = ip_map.get(gateway_router_id) or app.router_ip(selected_network['cidr'])
    hacker_ip = app.hackerlab_ip(selected_network['cidr'])
    try:
        current_networks = docker_inspect_json(['inspect', '-f', '{{json .NetworkSettings.Networks}}', container_name]) or {}
    except Exception:
        return {'status': 'missing'}
    attached_network_names = [name for name in current_networks.keys() if topology['id'].lower() in name.lower() and name != 'scl-playground-net']
    for network_name in attached_network_names:
        if network_name != selected_network_name:
            try:
                docker_run(['network', 'disconnect', '-f', network_name, container_name])
            except Exception:
                pass
    try:
        docker_run(['network', 'disconnect', '-f', selected_network_name, container_name])
    except Exception:
        pass
    docker_run(['network', 'connect', '--ip', hacker_ip, selected_network_name, container_name])
    docker_run(['exec', container_name, 'sh', '-lc', f'ip route replace default via {gateway_ip} || true'])
    return {'status': 'updated', 'network': selected_network_name}


def recreate_host(topology_id, host_id, force_rebuild=False):
    """Regenerate compose and recreate a single host's container in place.

    This is the canonical entry point used by the agent-manager plugin after it
    mutates a host's `agents` list: because this plugin owns the topology's
    docker-compose file, the Docker socket, and the per-host OpenCode image
    build, the recreation must happen here (the agent-manager has no local
    compose file). Only the one host's service is recreated (--no-deps,
    --force-recreate); the rest of the topology is left running.

    Returns a dict with 'status' one of: 'recreated' (container rebuilt),
    'skipped' (topology not running — compose is regenerated so the change
    applies on next start), or raises on error.
    """
    path = app.topology_path(topology_id)
    if not path.exists():
        raise FileNotFoundError(f"Topology '{topology_id}' not found.")
    topology = app.read_json(path)

    service_name = None
    for network in topology.get('networks', []):
        for host in network.get('hosts', []):
            if host.get('id') == host_id:
                service_name = f'{network["id"]}-{host_id}'
                break
        if service_name:
            break
    if not service_name:
        raise KeyError(f"Host '{host_id}' not found in topology '{topology_id}'.")

    if not is_running(topology_id):
        return {
            'status': 'skipped',
            'service': service_name,
            'message': 'Topology is not running; compose regenerated, change applies on next start.',
        }

    app.ensure_base_image()
    if os.environ.get('FORCE_REBUILD_IMAGES', '').lower() in ('1', 'true', 'yes'):
        force_rebuild = True
    opencode_images = app.ensure_opencode_images(topology, force_rebuild=force_rebuild)
    app.ensure_slips_image(topology)

    compose = app.generate_compose(topology, opencode_images)
    with open(app.compose_path(topology_id), 'w', encoding='utf8') as file:
        json.dump(compose, file, indent=2)
        file.write('\n')

    run_compose(topology_id, ['up', '-d', '--force-recreate', '--no-deps', service_name])

    return {
        'status': 'recreated',
        'service': service_name,
        'container': f'scl-topology-{topology_id}-{service_name}',
    }


def is_running(topology_id):
    file = app.compose_path(topology_id)
    if not file.exists():
        return False
    try:
        output = run_compose(topology_id, ['ps', '--services', '--filter', 'status=running'])
    except Exception:
        return False
    return bool(output.strip())
