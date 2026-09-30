"""The vault's own config file, liber.toml: structure, limits, sync settings."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from liber.errors import LiberError, VaultNotFoundError

SENSITIVITY_LEVELS: tuple[str, ...] = ("public", "personal", "private")
SYSTEM_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "README.md", "inbox.md", "open-questions.md"})
EXEMPT_DIRS = frozenset({"sources", "inbox"})
DEFAULT_CONFLICT_PATTERNS: tuple[str, ...] = ("*conflicted copy*", "*.sync-conflict-*")


def sensitivity_rank(level: object) -> int | None:
    return SENSITIVITY_LEVELS.index(level) if level in SENSITIVITY_LEVELS else None


@dataclass(frozen=True)
class VaultConfig:
    schema_version: int
    agents_md_max_tokens: int
    conflict_patterns: tuple[str, ...]
    folders: dict[str, str]

    def type_for(self, rel_dir: str) -> str | None:
        """The `type` required for files in `rel_dir` (longest configured ancestor wins)."""
        best = None
        for key in self.folders:
            if rel_dir == key or rel_dir.startswith(key + "/"):
                if best is None or len(key) > len(best):
                    best = key
        return self.folders[best] if best is not None else None

    @property
    def top_level_folders(self) -> list[str]:
        return sorted({key.split("/")[0] for key in self.folders})


def load_vault_config(vault: Path) -> VaultConfig:
    path = vault / "liber.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VaultNotFoundError(f"{vault} has no liber.toml. Run `liber init <path>` to create a vault.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{path} is not valid TOML: {exc}") from exc

    folders: dict[str, str] = {}
    for key, value in data.get("folders", {}).items():
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise LiberError(f"liber.toml: folder '{key}' needs a `type`, e.g. {key} = {{ type = \"{key}\" }}")
        folders[key.strip("/")] = value["type"]

    return VaultConfig(
        schema_version=int(data.get("schema_version", 1)),
        agents_md_max_tokens=int(data.get("limits", {}).get("agents_md_max_tokens", 2000)),
        conflict_patterns=tuple(data.get("sync", {}).get("conflict_patterns", DEFAULT_CONFLICT_PATTERNS)),
        folders=folders,
    )
