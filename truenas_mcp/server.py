"""
Read-only MCP server for TrueNAS SCALE 25.04.2.6 (Fangtooth).

Runs locally on TrueNAS; uses the native `docker` CLI and `midclt call`
(no auth needed). Every tool is read-only — no run/rm/pull/exec, no
mutating midclt methods.

Target environment:
- Base OS: GNU/Linux Debian 13 "Trixie", Linux kernel 6.12, OpenZFS 2.3.x
  (FreeBSD base removed; CORE and SCALE merged into a single Linux line).
- Virtualization/containers: Docker (apps) + Incus (LXC "Containers") +
  QEMU/KVM (classic virtualization, reintroduced in 25.04.2).
- k3s / kubernetes fully removed — no kubectl tools here.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil

from mcp.server import MCPServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_binary(name: str, env_var: str, fallbacks: list[str]) -> str:
    """Resolve binary path: env var > PATH > fallback list."""
    if env_path := os.environ.get(env_var):
        return env_path
    if shutil.which(name):
        return shutil.which(name)  # type: ignore[return-value]
    for p in fallbacks:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return fallbacks[0]  # best-effort, will fail with clear error

DOCKER = _find_binary(
    "docker", "DOCKER_BIN",
    ["/usr/bin/docker", "/usr/local/bin/docker", "/usr/local/sbin/docker"]
)
# midclt lives under /usr/local/bin on TrueNAS (Debian 13 base); include
# /usr/bin and /usr/sbin as reasonable fallbacks for other Debian layouts.
MIDCLT = _find_binary(
    "midclt", "MIDCLT_BIN",
    ["/usr/local/bin/midclt", "/usr/bin/midclt", "/usr/sbin/midclt",
     "/usr/local/sbin/midclt"]
)

# Read-only docker subcommands we are willing to run. Anything else (run, rm,
# rmi, pull, exec, stop, kill, build, push, ...) is rejected up front.
DOCKER_READ_ONLY_SUBCOMMANDS = frozenset({
    "ps", "images", "inspect", "logs", "stats",
    "network", "volume", "compose", "system",
})

# Docker subcommands that take a further read-only action (network ls,
# volume ls, system df, compose ls). We validate the full argv prefix.
_DOCKER_ALLOWED_ARGS = frozenset({
    "ps", "images", "inspect", "logs", "stats",
    "network ls",
    "volume ls",
    "compose ls",
    "system df",
})


def _docker_guard(subcommand: str) -> None:
    """Reject any docker invocation that is not on the read-only allowlist."""
    subcommand = subcommand.strip()
    if subcommand not in _DOCKER_ALLOWED_ARGS:
        raise ValueError(
            f"Docker subcommand '{subcommand}' is not read-only / not allowed. "
            f"Allowed: {', '.join(sorted(_DOCKER_ALLOWED_ARGS))}"
        )


# whitelist of midclt calls — verified working on TrueNAS SCALE 25.04.2.6
ALLOWED_MIDCLT = frozenset({
    # Apps (Docker-backed in 25.04)
    "app.query",                 # list installed app releases (AppEntry)
    "app.image.query",           # docker images
    # Docker
    "docker.state",              # docker daemon / service state
    "docker.events",             # recent docker events
    # Containers (Incus/LXC, introduced in 25.04)
    "container.query",           # list LXC containers
    "container.image.query",     # list container (Incus) images
    "container.state",           # container runtime state
    # System
    "system.info",
    "system.version",
    "system.cpu_info",
    "system.mem_info",
    # Storage
    "pool.query",
    "pool.dataset.query",
    # Network
    "network.configuration.config",
    # Services
    "service.query",
    # Alerts
    "alert.list",
    # VMs (QEMU/KVM, reintroduced in 25.04.2)
    "vm.query",
    # Catalogs
    "catalog.query",
})


async def _run(args: list[str], timeout: int = 30) -> str:
    """Run a subprocess, return stdout. Raises on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(args)}")
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"exit code {proc.returncode}")
    return stdout.decode("utf-8", errors="replace").strip()


mcp = MCPServer("truenas-mcp")


# ═══════════════════════════════════════════════════════════════════════════
# Docker tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def docker_ps(all: bool = True) -> str:
    """List containers (defaults to `docker ps -a`).

    Args:
        all: Show all containers, including stopped ones (default True).
    """
    _docker_guard("ps")
    args = [DOCKER, "ps"]
    if all:
        args.append("-a")
    return await _run(args, timeout=30)


@mcp.tool()
async def docker_images() -> str:
    """List Docker images (`docker images`)."""
    _docker_guard("images")
    return await _run([DOCKER, "images"], timeout=30)


