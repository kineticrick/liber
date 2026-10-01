import sys

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

from helpers import write
from liber.errors import LiberError
from liber.server.app import Identity, build_server, identity_for
from liber.server.settings import Ceilings

pytestmark = pytest.mark.anyio
CEIL = Ceilings(local="private", oauth="personal", service="public")
TOOLS = {"get_user_profile", "list_user_knowledge", "read_user_knowledge", "search_user_knowledge", "propose_update"}


def fm(type, sens, body):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


async def test_tools_are_registered_with_descriptions(configured_vault):
    async with Client(build_server("stdio", CEIL)) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) == TOOLS
    assert "call this first" in tools["get_user_profile"].description.lower()
    assert "not a character" in tools["get_user_profile"].description.lower()
    for tool in tools.values():
        assert "user" in tool.description.lower()


async def test_stdio_mode_uses_local_ceiling(configured_vault):
    write(configured_vault / "core" / "priv.md", fm("core", "private", "PRIVMARK"))
    async with Client(build_server("stdio", CEIL)) as c:
        profile = (await c.call_tool("get_user_profile", {})).data
        files = (await c.call_tool("list_user_knowledge", {})).data["files"]
        read = (await c.call_tool("read_user_knowledge", {"path": "core/priv.md"})).data
        found = (await c.call_tool("search_user_knowledge", {"query": "privmark"})).data["results"]
    assert profile["access_level"] == "private"
    assert "core/priv.md" in {f["path"] for f in files}
    assert read["path"] == "core/priv.md" and "PRIVMARK" in read["content"]
    assert [r["path"] for r in found] == ["core/priv.md"]


async def test_configured_ceiling_flows_into_tools(configured_vault):
    write(configured_vault / "core" / "priv.md", fm("core", "private", "PRIVMARK"))
    async with Client(build_server("stdio", Ceilings(local="personal"))) as c:
        with pytest.raises(ToolError, match="not found: core/priv.md"):
            await c.call_tool("read_user_knowledge", {"path": "core/priv.md"})
        found = (await c.call_tool("search_user_knowledge", {"query": "privmark"})).data["results"]
    assert found == []


async def test_propose_update_tool(configured_vault):
    async with Client(build_server("stdio", CEIL)) as c:
        out = (await c.call_tool("propose_update", {"text": "New job at Initech.", "context": "chat"})).data
    assert out["file"].startswith("inbox/proposal-") and out["file"].endswith("-local.md")
    assert "Initech" in (configured_vault / out["file"]).read_text(encoding="utf-8")


async def test_validation_errors_become_tool_errors(configured_vault):
    async with Client(build_server("stdio", CEIL)) as c:
        with pytest.raises(ToolError, match="empty"):
            await c.call_tool("search_user_knowledge", {"query": "  "})
        with pytest.raises(ToolError, match="between 1 and 50"):
            await c.call_tool("search_user_knowledge", {"query": "x", "limit": 99})
        with pytest.raises(ToolError, match="empty"):
            await c.call_tool("propose_update", {"text": " "})


async def test_missing_vault_is_a_tool_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LIBER_VAULT", str(tmp_path / "nope"))
    async with Client(build_server("stdio", CEIL)) as c:
        with pytest.raises(ToolError, match="liber vault unavailable"):
            await c.call_tool("get_user_profile", {})


def test_identity_for():
    assert identity_for("stdio", None, CEIL) == Identity("private", "local")
    service = AccessToken(token="t", client_id="service:voice", scopes=["user"],
                          claims={"liber_kind": "service", "service_name": "voice"})
    assert identity_for("http", service, CEIL) == Identity("public", "voice")
    oauth = AccessToken(token="t", client_id="c1", scopes=["user"],
                        claims={"liber_kind": "oauth", "mcp_client_name": "Claude", "login": "rick"})
    assert identity_for("http", oauth, CEIL) == Identity("personal", "Claude")
    unnamed = AccessToken(token="t", client_id="c2", scopes=["user"], claims={"liber_kind": "oauth"})
    assert identity_for("http", unnamed, CEIL) == Identity("personal", "oauth")
    with pytest.raises(LiberError, match="unauthenticated"):
        identity_for("http", None, CEIL)


async def test_serve_command_speaks_mcp_over_stdio(configured_vault):
    script = (
        "import os; "
        f"os.environ['LIBER_VAULT'] = {str(configured_vault)!r}; "
        "from liber.cli import app; app(['serve'])"
    )
    async with Client(StdioTransport(command=sys.executable, args=["-c", script])) as c:
        profile = (await c.call_tool("get_user_profile", {})).data
    assert profile["access_level"] == "private"
    assert "About me" in profile["profile"]
