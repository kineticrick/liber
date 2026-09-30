# liber Foundation, Inbox & Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `liber` CLI (vault scaffolding, capture, extraction, archiving, validation, bundling) and the Claude Code `/ingest` and `/review` skills, so the user can capture notes/documents and turn them into an approved, sourced, git-versioned knowledge base about themselves.

**Architecture:** A small Python package (`src/liber/`) with one module per responsibility, plus a thin Typer CLI that only parses arguments, calls those modules, and prints results. All judgment lives in two Markdown skills (`src/liber/skills/*/SKILL.md`) that `liber init` symlinks into the user's vault. The vault's layout is read from its `liber.toml`, never hard-coded, so structure can evolve without code changes.

**Tech Stack:** Python 3.12 (pinned via `.python-version`), uv, Typer, python-frontmatter, markitdown[pdf,docx], pytest, python-docx (tests only), git.

**Spec:** `docs/superpowers/specs/2026-09-30-liber-foundation-design.md`

## Global Constraints

- Python `>=3.12`; `.python-version` pins `3.12` (markitdown's dependency chain may lag newer Pythons).
- Runtime dependencies: only `typer>=0.12`, `python-frontmatter>=1.1`, `markitdown[pdf,docx]>=0.1.2`. Dev: `pytest>=8`, `python-docx>=1.1`.
- Run tests with `uv run pytest` from the repo root.
- Sensitivity levels, in order: `public` < `personal` < `private`. Default bundle ceiling: `personal`.
- Required frontmatter on content files: `type`, `updated` (ISO date), `sensitivity`; `tags` optional but must be a list if present.
- Root system files (exempt from `type`): `AGENTS.md`, `CLAUDE.md`, `README.md`, `inbox.md`, `open-questions.md`. `AGENTS.md` must be `sensitivity: public`.
- Folders exempt from content checks: `sources/`, `inbox/`, and any path part starting with `.` or `_`.
- Nothing is ever overwritten: `add` and `archive` pick a free name with a `-2`, `-3`, … suffix.
- Extracted text sidecar for `inbox/<name>` is `inbox/<name>.md`; a sidecar containing `<!-- liber: no text found -->` means "no text found".
- `AGENTS.md` size estimate: characters ÷ 4 compared against `[limits] agents_md_max_tokens` (default 2000).
- Sync-conflict patterns come from `liber.toml` `[sync] conflict_patterns` (default `["*conflicted copy*", "*.sync-conflict-*"]`), matched case-insensitively against file names.
- User config: `$XDG_CONFIG_HOME/liber/config.toml` (default `~/.config/liber/config.toml`) with `vault = "<abs path>"`; env `LIBER_VAULT` overrides it.
- Every CLI failure prints `error: <message>` and exits 1; errors about a missing vault mention `liber init`.
- Tests must never touch the real user config, home dir, or global git config (autouse fixture in `tests/conftest.py`).
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV
  ```

## Review Focus

1. **A note typed on the phone while `/ingest` is running** must not be archived unreviewed — `liber archive --notes --count N` archives only the first N notes; the rest stay in `inbox.md` (test in Task 7).
2. **A file with missing or misspelled `sensitivity`** must never leak into a bundle — it is excluded and reported (test in Task 8).
3. **One corrupt or unsupported document** must not abort `liber extract` for the others — it is reported as failed and stays pending (test in Task 6).
4. **A legitimate note whose name contains "conflict"** (e.g. `interests/conflict-resolution.md`) must not be flagged as a sync conflict (test in Task 4).
5. **`liber archive` given a path like `../AGENTS.md` or `sub/x.pdf`** must refuse rather than move files outside `inbox/` (test in Task 7).

## File Structure

```
pyproject.toml, .python-version, .gitignore, README.md
src/liber/
  __init__.py          version
  errors.py            LiberError, VaultNotFoundError, VaultExistsError
  config.py            user config: user_config_path, resolve_vault, write_user_config
  vaultconfig.py       liber.toml: VaultConfig, load_vault_config, SENSITIVITY_LEVELS, SYSTEM_FILES, EXEMPT_DIRS
  docs.py              reading vault Markdown: VaultFile, read_vault_file, iter_content_files, is_hidden_part
  paths.py             free_name (collision-free file names)
  scaffold.py          init_vault, link_skills, default_skills_dir
  check.py             Problem, run_checks, find_conflicts
  inbox.py             add_note, add_files, pending_notes, inbox_documents, inbox_status, sidecar helpers
  extract.py           extract_pending, markitdown_convert
  archive.py           archive_document, archive_notes
  bundle.py            Bundle, build_bundle
  clipboard.py         copy_to_clipboard
  cli.py               Typer app (thin)
  templates/vault/...  files copied by `liber init` (dot-X → .X, YEAR → current year, {{today}}/{{year}} rendered)
  skills/ingest/SKILL.md, skills/review/SKILL.md
tests/
  conftest.py, helpers.py, test_*.py
docs/manual-test/      CHECKLIST.md + sample inbox files for the skills
```

---

### Task 1: Project skeleton, errors, user config, CLI shell

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`
- Create: `src/liber/__init__.py`, `src/liber/errors.py`, `src/liber/config.py`, `src/liber/cli.py`
- Create: `tests/conftest.py`, `tests/helpers.py`, `tests/test_config.py`, `tests/test_cli_shell.py`

**Interfaces:**
- Produces:
  - `liber.errors.LiberError(Exception)`, `VaultNotFoundError(LiberError)`, `VaultExistsError(LiberError)`
  - `liber.config.VAULT_MARKER = "liber.toml"`
  - `liber.config.user_config_path() -> Path`
  - `liber.config.resolve_vault() -> Path`
  - `liber.config.write_user_config(vault: Path) -> Path`
  - `liber.cli.app: typer.Typer`; `liber.cli.handle_errors()` context manager (LiberError → `error: …`, exit 1)
  - `tests/helpers.py`: `write(path: Path, text: str) -> Path`
  - `tests/conftest.py`: autouse fixture `isolated_env` returning the fake home `Path`

- [ ] **Step 1: Create project files**

`pyproject.toml`:
```toml
[project]
name = "liber"
version = "0.1.0"
description = "A personal knowledge base about you, readable by any LLM or agent."
requires-python = ">=3.12"
dependencies = [
    "typer>=0.12",
    "python-frontmatter>=1.1",
    "markitdown[pdf,docx]>=0.1.2",
]

[project.scripts]
liber = "liber.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/liber"]

[dependency-groups]
dev = ["pytest>=8", "python-docx>=1.1"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.pytest_cache/
dist/
build/
*.egg-info/
```

`src/liber/__init__.py`:
```python
"""liber: a personal knowledge base about you, readable by any LLM or agent."""

__version__ = "0.1.0"
```

`src/liber/errors.py`:
```python
class LiberError(Exception):
    """An expected failure. The CLI prints the message and exits 1."""


class VaultNotFoundError(LiberError):
    """No usable vault is configured."""


class VaultExistsError(LiberError):
    """`liber init` target already has content."""
```

Run: `uv sync`
Expected: creates `.venv`, installs dependencies without error. If markitdown fails to install, confirm `uv run python --version` reports 3.12.x.

- [ ] **Step 2: Write test helpers and the isolation fixture**

`tests/helpers.py`:
```python
from pathlib import Path


def write(path: Path, text: str) -> Path:
    """Create parent dirs and write UTF-8 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
```

`tests/conftest.py`:
```python
import pytest


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path_factory):
    """Keep every test away from the real home dir, user config, and git config."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("LIBER_VAULT", raising=False)
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    return home
```

- [ ] **Step 3: Write failing tests for config**

`tests/test_config.py`:
```python
import pytest

from helpers import write
from liber.config import resolve_vault, user_config_path, write_user_config
from liber.errors import VaultNotFoundError


def make_marker_vault(path):
    write(path / "liber.toml", "schema_version = 1\n")
    return path


def test_user_config_path_uses_xdg(isolated_env):
    assert user_config_path() == isolated_env / ".config" / "liber" / "config.toml"


def test_resolve_without_config_points_to_init():
    with pytest.raises(VaultNotFoundError, match="liber init"):
        resolve_vault()


def test_write_then_resolve(tmp_path):
    vault = make_marker_vault(tmp_path / "vault")
    write_user_config(vault)
    assert resolve_vault().resolve() == vault.resolve()


def test_env_overrides_config(tmp_path, monkeypatch):
    configured = make_marker_vault(tmp_path / "a")
    override = make_marker_vault(tmp_path / "b")
    write_user_config(configured)
    monkeypatch.setenv("LIBER_VAULT", str(override))
    assert resolve_vault().resolve() == override.resolve()


def test_configured_dir_without_marker_is_rejected(tmp_path):
    write_user_config(tmp_path)
    with pytest.raises(VaultNotFoundError, match="not a liber vault"):
        resolve_vault()


def test_config_missing_vault_key(isolated_env):
    write(user_config_path(), "other = 1\n")
    with pytest.raises(VaultNotFoundError, match="liber init"):
        resolve_vault()


def test_odd_characters_in_path_roundtrip(tmp_path):
    vault = make_marker_vault(tmp_path / 'we"ird \\ dïr')
    write_user_config(vault)
    assert resolve_vault().resolve() == vault.resolve()
```

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'liber.config'`.

- [ ] **Step 4: Implement config**

`src/liber/config.py`:
```python
"""User-level config: where the vault lives."""

import json
import os
import tomllib
from pathlib import Path

from liber.errors import VaultNotFoundError

VAULT_MARKER = "liber.toml"
_INIT_HINT = "Run `liber init <path>` to create a vault."


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "liber" / "config.toml"


def resolve_vault() -> Path:
    """Return the vault path from $LIBER_VAULT or the user config, or raise."""
    env = os.environ.get("LIBER_VAULT")
    if env:
        vault, source = Path(env).expanduser(), "LIBER_VAULT"
    else:
        cfg = user_config_path()
        if not cfg.is_file():
            raise VaultNotFoundError(f"No liber vault configured. {_INIT_HINT}")
        try:
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise VaultNotFoundError(f"{cfg} is not valid TOML ({exc}). {_INIT_HINT}") from exc
        if "vault" not in data:
            raise VaultNotFoundError(f"{cfg} has no `vault` entry. {_INIT_HINT}")
        vault, source = Path(data["vault"]).expanduser(), str(cfg)
    if not (vault / VAULT_MARKER).is_file():
        raise VaultNotFoundError(
            f"{vault} (from {source}) is not a liber vault (no {VAULT_MARKER}). {_INIT_HINT}"
        )
    return vault


def write_user_config(vault: Path) -> Path:
    """Point the user config at `vault`. JSON string escaping is valid TOML."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"vault = {json.dumps(str(vault.resolve()))}\n", encoding="utf-8")
    return path
```

Run: `uv run pytest tests/test_config.py -v`
Expected: 7 passed.

- [ ] **Step 5: Write failing CLI shell test**

`tests/test_cli_shell.py`:
```python
from typer.testing import CliRunner

from liber.cli import app, handle_errors
from liber.errors import LiberError

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "liber" in result.output


def test_handle_errors_converts_liber_error(capsys):
    import typer

    try:
        with handle_errors():
            raise LiberError("boom")
    except typer.Exit as exc:
        assert exc.exit_code == 1
    assert "error: boom" in capsys.readouterr().err
```

Run: `uv run pytest tests/test_cli_shell.py -v`
Expected: FAIL — `No module named 'liber.cli'`.

- [ ] **Step 6: Implement the CLI shell**

`src/liber/cli.py`:
```python
"""Thin command-line layer: parse args, call modules, print results."""

from contextlib import contextmanager

import typer

from liber.errors import LiberError

app = typer.Typer(
    no_args_is_help=True,
    help="liber: a personal knowledge base about you, readable by any LLM or agent.",
)


@app.callback()
def _root() -> None:
    """liber: a personal knowledge base about you."""


@contextmanager
def handle_errors():
    try:
        yield
    except LiberError as exc:
        typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from exc
```

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .python-version .gitignore uv.lock src tests
git commit -m "feat: project skeleton, user config, CLI shell

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 2: Vault config (`liber.toml`) and Markdown file reading

**Files:**
- Create: `src/liber/vaultconfig.py`, `src/liber/docs.py`
- Test: `tests/test_vaultconfig.py`, `tests/test_docs.py`

**Interfaces:**
- Consumes: `liber.errors.LiberError`, `VaultNotFoundError`
- Produces:
  - `liber.vaultconfig.SENSITIVITY_LEVELS: tuple[str, ...] = ("public", "personal", "private")`
  - `liber.vaultconfig.SYSTEM_FILES: frozenset[str]`, `EXEMPT_DIRS: frozenset[str] = {"sources", "inbox"}`
  - `liber.vaultconfig.DEFAULT_CONFLICT_PATTERNS: tuple[str, ...]`
  - `@dataclass(frozen=True) VaultConfig(schema_version: int, agents_md_max_tokens: int, conflict_patterns: tuple[str, ...], folders: dict[str, str])` with `.type_for(rel_dir: str) -> str | None` and `.top_level_folders -> list[str]`
  - `liber.vaultconfig.load_vault_config(vault: Path) -> VaultConfig`
  - `liber.vaultconfig.sensitivity_rank(level: object) -> int | None`
  - `@dataclass(frozen=True) liber.docs.VaultFile(path: Path, rel: str, text: str, meta: dict, has_frontmatter: bool, parse_error: str | None)`
  - `liber.docs.read_vault_file(vault: Path, path: Path) -> VaultFile`
  - `liber.docs.is_hidden_part(part: str) -> bool` (starts with `.` or `_`)
  - `liber.docs.iter_content_files(vault: Path) -> list[VaultFile]` (sorted by `rel`; excludes root files, `EXEMPT_DIRS`, hidden parts)

- [ ] **Step 1: Write failing tests for vault config**

`tests/test_vaultconfig.py`:
```python
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
```

Run: `uv run pytest tests/test_vaultconfig.py -v`
Expected: FAIL — `No module named 'liber.vaultconfig'`.

- [ ] **Step 2: Implement vault config**

`src/liber/vaultconfig.py`:
```python
"""The vault's own config file, liber.toml: structure, limits, sync settings."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from liber.errors import LiberError, VaultNotFoundError

SENSITIVITY_LEVELS: tuple[str, ...] = ("public", "personal", "private")
SYSTEM_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "README.md", "inbox.md", "open-questions.md"})
EXEMPT_DIRS = frozenset({"sources", "inbox"})
DEFAULT_CONFLICT_PATTERNS: tuple[str, ...] = ("*conflicted copy*", "*.sync-conflict-*")


