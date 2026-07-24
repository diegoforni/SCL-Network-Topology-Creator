"""
Shared constants for Network Topology plugin.

Contains base images, port mappings, labels, and other constants
used across the plugin components.
"""

# Default container images
BASE_IMAGE = "scl-plugin-network-topology-ubuntu:0.1"
OPENCODE_IMAGE = "scl-plugin-network-topology-ubuntu-opencode:0.1"

# Host role types with their configurations
HOST_TYPES = {
    "web-server": {
        "label": "Web server",
        "ports": ["80/tcp"],
        "description": "HTTP service with seeded web content.",
    },
    "db": {
        "label": "Database",
        "ports": ["5432/tcp", "3306/tcp"],
        "description": "Database-like host with SQLite seed data and DB service hints.",
    },
    "file-server": {
        "label": "File server",
        "ports": ["8080/tcp"],
        "description": "HTTP file share seeded with documents.",
    },
    "domain-admin": {
        "label": "Domain admin",
        "ports": ["22/tcp"],
        "description": "Privileged workstation identity for AD-style exercises.",
    },
    "normal-user": {
        "label": "Normal user",
        "ports": ["22/tcp"],
        "description": "Standard workstation user with low privileges.",
    },
    "jump-box": {
        "label": "Jump box",
        "ports": ["22/tcp"],
        "description": "Pivot host intended to bridge access paths.",
    },
    "log-server": {
        "label": "Log server",
        "ports": ["514/tcp"],
        "description": "Host prepared with log files for investigation.",
    },
}

# Default network CIDR patterns
DEFAULT_NETWORK_PREFIX = "10.77"
DEFAULT_TRANSIT_PREFIX = "10.250"

# Port mappings for Docker services
PLAYGROUND_NETWORK = "playground-net"

# Docker labels for SCL topology containers
TOPOLOGY_LABEL_KEY = "scl.plugin"
TOPOLOGY_LABEL_VALUE = "network-topology"
TOPOLOGY_ID_LABEL = "scl.topology"
NETWORK_ID_LABEL = "scl.network"
ROUTER_ID_LABEL = "scl.router"
HOST_TYPE_LABEL = "scl.host_type"

# Network configuration limits
MAX_NETWORKS = 8
MAX_HOSTS_PER_NETWORK = 24
MIN_HOSTS_PER_NETWORK = 1

# Transit network configuration
TRANSIT_NETWORK_CIDR_BITS = 29  # /29 = 8 addresses (6 usable)

# IP allocation ranges
HOST_IP_START = 10  # First host IP (e.g., .10)
ROUTER_IP_DEFAULT = 254  # Default router IP (e.g., .254)
ROUTER_IP_MIN = 240  # Minimum router IP for additional routers
HACKERLAB_IP = 2  # Hackerlab container IP (e.g., .2)

# Service ports by host type
SERVICE_PORTS = {
    "web-server": 80,
    "file-server": 8080,
    "db": 5432,
    "log-server": 514,
}

# SSH configuration
SSH_PORT = 22
SSH_DEFAULT_USERNAME = "admin"
SSH_DEFAULT_PASSWORD = "strato"
HOST_DEFAULT_USERNAME = "student"
HOST_DEFAULT_PASSWORD = "strato"

# File paths within containers
HOST_METADATA_PATH = "/etc/scl-host.json"
HOST_DATA_PATH = "/srv/scl-data"
HOST_WWW_PATH = "/srv/www"
HOST_FILES_PATH = "/srv/files"
HOST_DB_PATH = "/srv/db"
HOST_LOG_PATH = "/var/log/scl"
AGENT_SCRIPTS_PATH = "/app/agents"
SHARED_MODULES_PATH = "/opt/shared"

# OpenCode configuration
OPENCODE_CONFIG_PATH = "/root/.config/opencode/opencode.json"
OPENCODE_AUTH_PATH = "/root/.local/share/opencode/auth.json"
OPENCODE_LOG_PATH = "/var/log/opencode"
OPENCODE_PORT = 4096
OPENCODE_HEALTH_ENDPOINT = "http://localhost:4096/global/health"

