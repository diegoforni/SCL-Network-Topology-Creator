"""
Shared utility functions for Network Topology plugin.

Contains IP allocation, CIDR helpers, validation functions, and other
shared utilities used across the plugin.
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .constants import (
    DEFAULT_NETWORK_PREFIX,
    DEFAULT_TRANSIT_PREFIX,
    HACKERLAB_IP,
    HOST_IP_START,
    HOST_TYPES,
    MAX_HOSTS_PER_NETWORK,
    MAX_NETWORKS,
    ROUTER_IP_DEFAULT,
    ROUTER_IP_MIN,
    SSH_DEFAULT_PASSWORD,
    SSH_DEFAULT_USERNAME,
    TIMESTAMP_FORMAT,
    TRANSIT_NETWORK_CIDR_BITS,
)


# =============================================================================
# Identifier and String Utilities
# =============================================================================


def normalize_identifier(value: str, fallback: str) -> str:
    """
    Normalize a string to be used as an identifier.

    Removes special characters, converts to lowercase, and ensures
    the result is a valid identifier.

    Args:
        value: The string to normalize
        fallback: Fallback value if normalization results in empty string

    Returns:
        Normalized identifier string
    """
    normalized = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(value or '')).strip('-').lower()
    return normalized or fallback


def slugify(value: str) -> str:
    """
    Convert a string to a URL-safe slug.

    Args:
        value: String to slugify

    Returns:
        URL-safe slug string
    """
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug or f'topology-{uuid.uuid4().hex[:8]}'


def shell_quote(value: str) -> str:
    """
    Quote a string for safe use in shell commands.

    Args:
        value: String to quote

    Returns:
        Safely quoted string
    """
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


# =============================================================================
# Timestamp Utilities
# =============================================================================


def now_ts() -> str:
    """
    Get current UTC timestamp in ISO format.

    Returns:
        Current UTC timestamp as ISO string
    """
    return datetime.utcnow().strftime(TIMESTAMP_FORMAT)


def parse_timestamp(ts: str) -> Optional[datetime]:
    """
    Parse an ISO timestamp string.

    Args:
        ts: ISO timestamp string

    Returns:
        datetime object or None if parsing fails
    """
    try:
        return datetime.strptime(ts, TIMESTAMP_FORMAT)
    except (ValueError, TypeError):
        return None


# =============================================================================
# IP Address and CIDR Utilities
# =============================================================================


def subnet_prefix(cidr: str) -> str:
    """
    Extract the subnet prefix from a CIDR block.

    Args:
        cidr: CIDR block (e.g., '10.77.1.0/24')

    Returns:
        Subnet prefix (e.g., '10.77.1')

    Raises:
        ValueError: If CIDR format is invalid
    """
    if not cidr or '/' not in cidr:
        raise ValueError(f"Invalid CIDR format: {cidr}")
    return cidr.split('/')[0].rsplit('.', 1)[0]


def validate_cidr(cidr: str) -> bool:
    """
    Validate a CIDR block format.

    Args:
        cidr: CIDR block to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        parts = cidr.split('/')
        if len(parts) != 2:
            return False
        addr = parts[0].split('.')
        if len(addr) != 4:
            return False
        if not all(0 <= int(octet) <= 255 for octet in addr):
            return False
        prefix = int(parts[1])
        return 0 <= prefix <= 32
    except (ValueError, AttributeError):
        return False


def router_ip(cidr: str, offset: int = 0) -> str:
    """
    Calculate the default router IP for a CIDR block.

    Args:
        cidr: Network CIDR block
        offset: Offset from default (0 for .254, 1 for .253, etc.)

    Returns:
        Router IP address (e.g., '10.77.1.254')

    Raises:
        ValueError: If CIDR format is invalid
    """
    prefix = subnet_prefix(cidr)
    host_octet = max(ROUTER_IP_MIN, ROUTER_IP_DEFAULT - offset)
    return f'{prefix}.{host_octet}'


