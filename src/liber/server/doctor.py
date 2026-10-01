"""`liber server doctor`: check everything strict MCP clients (claude.ai, ChatGPT) need."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from liber.config import resolve_vault
from liber.errors import LiberError
from liber.server.settings import ServerSettings, load_secrets, load_server_settings
from liber.vaultconfig import load_vault_config

PRM_PATH = "/.well-known/oauth-protected-resource/mcp"
AS_PATH = "/.well-known/oauth-authorization-server"
UNITS = ("liber-mcp", "cloudflared-liber")
_PROBE = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_PROBE_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    warning: bool = False

    def __str__(self) -> str:
        mark = "✓" if self.ok else ("!" if self.warning else "✗")
        return f"{mark} {self.name} — {self.detail}"


def default_http_client() -> httpx.Client:
    return httpx.Client(timeout=10.0, follow_redirects=False)


def systemctl_active(unit: str) -> bool:
    try:
        return subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", f"{unit}.service"], check=False
        ).returncode == 0
    except OSError:
        return False


def _norm(url: str) -> str:
    return url.rstrip("/") + "/"


def _attempt(checks: list[Check], name: str, fn, describe):
    try:
        value = fn()
    except LiberError as exc:
        checks.append(Check(name, False, str(exc)))
        return None
    checks.append(Check(name, True, describe(value)))
    return value


def _vault():
    vault = resolve_vault()
    load_vault_config(vault)
    return vault


def _get_json(http: httpx.Client, url: str) -> tuple[dict | None, str]:
    try:
        response = http.get(url)
    except httpx.HTTPError as exc:
        return None, f"could not reach {url}: {exc}"
    if response.status_code != 200:
        return None, f"{url} returned {response.status_code}"
    try:
        data = response.json()
    except ValueError:
        return None, f"{url} did not return JSON"
    return (data, "") if isinstance(data, dict) else (None, f"{url} did not return a JSON object")


def _local_server(http: httpx.Client, settings: ServerSettings) -> Check:
    url = f"http://{settings.host}:{settings.port}/mcp"
    try:
        response = http.post(url, json=_PROBE, headers=_PROBE_HEADERS)
    except httpx.HTTPError:
        return Check("local server", False, f"nothing answering at {url}; check: systemctl --user status liber-mcp")
    if response.status_code != 401:
        return Check("local server", False, f"{url} answered {response.status_code}; expected 401 (login required)")
    return Check("local server", True, f"answering at {url}")


def _resource_metadata(http: httpx.Client, base: str) -> Check:
    data, error = _get_json(http, base + PRM_PATH)
    if data is None:
        return Check("protected resource metadata", False, error)
    problems = []
    if data.get("resource") != f"{base}/mcp":
        problems.append(f"resource is {data.get('resource')!r}, expected '{base}/mcp'")
    servers = data.get("authorization_servers")
    if not (isinstance(servers, list) and len(servers) == 1 and _norm(str(servers[0])) == _norm(base)):
        problems.append(f"authorization_servers is {servers!r}, expected exactly ['{base}/']")
    return Check("protected resource metadata", not problems, "; ".join(problems) or "resource and server match")


def _server_metadata(http: httpx.Client, base: str) -> Check:
    data, error = _get_json(http, base + AS_PATH)
    if data is None:
        return Check("authorization server metadata", False, error)
    problems = []
    if "S256" not in (data.get("code_challenge_methods_supported") or []):
        problems.append("PKCE S256 not advertised")
    if "none" not in (data.get("token_endpoint_auth_methods_supported") or []):
        problems.append("'none' missing from token_endpoint_auth_methods_supported")
    if data.get("client_id_metadata_document_supported") is not True:
        problems.append("client_id_metadata_document_supported is not true")
    if not data.get("registration_endpoint"):
        problems.append("no registration_endpoint (DCR)")
    if _norm(str(data.get("issuer", ""))) != _norm(base):
        problems.append(f"issuer is {data.get('issuer')!r}, expected '{base}/'")
    if data.get("authorization_response_iss_parameter_supported") is not True:
        problems.append("authorization_response_iss_parameter_supported is not true")
    return Check("authorization server metadata", not problems, "; ".join(problems) or "PKCE, CIMD, DCR and iss OK")


def _auth_challenge(http: httpx.Client, base: str) -> Check:
    try:
        response = http.post(f"{base}/mcp", json=_PROBE, headers=_PROBE_HEADERS)
    except httpx.HTTPError as exc:
        return Check("auth challenge", False, f"could not reach {base}/mcp: {exc}")
    header = response.headers.get("www-authenticate", "")
    if response.status_code != 401 or "resource_metadata=" not in header:
        return Check("auth challenge", False,
                     f"expected 401 with a resource_metadata= challenge, got {response.status_code} {header!r}")
    return Check("auth challenge", True, "unauthenticated requests get 401 with resource_metadata")


def run_doctor(*, http: httpx.Client, systemctl: Callable[[str], bool]) -> list[Check]:
    checks: list[Check] = []
    settings = _attempt(checks, "server config", load_server_settings, lambda s: f"{s.base_url}, port {s.port}")
    _attempt(checks, "secrets", load_secrets, lambda _: "present, mode 600")
    _attempt(checks, "vault", _vault, str)
    if settings is not None:
        checks.append(_local_server(http, settings))
        checks.append(_resource_metadata(http, settings.base_url))
        checks.append(_server_metadata(http, settings.base_url))
        checks.append(_auth_challenge(http, settings.base_url))
    for unit in UNITS:
        active = systemctl(unit)
        detail = "active" if active else f"not active; start it with: systemctl --user enable --now {unit}.service"
        checks.append(Check(f"service {unit}", active, detail, warning=True))
    return checks
