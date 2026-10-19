"""Converting inbox documents to Markdown text sidecars."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from liber.ledger import amend, file_key, load, word_count
from liber.inbox import NO_TEXT_MARKER, inbox_documents, sidecar_for

log = logging.getLogger(__name__)

Converter = Callable[[Path], str]
MIN_TEXT_CHARS = 20


def markitdown_convert(path: Path) -> str:
    from markitdown import MarkItDown

    return MarkItDown().convert(str(path)).text_content or ""


@dataclass(frozen=True)
class ExtractResult:
    name: str
    state: str
    detail: str = ""


def _amend_words(vault: Path, path: Path, words: int) -> None:
    """Fill in the word count the ledger couldn't know at capture; a ledger failure only warns."""
    try:
        key = file_key(path)
        if key in load(vault):
            amend(vault, key, words=words, owner_words=words)
    except OSError as exc:
        log.warning("couldn't update the ledger for %s (%s)", path.name, exc)


def extract_pending(vault: Path, convert: Converter = markitdown_convert) -> list[ExtractResult]:
    results = []
    for doc in inbox_documents(vault):
        if doc.state != "pending-extraction":
            continue
        path = vault / "inbox" / doc.name
        try:
            text = convert(path)
        except Exception as exc:  # any converter failure: report it, keep going
            results.append(ExtractResult(doc.name, "failed", f"{type(exc).__name__}: {exc}"))
            continue
        sidecar = sidecar_for(path)
        if len("".join(text.split())) < MIN_TEXT_CHARS:
            sidecar.write_text(NO_TEXT_MARKER + "\n", encoding="utf-8")
            results.append(ExtractResult(doc.name, "no-text"))  # words stay unknown; the sweep counts pasted text later
        else:
            sidecar.write_text(f"<!-- liber: extracted from {doc.name} -->\n\n{text.strip()}\n", encoding="utf-8")
            results.append(ExtractResult(doc.name, "extracted"))
            _amend_words(vault, path, word_count(text))
    return results
