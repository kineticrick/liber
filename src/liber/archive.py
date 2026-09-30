"""Moving processed inbox items into sources/."""

from datetime import datetime
from pathlib import Path

from liber.errors import LiberError
from liber.inbox import TEXT_SUFFIXES, inbox_template, pending_notes, sidecar_for
from liber.paths import free_name


def archive_document(vault: Path, name: str) -> list[Path]:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise LiberError(f"'{name}' is not a file name in inbox/")
    inbox = vault / "inbox"
    src = inbox / name
    if not src.is_file():
        raise LiberError(f"'{name}' is not in inbox/")
    if name.endswith(".md") and (inbox / name[:-3]).is_file():
        raise LiberError(f"'{name}' is extracted text; archive the original '{name[:-3]}' instead")
    sidecar = sidecar_for(src)
    if src.suffix.lower() not in TEXT_SUFFIXES and not sidecar.is_file():
        raise LiberError(f"'{name}' has not been converted to text yet; run liber extract first")

    dest_dir = vault / "sources" / "documents"
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = free_name(dest_dir, name)
    moved = [src.rename(dest_dir / final)]
    if sidecar.is_file():
        moved.append(sidecar.rename(dest_dir / f"{final}.md"))
    return moved


def archive_notes(vault: Path, now: datetime, count: int | None = None) -> Path | None:
    notes = pending_notes(vault)
    if count is not None:
        if count < 1:
            raise LiberError("--count must be at least 1")
        if count > len(notes):
            raise LiberError(f"asked to archive {count} notes but only {len(notes)} note(s) are pending")
    if not notes:
        return None
    take = len(notes) if count is None else count
    archived, remaining = notes[:take], notes[take:]

    dest = vault / "sources" / "notes" / f"{now:%Y-%m}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.read_text(encoding="utf-8") if dest.is_file() else f"# Notes archived {now:%Y-%m}\n"
    block = f"\n## Archived {now:%Y-%m-%d %H:%M}\n\n" + "\n".join(archived) + "\n"
    dest.write_text(existing + block, encoding="utf-8")

    inbox_text = inbox_template()
    if remaining:
        if not inbox_text.endswith("\n"):
            inbox_text += "\n"
        inbox_text += "\n" + "\n".join(remaining) + "\n"
    (vault / "inbox.md").write_text(inbox_text, encoding="utf-8")
    return dest
