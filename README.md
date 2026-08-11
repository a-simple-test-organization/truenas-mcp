# TrueNAS MCP Server

Read-only MCP server for TrueNAS Scale debugging. Runs directly on TrueNAS and provides:

- **k3s tools** — kubectl get/describe/logs/events/top wrapped as MCP tools
- **TrueNAS API** — whitelisted `midclt call` methods for system info, apps, pools

No destructive operations. Every tool is read-only.

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
MCP_TOKEN="your-token-here" MCP_PORT=38888 python -m truenas_mcp.server
```

Client configuration (e.g. OpenClaw, Claude Code, OpenCode):

```json
{
  "mcpServers": {
    "truenas": {
      "url": "http://192.168.2.48:38888/mcp",
      "headers": {
        "Authorization": "Bearer your-token-here"
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

# Edit Environment=MCP_TOKEN= to set your token
vim /etc/systemd/system/truenas-mcp.service

# Enable and start
systemctl daemon-reload
systemctl enable --now truenas-mcp.service

# Check
systemctl status truenas-mcp
journalctl -u truenas-mcp -f
```

## MCP Tools

### k3s (9 tools)
| Tool | Description |
|------|-------------|
| `kubectl_get` | Get resources (pods, deployments, nodes, ...) |
| `kubectl_describe` | Detailed resource description |
| `kubectl_logs` | Container logs |
| `kubectl_events` | Cluster events |
| `kubectl_nodes` | Node status |
| `kubectl_top_pods` | Resource usage by pod |
| `kubectl_top_nodes` | Resource usage by node |
| `kubectl_api_resources` | Available API types |
| `pod_status_summary` | Quick all-pods overview |

### TrueNAS (2 tools)
| Tool | Description |
|------|-------------|
| `midclt_call` | Call whitelisted TrueNAS API method |
| `midclt_call_arg` | Call with arguments |

## Debugging Slow App Startup

```
1. pod_status_summary
2. kubectl_events(all_namespaces=True)
3. kubectl_describe(resource_type="pod", name="<slow-pod>", namespace="ix-<app>")
4. kubectl_logs(pod_name="<slow-pod>", namespace="ix-<app>")
5. midclt_call(method="chart.release.query")
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `K3S_BIN` | auto (PATH + fallbacks) | Path to k3s binary |
| `MIDCLT_BIN` | auto (PATH + fallbacks) | Path to midclt binary |
| `MCP_PORT` | `8000` | HTTP listen port |
| `MCP_HOST` | `0.0.0.0` | HTTP listen host |
| `MCP_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `MCP_TOKEN` | (empty = no auth) | Bearer token for authentication |

## License

MIT
