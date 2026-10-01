import os
import re
from datetime import date

import pytest

from helpers import write
from liber.errors import LiberError
from liber.server.knowledge import NotFound, VaultView
from liber.vaultconfig import SENSITIVITY_LEVELS, sensitivity_rank


def fm(type, sens, body, updated="2026-09-30"):
    return f"---\ntype: {type}\nupdated: {updated}\nsensitivity: {sens}\ntags: [t]\n---\n\n{body}\n"


@pytest.fixture
def mixed(vault):
    write(vault / "core" / "pub.md", fm("core", "public", "# Public me\nPUB-MARK"))
    write(vault / "core" / "pers.md", fm("core", "personal", "# Personal me\nPERS-MARK"))
    write(vault / "core" / "priv.md", fm("core", "private", "# Private me\nPRIV-MARK"))
    write(vault / "core" / "nosens.md", "---\ntype: core\nupdated: 2026-09-30\n---\n\nNOSENS-MARK\n")
    write(vault / "core" / "pers (Conflicted copy phone).md", fm("core", "personal", "CONFLICT-MARK"))
    write(vault / "sources" / "documents" / "thesis.pdf.md", "SOURCE-MARK\n")
    write(vault / "sources" / "documents" / "claims-public.md", fm("core", "public", "SOURCE-PUBLIC-MARK"))
    write(vault / "inbox" / "doc.md", "INBOX-MARK\n")
    write(vault / "inbox.md", "# Inbox\n- INBOXNOTE-MARK\n")
    return vault


# rel -> lowest ceiling at which it is visible
EXPECTED = {
    "AGENTS.md": "public",
    "core/pub.md": "public",
    "core/pers.md": "personal",
    "open-questions.md": "personal",
    "core/priv.md": "private",
    "sources/documents/thesis.pdf.md": "private",
    "sources/documents/claims-public.md": "private",  # Review Focus 2: sources are always private
}
NEVER = [
    "core/nosens.md", "core/pers (Conflicted copy phone).md", "inbox/doc.md", "inbox.md",
    "CLAUDE.md", "README.md", "liber.toml", "_templates/person.md",
]


@pytest.mark.parametrize("ceiling", SENSITIVITY_LEVELS)
def test_list_respects_ceiling_both_directions(mixed, ceiling):
    entries = VaultView(mixed, ceiling).list()
    listed = {e["path"] for e in entries}
    for rel, minimum in EXPECTED.items():
        assert (rel in listed) == (sensitivity_rank(ceiling) >= sensitivity_rank(minimum)), rel
    for rel in NEVER:
        assert rel not in listed
    for e in entries:
        assert sensitivity_rank(e["sensitivity"]) <= sensitivity_rank(ceiling)


@pytest.mark.parametrize("ceiling", SENSITIVITY_LEVELS)
def test_read_respects_ceiling_both_directions(mixed, ceiling):
    view = VaultView(mixed, ceiling)
    for rel, minimum in EXPECTED.items():
        if sensitivity_rank(ceiling) >= sensitivity_rank(minimum):
            assert view.read(rel) == (mixed / rel).read_text(encoding="utf-8")
        else:
            with pytest.raises(NotFound, match=re.escape(f"not found: {rel}")):
                view.read(rel)
    for rel in NEVER:
        with pytest.raises(NotFound):
            view.read(rel)


def test_hidden_and_missing_give_the_same_error(mixed):
    view = VaultView(mixed, "personal")
    with pytest.raises(NotFound) as hidden:
        view.read("core/priv.md")
    with pytest.raises(NotFound) as missing:
        view.read("core/nope.md")
    assert str(hidden.value) == "not found: core/priv.md"
    assert str(missing.value) == "not found: core/nope.md"


@pytest.mark.parametrize("path", ["../AGENTS.md", "/etc/passwd", "core/../core/pub.md", "core\\pub.md", "", "core/./pub.md"])
def test_escape_attempts_are_not_found(mixed, path):
    with pytest.raises(NotFound):
        VaultView(mixed, "private").read(path)


def test_symlinks_escaping_the_vault_are_invisible(mixed, tmp_path):
    # Review Focus 1
    outside = tmp_path / "outside"
    secret = write(outside / "secret.md", fm("core", "public", "OUTSIDE-MARK"))
    os.symlink(secret, mixed / "core" / "linked.md")
    os.symlink(outside, mixed / "interests" / "linkdir")
    view = VaultView(mixed, "private")
    paths = {e["path"] for e in view.list()}
    assert not any("linked" in p or "linkdir" in p for p in paths)
    for rel in ("core/linked.md", "interests/linkdir/secret.md"):
        with pytest.raises(NotFound):
            view.read(rel)


def test_unreadable_files_are_skipped(mixed):
    (mixed / "core" / "binary.md").write_bytes(b"\xff\xfe\x00bad")
    assert "core/binary.md" not in {e["path"] for e in VaultView(mixed, "private").list()}


def test_summary_fields(mixed):
    entry = next(e for e in VaultView(mixed, "public").list() if e["path"] == "core/pub.md")
    assert entry == {
        "path": "core/pub.md", "title": "Public me", "type": "core",
        "updated": "2026-09-30", "sensitivity": "public", "tags": ["t"],
    }


def test_title_falls_back_to_file_stem(mixed):
    write(mixed / "interests" / "chess.md", fm("interest", "personal", "no heading here"))
    entry = next(e for e in VaultView(mixed, "personal").list() if e["path"] == "interests/chess.md")
    assert entry["title"] == "chess"


def test_list_folder_filter(mixed):
    assert {e["path"] for e in VaultView(mixed, "private").list("sources/")} == {
        "sources/documents/thesis.pdf.md", "sources/documents/claims-public.md",
    }
    assert VaultView(mixed, "personal").list("sources") == []
    assert {e["path"] for e in VaultView(mixed, "public").list("core")} == {"core/pub.md"}


def test_profile(mixed):
    p = VaultView(mixed, "personal").profile(date(2026, 10, 1))
    assert p["profile"] == (mixed / "AGENTS.md").read_text(encoding="utf-8")
    assert p["today"] == "2026-10-01"
    assert p["access_level"] == "personal"
    assert "core" in p["folders"] and "sources" not in p["folders"]
    assert "sources" in VaultView(mixed, "private").profile(date(2026, 10, 1))["folders"]


def test_invalid_ceiling(vault):
    with pytest.raises(LiberError, match="ceiling"):
        VaultView(vault, "secret")
