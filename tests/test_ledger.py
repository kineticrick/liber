import logging

import pytest

from liber import ledger
from liber.errors import LiberError
from liber.ledger import Entry


def _entry(**kw):
    base = dict(key="sha256:a", kind="document", date="2026-10-18", tool="liber add")
    base.update(kw)
    return Entry(**base)


def test_record_is_idempotent(tmp_path):
    assert ledger.record(tmp_path, _entry()) is True
    assert ledger.record(tmp_path, _entry()) is False
    assert len(ledger.ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()) == 1


def test_known_file_keys_include_files(tmp_path):
    ledger.record(tmp_path, _entry(key="url:x", files=["sha256:f"]))
    assert {"url:x", "sha256:f"} <= ledger.known_file_keys(ledger.load(tmp_path))


def test_amend_merges_last_wins(tmp_path):
    ledger.record(tmp_path, _entry())
    ledger.amend(tmp_path, "sha256:a", words=120, owner_words=120)
    got = ledger.load(tmp_path)["sha256:a"]
    assert got.words == 120 and got.owner_words == 120
    assert got.kind == "document"


def test_amend_unknown_key_raises(tmp_path):
    with pytest.raises(LiberError):
        ledger.amend(tmp_path, "sha256:nope", words=1)


def test_orphan_amend_line_is_ignored_with_warning(tmp_path, caplog):
    ledger.ledger_path(tmp_path).write_text('{"key": "sha256:z", "amends": true, "words": 3}\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert ledger.load(tmp_path) == {}
    assert caplog.records


def test_load_skips_corrupt_line(tmp_path, caplog):
    ledger.record(tmp_path, _entry())
    with ledger.ledger_path(tmp_path).open("a", encoding="utf-8") as f:
        f.write('{"key": "sha256:b", "kin')
    with caplog.at_level(logging.WARNING):
        assert len(ledger.load(tmp_path)) == 1
    assert caplog.records


def test_missing_ledger_is_empty(tmp_path):
    assert ledger.load(tmp_path) == {}


def test_unknown_kind_rejected():
    with pytest.raises(LiberError):
        _entry(kind="podcast")


def test_note_key_ignores_whitespace_and_crlf():
    assert ledger.note_key("buy  milk\r\n") == ledger.note_key("buy milk")


def test_file_key_is_content_based(tmp_path):
    a, b, c = tmp_path / "a.txt", tmp_path / "b.md", tmp_path / "c.txt"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    c.write_bytes(b"different")
    assert ledger.file_key(a) == ledger.file_key(b)
    assert ledger.file_key(a) != ledger.file_key(c)
    assert ledger.file_key(a).startswith("sha256:")


TRANSCRIPT = "**Jake** [00:01]: hi there\n\n**Rick Boone** [00:05]: one two three\n\n**Rick Boone:** four five"


def test_owner_words_transcript_formats():
    assert ledger.owner_words(TRANSCRIPT, "Rick Boone") == 5
    assert ledger.owner_words(TRANSCRIPT, "Rick") == 5


def test_owner_words_me_label():
    assert ledger.owner_words("**Me** [00:01]: a b\n\n**Interviewer** [00:02]: c", None) == 2


def test_owner_words_none_without_owner_or_me():
    assert ledger.owner_words(TRANSCRIPT, None) is None


def test_word_count_strips_frontmatter_and_comments():
    assert ledger.word_count("---\ntype: x\n---\n<!-- c -->\nhello world") == 2


def test_owner_name_prefers_owner_section(tmp_path):
    from liber.config import user_config_path

    cfg = user_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[owner]\nname = "Rick Boone"\n[interview]\nname = "Rick"\n', encoding="utf-8")
    assert ledger.owner_name() == "Rick Boone"
    cfg.write_text('[interview]\nname = "Rick"\n', encoding="utf-8")
    assert ledger.owner_name() == "Rick"
    cfg.write_text('', encoding="utf-8")
    assert ledger.owner_name() is None


def test_record_after_a_partial_last_line_is_not_lost(tmp_path):
    first = Entry(key="a", kind="note", date="2026-10-01", tool="t")
    ledger.record(tmp_path, first)
    with ledger.ledger_path(tmp_path).open("a", encoding="utf-8") as f:
        f.write('{"key": "frag')
    second = Entry(key="b", kind="note", date="2026-10-01", tool="t")
    assert ledger.record(tmp_path, second)
    assert set(ledger.load(tmp_path)) == {"a", "b"}


def _inbox(vault):
    d = vault / "inbox"
    d.mkdir(exist_ok=True)
    return d


def test_sweep_records_unrecorded_files_and_skips_sidecars(vault):
    from datetime import date

    inbox = _inbox(vault)
    (inbox / "a.pdf").write_bytes(b"%PDF")
    (inbox / "a.pdf.md").write_text("one two", encoding="utf-8")
    (inbox / "b.md").write_text("hello there world", encoding="utf-8")
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=False) == 2
    entries = ledger.load(vault)
    assert entries[ledger.file_key(inbox / "a.pdf")].words == 2
    assert ledger.file_key(inbox / "a.pdf.md") not in entries