# Validation error messages
ERR_TOPOLOGY_NAME_REQUIRED = "Topology name is required."
ERR_AT_LEAST_ONE_NETWORK = "At least one network is required."
ERR_MAX_NETWORKS = f"At most {MAX_NETWORKS} networks are supported."
ERR_NETWORK_NEEDS_HOSTS = "Network '{name}' needs at least one host."
ERR_MAX_HOSTS = "Network '{name}' has more than {max} hosts."
ERR_DUPLICATE_NETWORK_ID = "Duplicate network id '{id}'."
ERR_ROOT_ROUTER_REQUIRED = "At least one router is required."
ERR_HOSTS_REQUIRED = "At least one host is required."

# Docker Compose service name templates
SERVICE_NAME_ROUTER = "router-{router_key}"
SERVICE_NAME_HOST = "{network_id}-{host_id}"
SERVICE_NAME_PLAYGROUND = "scl-hackerlab"

# Network name templates
NETWORK_NAME_TEMPLATE = "{project_prefix}-{network_id}"
NETWORK_KEY_TEMPLATE = "topo_{network_id}"
TRANSIT_NETWORK_KEY = "transit_{parent_router}_{child_router}"

# Project naming
PROJECT_PREFIX_TEMPLATE = "scl-topology-{topology_id}"

# Environment variables
ENV_TOPOLOGY_DATA_DIR = "TOPOLOGY_DATA_DIR"
ENV_LLM_URL = "DASHBOARD_LLM_URL"
ENV_LLM_URL_FULL = "LLM_URL"
ENV_LLM_MODEL = "LLM_MODEL"
ENV_OPENCODE_API_KEY = "OPENCODE_API_KEY"
ENV_AGENTS_HOST_PATH = "AGENTS_HOST_PATH"

# Default values
DEFAULT_TOPOLOGY_DATA_DIR = "/app/data"
DEFAULT_LLM_URL = "http://dashboard/api/llm/chat"
DEFAULT_LLM_URL_FULL = "https://llm.ai.e-infra.cz/v1"
DEFAULT_LLM_MODEL = "glm-5.2"
DEFAULT_AGENTS_HOST_PATH = "/agent-scripts"

# Topology file names
TOPOLOGY_JSON_FILE = "topology.json"
COMPOSE_YAML_FILE = "docker-compose.yml"
TOPOLOGIES_DIR_NAME = "topologies"

# Job statuses
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"

# Timestamp format
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Legacy SSH lab topology name (auto-removed on list)
LEGACY_SSH_LAB_NAME = "ssh lab"

# Preset topology names
PRESET_BALANCED = "Balanced three-zone lab"
PRESET_ENTERPRISE = "Enterprise segmented lab"

# Default topology values
DEFAULT_NETWORK_COUNT = 3
DEFAULT_HOSTS_PER_NETWORK = 3
DEFAULT_TOPOLOGY_NAME = "Corporate training lab"
DEFAULT_ROUTER_NAME = "core"

# Network name suggestions for presets
NETWORK_NAME_PRESETS = [
    "dmz", "corp", "admin", "data", "lab", "guest", "ops", "dev"
]

# Firewall rule format
FIREWALL_RULE_SEPARATOR = "->"

# Visual layout defaults
VISUAL_CANVAS_WIDTH = 900
VISUAL_CANVAS_HEIGHT = 560
VISUAL_ROUTER_Y = 115
VISUAL_NETWORK_Y = 385
VISUAL_NODE_MIN_X = 60
VISUAL_ROUTER_MIN_Y = 70
VISUAL_ROUTER_MAX_Y = 220
VISUAL_NETWORK_MIN_Y = 300
VISUAL_NETWORK_MAX_Y = 500  # Will be adjusted dynamically
