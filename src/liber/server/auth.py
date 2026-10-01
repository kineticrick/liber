"""Remote access on fastmcp 4.0.10: GitHub OAuth (allow-listed at the callback) plus service tokens.

The allow-list override uses private fastmcp internals (_handle_idp_callback, _code_store,
_token_validator); fastmcp is pinned to exactly 4.0.10 and tests/test_server_auth.py guards it.
"""

import logging
import shutil
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from liber.errors import LiberError
from liber.server.app import build_server
from liber.server.settings import (
    Secrets, ServerSettings, data_dir, load_secrets, new_jwt_signing_key, write_secrets,
)
from liber.server.tokens import TokenStore

log = logging.getLogger("liber.auth")

HTTP_OPTIONS = {"path": "/mcp", "json_response": True, "stateless_http": True}
GITHUB_CACHE_SECONDS = 300
PRIVATE_PAGE = "<!doctype html><title>liber</title><p>This liber server is private.</p>"


def oauth_dir() -> Path:
    return data_dir() / "oauth"


def encrypted_file_storage(directory: Path, fernet_key: str) -> AsyncKeyValue:
    """Persistent, Fernet-encrypted store for OAuth clients and tokens."""
    directory.mkdir(parents=True, exist_ok=True)
    return FernetEncryptionWrapper(
        key_value=FileTreeStore(
            data_directory=directory,
            key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
            collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
        ),
        fernet=Fernet(fernet_key),
        raise_on_decryption_error=False,  # unreadable state is treated as missing; clients re-register
    )


class AllowListGitHubProvider(GitHubProvider):
    """GitHub OAuth proxy that only ever issues tokens to allow-listed GitHub logins."""

    def __init__(self, *, allowed_logins: list[str], **kwargs):
        super().__init__(**kwargs)
        self._allowed_logins = {login.lower() for login in allowed_logins}

    def _login_allowed(self, login: str | None) -> bool:
        return bool(login) and login.lower() in self._allowed_logins

    async def _handle_idp_callback(self, request: Request) -> HTMLResponse | RedirectResponse:
        # super() exchanges GitHub's code, stores a one-time liber authorization code, and returns a
        # redirect carrying it. Inspect that code before the browser sees it; on refusal, delete it.
        response = await super()._handle_idp_callback(request)
        if not isinstance(response, RedirectResponse):
            return response
        code = parse_qs(urlparse(response.headers["location"]).query).get("code", [None])[0]
        if code is None:
            return response  # an ?error=... redirect back to the client
        client_code = await self._code_store.get(key=code)
        login = None
        if client_code is not None:
            upstream = await self._token_validator.verify_token(client_code.idp_tokens["access_token"])
            login = upstream.claims.get("login") if upstream else None
        if not self._login_allowed(login):
            await self._code_store.delete(key=code)
            log.warning("refused GitHub login %r at the OAuth callback", login)
            return HTMLResponse(PRIVATE_PAGE, status_code=403)
        log.info("GitHub login %r accepted", login)
        return response

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        validated = await super().load_access_token(token)
        if validated is None:
            return None
        if not self._login_allowed(validated.claims.get("login")):
            return None  # the allow-list changed after this token was issued
        payload = self.jwt_issuer.verify_token(token)
        client = await self.get_client(payload["client_id"])
        return validated.model_copy(update={
            "client_id": payload["client_id"],
            "claims": {
                **validated.claims,
                "liber_kind": "oauth",
                "mcp_client_name": getattr(client, "client_name", None),
            },
        })


class ServiceTokenVerifier(TokenVerifier):
    """Static service tokens. Re-reads the store on every request so revocation is immediate."""

    def __init__(self, store: TokenStore, *, scopes: list[str]):
        super().__init__()
        self._store = store
        self._scopes = scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            name = self._store.match(token)
        except LiberError as exc:
            log.error("service token store unreadable: %s", exc)
            return None
        if name is None:
            return None
        return AccessToken(
            token=token, client_id=f"service:{name}", scopes=self._scopes,
            claims={"liber_kind": "service", "service_name": name},
        )


def build_http_server(
    settings: ServerSettings,
    secrets: Secrets,
    *,
    token_store: TokenStore | None = None,
    client_storage: AsyncKeyValue | None = None,
    provider_cls: type[AllowListGitHubProvider] = AllowListGitHubProvider,
    http_client=None,
    require_consent: bool = True,
) -> FastMCP:
    provider = provider_cls(
        allowed_logins=list(settings.allowed_github_logins),
        client_id=secrets.github_client_id,
        client_secret=secrets.github_client_secret,
        base_url=settings.base_url,
        jwt_signing_key=secrets.jwt_signing_key.encode("ascii"),  # bytes: used as-is, no PBKDF2
        client_storage=client_storage
        if client_storage is not None
        else encrypted_file_storage(oauth_dir(), secrets.storage_encryption_key),
        cache_ttl_seconds=GITHUB_CACHE_SECONDS,
        require_authorization_consent=require_consent,
        http_client=http_client,
    )
    verifier = ServiceTokenVerifier(token_store or TokenStore.default(), scopes=list(provider.required_scopes))
    return build_server("http", settings.ceilings, auth=MultiAuth(server=provider, verifiers=[verifier]))


def run_http(settings: ServerSettings, secrets: Secrets) -> None:
    mcp = build_http_server(settings, secrets)
    log.info("serving %s/mcp on http://%s:%d", settings.base_url, settings.host, settings.port)
    mcp.run(transport="http", host=settings.host, port=settings.port, show_banner=False, **HTTP_OPTIONS)


def logout_all() -> None:
    """Invalidate every OAuth session: new signing key, and forget stored clients and tokens."""
    current = load_secrets()
    write_secrets(replace(current, jwt_signing_key=new_jwt_signing_key()))
    shutil.rmtree(oauth_dir(), ignore_errors=True)
