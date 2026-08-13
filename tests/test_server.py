import os
import json
import pytest
from unittest import mock

from truenas_mcp.server import (
    _closest_match,
    _find_binary,
    _docker_guard,
    _DOCKER_ALLOWED_ARGS,
    DOCKER_READ_ONLY_SUBCOMMANDS,
    ALLOWED_MIDCLT,
    DOCKER,
    MIDCLT,
    mcp,
)


# ═══════════════════════════════════════════════════════════════════════════
# _find_binary
# ═══════════════════════════════════════════════════════════════════════════


class TestFindBinary:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("DOCKER_BIN", "/my/custom/docker")
        result = _find_binary("docker", "DOCKER_BIN", ["/fallback/docker"])
        assert result == "/my/custom/docker"

    def test_falls_back_to_which(self, monkeypatch):
        monkeypatch.delenv("DOCKER_BIN", raising=False)
        with mock.patch("shutil.which", return_value="/usr/bin/docker"):
            result = _find_binary("docker", "DOCKER_BIN", ["/fake/docker"])
        assert result == "/usr/bin/docker"

    def test_falls_back_to_list_if_which_none(self, monkeypatch):
        monkeypatch.delenv("DOCKER_BIN", raising=False)
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("os.path.isfile", return_value=False):
                with mock.patch("os.access", return_value=False):
                    result = _find_binary("docker", "DOCKER_BIN", ["/a/docker", "/b/docker"])
        assert result == "/a/docker"  # best-effort, first fallback

    def test_finds_existing_file_in_fallbacks(self, monkeypatch):
        monkeypatch.delenv("DOCKER_BIN", raising=False)
        with mock.patch("shutil.which", return_value=None):
            isfile_values = {"/a/docker": False, "/b/docker": True}
            def fake_isfile(p):
                return isfile_values.get(p, False)
            with mock.patch("os.path.isfile", side_effect=fake_isfile):
                with mock.patch("os.access", return_value=True):
                    result = _find_binary("docker", "DOCKER_BIN", ["/a/docker", "/b/docker"])
        assert result == "/b/docker"


# ═══════════════════════════════════════════════════════════════════════════
# _docker_guard (read-only enforcement)
# ═══════════════════════════════════════════════════════════════════════════


class TestDockerGuard:
    def test_allowed_subcommands_pass(self):
        for sub in sorted(_DOCKER_ALLOWED_ARGS):
            _docker_guard(sub)  # no exception

    def test_write_subcommands_blocked(self):
        for sub in ["run", "rm", "rmi", "pull", "exec", "stop", "kill",
                    "build", "push", "start", "restart", "commit", "create",
                    "network create", "volume rm", "system prune"]:
            with pytest.raises(ValueError, match="not read-only"):
                _docker_guard(sub)

    def test_unknown_subcommand_blocked(self):
        with pytest.raises(ValueError, match="not read-only"):
            _docker_guard("unicorn")


# ═══════════════════════════════════════════════════════════════════════════
# _closest_match
# ═══════════════════════════════════════════════════════════════════════════


class TestClosestMatch:
    def test_exact_match(self):
        candidates = frozenset({"app.query", "docker.state", "pool.query"})
        assert _closest_match("pool.query", candidates) == "pool.query"

    def test_prefix_match(self):
        candidates = frozenset({"app.query", "app.image.query"})
        assert _closest_match("app.image.q", candidates) == "app.image.query"

    def test_no_match_returns_none(self):
        candidates = frozenset({"pool.query", "app.query"})
        assert _closest_match("xyz", candidates) is None


# ═══════════════════════════════════════════════════════════════════════════
# Whitelists integrity
# ═══════════════════════════════════════════════════════════════════════════


