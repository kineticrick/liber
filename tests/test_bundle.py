import pytest
from typer.testing import CliRunner

import liber.cli
from helpers import write
from liber.bundle import build_bundle
from liber.cli import app
from liber.docs import iter_content_files
from liber.errors import LiberError
from liber.vaultconfig import SENSITIVITY_LEVELS, sensitivity_rank


def fm(type, sens):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n# {type} {sens}\n"


@pytest.fixture
def mixed(vault):
    write(vault / "core" / "pub.md", fm("core", "public"))
    write(vault / "core" / "priv.md", fm("core", "private"))
    write(vault / "career" / "projects" / "secret-project.md", fm("project", "private"))
    write(vault / "career" / "projects" / "open-project.md", fm("project", "public"))
    write(vault / "people" / "Sam Chen.md", fm("person", "personal"))
    write(vault / "sources" / "notes" / "2026-09.md", "raw notes")
    write(vault / "inbox" / "doc.md", "raw doc")
    return vault


@pytest.mark.parametrize("ceiling", SENSITIVITY_LEVELS)
def test_ceiling_includes_exactly_the_allowed_files(mixed, ceiling):
    bundle = build_bundle(mixed, max_sensitivity=ceiling)
    limit = sensitivity_rank(ceiling)
    expected = {vf.rel for vf in iter_content_files(mixed) if sensitivity_rank(vf.meta.get("sensitivity")) <= limit}
    assert set(bundle.included) == expected | {"AGENTS.md"}
    for rel in bundle.included:
        assert f"## `{rel}`" in bundle.text


def test_personal_ceiling_by_name(mixed):
    bundle = build_bundle(mixed)
    assert "core/pub.md" in bundle.included and "people/Sam Chen.md" in bundle.included
    assert "core/priv.md" not in bundle.included and "career/projects/secret-project.md" not in bundle.included
    assert "# core private" not in bundle.text and "# project private" not in bundle.text
    assert ("core/priv.md", "private") in bundle.excluded


def test_missing_or_invalid_sensitivity_is_excluded(vault):
    # Review Focus 2: fail safe — unknown sensitivity never leaks.
    write(vault / "core" / "nosens.md", "---\ntype: core\nupdated: 2026-09-30\n---\n\nSECRET-A\n")
    write(vault / "core" / "typo.md", fm("core", "privte").replace("# core privte", "SECRET-B"))
    write(vault / "core" / "nofm.md", "SECRET-C\n")
    bundle = build_bundle(vault, max_sensitivity="private")
    for marker in ("SECRET-A", "SECRET-B", "SECRET-C"):
        assert marker not in bundle.text
    reasons = dict(bundle.excluded)
    assert "sensitivity" in reasons["core/nosens.md"]
    assert "sensitivity" in reasons["core/typo.md"]


def test_agents_first_and_header(mixed):
    bundle = build_bundle(mixed)
    assert bundle.text.startswith("# liber context bundle")
    assert bundle.included[0] == "AGENTS.md"
    assert "sensitivity ceiling: personal" in bundle.text
    assert "raw notes" not in bundle.text and "raw doc" not in bundle.text


def test_topics_filter_includes_subfolders(mixed):
    bundle = build_bundle(mixed, topics=["career"], max_sensitivity="private")
    rels = set(bundle.included) - {"AGENTS.md"}
    assert rels and all(rel.startswith("career/") for rel in rels)
    assert "career/projects/open-project.md" in rels


def test_unknown_topic_and_ceiling(mixed):
    with pytest.raises(LiberError, match="core"):
        build_bundle(mixed, topics=["carrer"])
    with pytest.raises(LiberError, match="public"):
        build_bundle(mixed, max_sensitivity="secret")


def test_cli_bundle_stdout(configured_vault):
    result = CliRunner().invoke(app, ["bundle", "--topics", "core,career"])
    assert result.exit_code == 0
    assert "# liber context bundle" in result.output
    assert "## `core/identity.md`" in result.output


def test_cli_bundle_copy(configured_vault, monkeypatch):
    copied = {}
    monkeypatch.setattr(liber.cli, "copy_to_clipboard", lambda text: copied.setdefault("text", text) is not None)
    result = CliRunner().invoke(app, ["bundle", "--copy"])
    assert result.exit_code == 0
    assert "Copied" in result.output
    assert copied["text"].startswith("# liber context bundle")


def test_cli_bundle_copy_fallback(configured_vault, monkeypatch):
    monkeypatch.setattr(liber.cli, "copy_to_clipboard", lambda text: False)
    result = CliRunner().invoke(app, ["bundle", "--copy"])
    assert result.exit_code == 0
    assert "# liber context bundle" in result.output
    assert "wl-copy" in result.output


CONFLICT = "interests/rust (Conflicted copy phone 202609301200).md"


def test_bundle_excludes_sync_conflict_copies(vault):
    write(vault / "interests" / "rust.md", fm("interest", "private"))
    write(vault / CONFLICT, fm("interest", "personal") + "\nLEAKMARKER\n")
    bundle = build_bundle(vault)
    assert "LEAKMARKER" not in bundle.text
    assert "interests/rust.md" not in bundle.included
    assert CONFLICT not in bundle.included
    assert (CONFLICT, "sync-conflict copy") in bundle.excluded


def test_cli_bundle_lists_each_exclusion(configured_vault):
    write(configured_vault / "core" / "typo.md", fm("core", "personl"))
    result = CliRunner().invoke(app, ["bundle"])
    assert "excluded: core/typo.md (" in result.output
