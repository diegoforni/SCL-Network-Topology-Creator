import re
import time
import uuid


def now_ts():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def slugify(value):
    slug = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return slug or f'topology-{uuid.uuid4().hex[:8]}'


def normalize_identifier(value, fallback):
    normalized = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(value or '')).strip('-').lower()
    return normalized or fallback


def host_agents(host):
    """Get list of agents configured for a host. Returns empty list if no agents."""
    agents = host.get('agents', [])
    if not isinstance(agents, list):
        agents = []
    agents = [str(a) for a in agents if a]
    legacy_enabled = bool(host.get('agent_enabled', False))
    legacy_type = str(host.get('agent_type', '') or '')
    if legacy_enabled and legacy_type and legacy_type not in agents:
        agents.append(legacy_type)
    return list(dict.fromkeys(agents))


def shell_quote(value):
    return "'" + str(value).replace("'", "'\"'\"'") + "'"
