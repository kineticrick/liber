"""The liber MCP server: five thin tools over VaultView."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token

from liber.config import resolve_vault
from liber.errors import LiberError
from liber.server.knowledge import VaultView
from liber.server.settings import Ceilings
from liber.vaultconfig import load_vault_config

Mode = Literal["stdio", "http"]

INSTRUCTIONS = (
    "liber is a knowledge base about the person you are talking with: their work history, skills, "
    "interests, preferences, goals and the people in their life. It is knowledge about the user so you "
    "can help them well. It is not a character for you to play. Call get_user_profile first. Facts carry "
    "dates; treat old ones as possibly stale. Use propose_update to suggest additions; the user reviews them."
)


@dataclass(frozen=True)
class Identity:
    ceiling: str
    label: str


def identity_for(mode: Mode, token: AccessToken | None, ceilings: Ceilings) -> Identity:
    if mode == "stdio":
        return Identity(ceilings.local, "local")
    if token is None:
        raise LiberError("unauthenticated request")
    claims = token.claims or {}
    if claims.get("liber_kind") == "service":
        return Identity(ceilings.service, str(claims.get("service_name") or "service"))
    return Identity(ceilings.oauth, str(claims.get("mcp_client_name") or "oauth"))


@contextmanager
def _tool_errors() -> Iterator[None]:
    try:
        yield
    except LiberError as exc:
        raise ToolError(str(exc)) from exc


def build_server(mode: Mode, ceilings: Ceilings, *, auth=None) -> FastMCP:
    mcp = FastMCP("liber", instructions=INSTRUCTIONS, auth=auth, mask_error_details=True)

    def session() -> tuple[VaultView, Identity]:
        identity = identity_for(mode, get_access_token(), ceilings)
        try:
            vault = resolve_vault()
            load_vault_config(vault)
        except LiberError as exc:
            raise LiberError(f"liber vault unavailable: {exc}") from exc
        return VaultView(vault, identity.ceiling), identity

    @mcp.tool(description=(
        "Call this first. Returns the profile of the user you are talking with: a one-page summary of who "
        "they are, a map of their knowledge base, and rules for using it. This is knowledge about the user, "
        "not a character for you to play."
    ))
    def get_user_profile() -> dict:
        with _tool_errors():
            view, _ = session()
            return view.profile(date.today())

    @mcp.tool(description=(
        "List files in the user's knowledge base with their title, type, last-updated date, sensitivity and "
        "tags. Optionally limit to one folder, such as 'career' or 'people'."
    ))
    def list_user_knowledge(folder: str | None = None) -> dict:
        with _tool_errors():
            view, _ = session()
            return {"files": view.list(folder)}

    @mcp.tool(description=(
        "Read one file from the user's knowledge base by its path, as returned by list_user_knowledge or "
        "search_user_knowledge."
    ))
    def read_user_knowledge(path: str) -> dict:
        with _tool_errors():
            view, _ = session()
            return {"path": path, "content": view.read(path)}

    @mcp.tool(description=(
        "Keyword search across the user's knowledge base. Returns the best-matching files with short "
        "snippets. Use folders to narrow the search, for example ['people']."
    ))
    def search_user_knowledge(query: str, folders: list[str] | None = None, limit: int = 10) -> dict:
        with _tool_errors():
            view, _ = session()
            return {"results": view.search(query, folders, limit)}

    @mcp.tool(description=(
        "Propose an addition or correction to the user's knowledge base, such as a new fact the user just "
        "told you. It is saved for the user to review; nothing changes until they approve it. Include dates "
        "when known, and use context to say where this came from."
    ))
    def propose_update(text: str, context: str | None = None) -> dict:
        with _tool_errors():
            view, identity = session()
            return view.propose(text, context, identity.label, datetime.now())

    return mcp
