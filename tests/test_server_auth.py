import base64
import hashlib
import secrets
import stat
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.config import user_config_path
from liber.server import auth as auth_module
from liber.server.auth import (
    HTTP_OPTIONS, PRIVATE_PAGE, AllowListGitHubProvider, build_http_server, encrypted_file_storage, logout_all,
    oauth_dir,
)
from liber.server.settings import (
    Ceilings, Secrets, ServerSettings, load_secrets, new_jwt_signing_key, new_storage_key, secrets_path, write_secrets,
)
from liber.server.tokens import TokenStore

pytestmark = pytest.mark.anyio
BASE = "https://liber.example.com"
H = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SETTINGS = ServerSettings(BASE, "127.0.0.1", 8765, ("rick",), Ceilings())
REDIRECT = "http://localhost:33418/callback"
GITHUB_USERS = {"gho_rick": {"id": 1, "login": "Rick"}, "gho_eve": {"id": 2, "login": "eve"}}


def github_api(request: httpx2.Request) -> httpx2.Response:
    token = request.headers["authorization"].removeprefix("Bearer ")
    user = GITHUB_USERS.get(token)
    if user is None:
        return httpx2.Response(401, json={"message": "Bad credentials"})
    if request.url.path == "/user":
        return httpx2.Response(200, json=user)
    return httpx2.Response(200, json=[], headers={"x-oauth-scopes": ""})


class FakeUpstream:
    """Stands in for GitHub's token endpoint: code 'rick' -> token 'gho_rick'."""

    async def fetch_token(self, url, **params):
        return {"access_token": f"gho_{params['code']}", "token_type": "bearer"}

    async def aclose(self):
        pass


class MockedGitHubProvider(AllowListGitHubProvider):
    def _create_upstream_oauth_client(self):
        return FakeUpstream()


def fm(type, sens, body):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def rpc(method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return body


def call(name, args=None):
    return rpc("tools/call", {"name": name, "arguments": args or {}})


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "tokens.json")


def make_server(store, jwt_key=None, storage=None):
    keys = Secrets("Ov23test", "x" * 40, jwt_key or new_jwt_signing_key(), new_storage_key())
    return build_http_server(
        SETTINGS, keys, token_store=store, client_storage=storage if storage is not None else MemoryStore(),
        provider_cls=MockedGitHubProvider,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(github_api)),
        require_consent=False,
    )


@asynccontextmanager
async def serve(mcp):
    asgi = mcp.http_app(**HTTP_OPTIONS)
    async with asgi.router.lifespan_context(asgi):  # ASGITransport does not run lifespan itself
        async with httpx.AsyncClient(transport=httpx.ASGITransport(asgi), base_url=BASE) as client:
            yield client


async def oauth_until_callback(http, github_code):
    reg = await http.post("/register", json={
        "redirect_uris": [REDIRECT], "client_name": "Claude Test", "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = await http.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "client-state",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": f"{BASE}/mcp"})
    assert r.status_code == 302, r.text
    txn = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    callback = await http.get("/auth/callback", params={"code": github_code, "state": txn})
    return callback, client_id, verifier


async def oauth_access_token(http):
    callback, client_id, verifier = await oauth_until_callback(http, "rick")
    assert callback.status_code == 302, callback.text
    code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]
    token = await http.post("/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": REDIRECT,
        "code_verifier": verifier, "resource": f"{BASE}/mcp"})
    assert token.status_code == 200, token.text
    return token.json()["access_token"]


async def test_metadata_and_unauthenticated_challenge(store):
    async with serve(make_server(store)) as http:
        prm = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
        meta = (await http.get("/.well-known/oauth-authorization-server")).json()
        unauth = await http.post("/mcp", headers=H, json=rpc("tools/list"))
    assert prm["resource"] == f"{BASE}/mcp" and prm["authorization_servers"] == [f"{BASE}/"]
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert "none" in meta["token_endpoint_auth_methods_supported"]
    assert meta["client_id_metadata_document_supported"] is True
    assert unauth.status_code == 401
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in unauth.headers["www-authenticate"]


async def test_service_token_gets_service_ceiling(configured_vault, store):
    write(configured_vault / "core" / "priv.md", fm("core", "private", "PRIVMARK"))
    token = store.create("voice", date(2026, 10, 1))
    headers = {**H, "authorization": f"Bearer {token}"}
    async with serve(make_server(store)) as http:
        listed = await http.post("/mcp", headers=headers, json=rpc("tools/list"))
        profile = await http.post("/mcp", headers=headers, json=call("get_user_profile"))
        hidden = await http.post("/mcp", headers=headers, json=call("read_user_knowledge", {"path": "core/priv.md"}))
        proposal = await http.post("/mcp", headers=headers, json=call("propose_update", {"text": "fact"}))
    assert listed.status_code == 200 and len(listed.json()["result"]["tools"]) == 5
    assert profile.json()["result"]["structuredContent"]["access_level"] == "personal"
    assert hidden.json()["result"]["isError"] is True
    assert hidden.json()["result"]["content"][0]["text"] == "not found: core/priv.md"
    assert proposal.json()["result"]["structuredContent"]["file"].endswith("-voice.md")


async def test_wrong_service_token_is_401(store):
    async with serve(make_server(store)) as http:
        r = await http.post("/mcp", headers={**H, "authorization": "Bearer nope"}, json=rpc("tools/list"))
    assert r.status_code == 401