@mcp.tool()
async def docker_inspect(target: str) -> str:
    """Inspect a container or image (`docker inspect <container|image>`).

    Args:
        target: Container or image name/id.
    """
    _docker_guard("inspect")
    target = target.strip()
    if not target:
        raise ValueError("target must be a non-empty container or image name/id")
    return await _run([DOCKER, "inspect", target], timeout=30)


@mcp.tool()
async def docker_logs(container: str, tail: int = 200) -> str:
    """Fetch logs from a container (`docker logs --tail N <container>`).

    Args:
        container: Container name or id.
        tail: Number of lines from the end (default 200, clamped 1..500).
    """
    _docker_guard("logs")
    container = container.strip()
    if not container:
        raise ValueError("container must be a non-empty container name/id")
    tail = min(max(tail, 1), 500)
    return await _run([DOCKER, "logs", "--tail", str(tail), container], timeout=30)


@mcp.tool()
async def docker_stats() -> str:
    """One-shot CPU/memory usage for running containers (`docker stats --no-stream`)."""
    _docker_guard("stats")
    return await _run([DOCKER, "stats", "--no-stream"], timeout=30)


@mcp.tool()
async def docker_network_ls() -> str:
    """List Docker networks (`docker network ls`)."""
    _docker_guard("network ls")
    return await _run([DOCKER, "network", "ls"], timeout=15)


@mcp.tool()
async def docker_volume_ls() -> str:
    """List Docker volumes (`docker volume ls`)."""
    _docker_guard("volume ls")
    return await _run([DOCKER, "volume", "ls"], timeout=15)


@mcp.tool()
async def docker_compose_ls() -> str:
    """List Docker Compose projects (`docker compose ls`).

    Returns a graceful error if the `docker compose` plugin is unavailable.
    """
    _docker_guard("compose ls")
    try:
        return await _run([DOCKER, "compose", "ls"], timeout=30)
    except RuntimeError as e:
        return (
            "docker compose is not available on this host: "
            f"{e}\n\nInstall the docker-compose plugin to enable this tool."
        )


@mcp.tool()
async def docker_system_df() -> str:
    """Show Docker disk usage (`docker system df`)."""
    _docker_guard("system df")
    return await _run([DOCKER, "system", "df"], timeout=15)


@mcp.tool()
async def docker_status_summary() -> str:
    """Quick overview: containers (ps -a) + disk usage (system df) + images.
    Best first tool when debugging slow-starting apps."""
    _docker_guard("ps")
    parts = []
    parts.append("=== CONTAINERS (docker ps -a) ===")
    parts.append(await _run([DOCKER, "ps", "-a"], timeout=30))
    parts.append("\n=== DISK USAGE (docker system df) ===")
    parts.append(await _run([DOCKER, "system", "df"], timeout=15))
    parts.append("\n=== IMAGES (docker images) ===")
    parts.append(await _run([DOCKER, "images"], timeout=30))
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# TrueNAS / midclt tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def midclt_call(method: str) -> str:
    """Call a read-only TrueNAS API method via midclt.

    Whitelisted methods: apps, docker, containers (Incus/LXC), system info,
    pools, network, services, alerts, VMs (QEMU/KVM), catalogs.
    Verified on TrueNAS SCALE 25.04.2.6.
    Example methods:
      - app.query              — list installed app releases (AppEntry)
      - app.image.query        — docker images
      - docker.state           — docker daemon/service state
      - docker.events          — recent docker events
      - container.query        — list LXC containers
      - container.image.query  — list container (Incus) images
      - container.state        — container runtime state
      - system.info            — TrueNAS system info
      - pool.query             — list storage pools
      - alert.list             — current alerts
      - vm.query               — list VMs (QEMU/KVM)

    Args:
        method: The midclt method name (e.g. 'app.query', 'system.info').

    Returns:
        JSON string from midclt.
    """
    method = method.strip()
    if method not in ALLOWED_MIDCLT:
        close = _closest_match(method, ALLOWED_MIDCLT)
        hint = f" Did you mean '{close}'?" if close else ""
        raise ValueError(
            f"Method '{method}' is not in the read-only whitelist.{hint}\n"
            f"Allowed methods: {', '.join(sorted(ALLOWED_MIDCLT))}"
        )
    raw = await _run([MIDCLT, "call", method], timeout=30)
    # midclt returns Python repr for some types; try to make it JSON-ish
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return raw


