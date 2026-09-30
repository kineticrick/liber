import pytest

from helpers import write
from liber.errors import LiberError, VaultNotFoundError
from liber.vaultconfig import VaultConfig, load_vault_config, sensitivity_rank

TOML = """
schema_version = 1

[limits]
agents_md_max_tokens = 1500

[sync]
conflict_patterns = ["*conflicted copy*"]

[folders]
core = { type = "core" }
career = { type = "career" }
"career/projects" = { type = "project" }
"""


def test_load(tmp_path):
    write(tmp_path / "liber.toml", TOML)
    cfg = load_vault_config(tmp_path)
    assert cfg.schema_version == 1
    assert cfg.agents_md_max_tokens == 1500
    assert cfg.conflict_patterns == ("*conflicted copy*",)
    assert cfg.folders == {"core": "core", "career": "career", "career/projects": "project"}
    assert cfg.top_level_folders == ["career", "core"]


def test_defaults_when_sections_missing(tmp_path):
    write(tmp_path / "liber.toml", "schema_version = 1\n")
    cfg = load_vault_config(tmp_path)
    assert cfg.agents_md_max_tokens == 2000
    assert cfg.conflict_patterns == ("*conflicted copy*", "*.sync-conflict-*")
    assert cfg.folders == {}


def test_type_for_uses_longest_matching_folder():
    cfg = VaultConfig(1, 2000, (), {"career": "career", "career/projects": "project", "core": "core"})
    assert cfg.type_for("career") == "career"
    assert cfg.type_for("career/projects") == "project"
    assert cfg.type_for("career/projects/deep") == "project"
    assert cfg.type_for("careers") is None
    assert cfg.type_for("people") is None


def test_missing_file(tmp_path):
    with pytest.raises(VaultNotFoundError):
        load_vault_config(tmp_path)


def test_invalid_toml(tmp_path):
    write(tmp_path / "liber.toml", "schema_version = [\n")
    with pytest.raises(LiberError, match="not valid TOML"):
        load_vault_config(tmp_path)


def test_folder_without_type(tmp_path):
    write(tmp_path / "liber.toml", "[folders]\ncore = {}\n")
    with pytest.raises(LiberError, match="core"):
        load_vault_config(tmp_path)


def test_sensitivity_rank():
    assert sensitivity_rank("public") == 0
    assert sensitivity_rank("personal") == 1
    assert sensitivity_rank("private") == 2
    assert sensitivity_rank("secret") is None
    assert sensitivity_rank(None) is None
