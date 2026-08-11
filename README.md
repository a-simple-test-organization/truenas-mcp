# TrueNAS MCP Server

Read-only MCP server for TrueNAS Scale debugging. Runs directly on TrueNAS and provides:

- **k3s tools** — kubectl get/describe/logs/events/top wrapped as MCP tools
- **TrueNAS API** — whitelisted `midclt call` methods for system info, apps, pools

No destructive operations. No secrets. Every tool is read-only.

## Quick Start

```bash
# On the TrueNAS host:
pip install truenas-mcp
truenas-mcp
# Starts on http://0.0.0.0:8000/mcp by default
```

Or via Docker:

```bash
docker run -d --name truenas-mcp \
  -v /usr/local/bin/k3s:/usr/local/bin/k3s:ro \
  -v /usr/local/bin/midclt:/usr/local/bin/midclt:ro \
  -v /etc/rancher/k3s/k3s.yaml:/root/.kube/config:ro \
  -p 8000:8000 \
  ghcr.io/a-simple-test-organization/truenas-mcp:latest
```

Or as a TrueNAS Custom App (see [deploy/](./deploy/)).

## MCP Tools

### k3s
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

### TrueNAS
| Tool | Description |
|------|-------------|
| `midclt_call` | Call any whitelisted TrueNAS API method |
| `midclt_call_arg` | Call with arguments |

## Debugging Slow App Startup

```
1. pod_status_summary
2. kubectl_events(all_namespaces=True)
3. kubectl_describe(resource_type="pod", name="<slow-pod>", namespace="ix-<app>")
4. kubectl_logs(pod_name="<slow-pod>", namespace="ix-<app>")
5. midclt_call(method="chart.release.pod_status")
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `K3S_BIN` | `/usr/local/bin/k3s` | Path to k3s binary |
| `MIDCLT_BIN` | `/usr/local/bin/midclt` | Path to midclt binary |
| `MCP_PORT` | `8000` | HTTP listen port |
| `MCP_HOST` | `0.0.0.0` | HTTP listen host |

## License

MIT