async def test_service_token_revoked_while_running(configured_vault, store):
    # Review Focus 5
    token = store.create("voice", date(2026, 10, 1))
    headers = {**H, "authorization": f"Bearer {token}"}
    async with serve(make_server(store)) as http:
        assert (await http.post("/mcp", headers=headers, json=rpc("tools/list"))).status_code == 200
        TokenStore(store.path).revoke("voice")
        assert (await http.post("/mcp", headers=headers, json=rpc("tools/list"))).status_code == 401


async def test_allowed_github_login_full_flow(configured_vault, store):
    async with serve(make_server(store)) as http:
        access = await oauth_access_token(http)
        headers = {**H, "authorization": f"Bearer {access}"}
        profile = await http.post("/mcp", headers=headers, json=call("get_user_profile"))
        proposal = await http.post("/mcp", headers=headers, json=call("propose_update", {"text": "fact"}))
    assert profile.status_code == 200
    assert profile.json()["result"]["structuredContent"]["access_level"] == "personal"
    assert proposal.json()["result"]["structuredContent"]["file"].endswith("-claude-test.md")


async def test_disallowed_github_login_never_gets_a_code(store):
    async with serve(make_server(store)) as http:
        callback, _, _ = await oauth_until_callback(http, "eve")
    assert callback.status_code == 403
    assert "location" not in callback.headers
    assert callback.text == PRIVATE_PAGE


async def test_rotated_signing_key_invalidates_oauth_tokens(store):
    storage = MemoryStore()
    async with serve(make_server(store, jwt_key=new_jwt_signing_key(), storage=storage)) as http:
        access = await oauth_access_token(http)
    async with serve(make_server(store, jwt_key=new_jwt_signing_key(), storage=storage)) as http:
        r = await http.post("/mcp", headers={**H, "authorization": f"Bearer {access}"}, json=rpc("tools/list"))
    assert r.status_code == 401


async def test_allow_list_removal_rejects_existing_oauth_token(configured_vault, store):
    storage, key = MemoryStore(), new_jwt_signing_key()
    async with serve(make_server(store, jwt_key=key, storage=storage)) as http:
        access = await oauth_access_token(http)
        headers = {**H, "authorization": f"Bearer {access}"}
        assert (await http.post("/mcp", headers=headers, json=rpc("tools/list"))).status_code == 200
    keys = Secrets("Ov23test", "x" * 40, key, new_storage_key())
    narrowed = build_http_server(
        ServerSettings(BASE, "127.0.0.1", 8765, ("someone",), Ceilings()), keys, token_store=store,
        client_storage=storage, provider_cls=MockedGitHubProvider,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(github_api)), require_consent=False,
    )
    async with serve(narrowed) as http:
        r = await http.post("/mcp", headers={**H, "authorization": f"Bearer {access}"}, json=rpc("tools/list"))
    assert r.status_code == 401


async def test_encrypted_file_storage_roundtrip(configured_vault, store, tmp_path):
    root = tmp_path / "oauth"
    jwt_key, storage_key = new_jwt_signing_key(), new_storage_key()

    def server():
        keys = Secrets("Ov23test", "x" * 40, jwt_key, storage_key)
        return build_http_server(
            SETTINGS, keys, token_store=store, client_storage=encrypted_file_storage(root, storage_key),
            provider_cls=MockedGitHubProvider,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(github_api)), require_consent=False,
        )

    async with serve(server()) as http:
        access = await oauth_access_token(http)
    files = [p for p in root.rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
        data = path.read_bytes()
        assert b"gho_rick" not in data and b"Claude Test" not in data
    async with serve(server()) as http:
        r = await http.post("/mcp", headers={**H, "authorization": f"Bearer {access}"}, json=rpc("tools/list"))
    assert r.status_code == 200


def test_consent_is_required_by_default(store):
    keys = Secrets("Ov23test", "x" * 40, new_jwt_signing_key(), new_storage_key())
    mcp = build_http_server(SETTINGS, keys, token_store=store, client_storage=MemoryStore())
    assert mcp.auth.server._require_authorization_consent is True


def test_run_http_disables_access_log(monkeypatch):
    seen = {}

    class Stub:
        def run(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(auth_module, "build_http_server", lambda settings, secrets: Stub())
    auth_module.run_http(SETTINGS, Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key()))
    assert seen["uvicorn_config"] == {"access_log": False}
    assert seen["show_banner"] is False
    assert all(seen[k] == v for k, v in HTTP_OPTIONS.items())


def test_logout_all_rotates_key_and_clears_storage():
    original = Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key())
    write_secrets(original)
    write(oauth_dir() / "clients" / "x", "state")
    logout_all()
    updated = load_secrets()
    assert updated.jwt_signing_key != original.jwt_signing_key
    assert (updated.github_client_id, updated.storage_encryption_key) == ("cid", original.storage_encryption_key)
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600
    assert not oauth_dir().exists()


def test_cli_serve_http_wires_settings_and_secrets(monkeypatch):
    write(user_config_path(), '[server]\nbase_url = "https://liber.example.com"\nallowed_github_logins = ["rick"]\n')
    write_secrets(Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key()))
    seen = {}
    monkeypatch.setattr(auth_module, "run_http", lambda settings, keys: seen.update(settings=settings, keys=keys))
    result = CliRunner().invoke(app, ["serve", "--http"])
    assert result.exit_code == 0, result.output
    assert seen["settings"].base_url == "https://liber.example.com"
    assert seen["keys"].github_client_id == "cid"


def test_cli_serve_http_without_setup():
    result = CliRunner().invoke(app, ["serve", "--http"])
    assert result.exit_code == 1
    assert "liber server init" in result.output


def test_cli_logout_all():
    write_secrets(Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key()))
    result = CliRunner().invoke(app, ["logout-all"])
    assert result.exit_code == 0, result.output
    assert "systemctl --user restart liber-mcp" in result.output
