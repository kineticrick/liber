"""Creating a new vault from the packaged templates."""

import os
import shutil
import subprocess
from datetime import date
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from liber.errors import LiberError, VaultExistsError

SKILL_NAMES = ("ingest", "review")


def template_root() -> Traversable:
    return files("liber") / "templates" / "vault"


def default_skills_dir() -> Path:
    return Path(str(files("liber") / "skills"))


def _dest_name(name: str, today: date) -> str:
    if name.startswith("dot-"):
        name = "." + name[len("dot-"):]
    return name.replace("YEAR", str(today.year))


def _render(text: str, today: date) -> str:
    return text.replace("{{today}}", today.isoformat()).replace("{{year}}", str(today.year))


def _copy_tree(src: Traversable, dest: Path, today: date) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name == "__pycache__":
            continue
        target = dest / _dest_name(item.name, today)
        if item.is_dir():
            _copy_tree(item, target, today)
        else:
            target.write_text(_render(item.read_text(encoding="utf-8"), today), encoding="utf-8")


def link_skills(vault: Path, skills_dir: Path) -> None:
    for name in SKILL_NAMES:
        if not (skills_dir / name / "SKILL.md").is_file():
            raise LiberError(f"skill '{name}' not found at {skills_dir / name}")
    dest = vault / ".claude" / "skills"
    dest.mkdir(parents=True, exist_ok=True)
    for name in SKILL_NAMES:
        (dest / name).symlink_to((skills_dir / name).resolve(), target_is_directory=True)


def _git(vault: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=vault, capture_output=True, text=True, env=env)


def _git_init(vault: Path) -> None:
    if shutil.which("git") is None:
        raise LiberError("git is required but was not found on PATH")
    env = dict(os.environ)
    for step in (("init", "-q"), ("add", "-A")):
        result = _git(vault, *step)
        if result.returncode != 0:
            raise LiberError(f"git {step[0]} failed: {result.stderr.strip()}")
    if _git(vault, "config", "user.email").returncode != 0:
        env.update(
            GIT_AUTHOR_NAME="liber", GIT_AUTHOR_EMAIL="liber@localhost",
            GIT_COMMITTER_NAME="liber", GIT_COMMITTER_EMAIL="liber@localhost",
        )
    result = _git(vault, "commit", "-q", "-m", "liber: initialize vault", env=env)
    if result.returncode != 0:
        raise LiberError(f"git commit failed: {result.stderr.strip()}")


def init_vault(target: Path, skills_dir: Path, today: date | None = None) -> None:
    """Create a new vault at `target` (which must not exist or be an empty dir)."""
    today = today or date.today()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise VaultExistsError(f"{target} already exists and is not empty; liber init only creates new vaults.")
    for name in SKILL_NAMES:
        if not (skills_dir / name / "SKILL.md").is_file():
            raise LiberError(f"skill '{name}' not found at {skills_dir / name}")
    _copy_tree(template_root(), target, today)
    link_skills(target, skills_dir)
    _git_init(target)
