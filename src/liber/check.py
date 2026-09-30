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


def _bare_name_matches(vault: Path) -> dict[str, set[str]]:
    matches: dict[str, set[str]] = {}
    for path in _walk_files(vault):
        rel = path.relative_to(vault).as_posix()
        matches.setdefault(path.name, set()).add(rel)
        if path.suffix == ".md":
            matches.setdefault(path.stem, set()).add(rel)
    return matches


def _link_problems(vault: Path) -> list[Problem]:
    targets = _link_targets(vault)
    bare = _bare_name_matches(vault)
    files = [read_vault_file(vault, vault / n) for n in _LINK_SCAN_ROOT_FILES if (vault / n).is_file()]
    files += iter_content_files(vault)
    problems = []
    for vf in files:
        text = _INLINE.sub("", _FENCED.sub("", vf.text))
        for match in _WIKILINK.finditer(text):
            target = match.group(1).strip()
            if target not in targets and target.removesuffix(".md") not in targets:
                problems.append(Problem(vf.rel, "broken-link", f"[[{target}]] doesn't match any file in the vault"))
                continue
            if "/" not in target:
                found = sorted(bare.get(target, set()) | bare.get(target.removesuffix(".md"), set()))
                if len(found) > 1:
                    hint = found[0].removesuffix(".md")
                    problems.append(Problem(
                        vf.rel, "ambiguous-link",
                        f"[[{target}]] matches more than one file: {', '.join(found)}; use a path link like [[{hint}]]"))
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
