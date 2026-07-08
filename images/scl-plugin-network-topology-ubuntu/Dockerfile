FROM python:3.12-alpine

LABEL maintainer="StratoCyberLab Team"
LABEL description="SCL Network Topology Plugin"

# Install Docker CLI for container management
RUN apk add --no-cache docker-cli docker-cli-compose

# Create required directories
RUN mkdir -p /opt/agents /var/log/agent

# Copy agent files
COPY *.py /opt/agents/

# Set working directory
WORKDIR /opt/agents

# Make agent scripts executable
RUN chmod +x /opt/agents/*.py

# Default command
CMD ["/bin/bash"]
