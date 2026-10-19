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