def sensitivity_rank(level: object) -> int | None:
    return SENSITIVITY_LEVELS.index(level) if level in SENSITIVITY_LEVELS else None


@dataclass(frozen=True)
class VaultConfig:
    schema_version: int
    agents_md_max_tokens: int
    conflict_patterns: tuple[str, ...]
    folders: dict[str, str]

    def type_for(self, rel_dir: str) -> str | None:
        """The `type` required for files in `rel_dir` (longest configured ancestor wins)."""
        best = None
        for key in self.folders:
            if rel_dir == key or rel_dir.startswith(key + "/"):
                if best is None or len(key) > len(best):
                    best = key
        return self.folders[best] if best is not None else None

    @property
    def top_level_folders(self) -> list[str]:
        return sorted({key.split("/")[0] for key in self.folders})


def load_vault_config(vault: Path) -> VaultConfig:
    path = vault / "liber.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VaultNotFoundError(f"{vault} has no liber.toml. Run `liber init <path>` to create a vault.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{path} is not valid TOML: {exc}") from exc

    folders: dict[str, str] = {}
    for key, value in data.get("folders", {}).items():
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise LiberError(f"liber.toml: folder '{key}' needs a `type`, e.g. {key} = {{ type = \"{key}\" }}")
        folders[key.strip("/")] = value["type"]

    return VaultConfig(
        schema_version=int(data.get("schema_version", 1)),
        agents_md_max_tokens=int(data.get("limits", {}).get("agents_md_max_tokens", 2000)),
        conflict_patterns=tuple(data.get("sync", {}).get("conflict_patterns", DEFAULT_CONFLICT_PATTERNS)),
        folders=folders,
    )
```

Run: `uv run pytest tests/test_vaultconfig.py -v`
Expected: 7 passed.

- [ ] **Step 3: Write failing tests for docs**

`tests/test_docs.py`:
```python
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
```

Run: `uv run pytest tests/test_docs.py -v`
Expected: FAIL — `No module named 'liber.docs'`.

- [ ] **Step 4: Implement docs**

`src/liber/docs.py`:
```python
"""Reading Markdown files in a vault."""

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from liber.vaultconfig import EXEMPT_DIRS

_FRONTMATTER_START = re.compile(r"\A﻿?---[ \t]*\r?\n")


@dataclass(frozen=True)
class VaultFile:
    path: Path
    rel: str
    text: str
    meta: dict
    has_frontmatter: bool
    parse_error: str | None


def is_hidden_part(part: str) -> bool:
    return part.startswith(".") or part.startswith("_")


def read_vault_file(vault: Path, path: Path) -> VaultFile:
    text = path.read_text(encoding="utf-8")
    has_fm = bool(_FRONTMATTER_START.match(text))
    meta: dict = {}
    error = None
    if has_fm:
        try:
            meta = dict(frontmatter.loads(text).metadata)
        except Exception as exc:  # YAML errors come in several types
            error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return VaultFile(path, path.relative_to(vault).as_posix(), text, meta, has_fm, error)


def iter_content_files(vault: Path) -> list[VaultFile]:
    """Markdown files in content folders: not at the root, not exempt, not hidden."""
    files = []
    for path in vault.rglob("*.md"):
        parts = path.relative_to(vault).parts
        if len(parts) == 1 or parts[0] in EXEMPT_DIRS:
            continue
        if any(is_hidden_part(p) for p in parts[:-1]):
            continue
        files.append(read_vault_file(vault, path))
    return sorted(files, key=lambda vf: vf.rel)
```

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/liber/vaultconfig.py src/liber/docs.py tests/test_vaultconfig.py tests/test_docs.py
git commit -m "feat: liber.toml loader and vault Markdown reader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 3: Vault templates, `init_vault`, and `liber init`

**Files:**
- Create: everything under `src/liber/templates/vault/` (listed in Step 1)
- Create: `src/liber/scaffold.py`
- Modify: `src/liber/cli.py` (add `init` command)
- Modify: `tests/conftest.py` (add `fake_skills`, `vault`, `configured_vault` fixtures)
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `LiberError`, `VaultExistsError`, `write_user_config`, `load_vault_config`
- Produces:
  - `liber.scaffold.SKILL_NAMES = ("ingest", "review")`
  - `liber.scaffold.template_root() -> importlib.resources.abc.Traversable` (the `templates/vault` dir)
  - `liber.scaffold.default_skills_dir() -> Path`
  - `liber.scaffold.init_vault(target: Path, skills_dir: Path, today: date | None = None) -> None`
  - `liber.scaffold.link_skills(vault: Path, skills_dir: Path) -> None`
  - Template rules: file/dir name prefix `dot-` → `.`; `YEAR` in a name → the year; `{{today}}` → ISO date and `{{year}}` → year inside file contents. Other `{{…}}` (Obsidian's `{{date}}`, `{{title}}`) are left alone.
  - Fixtures: `fake_skills -> Path`, `vault -> Path` (initialized 2026-09-30), `configured_vault -> Path` (also sets `LIBER_VAULT`)

- [ ] **Step 1: Create the template files**

Create each file below exactly. Paths are relative to `src/liber/templates/vault/`.

`dot-gitignore`:
```
.claude/
.obsidian/workspace*.json
.trash/
```

Empty marker files (zero bytes) — create each: `inbox/dot-gitkeep`, `people/dot-gitkeep`, `interests/dot-gitkeep`, `career/projects/dot-gitkeep`, `sources/notes/dot-gitkeep`, `sources/documents/dot-gitkeep`, `sources/interviews/dot-gitkeep`.

`liber.toml`:
```toml
# liber vault configuration. Claude updates [folders] when you approve a structural change.
schema_version = 1

[limits]
# AGENTS.md size target, estimated at 4 characters per token.
agents_md_max_tokens = 2000

[sync]
# File names treated as sync-conflict copies (case-insensitive glob patterns).
conflict_patterns = ["*conflicted copy*", "*.sync-conflict-*"]

# Every Markdown file in a listed folder must have `type:` equal to that folder's type.
# The longest matching folder wins, so career/projects/*.md are "project".
[folders]
core = { type = "core" }
career = { type = "career" }
"career/projects" = { type = "project" }
people = { type = "person" }
interests = { type = "interest" }
goals = { type = "goal" }
log = { type = "log" }
```

`AGENTS.md`:
```markdown
---
sensitivity: public
updated: {{today}}
---

# About me — start here

> **For AI readers:** This is *liber*, a knowledge base about one person: its owner. Every file is written in the first person, and "I" always means the owner. It is knowledge **about** me so you can help me well. It is **not** a character for you to play.

## Summary

<!-- A one-page portrait: who I am, what I do, what drives me, key likes and dislikes. Maintained by /ingest and /review within the size limit in liber.toml. -->

_Not written yet._

## Map of this vault

