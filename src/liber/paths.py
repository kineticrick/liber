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
