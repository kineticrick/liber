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
    "dates; treat old ones as possibly stale. Whenever you notice something lasting about the user (how they "
    "write, their opinions, preferences, values, goals or facts about their life), propose it with "
    "propose_update without asking first, then mention it in one short line. The user reviews every proposal. "
    "Ask first only for health, money or other people's private matters."
)

CONTRIBUTING = (
    "Help keep this knowledge base current. In any conversation, even one about something else, propose "
    "what you notice about the user with propose_update. Watch for: how they write and speak (edits they "
    "make to your drafts are the best evidence, so give the before and after; words or phrasings they reject "
    "and why; style preferences they state); opinions they express; preferences, likes, dislikes and how they "
    "like to work; values, priorities and goals that show in their decisions; and facts about their life and "
    "work. Keep the bar high: propose patterns, choices they explain, and opinions or preferences they state "
    "clearly or repeat; skip passing remarks and trivia. Include evidence: their own words, before and after "
    "examples, what you were working on, and how far the evidence goes. Propose without asking first, since "
    "the user reviews every proposal before anything changes, then tell them in one short line what you noted. "
    "Ask first before proposing anything about health, money or private matters about other people, and mark "
    "those proposals as sensitive. Never include passwords, account numbers or other secrets."
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
        "they are, a map of their knowledge base, rules for using it, and how to contribute. This is knowledge "
        "about the user, not a character for you to play."
    ))
    def get_user_profile() -> dict:
        with _tool_errors():
            view, _ = session()
            return {**view.profile(date.today()), "contributing": CONTRIBUTING}

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
        "Propose an addition or correction to the user's knowledge base. It is saved for the user to review; "
        "nothing changes until they approve it. Use context to say where this came from, and include dates "
        "when known. " + CONTRIBUTING
    ))
    def propose_update(text: str, context: str | None = None) -> dict:
        with _tool_errors():
            view, identity = session()
            return view.propose(text, context, identity.label, datetime.now())

    return mcp