def test_sweep_notes_only_when_asked(vault):
    from datetime import date

    (vault / "inbox.md").write_text("# Inbox\n\n- 2026-10-01 09:00 — phone note here\n", encoding="utf-8")
    today = date(2026, 10, 18)
    assert ledger.sweep(vault, today, include_notes=False) == 0
    assert ledger.sweep(vault, today, include_notes=True) == 1
    (entry,) = ledger.load(vault).values()
    assert entry.date == "2026-10-01" and entry.kind == "note" and entry.words == 3


def test_sweep_is_idempotent(vault):
    from datetime import date

    (_inbox(vault) / "b.md").write_text("hello", encoding="utf-8")
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=True) == 1
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=True) == 0


def test_sweep_skips_files_owned_by_other_entries(vault):
    from datetime import date

    inbox = _inbox(vault)
    (inbox / "t.md").write_text("talk", encoding="utf-8")
    (inbox / "i.md").write_text("<!-- liber interview transcript -->\n**Me:** hi", encoding="utf-8")
    (inbox / "interview-1-notes.md").write_text("notes", encoding="utf-8")
    ledger.record(vault, _entry(key="url:x", kind="talk-audio", files=[ledger.file_key(inbox / "t.md")]))
    ledger.record(vault, _entry(key="session:1", kind="voice-interview", files=[
        ledger.file_key(inbox / "i.md"), ledger.file_key(inbox / "interview-1-notes.md")]))
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=False) == 0


def test_kind_for_markers():
    from pathlib import Path

    k = ledger.kind_for
    assert k(Path("x.md"), "<!-- liber proposal: writing analysis, 2026-10-18 -->\nbody") == "derived"
    assert k(Path("x.md"), "<!-- liber proposal -->\nbody") == "proposal"
    assert k(Path("x.md"), "<!-- liber interview transcript -->\n") == "voice-interview"
    assert k(Path("interview-2026-10-18-notes.md"), "notes") == "derived"
    assert k(Path("x.md"), "<!-- liber transcript: AssemblyAI -->\n") == "talk-audio"
    assert k(Path("transcript-foo.md"), "# T") == "interview-written"
    assert k(Path("my-journal.md"), "text") == "journal"
    assert k(Path("x.pdf"), None) == "document"


def test_sweep_derived_counts_nothing(vault):
    from datetime import date

    (_inbox(vault) / "p.md").write_text("<!-- liber proposal: writing analysis, 2026-10-18 -->\nsome words", encoding="utf-8")
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=False) == 1
    (entry,) = ledger.load(vault).values()
    assert entry.kind == "derived" and entry.items == 0 and entry.words == 0 and entry.owner_words == 0


# --- Task 4: merge and backfill ---

import json
import os
import subprocess
from datetime import date


