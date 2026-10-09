from typer.testing import CliRunner

from helpers import write
from liber.check import find_conflicts, run_checks
from liber.cli import app

FM = "---\ntype: {type}\nupdated: {updated}\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def note(type="core", updated="2026-09-30", sens="personal", body="# x"):
    return FM.format(type=type, updated=updated, sens=sens, body=body)


def kinds(vault):
    return sorted((p.rel, p.kind) for p in run_checks(vault))


def test_fresh_vault_is_clean(vault):
    assert run_checks(vault) == []


def test_missing_frontmatter(vault):
    write(vault / "core" / "x.md", "# no frontmatter\n")
    assert kinds(vault) == [("core/x.md", "frontmatter")]


def test_broken_yaml(vault):
    write(vault / "core" / "x.md", "---\ntype: [\n---\n")
    assert kinds(vault) == [("core/x.md", "frontmatter")]


def test_wrong_type_for_folder(vault):
    write(vault / "career" / "projects" / "p.md", note(type="career"))
    problems = run_checks(vault)
    assert [(p.rel, p.kind) for p in problems] == [("career/projects/p.md", "frontmatter")]
    assert "project" in problems[0].message


def test_bad_sensitivity_and_date(vault):
    write(vault / "core" / "a.md", note(sens="secret"))
    write(vault / "core" / "b.md", note(updated="last week"))
    assert kinds(vault) == [("core/a.md", "frontmatter"), ("core/b.md", "frontmatter")]


def test_tags_must_be_a_list(vault):
    write(vault / "core" / "a.md", note().replace("tags: []", "tags: oops"))
    assert kinds(vault) == [("core/a.md", "frontmatter")]


def test_unknown_folder(vault):
    write(vault / "finance" / "budget.md", note(type="finance"))
    assert kinds(vault) == [("finance/budget.md", "unknown-folder")]


def test_new_folder_accepted_after_liber_toml_update(vault):
    toml = vault / "liber.toml"
    toml.write_text(toml.read_text() + 'finance = { type = "finance" }\n')
    write(vault / "finance" / "budget.md", note(type="finance"))
    assert run_checks(vault) == []


def test_stray_root_note(vault):
    write(vault / "random.md", "# hi\n")
    assert kinds(vault) == [("random.md", "unknown-file")]


def test_agents_must_be_public(vault):
    agents = vault / "AGENTS.md"
    agents.write_text(agents.read_text().replace("sensitivity: public", "sensitivity: personal"))
    assert kinds(vault) == [("AGENTS.md", "frontmatter")]


def test_broken_and_resolved_links(vault):
    write(vault / "people" / "Sam Chen.md", note(type="person"))
    write(vault / "sources" / "documents" / "thesis.pdf", "x")
    body = "Met [[Sam Chen]] and [[Sam Chen|Sam]] and [[Sam Chen#Notes]]. (src: [[thesis.pdf]]) Also [[Nobody]]. `[[InCode]]`"
    write(vault / "core" / "x.md", note(body=body))
    problems = run_checks(vault)
    assert [(p.rel, p.kind) for p in problems] == [("core/x.md", "broken-link")]
    assert "Nobody" in problems[0].message


def test_links_inside_fenced_code_are_ignored(vault):
    write(vault / "core" / "x.md", note(body="```\n[[Ghost]]\n```"))
    assert run_checks(vault) == []


def test_sync_conflicts_detected(vault):
    write(vault / "core" / "identity (Conflicted copy Pixel 202609301200).md", note())
    write(vault / "inbox.sync-conflict-20260930-120000-ABC.md", "x")
    assert find_conflicts(vault, ("*conflicted copy*", "*.sync-conflict-*")) == [
        "core/identity (Conflicted copy Pixel 202609301200).md",
        "inbox.sync-conflict-20260930-120000-ABC.md",
    ]
    assert ("inbox.sync-conflict-20260930-120000-ABC.md", "sync-conflict") in kinds(vault)


def test_note_named_conflict_is_not_a_sync_conflict(vault):
    # Review Focus 4: legitimate names containing "conflict" must not be flagged.
    write(vault / "interests" / "conflict-resolution.md", note(type="interest"))
    assert run_checks(vault) == []


def test_agents_size_limit(vault):
    agents = vault / "AGENTS.md"
    agents.write_text(agents.read_text() + "word " * 3000)
    assert ("AGENTS.md", "size") in kinds(vault)


def test_cli_exit_codes(configured_vault):
    runner = CliRunner()
    ok = runner.invoke(app, ["check"])
    assert ok.exit_code == 0 and "no problems" in ok.output
    write(configured_vault / "core" / "x.md", "# bad\n")
    bad = runner.invoke(app, ["check"])
    assert bad.exit_code == 1
    assert "core/x.md: [frontmatter]" in bad.output


def test_cli_without_vault():
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 1
    assert "liber init" in result.output


def _sam_vault(vault, link_text):
    write(vault / "people" / "Sam Chen.md", note(type="person"))
    write(vault / "sources" / "documents" / "Sam Chen.md", "raw")
    write(vault / "core" / "x.md", note(body=link_text))
    return vault


def test_bare_link_matching_two_files_is_ambiguous(vault):
    _sam_vault(vault, "Met [[Sam Chen]].")
    problems = run_checks(vault)
    assert [(p.rel, p.kind) for p in problems] == [("core/x.md", "ambiguous-link")]
    assert "people/Sam Chen" in problems[0].message


def test_path_links_are_not_ambiguous(vault):
    _sam_vault(vault, "[[people/Sam Chen]] and [[sources/documents/Sam Chen]].")
    assert run_checks(vault) == []
