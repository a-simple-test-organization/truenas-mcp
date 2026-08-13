# Changelog

## [0.3.0] — 2026-08-13

### Changed
- Target TrueNAS SCALE 25.04+ (native Docker, k3s removed)
- Replaced all kubectl tools with read-only Docker CLI tools:
  `docker_ps`, `docker_images`, `docker_inspect`, `docker_logs`, `docker_stats`,
  `docker_network_ls`, `docker_volume_ls`, `docker_compose_ls`, `docker_system_df`,
  `docker_status_summary`
- Updated `ALLOWED_MIDCLT` whitelist for 25.04:
  - Added: `app.query`, `app.image.query`, `docker.state`, `docker.events`
  - Removed: all `chart.release.*`, all `kubernetes.*`, `app.config`,
    `app.available_versions`, `app.get_instance`
- Docker binary resolved via `DOCKER_BIN` env + PATH + fallbacks (like `_find_binary`)
- Enforced read-only docker subcommands; write operations (run/rm/rmi/pull/exec, ...) blocked

### Removed
- k3s / kubectl tools and resource-type whitelists

## [0.2.3] — 2026-08-11

### Fixed
- 421 Misdirected Request: disable MCP SDK DNS rebinding protection
  via `transport_security` config (MCP SDK adds strict Host header validation
  that blocks IP-based connections like `192.168.2.48:38888`)

### Added
- Static Bearer token authentication (`MCP_TOKEN` env var)
- `/health` endpoint (no auth required)
- systemd unit file (`deploy/truenas-mcp.service`)
- uvicorn + starlette as direct dependencies (no longer relying on `mcp.run()`)

## [0.1.4] — 2026-08-11

### Fixed
- Singular resource type aliases (pod, daemonset, deployment, etc.)
  added to kubectl_get and kubectl_describe

## [0.1.3] — 2026-08-11

### Fixed
- midclt whitelist: removed methods that don't exist in TrueNAS 23.10.2
  (`app.query`, `kubernetes.get_pods`, `system.cpu_temperatures`, etc.)
- Verified all remaining methods (21 total) work against live TrueNAS

## [0.1.2] — 2026-08-11

### Fixed
- Auto-detect k3s/midclt binary paths via `PATH` + fallback directories
  instead of hardcoded `/usr/local/bin/`

## [0.1.1] — 2026-08-11

### Fixed
- Streamable HTTP transport instead of default stdio (server was blocking)
- Startup logging (version, binary paths)

## [0.1.0] — 2026-08-11

### Added
- Initial release
- 9 k3s tools: kubectl_get, kubectl_describe, kubectl_logs, kubectl_events,
  kubectl_nodes, kubectl_top_pods, kubectl_top_nodes, kubectl_api_resources,
  pod_status_summary
- 2 TrueNAS tools: midclt_call, midclt_call_arg
- 1 resource: truenas://help
- MIT license
