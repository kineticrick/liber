"""Capturing notes and documents into the inbox, and reporting what's waiting."""

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from liber.check import find_conflicts
from liber.errors import LiberError
from liber.ledger import Entry, file_key, note_key, record, word_count
from liber.paths import free_name
from liber.scaffold import template_root
from liber.vaultconfig import load_vault_config

log = logging.getLogger(__name__)

NO_TEXT_MARKER = "<!-- liber: no text found -->"
TEXT_SUFFIXES = {".md", ".txt"}
_HEADING = re.compile(r"^#{1,6}\s")


def inbox_template() -> str:
    return (template_root() / "inbox.md").read_text(encoding="utf-8")


def sidecar_for(doc: Path) -> Path:
    return doc.with_name(doc.name + ".md")


def _is_sidecar(path: Path) -> bool:
    return path.name.endswith(".md") and path.with_name(path.name[:-3]).is_file()


def _is_note_line(line: str) -> bool:
    s = line.strip()
    if not s or _HEADING.match(s):
        return False
    return not (s.startswith("<!--") and s.endswith("-->"))


def pending_notes(vault: Path) -> list[str]:
    inbox = vault / "inbox.md"
    if not inbox.is_file():
        return []
    return [line.strip() for line in inbox.read_text(encoding="utf-8").splitlines() if _is_note_line(line)]


def _record(vault: Path, entry: Entry) -> None:
    """The capture is already safe in the inbox, so a ledger failure only warns."""
    try:
        record(vault, entry)
    except OSError as exc:
        log.warning("couldn't write the ledger entry for %s (%s)", entry.label or entry.key, exc)


def add_note(vault: Path, text: str, now: datetime) -> str:
    text = " ".join(text.split())
    if not text:
        raise LiberError("note is empty")
    line = f"- {now:%Y-%m-%d %H:%M} — {text}"
    inbox = vault / "inbox.md"
    existing = inbox.read_text(encoding="utf-8") if inbox.is_file() else inbox_template()
    if existing and not existing.endswith("\n"):
        existing += "\n"
    inbox.write_text(existing + line + "\n", encoding="utf-8")
    n = word_count(text)
    _record(vault, Entry(key=note_key(text), kind="note", date=now.date().isoformat(), tool="liber note",
                         words=n, owner_words=n))
    return line


def add_files(vault: Path, sources: list[Path]) -> list[Path]:
    for src in sources:
        if src.is_dir():
            raise LiberError(f"{src} is a folder; add the files inside it instead")
        if not src.is_file():
            raise LiberError(f"{src} not found")
    dest_dir = vault / "inbox"
    dest_dir.mkdir(exist_ok=True)
    added = []
    for src in sources:
        dest = dest_dir / free_name(dest_dir, src.name)
        shutil.copy2(src, dest)
        added.append(dest)
        text = dest.suffix.lower() in TEXT_SUFFIXES
        _record(vault, Entry(
            key=file_key(dest), kind="journal" if "journal" in dest.name.lower() else "document",
            date=date.today().isoformat(), tool="liber add", path=dest.relative_to(vault).as_posix(),
            label=dest.name, words=word_count(dest.read_text(encoding="utf-8", errors="replace")) if text else None))
    return added


@dataclass(frozen=True)
class InboxDocument:
    name: str
    state: str
    mtime: float


def _state(path: Path) -> str:
    if path.is_dir():
        return "unsupported-folder"
    if path.suffix.lower() in TEXT_SUFFIXES:
        return "ready"
    sidecar = sidecar_for(path)
    if not sidecar.is_file():
        return "pending-extraction"
    return "no-text" if NO_TEXT_MARKER in sidecar.read_text(encoding="utf-8") else "extracted"


def inbox_documents(vault: Path) -> list[InboxDocument]:
    inbox = vault / "inbox"
    if not inbox.is_dir():
        return []
    docs = [
        InboxDocument(p.name, _state(p), p.stat().st_mtime)
        for p in inbox.iterdir()
        if not p.name.startswith(".") and not _is_sidecar(p)
    ]
    return sorted(docs, key=lambda d: (d.mtime, d.name))


@dataclass(frozen=True)
class InboxStatus:
    notes: list[str]
    documents: list[InboxDocument]
    conflicts: list[str]


def inbox_status(vault: Path) -> InboxStatus:
    patterns = load_vault_config(vault).conflict_patterns
    return InboxStatus(pending_notes(vault), inbox_documents(vault), find_conflicts(vault, patterns))
