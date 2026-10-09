import subprocess
from datetime import date

import pytest
from typer.testing import CliRunner

import liber.cli
from liber.cli import app
from liber.config import resolve_vault
from liber.errors import LiberError, VaultExistsError
from liber.scaffold import init_vault
from liber.vaultconfig import load_vault_config


def git(vault, *args):
    return subprocess.run(["git", *args], cwd=vault, capture_output=True, text=True, check=True).stdout


def test_layout(vault):
    for rel in [
        "AGENTS.md", "CLAUDE.md", "README.md", "inbox.md", "open-questions.md", "liber.toml", ".gitignore",
        "core/identity.md", "core/personality.md", "core/values.md", "core/preferences.md",
        "career/timeline.md", "career/skills.md", "goals/current.md", "health/overview.md", "finance/overview.md", "log/2026.md",
        "_templates/person.md", "inbox/.gitkeep", "people/.gitkeep", "interests/.gitkeep",
        "career/projects/.gitkeep", "sources/notes/.gitkeep", "sources/documents/.gitkeep",
        "sources/interviews/.gitkeep",
    ]:
        assert (vault / rel).exists(), rel
    assert not list(vault.rglob("dot-*"))
    assert not list(vault.rglob("YEAR*"))


def test_rendering(vault):
    assert "updated: 2026-09-30" in (vault / "core/identity.md").read_text()
    assert "# 2026" in (vault / "log/2026.md").read_text()
    person = (vault / "_templates/person.md").read_text()
    assert "{{date}}" in person and "{{title}}" in person
    assert "{{today}}" not in (vault / "AGENTS.md").read_text()


def test_skills_are_symlinked(vault, fake_skills):
    for name in ("ingest", "review"):
        link = vault / ".claude" / "skills" / name
        assert link.is_symlink()
        assert link.resolve() == (fake_skills / name).resolve()


def test_git_initialized_and_clean(vault):
    assert "initialize vault" in git(vault, "log", "--oneline")
    assert git(vault, "status", "--porcelain") == ""


def test_config_loads(vault):
    cfg = load_vault_config(vault)
    assert cfg.folders["career/projects"] == "project"
    assert cfg.type_for("people") == "person"
    assert cfg.type_for("health") == "health"
    assert cfg.type_for("finance") == "finance"


def test_refuses_non_empty_target(tmp_path, fake_skills):
    target = tmp_path / "v"
    target.mkdir()
    (target / "keep.txt").write_text("mine")
    with pytest.raises(VaultExistsError):
        init_vault(target, fake_skills)
    assert [p.name for p in target.iterdir()] == ["keep.txt"]


def test_accepts_existing_empty_dir(tmp_path, fake_skills):
    target = tmp_path / "v"
    target.mkdir()
    init_vault(target, fake_skills, today=date(2026, 1, 1))
    assert (target / "log/2026.md").exists()


def test_missing_skill_is_an_error(tmp_path):
    with pytest.raises(LiberError, match="ingest"):
        init_vault(tmp_path / "v", tmp_path / "no-skills")


def test_cli_init_writes_config(tmp_path, fake_skills, monkeypatch):
    monkeypatch.setattr(liber.cli, "default_skills_dir", lambda: fake_skills)
    target = tmp_path / "myvault"
    result = CliRunner().invoke(app, ["init", str(target)])
    assert result.exit_code == 0, result.output
    assert resolve_vault().resolve() == target.resolve()
    assert "/ingest" in result.output


def test_cli_init_refuses_non_empty(tmp_path, fake_skills, monkeypatch):
    monkeypatch.setattr(liber.cli, "default_skills_dir", lambda: fake_skills)
    (tmp_path / "x.txt").write_text("x")
    result = CliRunner().invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "error:" in result.output
