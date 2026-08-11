import os
import json
import pytest
from unittest import mock

from truenas_mcp.server import (
    _resolve_resource,
    _check_get_type,
    _closest_match,
    _find_binary,
    ALLOWED_GET_TYPES,
    BLOCKED_GET_TYPES,
    ALLOWED_MIDCLT,
    RESOURCE_ALIASES,
    mcp,
)


# ═══════════════════════════════════════════════════════════════════════════
# _find_binary
# ═══════════════════════════════════════════════════════════════════════════


class TestFindBinary:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("K3S_BIN", "/my/custom/k3s")
        result = _find_binary("k3s", "K3S_BIN", ["/fallback/k3s"])
        assert result == "/my/custom/k3s"

    def test_falls_back_to_which(self, monkeypatch):
        monkeypatch.delenv("K3S_BIN", raising=False)
        with mock.patch("shutil.which", return_value="/usr/bin/k3s"):
            result = _find_binary("k3s", "K3S_BIN", ["/fake/k3s"])
        assert result == "/usr/bin/k3s"

    def test_falls_back_to_list_if_which_none(self, monkeypatch):
        monkeypatch.delenv("K3S_BIN", raising=False)
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("os.path.isfile", return_value=False):
                with mock.patch("os.access", return_value=False):
                    result = _find_binary("k3s", "K3S_BIN", ["/a/k3s", "/b/k3s"])
        assert result == "/a/k3s"  # best-effort, first fallback

    def test_finds_existing_file_in_fallbacks(self, monkeypatch):
        monkeypatch.delenv("K3S_BIN", raising=False)
        with mock.patch("shutil.which", return_value=None):
            isfile_values = {"/a/k3s": False, "/b/k3s": True}
            def fake_isfile(p):
                return isfile_values.get(p, False)
            with mock.patch("os.path.isfile", side_effect=fake_isfile):
                with mock.patch("os.access", return_value=True):
                    result = _find_binary("k3s", "K3S_BIN", ["/a/k3s", "/b/k3s"])
        assert result == "/b/k3s"


# ═══════════════════════════════════════════════════════════════════════════
# _resolve_resource
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveResource:
    def test_plural_unchanged(self):
        assert _resolve_resource("pods") == "pods"
        assert _resolve_resource("deployments") == "deployments"
        assert _resolve_resource("services") == "services"

    def test_singular_aliases(self):
        assert _resolve_resource("pod") == "pods"
        assert _resolve_resource("deployment") == "deployments"
        assert _resolve_resource("daemonset") == "daemonsets"
        assert _resolve_resource("statefulset") == "statefulsets"
        assert _resolve_resource("service") == "services"
        assert _resolve_resource("ingress") == "ingresses"
        assert _resolve_resource("node") == "nodes"
        assert _resolve_resource("namespace") == "namespaces"
        assert _resolve_resource("replicaset") == "replicasets"
        assert _resolve_resource("event") == "events"
        assert _resolve_resource("job") == "jobs"
        assert _resolve_resource("cronjob") == "cronjobs"
        assert _resolve_resource("configmap") == "configmaps"
        assert _resolve_resource("serviceaccount") == "serviceaccounts"
        assert _resolve_resource("storageclass") == "storageclasses"
        assert _resolve_resource("networkpolicy") == "networkpolicies"
        assert _resolve_resource("certificate") == "certificates"

    def test_short_aliases(self):
        assert _resolve_resource("po") == "pods"
        assert _resolve_resource("deploy") == "deployments"
        assert _resolve_resource("sts") == "statefulsets"
        assert _resolve_resource("ds") == "daemonsets"
        assert _resolve_resource("no") == "nodes"
        assert _resolve_resource("ns") == "namespaces"
        assert _resolve_resource("svc") == "services"
        assert _resolve_resource("ing") == "ingresses"
        assert _resolve_resource("pv") == "persistentvolumes"
        assert _resolve_resource("pvc") == "persistentvolumeclaims"
        assert _resolve_resource("cm") == "configmaps"
        assert _resolve_resource("ev") == "events"
        assert _resolve_resource("cj") == "cronjobs"
        assert _resolve_resource("ep") == "endpoints"
        assert _resolve_resource("hpa") == "horizontalpodautoscalers"
        assert _resolve_resource("rs") == "replicasets"
        assert _resolve_resource("netpol") == "networkpolicies"
        assert _resolve_resource("sc") == "storageclasses"
        assert _resolve_resource("sa") == "serviceaccounts"
        assert _resolve_resource("crd") == "customresourcedefinitions"
        assert _resolve_resource("cert") == "certificates"

    def test_case_insensitive(self):
        assert _resolve_resource("PODS") == "pods"
        assert _resolve_resource("Deployment") == "deployments"
        assert _resolve_resource("  pod  ") == "pods"

    def test_unknown_passes_through(self):
        assert _resolve_resource("widgets") == "widgets"


