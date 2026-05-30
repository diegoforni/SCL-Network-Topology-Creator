# Network Topology Builder Plugin

This repository is the standalone home for the SCL Network Topology Creator plugin.

It provides a local control plane for designing and running generated StratoCyberLab network topologies. Each topology can define routed networks, Ubuntu hosts, host roles, local users, optional generated data, internet access per network, and router firewall rules.
It also lets you enable SSH per host and attach the `hackerlab` container to a chosen network.

## Files

- `metadata.json` describes the plugin for the SCL dashboard.
- `docker-compose.yml` starts the plugin control plane.
- `Dockerfile` builds the control-plane container.
- `app.py` serves the UI and implements topology storage/start/stop.

## Runtime Model

Generated networks use a central router container connected to all topology networks and to `playground-net`. Network segments are configured as Docker bridge networks with deterministic `10.77.<n>.0/24` subnets. Hosts are Ubuntu containers with role labels and startup scripts.

The first version intentionally starts with Ubuntu-only hosts. Service roles prepare directories, users, and role-specific files; lightweight package-backed services are attempted when a segment has internet access.
If SSH is enabled for a host, the generated container creates the specified SSH user and starts `sshd`.

## Installation

Clone this repository into the `plugins` directory of an existing StratoCyberLab checkout:

```bash
cd /path/to/stratocyberlab
git clone https://github.com/<github-owner>/SCL-Network-Topology-Creator.git plugins/network-topology
docker compose up -d --build --force-recreate dashboard
```

Open `http://127.0.0.1/`, expand `Plugins`, select `Network Topology Builder`, and press `Start`.

Replace `<github-owner>` with the GitHub account or organization where this repository is published.

The hackerlab service uses the `scl-hackerlab` image that is built by StratoCyberLab itself, so the main SCL stack should be available when you start a topology that includes it.

## LLM Data Generation

The UI can request AI-generated sample data for selected hosts. The plugin calls the SCL dashboard LLM endpoint through `http://dashboard/api/llm/chat`, so it uses the model configured in the main SCL assistant.
