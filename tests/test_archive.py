from datetime import datetime

import pytest
from typer.testing import CliRunner

from helpers import write
from liber.archive import archive_document, archive_notes
from liber.cli import app
from liber.errors import LiberError
from liber.inbox import add_note, pending_notes

NOW = datetime(2026, 9, 30, 15, 0)


def test_archive_document_with_sidecar(vault):
    write(vault / "inbox" / "thesis.pdf", "pdf")
    write(vault / "inbox" / "thesis.pdf.md", "text")
    moved = archive_document(vault, "thesis.pdf")
    docs = vault / "sources" / "documents"
    assert moved == [docs / "thesis.pdf", docs / "thesis.pdf.md"]
    assert not (vault / "inbox" / "thesis.pdf").exists()
    assert not (vault / "inbox" / "thesis.pdf.md").exists()


def test_archive_collision_renames_both(vault):
    write(vault / "sources" / "documents" / "thesis.pdf", "old")
    write(vault / "inbox" / "thesis.pdf", "new")
    write(vault / "inbox" / "thesis.pdf.md", "new text")
    moved = archive_document(vault, "thesis.pdf")
    assert [p.name for p in moved] == ["thesis-2.pdf", "thesis-2.pdf.md"]
    assert (vault / "sources" / "documents" / "thesis.pdf").read_text() == "old"


def test_archive_markdown_document_without_sidecar(vault):
    write(vault / "inbox" / "essay.md", "text")
    assert [p.name for p in archive_document(vault, "essay.md")] == ["essay.md"]


@pytest.mark.parametrize("name", ["missing.pdf", "../AGENTS.md", "sub/x.pdf", "..", ""])
def test_archive_rejects_bad_names(vault, name):
    # Review Focus 5: nothing outside inbox/ may be moved.
    with pytest.raises(LiberError):
        archive_document(vault, name)
    assert (vault / "AGENTS.md").exists()


def test_archive_refuses_unextracted_and_sidecars(vault):
    write(vault / "inbox" / "a.pdf", "x")
    with pytest.raises(LiberError, match="liber extract"):
        archive_document(vault, "a.pdf")
    write(vault / "inbox" / "a.pdf.md", "text")
    with pytest.raises(LiberError, match="a.pdf"):
        archive_document(vault, "a.pdf.md")


def test_archive_notes(vault):
    add_note(vault, "first", NOW)
    add_note(vault, "second", NOW)
    dest = archive_notes(vault, NOW)
    assert dest == vault / "sources" / "notes" / "2026-09.md"
    text = dest.read_text()
    assert "## Archived 2026-09-30 15:00" in text and "first" in text and "second" in text
    assert pending_notes(vault) == []
    assert (vault / "inbox.md").read_text().startswith("# Inbox")


def test_archive_notes_appends_to_month_file(vault):
    add_note(vault, "one", NOW)
    archive_notes(vault, NOW)
    add_note(vault, "two", NOW)
    dest = archive_notes(vault, datetime(2026, 9, 30, 16, 0))
    text = dest.read_text()
    assert text.count("## Archived") == 2 and "one" in text and "two" in text


def test_archive_notes_count_keeps_late_arrivals(vault):
    # Review Focus 1: a note added (e.g. on the phone) after review stays in the inbox.
    add_note(vault, "reviewed one", NOW)
    add_note(vault, "reviewed two", NOW)
    add_note(vault, "arrived during ingest", NOW)
    archive_notes(vault, NOW, count=2)
    remaining = pending_notes(vault)
    assert len(remaining) == 1 and "arrived during ingest" in remaining[0]
    assert "arrived during ingest" not in (vault / "sources" / "notes" / "2026-09.md").read_text()


def test_archive_notes_bad_count(vault):
    add_note(vault, "only one", NOW)
    with pytest.raises(LiberError, match="1 note"):
        archive_notes(vault, NOW, count=2)
    with pytest.raises(LiberError):
        archive_notes(vault, NOW, count=0)


def test_archive_notes_when_empty(vault):
    assert archive_notes(vault, NOW) is None


def test_cli_archive(configured_vault):
    runner = CliRunner()
    write(configured_vault / "inbox" / "essay.md", "text")
    result = runner.invoke(app, ["archive", "essay.md"])
    assert result.exit_code == 0 and "sources/documents/essay.md" in result.output
    add_note(configured_vault, "n1", NOW)
    add_note(configured_vault, "n2", NOW)
    notes = runner.invoke(app, ["archive", "--notes", "--count", "1"])
    assert notes.exit_code == 0 and "sources/notes/" in notes.output
    assert len(pending_notes(configured_vault)) == 1
    assert runner.invoke(app, ["archive"]).exit_code == 1
    assert runner.invoke(app, ["archive", "x.pdf", "--notes"]).exit_code == 1
