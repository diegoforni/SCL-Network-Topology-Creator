import os
import subprocess
from pathlib import Path

import app


def docker_command():
    if subprocess.run(['docker', 'compose', 'version'], capture_output=True, text=True).returncode == 0:
        return ['docker', 'compose']
    return ['docker-compose']


def get_opencode_image_name(base_image):
    """Generate OpenCode image name from base image name.

    Examples:
        ubuntu:24.04 -> ubuntu-24.04-opencode:0.1
        debian:bookworm -> debian-bookworm-opencode:0.1
        kalilinux/kali-rolling:latest -> kali-rolling-opencode:0.1
    """
    # Normalize image name
    base_name = base_image.replace('/', '-').replace(':', '-')
    # Remove 'latest' suffix if present
    if base_name.endswith('-latest'):
        base_name = base_name[:-7]
    return f'{base_name}-opencode:0.1'


def build_opencode_image(base_image, opencode_image):
    """Build an OpenCode-enabled variant of a base OS image.

    IMPORTANT APPROACH: This function ADDS OpenCode on top of existing images.
    It does NOT replace SCL's packages - it only adds what's needed for agents.

    For Ubuntu/Debian-based images: Uses SCL's BASE_IMAGE as foundation to
    automatically inherit all SCL packages (bash, curl, sqlite3, etc.). This
    ensures automatic compatibility with new SCL host types.

    For other images (Kali, etc.): Builds from base but includes all essentials.

    This function creates a Docker image that includes:
    - Everything from the foundation image (SCL packages or base OS)
    - OpenCode server (HTTP API for agents) - ADDED
    - labuser account for SSH access - ADDED
    - Python requests library - ADDED

    Args:
        base_image: User-selected base image (ubuntu:24.04, debian:bookworm, etc.)
        opencode_image: Name for the resulting OpenCode-enabled image
    """
    print(f"🔨 Building OpenCode image: {opencode_image} from {base_image}")

    # SCL's BASE_IMAGE is built from python:3.12-alpine (Alpine-based)
    # This is a known fact from the Dockerfile
    BASE_IMAGE_IS_ALPINE = True  # scl-plugin-network-topology-ubuntu:0.1 uses python:3.12-alpine

    # Determine foundation image and what needs to be added
    if base_image.startswith(('ubuntu:', 'debian:')):
        # Use SCL BASE_IMAGE to automatically inherit SCL packages
        foundation_image = app.BASE_IMAGE
        print(f"   Foundation: {app.BASE_IMAGE} (Alpine-based, will inherit SCL packages)")
        needs_scl_packages = False  # Already in BASE_IMAGE
        foundation_is_alpine = BASE_IMAGE_IS_ALPINE
    else:
        # Build from user-selected image
        foundation_image = base_image
        print(f"   Foundation: {base_image} (user-selected image)")
        needs_scl_packages = True
        # Check if user image is Alpine-based
        foundation_is_alpine = 'alpine' in base_image.lower()

    # Detect OS family for package installation
    if foundation_is_alpine or 'alpine' in foundation_image.lower():
        package_manager = 'apk'
        install_cmd = 'apk add --no-cache'
        curl_cmd = 'wget -qO-'
        bash_path = 'sh'  # Alpine uses ash/sh, avoid /bin/sh explicit path
        useradd_cmd = 'adduser -D -s /bin/bash labuser'
    else:
        package_manager = 'apt'
        install_cmd = 'apt-get update && apt-get install -y --no-install-recommends'
        curl_cmd = 'curl -fsSL'
        bash_path = '/bin/bash'
        useradd_cmd = 'useradd -m -s /bin/bash labuser'

    # Determine what packages to ADD (not replace)
    if needs_scl_packages:
        # Need to add SCL essentials + OpenCode
        if package_manager == 'apk':
            # Alpine uses 'sqlite' for the CLI tool, not 'sqlite3'
            scl_packages = 'bash curl sudo sqlite openssh openssh-server'
        else:
            scl_packages = 'bash curl sudo sqlite3 openssh-server python3 python3-pip'
    else:
        # SCL packages already in BASE_IMAGE, only add OpenCode needs
        if package_manager == 'apk':
            # Alpine BASE_IMAGE needs bash, curl, sudo, openssh added
            # Also add libstdc++ and libgcc for OpenCode binary
            # And sqlite for the sqlite3 CLI tool
            scl_packages = 'bash curl sudo openssh openssh-server libstdc++ libgcc sqlite iptables iproute2'
        else:
            scl_packages = ''  # Everything already there

    print(f"   Package manager: {package_manager}")
    print(f"   Installing: {scl_packages if scl_packages else '(no additional packages needed)'}")

    # Pre-compute the OpenCode install command to avoid f-string complexity
    if package_manager == 'apk':
        # For Alpine, use bash (which we just installed) with fallback to sh
        opencode_install = f"RUN {curl_cmd} https://opencode.ai/install | bash -s -- --version 1.18.3 2>/dev/null || {curl_cmd} https://opencode.ai/install | sh -s -- --version 1.18.3"
    else:
        opencode_install = f"RUN {curl_cmd} https://opencode.ai/install | {bash_path} -s -- --version 1.18.3"

    dockerfile = f"""FROM {foundation_image}

# Set environment for non-interactive install
ENV DEBIAN_FRONTEND=noninteractive

# ADD packages needed for OpenCode (only what's not already there)
{f'RUN {install_cmd} {scl_packages} && rm -rf /var/lib/apt/lists/* 2>/dev/null || true' if scl_packages else '# All required packages already in foundation image'}

# Install OpenCode (ADD only)
{opencode_install}

# Add OpenCode to PATH
ENV PATH="/root/.opencode/bin:${{PATH}}"
RUN ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode

# Create required directories for OpenCode agents
RUN mkdir -p \\
    /root/.config/opencode \\
    /root/.local/share/opencode \\
    /opt/shared \\
    /opt/agents \\
    /var/log/agent \\
    /secrets \\
    /var/run/sshd

# Install Python requests library (ADD only)
RUN pip3 install --no-cache-dir requests --break-system-packages 2>/dev/null || \\
    pip3 install --no-cache-dir requests || \\
    apk add --no-cache py3-requests 2>/dev/null || true

# Create labuser for SSH access (ADD only)
{f'RUN adduser -D -s /bin/bash labuser || useradd -m -s /bin/bash labuser' if package_manager == 'apk' else f'RUN useradd -m -s /bin/bash labuser'}
RUN echo "labuser ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/labuser && \\
    chmod 440 /etc/sudoers.d/labuser && \\
    ssh-keygen -A 2>/dev/null || true

# Copy OpenCode configuration file with {{env:}} placeholders
# This is a fallback config - opencode_agent_block will override with selected agents
# OpenCode will substitute {{env:VAR_NAME}} with actual environment variable values
RUN printf '{{\\n  "$schema": "https://opencode.ai/config.json",\\n  "provider": {{\\n    "e-infra-chat": {{\\n      "npm": "@ai-sdk/openai-compatible",\\n      "name": "e-INFRA CZ Chat API",\\n      "options": {{\\n        "baseURL": "{{{{env:LLM_URL}}}}",\\n        "apiKey": "{{{{env:OPENCODE_API_KEY}}}}"\\n      }},\\n      "models": {{\\n        "glm-5.2": {{\\n          "name": "GLM-5.2",\\n          "limit": {{\\n            "context": 200000,\\n            "output": 65536\\n          }}\\n        }}\\n      }}\\n    }}\\n  }},\\n  "model": "e-infra-chat/glm-5.2",\\n  "autoupdate": false,\\n  "subagent_depth": 2,\\n  "tools": {{\\n    "skill": true,\\n    "task": true,\\n    "bash": true\\n  }},\\n  "permission": {{\\n    "default": "allow",\\n    "edit": {{ "*": "allow" }},\\n    "write": {{ "*": "allow" }}\\n  }}\\n}}\\n' > /root/.config/opencode/opencode.json

# Expose OpenCode HTTP API port
EXPOSE 4096

# Set working directory
WORKDIR /opt/agents

# Default command - will be overridden by entrypoint at runtime
CMD ["/bin/bash"]
"""

    # --- Guardrail (coder56 / defender auditor) wiring -----------------------
    # The generated Dockerfile above is piped on stdin with NO build context and
    # uses a flaky `wget ... 2>/dev/null || ...` opencode install that silently
    # leaves no binary; it also cannot share layer cache with the working static
    # build, so topology hosts (ubuntu:24.04 -> ubuntu-24.04-opencode:0.1) ended
    # up with no opencode AND no guardrail. For ubuntu/debian hosts the foundation
    # IS SCL's BASE_IMAGE, which is exactly what the static
    # images/scl-plugin-network-topology-ubuntu-opencode/ Dockerfile builds on
    # (proven opencode install + labuser + the guardrail plugin + guardrail-aware
    # entrypoint). Build THAT Dockerfile directly — cache-friendly and complete.
    static_dir = Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-plugin-network-topology-ubuntu-opencode'
    use_static = (
        foundation_image == app.BASE_IMAGE
        and (static_dir / 'Dockerfile').is_file()
        and (static_dir / 'guardrail' / 'guardrail.ts').is_file()
    )

    if use_static:
        print(f"   Using static opencode image Dockerfile (guardrail-ready): {static_dir}")
        result = subprocess.run(
            ['docker', 'build', '-t', opencode_image, str(static_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        # Non-SCL foundation (kali, etc.) -> generated Dockerfile, contextless stdin build.
        result = subprocess.run(
            ['docker', 'build', '-t', opencode_image, '-'],
            input=dockerfile,
            capture_output=True,
            text=True,
            check=False,
        )

    if result.returncode != 0:
        error_msg = result.stderr or result.stdout or f'Failed to build {opencode_image}'
        print(f"❌ {error_msg}")
        raise RuntimeError(error_msg)

    print(f"✅ Built OpenCode image: {opencode_image}")
    return opencode_image


def ensure_opencode_images(topology, force_rebuild=False):
    """Ensure OpenCode variants exist for all base OS images in the topology.

    This function:
    1. Collects all unique base images from hosts that have agents
    2. Checks if OpenCode variants exist for each
    3. Builds missing variants (or rebuilds if force_rebuild=True)

    Args:
        topology: The topology configuration
        force_rebuild: If True, rebuilds all OpenCode images even if they exist

    Returns a dict mapping base_image -> opencode_image
    """
    opencode_images = {}

    # Collect all base images from hosts with agents
    for network in topology.get('networks', []):
        for host in network.get('hosts', []):
            if app.host_agents(host):
                base_image = host.get('image', 'ubuntu:24.04')
                if base_image not in opencode_images:
                    opencode_image = get_opencode_image_name(base_image)
                    opencode_images[base_image] = opencode_image

    # Build missing OpenCode images (or rebuild if force_rebuild=True)
    for base_image, opencode_image in opencode_images.items():
        if force_rebuild:
            print(f"🔄 Force rebuilding OpenCode image: {opencode_image}")
            build_opencode_image(base_image, opencode_image)
        else:
            result = subprocess.run(
                ['docker', 'image', 'inspect', opencode_image],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                build_opencode_image(base_image, opencode_image)
            else:
                print(f"✅ OpenCode image exists: {opencode_image}")

    return opencode_images


def ensure_base_image():
    images_to_check = [app.BASE_IMAGE]
    if app.OPENCODE_IMAGE != app.BASE_IMAGE:
        images_to_check.append(app.OPENCODE_IMAGE)

    for image in images_to_check:
        result = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, text=True)
        if result.returncode == 0:
            continue

        if image == app.OPENCODE_IMAGE:
            # Build OpenCode image with additional packages
            dockerfile = f"""FROM {app.BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    nodejs npm curl \\
  && npm install -g @opencode-ai/cli \\
  && rm -rf /var/lib/apt/lists/*
# Agents will be mounted from host at runtime, not copied at build time
"""
        else:
            dockerfile = """FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    bash ca-certificates curl iproute2 iputils-ping netcat-openbsd nftables tcpdump openssh-server python3 sqlite3 sudo procps \\
  && mkdir -p /run/sshd \\
  && rm -rf /var/lib/apt/lists/*
"""

        build = subprocess.run(
            ['docker', 'build', '-t', image, '-'],
            input=dockerfile,
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            raise RuntimeError(build.stderr or build.stdout or f'Docker image build failed for {image}')


def ensure_slips_image(topology):
    """Build the SLIPS sensor image (from its static context dir) if monitoring
    is enabled and the image is missing. Unlike the opencode image (built from a
    piped Dockerfile), SLIPS needs a real build context for its patches/scripts.
    """
    slips_cfg = (topology.get('monitoring') or {}).get('slips') or {}
    if not slips_cfg.get('enabled'):
        return
    result = subprocess.run(['docker', 'image', 'inspect', app.SLIPS_IMAGE], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"✅ SLIPS image exists: {app.SLIPS_IMAGE}")
        return
    context = Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-slips-sensor'
    if not context.exists():
        raise RuntimeError(f'SLIPS image build context not found: {context}')
    print(f"🔨 Building SLIPS sensor image: {app.SLIPS_IMAGE} from {context}")
    build = subprocess.run(
        ['docker', 'build', '-t', app.SLIPS_IMAGE, str(context)],
        capture_output=True, text=True, check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or f'Failed to build {app.SLIPS_IMAGE}')
    print(f"✅ Built SLIPS image: {app.SLIPS_IMAGE}")


def ensure_repo_image(topology, force_rebuild=False):
    """Build the repo-host image if any host is a `repo-server`.

    The image bakes in an external Git repo (cloned at build time by the daemon,
    so the topology host needs no runtime egress and the third-party repo is
    never vendored into this plugin). Mirrors ensure_slips_image's build-from-
    context pattern; the cloned repo URL comes from the REPO_HOST_URL env var.
    """
    has_repo_host = any(
        host.get('type') == 'repo-server'
        for network in topology.get('networks', [])
        for host in network.get('hosts', [])
    )
    if not has_repo_host:
        return

    if not force_rebuild:
        result = subprocess.run(['docker', 'image', 'inspect', app.REPO_HOST_IMAGE], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ Repo-host image exists: {app.REPO_HOST_IMAGE}")
            return

    context = Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-repo-host'
    if not context.exists():
        raise RuntimeError(f'Repo-host image build context not found: {context}')
    print(f"🔨 Building repo-host image: {app.REPO_HOST_IMAGE} from {context} (repo: {app.REPO_HOST_URL})")
    build = subprocess.run(
        ['docker', 'build', '--build-arg', f'REPO_HOST_URL={app.REPO_HOST_URL}', '-t', app.REPO_HOST_IMAGE, str(context)],
        capture_output=True, text=True, check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or f'Failed to build {app.REPO_HOST_IMAGE}')
    print(f"✅ Built repo-host image: {app.REPO_HOST_IMAGE}")


def ensure_ad_image(topology, force_rebuild=False):
    """Build the AD-host (Samba AD DC) image if any host is an `ad-server`.

    Mirrors ensure_repo_image's build-from-context pattern. The image bakes Samba 4 +
    a first-boot domain-provisioning supervisor (no external repo clone, no runtime
    egress). Provisioning runs at first boot and is idempotent (gated by a marker file)
    so the DC adapts to the container hostname/IP. See images/scl-ad-host/.
    """
    has_ad_host = any(
        host.get('type') == 'ad-server'
        for network in topology.get('networks', [])
        for host in network.get('hosts', [])
    )
    if not has_ad_host:
        return

    if not force_rebuild:
        result = subprocess.run(['docker', 'image', 'inspect', app.AD_HOST_IMAGE], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ AD-host image exists: {app.AD_HOST_IMAGE}")
            return

    context = Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-ad-host'
    if not context.exists():
        raise RuntimeError(f'AD-host image build context not found: {context}')
    print(f"🔨 Building AD-host image: {app.AD_HOST_IMAGE} from {context}")
    build = subprocess.run(
        ['docker', 'build', '-t', app.AD_HOST_IMAGE, str(context)],
        capture_output=True, text=True, check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or f'Failed to build {app.AD_HOST_IMAGE}')
    print(f"✅ Built AD-host image: {app.AD_HOST_IMAGE}")


def ensure_rdp_image(topology, force_rebuild=False):
    """Build the RDP-host (xrdp + pwsh) image if any host is a `windows-client`.

    Mirrors ensure_ad_image's build-from-context pattern. The image bakes a real RDP
    server + PowerShell and a first-boot supervisor (no runtime egress). Provisioning
    runs at first boot and is idempotent (gated by a marker file). See
    images/scl-rdp-host/.
    """
    has_rdp_host = any(
        host.get('type') == 'windows-client'
        for network in topology.get('networks', [])
        for host in network.get('hosts', [])
    )
    if not has_rdp_host:
        return

    if not force_rebuild:
        result = subprocess.run(['docker', 'image', 'inspect', app.RDP_HOST_IMAGE], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ RDP-host image exists: {app.RDP_HOST_IMAGE}")
            return

    context = Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-rdp-host'
    if not context.exists():
        raise RuntimeError(f'RDP-host image build context not found: {context}')
    print(f"🔨 Building RDP-host image: {app.RDP_HOST_IMAGE} from {context}")
    build = subprocess.run(
        ['docker', 'build', '-t', app.RDP_HOST_IMAGE, str(context)],
        capture_output=True, text=True, check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or f'Failed to build {app.RDP_HOST_IMAGE}')
    print(f"✅ Built RDP-host image: {app.RDP_HOST_IMAGE}")


def ensure_web_image(topology, force_rebuild=False):
    """Build the web-host (lighttpd + SSH, Shellshock) image if any host is a
    `vuln-web-server`.

    Mirrors ensure_rdp_image's build-from-context pattern. The image bakes lighttpd +
    a Shellshock-vulnerable old bash (4.3 base) and a first-boot supervisor (no runtime
    egress). Provisioning runs at first boot and is idempotent (gated by a marker file).
    See images/scl-web-host/.
    """
    has_web_host = any(
        host.get('type') == 'vuln-web-server'
        for network in topology.get('networks', [])
        for host in network.get('hosts', [])
    )
    if not has_web_host:
        return

    if not force_rebuild:
        result = subprocess.run(['docker', 'image', 'inspect', app.WEB_HOST_IMAGE], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ Web-host image exists: {app.WEB_HOST_IMAGE}")
            return

    context = Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-web-host'
    if not context.exists():
        raise RuntimeError(f'Web-host image build context not found: {context}')
    print(f"🔨 Building Web-host image: {app.WEB_HOST_IMAGE} from {context}")
    build = subprocess.run(
        ['docker', 'build', '-t', app.WEB_HOST_IMAGE, str(context)],
        capture_output=True, text=True, check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or f'Failed to build {app.WEB_HOST_IMAGE}')
    print(f"✅ Built Web-host image: {app.WEB_HOST_IMAGE}")


def ensure_greedy_image(topology, force_rebuild=False):
    """Build the greedy-host image if any host is a `greedy-server`.

    Mirrors ensure_repo_image: the image bakes in the ingSoftII "Greedy Cars"
    repo (sparse-cloned to SCRUM/integrador at build time by the daemon, so the
    topology host needs no runtime egress for the SOURCE — note greedy_cars
    still does an eager Auth0 fetch at startup, so the host net needs internet).
    The cloned repo URL and tested revision come from GREEDY_HOST_URL and
    GREEDY_HOST_REF. Existing tags are reused only when their OCI source labels
    match both values, avoiding the stale-image failure that masked fixes.
    """
    has_greedy_host = any(
        host.get('type') == 'greedy-server'
        for network in topology.get('networks', [])
        for host in network.get('hosts', [])
    )
    if not has_greedy_host:
        return

    if not force_rebuild:
        result = subprocess.run(
            [
                'docker', 'image', 'inspect',
                '--format',
                '{{index .Config.Labels "org.opencontainers.image.source"}} '
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                app.GREEDY_HOST_IMAGE,
            ],
            capture_output=True,
            text=True,
        )
        expected = f'{app.GREEDY_HOST_URL} {app.GREEDY_HOST_REF}'
        if result.returncode == 0 and result.stdout.strip() == expected:
            print(f"✅ Greedy-host image exists: {app.GREEDY_HOST_IMAGE}")
            return
        if result.returncode == 0:
            print(
                f"♻️ Rebuilding stale Greedy-host image: "
                f"found labels {result.stdout.strip()!r}, expected {expected!r}"
            )

    context = Path(os.environ.get('IMAGES_DIR', '/app/images')) / 'scl-greedy-host'
    if not context.exists():
        raise RuntimeError(f'Greedy-host image build context not found: {context}')
    print(
        f"🔨 Building greedy-host image: {app.GREEDY_HOST_IMAGE} from {context} "
        f"(repo: {app.GREEDY_HOST_URL}, ref: {app.GREEDY_HOST_REF})"
    )
    build = subprocess.run(
        [
            'docker', 'build',
            '--build-arg', f'GREEDY_HOST_URL={app.GREEDY_HOST_URL}',
            '--build-arg', f'GREEDY_HOST_REF={app.GREEDY_HOST_REF}',
            '-t', app.GREEDY_HOST_IMAGE,
            str(context),
        ],
        capture_output=True, text=True, check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or f'Failed to build {app.GREEDY_HOST_IMAGE}')
    print(f"✅ Built greedy-host image: {app.GREEDY_HOST_IMAGE}")