def host_ip(cidr: str, host_index: int) -> str:
    """
    Calculate the IP address for a host in a network.

    Args:
        cidr: Network CIDR block
        host_index: 1-based host index (1 = first host)

    Returns:
        Host IP address (e.g., '10.77.1.11')

    Raises:
        ValueError: If CIDR format is invalid or index is invalid
    """
    if host_index < 1:
        raise ValueError(f"Host index must be >= 1, got {host_index}")
    prefix = subnet_prefix(cidr)
    return f'{prefix}.{HOST_IP_START + host_index}'


def hackerlab_ip(cidr: str) -> str:
    """
    Calculate the IP for the hackerlab container attachment.

    Args:
        cidr: Network CIDR block

    Returns:
        Hackerlab IP address (e.g., '10.77.1.2')

    Raises:
        ValueError: If CIDR format is invalid
    """
    prefix = subnet_prefix(cidr)
    return f'{prefix}.{HACKERLAB_IP}'


def network_router_ips(network: Dict[str, Any], router_ids: List[str]) -> Dict[str, str]:
    """
    Calculate IP addresses for all routers attached to a network.

    Args:
        network: Network configuration with 'cidr' field
        router_ids: List of router IDs attached to the network

    Returns:
        Mapping of router_id -> IP address
    """
    assigned = list(dict.fromkeys(router_ids))  # Remove duplicates while preserving order
    ip_map = {}
    for offset, router_id in enumerate(assigned):
        if offset == 0:
            host_octet = ROUTER_IP_DEFAULT
        else:
            host_octet = max(ROUTER_IP_MIN, ROUTER_IP_DEFAULT - offset)
        ip_map[router_id] = f"{subnet_prefix(network['cidr'])}.{host_octet}"
    return ip_map


def transit_network_key(parent_id: str, child_id: str) -> str:
    """
    Generate a network key for a transit link between routers.

    Args:
        parent_id: Parent router ID
        child_id: Child router ID

    Returns:
        Transit network key (e.g., 'transit_router1_router2')
    """
    parent_key = normalize_identifier(parent_id, 'router')
    child_key = normalize_identifier(child_id, 'router')
    return f"transit_{parent_key}_{child_key}"


def transit_subnet(index: int) -> str:
    """
    Generate a CIDR block for a transit network.

    Args:
        index: 1-based transit network index

    Returns:
        Transit network CIDR (e.g., '10.250.1.0/29')
    """
    return f'{DEFAULT_TRANSIT_PREFIX}.{index}.0/{TRANSIT_NETWORK_CIDR_BITS}'


def cidr_contains_ip(cidr: str, ip: str) -> bool:
    """
    Check if an IP address is within a CIDR block.

    Args:
        cidr: Network CIDR block
        ip: IP address to check

    Returns:
        True if IP is in the CIDR block, False otherwise
    """
    try:
        network_addr = cidr.split('/')[0]
        prefix_len = int(cidr.split('/')[1])

        ip_parts = list(map(int, ip.split('.')))
        network_parts = list(map(int, network_addr.split('.')))

        # Convert to integers
        ip_int = (ip_parts[0] << 24) + (ip_parts[1] << 16) + (ip_parts[2] << 8) + ip_parts[3]
        network_int = (network_parts[0] << 24) + (network_parts[1] << 16) + \
                      (network_parts[2] << 8) + network_parts[3]

        mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
        return (ip_int & mask) == (network_int & mask)
    except (ValueError, IndexError):
        return False


def is_valid_ip(ip: str) -> bool:
    """
    Validate an IPv4 address format.

    Args:
        ip: IP address string to validate

    Returns:
        True if valid IPv4 address, False otherwise
    """
    try:
        parts = ip.split('.')
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    except (ValueError, AttributeError):
        return False


# =============================================================================
# Validation Functions
# =============================================================================


def validate_host_type(host_type: str) -> bool:
    """
    Validate that a host type is defined.

    Args:
        host_type: Host type to validate

    Returns:
        True if valid host type, False otherwise
    """
    return host_type in HOST_TYPES


