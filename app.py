# Ensure `import app` from sibling modules resolves to THIS module whether it
# was loaded as '__main__' (``python app.py`` / the container CMD) or as 'app'
# (``import app``). Without this, running app.py directly creates a SECOND
# 'app' module object and the submodules' ``app.<name>`` references miss the
# config/re-exports (AttributeError: module 'app' has no attribute ...).
# No-op under ``import app`` (sys.modules['app'] already set to this module).
import sys as _sys
_sys.modules.setdefault('app', _sys.modules[__name__])

import json
import datetime
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import unquote, urlsplit

# Load environment variables from .env file if it exists
ENV_FILE = Path(__file__).parent / '.env'
if ENV_FILE.exists():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

# Add agents directory to sys.path for importing generate_opencode_config
# The opencode_config.py ships inside this plugin's own images directory
# (plugins/network-topology/images/scl-plugin-network-topology-ubuntu/).
# In the plugin container the host images dir is mounted at /app/images
# (see docker-compose.yml: ${IMAGES_DIR:-./images}:/app/images:ro); for local
# development (app.py run on the host) the images live in this plugin's own
# ./images directory — fully self-contained, no stratocyberlab-level images dir.
AGENTS_DIR = None
possible_paths = [
    Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-plugin-network-topology-ubuntu',
    Path('/app/images/scl-plugin-network-topology-ubuntu'),
    Path(__file__).parent / 'images' / 'scl-plugin-network-topology-ubuntu',
]

for possible_path in possible_paths:
    if possible_path.exists() and (possible_path / 'opencode_config.py').exists():
        AGENTS_DIR = possible_path
        break

if AGENTS_DIR and str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
    print(f"📂 Added {AGENTS_DIR} to sys.path for opencode_config import")

try:
    from opencode_config import generate_opencode_config, AGENT_TEMPLATES
    print(f"✅ Successfully imported opencode_config: generate_opencode_config={generate_opencode_config is not None}")
except ImportError as e:
    print(f"⚠️ Failed to import opencode_config: {e}")
    generate_opencode_config = None
    AGENT_TEMPLATES = {}

# --- Guardrail config: agent types subject to external auditing, and per-agent goals.
# Prefer importing GUARDED_AGENTS / GUARDRAIL_GOALS from opencode_config.py when present
# (single source of truth alongside the agent prompts). Fall back to hardcoded
# canonical values if the module does not yet export them.
GUARDED_AGENTS = ("coder56", "soc_god")
GUARDRAIL_GOALS = {
    "coder56": "Advance the assigned red-team objective (recon, exploitation, persistence) against the target.",
    "soc_god": "Defend: analyze IDS alerts and remediate without losing SSH/443/4096 connectivity.",
}
try:
    from opencode_config import GUARDED_AGENTS as _GUARDED_AGENTS, GUARDRAIL_GOALS as _GUARDRAIL_GOALS  # type: ignore
    GUARDED_AGENTS = tuple(_GUARDED_AGENTS)
    GUARDRAIL_GOALS = dict(_GUARDRAIL_GOALS)
    print(f"✅ Imported guardrail config from opencode_config: GUARDED_AGENTS={GUARDED_AGENTS}")
except ImportError:
    print("ℹ️ opencode_config does not export GUARDED_AGENTS/GUARDRAIL_GOALS; using hardcoded canonical values.")
except Exception as _e:  # defensive: bad shapes, partial exports, etc.
    print(f"⚠️ Failed to import guardrail config from opencode_config ({_e}); using hardcoded canonical values.")

