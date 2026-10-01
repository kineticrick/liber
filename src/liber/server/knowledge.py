"""What an MCP connection may see and do in the vault, at a given sensitivity ceiling."""

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from liber.check import find_conflicts
from liber.docs import content_paths, is_hidden_part, read_vault_file
from liber.errors import LiberError
from liber.vaultconfig import load_vault_config, sensitivity_rank

log = logging.getLogger("liber.server")

_SOURCE_SUFFIXES = {".md", ".txt"}
_FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class NotFound(LiberError):
    """A file that doesn't exist, or that this connection may not see. Deliberately indistinguishable."""


@dataclass(frozen=True)
class Doc:
    rel: str
    text: str
    body: str
    meta: dict
    sensitivity: str
    title: str


def _body(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1)


def _title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


class VaultView:
    def __init__(self, vault: Path, ceiling: str):
        limit = sensitivity_rank(ceiling)
        if limit is None:
            raise LiberError(f"invalid ceiling {ceiling!r}")
        self.vault = vault
        self.ceiling = ceiling
        self._limit = limit
        self._docs: dict[str, Doc] | None = None

    def _candidates(self) -> list[tuple[Path, str | None]]:
        """(path, forced sensitivity), where None means: use the file's frontmatter."""
        items: list[tuple[Path, str | None]] = [(p, None) for p in content_paths(self.vault)]
        items.append((self.vault / "AGENTS.md", None))
        items.append((self.vault / "open-questions.md", "personal"))
        if self._limit >= sensitivity_rank("private"):
            sources = self.vault / "sources"
            if sources.is_dir():
                for p in sorted(sources.rglob("*")):
                    if p.suffix.lower() in _SOURCE_SUFFIXES:
                        # Exclude files in hidden directories under sources/
                        rel_parts = p.relative_to(self.vault).parts
                        if not any(is_hidden_part(part) for part in rel_parts[:-1]):
                            items.append((p, "private"))
        return items

    def docs(self) -> dict[str, Doc]:
        if self._docs is not None:
            return self._docs
        root = self.vault.resolve()
        conflicts = set(find_conflicts(self.vault, load_vault_config(self.vault).conflict_patterns))
        docs: dict[str, Doc] = {}
        for path, forced in self._candidates():
            rel = path.relative_to(self.vault).as_posix()
            if rel in conflicts:
                continue
            try:
                if not path.is_file():
                    continue
                # A file is visible only if its resolved path equals its original rel (no symlinks)
                resolved_rel = path.resolve().relative_to(root).as_posix()
                if resolved_rel != rel:
                    continue
                vf = read_vault_file(self.vault, path)
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            sensitivity = forced or (None if vf.parse_error else vf.meta.get("sensitivity"))
            rank = sensitivity_rank(sensitivity)
            if rank is None or rank > self._limit:
                continue
            body = _body(vf.text)
            docs[rel] = Doc(rel, vf.text, body, vf.meta, sensitivity, _title(body, path.stem))
        self._docs = docs
        return docs

    def profile(self, today: date) -> dict:
        docs = self.docs()
        agents = docs.get("AGENTS.md")
        return {
            "profile": agents.text if agents else "",
            "today": today.isoformat(),
            "access_level": self.ceiling,
            "folders": sorted({rel.split("/")[0] for rel in docs if "/" in rel}),
        }

    def list(self, folder: str | None = None) -> list[dict]:
        prefix = folder.strip().strip("/") + "/" if folder and folder.strip().strip("/") else None
        return [
            self._summary(doc)
            for rel, doc in sorted(self.docs().items())
            if prefix is None or rel.startswith(prefix)
        ]

    def read(self, path: str) -> str:
        rel = self._normalize(path)
        doc = self.docs().get(rel) if rel else None
        if doc is None:
            raise NotFound(f"not found: {path}")
        return doc.text

    @staticmethod
    def _normalize(path: str) -> str | None:
        candidate = path.strip()
        parts = candidate.split("/")
        if not candidate or "\\" in candidate or candidate.startswith("/") or any(p in ("", ".", "..") for p in parts):
            if ".." in candidate or candidate.startswith("/"):
                log.warning("refused path outside the vault: %r", path)
            return None
        return candidate

    @staticmethod
    def _summary(doc: Doc) -> dict:
        updated = doc.meta.get("updated")
        tags = doc.meta.get("tags")
        return {
            "path": doc.rel,
            "title": doc.title,
            "type": doc.meta.get("type"),
            "updated": str(updated) if updated is not None else None,
            "sensitivity": doc.sensitivity,
            "tags": tags if isinstance(tags, list) else [],
        }