def validate_network_count(count: int) -> bool:
    """
    Validate network count is within allowed range.

    Args:
        count: Number of networks

    Returns:
        True if valid, False otherwise
    """
    return 1 <= count <= MAX_NETWORKS


def validate_host_count(count: int) -> bool:
    """
    Validate host count is within allowed range.

    Args:
        count: Number of hosts

    Returns:
        True if valid, False otherwise
    """
    return 1 <= count <= MAX_HOSTS_PER_NETWORK


def validate_ssh_credentials(username: str, password: str) -> Tuple[bool, Optional[str]]:
    """
    Validate SSH credentials.

    Args:
        username: SSH username
        password: SSH password

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not username or not username.strip():
        return False, "SSH username is required"
    if not password:
        return False, "SSH password is required"
    if len(username) > 32:
        return False, "SSH username must be 32 characters or less"
    return True, None


def validate_firewall_rule(rule: str) -> bool:
    """
    Validate a firewall rule format.

    Args:
        rule: Firewall rule string (e.g., 'net1->net2')

    Returns:
        True if valid format, False otherwise
    """
    if not rule or '->' not in rule:
        return False
    parts = rule.split('->')
    if len(parts) != 2:
        return False
    source, dest = parts
    return bool(normalize_identifier(source, '')) and bool(normalize_identifier(dest, ''))


def normalize_agent_list(agents: Any) -> List[str]:
    """
    Normalize and deduplicate an agent list.

    Args:
        agents: Agent list (list, string, or other)

    Returns:
        Normalized list of unique agent strings
    """
    if not isinstance(agents, list):
        agents = []
    return list(dict.fromkeys(str(a) for a in agents if a))


def merge_agent_config(
    agents: Optional[List[str]],
    legacy_enabled: bool = False,
    legacy_type: Optional[str] = None
) -> List[str]:
    """
    Merge legacy and modern agent configuration.

    Args:
        agents: Modern agent list
        legacy_enabled: Legacy agent enabled flag
        legacy_type: Legacy agent type

    Returns:
        Unified list of agents
    """
    result = normalize_agent_list(agents)
    if legacy_enabled and legacy_type and legacy_type not in result:
        result.append(legacy_type)
    return result


# =============================================================================
# File and JSON Utilities
# =============================================================================


def read_json(path: Path) -> Dict[str, Any]:
    """
    Read and parse a JSON file.

    Args:
        path: Path to JSON file

    Returns:
        Parsed JSON as dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If JSON is invalid
    """
    with open(path, 'r', encoding='utf8') as f:
        return json.load(f)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    """
    Write data to a JSON file with formatting.

    Args:
        path: Path to write to (parent directories created if needed)
        data: Data to write as JSON
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf8') as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write('\n')


def ensure_directory(path: Path) -> Path:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path

    Returns:
        The path (for chaining)
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


# =============================================================================
# Router and Network Topology Utilities
# =============================================================================


def build_router_maps(
    routers: List[Dict[str, Any]],
    networks: List[Dict[str, Any]]
) -> Tuple[Dict[str, Dict], Dict[str, List[str]], Dict[str, List[Dict]]]:
    """
    Build helper maps for router hierarchy and network attachments.

    Args:
        routers: List of router configurations
        networks: List of network configurations

    Returns:
        Tuple of:
        - by_id: Mapping of router_id -> router config
        - children: Mapping of router_id -> list of child router IDs
        - networks_by_router: Mapping of router_id -> list of attached networks
    """
    by_id = {router['id']: router for router in routers}
    children = {router['id']: [] for router in routers}
    networks_by_router = {router['id']: [] for router in routers}

    for router in routers:
        parent_id = router.get('parent_router_id') or ''
        if parent_id and parent_id in children:
            children[parent_id].append(router['id'])

    for network in networks:
        owner = network.get('default_router_id') or \
                network.get('router_id') or \
                (routers[0]['id'] if routers else '')
        if owner in networks_by_router:
            networks_by_router[owner].append(network)

    return by_id, children, networks_by_router


