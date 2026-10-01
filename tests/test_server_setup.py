import json
import stat

import pytest
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.config import user_config_path, write_user_config
from liber.errors import LiberError
from liber.server import setup as server_setup
from liber.server.settings import Ceilings, load_secrets, load_server_settings, secrets_path
from liber.server.setup import cloudflared_dir, find_tunnel_credentials, init_server, systemd_user_dir


@pytest.fixture
def creds(tmp_path):
    return write(tmp_path / "cf" / "6f1c.json", json.dumps({"TunnelID": "6f1c-uuid", "AccountTag": "x"}))


@pytest.fixture
def with_config(vault):
    write_user_config(vault)
    return vault


def run_init(creds, **overrides):
    args = dict(
        github_login="rick", github_client_id="Ov23id", github_client_secret="sec",
        base_url="https://liber.example.com/", tunnel_credentials=creds,
        liber_cmd="/home/u/.local/bin/liber", cloudflared_cmd="/usr/bin/cloudflared",
    )
    args.update(overrides)
    return init_server(**args)


def test_init_writes_everything(with_config, creds):
    result = run_init(creds)
    keys = load_secrets()
    assert (keys.github_client_id, keys.github_client_secret) == ("Ov23id", "sec")
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600
    settings = load_server_settings()
    assert settings.base_url == "https://liber.example.com"
    assert settings.allowed_github_logins == ("rick",)
    assert settings.port == 8765 and settings.ceilings == Ceilings()
    assert f'vault = "{with_config.resolve()}"' in user_config_path().read_text()

    mcp_unit = (systemd_user_dir() / "liber-mcp.service").read_text()
    assert "ExecStart=/home/u/.local/bin/liber serve --http" in mcp_unit
    assert "Environment=FASTMCP_CHECK_FOR_UPDATES=off" in mcp_unit
    tunnel_unit = (systemd_user_dir() / "cloudflared-liber.service").read_text()
    config_path = cloudflared_dir() / "liber.yml"
    assert f"ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --config {config_path} run" in tunnel_unit
    tunnel_config = config_path.read_text()
    for expected in ("tunnel: 6f1c-uuid", f"credentials-file: {creds}", "hostname: liber.example.com",
                     "service: http://127.0.0.1:8765", "service: http_status:404"):
        assert expected in tunnel_config

    assert set(result.written) == {secrets_path(), user_config_path(), systemd_user_dir() / "liber-mcp.service",
                                   systemd_user_dir() / "cloudflared-liber.service", config_path}
    assert "systemctl --user enable --now liber-mcp.service cloudflared-liber.service" in result.next_steps
    assert any("loginctl enable-linger" in step for step in result.next_steps)


def test_refuses_existing_secrets_without_force(with_config, creds):
    run_init(creds)
    first_key = load_secrets().jwt_signing_key
    with pytest.raises(LiberError, match="--force"):
        run_init(creds)
    run_init(creds, force=True, github_client_id="Ov23new")
    assert load_secrets().github_client_id == "Ov23new"
    assert load_secrets().jwt_signing_key != first_key


def test_existing_server_section_and_files_are_kept(with_config, creds):
    with user_config_path().open("a", encoding="utf-8") as handle:
        handle.write('\n[server]\nbase_url = "https://other.example.com"\nallowed_github_logins = ["someone"]\n')
    write(systemd_user_dir() / "liber-mcp.service", "custom")
    result = run_init(creds)
    assert load_server_settings().base_url == "https://other.example.com"
    assert (systemd_user_dir() / "liber-mcp.service").read_text() == "custom"
    assert user_config_path() in result.kept and systemd_user_dir() / "liber-mcp.service" in result.kept


def test_requires_https(with_config, creds):
    with pytest.raises(LiberError, match="https://"):
        run_init(creds, base_url="http://liber.example.com")


def test_requires_existing_config(creds):
    with pytest.raises(LiberError, match="liber init"):
        run_init(creds)


def test_find_tunnel_credentials(isolated_env):
    with pytest.raises(LiberError, match="cloudflared tunnel create liber"):
        find_tunnel_credentials()
    write(isolated_env / ".cloudflared" / "a.json", "{}")
    assert find_tunnel_credentials() == isolated_env / ".cloudflared" / "a.json"
    write(isolated_env / ".cloudflared" / "b.json", "{}")
    with pytest.raises(LiberError, match="--tunnel-credentials"):
        find_tunnel_credentials()


def test_cli_server_init(with_config, creds, monkeypatch):
    monkeypatch.setattr(server_setup, "require_command", lambda name: f"/usr/bin/{name}")
    result = CliRunner().invoke(
        app, ["server", "init", "--tunnel-credentials", str(creds)],
        input="https://liber.example.com\nrick\nOv23id\ntopsecretvalue\n",
    )
    assert result.exit_code == 0, result.output
    assert load_server_settings().allowed_github_logins == ("rick",)
    assert load_secrets().github_client_secret == "topsecretvalue"
    assert "systemctl --user enable --now" in result.output
    assert "topsecretvalue" not in result.output  # hidden prompt: never echoed


def test_relative_tunnel_credentials_are_resolved(with_config, creds, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(creds.parent)
    result = run_init(creds, tunnel_credentials=Path(creds.name))
    config_path = cloudflared_dir() / "liber.yml"
    tunnel_config = config_path.read_text()
    assert f"credentials-file: {creds.resolve()}" in tunnel_config


def test_bad_tunnel_credentials(with_config, creds):
    # Missing file
    with pytest.raises(LiberError, match="not found"):
        run_init(creds, tunnel_credentials=creds.parent / "missing.json")
    assert not secrets_path().exists()

    # JSON without TunnelID
    bad_creds = write(creds.parent / "bad.json", json.dumps({"AccountTag": "x"}))
    with pytest.raises(LiberError, match="TunnelID"):
        run_init(creds, tunnel_credentials=bad_creds)
    assert not secrets_path().exists()


def test_cli_refuses_existing_secrets_before_prompting(with_config, creds, monkeypatch):
    monkeypatch.setattr(server_setup, "require_command", lambda name: f"/usr/bin/{name}")
    # First init succeeds
    result = CliRunner().invoke(
        app, ["server", "init", "--tunnel-credentials", str(creds)],
        input="https://liber.example.com\nrick\nOv23id\ntopsecretvalue\n",
    )
    assert result.exit_code == 0

    # Second init fails before prompting
    result = CliRunner().invoke(
        app, ["server", "init", "--tunnel-credentials", str(creds)],
        input="",
    )
    assert result.exit_code == 1
    assert "--force" in result.output
    assert "Public URL" not in result.output
