"""
Read-only MCP server for TrueNAS Scale.

Runs locally on TrueNAS; uses `k3s kubectl` and `midclt call` (no auth needed).
Every tool is read-only — no apply/delete/create/exec.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

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

K3S = _find_binary("k3s", "K3S_BIN", ["/usr/local/bin/k3s", "/usr/bin/k3s"])
MIDCLT = _find_binary(
    "midclt", "MIDCLT_BIN",
    ["/usr/local/bin/midclt", "/usr/bin/midclt", "/usr/local/sbin/midclt"]
)

# Safe resource types for kubectl get (read-only, no secrets)
ALLOWED_GET_TYPES = frozenset({
    "pods", "po", "deployments", "deploy", "statefulsets", "sts", "daemonsets", "ds",
    "nodes", "no", "namespaces", "ns", "services", "svc", "ingresses", "ing",
    "persistentvolumes", "pv", "persistentvolumeclaims", "pvc",
    "configmaps", "cm", "events", "ev", "jobs", "cronjobs", "cj",
    "endpoints", "ep", "horizontalpodautoscalers", "hpa",
    "replicasets", "rs", "networkpolicies", "netpol",
    "storageclasses", "sc", "serviceaccounts", "sa",
    "customresourcedefinitions", "crd",
    "certificates", "cert", "issuers", "clusterissuers", "orders", "challenges",
    "helmcharts", "helmreleases",
})

BLOCKED_GET_TYPES = frozenset({"secrets", "secret"})

ALLOWED_DESCRIBE_TYPES = ALLOWED_GET_TYPES

RESOURCE_ALIASES = {
    "po": "pods", "deploy": "deployments", "sts": "statefulsets",
    "ds": "daemonsets", "no": "nodes", "ns": "namespaces",
    "svc": "services", "ing": "ingresses", "pv": "persistentvolumes",
    "pvc": "persistentvolumeclaims", "cm": "configmaps", "ev": "events",
    "cj": "cronjobs", "ep": "endpoints", "hpa": "horizontalpodautoscalers",
    "rs": "replicasets", "netpol": "networkpolicies",
    "sc": "storageclasses", "sa": "serviceaccounts",
    "crd": "customresourcedefinitions",
    "cert": "certificates",
}

# whitelist of midclt calls — verified working on TrueNAS SCALE 23.10.2
ALLOWED_MIDCLT = frozenset({
    # Apps / Charts
    "app.config",
    "app.available_versions",
    "app.get_instance",           # needs args: id
    "chart.release.query",
    "chart.release.get_instance", # needs args: id
    "chart.release.events",       # needs args: release_name
    "chart.release.pod_status",   # needs args: release_name
    "chart.release.pod_logs",     # needs args: release_name, pod_name, tail_lines, ...
    # Kubernetes
    "kubernetes.config",
    "kubernetes.node_ip",
    "kubernetes.status",
    "kubernetes.events",
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
    # VMs
    "vm.query",
    # Catalogs
    "catalog.query",
})


def _resolve_resource(rtype: str) -> str:
    """Resolve shorthand to full resource type."""
    rtype = rtype.lower().strip()
    return RESOURCE_ALIASES.get(rtype, rtype)


def _check_get_type(rtype: str) -> None:
    resolved = _resolve_resource(rtype)
    if resolved in BLOCKED_GET_TYPES:
        raise ValueError(f"Access to '{rtype}' is denied (read-only policy)")
    if resolved not in ALLOWED_GET_TYPES:
        raise ValueError(
            f"Unknown or unsupported resource type '{rtype}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_GET_TYPES))}"
        )


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
OutputFormat = Annotated[str, "Output format: plain, json, yaml"]


# ═══════════════════════════════════════════════════════════════════════════
# k3s / Kubernetes tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def kubectl_get(
    resource_type: str,
    namespace: str | None = None,
    output: str = "json",
    selector: str | None = None,
    all_namespaces: bool = False,
) -> str:
    """Read-only kubectl get for debugging.

    Args:
        resource_type: Kubernetes resource type — pods, deployments, statefulsets,
            daemonsets, nodes, namespaces, services, ingresses, pv, pvc,
            configmaps, events, jobs, cronjobs, endpoints, hpa, replicasets,
            networkpolicies, storageclasses, serviceaccounts, crd.
        namespace: Limit to namespace (default app namespace on TrueNAS is usually 'ix-*').
            Leave empty + all_namespaces=False to use current-context default.
        output: json (default), yaml, or wide.
        selector: Label selector, e.g. 'app=plex'.
        all_namespaces: List across all namespaces.
    """
    _check_get_type(resource_type)
    rtype = _resolve_resource(resource_type)
    args = [K3S, "kubectl", "get", rtype]
    if all_namespaces:
        args.append("--all-namespaces")
    elif namespace:
        args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    if output == "json":
        args.extend(["-o", "json"])
    elif output == "yaml":
        args.extend(["-o", "yaml"])
    elif output == "wide":
        args.extend(["-o", "wide"])
    return await _run(args, timeout=30)


@mcp.tool()
async def kubectl_describe(
    resource_type: str,
    name: str | None = None,
    namespace: str | None = None,
) -> str:
    """Describe a Kubernetes resource — events, status, conditions.

    Args:
        resource_type: e.g. pod, deployment, statefulset, node, pvc, service.
        name: Resource name; omit to describe all of that type in namespace.
        namespace: Namespace. Omit for cluster-scoped or default.
    """
    _check_get_type(resource_type)
    rtype = _resolve_resource(resource_type)
    args = [K3S, "kubectl", "describe", rtype]
    if name:
        args.append(name)
    if namespace:
        args.extend(["-n", namespace])
    return await _run(args, timeout=30)


@mcp.tool()
async def kubectl_logs(
    pod_name: str,
    namespace: str | None = None,
    container: str | None = None,
    tail: int = 200,
    previous: bool = False,
) -> str:
    """Fetch logs from a pod/container.

    Args:
        pod_name: Pod name.
        namespace: Namespace (omit for default).
        container: Container name (if pod has multiple containers).
        tail: Number of lines from the end (default 200, max 500).
        previous: Get logs from previous crashed container.
    """
    tail = min(max(tail, 1), 500)
    args = [K3S, "kubectl", "logs", pod_name, f"--tail={tail}"]
    if namespace:
        args.extend(["-n", namespace])
    if container:
        args.extend(["-c", container])
    if previous:
        args.append("--previous")
    return await _run(args, timeout=30)


@mcp.tool()
async def kubectl_events(
    namespace: str | None = None,
    all_namespaces: bool = False,
) -> str:
    """Get recent Kubernetes events — useful for debugging startup issues.

    Args:
        namespace: Filter by namespace.
        all_namespaces: Show events across all namespaces.
    """
    args = [K3S, "kubectl", "get", "events", "--sort-by=.metadata.creationTimestamp"]
    if all_namespaces:
        args.append("--all-namespaces")
    elif namespace:
        args.extend(["-n", namespace])
    args.extend(["-o", "json"])
    data = await _run(args, timeout=30)
    # Parse JSON, truncate to last 100 events, return pretty
    try:
        events = json.loads(data)
        items = events.get("items", [])
        trimmed = dict(events, items=items[-100:])
        return json.dumps(trimmed, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return data


@mcp.tool()
async def kubectl_api_resources() -> str:
    """List all available API resources on the cluster."""
    return await _run([K3S, "kubectl", "api-resources", "-o", "wide"], timeout=15)


@mcp.tool()
async def kubectl_nodes() -> str:
    """Get node status (conditions, capacity, allocatable). Useful to check if
    nodes are Ready and their resource pressure."""
    return await _run([K3S, "kubectl", "get", "nodes", "-o", "wide"], timeout=15)


@mcp.tool()
async def kubectl_top_pods(
    namespace: str | None = None,
    all_namespaces: bool = False,
) -> str:
    """Top pods by CPU/memory usage. Requires metrics-server.

    Args:
        namespace: Limit to namespace.
        all_namespaces: Across all namespaces.
    """
    args = [K3S, "kubectl", "top", "pods"]
    if all_namespaces:
        args.append("--all-namespaces")
    elif namespace:
        args.extend(["-n", namespace])
    return await _run(args, timeout=15)


@mcp.tool()
async def kubectl_top_nodes() -> str:
    """Top nodes by CPU/memory usage. Requires metrics-server."""
    return await _run([K3S, "kubectl", "top", "nodes"], timeout=15)


# ═══════════════════════════════════════════════════════════════════════════
# TrueNAS / midclt tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def midclt_call(method: str) -> str:
    """Call a read-only TrueNAS API method via midclt.

    Whistlisted methods: apps, charts, kubernetes, system info, pools,
    network, services, alerts, VMs, catalogs. Verified on 23.10.2.
    Example methods:
      - chart.release.query    — list all installed app releases
      - app.config             — app configuration
      - app.get_instance       — single app detail (needs id arg, use midclt_call_arg)
      - chart.release.pod_status — pod status for an app (needs release_name arg)
      - chart.release.events   — app events (needs release_name arg)
      - kubernetes.status      — k8s cluster status
      - system.info            — TrueNAS system info
      - pool.query             — list storage pools
      - alert.list             — current alerts
      - vm.query               — list VMs

    Args:
        method: The midclt method name (e.g. 'chart.release.query', 'system.info').

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


