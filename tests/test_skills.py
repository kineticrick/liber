import re

import frontmatter
import pytest

from liber.cli import app
from liber.scaffold import SKILL_NAMES, default_skills_dir


def command_names():
    return {cmd.name for cmd in app.registered_commands}


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_has_frontmatter(name):
    post = frontmatter.load(default_skills_dir() / name / "SKILL.md")
    assert post["name"] == name
    assert len(post["description"]) > 40


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_only_mentions_real_commands(name):
    text = (default_skills_dir() / name / "SKILL.md").read_text()
    mentioned = set(re.findall(r"`liber ([a-z]+)", text))
    assert mentioned, "skill should reference liber commands"
    assert mentioned <= command_names(), mentioned - command_names()


def test_real_skills_link_into_a_vault(tmp_path):
    from liber.scaffold import init_vault

    init_vault(tmp_path / "v", default_skills_dir())
    assert (tmp_path / "v" / ".claude" / "skills" / "ingest" / "SKILL.md").is_file()