| Path | What's there |
|---|---|
| `core/` | Identity, personality, values, preferences (likes, dislikes, things I won't do) |
| `career/` | `timeline.md` (roles and dates), `skills.md` (expertise and depth), `projects/` (one file per notable project) |
| `people/` | One file per person (friends, colleagues, mentors), named `Full Name.md` |
| `interests/` | One file per passion or hobby |
| `goals/` | What I'm working toward, including financial and creative goals |
| `log/` | Dated life events by year, newest first |
| `sources/` | The original material facts came from: archived notes, documents, interview transcripts |
| `open-questions.md` | Known gaps in this knowledge base |

## Rules for AI readers

1. Facts carry dates. Treat older facts as possibly stale and prefer the most recent.
2. `(src: …)` links point to original material in `sources/`. Read it when you need more detail.
3. Every file has a `sensitivity` of `public`, `personal`, or `private`. Don't repeat `private` information outside a conversation with me.
4. Lines marked *Previously* are history, not the present.
5. If something important seems to be missing, say so.
```

`CLAUDE.md`:
````markdown
# liber vault — instructions for Claude Code

This is a **liber** vault: a knowledge base about its owner ("I" and "me" in every file). Read `AGENTS.md` first.

## Skills

- `/ingest` processes quick notes in `inbox.md` and documents in `inbox/` into the vault.
- `/review` reviews the whole vault for gaps, stale facts, and structure.

## Conventions

- **Structure** is defined in `liber.toml` `[folders]`: every Markdown file in a folder must have that folder's `type`. Change structure only with the owner's approval. When you do, update `liber.toml`, this file, and the map in `AGENTS.md` together, then run `liber check`.
- **Frontmatter** on every file in a content folder:
  ```yaml
  ---
  type: <the folder's type from liber.toml>
  updated: YYYY-MM-DD
  sensitivity: public | personal | private
  tags: []
  ---
  ```
  Sensitivity is per file. Put more sensitive material in its own file. New files default to `personal`; confirm with the owner.
- **First person**, in the owner's voice.
- **Facts carry dates and sources**, for example `- Led the payments migration at Acme (2019–2021). (src: [[thesis.pdf]])`.
- **Never delete facts silently.** Superseded facts become history, for example `- *Previously* lived in Denver (until 2024).`
- **People** are linked by name, for example `[[Sam Chen]]`, and each has `people/Sam Chen.md` based on `_templates/person.md`. Keep notes about others factual and kind.
- **Log** entries go in `log/<YYYY>.md`, newest first, for example `- 2026-09-30 — Started liber.`
- **Root files** are only `AGENTS.md`, `CLAUDE.md`, `README.md`, `inbox.md`, `open-questions.md`, and `liber.toml`. Don't add other notes at the root.
- **`sources/`** holds original material. Never edit it.
- Run `liber check` after editing. It must pass before you commit.

## Commands

`liber status`, `liber extract`, `liber archive <name>`, `liber archive --notes --count <N>`, `liber check`, `liber bundle`.
````

`README.md`:
```markdown
# My liber vault

This vault is a knowledge base about me, kept in plain Markdown so any AI tool can use it.

## Adding things

- **Quick thought:** add a line to `inbox.md`, from Obsidian on any device or with `liber note "..."` on the desktop.
- **Whole document** (a paper, a resume, an export): drop it into `inbox/`.

## Processing the inbox

On the desktop, open Claude Code in this folder and run `/ingest`. Claude proposes changes, I approve them, and each processed item becomes one git commit. Run `/review` every so often for a whole-vault check-up.

## Using it

- `liber bundle` prints everything up to `personal` sensitivity as one document to paste into any chatbot.
- `AGENTS.md` is the entry point for AI tools.

## Where things go

See the map in `AGENTS.md`. The structure is defined in `liber.toml` and can grow over time.
```

`inbox.md`:
```markdown
# Inbox

<!-- Quick notes: one thought per line, any format. /ingest files them into the vault and clears this list. -->
```

`open-questions.md`:
```markdown
# Open questions

<!-- Gaps noticed by /ingest and /review. Answer any of them in inbox.md. Remove a question once it's answered. -->
```

`_templates/person.md`:
```markdown
---
type: person
updated: {{date}}
sensitivity: personal
tags: []
relationship:
met:
last_contact:
---

# {{title}}

## Who they are

## How we know each other

## Notes
```

`core/identity.md`:
```markdown
---
type: core
updated: {{today}}
sensitivity: personal
tags: []
---

# Identity

<!-- The basics: name, where I live, family, background. -->
```

`core/personality.md`:
```markdown
---
type: core
updated: {{today}}
sensitivity: personal
tags: []
---

# Personality

<!-- How I think, what energizes and drains me, how I communicate. -->
```

`core/values.md`:
```markdown
---
type: core
updated: {{today}}
sensitivity: personal
tags: []
---

# Values

<!-- What matters most to me, and why. -->
```

`core/preferences.md`:
```markdown
---
type: core
updated: {{today}}
sensitivity: personal
tags: []
---

# Preferences

<!-- Likes, dislikes, and things I won't do. -->

## Likes

## Dislikes

## Things I won't do
```

`career/timeline.md`:
```markdown
---
type: career
updated: {{today}}
sensitivity: personal
tags: []
---

# Career timeline

<!-- Roles newest first: dates, organization, title, what I actually did, what I was proud of. -->
```

`career/skills.md`:
```markdown
---
type: career
updated: {{today}}
sensitivity: personal
tags: []
---

# Skills

<!-- Expertise with an honest depth rating (learning / working / deep) and where it came from. -->
```

`goals/current.md`:
```markdown
---
type: goal
updated: {{today}}
sensitivity: personal
tags: []
---

# Current goals

<!-- What I'm working toward: career, financial, creative, personal. -->
```

`log/YEAR.md`:
```markdown
---
type: log
updated: {{today}}
sensitivity: personal
tags: []
---

# {{year}}

<!-- Dated events, newest first: "- 2026-09-30 — Started liber." -->
```

- [ ] **Step 2: Add vault fixtures to conftest**

Append to `tests/conftest.py`:
```python
from datetime import date


@pytest.fixture
def fake_skills(tmp_path):
    root = tmp_path / "skills"
    for name in ("ingest", "review"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")
    return root


@pytest.fixture
def vault(tmp_path, fake_skills):
    from liber.scaffold import init_vault

    path = tmp_path / "vault"
    init_vault(path, fake_skills, today=date(2026, 9, 30))
    return path


@pytest.fixture
def configured_vault(vault, monkeypatch):
    monkeypatch.setenv("LIBER_VAULT", str(vault))
    return vault
```

- [ ] **Step 3: Write failing scaffold tests**

`tests/test_scaffold.py`:
```python
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
        "career/timeline.md", "career/skills.md", "goals/current.md", "log/2026.md",
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
```

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: FAIL — `No module named 'liber.scaffold'`.

- [ ] **Step 4: Implement scaffold**

`src/liber/scaffold.py`:
```python
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
```

- [ ] **Step 5: Add the `init` command**

In `src/liber/cli.py`, add imports at the top (below `import typer`):
```python
from pathlib import Path
from typing import Annotated

from liber.config import write_user_config
from liber.scaffold import default_skills_dir, init_vault
```
and append:
```python
@app.command("init")
def init_cmd(path: Annotated[Path, typer.Argument(help="Where to create the vault, e.g. ~/liber-vault")]) -> None:
    """Create a new vault and make it the default."""
    with handle_errors():
        target = path.expanduser().resolve()
        init_vault(target, default_skills_dir())
        config = write_user_config(target)
    typer.echo(f"Created liber vault at {target}")
    typer.echo(f"Default vault set in {config}")
    typer.echo("Next: open it in Obsidian, add notes to inbox.md, then run /ingest in Claude Code there.")
```

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/liber/templates src/liber/scaffold.py src/liber/cli.py tests/conftest.py tests/test_scaffold.py
git commit -m "feat: vault templates and liber init

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 4: `liber check`

**Files:**
- Create: `src/liber/check.py`
- Modify: `src/liber/cli.py` (add `check` command)
- Test: `tests/test_check.py`

**Interfaces:**
- Consumes: `load_vault_config`, `VaultConfig`, `SENSITIVITY_LEVELS`, `SYSTEM_FILES`, `iter_content_files`, `read_vault_file`, `is_hidden_part`, `resolve_vault`; fixtures `vault`, `configured_vault`
- Produces:
  - `@dataclass(frozen=True) liber.check.Problem(rel: str, kind: str, message: str)`; `str(problem)` → `"<rel>: [<kind>] <message>"`
  - Problem kinds: `frontmatter`, `unknown-folder`, `unknown-file`, `broken-link`, `sync-conflict`, `size`
  - `liber.check.find_conflicts(vault: Path, patterns: tuple[str, ...]) -> list[str]` (sorted vault-relative posix paths)
  - `liber.check.run_checks(vault: Path) -> list[Problem]`

- [ ] **Step 1: Write failing tests**

`tests/test_check.py`:
```python
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
    write(vault / "health" / "sleep.md", note(type="health"))
    assert kinds(vault) == [("health/sleep.md", "unknown-folder")]


def test_new_folder_accepted_after_liber_toml_update(vault):
    toml = vault / "liber.toml"
    toml.write_text(toml.read_text() + 'health = { type = "health" }\n')
    write(vault / "health" / "sleep.md", note(type="health"))
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
```

Run: `uv run pytest tests/test_check.py -v`
Expected: FAIL — `No module named 'liber.check'`.

- [ ] **Step 2: Implement check**

`src/liber/check.py`:
```python
"""Vault health checks."""

import fnmatch
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from liber.docs import VaultFile, iter_content_files, read_vault_file
from liber.vaultconfig import SENSITIVITY_LEVELS, SYSTEM_FILES, VaultConfig, load_vault_config

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]*`")
_LINK_SCAN_ROOT_FILES = ("AGENTS.md", "CLAUDE.md", "README.md", "open-questions.md")


@dataclass(frozen=True)
class Problem:
    rel: str
    kind: str
    message: str

    def __str__(self) -> str:
        return f"{self.rel}: [{self.kind}] {self.message}"


def _is_date(value: object) -> bool:
    if isinstance(value, date):
        return True
    if isinstance(value, str):
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
    return False


def _frontmatter_problems(vf: VaultFile, expected_type: str | None) -> list[Problem]:
    if vf.parse_error:
        return [Problem(vf.rel, "frontmatter", f"unreadable frontmatter: {vf.parse_error}")]
    if not vf.has_frontmatter:
        return [Problem(vf.rel, "frontmatter", "missing frontmatter block")]
    problems = []
    meta = vf.meta
    if expected_type is not None and meta.get("type") != expected_type:
        problems.append(Problem(vf.rel, "frontmatter", f"type is {meta.get('type')!r}; this folder requires {expected_type!r}"))
    if not _is_date(meta.get("updated")):
        problems.append(Problem(vf.rel, "frontmatter", f"updated must be a YYYY-MM-DD date, got {meta.get('updated')!r}"))
    if meta.get("sensitivity") not in SENSITIVITY_LEVELS:
        problems.append(Problem(vf.rel, "frontmatter", f"sensitivity must be one of {', '.join(SENSITIVITY_LEVELS)}, got {meta.get('sensitivity')!r}"))
    if "tags" in meta and not isinstance(meta["tags"], list):
        problems.append(Problem(vf.rel, "frontmatter", "tags must be a list, e.g. tags: [career]"))
    return problems


def _content_problems(vault: Path, cfg: VaultConfig) -> list[Problem]:
    problems = []
    for vf in iter_content_files(vault):
        rel_dir = vf.rel.rsplit("/", 1)[0]
        expected = cfg.type_for(rel_dir)
        if expected is None:
            problems.append(Problem(vf.rel, "unknown-folder", f"folder '{rel_dir}' is not listed in liber.toml [folders]"))
            continue
        problems.extend(_frontmatter_problems(vf, expected))
    return problems


def _root_problems(vault: Path) -> list[Problem]:
    problems = []
    for path in sorted(vault.glob("*.md")):
        if path.name not in SYSTEM_FILES:
            problems.append(Problem(path.name, "unknown-file", "notes don't belong at the root; move it into a folder or into inbox.md"))
    agents = vault / "AGENTS.md"
    if not agents.is_file():
        problems.append(Problem("AGENTS.md", "unknown-file", "AGENTS.md is missing"))
    else:
        vf = read_vault_file(vault, agents)
        if vf.parse_error or vf.meta.get("sensitivity") != "public":
            problems.append(Problem("AGENTS.md", "frontmatter", "AGENTS.md must have sensitivity: public"))
    return problems


def _walk_files(vault: Path):
    for root, dirs, names in os.walk(vault):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            yield Path(root) / name


def _link_targets(vault: Path) -> set[str]:
    names: set[str] = set()
    for path in _walk_files(vault):
        rel = path.relative_to(vault).as_posix()
        names.update({path.name, rel})
        if path.suffix == ".md":
            names.update({path.stem, rel[:-3]})
    return names


def _link_problems(vault: Path) -> list[Problem]:
    targets = _link_targets(vault)
    files = [read_vault_file(vault, vault / n) for n in _LINK_SCAN_ROOT_FILES if (vault / n).is_file()]
    files += iter_content_files(vault)
    problems = []
    for vf in files:
        text = _INLINE.sub("", _FENCED.sub("", vf.text))
        for match in _WIKILINK.finditer(text):
            target = match.group(1).strip()
            if target not in targets and target.removesuffix(".md") not in targets:
                problems.append(Problem(vf.rel, "broken-link", f"[[{target}]] doesn't match any file in the vault"))
    return problems


def find_conflicts(vault: Path, patterns: tuple[str, ...]) -> list[str]:
    lowered = [p.lower() for p in patterns]
    found = []
    for path in _walk_files(vault):
        name = path.name.lower()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in lowered):
            found.append(path.relative_to(vault).as_posix())
    return sorted(found)


def _size_problems(vault: Path, cfg: VaultConfig) -> list[Problem]:
    agents = vault / "AGENTS.md"
    if not agents.is_file():
        return []
    tokens = len(agents.read_text(encoding="utf-8")) // 4
    if tokens > cfg.agents_md_max_tokens:
        return [Problem("AGENTS.md", "size", f"about {tokens} tokens; the limit is {cfg.agents_md_max_tokens}")]
    return []


def run_checks(vault: Path) -> list[Problem]:
    cfg = load_vault_config(vault)
    problems = _content_problems(vault, cfg) + _root_problems(vault) + _link_problems(vault)
    problems += [Problem(rel, "sync-conflict", "sync-conflict copy; merge it into the original, then delete it")
                 for rel in find_conflicts(vault, cfg.conflict_patterns)]
    problems += _size_problems(vault, cfg)
    return problems
```

- [ ] **Step 3: Add the `check` command**

In `src/liber/cli.py`, add imports:
```python
from liber.check import run_checks
from liber.config import resolve_vault
```
(merge `resolve_vault` into the existing `liber.config` import line) and append:
```python
@app.command("check")
def check_cmd() -> None:
    """Validate the vault: frontmatter, structure, links, sync conflicts, AGENTS.md size."""
    with handle_errors():
        problems = run_checks(resolve_vault())
    if not problems:
        typer.echo("✓ no problems found")
        return
    for problem in problems:
        typer.echo(str(problem))
    typer.echo(f"{len(problems)} problem(s) found")
    raise typer.Exit(1)
```

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add src/liber/check.py src/liber/cli.py tests/test_check.py
git commit -m "feat: liber check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 5: Inbox capture and `liber note` / `add` / `status`

**Files:**
- Create: `src/liber/paths.py`, `src/liber/inbox.py`
- Modify: `src/liber/cli.py` (add `note`, `add`, `status`)
- Test: `tests/test_inbox.py`

**Interfaces:**
- Consumes: `LiberError`, `load_vault_config`, `find_conflicts`, `template_root`, `resolve_vault`; fixtures `vault`, `configured_vault`
- Produces:
  - `liber.paths.free_name(directory: Path, name: str) -> str` — `name` if neither `name` nor `name + ".md"` exists in `directory`, else `stem-2.suffix`, `stem-3.suffix`, …
  - `liber.inbox.NO_TEXT_MARKER = "<!-- liber: no text found -->"`, `TEXT_SUFFIXES = {".md", ".txt"}`
  - `liber.inbox.inbox_template() -> str` (packaged `inbox.md` text)
  - `liber.inbox.sidecar_for(doc: Path) -> Path` (`doc.name + ".md"`)
  - `liber.inbox.add_note(vault: Path, text: str, now: datetime) -> str` (returns the appended line)
  - `liber.inbox.pending_notes(vault: Path) -> list[str]`
  - `liber.inbox.add_files(vault: Path, sources: list[Path]) -> list[Path]`
  - `@dataclass(frozen=True) liber.inbox.InboxDocument(name: str, state: str, mtime: float)`; states: `"ready"`, `"pending-extraction"`, `"extracted"`, `"no-text"`, `"unsupported-folder"`
  - `liber.inbox.inbox_documents(vault: Path) -> list[InboxDocument]` (sorted by mtime, then name; sidecars not listed)
  - `@dataclass(frozen=True) liber.inbox.InboxStatus(notes: list[str], documents: list[InboxDocument], conflicts: list[str])`
  - `liber.inbox.inbox_status(vault: Path) -> InboxStatus`

- [ ] **Step 1: Write failing tests**

`tests/test_inbox.py`:
```python
import os
from datetime import datetime

import pytest
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.errors import LiberError
from liber.inbox import (
    NO_TEXT_MARKER, add_files, add_note, inbox_documents, inbox_status, pending_notes,
)
from liber.paths import free_name

NOW = datetime(2026, 9, 30, 14, 5)


def test_free_name(tmp_path):
    assert free_name(tmp_path, "thesis.pdf") == "thesis.pdf"
    (tmp_path / "thesis.pdf").write_text("x")
    assert free_name(tmp_path, "thesis.pdf") == "thesis-2.pdf"
    (tmp_path / "thesis-2.pdf.md").write_text("x")
    assert free_name(tmp_path, "thesis.pdf") == "thesis-3.pdf"
    (tmp_path / "README").write_text("x")
    assert free_name(tmp_path, "README") == "README-2"


def test_fresh_inbox_has_no_notes(vault):
    assert pending_notes(vault) == []


def test_add_note(vault):
    line = add_note(vault, "  I love mentoring  ", NOW)
    assert line == "- 2026-09-30 14:05 — I love mentoring"
    assert pending_notes(vault) == [line]


def test_notes_ignore_headings_and_comments_but_keep_tags(vault):
    inbox = vault / "inbox.md"
    inbox.write_text(inbox.read_text() + "\n## Heading\n#tag idea about pottery\nplain line")
    add_note(vault, "after a file with no trailing newline", NOW)
    assert pending_notes(vault) == [
        "#tag idea about pottery",
        "plain line",
        "- 2026-09-30 14:05 — after a file with no trailing newline",
    ]


def test_empty_note_rejected(vault):
    with pytest.raises(LiberError):
        add_note(vault, "   ", NOW)


def test_add_files_copies_with_collision_suffix(vault, tmp_path):
    src = write(tmp_path / "thesis.pdf", "pdf")
    first, second = add_files(vault, [src, src])
    assert first.name == "thesis.pdf" and second.name == "thesis-2.pdf"
    assert src.exists()


def test_add_missing_or_folder_rejected(vault, tmp_path):
    with pytest.raises(LiberError, match="not found"):
        add_files(vault, [tmp_path / "nope.pdf"])
    with pytest.raises(LiberError, match="folder"):
        add_files(vault, [tmp_path])


def test_document_states_and_order(vault):
    inbox = vault / "inbox"
    write(inbox / "a.pdf", "x")
    write(inbox / "b.docx", "x")
    write(inbox / "b.docx.md", "extracted text")
    write(inbox / "c.pdf", "x")
    write(inbox / "c.pdf.md", NO_TEXT_MARKER + "\n")
    write(inbox / "d.txt", "plain")
    (inbox / "folder").mkdir()
    for i, name in enumerate(["d.txt", "c.pdf", "b.docx", "a.pdf", "folder"]):
        os.utime(inbox / name, (1_000_000 + i, 1_000_000 + i))
    docs = inbox_documents(vault)
    assert [(d.name, d.state) for d in docs] == [
        ("d.txt", "ready"),
        ("c.pdf", "no-text"),
        ("b.docx", "extracted"),
        ("a.pdf", "pending-extraction"),
        ("folder", "unsupported-folder"),
    ]


def test_status_includes_conflicts(vault):
    write(vault / "inbox (Conflicted copy Pixel).md", "x")
    status = inbox_status(vault)
    assert status.conflicts == ["inbox (Conflicted copy Pixel).md"]


def test_cli_note_add_status(configured_vault, tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["note", "reconnected", "with", "Sam"]).exit_code == 0
    doc = write(tmp_path / "essay.pdf", "x")
    added = runner.invoke(app, ["add", str(doc)])
    assert added.exit_code == 0 and "inbox/essay.pdf" in added.output
    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0
    assert "reconnected with Sam" in status.output
    assert "essay.pdf" in status.output and "needs extraction" in status.output
    assert "Sync conflicts: none" in status.output
```

Run: `uv run pytest tests/test_inbox.py -v`
Expected: FAIL — `No module named 'liber.inbox'`.

- [ ] **Step 2: Implement paths and inbox**

`src/liber/paths.py`:
```python
from pathlib import Path


def free_name(directory: Path, name: str) -> str:
    """`name`, or `stem-N.suffix` if `name` (or its `.md` sidecar) is taken in `directory`."""
    candidate = Path(name)
    stem, suffix = candidate.stem, candidate.suffix
    n = 2
    while (directory / name).exists() or (directory / f"{name}.md").exists():
        name = f"{stem}-{n}{suffix}"
        n += 1
    return name
```

`src/liber/inbox.py`:
```python
"""Capturing notes and documents into the inbox, and reporting what's waiting."""

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from liber.check import find_conflicts
from liber.errors import LiberError
from liber.paths import free_name
from liber.scaffold import template_root
from liber.vaultconfig import load_vault_config

NO_TEXT_MARKER = "<!-- liber: no text found -->"
TEXT_SUFFIXES = {".md", ".txt"}
_HEADING = re.compile(r"^#{1,6}\s")


def inbox_template() -> str:
    return (template_root() / "inbox.md").read_text(encoding="utf-8")


def sidecar_for(doc: Path) -> Path:
    return doc.with_name(doc.name + ".md")


def _is_sidecar(path: Path) -> bool:
    return path.name.endswith(".md") and path.with_name(path.name[:-3]).is_file()


def _is_note_line(line: str) -> bool:
    s = line.strip()
    if not s or _HEADING.match(s):
        return False
    return not (s.startswith("<!--") and s.endswith("-->"))


def pending_notes(vault: Path) -> list[str]:
    inbox = vault / "inbox.md"
    if not inbox.is_file():
        return []
    return [line.strip() for line in inbox.read_text(encoding="utf-8").splitlines() if _is_note_line(line)]


def add_note(vault: Path, text: str, now: datetime) -> str:
    text = " ".join(text.split())
    if not text:
        raise LiberError("note is empty")
    line = f"- {now:%Y-%m-%d %H:%M} — {text}"
    inbox = vault / "inbox.md"
    existing = inbox.read_text(encoding="utf-8") if inbox.is_file() else inbox_template()
    if existing and not existing.endswith("\n"):
        existing += "\n"
    inbox.write_text(existing + line + "\n", encoding="utf-8")
    return line


def add_files(vault: Path, sources: list[Path]) -> list[Path]:
    for src in sources:
        if src.is_dir():
            raise LiberError(f"{src} is a folder; add the files inside it instead")
        if not src.is_file():
            raise LiberError(f"{src} not found")
    dest_dir = vault / "inbox"
    dest_dir.mkdir(exist_ok=True)
    added = []
    for src in sources:
        dest = dest_dir / free_name(dest_dir, src.name)
        shutil.copy2(src, dest)
        added.append(dest)
    return added


@dataclass(frozen=True)
class InboxDocument:
    name: str
    state: str
    mtime: float


def _state(path: Path) -> str:
    if path.is_dir():
        return "unsupported-folder"
    if path.suffix.lower() in TEXT_SUFFIXES:
        return "ready"
    sidecar = sidecar_for(path)
    if not sidecar.is_file():
        return "pending-extraction"
    return "no-text" if NO_TEXT_MARKER in sidecar.read_text(encoding="utf-8") else "extracted"


def inbox_documents(vault: Path) -> list[InboxDocument]:
    inbox = vault / "inbox"
    if not inbox.is_dir():
        return []
    docs = [
        InboxDocument(p.name, _state(p), p.stat().st_mtime)
        for p in inbox.iterdir()
        if not p.name.startswith(".") and not _is_sidecar(p)
    ]
    return sorted(docs, key=lambda d: (d.mtime, d.name))


@dataclass(frozen=True)
class InboxStatus:
    notes: list[str]
    documents: list[InboxDocument]
    conflicts: list[str]


def inbox_status(vault: Path) -> InboxStatus:
    patterns = load_vault_config(vault).conflict_patterns
    return InboxStatus(pending_notes(vault), inbox_documents(vault), find_conflicts(vault, patterns))
```

- [ ] **Step 3: Add the CLI commands**

In `src/liber/cli.py`, add imports:
```python
from datetime import datetime

from liber.inbox import add_files, add_note, inbox_status
```
Add this constant below the imports:
```python
STATE_LABELS = {
    "ready": "ready",
    "pending-extraction": "needs extraction (run liber extract)",
    "extracted": "extracted",
    "no-text": "no text found",
    "unsupported-folder": "folder — move its files into inbox/ directly",
}
```
Append:
```python
@app.command("note")
def note_cmd(text: Annotated[list[str], typer.Argument(help="The note; quotes optional")]) -> None:
    """Add a quick note to inbox.md."""
    with handle_errors():
        line = add_note(resolve_vault(), " ".join(text), datetime.now())
    typer.echo(f"Added to inbox.md: {line}")


@app.command("add")
def add_cmd(files: Annotated[list[Path], typer.Argument(help="Documents to copy into inbox/")]) -> None:
    """Copy documents into inbox/ for the next /ingest."""
    with handle_errors():
        vault = resolve_vault()
        added = add_files(vault, [f.expanduser() for f in files])
    for path in added:
        typer.echo(f"Added {path.relative_to(vault).as_posix()}")


@app.command("status")
def status_cmd() -> None:
    """Show what's waiting in the inbox."""
    with handle_errors():
        status = inbox_status(resolve_vault())
    typer.echo(f"Inbox notes: {len(status.notes)}")
    for line in status.notes:
        typer.echo(f"  {line}")
    typer.echo(f"Documents: {len(status.documents)}")
    for doc in status.documents:
        typer.echo(f"  {doc.name} — {STATE_LABELS[doc.state]}")
    if status.conflicts:
        typer.echo("Sync conflicts:")
        for rel in status.conflicts:
            typer.echo(f"  {rel}")
    else:
        typer.echo("Sync conflicts: none")
```

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add src/liber/paths.py src/liber/inbox.py src/liber/cli.py tests/test_inbox.py
git commit -m "feat: inbox capture (note, add) and status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 6: `liber extract`

**Files:**
- Create: `src/liber/extract.py`
- Modify: `src/liber/cli.py` (add `extract`), `tests/helpers.py` (add `make_pdf`, `make_docx`)
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `inbox_documents`, `sidecar_for`, `NO_TEXT_MARKER`, `resolve_vault`; fixtures `vault`, `configured_vault`
- Produces:
  - `liber.extract.Converter = Callable[[Path], str]`
  - `liber.extract.markitdown_convert(path: Path) -> str`
  - `liber.extract.MIN_TEXT_CHARS = 20` (non-whitespace characters)
  - `@dataclass(frozen=True) liber.extract.ExtractResult(name: str, state: str, detail: str = "")`; states `"extracted"`, `"no-text"`, `"failed"`
  - `liber.extract.extract_pending(vault: Path, convert: Converter = markitdown_convert) -> list[ExtractResult]`
  - Sidecar content on success: `<!-- liber: extracted from <name> -->\n\n<text>\n`; on no text: `NO_TEXT_MARKER + "\n"`; on failure: no sidecar written (stays pending).
  - `tests/helpers.py`: `make_pdf(path: Path, text: str | None) -> Path`, `make_docx(path: Path, text: str) -> Path`

- [ ] **Step 1: Add fixture builders to helpers**

Append to `tests/helpers.py`:
```python
def make_pdf(path: Path, text: str | None) -> Path:
    """A minimal one-page PDF; text=None makes a page with no text layer."""
    content = b"" if text is None else f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def make_docx(path: Path, text: str) -> Path:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path
```

- [ ] **Step 2: Write failing tests**

`tests/test_extract.py`:
```python
from typer.testing import CliRunner

from helpers import make_docx, make_pdf, write
from liber.cli import app
from liber.extract import extract_pending
from liber.inbox import NO_TEXT_MARKER, inbox_documents

TEXT = "The quick brown fox jumps over the lazy dog near liber."


def states(vault):
    return {d.name: d.state for d in inbox_documents(vault)}


def test_fake_converter_writes_sidecars(vault):
    write(vault / "inbox" / "a.pdf", "x")
    write(vault / "inbox" / "b.pdf", "x")
    write(vault / "inbox" / "c.md", "already text")
    texts = {"a.pdf": TEXT, "b.pdf": "   \n  "}
    results = extract_pending(vault, lambda p: texts[p.name])
    assert sorted((r.name, r.state) for r in results) == [("a.pdf", "extracted"), ("b.pdf", "no-text")]
    sidecar = (vault / "inbox" / "a.pdf.md").read_text()
    assert sidecar.startswith("<!-- liber: extracted from a.pdf -->") and TEXT in sidecar
    assert NO_TEXT_MARKER in (vault / "inbox" / "b.pdf.md").read_text()
    assert states(vault) == {"a.pdf": "extracted", "b.pdf": "no-text", "c.md": "ready"}


def test_already_extracted_is_not_reconverted(vault):
    write(vault / "inbox" / "a.pdf", "x")
    calls = []
    convert = lambda p: calls.append(p.name) or TEXT
    extract_pending(vault, convert)
    extract_pending(vault, convert)
    assert calls == ["a.pdf"]


def test_one_failure_does_not_stop_the_batch(vault):
    # Review Focus 3: a corrupt document is reported and left pending; others still extract.
    write(vault / "inbox" / "bad.pdf", "x")
    write(vault / "inbox" / "good.pdf", "x")

    def convert(path):
        if path.name == "bad.pdf":
            raise ValueError("corrupt file")
        return TEXT

    results = {r.name: r for r in extract_pending(vault, convert)}
    assert results["bad.pdf"].state == "failed" and "corrupt" in results["bad.pdf"].detail
    assert results["good.pdf"].state == "extracted"
    assert states(vault)["bad.pdf"] == "pending-extraction"


def test_real_html(vault):
    write(vault / "inbox" / "page.html", f"<html><body><h1>Title</h1><p>{TEXT}</p></body></html>")
    assert [r.state for r in extract_pending(vault)] == ["extracted"]
    assert "quick brown fox" in (vault / "inbox" / "page.html.md").read_text()


def test_real_pdf(vault):
    make_pdf(vault / "inbox" / "paper.pdf", "Hello liber this is a test document")
    assert [r.state for r in extract_pending(vault)] == ["extracted"]
    assert "Hello liber" in (vault / "inbox" / "paper.pdf.md").read_text()


def test_real_textless_pdf(vault):
    make_pdf(vault / "inbox" / "scan.pdf", None)
    assert [r.state for r in extract_pending(vault)] == ["no-text"]


def test_real_docx(vault):
    make_docx(vault / "inbox" / "resume.docx", TEXT)
    assert [r.state for r in extract_pending(vault)] == ["extracted"]
    assert "quick brown fox" in (vault / "inbox" / "resume.docx.md").read_text()


def test_cli_extract(configured_vault):
    write(configured_vault / "inbox" / "page.html", f"<p>{TEXT}</p>")
    runner = CliRunner()
    result = runner.invoke(app, ["extract"])
    assert result.exit_code == 0
    assert "page.html — extracted" in result.output
    again = runner.invoke(app, ["extract"])
    assert "Nothing to extract" in again.output
```

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `No module named 'liber.extract'`.

- [ ] **Step 3: Implement extract**

`src/liber/extract.py`:
```python
"""Converting inbox documents to Markdown text sidecars."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from liber.inbox import NO_TEXT_MARKER, inbox_documents, sidecar_for

Converter = Callable[[Path], str]
MIN_TEXT_CHARS = 20


def markitdown_convert(path: Path) -> str:
    from markitdown import MarkItDown

    return MarkItDown().convert(str(path)).text_content or ""


@dataclass(frozen=True)
class ExtractResult:
    name: str
    state: str
    detail: str = ""


def extract_pending(vault: Path, convert: Converter = markitdown_convert) -> list[ExtractResult]:
    results = []
    for doc in inbox_documents(vault):
        if doc.state != "pending-extraction":
            continue
        path = vault / "inbox" / doc.name
        try:
            text = convert(path)
        except Exception as exc:  # any converter failure: report it, keep going
            results.append(ExtractResult(doc.name, "failed", f"{type(exc).__name__}: {exc}"))
            continue
        sidecar = sidecar_for(path)
        if len("".join(text.split())) < MIN_TEXT_CHARS:
            sidecar.write_text(NO_TEXT_MARKER + "\n", encoding="utf-8")
            results.append(ExtractResult(doc.name, "no-text"))
        else:
            sidecar.write_text(f"<!-- liber: extracted from {doc.name} -->\n\n{text.strip()}\n", encoding="utf-8")
            results.append(ExtractResult(doc.name, "extracted"))
    return results
```

- [ ] **Step 4: Add the `extract` command**

In `src/liber/cli.py`, add `from liber.extract import extract_pending` and append:
```python
@app.command("extract")
def extract_cmd() -> None:
    """Convert waiting PDF, Word, and HTML documents in inbox/ to Markdown text."""
    with handle_errors():
        results = extract_pending(resolve_vault())
    if not results:
        typer.echo("Nothing to extract.")
        return
    labels = {"extracted": "extracted", "no-text": "no text found", "failed": "failed"}
    for result in results:
        suffix = f" ({result.detail})" if result.detail else ""
        typer.echo(f"{result.name} — {labels[result.state]}{suffix}")
```

Run: `uv run pytest -v`
Expected: all passed. If `test_real_pdf` fails because the extracted text differs, print the sidecar and adjust only the fixture (`make_pdf`), not the product code.

- [ ] **Step 5: Commit**

```bash
git add src/liber/extract.py src/liber/cli.py tests/helpers.py tests/test_extract.py
git commit -m "feat: liber extract via markitdown

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 7: `liber archive`

**Files:**
- Create: `src/liber/archive.py`
- Modify: `src/liber/cli.py` (add `archive`)
- Test: `tests/test_archive.py`

**Interfaces:**
- Consumes: `free_name`, `pending_notes`, `inbox_template`, `sidecar_for`, `TEXT_SUFFIXES`, `LiberError`, `resolve_vault`; fixtures `vault`, `configured_vault`
- Produces:
  - `liber.archive.archive_document(vault: Path, name: str) -> list[Path]` — moved paths (document first, then its sidecar if any) in `sources/documents/`
  - `liber.archive.archive_notes(vault: Path, now: datetime, count: int | None = None) -> Path | None` — appends the first `count` pending notes (all if `None`) to `sources/notes/<YYYY-MM>.md` under `## Archived YYYY-MM-DD HH:MM`, rewrites `inbox.md` as the template plus any remaining notes; returns the notes file, or `None` if there were no notes.

- [ ] **Step 1: Write failing tests**

`tests/test_archive.py`:
```python
from datetime import datetime

import pytest
from typer.testing import CliRunner

from helpers import write
from liber.archive import archive_document, archive_notes
from liber.cli import app
from liber.errors import LiberError
from liber.inbox import add_note, pending_notes

NOW = datetime(2026, 9, 30, 15, 0)


def test_archive_document_with_sidecar(vault):
    write(vault / "inbox" / "thesis.pdf", "pdf")
    write(vault / "inbox" / "thesis.pdf.md", "text")
    moved = archive_document(vault, "thesis.pdf")
    docs = vault / "sources" / "documents"
    assert moved == [docs / "thesis.pdf", docs / "thesis.pdf.md"]
    assert not (vault / "inbox" / "thesis.pdf").exists()
    assert not (vault / "inbox" / "thesis.pdf.md").exists()


def test_archive_collision_renames_both(vault):
    write(vault / "sources" / "documents" / "thesis.pdf", "old")
    write(vault / "inbox" / "thesis.pdf", "new")
    write(vault / "inbox" / "thesis.pdf.md", "new text")
    moved = archive_document(vault, "thesis.pdf")
    assert [p.name for p in moved] == ["thesis-2.pdf", "thesis-2.pdf.md"]
    assert (vault / "sources" / "documents" / "thesis.pdf").read_text() == "old"


def test_archive_markdown_document_without_sidecar(vault):
    write(vault / "inbox" / "essay.md", "text")
    assert [p.name for p in archive_document(vault, "essay.md")] == ["essay.md"]


@pytest.mark.parametrize("name", ["missing.pdf", "../AGENTS.md", "sub/x.pdf", "..", ""])
def test_archive_rejects_bad_names(vault, name):
    # Review Focus 5: nothing outside inbox/ may be moved.
    with pytest.raises(LiberError):
        archive_document(vault, name)
    assert (vault / "AGENTS.md").exists()


def test_archive_refuses_unextracted_and_sidecars(vault):
    write(vault / "inbox" / "a.pdf", "x")
    with pytest.raises(LiberError, match="liber extract"):
        archive_document(vault, "a.pdf")
    write(vault / "inbox" / "a.pdf.md", "text")
    with pytest.raises(LiberError, match="a.pdf"):
        archive_document(vault, "a.pdf.md")


def test_archive_notes(vault):
    add_note(vault, "first", NOW)
    add_note(vault, "second", NOW)
    dest = archive_notes(vault, NOW)
    assert dest == vault / "sources" / "notes" / "2026-09.md"
    text = dest.read_text()
    assert "## Archived 2026-09-30 15:00" in text and "first" in text and "second" in text
    assert pending_notes(vault) == []
    assert (vault / "inbox.md").read_text().startswith("# Inbox")


def test_archive_notes_appends_to_month_file(vault):
    add_note(vault, "one", NOW)
    archive_notes(vault, NOW)
    add_note(vault, "two", NOW)
    dest = archive_notes(vault, datetime(2026, 9, 30, 16, 0))
    text = dest.read_text()
    assert text.count("## Archived") == 2 and "one" in text and "two" in text


def test_archive_notes_count_keeps_late_arrivals(vault):
    # Review Focus 1: a note added (e.g. on the phone) after review stays in the inbox.
    add_note(vault, "reviewed one", NOW)
    add_note(vault, "reviewed two", NOW)
    add_note(vault, "arrived during ingest", NOW)
    archive_notes(vault, NOW, count=2)
    remaining = pending_notes(vault)
    assert len(remaining) == 1 and "arrived during ingest" in remaining[0]
    assert "arrived during ingest" not in (vault / "sources" / "notes" / "2026-09.md").read_text()


def test_archive_notes_bad_count(vault):
    add_note(vault, "only one", NOW)
    with pytest.raises(LiberError, match="1 note"):
        archive_notes(vault, NOW, count=2)
    with pytest.raises(LiberError):
        archive_notes(vault, NOW, count=0)


def test_archive_notes_when_empty(vault):
    assert archive_notes(vault, NOW) is None


def test_cli_archive(configured_vault):
    runner = CliRunner()
    write(configured_vault / "inbox" / "essay.md", "text")
    result = runner.invoke(app, ["archive", "essay.md"])
    assert result.exit_code == 0 and "sources/documents/essay.md" in result.output
    add_note(configured_vault, "n1", NOW)
    add_note(configured_vault, "n2", NOW)
    notes = runner.invoke(app, ["archive", "--notes", "--count", "1"])
    assert notes.exit_code == 0 and "sources/notes/" in notes.output
    assert len(pending_notes(configured_vault)) == 1
    assert runner.invoke(app, ["archive"]).exit_code == 1
    assert runner.invoke(app, ["archive", "x.pdf", "--notes"]).exit_code == 1
```

Run: `uv run pytest tests/test_archive.py -v`
Expected: FAIL — `No module named 'liber.archive'`.

- [ ] **Step 2: Implement archive**

`src/liber/archive.py`:
```python
"""Moving processed inbox items into sources/."""

from datetime import datetime
from pathlib import Path

from liber.errors import LiberError
from liber.inbox import TEXT_SUFFIXES, inbox_template, pending_notes, sidecar_for
from liber.paths import free_name


def archive_document(vault: Path, name: str) -> list[Path]:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise LiberError(f"'{name}' is not a file name in inbox/")
    inbox = vault / "inbox"
    src = inbox / name
    if not src.is_file():
        raise LiberError(f"'{name}' is not in inbox/")
    if name.endswith(".md") and (inbox / name[:-3]).is_file():
        raise LiberError(f"'{name}' is extracted text; archive the original '{name[:-3]}' instead")
    sidecar = sidecar_for(src)
    if src.suffix.lower() not in TEXT_SUFFIXES and not sidecar.is_file():
        raise LiberError(f"'{name}' has not been converted to text yet; run liber extract first")

    dest_dir = vault / "sources" / "documents"
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = free_name(dest_dir, name)
    moved = [src.rename(dest_dir / final)]
    if sidecar.is_file():
        moved.append(sidecar.rename(dest_dir / f"{final}.md"))
    return moved


def archive_notes(vault: Path, now: datetime, count: int | None = None) -> Path | None:
    notes = pending_notes(vault)
    if count is not None:
        if count < 1:
            raise LiberError("--count must be at least 1")
        if count > len(notes):
            raise LiberError(f"asked to archive {count} notes but only {len(notes)} note(s) are pending")
    if not notes:
        return None
    take = len(notes) if count is None else count
    archived, remaining = notes[:take], notes[take:]

    dest = vault / "sources" / "notes" / f"{now:%Y-%m}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.read_text(encoding="utf-8") if dest.is_file() else f"# Notes archived {now:%Y-%m}\n"
    block = f"\n## Archived {now:%Y-%m-%d %H:%M}\n\n" + "\n".join(archived) + "\n"
    dest.write_text(existing + block, encoding="utf-8")

    inbox_text = inbox_template()
    if remaining:
        if not inbox_text.endswith("\n"):
            inbox_text += "\n"
        inbox_text += "\n" + "\n".join(remaining) + "\n"
    (vault / "inbox.md").write_text(inbox_text, encoding="utf-8")
    return dest
```

- [ ] **Step 3: Add the `archive` command**

In `src/liber/cli.py`, add `from typing import Optional` (merge with the existing `typing` import) and `from liber.archive import archive_document, archive_notes`, then append:
```python
@app.command("archive")
def archive_cmd(
    name: Annotated[Optional[str], typer.Argument(help="Document in inbox/ to move to sources/documents/")] = None,
    notes: Annotated[bool, typer.Option("--notes", help="Archive the notes in inbox.md instead")] = False,
    count: Annotated[Optional[int], typer.Option("--count", help="With --notes: archive only the first N notes")] = None,
) -> None:
    """Move a processed item out of the inbox into sources/. Prints the final path(s)."""
    with handle_errors():
        if (name is None) == (not notes):
            raise LiberError("give either a document name or --notes (not both)")
        if count is not None and not notes:
            raise LiberError("--count only applies with --notes")
        vault = resolve_vault()
        if notes:
            dest = archive_notes(vault, datetime.now(), count)
            moved = [dest] if dest else []
        else:
            moved = archive_document(vault, name)
    if not moved:
        typer.echo("No notes to archive.")
    for path in moved:
        typer.echo(path.relative_to(vault).as_posix())
```

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add src/liber/archive.py src/liber/cli.py tests/test_archive.py
git commit -m "feat: liber archive for documents and notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 8: `liber bundle` and clipboard

**Files:**
- Create: `src/liber/bundle.py`, `src/liber/clipboard.py`
- Modify: `src/liber/cli.py` (add `bundle`)
- Test: `tests/test_bundle.py`

**Interfaces:**
- Consumes: `load_vault_config`, `sensitivity_rank`, `SENSITIVITY_LEVELS`, `iter_content_files`, `read_vault_file`, `LiberError`, `resolve_vault`; fixtures `vault`, `configured_vault`
- Produces:
  - `@dataclass(frozen=True) liber.bundle.Bundle(text: str, included: list[str], excluded: list[tuple[str, str]])` — `excluded` is `(rel, reason)`
  - `liber.bundle.build_bundle(vault: Path, topics: list[str] | None = None, max_sensitivity: str = "personal") -> Bundle`
  - `liber.clipboard.copy_to_clipboard(text: str) -> bool`

- [ ] **Step 1: Write failing tests**

`tests/test_bundle.py`:
```python
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
```

Run: `uv run pytest tests/test_bundle.py -v`
Expected: FAIL — `No module named 'liber.bundle'`.

- [ ] **Step 2: Implement bundle and clipboard**

`src/liber/bundle.py`:
```python
"""Combining vault files into one Markdown document for pasting into any chatbot."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from liber.docs import VaultFile, iter_content_files, read_vault_file
from liber.errors import LiberError
from liber.vaultconfig import SENSITIVITY_LEVELS, load_vault_config, sensitivity_rank


@dataclass(frozen=True)
class Bundle:
    text: str
    included: list[str]
    excluded: list[tuple[str, str]]


def _exclusion_reason(vf: VaultFile, limit: int) -> str | None:
    rank = sensitivity_rank(vf.meta.get("sensitivity"))
    if vf.parse_error or rank is None:
        return f"missing or invalid sensitivity ({vf.meta.get('sensitivity')!r})"
    if rank > limit:
        return vf.meta["sensitivity"]
    return None


def build_bundle(vault: Path, topics: list[str] | None = None, max_sensitivity: str = "personal") -> Bundle:
    limit = sensitivity_rank(max_sensitivity)
    if limit is None:
        raise LiberError(f"--max-sensitivity must be one of {', '.join(SENSITIVITY_LEVELS)}")
    available = load_vault_config(vault).top_level_folders
    chosen = available if topics is None else topics
    unknown = [t for t in chosen if t not in available]
    if unknown:
        raise LiberError(f"unknown topic(s) {', '.join(unknown)}; choose from {', '.join(available)}")

    candidates = []
    agents = vault / "AGENTS.md"
    if agents.is_file():
        candidates.append(read_vault_file(vault, agents))
    candidates += [vf for vf in iter_content_files(vault) if vf.rel.split("/")[0] in chosen]

    included: list[VaultFile] = []
    excluded: list[tuple[str, str]] = []
    for vf in candidates:
        reason = _exclusion_reason(vf, limit)
        if reason:
            excluded.append((vf.rel, reason))
        else:
            included.append(vf)

    header = (
        "# liber context bundle\n\n"
        "The files below are a knowledge base about the person sharing this bundle, written by them "
        "in the first person (\"I\" means them). Use it as background about them; it is not a character to play. "
        f"Generated {date.today().isoformat()}; sensitivity ceiling: {max_sensitivity}; "
        f"topics: {', '.join(chosen)}.\n"
    )
    sections = [f"\n---\n\n## `{vf.rel}`\n\n{vf.text.strip()}\n" for vf in included]
    return Bundle(header + "".join(sections), [vf.rel for vf in included], excluded)
```

`src/liber/clipboard.py`:
```python
import os
import shutil
import subprocess


def copy_to_clipboard(text: str) -> bool:
    """Copy via wl-copy (Wayland) or xclip (X11). Returns False if neither works."""
    commands = []
    if os.environ.get("WAYLAND_DISPLAY"):
        commands.append(["wl-copy"])
    commands.append(["xclip", "-selection", "clipboard"])
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text.encode("utf-8"), check=True, timeout=5)
            return True
        except (subprocess.SubprocessError, OSError):
            continue
    return False
```

- [ ] **Step 3: Add the `bundle` command**

In `src/liber/cli.py`, add `from liber.bundle import build_bundle` and `from liber.clipboard import copy_to_clipboard`, then append:
```python
@app.command("bundle")
def bundle_cmd(
    topics: Annotated[Optional[str], typer.Option("--topics", help="Comma-separated top-level folders, e.g. career,goals")] = None,
    max_sensitivity: Annotated[str, typer.Option("--max-sensitivity", help="public, personal, or private")] = "personal",
    copy: Annotated[bool, typer.Option("--copy", help="Copy to the clipboard instead of printing")] = False,
) -> None:
    """Combine AGENTS.md and vault files into one Markdown document for any chatbot."""
    with handle_errors():
        topic_list = [t.strip() for t in topics.split(",") if t.strip()] if topics else None
        bundle = build_bundle(resolve_vault(), topic_list, max_sensitivity)
    summary = f"{len(bundle.included)} file(s) included, {len(bundle.excluded)} excluded above '{max_sensitivity}' or without valid sensitivity"
    if copy and copy_to_clipboard(bundle.text):
        typer.echo(f"Copied to clipboard: {summary}", err=True)
        return
    if copy:
        typer.echo("warning: no clipboard tool worked (install wl-copy or xclip); printing instead", err=True)
    typer.echo(bundle.text)
    typer.echo(summary, err=True)
```

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add src/liber/bundle.py src/liber/clipboard.py src/liber/cli.py tests/test_bundle.py
git commit -m "feat: liber bundle with sensitivity ceiling and clipboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 9: `/ingest` and `/review` skills, manual test kit

**Files:**
- Create: `src/liber/skills/ingest/SKILL.md`, `src/liber/skills/review/SKILL.md`
- Create: `docs/manual-test/CHECKLIST.md`, `docs/manual-test/sample-inbox/notes.txt`, `docs/manual-test/sample-inbox/thesis-summary.md`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `default_skills_dir`, `SKILL_NAMES`, `liber.cli.app`
- Produces: packaged skills that `liber init` links; every `liber <command>` they mention exists.

- [ ] **Step 1: Write failing tests**

`tests/test_skills.py`:
```python
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
```

Run: `uv run pytest tests/test_skills.py -v`
Expected: FAIL — `SKILL.md` not found.

- [ ] **Step 2: Write the ingest skill**

`src/liber/skills/ingest/SKILL.md`:
````markdown
---
name: ingest
description: Process the liber vault's inbox (quick notes in inbox.md and documents in inbox/) into the knowledge base, proposing dated, sourced edits for the owner to approve, one git commit per item. Use when the user runs /ingest or asks to process their liber inbox.
---

# Ingest the liber inbox

You are updating **liber**, a knowledge base about its owner, who is the person you are talking to. First read `CLAUDE.md` and `AGENTS.md` in the vault root. They define the conventions you must follow. **The owner approves every change. Never edit content they haven't approved.**

## 1. Preflight

1. Run `git status --porcelain`. If anything is uncommitted, show it and offer to commit it first (`git add -A && git commit -m "manual edits"`), so each ingested item gets a clean commit of its own. Continue only once the tree is clean or the owner says to go ahead anyway.
2. Run `liber check`.
   - For each `sync-conflict` file, compare it with the original, agree the merged text with the owner, write it into the original, and delete the conflict copy.
   - Report other problems and offer to fix them.
3. Run `liber extract`, then `liber status`.
4. Tell the owner what's waiting and the order you'll take: inbox notes first as one batch, then documents oldest first.
   - `no text found`: ask the owner to paste the text or skip the item. If they paste it, replace `inbox/<name>.md` with the pasted text.
   - `failed`: report the error and skip the item unless the owner can supply the text.
   - `folder`: ask the owner to move the files inside it directly into `inbox/`.

## 2. For each item

### a. Read

- The item itself: the notes listed by `liber status`, or the document's text (`inbox/<name>.md` for converted documents, or the file itself for `.md` and `.txt`). Read long documents in chunks.
- `AGENTS.md`, and every existing file the item touches, including `people/` files for anyone mentioned, so you know what is already recorded.

### b. Propose

Present one numbered list of proposed changes, grouped like this:

- **Content edits.** For each one: the target file, whether it adds, updates, or creates a new file, and the exact text.
- **⚠️ Contradictions.** Questions where the item disagrees with the vault, for example "career/timeline.md says grad school ended 2015, but this is dated 2014. Which is right?"
- **New people.** A new `people/` file for each person.
- **Log entries.**
- **Structural suggestions,** kept separate from content: a new top-level folder when something has no good home, splitting a sprawling file, a new frontmatter field, a new template.

Example:

> **From `thesis.pdf`:**
> 1. `career/skills.md`: add *Bayesian modeling: deep; basis of thesis work (2014)*
> 2. `interests/epistemology.md`: **new file** (sensitivity: personal?)
> 3. `people/Ana Ruiz.md`: **new**, thesis advisor
> 4. ⚠️ `career/timeline.md` says grad school ended 2015, but the thesis is dated 2014. Which is right?
>
> **Structure:** nothing to suggest.

Then stop and wait. The owner answers in plain language, for example "all but 2", "3 was my co-advisor", or "skip this one".

- **Skip:** leave the item in the inbox untouched and move on.
- **Nothing worth keeping:** archive it with no content edits, as in step c.

### c. Archive, then apply

1. **Archive first**, so you know the final source name for links:
   - Notes: `liber archive --notes --count <N>`, where N is the number of notes you reviewed. Notes that arrived while you worked stay in the inbox. Link to the printed notes file by name, for example `[[2026-09]]`.
   - Document: `liber archive "<name>"`. Link to the archived name it prints, which may carry a `-2` suffix, for example `[[thesis.pdf]]`.
2. **Apply the approved edits, with the owner's corrections:**
   - End every added fact with `(src: [[<source name>]])`, and give it a date or date range whenever one is known.
   - Set `updated:` to today on every file you change.
   - New files get full frontmatter per `CLAUDE.md`. Confirm their sensitivity, defaulting to `personal`. Create people from `_templates/person.md`, replacing `{{title}}` and `{{date}}`.
   - A superseded fact becomes history (`- *Previously* …`). It is never deleted.
   - For approved structural changes, update `liber.toml` `[folders]`, `CLAUDE.md`, and the map in `AGENTS.md` together, and create the folder.
3. Run `liber check` and fix everything it reports.
4. Commit: `git add -A && git commit -m "ingest: <item name>"`.

## 3. Wrap up

1. For each gap you noticed, such as something mentioned but never explained, add a line to `open-questions.md`: `- [YYYY-MM-DD] <question> (from [[<source name>]])`. Commit with `ingest: open questions`.
2. If anything significant changed, propose a refresh of the **Summary** in `AGENTS.md`.
   - Show the full new text and get approval separately.
   - Keep it within the size limit; `liber check` enforces this.
   - Commit with `ingest: refresh AGENTS.md`.
3. Give a short recap: items processed, files changed, questions added, anything skipped.

## Rules

- **Record only what the source supports.** Mark inferences, for example "suggests I enjoy teaching (inferred)", or ask instead.
- **Never delete facts silently.** Contradictions go to the owner. Outdated facts become *Previously* history.
- **Distill long documents instead of copying them.** The full text stays in `sources/`.
- **Keep notes about other people factual and kind.**
- **Actively suggest structure.** When an item has no good home, a file is sprawling, or a new field would help, propose the change.
- **Never edit anything in `sources/`.**
- **The owner's word wins.** Record their correction, not your first reading.
````

- [ ] **Step 3: Write the review skill**

`src/liber/skills/review/SKILL.md`:
````markdown
---
name: review
description: Whole-vault review of the liber knowledge base, covering uncaptured parts of the owner's life, stale facts, thin areas, structural fit, and AGENTS.md accuracy, and proposing changes and open questions for approval. Use when the user runs /review or asks for a check-up of their liber vault.
---

# Review the liber vault

You are reviewing **liber**, a knowledge base about its owner, who is the person you are talking to. First read `CLAUDE.md` and `AGENTS.md`. **The owner approves every change.**

## 1. Preflight

1. Run `git status --porcelain`. Offer to commit any uncommitted changes first.
2. Run `liber check` and report the results.

## 2. Survey

Read `liber.toml`, `AGENTS.md`, `open-questions.md`, and every content folder. For large vaults, read file headings and frontmatter first, then go deeper where needed. Look for:

- **Uncaptured life areas.** Common parts of a life with no home yet, for example health and fitness, places lived, education, finances, family history, creative work, beliefs, or routines. Only raise the ones that seem relevant to this owner.
- **Thin areas.** Folders or files with little content compared with their importance to the owner.
- **Stale facts.** Old `updated` dates, `last_contact` long past, and present-tense facts that are probably no longer true.
- **Structure.** Files that have grown too large, folders that no longer fit, and repeated patterns that deserve a template or a frontmatter field.
- **AGENTS.md.** Whether the Summary still reflects the vault, and whether it is close to the size limit.
- **Open questions** that the vault now answers and can be removed.

## 3. Propose

Present one numbered list, grouped as: **Structure**, **Content fixes**, **Stale items to confirm**, **AGENTS.md**, and **New open questions**. Wait for the owner's answers.

## 4. Apply

1. Apply only the approved changes, following `CLAUDE.md`:
   - Update the `updated:` dates.
   - Turn superseded facts into *Previously* history.
   - For approved structural changes, update `liber.toml` `[folders]`, `CLAUDE.md`, and the map in `AGENTS.md` together.
2. Add the approved questions to `open-questions.md` as `- [YYYY-MM-DD] <question> (from review)`.
3. Run `liber check` and fix everything it reports.
4. Commit: `git add -A && git commit -m "review: <short summary>"`.
5. Recap what changed and what's still open.

## Rules

- **Suggest boldly, change nothing without approval.**
- **Never delete facts silently.**
- **Never edit anything in `sources/`.**
- **Keep notes about other people factual and kind.**
````

- [ ] **Step 4: Write the manual test kit**

`docs/manual-test/sample-inbox/notes.txt`:
```
Realized I love mentoring junior engineers but really dislike formal people management.
Had coffee with Priya Nair — she's now running a small consultancy and asked if I'd ever freelance.
Idea: maybe teach a course on the thing I know best?
```

`docs/manual-test/sample-inbox/thesis-summary.md`:
```markdown
# Thesis summary (sample document)

I completed my master's thesis on probabilistic models for sensor data in May 2014,
advised by Dr. Ana Ruiz. The work combined Bayesian inference with embedded systems,
and I presented it at a regional conference that summer. Writing it convinced me I enjoy
explaining hard ideas more than I enjoy pure research.
```

`docs/manual-test/CHECKLIST.md`:
````markdown
# Manual test: /ingest and /review

The skills can't be unit-tested, so run through this checklist after changing either `SKILL.md`. Use a throwaway vault; this never touches your real one.

## Setup

```bash
export LIBER_VAULT=/tmp/liber-manual-test
rm -rf "$LIBER_VAULT"
uv run liber init "$LIBER_VAULT"
```

`liber init` also points `~/.config/liber/config.toml` at the test vault. See Cleanup to point it back.

Add a fact that the sample document contradicts:

```bash
cat >> "$LIBER_VAULT/career/timeline.md" <<'EOF'

- Graduate school, M.S. at State University (2012–2015).
EOF
git -C "$LIBER_VAULT" commit -qam "seed timeline"
```

Load the sample inbox:

```bash
while IFS= read -r line; do uv run liber note "$line"; done < docs/manual-test/sample-inbox/notes.txt
uv run liber add docs/manual-test/sample-inbox/thesis-summary.md
uv run liber status
```

## Run /ingest

Open Claude Code in `$LIBER_VAULT` and run `/ingest`. Check each item:

- [ ] Preflight runs `liber check`, `liber extract`, and `liber status`, and states the processing order (notes first, then the document).
- [ ] Notes: proposes `core/preferences.md` (likes mentoring, dislikes managing), a new `people/Priya Nair.md`, and an open question or goal about teaching or freelancing. Waits for approval.
- [ ] Notes archived with `--count 3`; links look like `(src: [[2026-MM]])`.
- [ ] Document: flags ⚠️ the 2014 thesis versus grad school ending in 2015. Doesn't silently change either date.
- [ ] Proposes a new `people/Ana Ruiz.md` and a skills entry. Marks "enjoys explaining" as inferred or cites it directly.
- [ ] Asks for the sensitivity of each new file.
- [ ] One commit per item (`git -C "$LIBER_VAULT" log --oneline`).
- [ ] `liber check` passes at the end, and `open-questions.md` has at least one entry.
- [ ] Offers an `AGENTS.md` Summary refresh as a separate approval.

Late-arrival check: during the notes proposal, run `uv run liber note "arrived late"` in another terminal. After archiving, it must still be in `inbox.md`.

## Run /review

- [ ] Proposes at least one uncaptured life area and at least one structural or content suggestion, and waits for approval.
- [ ] Approving a new top-level folder updates `liber.toml`, `CLAUDE.md`, and the `AGENTS.md` map together, and `liber check` passes.

## Cleanup

```bash
rm -rf /tmp/liber-manual-test
unset LIBER_VAULT
```

If you already have a real vault, set `vault = "/path/to/your/vault"` in `~/.config/liber/config.toml` again.
````

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/liber/skills docs/manual-test tests/test_skills.py
git commit -m "feat: /ingest and /review skills with manual test kit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 10: README and final verification

**Files:**
- Create: `README.md`
- Modify: `pyproject.toml` (add `readme = "README.md"` under `[project]`)

**Interfaces:**
- Consumes: every command name and behavior above.
- Produces: user-facing documentation that includes the name derivation.

- [ ] **Step 1: Write the README**

`README.md`:
````markdown
# liber

**liber** is a personal knowledge base about you: your work history, life, likes and dislikes, passions, expertise, and the people in your life. It's kept as plain Markdown in a private git repo, so any LLM, chatbot, or agent can use it as "context about me", and it grows over time as you add to it.

## Why "liber"

The name comes from Carl Jung's *Liber Novus* ("The New Book"), better known as *The Red Book*. Jung spent about sixteen years writing it: a private, ever-growing record of his inner life that was never meant as a public face. That is the spirit of this project, an honest, accumulating record of a whole person.

Two other Jungian names were considered and set aside:

- **persona** is Jung's term for the *mask* we show the world, the opposite of what liber holds. LLMs also tend to read "persona" as an instruction to role-play.
- **anima** is, strictly, one part of the psyche (the contrasexual archetype), not the whole Self.

## How it fits together

```
 capture                     process                      use
 ───────                     ───────                      ───
 inbox.md  (quick notes)  ┐
 inbox/    (documents)    ├─► /ingest in Claude Code ─► vault files ─► liber bundle → any chatbot
 voice transcripts (later)┘   (you approve each change)   (git history)   MCP server (later) → agents
```

- **The vault** (for example `~/liber-vault`) is your content: Markdown files with small frontmatter headers, a private git repo, and an Obsidian vault synced to your phone.
- **This repo** is the tool: the `liber` command and two Claude Code skills, `/ingest` and `/review`.

Planned later: an MCP access server so agents can query the vault directly, and a voice interviewer (OpenAI GPT-Live-1 for the conversation, Claude as the interviewer's brain) that drops transcripts into `inbox/`.

## Install

Requires [uv](https://docs.astral.sh/uv/), git, and [Claude Code](https://claude.com/claude-code). Optional: `wl-copy` or `xclip` for `liber bundle --copy`.

```bash
git clone <this repo> ~/code/python/liber
cd ~/code/python/liber
uv tool install --editable .
```

The `--editable` install matters. Your vault's skills are symlinks into this repo, so updating the repo updates the skills everywhere.

## Create your vault

```bash
liber init ~/liber-vault
```

This creates the folders and starter files, links the skills into `.claude/skills/`, makes the first git commit, and sets the vault as your default in `~/.config/liber/config.toml`. Set `LIBER_VAULT` to point a single command at a different vault.

### Obsidian and phone sync (Obsidian Sync)

1. In Obsidian on the desktop: **Open folder as vault** and choose `~/liber-vault`.
2. **Settings → Sync:** create a new remote vault for it. Keep it separate from any other vault.
3. **Sync settings:** turn on syncing for **PDFs** and **other file types**, so documents saved into `inbox/` on the phone reach the desktop.
4. **Settings → Templates:** set the template folder to `_templates`. This lets you create people with the `person` template.
5. On your Android phone: open Obsidian, connect to the same remote vault, and add thoughts to `inbox.md` whenever you like.

git runs on the desktop only. Obsidian Sync doesn't sync hidden folders like `.git/`, so there's nothing to exclude.

To seed liber from notes you already have in another Obsidian vault, copy them into `inbox/` and run `/ingest`.

- **Storage:** original documents in `sources/documents/` count against your Sync storage. If that becomes a problem, add `sources/documents` to Sync's excluded folders. The originals stay safe in git on the desktop.
- **Conflicts:** Obsidian Sync usually merges edits automatically. If it ever leaves a conflict copy, `liber check` finds it, and `/ingest` helps you merge it. The file-name patterns are in `liber.toml` under `[sync] conflict_patterns`.

## Everyday use

1. **Capture:**
   - Add a line to `inbox.md` (from your phone or desktop), or run `liber note "..."`.
   - Drop documents into `inbox/`, or run `liber add file.pdf`.
2. **Ingest:** open Claude Code in your vault and run `/ingest`.
   - Claude proposes dated, sourced edits, flags contradictions, and suggests structural changes.
   - You approve or correct them in plain language. Each item becomes one git commit.
3. **Use:**
   - `liber bundle --topics career,goals,core --copy` puts a single document on your clipboard, ready to paste into any chatbot.
   - For example: "Given everything about me, what new revenue sources should I pursue?"
4. **Review:** run `/review` every month or so.
   - It finds uncaptured parts of your life, stale facts, and structural improvements.
   - It adds questions to `open-questions.md`.

## Vault layout

| Path | Contents |
|---|---|
| `AGENTS.md` | One-page summary of you, a map of the vault, and rules for AI readers. The first thing any tool reads |
| `CLAUDE.md` | Conventions Claude Code follows in the vault |
| `liber.toml` | The vault's structure (folders and their `type`), size limits, and sync settings |
| `inbox.md`, `inbox/` | Quick notes and documents waiting for `/ingest` |
| `open-questions.md` | Gaps to fill later |
| `core/`, `career/`, `people/`, `interests/`, `goals/`, `log/` | Your knowledge base |
| `sources/` | The originals everything came from: notes, documents, and (later) interviews |
| `_templates/` | Obsidian templates, for example `person.md` |

The structure is meant to grow. When Claude suggests a new folder and you approve it, the folder is added to `liber.toml`, and `liber check` accepts it from then on.

Every content file starts with:

```yaml
---
type: core            # must match the folder's type in liber.toml
updated: 2026-09-30
sensitivity: personal # public | personal | private
tags: []
---
```

## Commands

| Command | What it does |
|---|---|
| `liber init <path>` | Create a new vault and make it the default |
| `liber note "text"` | Add a dated note to `inbox.md` |
| `liber add <file>…` | Copy documents into `inbox/`, never overwriting |
| `liber status` | Show waiting notes, documents (and whether they've been converted to text), and sync conflicts |
| `liber extract` | Convert waiting PDF, Word, and HTML documents to Markdown text |
| `liber archive <name>` | Move a processed document (and its text) to `sources/documents/`, printing the final name |
| `liber archive --notes [--count N]` | Move processed notes (optionally only the first N) to `sources/notes/<YYYY-MM>.md` |
| `liber check` | Validate frontmatter, folder structure, `[[links]]`, sync conflicts, and `AGENTS.md` size. Exits 1 on problems |
| `liber bundle [--topics a,b] [--max-sensitivity personal] [--copy]` | Combine `AGENTS.md` and the selected folders into one Markdown document. Files above the ceiling, or with missing or invalid sensitivity, are left out |

## Development

```bash
uv sync
uv run pytest
```

The skills live in `src/liber/skills/`. After changing them, walk through `docs/manual-test/CHECKLIST.md`. The design and plan are in `docs/superpowers/`.
````

- [ ] **Step 2: Point pyproject at the README**

In `pyproject.toml`, add under `[project]` after `description`:
```toml
readme = "README.md"
```

- [ ] **Step 3: Full verification**

Run: `uv run pytest -v`
Expected: all passed, 0 failures.

Run: `uv run liber --help`
Expected: lists `init`, `note`, `add`, `status`, `extract`, `archive`, `check`, `bundle`.

Run an end-to-end smoke test in a throwaway vault:
```bash
export LIBER_VAULT=$(mktemp -d)/v
XDG_CONFIG_HOME=$(mktemp -d) uv run liber init "$LIBER_VAULT"
uv run liber note "smoke test note"
uv run liber status
uv run liber check
uv run liber bundle --topics core | head -20
```
Expected: `init` succeeds, `status` shows 1 note, `check` prints `✓ no problems found`, and `bundle` starts with `# liber context bundle`. (Setting `XDG_CONFIG_HOME` keeps the smoke test from changing your real `~/.config/liber`.)

- [ ] **Step 4: Commit**

```bash
git add README.md pyproject.toml
git commit -m "docs: README with name origin, setup, and command reference

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

## After the plan: handoff to the user (not a subagent task)

These steps change the user's machine and real data, so the user does them (or confirms them) after all tasks pass:

1. Rename the repo folder: `mv ~/code/python/persona ~/code/python/liber`. This must happen **before** the editable install, because the vault's skill symlinks point into this folder.
2. `cd ~/code/python/liber && uv tool install --editable .`
3. `liber init ~/liber-vault`, then follow the README's Obsidian Sync steps.