HOST = '0.0.0.0'
PORT = 9002
DATA_DIR = Path(os.environ.get('TOPOLOGY_DATA_DIR', '/app/data'))
TOPOLOGIES_DIR = DATA_DIR / 'topologies'
# Preset topologies ship with the plugin code (read-only, version-controlled),
# not on the runtime data volume. Overridable for tests/deployment.
PRESETS_DIR = Path(os.environ.get('TOPOLOGY_PRESETS_DIR', str(Path(__file__).resolve().parent / 'presets')))
BASE_IMAGE = 'scl-plugin-network-topology-ubuntu:0.1'
OPENCODE_IMAGE = 'scl-plugin-network-topology-ubuntu-opencode:0.1'
SLIPS_IMAGE = 'scl-slips-sensor:0.1'
# Dedicated image for `repo-server` hosts — serves an entire external Git repo
# (cloned at build time, see images/scl-repo-host/Dockerfile). Override the
# cloned repo with the REPO_HOST_URL env var.
REPO_HOST_IMAGE = 'scl-repo-host:0.1'
REPO_HOST_URL = os.environ.get(
    'REPO_HOST_URL', 'https://github.com/JuanLoncharich/accion_del_sur'
)
# Dedicated image for `ad-server` hosts — a Samba 4 AD DC emulating a Windows Active
# Directory domain controller. It serves Kerberos/LDAP/SMB, exposes AS-REP roasting +
# Kerberoasting, and holds a protected "passwords" data blob. The domain is provisioned
# at first boot by the baked supervisor (see images/scl-ad-host/); no runtime egress needed.
AD_HOST_IMAGE = 'scl-ad-host:0.1'
# Dedicated image for `windows-client` hosts — a real RDP server (xrdp, speaks the
# actual MS-RDP protocol) + Microsoft's real PowerShell (pwsh) on Linux, emulating a
# Windows desktop client. Takeover = a predictable weak RDP credential -> become the
# low-priv user -> read the planted root SSH key -> ssh in as root. Provisioned at first
# boot by the baked supervisor (see images/scl-rdp-host/); no runtime egress needed.
RDP_HOST_IMAGE = 'scl-rdp-host:0.1'
# Dedicated image for `vuln-web-server` hosts — a lighttpd (:80) + sshd (:22) web host
# carrying Shellshock (CVE-2014-6271): a CGI status script whose interpreter is a
# deliberately-vulnerable old bash, so a single crafted HTTP header yields direct RCE
# as the web user. Provisioned at first boot by the baked supervisor
# (see images/scl-web-host/); no runtime egress needed.
WEB_HOST_IMAGE = 'scl-web-host:0.1'
# Dedicated image for `smb-server` hosts — Samba baked in at build time (the
# package install needs host-side internet access, which an isolated,
# non-internet topology subnet doesn't have at container-runtime).
SMB_HOST_IMAGE = 'scl-smb-server:0.1'

# Map of base OS images to their OpenCode-enabled variants
# Built dynamically by ensure_opencode_images()
OPENCODE_IMAGES_CACHE = {}
LLM_URL = os.environ.get('DASHBOARD_LLM_URL', 'http://dashboard/api/llm/chat')
OPENCODE_API_KEY = os.environ.get('OPENCODE_API_KEY', '')
LLM_URL_FULL = os.environ.get('LLM_URL', 'https://llm.ai.e-infra.cz/v1')
LLM_MODEL = os.environ.get('LLM_MODEL', 'glm-5.2')
AGENTS_HOST_PATH = os.environ.get('AGENTS_HOST_PATH', '/agent-scripts')
# Host path bind-mounted (rw) into opencode + SLIPS topology containers so their
# run logs persist on the host and are visible to the agent-manager's Replay.
# Must match the agent-manager's OUTPUTS_DIR host path.
OUTPUTS_HOST_PATH = os.environ.get('OUTPUTS_HOST_PATH', '/tmp/outputs')
SERVER = None
JOBS = {}
JOBS_LOCK = threading.Lock()