class TestWhitelists:
    def test_midclt_whitelist_not_empty(self):
        assert len(ALLOWED_MIDCLT) >= 10

    def test_midclt_whitelist_contains_essentials(self):
        must_have = {
            "app.query",
            "app.image.query",
            "docker.state",
            "docker.events",
            "system.info",
            "system.version",
            "system.cpu_info",
            "system.mem_info",
            "pool.query",
            "pool.dataset.query",
            "network.configuration.config",
            "service.query",
            "alert.list",
            "vm.query",
            "catalog.query",
        }
        missing = must_have - ALLOWED_MIDCLT
        assert not missing, f"Missing essential methods: {missing}"

    def test_midclt_whitelist_removed_obsolete(self):
        removed = {
            "chart.release.query",
            "chart.release.get_instance",
            "chart.release.events",
            "chart.release.pod_status",
            "chart.release.pod_logs",
            "kubernetes.config",
            "kubernetes.node_ip",
            "kubernetes.status",
            "kubernetes.events",
            "app.config",
            "app.available_versions",
            "app.get_instance",
        }
        leaked = removed & ALLOWED_MIDCLT
        assert not leaked, f"Obsolete methods still whitelisted: {leaked}"

    def test_docker_readonly_subcommands_are_known(self):
        for sub in DOCKER_READ_ONLY_SUBCOMMANDS:
            assert sub in {"ps", "images", "inspect", "logs", "stats",
                           "network", "volume", "compose", "system"}


# ═══════════════════════════════════════════════════════════════════════════
# MCP Server smoke tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMCPServer:
    @pytest.mark.anyio
    async def test_tools_registered(self):
        tools = await mcp.list_tools()
        assert len(tools) == 12
        tool_names = {t.name for t in tools}
        expected = {
            "docker_ps", "docker_images", "docker_inspect", "docker_logs",
            "docker_stats", "docker_network_ls", "docker_volume_ls",
            "docker_compose_ls", "docker_system_df", "docker_status_summary",
            "midclt_call", "midclt_call_arg",
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
        assert "docker_ps" in text
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
            assert "not.allowed" in error_text

    @pytest.mark.anyio
    async def test_docker_bin_resolved(self):
        assert "docker" in DOCKER

    @pytest.mark.anyio
    async def test_midclt_bin_resolved(self):
        assert "midclt" in MIDCLT


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


# ═══════════════════════════════════════════════════════════════════════════
# Docker tool command assembly (mocked _run)
# ═══════════════════════════════════════════════════════════════════════════


class TestDockerTools:
    @pytest.mark.anyio
    async def test_docker_logs_clamps_tail(self):
        from truenas_mcp import server

        captured = {}

        async def fake_run(args, timeout=30):
            captured["args"] = args
            return "logs output"

        with mock.patch.object(server, "_run", side_effect=fake_run):
            await server.docker_logs(container="plex", tail=10000)
            assert captured["args"] == [DOCKER, "logs", "--tail", "500", "plex"]

    @pytest.mark.anyio
    async def test_docker_logs_tail_lower_bound(self):
        from truenas_mcp import server

        captured = {}

        async def fake_run(args, timeout=30):
            captured["args"] = args
            return "logs output"

        with mock.patch.object(server, "_run", side_effect=fake_run):
            await server.docker_logs(container="plex", tail=0)
            assert captured["args"] == [DOCKER, "logs", "--tail", "1", "plex"]

    @pytest.mark.anyio
    async def test_docker_ps_defaults_to_all(self):
        from truenas_mcp import server

        captured = {}

        async def fake_run(args, timeout=30):
            captured["args"] = args
            return "CONTAINER ID ..."

        with mock.patch.object(server, "_run", side_effect=fake_run):
            await server.docker_ps()
            assert captured["args"] == [DOCKER, "ps", "-a"]

    @pytest.mark.anyio
    async def test_docker_inspect_rejects_empty_target(self):
        from truenas_mcp import server

        with pytest.raises(ValueError, match="non-empty"):
            await server.docker_inspect("   ")

    @pytest.mark.anyio
    async def test_docker_compose_ls_graceful_error(self):
        from truenas_mcp import server

        async def fake_run(args, timeout=30):
            raise RuntimeError("docker: 'compose' is not a docker command.")

        with mock.patch.object(server, "_run", side_effect=fake_run):
            result = await server.docker_compose_ls()
            assert "not available" in result

    @pytest.mark.anyio
    async def test_docker_status_summary_assembles_commands(self):
        from truenas_mcp import server

        calls = []

        async def fake_run(args, timeout=30):
            calls.append(args)
            return "out"

        with mock.patch.object(server, "_run", side_effect=fake_run):
            result = await server.docker_status_summary()
        assert len(calls) == 3
        assert calls[0] == [DOCKER, "ps", "-a"]
        assert calls[1] == [DOCKER, "system", "df"]
        assert calls[2] == [DOCKER, "images"]
        assert "=== CONTAINERS" in result
        assert "=== DISK USAGE" in result
        assert "=== IMAGES" in result
