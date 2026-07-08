# syntax=docker/dockerfile:1
# SLIPS sensor image for the SCL network-topology plugin.
#
# Ports Trident's slips_defender image with two structural changes:
#   * the in-container defender_api / planner / auto_responder are GONE — the
#     response side now lives in the agent-manager plugin (soc_god is driven over
#     its session/opencode layer, not SSH+pexpect);
#   * the SSH exec path (openssh-client / sshpass / key generation) is GONE.
# This image keeps only the SENSOR side: SLIPS (patched) analyzes pcaps from the
# shared /pcaps volume and forward_alerts POSTs alerts to the agent-manager.

FROM stratosphereips/slips:latest

# pcap tooling + python pip. openssh-client/sshpass intentionally omitted.
RUN apt-get update && apt-get install -y --no-install-recommends \
        netcat-openbsd \
        telnet \
        iputils-ping \
        python3-pip \
        tshark \
    && rm -rf /var/lib/apt/lists/*

# Python deps for the sensor scripts. langchain/langfuse/pexpect dropped (the
# planner no longer runs here). forward_alerts/watch_pcaps only need `requests`.
RUN pip3 install --no-cache-dir requests || true

WORKDIR /opt/lab

# Sensor scripts + SLIPS config/patches (patches applied at runtime by the
# entrypoint to survive SLIPS base version differences).
COPY forward_alerts.py watch_pcaps.py slips.yaml local.zeek __load__.zeek ./
COPY slips_files/ ./slips_files/
COPY patches/ ./patches/
COPY slips_entrypoint.sh /opt/lab/slips_entrypoint.sh

RUN chmod +x /opt/lab/slips_entrypoint.sh /opt/lab/*.py && \
    touch /opt/lab/patches_available.flag

# Shared pcaps volume: the capture_source router writes here, watch_pcaps reads here.
VOLUME ["/pcaps"]

ENTRYPOINT ["/opt/lab/slips_entrypoint.sh"]