@mcp.tool()
async def midclt_call_arg(method: str, args_json: str) -> str:
    """Call a read-only TrueNAS API method with arguments.

    The same whitelist as midclt_call applies. Pass arguments as a JSON array.

    Args:
        method: The midclt method name.
        args_json: JSON array of arguments, e.g. '["plex"]' or '[{"name": "plex"}]'.
    """
    method = method.strip()
    if method not in ALLOWED_MIDCLT:
        close = _closest_match(method, ALLOWED_MIDCLT)
        hint = f" Did you mean '{close}'?" if close else ""
        raise ValueError(
            f"Method '{method}' is not in the read-only whitelist.{hint}"
        )
    try:
        parsed = json.loads(args_json)
        if not isinstance(parsed, list):
            raise ValueError("args_json must be a JSON array, e.g. '[\"plex\"]'")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in args_json: {e}")

    cmd = [MIDCLT, "call", method] + [json.dumps(a, ensure_ascii=False) for a in parsed]
    raw = await _run(cmd, timeout=30)
    try:
        parsed_out = json.loads(raw)
        return json.dumps(parsed_out, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return raw


# ═══════════════════════════════════════════════════════════════════════════
# Discovery resource
# ═══════════════════════════════════════════════════════════════════════════


@mcp.resource("truenas://help")
def help_resource() -> str:
    """Quick reference for available tools."""
    return (
        "# TrueNAS MCP — Read-Only Debugging Tools\n\n"
        "## Docker Tools\n"
        "- `docker_ps` — list containers (`docker ps -a`)\n"
        "- `docker_images` — list images\n"
        "- `docker_inspect` — inspect a container or image\n"
        "- `docker_logs` — container logs (last N lines)\n"
        "- `docker_stats` — one-shot CPU/memory usage\n"
        "- `docker_network_ls` — list networks\n"
        "- `docker_volume_ls` — list volumes\n"
        "- `docker_compose_ls` — list compose projects\n"
        "- `docker_system_df` — disk usage\n"
        "- `docker_status_summary` — quick containers + disk + images overview\n\n"
        "## TrueNAS Tools (midclt)\n"
        "- `midclt_call` — call a whitelisted TrueNAS API method\n"
        "- `midclt_call_arg` — call with JSON arguments\n\n"
        "## Common Debugging Flow\n"
        "1. `docker_status_summary` — check container states\n"
        "2. `docker_logs(container=...)` — container output\n"
        "3. `docker_inspect(target=...)` — container/image details\n"
        "4. `midclt_call('app.query')` — app releases\n"
        "5. `midclt_call('docker.events')` — recent docker events\n"
    )


# ---------------------------------------------------------------------------
# Fuzzy match helper
# ---------------------------------------------------------------------------

def _closest_match(text: str, candidates: frozenset[str]) -> str | None:
    """Return the closest candidate by prefix match."""
    best, best_score = None, 0
    for c in candidates:
        # simple prefix score
        score = sum(1 for a, b in zip(text, c) if a == b)
        if score > best_score:
            best, best_score = c, score
    return best if best_score > 3 else None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: run the MCP server via Streamable HTTP with optional token auth."""
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    path = os.environ.get("MCP_PATH", "/mcp")
    token = os.environ.get("MCP_TOKEN", "").strip()

    if token:
        class TokenAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                # Skip health check
                if request.url.path.rstrip("/") in ("/health", "/healthz"):
                    return await call_next(request)
                auth = request.headers.get("Authorization", "")
                expected = f"Bearer {token}"
                if auth != expected:
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                return await call_next(request)

        print(f"  auth: token enabled ({'*' * 8}{token[-4:] if len(token) > 4 else ''})", flush=True)
    else:
        TokenAuthMiddleware = None  # type: ignore[assignment]
        print(f"  auth: DISABLED (set MCP_TOKEN env var to enable)", flush=True)

    from mcp.server.transport_security import TransportSecuritySettings

    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )

    starlette_app = mcp.streamable_http_app(
        streamable_http_path=path,
        json_response=False,
        stateless_http=False,
        host=host,
        transport_security=transport_security,
    )

    # Add /health endpoint
    async def health(request):
        return JSONResponse({"status": "ok", "auth_enabled": bool(token)})
    starlette_app.add_route("/health", health, methods=["GET"])

    if TokenAuthMiddleware:
        starlette_app.add_middleware(TokenAuthMiddleware)

    print(f"truenas-mcp v0.4.1 starting on http://{host}:{port}{path}", flush=True)
    print(f"  docker binary: {DOCKER}", flush=True)
    print(f"  midclt binary: {MIDCLT}", flush=True)

    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    import anyio
    anyio.run(server.serve)


if __name__ == "__main__":
    main()
