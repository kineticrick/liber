"""Reading Markdown files in a vault."""

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from liber.vaultconfig import EXEMPT_DIRS

_FRONTMATTER_START = re.compile(r"\A﻿?---[ \t]*\r?\n")


@dataclass(frozen=True)
class VaultFile:
    path: Path
    rel: str
    text: str
    meta: dict
    has_frontmatter: bool
    parse_error: str | None


def is_hidden_part(part: str) -> bool:
    return part.startswith(".") or part.startswith("_")


def read_vault_file(vault: Path, path: Path) -> VaultFile:
    text = path.read_text(encoding="utf-8")
    has_fm = bool(_FRONTMATTER_START.match(text))
    meta: dict = {}
    error = None
    if has_fm:
        try:
            meta = dict(frontmatter.loads(text).metadata)
        except Exception as exc:  # YAML errors come in several types
            error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return VaultFile(path, path.relative_to(vault).as_posix(), text, meta, has_fm, error)


def content_paths(vault: Path) -> list[Path]:
    """Markdown files in content folders: not at the root, not exempt, not hidden. Sorted by relative path."""
    paths = []
    for path in vault.rglob("*.md"):
        parts = path.relative_to(vault).parts
        if len(parts) == 1 or parts[0] in EXEMPT_DIRS:
            continue
        if any(is_hidden_part(p) for p in parts[:-1]):
            continue
        paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(vault).as_posix())


def iter_content_files(vault: Path) -> list[VaultFile]:
    """Markdown files in content folders: not at the root, not exempt, not hidden."""
    return [read_vault_file(vault, path) for path in content_paths(vault)]
