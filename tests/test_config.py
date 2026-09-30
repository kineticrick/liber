import pytest

from helpers import write
from liber.config import resolve_vault, user_config_path, write_user_config
from liber.errors import VaultNotFoundError


def make_marker_vault(path):
    write(path / "liber.toml", "schema_version = 1\n")
    return path


def test_user_config_path_uses_xdg(isolated_env):
    assert user_config_path() == isolated_env / ".config" / "liber" / "config.toml"


def test_resolve_without_config_points_to_init():
    with pytest.raises(VaultNotFoundError, match="liber init"):
        resolve_vault()


def test_write_then_resolve(tmp_path):
    vault = make_marker_vault(tmp_path / "vault")
    write_user_config(vault)
    assert resolve_vault().resolve() == vault.resolve()


def test_env_overrides_config(tmp_path, monkeypatch):
    configured = make_marker_vault(tmp_path / "a")
    override = make_marker_vault(tmp_path / "b")
    write_user_config(configured)
    monkeypatch.setenv("LIBER_VAULT", str(override))
    assert resolve_vault().resolve() == override.resolve()


def test_configured_dir_without_marker_is_rejected(tmp_path):
    write_user_config(tmp_path)
    with pytest.raises(VaultNotFoundError, match="not a liber vault"):
        resolve_vault()


def test_config_missing_vault_key(isolated_env):
    write(user_config_path(), "other = 1\n")
    with pytest.raises(VaultNotFoundError, match="liber init"):
        resolve_vault()


def test_odd_characters_in_path_roundtrip(tmp_path):
    vault = make_marker_vault(tmp_path / 'we"ird \\ dïr')
    write_user_config(vault)
    assert resolve_vault().resolve() == vault.resolve()
