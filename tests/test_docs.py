from datetime import date

from helpers import write
from liber.docs import is_hidden_part, iter_content_files, read_vault_file

GOOD = "---\ntype: core\nupdated: 2026-09-30\nsensitivity: personal\ntags: []\n---\n\n# Identity\n"


def test_read_parses_frontmatter(tmp_path):
    path = write(tmp_path / "core" / "identity.md", GOOD)
    vf = read_vault_file(tmp_path, path)
    assert vf.rel == "core/identity.md"
    assert vf.has_frontmatter
    assert vf.parse_error is None
    assert vf.meta["type"] == "core"
    assert vf.meta["updated"] == date(2026, 9, 30)
    assert vf.text == GOOD


def test_read_without_frontmatter(tmp_path):
    vf = read_vault_file(tmp_path, write(tmp_path / "core" / "x.md", "# Just text\n"))
    assert not vf.has_frontmatter
    assert vf.meta == {}


def test_read_with_broken_yaml(tmp_path):
    vf = read_vault_file(tmp_path, write(tmp_path / "core" / "x.md", "---\ntype: [\n---\n"))
    assert vf.has_frontmatter
    assert vf.parse_error


def test_iter_content_files_skips_root_exempt_and_hidden(tmp_path):
    write(tmp_path / "AGENTS.md", GOOD)
    write(tmp_path / "core" / "identity.md", GOOD)
    write(tmp_path / "career" / "projects" / "p.md", GOOD)
    write(tmp_path / "sources" / "notes" / "2026-09.md", "x")
    write(tmp_path / "inbox" / "doc.md", "x")
    write(tmp_path / ".obsidian" / "x.md", "x")
    write(tmp_path / "_templates" / "person.md", "x")
    write(tmp_path / "core" / "photo.png", "x")
    rels = [vf.rel for vf in iter_content_files(tmp_path)]
    assert rels == ["career/projects/p.md", "core/identity.md"]


def test_is_hidden_part():
    assert is_hidden_part(".git")
    assert is_hidden_part("_templates")
    assert not is_hidden_part("core")
