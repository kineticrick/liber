"""User-level config: where the vault lives."""

import json
import os
import tomllib
from pathlib import Path

from liber.errors import VaultNotFoundError

VAULT_MARKER = "liber.toml"
_INIT_HINT = "Run `liber init <path>` to create a vault."


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "liber" / "config.toml"


def resolve_vault() -> Path:
    """Return the vault path from $LIBER_VAULT or the user config, or raise."""
    env = os.environ.get("LIBER_VAULT")
    if env:
        vault, source = Path(env).expanduser(), "LIBER_VAULT"
    else:
        cfg = user_config_path()
        if not cfg.is_file():
            raise VaultNotFoundError(f"No liber vault configured. {_INIT_HINT}")
        try:
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise VaultNotFoundError(f"{cfg} is not valid TOML ({exc}). {_INIT_HINT}") from exc
        if "vault" not in data:
            raise VaultNotFoundError(f"{cfg} has no `vault` entry. {_INIT_HINT}")
        vault, source = Path(data["vault"]).expanduser(), str(cfg)
    if not (vault / VAULT_MARKER).is_file():
        raise VaultNotFoundError(
            f"{vault} (from {source}) is not a liber vault (no {VAULT_MARKER}). {_INIT_HINT}"
        )
    return vault


def write_user_config(vault: Path) -> Path:
    """Point the user config at `vault`. JSON string escaping is valid TOML."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"vault = {json.dumps(str(vault.resolve()))}\n", encoding="utf-8")
    return path
