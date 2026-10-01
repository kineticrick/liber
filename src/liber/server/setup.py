"""`liber server init`: write secrets, [server] config, systemd user units and the cloudflared config."""

import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from liber.config import user_config_path
from liber.errors import LiberError
from liber.server.settings import (
    DEFAULT_PORT, Secrets, new_jwt_signing_key, new_storage_key, secrets_path, write_secrets,
)

MCP_UNIT = "liber-mcp.service"
TUNNEL_UNIT = "cloudflared-liber.service"


@dataclass(frozen=True)
class InitResult:
    written: list[Path]
    kept: list[Path]
    next_steps: list[str]


def systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "systemd" / "user"


def cloudflared_dir() -> Path:
    return Path.home() / ".cloudflared"


def require_command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise LiberError(f"`{name}` was not found on PATH; install it first (see the README)")
    return path


def find_tunnel_credentials() -> Path:
    found = sorted(cloudflared_dir().glob("*.json"))
    if not found:
        raise LiberError(
            f"no tunnel credentials in {cloudflared_dir()}; run `cloudflared tunnel create liber` first"
        )
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        raise LiberError(f"several tunnel credential files found ({names}); choose one with --tunnel-credentials")
    return found[0]


def _tunnel_id(credentials: Path) -> str:
    try:
        data = json.loads(credentials.read_text(encoding="utf-8"))
        if "TunnelID" not in data:
            raise KeyError("TunnelID")
        return str(data["TunnelID"])
    except (OSError, ValueError, KeyError):
        raise LiberError(f"{credentials} is not a cloudflared tunnel credentials file (no TunnelID); run `cloudflared tunnel create liber`")


def _server_block(base_url: str, port: int, github_login: str) -> str:
    return (
        "\n[server]\n"
        f"base_url = {json.dumps(base_url)}\n"
        'host = "127.0.0.1"\n'
        f"port = {port}\n"
        f"allowed_github_logins = [{json.dumps(github_login)}]\n"
        "\n[server.ceilings]\n"
        'local = "private"\n'
        'oauth = "personal"\n'
        'service = "personal"\n'
    )


def _mcp_unit(liber_cmd: str) -> str:
    return (
        "[Unit]\nDescription=liber MCP server (HTTP)\nAfter=network-online.target\n\n"
        f"[Service]\nExecStart={liber_cmd} serve --http\nRestart=on-failure\nRestartSec=5\n"
        "Environment=FASTMCP_ENABLE_RICH_LOGGING=false\nEnvironment=FASTMCP_CHECK_FOR_UPDATES=off\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def _tunnel_unit(cloudflared_cmd: str, config: Path) -> str:
    return (
        "[Unit]\nDescription=Cloudflare Tunnel for liber\nAfter=network-online.target\nWants=network-online.target\n\n"
        f"[Service]\nExecStart={cloudflared_cmd} tunnel --no-autoupdate --config {config} run\n"
        "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
    )


def _tunnel_config(tunnel_id: str, credentials: Path, hostname: str, port: int) -> str:
    return (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {credentials}\n"
        "ingress:\n"
        f"  - hostname: {hostname}\n"
        f"    service: http://127.0.0.1:{port}\n"
        "  - service: http_status:404\n"
    )


def _write_if_new(path: Path, content: str, force: bool, written: list[Path], kept: list[Path]) -> None:
    if path.exists() and not force:
        kept.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    written.append(path)


def check_secrets_absent(force: bool) -> None:
    """Raise LiberError if secrets exist and force is False."""
    if secrets_path().exists() and not force:
        raise LiberError(
            f"{secrets_path()} already exists; rerun with --force to replace it (this logs out every connected app)"
        )


def init_server(
    *,
    github_login: str,
    github_client_id: str,
    github_client_secret: str,
    base_url: str,
    tunnel_credentials: Path,
    liber_cmd: str,
    cloudflared_cmd: str,
    port: int = DEFAULT_PORT,
    force: bool = False,
) -> InitResult:
    # === VALIDATION PHASE: All checks first, no writes ===
    base_url = base_url.strip().rstrip("/")
    hostname = urlparse(base_url).hostname
    if not base_url.startswith("https://") or not hostname:
        raise LiberError("the public URL must start with https://, e.g. https://liber.example.com")
    if not github_login.strip() or not github_client_id.strip() or not github_client_secret.strip():
        raise LiberError("the GitHub login, client ID and client secret are all required")

    config = user_config_path()
    if not config.is_file():
        raise LiberError(f"{config} not found; run `liber init <path>` to create your vault first")

    # Parse and validate config TOML
    try:
        existing = tomllib.loads(config.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{config} is not valid TOML: {exc}") from exc

    # Resolve and validate tunnel credentials
    tunnel_credentials = tunnel_credentials.expanduser().resolve()
    if not tunnel_credentials.is_file():
        raise LiberError(f"tunnel credentials file {tunnel_credentials} not found")

    # Validate tunnel ID (will raise LiberError if invalid)
    _ = _tunnel_id(tunnel_credentials)

    # Check secrets before any writes
    check_secrets_absent(force)

    # === WRITE PHASE: Config and generated files ===
    written: list[Path] = []
    kept: list[Path] = []

    if "server" in existing:
        kept.append(config)
    else:
        text = config.read_text(encoding="utf-8")
        config.write_text(text + _server_block(base_url, port, github_login.strip()), encoding="utf-8")
        written.append(config)

    tunnel_config = cloudflared_dir() / "liber.yml"
    _write_if_new(systemd_user_dir() / MCP_UNIT, _mcp_unit(liber_cmd), force, written, kept)
    _write_if_new(systemd_user_dir() / TUNNEL_UNIT, _tunnel_unit(cloudflared_cmd, tunnel_config), force, written, kept)
    _write_if_new(
        tunnel_config, _tunnel_config(_tunnel_id(tunnel_credentials), tunnel_credentials, hostname, port),
        force, written, kept,
    )

    # === SECRETS PHASE: Write secrets last (after all other writes succeed) ===
    write_secrets(Secrets(github_client_id.strip(), github_client_secret.strip(), new_jwt_signing_key(), new_storage_key()))
    written.append(secrets_path())

    next_steps = [
        "systemctl --user daemon-reload",
        f"systemctl --user enable --now {MCP_UNIT} {TUNNEL_UNIT}",
        "loginctl enable-linger $USER",
        "liber server doctor",
    ]
    return InitResult(written, kept, next_steps)
