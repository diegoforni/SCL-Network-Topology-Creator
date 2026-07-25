FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    docker-compose \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files. The plugin is split across flat modules
# (app.py = config registry + re-export hub; helpers/store/netmath/jobs/llm/
# scripts/images/compose/docker_ops/topology_model/http_handlers/server.py).
COPY *.py ./
COPY shared/ ./shared/
COPY templates/ ./templates/
COPY static/ ./static/

# Create data directory
RUN mkdir -p /app/data/topologies

# Expose control plane port
EXPOSE 9002

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9002/health || exit 1

# Run the application
CMD ["python", "-u", "app.py"]