@mcp.tool()
async def pod_status_summary() -> str:
    """Quick overview: all pods with status, restarts, age. Best first tool
    when debugging slow-starting apps."""
    return await _run(
        [K3S, "kubectl", "get", "pods", "--all-namespaces", "-o", "wide"],
        timeout=30,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Discovery resource
# ═══════════════════════════════════════════════════════════════════════════


@mcp.resource("truenas://help")
def help_resource() -> str:
    """Quick reference for available tools."""
    return (
        "# TrueNAS MCP — Read-Only Debugging Tools\n\n"
        "## k3s Tools\n"
        "- `kubectl_get` — get pods, deployments, statefulsets, etc.\n"
        "- `kubectl_describe` — detailed resource status + events\n"
        "- `kubectl_logs` — container logs (last N lines)\n"
        "- `kubectl_events` — cluster events (sorted, last 100)\n"
        "- `kubectl_nodes` — node status\n"
        "- `kubectl_api_resources` — available resource types\n"
        "- `kubectl_top_pods` / `kubectl_top_nodes` — resource usage\n"
        "- `pod_status_summary` — quick all-namespaces pod overview\n\n"
        "## TrueNAS Tools (midclt)\n"
        "- `midclt_call` — call a whitelisted TrueNAS API method\n"
        "- `midclt_call_arg` — call with JSON arguments\n\n"
        "## Common Debugging Flow\n"
        "1. `pod_status_summary` — check pod states\n"
        "2. `kubectl_events(all_namespaces=True)` — recent events\n"
        "3. `kubectl_describe` on slow pods — check conditions\n"
        "4. `kubectl_logs` — container output\n"
        "5. `midclt_call('kubernetes.status')` — cluster-level info\n"
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
    """Entry point: run the MCP server via Streamable HTTP."""
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    path = os.environ.get("MCP_PATH", "/mcp")

    print(f"truenas-mcp v0.1.3 starting on http://{host}:{port}{path}", flush=True)
    print(f"  k3s binary: {K3S}", flush=True)
    print(f"  midclt binary: {MIDCLT}", flush=True)

    mcp.run(transport="streamable-http", host=host, port=port, streamable_http_path=path)


if __name__ == "__main__":
    main()