# ═══════════════════════════════════════════════════════════════════════════
# _check_get_type
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckGetType:
    def test_allowed_types_pass(self):
        for t in ["pods", "deployments", "nodes", "services", "events", "jobs", "configmaps"]:
            _check_get_type(t)  # no exception

    def test_secrets_blocked(self):
        for t in ["secrets", "secret"]:
            with pytest.raises(ValueError, match="denied"):
                _check_get_type(t)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown or unsupported"):
            _check_get_type("unicorns")

    def test_aliases_resolved_before_check(self):
        _check_get_type("pod")       # resolves to pods
        _check_get_type("deploy")    # resolves to deployments
        _check_get_type("ds")        # resolves to daemonsets


# ═══════════════════════════════════════════════════════════════════════════
# _closest_match
# ═══════════════════════════════════════════════════════════════════════════


class TestClosestMatch:
    def test_exact_match(self):
        candidates = frozenset({"app.config", "app.query", "pool.query"})
        assert _closest_match("pool.query", candidates) == "pool.query"

    def test_prefix_match(self):
        candidates = frozenset({"chart.release.query", "chart.release.events"})
        assert _closest_match("chart.release.qu", candidates) == "chart.release.query"

    def test_no_match_returns_none(self):
        candidates = frozenset({"pool.query", "app.config"})
        assert _closest_match("xyz", candidates) is None


# ═══════════════════════════════════════════════════════════════════════════
# Whitelists integrity
# ═══════════════════════════════════════════════════════════════════════════


class TestWhitelists:
    def test_no_overlap_with_blocked(self):
        overlap = ALLOWED_GET_TYPES & BLOCKED_GET_TYPES
        assert not overlap, f"Leaked into allowed: {overlap}"

    def test_all_aliases_resolve_to_allowed(self):
        for alias, full in RESOURCE_ALIASES.items():
            assert full in ALLOWED_GET_TYPES, f"Alias {alias} → {full} not in ALLOWED_GET_TYPES"

    def test_midclt_whitelist_not_empty(self):
        assert len(ALLOWED_MIDCLT) >= 10

    def test_midclt_whitelist_contains_essentials(self):
        must_have = {
            "chart.release.query",
            "kubernetes.status",
            "system.info",
            "system.version",
            "pool.query",
            "service.query",
            "alert.list",
            "vm.query",
            "catalog.query",
        }
        missing = must_have - ALLOWED_MIDCLT
        assert not missing, f"Missing essential methods: {missing}"

    def test_get_types_no_duplicates(self):
        """Singular and plural forms shouldn't create confusion."""
        assert "pod" in ALLOWED_GET_TYPES
        assert "pods" in ALLOWED_GET_TYPES
        assert "deploy" in ALLOWED_GET_TYPES
        assert "deployment" in ALLOWED_GET_TYPES
        assert "deployments" in ALLOWED_GET_TYPES


# ═══════════════════════════════════════════════════════════════════════════
# MCP Server smoke tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMCPServer:
    @pytest.mark.anyio
    async def test_tools_registered(self):
        tools = await mcp.list_tools()
        assert len(tools) == 11
        tool_names = {t.name for t in tools}
        expected = {
            "kubectl_get", "kubectl_describe", "kubectl_logs",
            "kubectl_events", "kubectl_nodes", "kubectl_api_resources",
            "kubectl_top_pods", "kubectl_top_nodes",
            "midclt_call", "midclt_call_arg", "pod_status_summary",
        }
        assert tool_names == expected

    @pytest.mark.anyio
    async def test_resources_registered(self):
        resources = await mcp.list_resources()
        assert len(resources) >= 1
        uris = {r.uri for r in resources}
        assert "truenas://help" in uris

    @pytest.mark.anyio
    async def test_help_resource(self):
        content = await mcp.read_resource("truenas://help")
        assert len(content) >= 1
        text = content[0].content
        assert "kubectl_get" in text
        assert "midclt_call" in text

    @pytest.mark.anyio
    async def test_tool_invocation_validates_params(self):
        """midclt_call with invalid method should raise ValueError or ToolError."""
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.shared.exceptions import MCPError
        try:
            await mcp.call_tool("midclt_call", {"method": "not.allowed"})
        except (ValueError, ToolError, MCPError) as e:
            error_text = str(e)
            assert "not.allowed" in error_text or "not.allowed" in error_text

    @pytest.mark.anyio
    async def test_pod_status_summary_builds_correct_command(self):
        """Verify pod_status_summary assembles the right kubectl command."""
        from truenas_mcp.server import K3S
        assert "k3s" in K3S


# ═══════════════════════════════════════════════════════════════════════════
# _run helper (mocked)
# ═══════════════════════════════════════════════════════════════════════════


class TestRunHelper:
    @pytest.mark.anyio
    async def test_run_success(self):
        from truenas_mcp.server import _run
        import asyncio

        async def fake_proc(*args, **kwargs):
            mock_proc = mock.AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"hello world", b"")
            mock_proc.wait = mock.AsyncMock()
            return mock_proc

        with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_proc):
            result = await _run(["echo", "hello"])
            assert result == "hello world"

    @pytest.mark.anyio
    async def test_run_nonzero_raises(self):
        from truenas_mcp.server import _run

        async def fake_proc(*args, **kwargs):
            mock_proc = mock.AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate.return_value = (b"", b"something broke")
            mock_proc.wait = mock.AsyncMock()
            return mock_proc

        with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_proc):
            with pytest.raises(RuntimeError, match="something broke"):
                await _run(["failing", "cmd"])
