import os
from datetime import datetime

import pytest
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.errors import LiberError
from liber.inbox import (
    NO_TEXT_MARKER, add_files, add_note, inbox_documents, inbox_status, pending_notes,
)
from liber.paths import free_name

NOW = datetime(2026, 9, 30, 14, 5)


def test_free_name(tmp_path):
    assert free_name(tmp_path, "thesis.pdf") == "thesis.pdf"
    (tmp_path / "thesis.pdf").write_text("x")
    assert free_name(tmp_path, "thesis.pdf") == "thesis-2.pdf"
    (tmp_path / "thesis-2.pdf.md").write_text("x")
    assert free_name(tmp_path, "thesis.pdf") == "thesis-3.pdf"
    (tmp_path / "README").write_text("x")
    assert free_name(tmp_path, "README") == "README-2"


def test_fresh_inbox_has_no_notes(vault):
    assert pending_notes(vault) == []


def test_add_note(vault):
    line = add_note(vault, "  I love mentoring  ", NOW)
    assert line == "- 2026-09-30 14:05 — I love mentoring"
    assert pending_notes(vault) == [line]


def test_notes_ignore_headings_and_comments_but_keep_tags(vault):
    inbox = vault / "inbox.md"
    inbox.write_text(inbox.read_text() + "\n## Heading\n#tag idea about pottery\nplain line")
    add_note(vault, "after a file with no trailing newline", NOW)
    assert pending_notes(vault) == [
        "#tag idea about pottery",
        "plain line",
        "- 2026-09-30 14:05 — after a file with no trailing newline",
    ]


def test_empty_note_rejected(vault):
    with pytest.raises(LiberError):
        add_note(vault, "   ", NOW)


def test_add_files_copies_with_collision_suffix(vault, tmp_path):
    src = write(tmp_path / "thesis.pdf", "pdf")
    first, second = add_files(vault, [src, src])
    assert first.name == "thesis.pdf" and second.name == "thesis-2.pdf"
    assert src.exists()


def test_add_missing_or_folder_rejected(vault, tmp_path):
    with pytest.raises(LiberError, match="not found"):
        add_files(vault, [tmp_path / "nope.pdf"])
    with pytest.raises(LiberError, match="folder"):
        add_files(vault, [tmp_path])


def test_document_states_and_order(vault):
    inbox = vault / "inbox"
    write(inbox / "a.pdf", "x")
    write(inbox / "b.docx", "x")
    write(inbox / "b.docx.md", "extracted text")
    write(inbox / "c.pdf", "x")
    write(inbox / "c.pdf.md", NO_TEXT_MARKER + "\n")
    write(inbox / "d.txt", "plain")
    (inbox / "folder").mkdir()
    for i, name in enumerate(["d.txt", "c.pdf", "b.docx", "a.pdf", "folder"]):
        os.utime(inbox / name, (1_000_000 + i, 1_000_000 + i))
    docs = inbox_documents(vault)
    assert [(d.name, d.state) for d in docs] == [
        ("d.txt", "ready"),
        ("c.pdf", "no-text"),
        ("b.docx", "extracted"),
        ("a.pdf", "pending-extraction"),
        ("folder", "unsupported-folder"),
    ]


def test_status_includes_conflicts(vault):
    write(vault / "inbox (Conflicted copy Pixel).md", "x")
    status = inbox_status(vault)
    assert status.conflicts == ["inbox (Conflicted copy Pixel).md"]


def test_cli_note_add_status(configured_vault, tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["note", "reconnected", "with", "Sam"]).exit_code == 0
    doc = write(tmp_path / "essay.pdf", "x")
    added = runner.invoke(app, ["add", str(doc)])
    assert added.exit_code == 0 and "inbox/essay.pdf" in added.output
    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0
    assert "reconnected with Sam" in status.output
    assert "essay.pdf" in status.output and "needs extraction" in status.output
    assert "Sync conflicts: none" in status.output
