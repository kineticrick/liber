"""Counting what the vault stores (from the working tree) and how it grew (from git history)."""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import frontmatter

from liber import ledger
from liber.docs import is_content_rel, iter_content_files
from liber.vaultconfig import VaultConfig, load_vault_config

FACT_LINE = re.compile(r"^\s*[-*] .*\(src: ", re.M)
_SRC = re.compile(r"\(src:.*?\)")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


@dataclass
class Output:
    facts: int = 0
    private_facts: int = 0
    people: int = 0
    interests: int = 0
    projects: int = 0
    files: int = 0
    words: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    earliest_year: int | None = None


@dataclass(frozen=True)
class MonthRow:
    month: str
    words_in: int
    owner_words_in: int
    facts: int
    facts_added: int


def _meta(text: str) -> dict:
    try:
        return dict(frontmatter.loads(text).metadata)
    except Exception:  # malformed YAML: count the file, treat it as having no metadata
        return {}


def output_from_files(files: list[tuple[str, str]], cfg: VaultConfig) -> Output:
    """Count facts, people and words in (relative path, text) pairs of content files."""
    out = Output()
    years: list[int] = []
    counted = {"person": "people", "interest": "interests", "project": "projects"}
    for rel, text in files:
        out.files += 1
        out.words += ledger.word_count(text)
        kind = cfg.type_for(rel.rpartition("/")[0])
        if kind in counted:
            setattr(out, counted[kind], getattr(out, counted[kind]) + 1)
        private = _meta(text).get("sensitivity") == "private"
        for line in text.splitlines():
            if not FACT_LINE.search(line):
                continue
            out.facts += 1
            out.private_facts += private
            years += [int(y) for y in _YEAR.findall(_SRC.sub("", line))]
    out.earliest_year = min(years) if years else None
    return out


def _count_sources(vault: Path) -> dict[str, int]:
    from liber.inbox import is_sidecar  # inbox imports ledger; keep this module light

    root = vault / "sources"
    counts: dict[str, int] = {}
    if not root.is_dir():
        return counts
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        n = sum(1 for f in sub.rglob("*")
                if f.is_file() and not any(part.startswith(".") for part in f.relative_to(sub).parts)
                and not is_sidecar(f))
        if n:
            counts[sub.name] = n
    return counts


def output_now(vault: Path) -> Output:
    cfg = load_vault_config(vault)
    out = output_from_files([(f.rel, f.text) for f in iter_content_files(vault)], cfg)
    out.sources = _count_sources(vault)
    return out


def _git(vault: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def _facts_at(vault: Path, sha: str, cfg: VaultConfig) -> int | None:
    names = _git(vault, "ls-tree", "-r", "--name-only", sha)
    if names is None:
        return None
    files = []
    for name in names.splitlines():
        if not is_content_rel(name):
            continue
        text = _git(vault, "show", f"{sha}:{name}")
        if text is not None:
            files.append((name, text))
    return output_from_files(files, cfg).facts


def _words_by_month(vault: Path) -> dict[str, tuple[int, int]]:
    sums: dict[str, tuple[int, int]] = {}
    for key, e in ledger.load(vault).items():
        if e.kind == "derived" or key.startswith("meta:"):
            continue
        w, o = sums.get(e.date[:7], (0, 0))
        sums[e.date[:7]] = (w + (e.words or 0), o + (e.owner_words or 0))
    return sums


def growth(vault: Path, today: date) -> list[MonthRow]:
    """One row per month from the first commit to today. Without git history, just the current month."""
    cfg = load_vault_config(vault)
    this_month = today.isoformat()[:7]
    inputs = _words_by_month(vault)
    log = _git(vault, "log", "--format=%cs")
    months = sorted({line[:7] for line in (log or "").split()})
    if not months:
        facts = output_now(vault).facts
        w, o = inputs.get(this_month, (0, 0))
        return [MonthRow(this_month, w, o, facts, facts)]
    rows: list[MonthRow] = []
    previous = 0
    month = months[0]
    while month <= this_month:
        sha = (_git(vault, "rev-list", "-1", f"--before={_next_month(month)}-01T00:00:00Z", "HEAD") or "").strip()
        facts = _facts_at(vault, sha, cfg) if sha else None
        if facts is None:
            facts = previous
        w, o = inputs.get(month, (0, 0))
        rows.append(MonthRow(month, w, o, facts, facts - previous))
        previous = facts
        month = _next_month(month)
    return rows
