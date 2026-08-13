# Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY truenas_mcp/ ./truenas_mcp/

RUN pip install --no-cache-dir .

# We need the docker CLI and midclt from the host — mount them at runtime
ENV DOCKER_BIN=/host/bin/docker
ENV MIDCLT_BIN=/host/bin/midclt
ENV MCP_PORT=8000
ENV MCP_HOST=0.0.0.0

EXPOSE 8000

CMD ["python", "-m", "truenas_mcp.server"]
