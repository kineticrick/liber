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