def _commit(vault, msg, day):
    env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T12:00:00", "GIT_COMMITTER_DATE": f"{day}T12:00:00",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(vault), "commit", "-q", "-m", msg], check=True, env=env)


def test_merge_unions_keys(tmp_path):
    ledger.record(tmp_path, _entry(key="a"))
    ledger.record(tmp_path, _entry(key="b"))
    other = tmp_path / "ledger.sync-conflict.jsonl"
    other.write_text(
        json.dumps({"key": "b", "kind": "document", "date": "2026-10-18", "tool": "x"}) + "\n"
        + json.dumps({"key": "c", "kind": "document", "date": "2026-10-18", "tool": "x"}) + "\n", encoding="utf-8")
    assert ledger.merge(tmp_path, other) == 1
    assert set(ledger.load(tmp_path)) == {"a", "b", "c"}
    assert not other.exists()


def _archive_fixture(vault):
    (vault / "sources" / "documents").mkdir(parents=True, exist_ok=True)
    (vault / "sources" / "notes").mkdir(parents=True, exist_ok=True)
    (vault / "sources" / "documents" / "x.md").write_text("hello there world", encoding="utf-8")
    (vault / "sources" / "notes" / "2026-09.md").write_text(
        "# Notes archived 2026-09\n\n## Archived 2026-09-05 10:00\n\n"
        "- 2026-09-01 08:00 — first note here\n- 2026-09-02 09:00 — second\n", encoding="utf-8")


def test_backfill_records_archived_sources_before_cutoff(vault):
    _archive_fixture(vault)
    _commit(vault, "archive", "2026-09-10")
    assert ledger.backfill(vault, date.today()) == 3
    assert ledger.backfill(vault, date.today()) == 0
    entries = ledger.load(vault)
    assert entries["meta:backfill"].items == 0
    doc = entries[ledger.file_key(vault / "sources/documents/x.md")]
    assert doc.date == "2026-09-10" and doc.tool == "backfill" and doc.words == 3
    assert ledger.note_key("second") in entries


def test_backfill_skips_items_already_recorded_under_another_path(vault):
    (vault / "inbox").mkdir(exist_ok=True)
    src = vault / "inbox" / "x.md"
    src.write_text("same bytes", encoding="utf-8")
    ledger.record(vault, _entry(key=ledger.file_key(src), path="inbox/x.md"))
    (vault / "sources" / "documents").mkdir(parents=True, exist_ok=True)
    src.rename(vault / "sources" / "documents" / "x-2.md")
    _commit(vault, "archive", "2026-09-10")
    assert ledger.backfill(vault, date.today()) == 0


def test_backfill_respects_cutoff(vault):
    _archive_fixture(vault)
    _commit(vault, "archive", "2026-09-10")
    assert ledger.backfill(vault, date(2026, 9, 1)) == 0
    assert ledger.backfill(vault, date.today()) == 0  # the stored cutoff wins


