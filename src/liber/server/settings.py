"""Server settings ([server] in config.toml) and secrets (secrets.toml)."""

import base64
import contextlib
import json
import os
import secrets as pysecrets
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from liber.config import user_config_path
from liber.errors import LiberError
from liber.vaultconfig import SENSITIVITY_LEVELS

DEFAULT_PORT = 8765
SECRET_KEYS: tuple[str, ...] = ("github_client_id", "github_client_secret", "jwt_signing_key", "storage_encryption_key")
_INIT_HINT = "Run `liber server init` to set up the HTTP server."


@dataclass(frozen=True)
class Ceilings:
    local: str = "private"
    oauth: str = "personal"
    service: str = "personal"


@dataclass(frozen=True)
class ServerSettings:
    base_url: str
    host: str
    port: int
    allowed_github_logins: tuple[str, ...]
    ceilings: Ceilings


@dataclass(frozen=True)
class Secrets:
    github_client_id: str
    github_client_secret: str
    jwt_signing_key: str
    storage_encryption_key: str


def secrets_path() -> Path:
    return user_config_path().parent / "secrets.toml"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "liber"


def write_private_file(path: Path, text: str) -> None:
    """Atomically write `text` to `path` with mode 0600 (temp file in the same dir, then os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def new_jwt_signing_key() -> str:
    return base64.urlsafe_b64encode(pysecrets.token_bytes(32)).decode("ascii")


def new_storage_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _read_config() -> dict:
    path = user_config_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{path} is not valid TOML: {exc}") from exc


def load_ceilings() -> Ceilings:
    server = _read_config().get("server", {})
    raw = server.get("ceilings", {}) if isinstance(server, dict) else {}
    if not isinstance(raw, dict):
        raise LiberError("[server.ceilings] must be a table")
    values = {}
    for key in ("local", "oauth", "service"):
        if key in raw:
            if raw[key] not in SENSITIVITY_LEVELS:
                raise LiberError(
                    f"[server.ceilings] {key} must be one of {', '.join(SENSITIVITY_LEVELS)}, got {raw[key]!r}"
                )
            values[key] = raw[key]
    return Ceilings(**values)


def load_server_settings() -> ServerSettings:
    server = _read_config().get("server")
    if not isinstance(server, dict):
        raise LiberError(f"no [server] section in {user_config_path()}. {_INIT_HINT}")
    base_url = server.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
        raise LiberError(f"[server] base_url must be a URL like https://liber.example.com. {_INIT_HINT}")
    logins = server.get("allowed_github_logins")
    if not isinstance(logins, list) or not logins or not all(isinstance(x, str) and x for x in logins):
        raise LiberError(f"[server] allowed_github_logins must list at least one GitHub login. {_INIT_HINT}")
    port = server.get("port", DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise LiberError("[server] port must be a number between 1 and 65535")
    host = server.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        raise LiberError("[server] host must be a string such as 127.0.0.1")
    return ServerSettings(base_url.rstrip("/"), host, port, tuple(logins), load_ceilings())


def load_secrets() -> Secrets:
    path = secrets_path()
    if not path.is_file():
        raise LiberError(f"{path} not found. {_INIT_HINT}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise LiberError(f"{path} can be read by other users (mode {mode:o}); fix it with: chmod 600 {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{path} is not valid TOML: {exc}") from exc
    missing = [key for key in SECRET_KEYS if not isinstance(data.get(key), str) or not data[key]]
    if missing:
        raise LiberError(f"{path} is missing {', '.join(missing)}. {_INIT_HINT}")
    return Secrets(**{key: data[key] for key in SECRET_KEYS})


def read_secret_values() -> dict[str, str]:
    """Every string value in secrets.toml, unvalidated. Empty if the file is absent or unreadable."""
    path = secrets_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


def write_secret_values(values: dict[str, str]) -> Path:
    """Replace secrets.toml with `values` (atomic, mode 0600). JSON string escaping is valid TOML."""
    path = secrets_path()
    write_private_file(path, "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items()))
    return path


def write_secrets(secrets: Secrets) -> Path:
    """Write the server secrets, keeping any other keys already in the file (e.g. voice API keys)."""
    values = read_secret_values()
    values.update({key: getattr(secrets, key) for key in SECRET_KEYS})
    return write_secret_values(values)
