"""Converting inbox documents to Markdown text sidecars."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from liber.inbox import NO_TEXT_MARKER, inbox_documents, sidecar_for

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
            results.append(ExtractResult(doc.name, "no-text"))
        else:
            sidecar.write_text(f"<!-- liber: extracted from {doc.name} -->\n\n{text.strip()}\n", encoding="utf-8")
            results.append(ExtractResult(doc.name, "extracted"))
    return results