HOST_TYPES = {
    'web-server': {
        'label': 'Web server',
        'ports': ['80/tcp'],
        'description': 'HTTP service with seeded web content.',
    },
    'db': {
        'label': 'Database',
        'ports': ['5432/tcp', '3306/tcp'],
        'description': 'Database-like host with SQLite seed data and DB service hints.',
    },
    'file-server': {
        'label': 'File server',
        'ports': ['8080/tcp'],
        'description': 'HTTP file share seeded with documents.',
    },
    'domain-admin': {
        'label': 'Domain admin',
        'ports': ['22/tcp'],
        'description': 'Privileged workstation identity for AD-style exercises.',
    },
    'normal-user': {
        'label': 'Normal user',
        'ports': ['22/tcp'],
        'description': 'Standard workstation user with low privileges.',
    },
    'jump-box': {
        'label': 'Jump box',
        'ports': ['22/tcp'],
        'description': 'Pivot host intended to bridge access paths.',
    },
    'log-server': {
        'label': 'Log server',
        'ports': ['514/tcp'],
        'description': 'Host prepared with log files for investigation.',
    },
    'repo-server': {
        'label': 'Repo server',
        'ports': ['80/tcp', '3001/tcp', '3306/tcp'],
        'description': 'Runs an external Git repo as a full-stack app (frontend on :80, Node API on :3001, MariaDB on :3306). Repo + config baked into the image at build time.',
    },
    'ad-server': {
        'label': 'Active Directory (Samba DC)',
        'ports': ['53/tcp', '88/tcp', '135/tcp', '139/tcp', '389/tcp', '445/tcp', '464/tcp', '3268/tcp'],
        'description': 'Windows Active Directory emulated with a Samba 4 AD DC. Serves Kerberos (:88), LDAP (:389), SMB (:445). Exposes AS-REP roasting + Kerberoasting and a protected passwords blob; domain provisioned at first boot.',
    },
    'windows-client': {
        'label': 'Windows client (RDP + PowerShell)',
        'ports': ['3389/tcp', '22/tcp'],
        'description': 'Windows desktop client emulated with a real RDP server (xrdp :3389) + Microsoft PowerShell (pwsh). Carries a predictable weak RDP credential -> planted root SSH key -> ssh root; provisioned at first boot. Used for client_1/client_2.',
    },
    'vuln-web-server': {
        'label': 'Web server (lighttpd + SSH, Shellshock RCE)',
        'ports': ['80/tcp', '22/tcp'],
        'description': 'Linux web host running lighttpd (:80, static site + a /cgi-bin/status.sh CGI) + sshd (:22, bash shell). Carries a common web-server RCE (Shellshock CVE-2014-6271): the CGI interpreter is a vulnerable old bash, so one crafted HTTP header yields direct RCE as the web user; weak SSH creds are a fallback. Provisioned at first boot.',
    },
    'smb-server': {
        'label': 'SMB server (vulnerable)',
        'ports': ['445/tcp', '139/tcp'],
        'description': 'Samba file share deliberately misconfigured (anonymous guest access, world-writable share) so it is exploitable without any authentication, holding seeded private data.',
    },
    'exfil-listener': {
        'label': 'Exfil listener',
        'ports': [],
        'description': 'Attacker-controlled listener that receives and stores exfiltrated data — no auth, no vulnerability, it is already attacker-owned.',
    },
}


# ============================================================================
# LOGIC LIVES IN SIBLING MODULES. This file is the config registry + re-export
# hub. Re-exporting keeps `from app import X` working, and because every module
# reads config/cross-module symbols via `app.<name>`, the test suite's
# monkeypatch.setattr(app, 'X', ...) propagates to every call site unchanged.
# ============================================================================
from helpers import *          # noqa: E402,F401,F403
from store import *            # noqa: E402,F401,F403
from netmath import *          # noqa: E402,F401,F403
from jobs import *             # noqa: E402,F401,F403
from llm import *              # noqa: E402,F401,F403
from scripts import *          # noqa: E402,F401,F403
from images import *           # noqa: E402,F401,F403
from compose import *          # noqa: E402,F401,F403
from docker_ops import *       # noqa: E402,F401,F403
from topology_model import *   # noqa: E402,F401,F403
from http_handlers import *    # noqa: E402,F401,F403
from server import *           # noqa: E402,F401,F403

if __name__ == '__main__':
    main()
