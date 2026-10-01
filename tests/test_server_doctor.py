import os

import httpx
import pytest
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.config import user_config_path, write_user_config
from liber.server import doctor as doctor_module
from liber.server.doctor import run_doctor
from liber.server.settings import Secrets, new_jwt_signing_key, new_storage_key, secrets_path, write_secrets

BASE = "https://liber.example.com"
GOOD_AS = {
    "issuer": f"{BASE}/",
    "registration_endpoint": f"{BASE}/register",
    "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
    "code_challenge_methods_supported": ["S256"],
    "client_id_metadata_document_supported": True,
    "authorization_response_iss_parameter_supported": True,
}
CHALLENGE = f'Bearer scope="user", resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"'
NAMES = ["server config", "secrets", "vault", "local server", "protected resource metadata",
         "authorization server metadata", "auth challenge", "service liber-mcp", "service cloudflared-liber"]


def handler_with(prm=None, as_meta=None, challenge_status=401, challenge_header=CHALLENGE, local_up=True):
    prm = prm if prm is not None else {"resource": f"{BASE}/mcp", "authorization_servers": [f"{BASE}/"]}
    as_meta = as_meta if as_meta is not None else GOOD_AS

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            if not local_up:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(401, headers={"www-authenticate": CHALLENGE})
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json=prm)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=as_meta)
        if request.url.path == "/mcp" and request.method == "POST":
            headers = {"www-authenticate": challenge_header} if challenge_header else {}
            return httpx.Response(challenge_status, headers=headers)
        return httpx.Response(404)

    return handler


def doctor(handler, active=True):
    return run_doctor(http=httpx.Client(transport=httpx.MockTransport(handler)), systemctl=lambda unit: active)


@pytest.fixture
def configured(vault):
    write_user_config(vault)
    with user_config_path().open("a", encoding="utf-8") as handle:
        handle.write(f'\n[server]\nbase_url = "{BASE}"\nallowed_github_logins = ["rick"]\n')
    write_secrets(Secrets("cid", "sec", new_jwt_signing_key(), new_storage_key()))
    return vault


def by_name(checks):
    return {c.name: c for c in checks}


def test_all_good(configured):
    checks = doctor(handler_with())
    assert [c.name for c in checks] == NAMES
    assert all(c.ok for c in checks), [str(c) for c in checks if not c.ok]


@pytest.mark.parametrize("kwargs, failing, detail", [
    ({"prm": {"resource": f"{BASE}/", "authorization_servers": [f"{BASE}/"]}}, "protected resource metadata", "resource"),
    ({"prm": {"resource": f"{BASE}/mcp", "authorization_servers": [f"{BASE}/", "https://x/"]}},
     "protected resource metadata", "authorization_servers"),
    ({"as_meta": {**GOOD_AS, "code_challenge_methods_supported": ["plain"]}}, "authorization server metadata", "S256"),
    ({"as_meta": {**GOOD_AS, "token_endpoint_auth_methods_supported": ["client_secret_post"]}},
     "authorization server metadata", "none"),
    ({"as_meta": {**GOOD_AS, "client_id_metadata_document_supported": False}},
     "authorization server metadata", "client_id_metadata_document"),
    ({"as_meta": {**GOOD_AS, "issuer": "https://elsewhere.example.com/"}}, "authorization server metadata", "issuer"),
    ({"challenge_header": 'Bearer scope="user"'}, "auth challenge", "resource_metadata"),
    ({"challenge_status": 200, "challenge_header": None}, "auth challenge", "401"),
    ({"local_up": False}, "local server", "liber-mcp"),
])
def test_each_failure_is_reported(configured, kwargs, failing, detail):
    checks = by_name(doctor(handler_with(**kwargs)))
    assert checks[failing].ok is False
    assert detail in checks[failing].detail
    assert all(c.ok for name, c in checks.items() if name != failing)


def test_issuer_trailing_slash_is_normalised(configured):
    checks = by_name(doctor(handler_with(as_meta={**GOOD_AS, "issuer": BASE})))
    assert checks["authorization server metadata"].ok


def test_missing_settings_skips_network_checks(vault):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(500)

    checks = by_name(doctor(handler))
    assert checks["server config"].ok is False and "liber server init" in checks["server config"].detail
    assert "local server" not in checks and calls == []


def test_open_secrets_permissions(configured):
    os.chmod(secrets_path(), 0o644)
    assert "chmod 600" in by_name(doctor(handler_with()))["secrets"].detail


def test_inactive_services_are_warnings(configured):
    checks = by_name(doctor(handler_with(), active=False))
    assert checks["service liber-mcp"].ok is False and checks["service liber-mcp"].warning is True


def test_cli_doctor(configured, monkeypatch):
    monkeypatch.setattr(doctor_module, "default_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler_with())))
    monkeypatch.setattr(doctor_module, "systemctl_active", lambda unit: False)
    ok = CliRunner().invoke(app, ["server", "doctor"])
    assert ok.exit_code == 0, ok.output
    assert "✓ protected resource metadata" in ok.output and "! service liber-mcp" in ok.output
    monkeypatch.setattr(doctor_module, "default_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler_with(local_up=False))))
    bad = CliRunner().invoke(app, ["server", "doctor"])
    assert bad.exit_code == 1 and "✗ local server" in bad.output