def router_descendant_networks(
    router_id: str,
    children_map: Dict[str, List[str]],
    networks_by_router: Dict[str, List[Dict]]
) -> List[Dict[str, Any]]:
    """
    Get all networks attached to a router and its descendants.

    Args:
        router_id: Root router ID
        children_map: Mapping of router_id -> child router IDs
        networks_by_router: Mapping of router_id -> attached networks

    Returns:
        List of network configurations
    """
    nets = list(networks_by_router.get(router_id, []))
    for child_id in children_map.get(router_id, []):
        nets.extend(router_descendant_networks(child_id, children_map, networks_by_router))
    return nets


def get_network_by_id(
    networks: List[Dict[str, Any]],
    network_id: str
) -> Optional[Dict[str, Any]]:
    """
    Find a network by its ID.

    Args:
        networks: List of network configurations
        network_id: Network ID to find

    Returns:
        Network configuration or None if not found
    """
    for network in networks:
        if network.get('id') == network_id:
            return network
    return None


def get_router_by_id(
    routers: List[Dict[str, Any]],
    router_id: str
) -> Optional[Dict[str, Any]]:
    """
    Find a router by its ID.

    Args:
        routers: List of router configurations
        router_id: Router ID to find

    Returns:
        Router configuration or None if not found
    """
    for router in routers:
        if router.get('id') == router_id:
            return router
    return None


def count_hosts_in_topology(topology: Dict[str, Any]) -> int:
    """
    Count total hosts across all networks in a topology.

    Args:
        topology: Topology configuration

    Returns:
        Total number of hosts
    """
    return sum(
        len(network.get('hosts', []))
        for network in topology.get('networks', [])
    )


