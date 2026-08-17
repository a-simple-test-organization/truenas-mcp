# TrueNAS MCP Server

Read-only MCP server for TrueNAS Scale debugging. Runs directly on TrueNAS and provides:

- **Docker tools** — `docker ps/images/inspect/logs/stats/network/volume/compose/system df` wrapped as MCP tools
- **TrueNAS API** — whitelisted `midclt call` methods for system info, apps, docker, pools

Targets TrueNAS SCALE 25.04.2.6 (Fangtooth) — Debian 13 "Trixie", kernel 6.12,
OpenZFS 2.3, with Docker (apps) + Incus (LXC containers) + QEMU/KVM
(classic virtualization). k3s removed. No destructive operations. Every tool
is read-only.

## Quick Start

```bash
# Install
pip install git+https://github.com/a-simple-test-organization/truenas-mcp.git

# Run (no auth)
MCP_PORT=38888 python -m truenas_mcp.server
# Starts on http://0.0.0.0:38888/mcp

# Health check
curl http://localhost:38888/health
# → {"status": "ok", "auth_enabled": false}
```

## Authentication

Set `MCP_TOKEN` to enable Bearer token auth. Then all requests must include `Authorization: Bearer <token>`.

```bash
# Generate a token
openssl rand -hex 32

# Run with auth
MCP_TOKEN="***" MCP_PORT=38888 python -m truenas_mcp.server
```

Client configuration (e.g. OpenClaw, Claude Code, OpenCode):

```json
{
  "mcpServers": {
    "truenas": {
      "url": "http://192.168.2.48:38888/mcp",
      "headers": {
        "Authorization": "***"
      }
    }
  }
}
```

The `/health` endpoint is always available without authentication.

## systemd Deployment

```bash
# Copy the unit file
cp deploy/truenas-mcp.service /etc/systemd/system/

# Edit Environment=MCP_TOKEN= *** set your token
vim /etc/systemd/system/truenas-mcp.service

# Enable and start
systemctl daemon-reload
systemctl enable --now truenas-mcp.service

# Check
systemctl status truenas-mcp
journalctl -u truenas-mcp -f
```

## MCP Tools

### Docker (10 tools)
| Tool | Description |
|------|-------------|
| `docker_ps` | List containers (`docker ps -a`) |
| `docker_images` | List images |
| `docker_inspect` | Inspect a container or image |
| `docker_logs` | Container logs (last N lines) |
| `docker_stats` | One-shot CPU/memory usage |
| `docker_network_ls` | List networks |
| `docker_volume_ls` | List volumes |
| `docker_compose_ls` | List compose projects |
| `docker_system_df` | Disk usage |
| `docker_status_summary` | Quick containers + disk + images overview |

### TrueNAS (2 tools)
| Tool | Description |
|------|-------------|
| `midclt_call` | Call whitelisted TrueNAS API method |
| `midclt_call_arg` | Call with arguments |

## Debugging Slow App Startup

```
1. docker_status_summary
2. docker_logs(container="<container>")
3. docker_inspect(target="<container>")
4. midclt_call(method="app.query")
5. midclt_call(method="docker.events")
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCKER_BIN` | auto (PATH + fallbacks) | Path to docker binary |
| `MIDCLT_BIN` | auto (PATH + fallbacks) | Path to midclt binary |
| `MCP_PORT` | `8000` | HTTP listen port |
| `MCP_HOST` | `0.0.0.0` | HTTP listen host |
| `MCP_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `MCP_TOKEN` | (empty = no auth) | Bearer token for authentication |

## License

MIT
