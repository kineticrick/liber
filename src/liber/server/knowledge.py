"""What an MCP connection may see and do in the vault, at a given sensitivity ceiling."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from liber.check import find_conflicts
from liber.docs import content_paths, is_hidden_part, read_vault_file
from liber.errors import LiberError
from liber.paths import free_name
from liber.vaultconfig import load_vault_config, sensitivity_rank

log = logging.getLogger("liber.server")

_SOURCE_SUFFIXES = {".md", ".txt"}
_FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

MAX_PROPOSAL_CHARS = 20_000
MAX_CONTEXT_CHARS = 2_000
MAX_PENDING_PROPOSALS = 50
MAX_SEARCH_LIMIT = 50
SNIPPET_CHARS = 200
_MAX_SNIPPETS = 3
_LABEL_BAD = re.compile(r"[^a-z0-9.-]+")


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


def proposal_label(raw: str | None) -> str:
    label = _LABEL_BAD.sub("-", (raw or "").lower()).strip("-.")
    label = re.sub(r"\.{2,}", ".", label)[:40].strip("-.")
    return label or "unknown"


def _updated_ordinal(doc: Doc) -> int:
    value = doc.meta.get("updated")
    if isinstance(value, date):
        return value.toordinal()
    try:
        return date.fromisoformat(str(value)).toordinal()
    except ValueError:
        return 0


def _positions(haystack: str, needle: str) -> list[int]:
    found, start = [], haystack.find(needle)
    while start != -1:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return found


def _snippets(text: str, terms: list[str]) -> list[str]:
    flat = " ".join(text.split())
    lowered = flat.lower()
    out: list[str] = []
    covered_until = -1
    for pos in sorted(p for term in terms for p in _positions(lowered, term)):
        if pos < covered_until:
            continue
        start = max(0, pos - SNIPPET_CHARS // 2)
        end = min(len(flat), start + SNIPPET_CHARS)
        out.append(("…" if start > 0 else "") + flat[start:end] + ("…" if end < len(flat) else ""))
        covered_until = end
        if len(out) == _MAX_SNIPPETS:
            break
    return out


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

    def search(self, query: str, folders: list[str] | None = None, limit: int = 10) -> list[dict]:
        terms = query.lower().split()
        if not terms:
            raise LiberError("query must not be empty")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise LiberError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        prefixes = [f.strip().strip("/") + "/" for f in folders if f.strip().strip("/")] if folders else []
        ranked = []
        for rel, doc in self.docs().items():
            if prefixes and not any(rel.startswith(p) for p in prefixes):
                continue
            body, title = doc.body.lower(), doc.title.lower()
            counts = [body.count(t) + title.count(t) for t in terms]
            score = sum(counts)
            if score == 0:
                continue
            missing_any = not all(counts)
            ranked.append((missing_any, -score, -_updated_ordinal(doc), rel, doc, score))
        ranked.sort(key=lambda item: item[:4])
        return [
            {"path": rel, "title": doc.title, "score": score, "snippets": _snippets(doc.body, terms)}
            for _, _, _, rel, doc, score in ranked[:limit]
        ]

    def propose(self, text: str, context: str | None, client: str | None, now: datetime) -> dict:
        body = text.strip()
        if not body:
            raise LiberError("proposal text is empty")
        if len(text) > MAX_PROPOSAL_CHARS:
            raise LiberError(f"proposal text is {len(text)} characters; the limit is {MAX_PROPOSAL_CHARS}")
        if context is not None and len(context) > MAX_CONTEXT_CHARS:
            raise LiberError(f"context is {len(context)} characters; the limit is {MAX_CONTEXT_CHARS}")
        inbox = self.vault / "inbox"
        inbox.mkdir(exist_ok=True)
        pending = len(list(inbox.glob("proposal-*.md")))
        if pending >= MAX_PENDING_PROPOSALS:
            raise LiberError(
                f"{pending} proposals are already waiting for review; ask the owner to run /ingest before proposing more"
            )
        label = proposal_label(client)
        name = free_name(inbox, f"proposal-{now:%Y-%m-%dT%H-%M-%S}-{label}.md")
        context_line = (context or "").strip() or "none given"
        (inbox / name).write_text(
            f"<!-- liber proposal -->\n# Proposed update from {label} — {now:%Y-%m-%d %H:%M}\n\n"
            f"**Context:** {context_line}\n\n{body}\n",
            encoding="utf-8",
        )
        log.info("proposal saved: %s (%d characters)", name, len(body))
        return {
            "file": f"inbox/{name}",
            "message": "Saved for the owner to review with /ingest. Nothing in the knowledge base has changed yet.",
        }

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