def test_backfill_amends_binary_with_sidecar_words(vault):
    (vault / "sources" / "documents").mkdir(parents=True, exist_ok=True)
    (vault / "sources" / "documents" / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    (vault / "sources" / "documents" / "a.pdf.md").write_text("one two three four", encoding="utf-8")
    _commit(vault, "archive", "2026-09-10")
    assert ledger.backfill(vault, date.today()) == 1
    (e,) = [e for k, e in ledger.load(vault).items() if k != "meta:backfill"]
    assert e.words == 4 and e.path == "sources/documents/a.pdf"


# --- final-review fixes ---

NOTES = "<!-- liber interview notes -->\n# Interview notes\n\nlots of ai written words here\n"


def test_kind_for_interview_notes_marker_and_renamed():
    from pathlib import Path

    assert ledger.kind_for(Path("interview-2026-10-18-notes-2.md"), "notes") == "derived"
    assert ledger.kind_for(Path("whatever.md"), NOTES) == "derived"


def test_sweep_regenerated_notes_are_derived(vault):
    inbox = _inbox(vault)
    (inbox / "interview-2026-10-18-x-notes-2.md").write_text(NOTES, encoding="utf-8")
    (inbox / "renamed.md").write_text(NOTES + "more", encoding="utf-8")
    ledger.sweep(vault, date(2026, 10, 18), include_notes=False)
    assert {(e.kind, e.items, e.owner_words) for e in ledger.load(vault).values()} == {("derived", 0, 0)}


def test_backfill_regenerated_notes_are_derived(vault):
    (vault / "sources" / "documents").mkdir(parents=True, exist_ok=True)
    (vault / "sources" / "documents" / "interview-2026-10-18-x-notes-2.md").write_text(NOTES, encoding="utf-8")
    _commit(vault, "archive", "2026-09-10")
    assert ledger.backfill(vault, date.today()) == 0
    (e,) = [e for k, e in ledger.load(vault).items() if k != "meta:backfill"]
    assert e.kind == "derived" and e.owner_words == 0


def test_backfill_count_excludes_derived(vault):
    (vault / "sources" / "documents").mkdir(parents=True, exist_ok=True)
    (vault / "sources" / "documents" / "a.md").write_text("<!-- liber proposal: x -->\nbody", encoding="utf-8")
    (vault / "sources" / "documents" / "b.md").write_text("mine", encoding="utf-8")
    _commit(vault, "archive", "2026-09-10")
    assert ledger.backfill(vault, date.today()) == 1


def test_sweep_amends_an_edited_inbox_file(vault, tmp_path):
    from liber.inbox import add_files

    src = tmp_path / "draft.md"
    src.write_text("one two", encoding="utf-8")
    (dest,) = add_files(vault, [src])
    first = ledger.file_key(dest)
    dest.write_text("one two three four", encoding="utf-8")
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=False) == 0
    (e,) = ledger.load(vault).values()
    assert e.key == first and e.words == 4 and e.owner_words == 4 and ledger.file_key(dest) in e.files
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=False) == 0
    assert len(ledger.ledger_path(vault).read_text(encoding="utf-8").splitlines()) == 2


def test_owner_words_unknown_without_any_turns():
    assert ledger.owner_words("just some prose, no speaker turns", "Rick Boone") is None
    assert ledger.owner_words("**Jake:** hi there", "Rick Boone") == 0


def test_sweep_fills_owner_words_once_owner_is_known(vault):
    from liber.config import user_config_path

    (_inbox(vault) / "transcript-x.md").write_text("**Host:** hi\n\n**Rick Boone:** one two three", encoding="utf-8")
    ledger.sweep(vault, date(2026, 10, 18), include_notes=False)
    (e,) = ledger.load(vault).values()
    assert e.owner_words is None
    cfg = user_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[owner]\nname = "Rick Boone"\n', encoding="utf-8")
    ledger.sweep(vault, date(2026, 10, 18), include_notes=False)
    (e,) = ledger.load(vault).values()
    assert e.owner_words == 3


def test_load_survives_invalid_utf8(tmp_path, caplog):
    ledger.record(tmp_path, _entry())
    with ledger.ledger_path(tmp_path).open("ab") as f:
        f.write(b'\xff\xfe{"key": "x\n')
    ledger.record(tmp_path, _entry(key="sha256:b"))
    with caplog.at_level(logging.WARNING):
        assert set(ledger.load(tmp_path)) == {"sha256:a", "sha256:b"}


def test_note_key_parity_across_capture_archive_and_backfill(vault):
    from datetime import datetime

    from liber.archive import archive_notes
    from liber.inbox import add_note

    now = datetime(2026, 9, 30, 10, 0)
    add_note(vault, "a  note with   odd spacing — and a dash", now)
    assert ledger.sweep(vault, date(2026, 10, 18), include_notes=True) == 0
    archive_notes(vault, now)
    _commit(vault, "archive", "2026-09-30")
    assert ledger.backfill(vault, date.today()) == 0
    assert [e.kind for k, e in ledger.load(vault).items() if k != "meta:backfill"] == ["note"]
