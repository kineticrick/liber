import os
import stat

import pytest

from helpers import write
from liber.config import user_config_path
from liber.errors import LiberError
from liber.server.settings import (
    Ceilings, Secrets, data_dir, load_ceilings, load_secrets, load_server_settings,
    new_jwt_signing_key, new_storage_key, secrets_path, write_secrets,
)

SERVER_TOML = """
vault = "/tmp/v"

[server]
base_url = "https://liber.example.com/"
port = 9000
allowed_github_logins = ["rick"]

[server.ceilings]
oauth = "public"
"""


def test_data_dir_uses_xdg(isolated_env):
    assert data_dir() == isolated_env / ".local" / "share" / "liber"


def test_secrets_path_is_next_to_config():
    assert secrets_path() == user_config_path().parent / "secrets.toml"


def test_ceiling_defaults_without_config():
    assert load_ceilings() == Ceilings("private", "personal", "personal")


def test_load_server_settings():
    write(user_config_path(), SERVER_TOML)
    s = load_server_settings()
    assert s.base_url == "https://liber.example.com"
    assert (s.host, s.port) == ("127.0.0.1", 9000)
    assert s.allowed_github_logins == ("rick",)
    assert s.ceilings == Ceilings("private", "public", "personal")


def test_missing_server_section_points_to_server_init():
    write(user_config_path(), 'vault = "/tmp/v"\n')
    with pytest.raises(LiberError, match="liber server init"):
        load_server_settings()


@pytest.mark.parametrize("body, match", [
    ('base_url = "liber.example.com"\nallowed_github_logins = ["rick"]', "base_url"),
    ('base_url = "https://x"\nallowed_github_logins = []', "allowed_github_logins"),
    ('base_url = "https://x"\nallowed_github_logins = ["rick"]\nport = 0', "port"),
    ('base_url = "https://x"\nallowed_github_logins = ["rick"]\nport = "80"', "port"),
])
def test_invalid_server_settings(body, match):
    write(user_config_path(), f"[server]\n{body}\n")
    with pytest.raises(LiberError, match=match):
        load_server_settings()


def test_invalid_ceiling():
    write(user_config_path(), '[server.ceilings]\noauth = "secret"\n')
    with pytest.raises(LiberError, match="oauth"):
        load_ceilings()


def test_secrets_roundtrip_and_mode():
    s = Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key())
    path = write_secrets(s)
    assert path == secrets_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_secrets() == s


def test_secrets_missing_points_to_server_init():
    with pytest.raises(LiberError, match="liber server init"):
        load_secrets()


def test_secrets_with_open_permissions_refused():
    path = write_secrets(Secrets("a", "b", "c", "d"))
    os.chmod(path, 0o644)
    with pytest.raises(LiberError, match="chmod 600"):
        load_secrets()


def test_secrets_missing_key():
    path = write_secrets(Secrets("a", "b", "c", "d"))
    path.write_text('github_client_id = "a"\n', encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(LiberError, match="github_client_secret"):
        load_secrets()


def test_generated_keys_are_distinct_and_valid():
    from cryptography.fernet import Fernet

    assert new_jwt_signing_key() != new_jwt_signing_key()
    assert len(new_jwt_signing_key()) >= 43
    Fernet(new_storage_key())