def get_all_hosts(topology: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get all hosts from all networks with network context.

    Args:
        topology: Topology configuration

    Returns:
        List of host configurations with network_id added
    """
    hosts = []
    for network in topology.get('networks', []):
        network_id = network.get('id')
        for host in network.get('hosts', []):
            host_copy = dict(host)
            host_copy['_network_id'] = network_id
            host_copy['_network_name'] = network.get('name')
            hosts.append(host_copy)
    return hosts


def find_host_by_id(
    topology: Dict[str, Any],
    host_id: str
) -> Optional[Dict[str, Any]]:
    """
    Find a host by its ID across all networks.

    Args:
        topology: Topology configuration
        host_id: Host ID to find

    Returns:
        Host configuration with network context or None if not found
    """
    for network in topology.get('networks', []):
        for host in network.get('hosts', []):
            if host.get('id') == host_id:
                result = dict(host)
                result['_network_id'] = network.get('id')
                result['_network_name'] = network.get('name')
                return result
    return None


# =============================================================================
# Firewall Utilities
# =============================================================================


def build_allowed_pairs(
    networks: List[Dict[str, Any]],
    allowed_rules: Optional[List[str]] = None
) -> Set[str]:
    """
    Build a set of allowed firewall rule pairs.

    Args:
        networks: List of network configurations
        allowed_rules: Optional list of existing allowed rules

    Returns:
        Set of allowed rule strings (format: 'source_id->dest_id')
    """
    if allowed_rules is None:
        allowed_rules = []

    network_ids = {net.get('id') for net in networks if net.get('id')}
    allowed = set()

    for rule in allowed_rules:
        if validate_firewall_rule(rule):
            source, dest = rule.split('->')
            if source in network_ids and dest in network_ids and source != dest:
                allowed.add(rule)

    return allowed


def get_firewall_action(
    source_id: str,
    dest_id: str,
    allowed_rules: Set[str]
) -> str:
    """
    Determine if traffic is allowed between networks.

    Args:
        source_id: Source network ID
        dest_id: Destination network ID
        allowed_rules: Set of allowed firewall rules

    Returns:
        'allow' or 'deny'
    """
    if source_id == dest_id:
        return 'allow'  # Same network always allowed
    rule = f'{source_id}->{dest_id}'
    return 'allow' if rule in allowed_rules else 'deny'


# =============================================================================
# SSH Configuration Utilities
# =============================================================================


def ssh_setup_block(username: str, password: str) -> str:
    """
    Generate shell script block for SSH setup.

    Args:
        username: SSH username
        password: SSH password

    Returns:
        Shell script commands for SSH configuration
    """
    return f"""mkdir -p /var/run/sshd
useradd -m -s /bin/bash {shell_quote(username)} 2>/dev/null || true
echo {shell_quote(username + ':' + password)} | chpasswd || true
/usr/sbin/sshd || true
"""


def get_router_credentials(router: Dict[str, Any]) -> Tuple[str, str]:
    """
    Get SSH credentials for a router with defaults.

    Args:
        router: Router configuration

    Returns:
        Tuple of (username, password)
    """
    return (
        router.get('username') or SSH_DEFAULT_USERNAME,
        router.get('password') or SSH_DEFAULT_PASSWORD
    )


def get_host_credentials(host: Dict[str, Any]) -> Tuple[str, str]:
    """
    Get SSH credentials for a host with defaults.

    Args:
        host: Host configuration

    Returns:
        Tuple of (username, password)
    """
    return (
        host.get('username') or 'student',
        host.get('password') or 'strato'
    )


# =============================================================================
# Data Generation Utilities
# =============================================================================


def default_host_data(
    topology_name: str,
    network_name: str,
    host_name: str,
    host_type: str
) -> str:
    """
    Generate default data content for a host.

    Args:
        topology_name: Topology name
        network_name: Network name
        host_name: Host name
        host_type: Host role type

    Returns:
        Default data content string
    """
    host_label = HOST_TYPES.get(host_type, {}).get('label', host_type)
    return (
        f"Topology: {topology_name}\n"
        f"Network: {network_name}\n"
        f"Host: {host_name}\n"
        f"Role: {host_label}\n"
        "No AI-generated data was requested for this host.\n"
    )


# =============================================================================
# Label and Metadata Utilities
# =============================================================================


def build_topology_labels(
    topology_id: str,
    network_id: Optional[str] = None,
    router_id: Optional[str] = None,
    host_type: Optional[str] = None
) -> List[str]:
    """
    Build Docker container labels for topology components.

    Args:
        topology_id: Topology ID
        network_id: Optional network ID
        router_id: Optional router ID
        host_type: Optional host type

    Returns:
        List of label strings
    """
    from .constants import (
        TOPOLOGY_ID_LABEL,
        NETWORK_ID_LABEL,
        ROUTER_ID_LABEL,
        HOST_TYPE_LABEL,
        TOPOLOGY_LABEL_KEY,
        TOPOLOGY_LABEL_VALUE,
    )

    labels = [
        f'{TOPOLOGY_LABEL_KEY}={TOPOLOGY_LABEL_VALUE}',
        f'{TOPOLOGY_ID_LABEL}={topology_id}',
    ]

    if network_id:
        labels.append(f'{NETWORK_ID_LABEL}={network_id}')
    if router_id:
        labels.append(f'{ROUTER_ID_LABEL}={router_id}')
    if host_type:
        labels.append(f'{HOST_TYPE_LABEL}={host_type}')

    return labels


def generate_service_name(
    topology_id: str,
    network_id: str,
    host_id: str
) -> str:
    """
    Generate a Docker service name for a host.

    Args:
        topology_id: Topology ID
        network_id: Network ID
        host_id: Host ID

    Returns:
        Service name (e.g., 'scl-topology-test1-dmz-h1')
    """
    slug_id = slugify(topology_id)
    return f'scl-topology-{slug_id}-{network_id}-{host_id}'


def generate_router_service_name(
    topology_id: str,
    router_id: str
) -> str:
    """
    Generate a Docker service name for a router.

    Args:
        topology_id: Topology ID
        router_id: Router ID

    Returns:
        Service name (e.g., 'scl-topology-test1-router-core')
    """
    slug_id = slugify(topology_id)
    router_key = normalize_identifier(router_id, 'router')
    return f'scl-topology-{slug_id}-router-{router_key}'
